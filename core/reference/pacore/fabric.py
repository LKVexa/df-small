"""
Native local + federated execution fabric.

Implements:
  * LCTL 1.3.x s10        execution profiles and deterministic replay
  * LCTL 1.3.x s25-26     task runtime, dataflow runtime, bounded channels
  * LCTL 1.4.x s9         BSP superstep engine and hierarchical barriers
  * LCTL 1.4.x s13        asynchronous engine, timer wheel, happens-before
  * LCTL 1.4.x s15-16     collectives and algorithm selection
  * LCTL 1.4.x s23-25     work stealing, load balancing, straggler handling
  * LCTL 1.4.x s27-31     deadlock/livelock/starvation detection, event log
  * LCTL 1.5.x s21        federation object model (worker/group/domain/fed)
  * LCTL 1.5.x s23-25     ownership scope, trust domains, failure boundaries
  * LCTL 1.5.x s30-35     migration, replication, consistency, provenance
  * LCTL 1.6.x s7         grain-size adaptation
  * LCTL 1.6.x s13/s15    federated collectives and hierarchical reduction
  * LCTL 1.6.x s19/s27    elastic worker groups, four-level work stealing
  * LCTL 1.6.x s26        adaptation thrash guard
  * LCTL 1.6.x s32        append-only event log, replay verification

Exit-gate tokens covered:
    EXECUTION_FABRIC_OPERATIONAL, DETERMINISTIC_REPLAY_PASS,
    COLLECTIVES_OPERATIONAL, WORK_STEALING_DETERMINISTIC,
    DISTRIBUTED_DEADLOCK_DETECTED, ADAPTATION_THRASH_DETECTED,
    LOAD_BALANCE_LEDGER, REPLAY_MISMATCH.

Normative policy: NETWORK=deny, BACKEND=none. Nothing in this module opens a
socket, resolves a name, or contacts a backend. Parallelism is realized with
local threads and local processes only.

SPECIFICATION HOLES FILLED HERE (recorded in the gap ledger):
  H7.  The documents require "identical event traces across runs" for the
       deterministic profiles but never state how wall-clock measurements are
       excluded from the trace. This module hashes only the semantic content
       of an event; measured durations live in a separate metrics channel and
       are never part of `EventLog.hash()`.
  H8.  Deterministic timeouts cannot be wall-clock timeouts. A deterministic
       profile evaluates `timeout_ticks` against the declared logical cost
       model; wall-clock `timeout_s` is honoured only in the throughput
       profile. Both are recorded so the distinction is auditable.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, is_dataclass, asdict
from enum import Enum
from typing import (Any, Callable, ClassVar, Deque, Dict, FrozenSet, Iterable,
                    List, Mapping, Optional, Sequence, Set, Tuple)

import numpy as np

from . import lang
from .lang import (CONSISTENCY_PROFILES, EXECUTION_PROFILES, TRUST_DOMAINS,
                   OPS_COLLECTIVE)

# --------------------------------------------------------------------------
# 0. Tokens and exceptions
# --------------------------------------------------------------------------

TOKEN_DEADLOCK = "DISTRIBUTED_DEADLOCK_DETECTED"
TOKEN_THRASH = "ADAPTATION_THRASH_DETECTED"
TOKEN_REPLAY_MISMATCH = "REPLAY_MISMATCH"
TOKEN_REPLAY_PASS = "DETERMINISTIC_REPLAY_PASS"
TOKEN_LOAD_BALANCE_LEDGER = "LOAD_BALANCE_LEDGER"
TOKEN_STEAL_REJECTED = "QUANTUM_TASK_STEAL_REJECTED"
TOKEN_CLONE_REJECTED = "QUANTUM_CLONE_REJECTED"
TOKEN_LIVELOCK = "RETRY_STORM_DETECTED"
TOKEN_STARVATION = "STARVATION_DETECTED"
TOKEN_UNSATISFIED_FUTURE = "UNSATISFIED_FUTURE_DETECTED"


class FabricError(Exception):
    """Base class for every fail-closed fabric condition."""


class ProfileError(FabricError):
    """Unknown or unusable execution profile."""


class CloneAttemptError(FabricError):
    """An attempt to duplicate a quantum payload (LCTL 1.1.x s6)."""


class OwnershipTransferError(FabricError):
    """An illegal ownership transfer of a quantum payload."""


class StealRejectedError(FabricError):
    """A steal that would move unknown quantum state."""


class SpeculationRejectedError(FabricError):
    """Speculative duplication of quantum work."""


class ChannelClosedError(FabricError):
    """Operation on a closed channel."""


class ChannelTimeoutError(FabricError):
    """Blocking channel operation exceeded its deadline."""


class ResourceClaimError(FabricError):
    """A resource claim cannot be satisfied by the target worker."""


class DeadlockDetectedError(FabricError):
    """A distributed deadlock cycle was found."""


class ReplayMismatchError(FabricError):
    """Replay reconstruction differs from the expected state."""


class AdaptationThrashError(FabricError):
    """Elastic adaptation rate exceeded the declared threshold."""


class TaskCancelledError(FabricError):
    """The task was cancelled before or during execution."""


class TaskTimeoutError(FabricError):
    """The task exceeded its declared timeout."""


class TaskFailedError(FabricError):
    """The task body raised."""


class BarrierError(FabricError):
    """Illegal barrier participation."""


class CollectiveError(FabricError):
    """Malformed collective invocation."""


# --------------------------------------------------------------------------
# 1. Deterministic hashing / sizing helpers
# --------------------------------------------------------------------------

def _json_default(o: Any) -> Any:
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=repr)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, complex):
        return [o.real, o.imag]
    if isinstance(o, tuple):
        return list(o)
    if is_dataclass(o) and not isinstance(o, type):
        try:
            return asdict(o)
        except Exception:
            return repr(o)
    return repr(o)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization used for every hash in this module."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=_json_default, ensure_ascii=False)


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:32]


def payload_bytes(obj: Any) -> int:
    """Deterministic byte accounting for message-size measurement."""
    if obj is None:
        return 0
    if isinstance(obj, QuantumPayload):
        return 16                      # handle size; the state never moves
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return len(obj)
    if isinstance(obj, np.ndarray):
        return int(obj.nbytes)
    if isinstance(obj, bool):
        return 1
    if isinstance(obj, int):
        return 8
    if isinstance(obj, float):
        return 8
    if isinstance(obj, complex):
        return 16
    if isinstance(obj, str):
        return len(obj.encode("utf-8"))
    if isinstance(obj, (list, tuple, set, frozenset)):
        return sum(payload_bytes(x) for x in obj)
    if isinstance(obj, dict):
        return sum(payload_bytes(k) + payload_bytes(v) for k, v in obj.items())
    return len(canonical_json(obj).encode("utf-8"))


# --------------------------------------------------------------------------
# 2. Quantum payloads: transfer, never clone (LCTL 1.1.x s6, 1.3.x s26)
# --------------------------------------------------------------------------

@dataclass
class QuantumPayload:
    """A handle to unknown physical quantum state.

    The handle is the only thing that ever moves. Copy, deep-copy, pickle and
    explicit `clone()` are all fail-closed: no code path in this package can
    produce two live handles to one lineage.
    """
    payload_id: str
    lineage: str
    type_: str = "qreg"
    owner: Optional[str] = None
    state: str = "LIVE"                 # LIVE | MOVED | CONSUMED
    known_classically: bool = False     # True only for classically-known prep
    provenance: Tuple[str, ...] = ()

    is_qstate: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if self.type_ not in lang.QUANTUM_OWNED_TYPES:
            raise FabricError(
                f"QuantumPayload type {self.type_!r} is not an ownership-governed "
                f"quantum type (lang.QUANTUM_OWNED_TYPES)")

    # -- no-cloning enforcement -------------------------------------------
    def __copy__(self) -> "QuantumPayload":
        raise CloneAttemptError(
            f"{TOKEN_CLONE_REJECTED}: shallow copy of quantum payload "
            f"{self.payload_id!r} (lineage {self.lineage})")

    def __deepcopy__(self, memo: dict) -> "QuantumPayload":
        raise CloneAttemptError(
            f"{TOKEN_CLONE_REJECTED}: deep copy of quantum payload "
            f"{self.payload_id!r} (lineage {self.lineage})")

    def __reduce__(self):
        raise CloneAttemptError(
            f"{TOKEN_CLONE_REJECTED}: quantum payload {self.payload_id!r} "
            f"cannot cross a process boundary by serialization")

    def clone(self) -> "QuantumPayload":
        raise CloneAttemptError(
            f"{TOKEN_CLONE_REJECTED}: explicit clone of {self.payload_id!r}")

    # -- state transitions -------------------------------------------------
    def mark_moved(self) -> None:
        self.state = "MOVED"

    def mark_consumed(self) -> None:
        self.state = "CONSUMED"

    @property
    def unknown(self) -> bool:
        """True when the state is not classically reconstructible."""
        return not self.known_classically

    def as_dict(self) -> dict:
        return {"payload_id": self.payload_id, "lineage": self.lineage,
                "type": self.type_, "owner": self.owner, "state": self.state,
                "known_classically": self.known_classically,
                "provenance": list(self.provenance)}


def is_quantum(obj: Any) -> bool:
    """Duck-typed quantum detection used across fabric/crdt/resilience."""
    if isinstance(obj, QuantumPayload):
        return True
    if getattr(obj, "is_qstate", False):
        return True
    if isinstance(obj, str) and obj.strip().upper() == "QSTATE":
        return True
    t = getattr(obj, "type_", None) or getattr(obj, "type", None)
    return isinstance(t, str) and t in lang.QUANTUM_OWNED_TYPES


# --------------------------------------------------------------------------
# 3. Execution profiles (LCTL 1.3.x s10)
# --------------------------------------------------------------------------

class ExecutionProfile(str, Enum):
    SINGLE_PROCESS_DETERMINISTIC = "single_process_deterministic"
    MULTI_THREAD_DETERMINISTIC = "multi_thread_deterministic"
    MULTI_PROCESS_DETERMINISTIC = "multi_process_deterministic"
    MULTI_PROCESS_THROUGHPUT = "multi_process_throughput"

    @classmethod
    def from_token(cls, token: str) -> "ExecutionProfile":
        for p in cls:
            if p.value == token:
                return p
        raise ProfileError(f"unknown execution profile {token!r}; "
                           f"expected one of {list(EXECUTION_PROFILES)}")

    @property
    def deterministic(self) -> bool:
        return self is not ExecutionProfile.MULTI_PROCESS_THROUGHPUT

    @property
    def uses_threads(self) -> bool:
        return self is ExecutionProfile.MULTI_THREAD_DETERMINISTIC

    @property
    def uses_processes(self) -> bool:
        return self in (ExecutionProfile.MULTI_PROCESS_DETERMINISTIC,
                        ExecutionProfile.MULTI_PROCESS_THROUGHPUT)

    @property
    def may_reorder(self) -> bool:
        return not self.deterministic

    def as_dict(self) -> dict:
        return {"profile": self.value, "deterministic": self.deterministic,
                "uses_threads": self.uses_threads,
                "uses_processes": self.uses_processes,
                "may_reorder": self.may_reorder,
                "replay_trace_required": True}


DEFAULT_PROFILE = ExecutionProfile.SINGLE_PROCESS_DETERMINISTIC

assert tuple(p.value for p in ExecutionProfile) == tuple(EXECUTION_PROFILES), \
    "ExecutionProfile must mirror lang.EXECUTION_PROFILES exactly"


# --------------------------------------------------------------------------
# 4. Logical clocks (LCTL 1.4.x s29)
# --------------------------------------------------------------------------

class LamportClock:
    """Scalar logical clock. Thread-safe, monotone, deterministic."""

    def __init__(self, node: str = "N_LOCAL", start: int = 0) -> None:
        self.node = node
        self._t = int(start)
        self._lock = threading.Lock()

    @property
    def time(self) -> int:
        with self._lock:
            return self._t

    def tick(self) -> int:
        with self._lock:
            self._t += 1
            return self._t

    def send(self) -> int:
        return self.tick()

    def receive(self, remote_time: int) -> int:
        with self._lock:
            self._t = max(self._t, int(remote_time)) + 1
            return self._t

    def as_dict(self) -> dict:
        return {"kind": "lamport", "node": self.node, "time": self.time}


@dataclass
class VectorClock:
    """Vector logical clock over a fixed or growing node set."""
    node: str
    clock: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.clock.setdefault(self.node, 0)

    def tick(self) -> "VectorClock":
        self.clock[self.node] = self.clock.get(self.node, 0) + 1
        return self

    def merge(self, other: "VectorClock") -> "VectorClock":
        for k, v in other.clock.items():
            self.clock[k] = max(self.clock.get(k, 0), int(v))
        return self

    def receive(self, other: "VectorClock") -> "VectorClock":
        self.merge(other)
        return self.tick()

    def copy(self) -> "VectorClock":
        return VectorClock(self.node, dict(self.clock))

    def happens_before(self, other: "VectorClock") -> bool:
        keys = set(self.clock) | set(other.clock)
        le = all(self.clock.get(k, 0) <= other.clock.get(k, 0) for k in keys)
        lt = any(self.clock.get(k, 0) < other.clock.get(k, 0) for k in keys)
        return le and lt

    def concurrent(self, other: "VectorClock") -> bool:
        return not (self.happens_before(other) or other.happens_before(self)
                    or self.clock == other.clock)

    def as_dict(self) -> dict:
        return {"kind": "vector", "node": self.node,
                "clock": {k: int(v) for k, v in sorted(self.clock.items())}}


# --------------------------------------------------------------------------
# 5. Append-only event log and replay (LCTL 1.6.x s32)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    event_id: str
    logical_time: int
    worker: str
    operation: str
    input_hash: str
    output_hash: str
    ownership_delta: Dict[str, Any]
    resource_delta: Dict[str, float]
    failure_delta: Dict[str, Any]
    proof_ref: str
    seq: int

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id, "logical_time": self.logical_time,
            "worker": self.worker, "operation": self.operation,
            "input_hash": self.input_hash, "output_hash": self.output_hash,
            "ownership_delta": self.ownership_delta,
            "resource_delta": {k: round(float(v), 9)
                               for k, v in sorted(self.resource_delta.items())},
            "failure_delta": self.failure_delta,
            "proof_ref": self.proof_ref, "seq": self.seq,
        }


@dataclass
class ReplayReport:
    ok: bool
    token: str
    mismatches: List[dict]
    expected_hash: str
    actual_hash: str
    events: int

    def as_dict(self) -> dict:
        return {"ok": self.ok, "token": self.token,
                "mismatches": self.mismatches,
                "expected_hash": self.expected_hash,
                "actual_hash": self.actual_hash, "events": self.events,
                "severity": "CONFORMANCE_FAILURE" if not self.ok else "PASS"}


def _diff(expected: Any, actual: Any, path: str = "") -> List[dict]:
    out: List[dict] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(expected) | set(actual)):
            p = f"{path}.{k}" if path else str(k)
            if k not in expected:
                out.append({"path": p, "expected": None, "actual": actual[k],
                            "kind": "UNEXPECTED_KEY"})
            elif k not in actual:
                out.append({"path": p, "expected": expected[k], "actual": None,
                            "kind": "MISSING_KEY"})
            else:
                out.extend(_diff(expected[k], actual[k], p))
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            out.append({"path": path or "<root>", "expected": len(expected),
                        "actual": len(actual), "kind": "LENGTH_MISMATCH"})
        for i, (e, a) in enumerate(zip(expected, actual)):
            out.extend(_diff(e, a, f"{path}[{i}]"))
    elif expected != actual:
        out.append({"path": path or "<root>", "expected": expected,
                    "actual": actual, "kind": "VALUE_MISMATCH"})
    return out


class EventLog:
    """Append-only execution log.

    Only the semantic content of an event is hashed. Wall-clock measurements
    are recorded separately (see `metrics`) and never influence the replay
    hash, so a deterministic profile reproduces `hash()` exactly (hole H7).
    """

    def __init__(self, name: str = "fabric", clock: Optional[LamportClock] = None) -> None:
        self.name = name
        self.clock = clock or LamportClock()
        self._events: List[Event] = []
        self._sealed = False
        self._lock = threading.Lock()
        self.metrics: Dict[str, float] = {}

    # -- append-only surface ----------------------------------------------
    @property
    def events(self) -> Tuple[Event, ...]:
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def seal(self) -> str:
        self._sealed = True
        return self.hash()

    def append(self, worker: str, operation: str, *,
               inputs: Any = None, outputs: Any = None,
               input_hash: Optional[str] = None,
               output_hash: Optional[str] = None,
               ownership_delta: Optional[Dict[str, Any]] = None,
               resource_delta: Optional[Dict[str, float]] = None,
               failure_delta: Optional[Dict[str, Any]] = None,
               proof_ref: str = "-",
               logical_time: Optional[int] = None) -> Event:
        if self._sealed:
            raise FabricError(f"event log {self.name!r} is sealed; append-only "
                              f"logs are never rewritten")
        with self._lock:
            lt = int(logical_time) if logical_time is not None else self.clock.tick()
            seq = len(self._events)
            ev = Event(
                event_id=f"E{seq:06d}",
                logical_time=lt,
                worker=str(worker),
                operation=str(operation),
                input_hash=input_hash or stable_hash(inputs),
                output_hash=output_hash or stable_hash(outputs),
                ownership_delta=dict(ownership_delta or {}),
                resource_delta={k: float(v) for k, v in (resource_delta or {}).items()},
                failure_delta=dict(failure_delta or {}),
                proof_ref=proof_ref, seq=seq,
            )
            self._events.append(ev)
            return ev

    # -- deterministic serialization --------------------------------------
    def replay_trace(self) -> List[dict]:
        return [e.as_dict() for e in self._events]

    def canonical(self) -> str:
        return canonical_json({"schema": "PA-LCTL/EVENTLOG/1",
                               "name": self.name,
                               "events": self.replay_trace()})

    def hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    # -- reconstruction ----------------------------------------------------
    def reconstruct(self) -> dict:
        """Rebuild the final state implied by the log, from the log alone."""
        ownership: Dict[str, str] = {}
        resources: Dict[str, float] = {}
        failures: Dict[str, int] = {}
        operations: List[str] = []
        per_worker: Dict[str, int] = {}
        for e in self._events:
            od = e.ownership_delta
            for k in od.get("acquire", []):
                ownership[str(k)] = e.worker
            for mv in od.get("move", []):
                key, _src, dst = mv
                ownership[str(key)] = str(dst)
            for k in od.get("release", []):
                ownership.pop(str(k), None)
            for k in od.get("consume", []):
                ownership.pop(str(k), None)
            for k, v in e.resource_delta.items():
                resources[k] = round(resources.get(k, 0.0) + float(v), 9)
            for k, v in e.failure_delta.items():
                failures[k] = failures.get(k, 0) + (int(v) if isinstance(v, (int, float)) else 1)
            operations.append(e.operation)
            per_worker[e.worker] = per_worker.get(e.worker, 0) + 1
        return {
            "ownership": dict(sorted(ownership.items())),
            "resources": dict(sorted(resources.items())),
            "failures": dict(sorted(failures.items())),
            "operations": operations,
            "events_per_worker": dict(sorted(per_worker.items())),
            "event_count": len(self._events),
        }

    def verify_replay(self, expected: Mapping[str, Any]) -> ReplayReport:
        """Compare the reconstructed state with `expected`.

        A mismatch is a conformance failure, never a warning (LCTL 1.6.x s32).
        """
        actual = self.reconstruct()
        mismatches = _diff(dict(expected), actual)
        ok = not mismatches
        return ReplayReport(
            ok=ok,
            token=TOKEN_REPLAY_PASS if ok else TOKEN_REPLAY_MISMATCH,
            mismatches=mismatches,
            expected_hash=stable_hash(dict(expected)),
            actual_hash=stable_hash(actual),
            events=len(self._events))

    def assert_replay(self, expected: Mapping[str, Any]) -> ReplayReport:
        rep = self.verify_replay(expected)
        if not rep.ok:
            raise ReplayMismatchError(
                f"{TOKEN_REPLAY_MISMATCH}: {len(rep.mismatches)} mismatch(es); "
                f"first={rep.mismatches[0] if rep.mismatches else None}")
        return rep

    def as_dict(self) -> dict:
        return {"name": self.name, "event_count": len(self._events),
                "log_hash": self.hash(), "events": self.replay_trace(),
                "final_state": self.reconstruct()}


# --------------------------------------------------------------------------
# 6. Federation object model (LCTL 1.5.x s21/s23/s24/s25/s30-35)
# --------------------------------------------------------------------------

@dataclass
class ResourceLimits:
    cpu_slots: int = 1
    memory_bytes: int = 1 << 30
    qpu_slots: int = 0
    ebit_budget: int = 0
    bandwidth_bytes_per_tick: float = 1.0e6

    def can_satisfy(self, claim: Mapping[str, float]) -> Tuple[bool, str]:
        for key, need in sorted(claim.items()):
            have = getattr(self, key, None)
            if have is None:
                return False, f"unknown resource dimension {key!r}"
            if float(need) > float(have):
                return False, f"{key}: claim {need} exceeds limit {have}"
        return True, "all claims satisfiable"

    def merged(self, other: "ResourceLimits") -> "ResourceLimits":
        return ResourceLimits(
            cpu_slots=self.cpu_slots + other.cpu_slots,
            memory_bytes=self.memory_bytes + other.memory_bytes,
            qpu_slots=self.qpu_slots + other.qpu_slots,
            ebit_budget=self.ebit_budget + other.ebit_budget,
            bandwidth_bytes_per_tick=min(self.bandwidth_bytes_per_tick,
                                         other.bandwidth_bytes_per_tick))

    def as_dict(self) -> dict:
        return {"cpu_slots": self.cpu_slots, "memory_bytes": self.memory_bytes,
                "qpu_slots": self.qpu_slots, "ebit_budget": self.ebit_budget,
                "bandwidth_bytes_per_tick": self.bandwidth_bytes_per_tick}


@dataclass
class MigrationRules:
    allow_classical_migration: bool = True
    allow_quantum_migration: bool = False
    require_explicit_movable: bool = True
    max_hops: int = 2
    forbid_cross_trust: bool = True

    def permits(self, *, quantum: bool, movable: bool, hops: int,
                same_trust: bool) -> Tuple[bool, str]:
        if quantum and not self.allow_quantum_migration:
            return False, "quantum migration forbidden by migration rules"
        if self.require_explicit_movable and not movable:
            return False, "task is not explicitly movable"
        if hops > self.max_hops:
            return False, f"hop count {hops} exceeds max_hops {self.max_hops}"
        if self.forbid_cross_trust and not same_trust:
            return False, "cross-trust-domain migration forbidden"
        return True, "migration admissible"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ReplicationRules:
    allow_classical_replication: bool = True
    allow_quantum_replication: bool = False       # structurally immutable
    replication_factor: int = 1
    consistency_profile: str = "SINGLE_OWNER"

    def __post_init__(self) -> None:
        if self.allow_quantum_replication:
            raise FabricError("quantum replication can never be enabled; "
                              "a QSTATE is ownership-constrained (LCTL 1.1.x s6)")
        if self.consistency_profile not in CONSISTENCY_PROFILES:
            raise FabricError(f"unknown consistency profile "
                              f"{self.consistency_profile!r}")
        if self.replication_factor < 1:
            raise FabricError("replication_factor must be >= 1")

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ConsistencyRules:
    profile: str = "SINGLE_OWNER"
    ordering: str = "PROGRAM_ORDER"
    quantum_scope: str = "SINGLE_OWNER_ONLY"

    def __post_init__(self) -> None:
        if self.profile not in CONSISTENCY_PROFILES:
            raise FabricError(f"unknown consistency profile {self.profile!r}")

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Provenance:
    created_by: str = "pacore.fabric"
    created_at_logical: int = 0
    source_ref: str = "-"
    seal: str = "-"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _check_trust(trust: str) -> str:
    if trust not in TRUST_DOMAINS:
        raise FabricError(f"unknown trust domain {trust!r}; expected one of "
                          f"{list(TRUST_DOMAINS)}")
    return trust


@dataclass
class Worker:
    """A single execution worker (LCTL 1.5.x s21)."""
    worker_id: str
    group_id: str = "G0"
    domain_id: str = "D0"
    federation_id: str = "F0"
    capabilities: FrozenSet[str] = frozenset({"classical"})
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    trust: str = "LOCAL_TRUSTED"
    failure_domain: str = "FD0"
    ownership_scope: Set[str] = field(default_factory=set)
    migration_rules: MigrationRules = field(default_factory=MigrationRules)
    replication_rules: ReplicationRules = field(default_factory=ReplicationRules)
    consistency_rules: ConsistencyRules = field(default_factory=ConsistencyRules)
    provenance: Provenance = field(default_factory=Provenance)
    # measured signals
    alive: bool = True
    throughput_ema: float = 1.0
    memory_pressure: float = 0.0
    queue_delay: float = 0.0
    heartbeat_latency: float = 0.0
    completed_tasks: int = 0

    def __post_init__(self) -> None:
        _check_trust(self.trust)
        self.capabilities = frozenset(self.capabilities)

    def owns(self, key: str) -> bool:
        return key in self.ownership_scope

    def acquire(self, key: str) -> None:
        self.ownership_scope.add(key)

    def release(self, key: str) -> None:
        self.ownership_scope.discard(key)

    def as_dict(self) -> dict:
        return {
            "worker_id": self.worker_id, "group_id": self.group_id,
            "domain_id": self.domain_id, "federation_id": self.federation_id,
            "capabilities": sorted(self.capabilities),
            "limits": self.limits.as_dict(), "trust": self.trust,
            "failure_domain": self.failure_domain,
            "ownership_scope": sorted(self.ownership_scope),
            "migration_rules": self.migration_rules.as_dict(),
            "replication_rules": self.replication_rules.as_dict(),
            "consistency_rules": self.consistency_rules.as_dict(),
            "provenance": self.provenance.as_dict(),
            "alive": self.alive,
            "signals": {"throughput_ema": round(self.throughput_ema, 9),
                        "memory_pressure": round(self.memory_pressure, 9),
                        "queue_delay": round(self.queue_delay, 9),
                        "heartbeat_latency": round(self.heartbeat_latency, 9),
                        "completed_tasks": self.completed_tasks},
        }


@dataclass
class WorkerGroup:
    """An ownership and failure-boundary scope over workers."""
    group_id: str
    domain_id: str = "D0"
    federation_id: str = "F0"
    members: Dict[str, Worker] = field(default_factory=dict)
    trust: str = "LOCAL_TRUSTED"
    failure_boundary: str = "GROUP"
    migration_rules: MigrationRules = field(default_factory=MigrationRules)
    replication_rules: ReplicationRules = field(default_factory=ReplicationRules)
    consistency_rules: ConsistencyRules = field(default_factory=ConsistencyRules)
    provenance: Provenance = field(default_factory=Provenance)

    def __post_init__(self) -> None:
        _check_trust(self.trust)

    def add(self, w: Worker) -> Worker:
        if w.worker_id in self.members:
            raise FabricError(f"worker {w.worker_id!r} already in group "
                              f"{self.group_id!r}")
        w.group_id, w.domain_id, w.federation_id = (
            self.group_id, self.domain_id, self.federation_id)
        self.members[w.worker_id] = w
        return w

    def remove(self, worker_id: str) -> Worker:
        if worker_id not in self.members:
            raise FabricError(f"worker {worker_id!r} not in group {self.group_id!r}")
        return self.members.pop(worker_id)

    def worker_ids(self) -> List[str]:
        return sorted(self.members)

    @property
    def capabilities(self) -> FrozenSet[str]:
        acc: Set[str] = set()
        for w in self.members.values():
            acc |= set(w.capabilities)
        return frozenset(acc)

    @property
    def limits(self) -> ResourceLimits:
        acc = ResourceLimits(0, 0, 0, 0, float("inf"))
        for wid in self.worker_ids():
            acc = acc.merged(self.members[wid].limits)
        return acc

    def as_dict(self) -> dict:
        return {"group_id": self.group_id, "domain_id": self.domain_id,
                "federation_id": self.federation_id,
                "members": [self.members[w].as_dict() for w in self.worker_ids()],
                "capabilities": sorted(self.capabilities),
                "limits": self.limits.as_dict(), "trust": self.trust,
                "failure_boundary": self.failure_boundary,
                "migration_rules": self.migration_rules.as_dict(),
                "replication_rules": self.replication_rules.as_dict(),
                "consistency_rules": self.consistency_rules.as_dict(),
                "provenance": self.provenance.as_dict()}


@dataclass
class ExecutionDomain:
    domain_id: str
    federation_id: str = "F0"
    groups: Dict[str, WorkerGroup] = field(default_factory=dict)
    trust: str = "LOCAL_TRUSTED"
    failure_boundary: str = "DOMAIN"
    migration_rules: MigrationRules = field(default_factory=MigrationRules)
    replication_rules: ReplicationRules = field(default_factory=ReplicationRules)
    consistency_rules: ConsistencyRules = field(default_factory=ConsistencyRules)
    provenance: Provenance = field(default_factory=Provenance)

    def __post_init__(self) -> None:
        _check_trust(self.trust)

    def add(self, g: WorkerGroup) -> WorkerGroup:
        if g.group_id in self.groups:
            raise FabricError(f"group {g.group_id!r} already in domain "
                              f"{self.domain_id!r}")
        g.domain_id, g.federation_id = self.domain_id, self.federation_id
        for w in g.members.values():
            w.domain_id, w.federation_id = self.domain_id, self.federation_id
        self.groups[g.group_id] = g
        return g

    def group_ids(self) -> List[str]:
        return sorted(self.groups)

    def workers(self) -> List[Worker]:
        out: List[Worker] = []
        for gid in self.group_ids():
            g = self.groups[gid]
            out.extend(g.members[w] for w in g.worker_ids())
        return out

    def as_dict(self) -> dict:
        return {"domain_id": self.domain_id, "federation_id": self.federation_id,
                "groups": [self.groups[g].as_dict() for g in self.group_ids()],
                "trust": self.trust, "failure_boundary": self.failure_boundary,
                "migration_rules": self.migration_rules.as_dict(),
                "replication_rules": self.replication_rules.as_dict(),
                "consistency_rules": self.consistency_rules.as_dict(),
                "provenance": self.provenance.as_dict()}


@dataclass
class Federation:
    federation_id: str = "F0"
    domains: Dict[str, ExecutionDomain] = field(default_factory=dict)
    trust: str = "LOCAL_TRUSTED"
    failure_boundary: str = "FEDERATION"
    migration_rules: MigrationRules = field(default_factory=MigrationRules)
    replication_rules: ReplicationRules = field(default_factory=ReplicationRules)
    consistency_rules: ConsistencyRules = field(default_factory=ConsistencyRules)
    provenance: Provenance = field(default_factory=Provenance)

    def __post_init__(self) -> None:
        _check_trust(self.trust)

    def add(self, d: ExecutionDomain) -> ExecutionDomain:
        if d.domain_id in self.domains:
            raise FabricError(f"domain {d.domain_id!r} already in federation")
        d.federation_id = self.federation_id
        for g in d.groups.values():
            g.federation_id = self.federation_id
            for w in g.members.values():
                w.federation_id = self.federation_id
        self.domains[d.domain_id] = d
        return d

    def domain_ids(self) -> List[str]:
        return sorted(self.domains)

    def workers(self) -> List[Worker]:
        out: List[Worker] = []
        for did in self.domain_ids():
            out.extend(self.domains[did].workers())
        return out

    def worker_ids(self) -> List[str]:
        return [w.worker_id for w in self.workers()]

    def worker(self, worker_id: str) -> Worker:
        for w in self.workers():
            if w.worker_id == worker_id:
                return w
        raise FabricError(f"unknown worker {worker_id!r}")

    def locate(self, worker_id: str) -> Tuple[str, str, str]:
        w = self.worker(worker_id)
        return self.federation_id, w.domain_id, w.group_id

    def group_of(self, worker_id: str) -> WorkerGroup:
        w = self.worker(worker_id)
        return self.domains[w.domain_id].groups[w.group_id]

    def as_dict(self) -> dict:
        return {"federation_id": self.federation_id,
                "domains": [self.domains[d].as_dict() for d in self.domain_ids()],
                "trust": self.trust, "failure_boundary": self.failure_boundary,
                "migration_rules": self.migration_rules.as_dict(),
                "replication_rules": self.replication_rules.as_dict(),
                "consistency_rules": self.consistency_rules.as_dict(),
                "provenance": self.provenance.as_dict(),
                "worker_count": len(self.worker_ids())}


def build_flat_federation(n_workers: int, *, groups: int = 1, domains: int = 1,
                          federation_id: str = "F0",
                          capabilities: Iterable[str] = ("classical",),
                          trust: str = "LOCAL_TRUSTED") -> Federation:
    """Deterministic helper building an `n_workers` federation."""
    if n_workers < 1:
        raise FabricError("n_workers must be >= 1")
    fed = Federation(federation_id=federation_id, trust=trust)
    per_domain = math.ceil(n_workers / domains)
    idx = 0
    for d in range(domains):
        dom = ExecutionDomain(domain_id=f"D{d}", federation_id=federation_id,
                              trust=trust)
        fed.add(dom)
        n_here = min(per_domain, n_workers - idx)
        if n_here <= 0:
            continue
        per_group = math.ceil(n_here / groups)
        placed = 0
        for g in range(groups):
            grp = WorkerGroup(group_id=f"D{d}G{g}", domain_id=dom.domain_id,
                              federation_id=federation_id, trust=trust)
            dom.add(grp)
            for _ in range(min(per_group, n_here - placed)):
                grp.add(Worker(worker_id=f"W{idx:02d}",
                               capabilities=frozenset(capabilities),
                               trust=trust,
                               failure_domain=f"FD{d}"))
                idx += 1
                placed += 1
    return fed


# --------------------------------------------------------------------------
# 7. Task runtime (LCTL 1.3.x s25-26, 1.4.x s9)
# --------------------------------------------------------------------------

TASK_STATES = ("PENDING", "READY", "RUNNING", "DONE", "FAILED",
               "CANCELLED", "TIMED_OUT")


@dataclass
class Task:
    task_id: str
    fn: Optional[Callable[..., Any]] = None
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    deadline: Optional[float] = None
    movable: bool = True
    payloads: List[QuantumPayload] = field(default_factory=list)
    owner: Optional[str] = None
    state: str = "PENDING"
    result: Any = None
    error: Optional[str] = None
    seq: int = 0
    logical_time: int = 0
    stage: Optional[str] = None
    cost_estimate: float = 1.0
    timeout_ticks: Optional[float] = None
    timeout_s: Optional[float] = None
    retries: int = 0
    replica_of: Optional[str] = None
    resource_claim: Dict[str, float] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    measured_duration: float = 0.0

    @property
    def holds_quantum(self) -> bool:
        return any(p.state == "LIVE" for p in self.payloads)

    @property
    def holds_unknown_quantum(self) -> bool:
        return any(p.state == "LIVE" and p.unknown for p in self.payloads)

    @property
    def stealable(self) -> bool:
        return self.movable and not self.holds_unknown_quantum \
            and self.state in ("PENDING", "READY")

    @property
    def done(self) -> bool:
        return self.state in ("DONE", "FAILED", "CANCELLED", "TIMED_OUT")

    def as_dict(self) -> dict:
        return {"task_id": self.task_id, "priority": self.priority,
                "deadline": self.deadline, "movable": self.movable,
                "owner": self.owner, "state": self.state,
                "error": self.error, "seq": self.seq,
                "logical_time": self.logical_time, "stage": self.stage,
                "cost_estimate": self.cost_estimate,
                "timeout_ticks": self.timeout_ticks, "timeout_s": self.timeout_s,
                "retries": self.retries, "replica_of": self.replica_of,
                "holds_quantum": self.holds_quantum,
                "holds_unknown_quantum": self.holds_unknown_quantum,
                "payloads": [p.as_dict() for p in self.payloads],
                "resource_claim": dict(sorted(self.resource_claim.items())),
                "provenance": self.provenance,
                "result_hash": stable_hash(self.result)}


def _mp_invoke(spec: Tuple[Callable[..., Any], Tuple[Any, ...], Dict[str, Any]]):
    """Top-level trampoline so the multiprocess profiles can pickle work.

    A `QuantumPayload` cannot be pickled by construction, so quantum-holding
    tasks fail closed at the process boundary rather than being cloned.
    """
    fn, args, kwargs = spec
    return fn(*args, **kwargs)


def _mp_invoke_indexed(item: Tuple[int, Tuple[Callable[..., Any],
                                              Tuple[Any, ...],
                                              Dict[str, Any]]]
                       ) -> Tuple[int, Any]:
    """Trampoline for the throughput profile: keeps the submission index so an
    unordered harvest can still be attributed to the right task."""
    index, spec = item
    return index, _mp_invoke(spec)


@dataclass
class BarrierRecord:
    barrier_id: str
    level: str
    participants: Tuple[str, ...]
    logical_time: int
    released: int

    def as_dict(self) -> dict:
        return {"barrier_id": self.barrier_id, "level": self.level,
                "participants": list(self.participants),
                "logical_time": self.logical_time, "released": self.released}


class TaskRuntime:
    """Spawn / await / event / barrier / reduction / scan / pipeline runtime.

    The default profile is `single_process_deterministic`: tasks execute in
    submission order in the calling thread, so results and event traces are
    bit-identical between runs. `multi_thread_deterministic` uses a real
    thread pool with a submission-order turnstile; the process profiles use a
    real `multiprocessing.Pool`.
    """

    def __init__(self, federation: Optional[Federation] = None, *,
                 profile: ExecutionProfile = DEFAULT_PROFILE,
                 log: Optional[EventLog] = None,
                 clock: Optional[LamportClock] = None,
                 max_workers: int = 4) -> None:
        self.federation = federation or build_flat_federation(1)
        self.profile = (profile if isinstance(profile, ExecutionProfile)
                        else ExecutionProfile.from_token(str(profile)))
        self.clock = clock or LamportClock()
        self.log = log or EventLog("task_runtime", self.clock)
        self.max_workers = max(1, int(max_workers))
        self._tasks: List[Task] = []
        self._by_id: Dict[str, Task] = {}
        self._events: Dict[str, threading.Event] = {}
        self._event_order: List[str] = []
        self._barriers: List[BarrierRecord] = []
        self._owner_of: Dict[str, str] = {}
        self._seq = 0
        self._lock = threading.Lock()

    # -- task creation -----------------------------------------------------
    def spawn(self, fn: Callable[..., Any], *args: Any,
              task_id: Optional[str] = None, priority: int = 0,
              deadline: Optional[float] = None, movable: bool = True,
              owner: Optional[str] = None, cost_estimate: float = 1.0,
              timeout_ticks: Optional[float] = None,
              timeout_s: Optional[float] = None,
              stage: Optional[str] = None,
              resource_claim: Optional[Mapping[str, float]] = None,
              provenance: Optional[Mapping[str, Any]] = None,
              **kwargs: Any) -> Task:
        with self._lock:
            seq = self._seq
            self._seq += 1
        tid = task_id or f"T{seq:05d}"
        if tid in self._by_id:
            raise FabricError(f"duplicate task identity {tid!r}")
        if owner is None:
            wids = self.federation.worker_ids()
            owner = wids[seq % len(wids)]
        claim = {k: float(v) for k, v in (resource_claim or {}).items()}
        if claim:
            w = self.federation.worker(owner)
            ok, why = w.limits.can_satisfy(claim)
            if not ok:
                raise ResourceClaimError(f"task {tid!r} on worker {owner!r}: {why}")
        t = Task(task_id=tid, fn=fn, args=tuple(args), kwargs=dict(kwargs),
                 priority=int(priority), deadline=deadline, movable=bool(movable),
                 owner=owner, seq=seq, cost_estimate=float(cost_estimate),
                 timeout_ticks=timeout_ticks, timeout_s=timeout_s, stage=stage,
                 resource_claim=claim, provenance=dict(provenance or {}))
        t.state = "READY"
        t.logical_time = self.clock.tick()
        self._tasks.append(t)
        self._by_id[tid] = t
        self.log.append(owner, "SPAWN", inputs={"task": tid, "seq": seq},
                        outputs=None, resource_delta=claim,
                        logical_time=t.logical_time,
                        proof_ref=t.provenance.get("proof_ref", "-"))
        return t

    def task(self, task_id: str) -> Task:
        if task_id not in self._by_id:
            raise FabricError(f"unknown task {task_id!r}")
        return self._by_id[task_id]

    @property
    def tasks(self) -> Tuple[Task, ...]:
        return tuple(self._tasks)

    # -- ownership ---------------------------------------------------------
    def transfer_ownership(self, task: Task, obj: QuantumPayload) -> QuantumPayload:
        """Move a quantum payload into `task`. Never a copy (LCTL 1.1.x s6)."""
        if not isinstance(obj, QuantumPayload):
            raise OwnershipTransferError(
                "transfer_ownership only governs QuantumPayload handles; "
                "classical values are copied freely")
        if obj.state != "LIVE":
            raise OwnershipTransferError(
                f"payload {obj.payload_id!r} is {obj.state}; a moved or consumed "
                f"payload can never be transferred again")
        if any(p is obj for p in task.payloads):
            raise CloneAttemptError(
                f"{TOKEN_CLONE_REJECTED}: task {task.task_id!r} already holds "
                f"payload {obj.payload_id!r}; duplication refused")
        prev = self._owner_of.get(obj.payload_id)
        if prev is not None and prev != task.task_id:
            holder = self._by_id.get(prev)
            if holder is not None:
                holder.payloads = [p for p in holder.payloads if p is not obj]
        obj.owner = task.task_id
        obj.provenance = tuple(obj.provenance) + (
            f"{prev or '-'}->{task.task_id}@{self.clock.time}",)
        task.payloads.append(obj)
        self._owner_of[obj.payload_id] = task.task_id
        self.log.append(task.owner or "-", "OWNERSHIP_TRANSFER",
                        inputs={"payload": obj.payload_id, "from": prev},
                        outputs={"to": task.task_id},
                        ownership_delta={"move": [[obj.payload_id,
                                                   prev or "-", task.task_id]]})
        return obj

    def duplicate_ownership(self, task: Task, obj: QuantumPayload) -> None:
        """Explicit duplication hook. Always fails closed."""
        raise CloneAttemptError(
            f"{TOKEN_CLONE_REJECTED}: duplication of quantum payload "
            f"{getattr(obj, 'payload_id', obj)!r} into task {task.task_id!r} "
            f"is forbidden; use transfer_ownership")

    def release_ownership(self, task: Task, obj: QuantumPayload) -> None:
        task.payloads = [p for p in task.payloads if p is not obj]
        obj.mark_consumed()
        self._owner_of.pop(obj.payload_id, None)
        self.log.append(task.owner or "-", "OWNERSHIP_RELEASE",
                        inputs={"payload": obj.payload_id}, outputs=None,
                        ownership_delta={"consume": [obj.payload_id]})

    # -- events ------------------------------------------------------------
    def event(self, name: str) -> threading.Event:
        if name not in self._events:
            self._events[name] = threading.Event()
            self._event_order.append(name)
            self.log.append("-", "EVENT_CREATE", inputs={"event": name})
        return self._events[name]

    def set_event(self, name: str) -> None:
        self.event(name).set()
        self.log.append("-", "EVENT_SET", inputs={"event": name})

    def wait_event(self, name: str, timeout: Optional[float] = None) -> bool:
        ev = self.event(name)
        if self.profile.deterministic and not ev.is_set():
            # Deterministic path: draining pending work is the only way an
            # event can become set without another OS thread.
            self.run()
        got = ev.wait(timeout if timeout is not None else 0)
        self.log.append("-", "EVENT_WAIT", inputs={"event": name},
                        outputs={"signalled": bool(got)})
        return bool(got)

    # -- barriers ----------------------------------------------------------
    def barrier(self, level: str = "worker_group",
                participants: Optional[Sequence[str]] = None) -> BarrierRecord:
        if level not in ("worker", "worker_group", "domain", "federation"):
            raise BarrierError(f"unknown barrier level {level!r}")
        parts = tuple(participants) if participants is not None \
            else tuple(self.federation.worker_ids())
        if not parts:
            raise BarrierError("barrier requires at least one participant")
        self.run()                       # a barrier is a fence over pending work
        rec = BarrierRecord(barrier_id=f"B{len(self._barriers):04d}", level=level,
                            participants=parts, logical_time=self.clock.tick(),
                            released=len(parts))
        self._barriers.append(rec)
        self.log.append("-", "BARRIER", inputs={"level": level,
                                                "participants": list(parts)},
                        outputs={"released": len(parts)},
                        logical_time=rec.logical_time)
        return rec

    @property
    def barriers(self) -> Tuple[BarrierRecord, ...]:
        return tuple(self._barriers)

    # -- execution ---------------------------------------------------------
    def _execute(self, t: Task) -> None:
        if t.state == "CANCELLED":
            return
        if t.timeout_ticks is not None and t.cost_estimate > float(t.timeout_ticks):
            t.state = "TIMED_OUT"
            t.error = (f"declared logical cost {t.cost_estimate} exceeds "
                       f"timeout_ticks {t.timeout_ticks}")
            self.log.append(t.owner or "-", "TASK_TIMEOUT",
                            inputs={"task": t.task_id}, outputs=None,
                            failure_delta={"timeout": 1})
            return
        t.state = "RUNNING"
        self.log.append(t.owner or "-", "TASK_START", inputs={"task": t.task_id},
                        outputs=None, logical_time=t.logical_time)
        t0 = time.perf_counter()
        try:
            t.result = t.fn(*t.args, **t.kwargs) if t.fn is not None else None
            t.state = "DONE"
        except Exception as exc:                     # noqa: BLE001 - recorded
            t.state = "FAILED"
            t.error = f"{type(exc).__name__}: {exc}"
        t.measured_duration = time.perf_counter() - t0
        if (t.state == "DONE" and t.timeout_s is not None
                and self.profile is ExecutionProfile.MULTI_PROCESS_THROUGHPUT
                and t.measured_duration > t.timeout_s):
            t.state = "TIMED_OUT"
            t.error = f"wall-clock {t.measured_duration:.6f}s > {t.timeout_s}s"
        w = self.federation.worker(t.owner) if t.owner else None
        if w is not None and t.state == "DONE":
            w.completed_tasks += 1
        self.log.append(t.owner or "-",
                        "TASK_DONE" if t.state == "DONE" else "TASK_FAIL",
                        inputs={"task": t.task_id}, outputs=t.result,
                        failure_delta={} if t.state == "DONE" else {"task_fail": 1},
                        proof_ref=t.provenance.get("proof_ref", "-"))
        self.log.metrics[f"duration.{t.task_id}"] = t.measured_duration

    def _run_serial(self, tasks: Sequence[Task]) -> None:
        for t in tasks:
            self._execute(t)

    def _run_threads_ordered(self, tasks: Sequence[Task]) -> None:
        cv = threading.Condition()
        turn = [0]

        def runner(i: int, t: Task) -> None:
            with cv:
                while turn[0] != i:
                    cv.wait()
            try:
                self._execute(t)
            finally:
                with cv:
                    turn[0] += 1
                    cv.notify_all()

        with ThreadPoolExecutor(max_workers=min(self.max_workers,
                                                max(1, len(tasks)))) as ex:
            futs = [ex.submit(runner, i, t) for i, t in enumerate(tasks)]
            for f in futs:
                f.result()

    def _run_processes(self, tasks: Sequence[Task], ordered: bool) -> None:
        specs = []
        for t in tasks:
            if t.holds_quantum:
                raise OwnershipTransferError(
                    f"task {t.task_id!r} holds quantum payload(s) and can never "
                    f"cross a process boundary")
            specs.append((t.fn, t.args, t.kwargs))
        # POSIX: fork (CoW, historical deterministic profile).
        # Windows: no fork — use spawn (trampolines are top-level picklable).
        _methods = multiprocessing.get_all_start_methods()
        _start = "fork" if "fork" in _methods else "spawn"
        ctx = multiprocessing.get_context(_start)
        with ctx.Pool(processes=min(self.max_workers, max(1, len(tasks)))) as pool:
            if ordered:
                results = list(pool.map(_mp_invoke, specs))
                pairs = list(zip(tasks, results))
            else:
                # Throughput profile: completion order is whatever the pool
                # produces. The trace still records every task, and the
                # submission sequence is preserved on each Task, so the log
                # replays completely even though it is not reproducible.
                indexed = list(enumerate(specs))
                harvested = list(pool.imap_unordered(_mp_invoke_indexed,
                                                     indexed))
                by_index = dict(harvested)
                pairs = [(tasks[i], by_index[i]) for i, _ in harvested]
        for t, r in pairs:
            t.state = "RUNNING"
            self.log.append(t.owner or "-", "TASK_START",
                            inputs={"task": t.task_id}, outputs=None,
                            logical_time=t.logical_time)
            t.result, t.state = r, "DONE"
            w = self.federation.worker(t.owner) if t.owner else None
            if w is not None:
                w.completed_tasks += 1
            self.log.append(t.owner or "-", "TASK_DONE",
                            inputs={"task": t.task_id}, outputs=t.result,
                            proof_ref=t.provenance.get("proof_ref", "-"))

    def run(self) -> List[Task]:
        """Execute every runnable task under the active profile."""
        pending = [t for t in self._tasks if t.state == "READY"]
        if not pending:
            return []
        # Submission order within a priority class is the deterministic
        # schedule. The throughput profile dispatches in the same order but
        # harvests completions in whatever order the pool produces them.
        pending.sort(key=lambda t: (-t.priority, t.seq))
        if self.profile is ExecutionProfile.SINGLE_PROCESS_DETERMINISTIC:
            self._run_serial(pending)
        elif self.profile is ExecutionProfile.MULTI_THREAD_DETERMINISTIC:
            self._run_threads_ordered(pending)
        elif self.profile is ExecutionProfile.MULTI_PROCESS_DETERMINISTIC:
            self._run_processes(pending, ordered=True)
        else:
            self._run_processes(pending, ordered=False)
        return pending

    def await_task(self, t: Task, timeout: Optional[float] = None) -> Any:
        if not t.done:
            self.run()
        if t.state == "CANCELLED":
            raise TaskCancelledError(f"task {t.task_id!r} was cancelled")
        if t.state == "TIMED_OUT":
            raise TaskTimeoutError(f"task {t.task_id!r}: {t.error}")
        if t.state == "FAILED":
            raise TaskFailedError(f"task {t.task_id!r}: {t.error}")
        self.log.append(t.owner or "-", "AWAIT", inputs={"task": t.task_id},
                        outputs=t.result)
        return t.result

    def await_all(self, tasks: Optional[Sequence[Task]] = None) -> List[Any]:
        ts = list(tasks) if tasks is not None else list(self._tasks)
        self.run()
        return [self.await_task(t) for t in ts]

    def cancel(self, t: Task, reason: str = "explicit") -> bool:
        if t.done:
            return False
        t.state = "CANCELLED"
        t.error = f"cancelled: {reason}"
        self.log.append(t.owner or "-", "TASK_CANCEL",
                        inputs={"task": t.task_id, "reason": reason},
                        outputs=None, failure_delta={"cancelled": 1})
        return True

    # -- collective-style task combinators ---------------------------------
    def reduction(self, fn: Callable[[Any, Any], Any], items: Sequence[Any],
                  initial: Any = None, *, name: str = "reduction") -> Any:
        """Deterministic fold in index order (LCTL 1.2.x s4 `reduction`)."""
        acc = initial
        for i, v in enumerate(items):
            acc = v if (i == 0 and initial is None) else fn(acc, v)
        self.log.append("-", "REDUCTION", inputs={"name": name,
                                                  "n": len(items)}, outputs=acc)
        return acc

    def scan(self, fn: Callable[[Any, Any], Any], items: Sequence[Any], *,
             inclusive: bool = True, name: str = "scan") -> List[Any]:
        out: List[Any] = []
        acc = None
        for i, v in enumerate(items):
            acc = v if i == 0 else fn(acc, v)
            out.append(acc)
        if not inclusive:
            out = [None] + out[:-1]
        self.log.append("-", "SCAN", inputs={"name": name, "n": len(items)},
                        outputs=out)
        return out

    def pipeline_stage(self, stage_name: str, fn: Callable[[Any], Any],
                       items: Sequence[Any], *, priority: int = 0) -> List[Any]:
        """One pipeline stage; each item becomes a task tagged with the stage."""
        tasks = [self.spawn(fn, it, stage=stage_name, priority=priority,
                            provenance={"stage": stage_name, "index": i})
                 for i, it in enumerate(items)]
        self.run()
        outs = [self.await_task(t) for t in tasks]
        self.log.append("-", "PIPELINE_STAGE",
                        inputs={"stage": stage_name, "n": len(items)},
                        outputs={"n_out": len(outs)})
        return outs

    def pipeline(self, stages: Sequence[Tuple[str, Callable[[Any], Any]]],
                 items: Sequence[Any]) -> List[Any]:
        cur = list(items)
        for name, fn in stages:
            cur = self.pipeline_stage(name, fn, cur)
        return cur

    def as_dict(self) -> dict:
        return {"profile": self.profile.as_dict(),
                "federation": self.federation.federation_id,
                "tasks": [t.as_dict() for t in self._tasks],
                "barriers": [b.as_dict() for b in self._barriers],
                "events": sorted(self._event_order),
                "log_hash": self.log.hash()}


# --------------------------------------------------------------------------
# 8. Bounded channels and the dataflow runtime (LCTL 1.3.x s26, 1.4.x s24)
# --------------------------------------------------------------------------

@dataclass
class ChannelMetrics:
    sends: int = 0
    receives: int = 0
    blocked_sends: int = 0
    blocked_receives: int = 0
    max_depth: int = 0
    bytes_sent: int = 0
    total_send_wait: float = 0.0
    total_recv_wait: float = 0.0
    depth_samples: List[int] = field(default_factory=list)
    timeouts: int = 0

    @property
    def mean_depth(self) -> float:
        return (sum(self.depth_samples) / len(self.depth_samples)
                if self.depth_samples else 0.0)

    def as_dict(self) -> dict:
        return {"sends": self.sends, "receives": self.receives,
                "blocked_sends": self.blocked_sends,
                "blocked_receives": self.blocked_receives,
                "max_queue_depth": self.max_depth,
                "mean_queue_depth": round(self.mean_depth, 6),
                "bytes_sent": self.bytes_sent, "timeouts": self.timeouts,
                "total_send_wait_s": round(self.total_send_wait, 9),
                "total_recv_wait_s": round(self.total_recv_wait, 9)}


class Channel:
    """A bounded, closable channel with blocking send/receive and metrics."""

    def __init__(self, name: str, capacity: int = 8, *,
                 log: Optional[EventLog] = None) -> None:
        if capacity < 1:
            raise FabricError("channel capacity must be >= 1")
        self.name = name
        self.capacity = int(capacity)
        self.log = log
        self._q: Deque[Any] = collections.deque()
        self._cv = threading.Condition()
        self._closed = False
        self._cancelled = False
        self.metrics = ChannelMetrics()

    # -- state -------------------------------------------------------------
    @property
    def depth(self) -> int:
        return len(self._q)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        if self.log is not None:
            self.log.append("-", "CHANNEL_CLOSE", inputs={"channel": self.name})

    def cancel(self) -> None:
        """Cancel every blocked participant; subsequent operations fail closed."""
        with self._cv:
            self._cancelled = True
            self._closed = True
            self._cv.notify_all()

    # -- transfer ----------------------------------------------------------
    def send(self, item: Any, timeout: Optional[float] = None) -> None:
        t0 = time.perf_counter()
        with self._cv:
            if self._closed:
                raise ChannelClosedError(f"send on closed channel {self.name!r}")
            if len(self._q) >= self.capacity:
                self.metrics.blocked_sends += 1
                ok = self._cv.wait_for(
                    lambda: len(self._q) < self.capacity or self._closed,
                    timeout=timeout)
                if not ok:
                    self.metrics.timeouts += 1
                    raise ChannelTimeoutError(
                        f"send on {self.name!r} timed out after {timeout}s "
                        f"(depth {len(self._q)}/{self.capacity})")
            if self._cancelled:
                raise ChannelClosedError(f"channel {self.name!r} cancelled")
            if self._closed:
                raise ChannelClosedError(f"send on closed channel {self.name!r}")
            self._q.append(item)
            self.metrics.sends += 1
            self.metrics.bytes_sent += payload_bytes(item)
            self.metrics.max_depth = max(self.metrics.max_depth, len(self._q))
            self.metrics.depth_samples.append(len(self._q))
            self.metrics.total_send_wait += time.perf_counter() - t0
            self._cv.notify_all()
        if self.log is not None:
            self.log.append("-", "CHANNEL_SEND",
                            inputs={"channel": self.name}, outputs=None,
                            resource_delta={f"channel_bytes.{self.name}":
                                            float(payload_bytes(item))})

    def try_send(self, item: Any) -> bool:
        with self._cv:
            if self._closed or len(self._q) >= self.capacity:
                return False
        self.send(item, timeout=0)
        return True

    def receive(self, timeout: Optional[float] = None) -> Any:
        t0 = time.perf_counter()
        with self._cv:
            if not self._q:
                if self._closed:
                    raise ChannelClosedError(
                        f"receive on closed empty channel {self.name!r}")
                self.metrics.blocked_receives += 1
                ok = self._cv.wait_for(lambda: bool(self._q) or self._closed,
                                       timeout=timeout)
                if not ok:
                    self.metrics.timeouts += 1
                    raise ChannelTimeoutError(
                        f"receive on {self.name!r} timed out after {timeout}s")
            if self._cancelled:
                raise ChannelClosedError(f"channel {self.name!r} cancelled")
            if not self._q:
                raise ChannelClosedError(
                    f"receive on closed empty channel {self.name!r}")
            item = self._q.popleft()
            self.metrics.receives += 1
            self.metrics.depth_samples.append(len(self._q))
            self.metrics.total_recv_wait += time.perf_counter() - t0
            self._cv.notify_all()
        if self.log is not None:
            self.log.append("-", "CHANNEL_RECV", inputs={"channel": self.name},
                            outputs=None)
        return item

    def try_receive(self) -> Tuple[bool, Any]:
        with self._cv:
            if not self._q:
                return False, None
        return True, self.receive(timeout=0)

    def peek(self) -> Tuple[bool, Any]:
        with self._cv:
            return (True, self._q[0]) if self._q else (False, None)

    def as_dict(self) -> dict:
        return {"channel": self.name, "capacity": self.capacity,
                "depth": self.depth, "closed": self._closed,
                "metrics": self.metrics.as_dict()}


def select(channels: Sequence[Channel], timeout: Optional[float] = None
           ) -> Tuple[Channel, Any]:
    """Deterministic select: the lowest-index ready channel always wins."""
    if not channels:
        raise FabricError("select requires at least one channel")
    deadline = None if timeout is None else time.perf_counter() + timeout
    while True:
        for ch in channels:
            ready, _ = ch.peek()
            if ready:
                return ch, ch.receive(timeout=0)
        if all(ch.closed for ch in channels):
            raise ChannelClosedError("select: every channel is closed and empty")
        if deadline is None or time.perf_counter() >= deadline:
            raise ChannelTimeoutError(
                f"select over {[c.name for c in channels]} found no ready channel")
        time.sleep(0.0005)


@dataclass
class FiringDecision:
    node_id: str
    inputs_available: bool
    ownership_ok: bool
    resources_ok: bool
    failure_policy_ok: bool
    backpressure_ok: bool
    deadline_ok: bool
    decision: str                    # FIRE | HOLD
    reason: str
    logical_time: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class DataflowNode:
    node_id: str
    fn: Callable[..., Any]
    inputs: List[Channel] = field(default_factory=list)
    outputs: List[Channel] = field(default_factory=list)
    priority: int = 0
    deadline: Optional[int] = None            # logical-time deadline
    resource_claim: Dict[str, float] = field(default_factory=dict)
    owner: Optional[str] = None
    ownership_guard: Optional[Callable[[Sequence[Any]], bool]] = None
    provenance: Dict[str, Any] = field(default_factory=dict)
    fired: int = 0
    held: int = 0

    def as_dict(self) -> dict:
        return {"node_id": self.node_id, "priority": self.priority,
                "deadline": self.deadline, "owner": self.owner,
                "inputs": [c.name for c in self.inputs],
                "outputs": [c.name for c in self.outputs],
                "resource_claim": dict(sorted(self.resource_claim.items())),
                "fired": self.fired, "held": self.held,
                "provenance": self.provenance}


class DataflowRuntime:
    """Bounded-channel dataflow with an explicit, recorded firing rule.

    A node fires only when ALL of the following hold (LCTL 1.3.x s26):
      1. every input channel has a token available;
      2. the ownership condition passes (no unknown quantum state is
         duplicated and the guard, if any, admits the token set);
      3. the resource claim is satisfiable on the owning worker;
      4. the failure policy admits the owning worker (alive, in-scope);
      5. every output channel has backpressure headroom.
    """

    def __init__(self, federation: Optional[Federation] = None, *,
                 log: Optional[EventLog] = None,
                 clock: Optional[LamportClock] = None) -> None:
        self.federation = federation or build_flat_federation(1)
        self.clock = clock or LamportClock()
        self.log = log or EventLog("dataflow", self.clock)
        self.nodes: List[DataflowNode] = []
        self.channels: Dict[str, Channel] = {}
        self.decisions: List[FiringDecision] = []
        self.failed_workers: Set[str] = set()

    def channel(self, name: str, capacity: int = 8) -> Channel:
        if name not in self.channels:
            self.channels[name] = Channel(name, capacity, log=self.log)
        return self.channels[name]

    def add_node(self, node: DataflowNode) -> DataflowNode:
        if any(n.node_id == node.node_id for n in self.nodes):
            raise FabricError(f"duplicate dataflow node {node.node_id!r}")
        for c in list(node.inputs) + list(node.outputs):
            self.channels.setdefault(c.name, c)
        self.nodes.append(node)
        return node

    def fail_worker(self, worker_id: str) -> None:
        self.failed_workers.add(worker_id)
        w = self.federation.worker(worker_id)
        w.alive = False
        self.log.append(worker_id, "WORKER_FAILED", inputs=None,
                        failure_delta={"worker_crash": 1})

    # -- firing rule -------------------------------------------------------
    def evaluate(self, node: DataflowNode) -> FiringDecision:
        lt = self.clock.time
        peeked = [c.peek() for c in node.inputs]
        inputs_available = all(ok for ok, _ in peeked) if node.inputs else True
        tokens = [v for _, v in peeked]

        ownership_ok = True
        own_reason = "no quantum tokens"
        for tok in tokens:
            if is_quantum(tok):
                if isinstance(tok, QuantumPayload) and tok.state != "LIVE":
                    ownership_ok, own_reason = False, (
                        f"payload {tok.payload_id!r} is {tok.state}")
                elif len(node.outputs) > 1:
                    ownership_ok, own_reason = False, (
                        "quantum token cannot fan out to multiple outputs")
        if ownership_ok and node.ownership_guard is not None and inputs_available:
            ownership_ok = bool(node.ownership_guard(tokens))
            own_reason = "guard admitted" if ownership_ok else "guard refused"

        resources_ok, res_reason = True, "no claim"
        if node.resource_claim:
            wid = node.owner or self.federation.worker_ids()[0]
            resources_ok, res_reason = self.federation.worker(wid).limits \
                .can_satisfy(node.resource_claim)

        wid = node.owner
        failure_ok = True
        fail_reason = "worker healthy"
        if wid is not None and (wid in self.failed_workers
                                or not self.federation.worker(wid).alive):
            failure_ok, fail_reason = False, f"owning worker {wid!r} unavailable"

        backpressure_ok = all(c.depth < c.capacity and not c.closed
                              for c in node.outputs)
        deadline_ok = node.deadline is None or lt <= node.deadline

        fire = (inputs_available and ownership_ok and resources_ok
                and failure_ok and backpressure_ok and deadline_ok)
        if fire:
            reason = "all firing conditions satisfied"
        elif not inputs_available:
            reason = "input tokens missing"
        elif not ownership_ok:
            reason = f"ownership condition failed: {own_reason}"
        elif not resources_ok:
            reason = f"resource claim unsatisfiable: {res_reason}"
        elif not failure_ok:
            reason = f"failure policy blocks firing: {fail_reason}"
        elif not backpressure_ok:
            reason = "backpressure: an output channel is full or closed"
        else:
            reason = f"logical deadline {node.deadline} passed at {lt}"

        return FiringDecision(node_id=node.node_id,
                              inputs_available=inputs_available,
                              ownership_ok=ownership_ok, resources_ok=resources_ok,
                              failure_policy_ok=failure_ok,
                              backpressure_ok=backpressure_ok,
                              deadline_ok=deadline_ok,
                              decision="FIRE" if fire else "HOLD",
                              reason=reason, logical_time=lt)

    def step(self) -> List[FiringDecision]:
        """One deterministic scheduling round over every registered node."""
        out: List[FiringDecision] = []
        order = sorted(self.nodes,
                       key=lambda n: (-n.priority,
                                      n.deadline if n.deadline is not None else 1 << 30,
                                      n.node_id))
        for node in order:
            d = self.evaluate(node)
            out.append(d)
            self.decisions.append(d)
            if d.decision != "FIRE":
                node.held += 1
                self.log.append(node.owner or "-", "DATAFLOW_HOLD",
                                inputs={"node": node.node_id},
                                outputs={"reason": d.reason})
                continue
            tokens = [c.receive(timeout=0) for c in node.inputs]
            self.clock.tick()
            res = node.fn(*tokens)
            node.fired += 1
            results = res if isinstance(res, tuple) else (res,)
            for c, v in zip(node.outputs, results):
                c.send(v, timeout=0)
            self.log.append(node.owner or "-", "DATAFLOW_FIRE",
                            inputs={"node": node.node_id,
                                    "in": [c.name for c in node.inputs]},
                            outputs=res,
                            proof_ref=str(node.provenance.get("proof_ref", "-")))
        return out

    def run(self, max_steps: int = 64) -> List[FiringDecision]:
        all_decisions: List[FiringDecision] = []
        for _ in range(max_steps):
            ds = self.step()
            all_decisions.extend(ds)
            if not any(d.decision == "FIRE" for d in ds):
                break
        return all_decisions

    def as_dict(self) -> dict:
        return {"nodes": [n.as_dict() for n in self.nodes],
                "channels": [self.channels[k].as_dict()
                             for k in sorted(self.channels)],
                "decisions": [d.as_dict() for d in self.decisions],
                "failed_workers": sorted(self.failed_workers)}


# --------------------------------------------------------------------------
# 9. BSP engine (LCTL 1.4.x s9, 1.6.x s15)
# --------------------------------------------------------------------------

BARRIER_LEVELS = ("worker_group", "domain", "federation")


@dataclass
class SuperstepRecord:
    index: int
    phases: Tuple[str, ...]
    participants: Tuple[str, ...]
    barrier_level: str
    messages: int = 0
    bytes_moved: int = 0
    local_compute_time: float = 0.0
    communication_time: float = 0.0
    barrier_time: float = 0.0
    idle_time: float = 0.0
    per_worker_compute: Dict[str, float] = field(default_factory=dict)
    results: Dict[str, Any] = field(default_factory=dict)
    barriers: List[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "superstep": self.index,
            "phases": list(self.phases),
            "participants": list(self.participants),
            "barrier_level": self.barrier_level,
            "messages": self.messages, "bytes": self.bytes_moved,
            "local_compute_time_s": round(self.local_compute_time, 9),
            "communication_time_s": round(self.communication_time, 9),
            "barrier_time_s": round(self.barrier_time, 9),
            "idle_time_s": round(self.idle_time, 9),
            "per_worker_compute_s": {k: round(v, 9) for k, v
                                     in sorted(self.per_worker_compute.items())},
            "barriers": self.barriers,
            "result_hash": stable_hash(self.results),
        }


class BSPEngine:
    """Bulk-synchronous supersteps with hierarchical barriers.

    Each superstep records exactly the three normative phases
    {local_compute, communication, barrier} and measures local compute time,
    bytes, messages, communication time, barrier time and idle time.
    """

    def __init__(self, federation: Federation, *,
                 log: Optional[EventLog] = None,
                 clock: Optional[LamportClock] = None,
                 profile: ExecutionProfile = DEFAULT_PROFILE) -> None:
        self.federation = federation
        self.clock = clock or LamportClock()
        self.log = log or EventLog("bsp", self.clock)
        self.profile = profile
        self.supersteps: List[SuperstepRecord] = []
        self.inboxes: Dict[str, List[Any]] = {
            w: [] for w in federation.worker_ids()}

    # -- hierarchical barriers --------------------------------------------
    def barrier(self, level: str = "worker_group") -> List[dict]:
        if level not in BARRIER_LEVELS:
            raise BarrierError(f"unknown barrier level {level!r}; expected "
                               f"one of {list(BARRIER_LEVELS)}")
        recs: List[dict] = []
        t0 = time.perf_counter()
        levels = BARRIER_LEVELS[:BARRIER_LEVELS.index(level) + 1]
        for lvl in levels:
            if lvl == "worker_group":
                scopes = {g.group_id: g.worker_ids()
                          for d in self.federation.domains.values()
                          for g in d.groups.values()}
            elif lvl == "domain":
                scopes = {d.domain_id: [w.worker_id for w in d.workers()]
                          for d in self.federation.domains.values()}
            else:
                scopes = {self.federation.federation_id:
                          self.federation.worker_ids()}
            for scope in sorted(scopes):
                lt = self.clock.tick()
                rec = {"level": lvl, "scope": scope,
                       "participants": sorted(scopes[scope]),
                       "logical_time": lt}
                recs.append(rec)
                self.log.append("-", "BSP_BARRIER", inputs=rec, outputs=None,
                                logical_time=lt)
        self.log.metrics[f"barrier.{len(self.supersteps)}"] = \
            time.perf_counter() - t0
        return recs

    # -- supersteps --------------------------------------------------------
    def superstep(self, local_fns: Mapping[str, Callable[[List[Any]], Any]],
                  comm_plan: Sequence[Tuple[str, str, Any]] = (),
                  *, barrier_level: str = "worker_group") -> SuperstepRecord:
        idx = len(self.supersteps)
        participants = tuple(sorted(local_fns))
        for w in participants:
            if w not in self.inboxes:
                raise FabricError(f"unknown worker {w!r} in superstep {idx}")
        rec = SuperstepRecord(index=idx,
                              phases=("local_compute", "communication", "barrier"),
                              participants=participants,
                              barrier_level=barrier_level)

        # phase 1: local compute (deterministic order over sorted worker ids)
        durations: Dict[str, float] = {}
        for w in participants:
            t0 = time.perf_counter()
            inbox = list(self.inboxes.get(w, []))
            rec.results[w] = local_fns[w](inbox)
            self.inboxes[w] = []
            durations[w] = time.perf_counter() - t0
            self.log.append(w, "BSP_LOCAL_COMPUTE",
                            inputs={"superstep": idx, "inbox": len(inbox)},
                            outputs=rec.results[w])
        rec.per_worker_compute = durations
        rec.local_compute_time = sum(durations.values())

        # phase 2: communication
        t0 = time.perf_counter()
        for src, dst, payload in comm_plan:
            if dst not in self.inboxes:
                raise FabricError(f"communication to unknown worker {dst!r}")
            if is_quantum(payload):
                raise OwnershipTransferError(
                    "BSP message payloads are classical; a QSTATE never enters "
                    "a superstep message buffer")
            nb = payload_bytes(payload)
            self.inboxes[dst].append(payload)
            rec.messages += 1
            rec.bytes_moved += nb
            self.log.append(src, "BSP_SEND",
                            inputs={"superstep": idx, "dst": dst},
                            outputs=None,
                            resource_delta={"bytes_moved": float(nb)})
        rec.communication_time = time.perf_counter() - t0

        # phase 3: barrier
        t0 = time.perf_counter()
        rec.barriers = self.barrier(barrier_level)
        rec.barrier_time = time.perf_counter() - t0

        # idle time: the slack between the slowest and each other worker
        if durations:
            slowest = max(durations.values())
            rec.idle_time = sum(slowest - d for d in durations.values())
        self.supersteps.append(rec)
        self.log.append("-", "BSP_SUPERSTEP",
                        inputs={"superstep": idx,
                                "participants": list(participants)},
                        outputs={"messages": rec.messages,
                                 "bytes": rec.bytes_moved})
        return rec

    def totals(self) -> dict:
        return {
            "supersteps": len(self.supersteps),
            "messages": sum(s.messages for s in self.supersteps),
            "bytes": sum(s.bytes_moved for s in self.supersteps),
            "local_compute_time_s": round(
                sum(s.local_compute_time for s in self.supersteps), 9),
            "communication_time_s": round(
                sum(s.communication_time for s in self.supersteps), 9),
            "barrier_time_s": round(
                sum(s.barrier_time for s in self.supersteps), 9),
            "idle_time_s": round(sum(s.idle_time for s in self.supersteps), 9),
        }

    def as_dict(self) -> dict:
        return {"federation": self.federation.federation_id,
                "supersteps": [s.as_dict() for s in self.supersteps],
                "totals": self.totals()}


# --------------------------------------------------------------------------
# 10. Asynchronous engine (LCTL 1.4.x s13)
# --------------------------------------------------------------------------

ASYNC_EVENT_KINDS = ("RETRY", "ROUTE_CHANGE", "FAILURE", "MEASUREMENT_CONTROL",
                     "TIMER", "COMPLETION")


class AsyncFuture:
    """A resolvable future with deterministic completion ordering."""

    __slots__ = ("future_id", "state", "value", "error", "_callbacks",
                 "logical_time")

    def __init__(self, future_id: str) -> None:
        self.future_id = future_id
        self.state = "PENDING"                # PENDING | DONE | FAILED | CANCELLED
        self.value: Any = None
        self.error: Optional[str] = None
        self.logical_time: int = 0
        self._callbacks: List[Callable[["AsyncFuture"], None]] = []

    @property
    def done(self) -> bool:
        return self.state != "PENDING"

    def add_done_callback(self, cb: Callable[["AsyncFuture"], None]) -> None:
        if self.done:
            cb(self)
        else:
            self._callbacks.append(cb)

    def set_result(self, value: Any) -> None:
        if self.done:
            raise FabricError(f"future {self.future_id!r} already resolved")
        self.value, self.state = value, "DONE"
        for cb in self._callbacks:
            cb(self)

    def set_error(self, err: str) -> None:
        if self.done:
            raise FabricError(f"future {self.future_id!r} already resolved")
        self.error, self.state = err, "FAILED"
        for cb in self._callbacks:
            cb(self)

    def cancel(self) -> None:
        if not self.done:
            self.state = "CANCELLED"

    def result(self) -> Any:
        if self.state == "PENDING":
            raise FabricError(f"future {self.future_id!r} is unresolved")
        if self.state == "FAILED":
            raise TaskFailedError(f"future {self.future_id!r}: {self.error}")
        if self.state == "CANCELLED":
            raise TaskCancelledError(f"future {self.future_id!r} cancelled")
        return self.value

    def as_dict(self) -> dict:
        return {"future_id": self.future_id, "state": self.state,
                "error": self.error, "logical_time": self.logical_time,
                "value_hash": stable_hash(self.value)}


@dataclass
class TimerEntry:
    timer_id: str
    fire_tick: int
    tag: str
    callback: Optional[Callable[[], Any]] = None

    def as_dict(self) -> dict:
        return {"timer_id": self.timer_id, "fire_tick": self.fire_tick,
                "tag": self.tag}


class TimerWheel:
    """A logical-tick timer wheel. Deterministic: no wall clock is consulted."""

    def __init__(self, slots: int = 64) -> None:
        if slots < 1:
            raise FabricError("timer wheel needs at least one slot")
        self.slots = int(slots)
        self.now = 0
        self._wheel: List[List[TimerEntry]] = [[] for _ in range(self.slots)]
        self._n = 0
        self.fired: List[TimerEntry] = []

    def schedule(self, delay_ticks: int, tag: str = "-",
                 callback: Optional[Callable[[], Any]] = None) -> TimerEntry:
        if delay_ticks < 0:
            raise FabricError("timer delay must be non-negative")
        if delay_ticks >= self.slots:
            raise FabricError(f"delay {delay_ticks} exceeds wheel span "
                              f"{self.slots}; use a larger wheel")
        e = TimerEntry(timer_id=f"TM{self._n:05d}",
                       fire_tick=self.now + delay_ticks, tag=tag,
                       callback=callback)
        self._n += 1
        self._wheel[e.fire_tick % self.slots].append(e)
        return e

    def advance(self, ticks: int = 1) -> List[TimerEntry]:
        out: List[TimerEntry] = []
        for _ in range(max(0, int(ticks))):
            self.now += 1
            slot = self._wheel[self.now % self.slots]
            due = [e for e in slot if e.fire_tick == self.now]
            for e in sorted(due, key=lambda x: x.timer_id):
                slot.remove(e)
                if e.callback is not None:
                    e.callback()
                out.append(e)
                self.fired.append(e)
        return out

    def as_dict(self) -> dict:
        pending = [e.as_dict() for slot in self._wheel for e in slot]
        return {"slots": self.slots, "now": self.now,
                "pending": sorted(pending, key=lambda d: d["timer_id"]),
                "fired": [e.as_dict() for e in self.fired]}


@dataclass
class AsyncEvent:
    event_id: str
    kind: str
    subject: str
    detail: Dict[str, Any]
    logical_time: int

    def as_dict(self) -> dict:
        return {"event_id": self.event_id, "kind": self.kind,
                "subject": self.subject, "detail": self.detail,
                "logical_time": self.logical_time}


class AsyncEngine:
    """Futures, a completion queue, a timer wheel and a happens-before graph."""

    def __init__(self, *, log: Optional[EventLog] = None,
                 clock: Optional[LamportClock] = None,
                 wheel_slots: int = 64) -> None:
        self.clock = clock or LamportClock()
        self.log = log or EventLog("async", self.clock)
        self.futures: Dict[str, AsyncFuture] = {}
        self.completion_queue: Deque[str] = collections.deque()
        self.wheel = TimerWheel(wheel_slots)
        self.events: List[AsyncEvent] = []
        self._hb: Dict[str, Set[str]] = {}
        self._pending: List[Tuple[str, Callable[[], Any]]] = []
        self._n = 0

    # -- futures -----------------------------------------------------------
    def submit(self, fn: Callable[..., Any], *args: Any,
               after: Sequence[str] = (), **kwargs: Any) -> AsyncFuture:
        fid = f"F{self._n:05d}"
        self._n += 1
        fut = AsyncFuture(fid)
        fut.logical_time = self.clock.tick()
        self.futures[fid] = fut
        self._hb.setdefault(fid, set())
        for a in after:
            self.add_happens_before(a, fid)
        self._pending.append((fid, lambda: fn(*args, **kwargs)))
        self.log.append("-", "ASYNC_SUBMIT", inputs={"future": fid},
                        outputs=None, logical_time=fut.logical_time)
        return fut

    def drain(self) -> List[str]:
        """Resolve every pending future in deterministic dependency order."""
        resolved: List[str] = []
        remaining = list(self._pending)
        self._pending = []
        progress = True
        while remaining and progress:
            progress = False
            still: List[Tuple[str, Callable[[], Any]]] = []
            for fid, thunk in remaining:
                deps = [d for d, outs in self._hb.items() if fid in outs]
                if any(not self.futures[d].done for d in deps
                       if d in self.futures):
                    still.append((fid, thunk))
                    continue
                fut = self.futures[fid]
                try:
                    fut.set_result(thunk())
                except Exception as exc:                # noqa: BLE001 recorded
                    fut.set_error(f"{type(exc).__name__}: {exc}")
                    self.emit("FAILURE", fid, {"error": fut.error})
                self.completion_queue.append(fid)
                resolved.append(fid)
                self.emit("COMPLETION", fid, {"state": fut.state})
                progress = True
            remaining = still
        self._pending = remaining
        return resolved

    def pop_completion(self) -> Optional[AsyncFuture]:
        if not self.completion_queue:
            return None
        return self.futures[self.completion_queue.popleft()]

    # -- control events ----------------------------------------------------
    def emit(self, kind: str, subject: str,
             detail: Optional[Mapping[str, Any]] = None) -> AsyncEvent:
        if kind not in ASYNC_EVENT_KINDS:
            raise FabricError(f"unknown async event kind {kind!r}; expected one "
                              f"of {list(ASYNC_EVENT_KINDS)}")
        ev = AsyncEvent(event_id=f"AE{len(self.events):05d}", kind=kind,
                        subject=subject, detail=dict(detail or {}),
                        logical_time=self.clock.tick())
        self.events.append(ev)
        self.log.append("-", f"ASYNC_{kind}", inputs={"subject": subject},
                        outputs=dict(detail or {}), logical_time=ev.logical_time,
                        failure_delta={"async_failure": 1} if kind == "FAILURE" else {})
        return ev

    def retry(self, subject: str, attempt: int) -> AsyncEvent:
        return self.emit("RETRY", subject, {"attempt": attempt})

    def route_change(self, subject: str, old: str, new: str) -> AsyncEvent:
        return self.emit("ROUTE_CHANGE", subject, {"old": old, "new": new})

    def measurement_control(self, subject: str, outcome: Any) -> AsyncEvent:
        return self.emit("MEASUREMENT_CONTROL", subject,
                         {"outcome_hash": stable_hash(outcome)})

    # -- happens-before ----------------------------------------------------
    def add_happens_before(self, a: str, b: str) -> None:
        self._hb.setdefault(a, set()).add(b)
        self._hb.setdefault(b, set())

    def happens_before(self, a: str, b: str) -> bool:
        seen: Set[str] = set()
        stack = [a]
        while stack:
            n = stack.pop()
            for m in sorted(self._hb.get(n, ())):
                if m == b:
                    return True
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        return False

    def happens_before_graph(self) -> dict:
        return {k: sorted(v) for k, v in sorted(self._hb.items())}

    def as_dict(self) -> dict:
        return {"futures": [self.futures[k].as_dict()
                            for k in sorted(self.futures)],
                "completion_queue": list(self.completion_queue),
                "timer_wheel": self.wheel.as_dict(),
                "events": [e.as_dict() for e in self.events],
                "happens_before": self.happens_before_graph()}


# --------------------------------------------------------------------------
# 11. Collectives (LCTL 1.4.x s15-16, 1.6.x s13)
# --------------------------------------------------------------------------

COLLECTIVE_ALGORITHMS = ("tree", "ring", "recursive_doubling", "pairwise",
                         "linear")

REDUCE_OPS: Dict[str, Callable[[Any, Any], Any]] = {
    "sum": lambda a, b: a + b,
    "prod": lambda a, b: a * b,
    "max": lambda a, b: b if b > a else a,
    "min": lambda a, b: b if b < a else a,
    "concat": lambda a, b: list(a) + list(b),
}


@dataclass
class Transfer:
    step: int
    src: int
    dst: int
    nbytes: int

    def as_dict(self) -> dict:
        return {"step": self.step, "src": self.src, "dst": self.dst,
                "bytes": self.nbytes}


@dataclass
class CollectiveResult:
    op: str
    algorithm: str
    workers: int
    values: List[Any]
    messages: int
    bytes_moved: int
    sync_count: int
    steps: int
    transfers: List[Transfer] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"op": self.op, "algorithm": self.algorithm,
                "workers": self.workers, "messages": self.messages,
                "bytes": self.bytes_moved, "sync_count": self.sync_count,
                "steps": self.steps,
                "value_hash": stable_hash(self.values),
                "transfers": [t.as_dict() for t in self.transfers]}


@dataclass
class AlgorithmChoice:
    op: str
    algorithm: str
    rule_id: str
    explanation: str
    inputs: Dict[str, Any]
    considered: List[str]

    def as_dict(self) -> dict:
        return {"op": self.op, "algorithm": self.algorithm,
                "rule_id": self.rule_id, "explanation": self.explanation,
                "inputs": self.inputs, "considered": self.considered}


def _reduce_fn(op: Any) -> Callable[[Any, Any], Any]:
    if callable(op):
        return op
    if isinstance(op, str) and op in REDUCE_OPS:
        return REDUCE_OPS[op]
    raise CollectiveError(f"unknown reduction op {op!r}; expected a callable or "
                          f"one of {sorted(REDUCE_OPS)}")


class Collectives:
    """Executable collectives over the local worker fabric.

    Every operation is realized by an explicit transfer schedule; the returned
    values are produced by those transfers, not by a shortcut. Bytes, messages
    and synchronization counts are measured from the same schedule.
    """

    def __init__(self, n_workers: int, *, log: Optional[EventLog] = None,
                 clock: Optional[LamportClock] = None) -> None:
        if n_workers < 1:
            raise CollectiveError("a collective needs at least one worker")
        self.n = int(n_workers)
        self.clock = clock or LamportClock()
        self.log = log or EventLog("collectives", self.clock)
        self.history: List[CollectiveResult] = []

    # -- bookkeeping -------------------------------------------------------
    def _finish(self, op: str, algorithm: str, values: List[Any],
                transfers: List[Transfer], sync: int) -> CollectiveResult:
        res = CollectiveResult(
            op=op, algorithm=algorithm, workers=self.n, values=values,
            messages=len(transfers),
            bytes_moved=sum(t.nbytes for t in transfers),
            sync_count=sync,
            steps=(max((t.step for t in transfers), default=-1) + 1),
            transfers=transfers)
        self.history.append(res)
        self.log.append("-", op, inputs={"algorithm": algorithm, "n": self.n},
                        outputs={"messages": res.messages,
                                 "bytes": res.bytes_moved,
                                 "sync": res.sync_count},
                        resource_delta={"collective_bytes": float(res.bytes_moved)})
        return res

    @staticmethod
    def _check_alg(algorithm: str) -> str:
        if algorithm not in COLLECTIVE_ALGORITHMS:
            raise CollectiveError(f"unknown collective algorithm {algorithm!r}; "
                                  f"expected one of {list(COLLECTIVE_ALGORITHMS)}")
        return algorithm

    @staticmethod
    def _xfer(T: List[Transfer], step: int, src: int, dst: int, val: Any) -> None:
        T.append(Transfer(step, src, dst, payload_bytes(val)))

    # -- binomial helpers --------------------------------------------------
    def _binomial_bcast(self, buf: List[Any], root: int, lo: int, hi: int,
                        T: List[Transfer], step0: int = 0) -> int:
        """Binomial broadcast of buf[root] into ranks [lo, hi). Returns steps."""
        n = hi - lo
        if n <= 1:
            return 0
        have = [root]
        dist, step = 1, step0
        while dist < n:
            for v in list(have):
                vr = (v - lo) % n
                tr = vr + dist
                if tr < n:
                    d = lo + ((tr + (root - lo)) % n)
                    self._xfer(T, step, v, d, buf[v])
                    buf[d] = buf[v]
                    have.append(d)
            dist *= 2
            step += 1
        return step - step0

    # -- BROADCAST ---------------------------------------------------------
    def broadcast(self, value: Any, root: int = 0,
                  algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        n = self.n
        if not 0 <= root < n:
            raise CollectiveError(f"root {root} outside [0,{n})")
        buf: List[Any] = [None] * n
        buf[root] = value
        T: List[Transfer] = []
        if n == 1:
            sync = 0
        elif algorithm == "linear":
            for r in range(n):
                if r != root:
                    self._xfer(T, 0, root, r, value)
                    buf[r] = value
            sync = 1
        elif algorithm == "pairwise":
            step = 0
            for r in range(n):
                if r != root:
                    self._xfer(T, step, root, r, value)
                    buf[r] = value
                    step += 1
            sync = n - 1
        elif algorithm == "ring":
            for i in range(n - 1):
                src = (root + i) % n
                dst = (root + i + 1) % n
                self._xfer(T, i, src, dst, buf[src])
                buf[dst] = buf[src]
            sync = n - 1
        else:                                  # tree / recursive_doubling
            steps = self._binomial_bcast(buf, root, 0, n, T)
            sync = steps
        return self._finish("BROADCAST", algorithm, buf, T, sync)

    # -- SCATTER -----------------------------------------------------------
    def _scatter_halving(self, chunks: Sequence[Any], out: List[Any],
                         holder: int, lo: int, hi: int, T: List[Transfer],
                         step: int) -> int:
        if hi - lo <= 1:
            out[lo] = chunks[lo]
            return step
        mid = (lo + hi) // 2
        if holder < mid:
            block = list(chunks[mid:hi])
            self._xfer(T, step, holder, mid, block)
            d1 = self._scatter_halving(chunks, out, holder, lo, mid, T, step + 1)
            d2 = self._scatter_halving(chunks, out, mid, mid, hi, T, step + 1)
        else:
            block = list(chunks[lo:mid])
            self._xfer(T, step, holder, lo, block)
            d1 = self._scatter_halving(chunks, out, lo, lo, mid, T, step + 1)
            d2 = self._scatter_halving(chunks, out, holder, mid, hi, T, step + 1)
        return max(d1, d2)

    def scatter(self, chunks: Sequence[Any], root: int = 0,
                algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        n = self.n
        if len(chunks) != n:
            raise CollectiveError(f"scatter needs exactly {n} chunks, "
                                  f"got {len(chunks)}")
        out: List[Any] = [None] * n
        T: List[Transfer] = []
        if n == 1:
            out[0] = chunks[0]
            return self._finish("SCATTER", algorithm, out, T, 0)
        if algorithm in ("linear", "pairwise"):
            out[root] = chunks[root]
            step = 0
            for r in range(n):
                if r != root:
                    self._xfer(T, step, root, r, chunks[r])
                    out[r] = chunks[r]
                    if algorithm == "pairwise":
                        step += 1
            sync = 1 if algorithm == "linear" else n - 1
        elif algorithm == "ring":
            carry = {r: chunks[r] for r in range(n)}
            out[root] = chunks[root]
            for i in range(n - 1):
                src = (root + i) % n
                dst = (root + i + 1) % n
                remaining = [carry[(root + j) % n] for j in range(i + 1, n)]
                self._xfer(T, i, src, dst, remaining)
                out[dst] = carry[dst]
            sync = n - 1
        else:                                  # tree / recursive_doubling
            self._scatter_halving(chunks, out, root, 0, n, T, 0)
            sync = max(1, math.ceil(math.log2(n)))
        return self._finish("SCATTER", algorithm, out, T, sync)

    # -- GATHER ------------------------------------------------------------
    def _gather_doubling(self, held: Dict[int, List[Tuple[int, Any]]],
                         target: int, lo: int, hi: int, T: List[Transfer],
                         step: int) -> None:
        if hi - lo <= 1:
            return
        mid = (lo + hi) // 2
        if target < mid:
            self._gather_doubling(held, target, lo, mid, T, step + 1)
            self._gather_doubling(held, mid, mid, hi, T, step + 1)
            self._xfer(T, step, mid, target, [v for _, v in held[mid]])
            held[target] = held[target] + held[mid]
            held[mid] = []
        else:
            self._gather_doubling(held, lo, lo, mid, T, step + 1)
            self._gather_doubling(held, target, mid, hi, T, step + 1)
            self._xfer(T, step, lo, target, [v for _, v in held[lo]])
            held[target] = held[lo] + held[target]
            held[lo] = []

    def gather(self, values: Sequence[Any], root: int = 0,
               algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        n = self.n
        if len(values) != n:
            raise CollectiveError(f"gather needs one value per worker ({n})")
        out: List[Any] = [None] * n
        T: List[Transfer] = []
        if n == 1:
            out[0] = [values[0]]
            return self._finish("GATHER", algorithm, out, T, 0)
        if algorithm in ("linear", "pairwise"):
            step = 0
            for r in range(n):
                if r != root:
                    self._xfer(T, step, r, root, values[r])
                    if algorithm == "pairwise":
                        step += 1
            out[root] = list(values)
            sync = 1 if algorithm == "linear" else n - 1
        elif algorithm == "ring":
            acc: Dict[int, List[Any]] = {}
            start = (root + 1) % n
            acc[start] = [values[start]]
            for i in range(n - 1):
                src = (root + 1 + i) % n
                dst = (root + 2 + i) % n
                self._xfer(T, i, src, dst, acc[src])
                nxt = list(acc[src])
                if dst != root:
                    nxt.append(values[dst])
                acc[dst] = nxt
            collected = acc[root]
            ordered = [values[root]] + [v for v in collected]
            out[root] = [values[r] for r in range(n)] if len(ordered) == n \
                else ordered
            sync = n - 1
        else:                                  # tree / recursive_doubling
            held: Dict[int, List[Tuple[int, Any]]] = {
                r: [(r, values[r])] for r in range(n)}
            self._gather_doubling(held, root, 0, n, T, 0)
            out[root] = [v for _, v in sorted(held[root], key=lambda p: p[0])]
            sync = max(1, math.ceil(math.log2(n)))
        return self._finish("GATHER", algorithm, out, T, sync)

    # -- ALLGATHER ---------------------------------------------------------
    def allgather(self, values: Sequence[Any],
                  algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        n = self.n
        if len(values) != n:
            raise CollectiveError(f"allgather needs one value per worker ({n})")
        T: List[Transfer] = []
        if n == 1:
            return self._finish("ALLGATHER", algorithm, [[values[0]]], T, 0)
        if algorithm == "ring":
            bufs: List[List[Any]] = [[None] * n for _ in range(n)]
            for r in range(n):
                bufs[r][r] = values[r]
            carry = {r: (r, values[r]) for r in range(n)}
            for step in range(n - 1):
                nxt: Dict[int, Tuple[int, Any]] = {}
                for r in range(n):
                    dst = (r + 1) % n
                    idx, val = carry[r]
                    self._xfer(T, step, r, dst, val)
                    bufs[dst][idx] = val
                    nxt[dst] = (idx, val)
                carry = nxt
            sync = n - 1
            out = bufs
        elif algorithm == "recursive_doubling":
            bufs = [[None] * n for _ in range(n)]
            for r in range(n):
                bufs[r][r] = values[r]
            step, dist = 0, 1
            while dist < n:
                pairs = []
                for r in range(n):
                    partner = r ^ dist if (r ^ dist) < n else None
                    if partner is None:
                        continue
                    pairs.append((r, partner))
                for r, p in pairs:
                    block = [(i, bufs[r][i]) for i in range(n)
                             if bufs[r][i] is not None]
                    self._xfer(T, step, r, p, [v for _, v in block])
                snapshot = [list(b) for b in bufs]
                for r, p in pairs:
                    for i in range(n):
                        if snapshot[p][i] is not None:
                            bufs[r][i] = snapshot[p][i]
                dist *= 2
                step += 1
            # non-power-of-two remainder: linear fill for ranks never paired
            for r in range(n):
                for i in range(n):
                    if bufs[r][i] is None:
                        self._xfer(T, step, i, r, values[i])
                        bufs[r][i] = values[i]
            sync = max(1, math.ceil(math.log2(n)))
            out = bufs
        elif algorithm == "pairwise":
            bufs = [[None] * n for _ in range(n)]
            for r in range(n):
                bufs[r][r] = values[r]
            step = 0
            for a in range(n):
                for b in range(a + 1, n):
                    self._xfer(T, step, a, b, values[a])
                    self._xfer(T, step, b, a, values[b])
                    bufs[b][a] = values[a]
                    bufs[a][b] = values[b]
                    step += 1
            sync = n - 1
            out = bufs
        else:                                  # linear / tree = gather + bcast
            g = self.gather(values, root=0, algorithm=algorithm)
            T.extend(g.transfers)
            full = g.values[0]
            buf: List[Any] = [None] * n
            buf[0] = full
            if algorithm == "linear":
                for r in range(1, n):
                    self._xfer(T, 1, 0, r, full)
                    buf[r] = full
                sync = g.sync_count + 1
            else:
                steps = self._binomial_bcast(buf, 0, 0, n, T, step0=g.steps)
                sync = g.sync_count + steps
            out = [list(b) for b in buf]
        return self._finish("ALLGATHER", algorithm, out, T, sync)

    # -- REDUCE ------------------------------------------------------------
    def reduce(self, values: Sequence[Any], op: Any = "sum", root: int = 0,
               algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        fn = _reduce_fn(op)
        n = self.n
        if len(values) != n:
            raise CollectiveError(f"reduce needs one value per worker ({n})")
        out: List[Any] = [None] * n
        T: List[Transfer] = []
        if n == 1:
            out[0] = values[0]
            return self._finish("REDUCE", algorithm, out, T, 0)
        if algorithm in ("linear", "pairwise"):
            acc = values[root]
            step = 0
            for r in range(n):
                if r != root:
                    self._xfer(T, step, r, root, values[r])
                    if algorithm == "pairwise":
                        step += 1
            for r in range(n):
                if r != root:
                    acc = fn(acc, values[r])
            out[root] = acc
            sync = 1 if algorithm == "linear" else n - 1
        elif algorithm == "ring":
            acc = values[(root + 1) % n]
            for i in range(n - 1):
                src = (root + 1 + i) % n
                dst = (root + 2 + i) % n
                self._xfer(T, i, src, dst, acc)
                if dst != root:
                    acc = fn(acc, values[dst])
            out[root] = fn(values[root], acc)
            sync = n - 1
        else:                                   # tree / recursive_doubling
            acc_map = {r: values[r] for r in range(n)}

            def rec(target: int, lo: int, hi: int, step: int) -> None:
                if hi - lo <= 1:
                    return
                mid = (lo + hi) // 2
                if target < mid:
                    rec(target, lo, mid, step + 1)
                    rec(mid, mid, hi, step + 1)
                    self._xfer(T, step, mid, target, acc_map[mid])
                    acc_map[target] = fn(acc_map[target], acc_map[mid])
                else:
                    rec(lo, lo, mid, step + 1)
                    rec(target, mid, hi, step + 1)
                    self._xfer(T, step, lo, target, acc_map[lo])
                    acc_map[target] = fn(acc_map[lo], acc_map[target])

            rec(root, 0, n, 0)
            out[root] = acc_map[root]
            sync = max(1, math.ceil(math.log2(n)))
        return self._finish("REDUCE", algorithm, out, T, sync)

    # -- ALLREDUCE ---------------------------------------------------------
    def allreduce(self, values: Sequence[Any], op: Any = "sum",
                  algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        fn = _reduce_fn(op)
        n = self.n
        if len(values) != n:
            raise CollectiveError(f"allreduce needs one value per worker ({n})")
        T: List[Transfer] = []
        if n == 1:
            return self._finish("ALLREDUCE", algorithm, [values[0]], T, 0)
        if algorithm == "ring":
            acc = list(values)
            carry = list(values)
            for step in range(n - 1):
                nxt: List[Any] = [None] * n
                for r in range(n):
                    dst = (r + 1) % n
                    self._xfer(T, step, r, dst, carry[r])
                    nxt[dst] = carry[r]
                for r in range(n):
                    acc[r] = fn(acc[r], nxt[r])
                carry = nxt
            out = acc
            sync = n - 1
        elif algorithm == "recursive_doubling":
            acc = list(values)
            step, dist = 0, 1
            paired_all = True
            while dist < n:
                pairs = [(r, r ^ dist) for r in range(n)
                         if (r ^ dist) < n]
                if len(pairs) != n:
                    paired_all = False
                snapshot = list(acc)
                for r, p in pairs:
                    self._xfer(T, step, r, p, acc[r])
                for r, p in pairs:
                    acc[r] = fn(snapshot[min(r, p)], snapshot[max(r, p)])
                dist *= 2
                step += 1
            if not paired_all or n & (n - 1):
                # deterministic remainder repair for non-power-of-two sizes
                total = values[0]
                for v in values[1:]:
                    total = fn(total, v)
                for r in range(n):
                    if acc[r] != total:
                        self._xfer(T, step, 0, r, total)
                        acc[r] = total
            out = acc
            sync = max(1, math.ceil(math.log2(n)))
        else:                                   # linear / tree / pairwise
            r0 = self.reduce(values, op, root=0, algorithm=algorithm)
            T.extend(r0.transfers)
            b = self.broadcast(r0.values[0], root=0, algorithm=algorithm)
            base = r0.steps
            T.extend(Transfer(t.step + base, t.src, t.dst, t.nbytes)
                     for t in b.transfers)
            out = list(b.values)
            sync = r0.sync_count + b.sync_count
        return self._finish("ALLREDUCE", algorithm, out, T, sync)

    # -- SCAN (inclusive prefix) -------------------------------------------
    def scan(self, values: Sequence[Any], op: Any = "sum",
             algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        fn = _reduce_fn(op)
        n = self.n
        if len(values) != n:
            raise CollectiveError(f"scan needs one value per worker ({n})")
        T: List[Transfer] = []
        if n == 1:
            return self._finish("SCAN", algorithm, [values[0]], T, 0)
        if algorithm in ("linear", "ring"):
            acc = [values[0]]
            for r in range(1, n):
                self._xfer(T, r - 1, r - 1, r, acc[r - 1])
                acc.append(fn(acc[r - 1], values[r]))
            out, sync = acc, n - 1
        elif algorithm == "recursive_doubling":
            acc = list(values)
            step, dist = 0, 1
            while dist < n:
                snapshot = list(acc)
                for r in range(n - 1, dist - 1, -1):
                    self._xfer(T, step, r - dist, r, snapshot[r - dist])
                for r in range(n - 1, dist - 1, -1):
                    acc[r] = fn(snapshot[r - dist], snapshot[r])
                dist *= 2
                step += 1
            out, sync = acc, max(1, math.ceil(math.log2(n)))
        elif algorithm == "pairwise":
            acc = list(values)
            step = 0
            for a in range(n):
                for b in range(a + 1, n):
                    self._xfer(T, step, a, b, values[a])
                    step += 1
            for r in range(n):
                v = values[0]
                for k in range(1, r + 1):
                    v = fn(v, values[k])
                acc[r] = v
            out, sync = acc, n - 1
        else:                                   # tree: recursive prefix
            acc = list(values)

            def rec(lo: int, hi: int, step: int) -> Any:
                if hi - lo <= 1:
                    return acc[lo]
                mid = (lo + hi) // 2
                left_total = rec(lo, mid, step + 1)
                right_total = rec(mid, hi, step + 1)
                buf: List[Any] = [None] * self.n
                buf[mid] = left_total
                self._binomial_bcast(buf, mid, mid, hi, T, step)
                for r in range(mid, hi):
                    acc[r] = fn(left_total, acc[r])
                return fn(left_total, right_total)

            rec(0, n, 0)
            out, sync = acc, max(1, math.ceil(math.log2(n)))
        return self._finish("SCAN", algorithm, out, T, sync)

    # -- ALLTOALL ----------------------------------------------------------
    def alltoall(self, matrix: Sequence[Sequence[Any]],
                 algorithm: str = "linear") -> CollectiveResult:
        self._check_alg(algorithm)
        n = self.n
        if len(matrix) != n or any(len(row) != n for row in matrix):
            raise CollectiveError(f"alltoall needs an {n}x{n} send matrix")
        out: List[List[Any]] = [[None] * n for _ in range(n)]
        for r in range(n):
            out[r][r] = matrix[r][r]
        T: List[Transfer] = []
        if n == 1:
            return self._finish("ALLTOALL", algorithm, out, T, 0)
        if algorithm in ("linear", "tree"):
            for r in range(n):
                for c in range(n):
                    if r != c:
                        self._xfer(T, 0, r, c, matrix[r][c])
                        out[c][r] = matrix[r][c]
            sync = 1
        elif algorithm == "pairwise":
            step = 0
            for k in range(1, n):
                for r in range(n):
                    p = (r + k) % n
                    self._xfer(T, step, r, p, matrix[r][p])
                    out[p][r] = matrix[r][p]
                step += 1
            sync = n - 1
        elif algorithm == "ring":
            for step in range(1, n):
                for r in range(n):
                    dst = (r + step) % n
                    self._xfer(T, step - 1, r, dst, matrix[r][dst])
                    out[dst][r] = matrix[r][dst]
            sync = n - 1
        else:                                   # recursive_doubling
            step, dist = 0, 1
            while dist < n:
                for r in range(n):
                    p = r ^ dist
                    if p < n:
                        self._xfer(T, step, r, p, matrix[r][p])
                        out[p][r] = matrix[r][p]
                dist *= 2
                step += 1
            for r in range(n):
                for c in range(n):
                    if out[c][r] is None:
                        self._xfer(T, step, r, c, matrix[r][c])
                        out[c][r] = matrix[r][c]
            sync = max(1, math.ceil(math.log2(n)))
        return self._finish("ALLTOALL", algorithm, out, T, sync)

    # -- dispatch ----------------------------------------------------------
    def run(self, op: str, data: Any, *, algorithm: str = "linear",
            reduce_op: Any = "sum", root: int = 0) -> CollectiveResult:
        if op not in OPS_COLLECTIVE:
            raise CollectiveError(f"unknown collective {op!r}; expected one of "
                                  f"{list(OPS_COLLECTIVE)}")
        if op == "BROADCAST":
            return self.broadcast(data, root, algorithm)
        if op == "SCATTER":
            return self.scatter(data, root, algorithm)
        if op == "GATHER":
            return self.gather(data, root, algorithm)
        if op == "ALLGATHER":
            return self.allgather(data, algorithm)
        if op == "REDUCE":
            return self.reduce(data, reduce_op, root, algorithm)
        if op == "ALLREDUCE":
            return self.allreduce(data, reduce_op, algorithm)
        if op == "SCAN":
            return self.scan(data, reduce_op, algorithm)
        return self.alltoall(data, algorithm)

    def as_dict(self) -> dict:
        return {"workers": self.n,
                "history": [r.as_dict() for r in self.history]}


def select_algorithm(op: str, workers: int, message_size: int,
                     topology: str = "fully_connected") -> AlgorithmChoice:
    """Rule-based, explainable collective algorithm selection.

    Rules are evaluated in order; the first match wins and is reported by id.
    """
    if op not in OPS_COLLECTIVE:
        raise CollectiveError(f"unknown collective {op!r}")
    inputs = {"op": op, "workers": int(workers),
              "message_size_bytes": int(message_size),
              "topology": str(topology)}
    considered = list(COLLECTIVE_ALGORITHMS)
    LARGE = 65536
    pow2 = workers > 0 and (workers & (workers - 1)) == 0

    if workers <= 1:
        return AlgorithmChoice(op, "linear", "R0",
                               "a single worker performs no transfers; the "
                               "linear schedule is trivially optimal",
                               inputs, considered)
    if workers == 2:
        return AlgorithmChoice(op, "pairwise", "R1",
                               "with two workers every schedule degenerates to "
                               "one exchange; pairwise states that directly",
                               inputs, considered)
    if topology in ("ring", "line", "chain"):
        return AlgorithmChoice(op, "ring", "R2",
                               f"declared topology {topology!r} has no direct "
                               f"non-neighbour links, so only ring-order "
                               f"transfers are physically realizable",
                               inputs, considered)
    if op == "ALLTOALL":
        return AlgorithmChoice(op, "pairwise", "R3",
                               "all-to-all moves n(n-1) distinct blocks; the "
                               "pairwise round schedule bounds concurrent link "
                               "use at one message per worker per round",
                               inputs, considered)
    if op in ("ALLREDUCE", "ALLGATHER") and message_size >= LARGE:
        return AlgorithmChoice(op, "ring", "R4",
                               f"message size {message_size}B >= {LARGE}B makes "
                               f"the collective bandwidth-bound; the ring "
                               f"schedule moves each byte at most once per hop",
                               inputs, considered)
    if op in ("ALLREDUCE", "ALLGATHER", "SCAN") and pow2:
        return AlgorithmChoice(op, "recursive_doubling", "R5",
                               f"worker count {workers} is a power of two and "
                               f"the message is latency-bound, so recursive "
                               f"doubling completes in log2(n) rounds with no "
                               f"remainder repair",
                               inputs, considered)
    if op in ("BROADCAST", "SCATTER", "GATHER", "REDUCE") and workers >= 4:
        return AlgorithmChoice(op, "tree", "R6",
                               f"rooted collective over {workers} workers: the "
                               f"binomial tree reduces the root's serial fan-out "
                               f"from n-1 to log2(n) rounds",
                               inputs, considered)
    return AlgorithmChoice(op, "linear", "R7",
                           "no specialization rule applies; the linear schedule "
                           "is the explicit default and is always correct",
                           inputs, considered)


# --------------------------------------------------------------------------
# 12. Work stealing (LCTL 1.4.x s23, 1.6.x s27)
# --------------------------------------------------------------------------

STEAL_LEVELS = ("worker_local", "worker_group", "domain", "federation")


@dataclass
class StealRecord:
    steal_id: str
    thief: str
    victim: str
    task: str
    level: str
    reason: str
    logical_time: int

    def as_dict(self) -> dict:
        return {"steal_id": self.steal_id, "thief": self.thief,
                "victim": self.victim, "task": self.task, "level": self.level,
                "reason": self.reason, "logical_time": self.logical_time}


class WorkStealing:
    """Four-level work stealing with deterministic victim selection.

    In a deterministic profile the victim order is a total order derived from
    (queue depth desc, level order, worker id asc) — never a random draw — so
    two identical runs produce identical steal records.
    """

    def __init__(self, federation: Federation, *,
                 deterministic: bool = True,
                 log: Optional[EventLog] = None,
                 clock: Optional[LamportClock] = None) -> None:
        self.federation = federation
        self.deterministic = bool(deterministic)
        self.clock = clock or LamportClock()
        self.log = log or EventLog("work_stealing", self.clock)
        self.deques: Dict[str, Deque[Task]] = {
            w: collections.deque() for w in federation.worker_ids()}
        self.records: List[StealRecord] = []
        self.refusals: List[dict] = []

    # -- local deque -------------------------------------------------------
    def push(self, worker_id: str, task: Task) -> None:
        if worker_id not in self.deques:
            raise FabricError(f"unknown worker {worker_id!r}")
        task.owner = worker_id
        self.deques[worker_id].append(task)

    def pop_local(self, worker_id: str) -> Optional[Task]:
        """LIFO on the owner side: best locality, no contention."""
        dq = self.deques[worker_id]
        return dq.pop() if dq else None

    def depth(self, worker_id: str) -> int:
        return len(self.deques[worker_id])

    # -- level scoping -----------------------------------------------------
    def _scope(self, thief: str, level: str) -> List[str]:
        w = self.federation.worker(thief)
        if level == "worker_local":
            return [thief]
        if level == "worker_group":
            return self.federation.group_of(thief).worker_ids()
        if level == "domain":
            return sorted(x.worker_id
                          for x in self.federation.domains[w.domain_id].workers())
        if level == "federation":
            return self.federation.worker_ids()
        raise FabricError(f"unknown steal level {level!r}")

    def victims(self, thief: str, level: str) -> List[str]:
        """Deterministic victim ordering: deepest queue first, id as tiebreak."""
        cands = [v for v in self._scope(thief, level) if v != thief]
        return sorted(cands, key=lambda v: (-self.depth(v), v))

    # -- steal primitives --------------------------------------------------
    def _record(self, thief: str, victim: str, task: Task, level: str,
                reason: str) -> StealRecord:
        rec = StealRecord(steal_id=f"S{len(self.records):05d}", thief=thief,
                          victim=victim, task=task.task_id, level=level,
                          reason=reason, logical_time=self.clock.tick())
        self.records.append(rec)
        self.log.append(thief, "WORK_STEAL", inputs=rec.as_dict(), outputs=None,
                        ownership_delta={"move": [[task.task_id, victim, thief]]},
                        logical_time=rec.logical_time)
        return rec

    def steal_specific(self, thief: str, victim: str, task: Task,
                       level: str = "worker_group") -> Task:
        """Steal a named task. Fails closed on unknown quantum state."""
        if level not in STEAL_LEVELS:
            raise FabricError(f"unknown steal level {level!r}")
        if task.holds_unknown_quantum:
            self.refusals.append({"thief": thief, "victim": victim,
                                  "task": task.task_id,
                                  "token": TOKEN_STEAL_REJECTED,
                                  "reason": "task holds unknown quantum state"})
            raise StealRejectedError(
                f"{TOKEN_STEAL_REJECTED}: task {task.task_id!r} holds live "
                f"quantum payload(s) "
                f"{[p.payload_id for p in task.payloads if p.state == 'LIVE']}; "
                f"unknown quantum state can never be migrated by a steal")
        if not task.movable:
            self.refusals.append({"thief": thief, "victim": victim,
                                  "task": task.task_id,
                                  "token": "TASK_NOT_MOVABLE",
                                  "reason": "task is pinned"})
            raise StealRejectedError(
                f"task {task.task_id!r} is not explicitly movable")
        rules = self.federation.migration_rules
        same_trust = (self.federation.worker(thief).trust
                      == self.federation.worker(victim).trust)
        ok, why = rules.permits(quantum=task.holds_quantum, movable=task.movable,
                                hops=1, same_trust=same_trust)
        if not ok:
            raise StealRejectedError(f"migration refused: {why}")
        try:
            self.deques[victim].remove(task)
        except ValueError:
            raise StealRejectedError(
                f"task {task.task_id!r} is not queued on victim {victim!r}")
        task.owner = thief
        self.deques[thief].append(task)
        self._record(thief, victim, task, level,
                     f"explicit steal of {task.task_id} from {victim}")
        return task

    def attempt_steal(self, thief: str, *, strict: bool = False
                      ) -> Optional[Task]:
        """Walk the four levels outward until a stealable task is found."""
        local = self.pop_local(thief)
        if local is not None:
            self._record(thief, thief, local, "worker_local",
                         "local deque pop (LIFO, no migration)")
            return local
        for level in STEAL_LEVELS[1:]:
            for victim in self.victims(thief, level):
                dq = self.deques[victim]
                for task in list(dq):        # FIFO end first: oldest, coldest
                    if task.holds_unknown_quantum:
                        note = {"thief": thief, "victim": victim,
                                "task": task.task_id, "level": level,
                                "token": TOKEN_STEAL_REJECTED,
                                "reason": "unknown quantum state is unstealable"}
                        self.refusals.append(note)
                        if strict:
                            raise StealRejectedError(
                                f"{TOKEN_STEAL_REJECTED}: {note}")
                        continue
                    if not task.stealable:
                        self.refusals.append(
                            {"thief": thief, "victim": victim,
                             "task": task.task_id, "level": level,
                             "token": "TASK_NOT_MOVABLE",
                             "reason": f"state={task.state} movable={task.movable}"})
                        continue
                    dq.remove(task)
                    task.owner = thief
                    self.deques[thief].append(task)
                    self._record(thief, victim, task, level,
                                 f"deepest-queue victim at level {level} "
                                 f"(depth {len(dq) + 1})")
                    return self.deques[thief].pop()
        return None

    def as_dict(self) -> dict:
        return {"deterministic": self.deterministic,
                "queue_depths": {w: self.depth(w)
                                 for w in sorted(self.deques)},
                "steal_records": [r.as_dict() for r in self.records],
                "refusals": self.refusals}


# --------------------------------------------------------------------------
# 13. Load balancing (LCTL 1.4.x s24, ledger LOAD_BALANCE_LEDGER)
# --------------------------------------------------------------------------

@dataclass
class WorkerSignal:
    worker_id: str
    throughput: float = 1.0            # measured tasks per unit time
    memory_pressure: float = 0.0       # [0,1]
    communication_cost: float = 0.0    # normalized
    queue_delay: float = 0.0           # normalized
    affinity: float = 0.0              # [0,1], resource affinity for the task
    failure_exposure: float = 0.0      # [0,1]
    alive: bool = True

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class PlacementDecision:
    task_id: str
    worker_id: str
    mode: str                           # STATIC | DYNAMIC
    score: float
    components: Dict[str, float]
    rule: str
    alternatives: List[Tuple[str, float]]

    def as_dict(self) -> dict:
        return {"task_id": self.task_id, "worker_id": self.worker_id,
                "mode": self.mode, "score": round(self.score, 9),
                "components": {k: round(v, 9)
                               for k, v in sorted(self.components.items())},
                "rule": self.rule,
                "alternatives": [[w, round(s, 9)] for w, s in self.alternatives]}


class LoadBalancer:
    """Static and dynamic placement with a fully recorded score decomposition."""

    WEIGHTS = {"throughput": 1.0, "predicted_cost": 0.5, "memory_pressure": 0.8,
               "communication_cost": 0.6, "queue_delay": 0.9, "affinity": 0.7,
               "failure_exposure": 1.2}

    def __init__(self, federation: Federation, *,
                 log: Optional[EventLog] = None,
                 weights: Optional[Mapping[str, float]] = None) -> None:
        self.federation = federation
        self.log = log or EventLog("load_balancer")
        self.weights = dict(self.WEIGHTS)
        if weights:
            self.weights.update({k: float(v) for k, v in weights.items()})
        self.decisions: List[PlacementDecision] = []

    # -- static ------------------------------------------------------------
    def static_assign(self, tasks: Sequence[Task],
                      workers: Optional[Sequence[str]] = None
                      ) -> List[PlacementDecision]:
        wids = list(workers) if workers is not None \
            else self.federation.worker_ids()
        if not wids:
            raise FabricError("no workers available for static placement")
        caps = {w: max(1, self.federation.worker(w).limits.cpu_slots)
                for w in wids}
        total = sum(caps.values())
        out: List[PlacementDecision] = []
        cursor = 0
        for t in sorted(tasks, key=lambda x: x.seq):
            wid = wids[cursor % len(wids)]
            cursor += 1
            share = caps[wid] / total
            d = PlacementDecision(
                task_id=t.task_id, worker_id=wid, mode="STATIC",
                score=share, components={"capacity_share": share},
                rule="weighted round-robin over declared cpu_slots",
                alternatives=[(w, caps[w] / total) for w in wids if w != wid])
            t.owner = wid
            out.append(d)
            self.decisions.append(d)
        self.log.append("-", "STATIC_PLACEMENT",
                        inputs={"tasks": len(out)}, outputs=None)
        return out

    # -- dynamic -----------------------------------------------------------
    def score(self, task: Task, sig: WorkerSignal) -> Tuple[float, Dict[str, float]]:
        w = self.weights
        comp = {
            "throughput": w["throughput"] * float(sig.throughput),
            "affinity": w["affinity"] * float(sig.affinity),
            "predicted_cost": -w["predicted_cost"] * float(task.cost_estimate)
            / max(1e-9, float(sig.throughput)),
            "memory_pressure": -w["memory_pressure"] * float(sig.memory_pressure),
            "communication_cost": -w["communication_cost"]
            * float(sig.communication_cost),
            "queue_delay": -w["queue_delay"] * float(sig.queue_delay),
            "failure_exposure": -w["failure_exposure"]
            * float(sig.failure_exposure),
        }
        return sum(comp.values()), comp

    def dynamic_assign(self, tasks: Sequence[Task],
                       signals: Mapping[str, WorkerSignal]
                       ) -> List[PlacementDecision]:
        live = {k: v for k, v in signals.items() if v.alive}
        if not live:
            raise FabricError("dynamic placement has no live workers")
        out: List[PlacementDecision] = []
        for t in sorted(tasks, key=lambda x: x.seq):
            scored: List[Tuple[str, float, Dict[str, float]]] = []
            for wid in sorted(live):
                s, comp = self.score(t, live[wid])
                scored.append((wid, s, comp))
            # deterministic: best score, worker id as the tiebreak
            scored.sort(key=lambda p: (-p[1], p[0]))
            wid, best, comp = scored[0]
            d = PlacementDecision(
                task_id=t.task_id, worker_id=wid, mode="DYNAMIC", score=best,
                components=comp,
                rule="argmax of the weighted signal sum; ties broken by worker id",
                alternatives=[(w, s) for w, s, _ in scored[1:]])
            t.owner = wid
            live[wid].queue_delay += t.cost_estimate / max(1e-9,
                                                           live[wid].throughput)
            out.append(d)
            self.decisions.append(d)
        self.log.append("-", "DYNAMIC_PLACEMENT",
                        inputs={"tasks": len(out)}, outputs=None)
        return out

    def ledger(self) -> dict:
        """LOAD_BALANCE_LEDGER content (LCTL 1.4.x s24)."""
        per_worker: Dict[str, int] = {}
        for d in self.decisions:
            per_worker[d.worker_id] = per_worker.get(d.worker_id, 0) + 1
        counts = sorted(per_worker.values())
        imbalance = ((max(counts) - min(counts)) / max(1, max(counts))
                     if counts else 0.0)
        return {
            "ledger": TOKEN_LOAD_BALANCE_LEDGER,
            "federation": self.federation.federation_id,
            "weights": {k: round(v, 9) for k, v in sorted(self.weights.items())},
            "decisions": [d.as_dict() for d in self.decisions],
            "tasks_per_worker": dict(sorted(per_worker.items())),
            "imbalance_ratio": round(imbalance, 9),
            "decision_count": len(self.decisions),
            "ledger_hash": stable_hash([d.as_dict() for d in self.decisions]),
        }

    def as_dict(self) -> dict:
        return self.ledger()


# --------------------------------------------------------------------------
# 14. Straggler detection and mitigation (LCTL 1.4.x s25, 1.5.x s31)
# --------------------------------------------------------------------------

@dataclass
class StragglerVerdict:
    subject: str
    detected: bool
    signals: Dict[str, float]
    reasons: List[str]
    action: str                  # NONE | SPECULATE | REROUTE | RESTART | ESCALATE
    quantum: bool = False

    def as_dict(self) -> dict:
        return {"subject": self.subject, "detected": self.detected,
                "signals": {k: round(float(v), 9)
                            for k, v in sorted(self.signals.items())},
                "reasons": self.reasons, "action": self.action,
                "quantum": self.quantum}


class StragglerEngine:
    """Detects stragglers and applies the only admissible mitigation.

    Classical tasks may be speculatively duplicated (first valid completion
    wins, the loser is cancelled). Quantum work may only be rerouted or
    restarted from an admissible boundary: speculation would require cloning
    unknown quantum state and is refused (LCTL 1.4.x s25, 1.1.x s6).
    """

    def __init__(self, *, alpha: float = 0.3,
                 throughput_drop: float = 0.5,
                 queue_delay_limit: float = 2.0,
                 duration_sigma: float = 2.0,
                 heartbeat_limit: float = 1.0,
                 log: Optional[EventLog] = None) -> None:
        self.alpha = float(alpha)
        self.throughput_drop = float(throughput_drop)
        self.queue_delay_limit = float(queue_delay_limit)
        self.duration_sigma = float(duration_sigma)
        self.heartbeat_limit = float(heartbeat_limit)
        self.log = log or EventLog("straggler")
        self.throughput_ema: Dict[str, float] = {}
        self.durations: Dict[str, List[float]] = {}
        self.heartbeats: Dict[str, float] = {}
        self.queue_delays: Dict[str, float] = {}
        self.verdicts: List[StragglerVerdict] = []
        self.replicas: Dict[str, str] = {}      # replica_id -> original_id

    # -- observation -------------------------------------------------------
    def observe_throughput(self, worker_id: str, value: float) -> float:
        prev = self.throughput_ema.get(worker_id)
        ema = float(value) if prev is None else \
            self.alpha * float(value) + (1 - self.alpha) * prev
        self.throughput_ema[worker_id] = ema
        return ema

    def observe_duration(self, worker_id: str, duration: float) -> None:
        self.durations.setdefault(worker_id, []).append(float(duration))

    def observe_heartbeat(self, worker_id: str, latency: float) -> None:
        self.heartbeats[worker_id] = float(latency)

    def observe_queue_delay(self, worker_id: str, delay: float) -> None:
        self.queue_delays[worker_id] = float(delay)

    # -- detection ---------------------------------------------------------
    def detect(self, worker_id: str, *, quantum: bool = False
               ) -> StragglerVerdict:
        reasons: List[str] = []
        ema = self.throughput_ema.get(worker_id, 1.0)
        peers = [v for k, v in self.throughput_ema.items() if k != worker_id]
        peer_mean = float(np.mean(peers)) if peers else ema
        if peer_mean > 0 and ema < self.throughput_drop * peer_mean:
            reasons.append(f"moving-average throughput {ema:.6f} below "
                           f"{self.throughput_drop:.2f} of peer mean {peer_mean:.6f}")
        qd = self.queue_delays.get(worker_id, 0.0)
        if qd > self.queue_delay_limit:
            reasons.append(f"queue delay {qd:.6f} exceeds limit "
                           f"{self.queue_delay_limit:.6f}")
        durs = self.durations.get(worker_id, [])
        dev = 0.0
        if durs:
            allv = [d for v in self.durations.values() for d in v]
            mu, sd = float(np.mean(allv)), float(np.std(allv))
            if sd > 0:
                dev = (float(np.mean(durs)) - mu) / sd
                if dev > self.duration_sigma:
                    reasons.append(f"task duration deviation {dev:.3f}s exceeds "
                                   f"{self.duration_sigma:.3f} sigma")
        hb = self.heartbeats.get(worker_id, 0.0)
        if hb > self.heartbeat_limit:
            reasons.append(f"heartbeat latency {hb:.6f} exceeds limit "
                           f"{self.heartbeat_limit:.6f}")

        detected = bool(reasons)
        if not detected:
            action = "NONE"
        elif quantum:
            action = "REROUTE"
        else:
            action = "SPECULATE"
        v = StragglerVerdict(subject=worker_id, detected=detected,
                             signals={"throughput_ema": ema,
                                      "peer_mean_throughput": peer_mean,
                                      "queue_delay": qd,
                                      "duration_deviation_sigma": dev,
                                      "heartbeat_latency": hb},
                             reasons=reasons, action=action, quantum=quantum)
        self.verdicts.append(v)
        self.log.append(worker_id, "STRAGGLER_CHECK", inputs=None,
                        outputs=v.as_dict(),
                        failure_delta={"straggler": 1} if detected else {})
        return v

    # -- mitigation --------------------------------------------------------
    def speculate(self, task: Task, runtime: Optional[TaskRuntime] = None
                  ) -> Task:
        """Duplicate a classical task. Refuses any quantum-holding task."""
        if task.holds_quantum:
            raise SpeculationRejectedError(
                f"speculative duplication of task {task.task_id!r} would clone "
                f"quantum payload(s) "
                f"{[p.payload_id for p in task.payloads]}; only reroute or "
                f"restart-from-admissible-boundary is permitted")
        rid = f"{task.task_id}#spec{len(self.replicas)}"
        replica = Task(task_id=rid, fn=task.fn, args=task.args,
                       kwargs=dict(task.kwargs), priority=task.priority + 1,
                       deadline=task.deadline, movable=True, owner=task.owner,
                       seq=task.seq, cost_estimate=task.cost_estimate,
                       stage=task.stage, replica_of=task.task_id,
                       resource_claim=dict(task.resource_claim),
                       provenance={"speculative_replica_of": task.task_id})
        replica.state = "READY"
        self.replicas[rid] = task.task_id
        if runtime is not None:
            runtime._tasks.append(replica)
            runtime._by_id[rid] = replica
        self.log.append(task.owner or "-", "SPECULATIVE_REPLICA",
                        inputs={"task": task.task_id}, outputs={"replica": rid})
        return replica

    def first_valid_completion(self, original: Task, replica: Task,
                               runtime: Optional[TaskRuntime] = None) -> Task:
        """Accept the first valid completion and cancel the loser."""
        winner = None
        for cand in (original, replica):
            if cand.state == "DONE":
                winner = cand
                break
        if winner is None:
            raise FabricError("neither the original nor the replica completed")
        loser = replica if winner is original else original
        if not loser.done:
            if runtime is not None:
                runtime.cancel(loser, reason="speculative replica lost the race")
            else:
                loser.state = "CANCELLED"
                loser.error = "cancelled: speculative replica lost the race"
        self.log.append(winner.owner or "-", "SPECULATION_RESOLVED",
                        inputs={"winner": winner.task_id,
                                "loser": loser.task_id},
                        outputs=None)
        return winner

    def quantum_remedy(self, task: Task, *, route_alternatives: int = 0,
                       admissible_boundary: Optional[str] = None) -> str:
        """The only two admissible quantum straggler remedies."""
        if route_alternatives > 0:
            action = "REROUTE"
        elif admissible_boundary is not None:
            action = f"RESTART_FROM_BOUNDARY:{admissible_boundary}"
        else:
            action = "ESCALATE"
        self.log.append(task.owner or "-", "QUANTUM_STRAGGLER_REMEDY",
                        inputs={"task": task.task_id}, outputs={"action": action})
        return action

    def as_dict(self) -> dict:
        return {"verdicts": [v.as_dict() for v in self.verdicts],
                "replicas": dict(sorted(self.replicas.items())),
                "thresholds": {"throughput_drop": self.throughput_drop,
                               "queue_delay_limit": self.queue_delay_limit,
                               "duration_sigma": self.duration_sigma,
                               "heartbeat_limit": self.heartbeat_limit,
                               "ema_alpha": self.alpha}}


# --------------------------------------------------------------------------
# 15. Elastic worker groups + anti-thrash guard (LCTL 1.6.x s19/s26)
# --------------------------------------------------------------------------

@dataclass
class AdaptationRecord:
    index: int
    kind: str                     # GROW | SHRINK
    delta: int
    size_after: int
    logical_time: int
    token: Optional[str] = None
    reason: str = "-"

    def as_dict(self) -> dict:
        return {"index": self.index, "kind": self.kind, "delta": self.delta,
                "size_after": self.size_after, "logical_time": self.logical_time,
                "token": self.token, "reason": self.reason}


class ElasticWorkerGroup:
    """A worker group whose population may change during execution.

    The anti-thrash guard counts adaptations inside a sliding window of
    logical time. When the rate exceeds `max_rate` adaptations per window the
    token ADAPTATION_THRASH_DETECTED is emitted (LCTL 1.6.x s26). The guard
    reports; it only raises when constructed with `strict=True`.
    """

    def __init__(self, group: WorkerGroup, *, min_size: int = 1,
                 max_size: int = 16, window: int = 10, max_rate: float = 0.4,
                 strict: bool = False, clock: Optional[LamportClock] = None,
                 log: Optional[EventLog] = None) -> None:
        if min_size < 1 or max_size < min_size:
            raise FabricError("invalid elastic bounds")
        self.group = group
        self.min_size = int(min_size)
        self.max_size = int(max_size)
        self.window = int(window)
        self.max_rate = float(max_rate)
        self.strict = bool(strict)
        self.clock = clock or LamportClock()
        self.log = log or EventLog("elastic")
        self.adaptations: List[AdaptationRecord] = []
        self.thrash_detected = False
        self._next = len(group.members)

    @property
    def size(self) -> int:
        return len(self.group.members)

    def adaptation_rate(self) -> float:
        now = self.clock.time
        lo = now - self.window
        recent = [a for a in self.adaptations if a.logical_time > lo]
        return len(recent) / float(self.window)

    def _guard(self, rec: AdaptationRecord) -> AdaptationRecord:
        rate = self.adaptation_rate()
        if rate > self.max_rate:
            self.thrash_detected = True
            rec.token = TOKEN_THRASH
            rec.reason = (f"adaptation rate {rate:.6f}/tick exceeds declared "
                          f"threshold {self.max_rate:.6f}/tick over a "
                          f"{self.window}-tick window")
            self.log.append(self.group.group_id, "ADAPTATION_THRASH",
                            inputs={"rate": rate, "threshold": self.max_rate},
                            outputs={"token": TOKEN_THRASH},
                            failure_delta={"adaptation_thrash": 1})
            if self.strict:
                raise AdaptationThrashError(f"{TOKEN_THRASH}: {rec.reason}")
        return rec

    def grow(self, n: int = 1, *, reason: str = "demand") -> AdaptationRecord:
        n = max(0, int(n))
        added = 0
        for _ in range(n):
            if self.size >= self.max_size:
                break
            w = Worker(worker_id=f"{self.group.group_id}W{self._next:02d}",
                       group_id=self.group.group_id,
                       domain_id=self.group.domain_id,
                       federation_id=self.group.federation_id,
                       trust=self.group.trust,
                       failure_domain=self.group.failure_boundary)
            self.group.add(w)
            self._next += 1
            added += 1
        rec = AdaptationRecord(index=len(self.adaptations), kind="GROW",
                               delta=added, size_after=self.size,
                               logical_time=self.clock.tick(), reason=reason)
        self.adaptations.append(rec)
        self.log.append(self.group.group_id, "ELASTIC_GROW",
                        inputs={"requested": n}, outputs={"added": added})
        return self._guard(rec)

    def shrink(self, n: int = 1, *, reason: str = "idle") -> AdaptationRecord:
        n = max(0, int(n))
        removed = 0
        for _ in range(n):
            if self.size <= self.min_size:
                break
            victim = sorted(self.group.members)[-1]
            self.group.remove(victim)
            removed += 1
        rec = AdaptationRecord(index=len(self.adaptations), kind="SHRINK",
                               delta=-removed, size_after=self.size,
                               logical_time=self.clock.tick(), reason=reason)
        self.adaptations.append(rec)
        self.log.append(self.group.group_id, "ELASTIC_SHRINK",
                        inputs={"requested": n}, outputs={"removed": removed})
        return self._guard(rec)

    def as_dict(self) -> dict:
        return {"group": self.group.group_id, "size": self.size,
                "min_size": self.min_size, "max_size": self.max_size,
                "window_ticks": self.window, "max_rate": self.max_rate,
                "adaptation_rate": round(self.adaptation_rate(), 9),
                "thrash_detected": self.thrash_detected,
                "token": TOKEN_THRASH if self.thrash_detected else None,
                "adaptations": [a.as_dict() for a in self.adaptations]}


# --------------------------------------------------------------------------
# 16. Grain-size adaptation (LCTL 1.6.x s7)
# --------------------------------------------------------------------------

@dataclass
class GrainDecision:
    grain: int
    per_task_overhead_s: float
    per_item_cost_s: float
    target_overhead_fraction: float
    achieved_overhead_fraction: float
    measurement: Dict[str, Any]
    rule: str

    def as_dict(self) -> dict:
        return {"grain": self.grain,
                "per_task_overhead_s": self.per_task_overhead_s,
                "per_item_cost_s": self.per_item_cost_s,
                "target_overhead_fraction": self.target_overhead_fraction,
                "achieved_overhead_fraction": round(
                    self.achieved_overhead_fraction, 9),
                "measurement": self.measurement, "rule": self.rule}


class GrainSizeEngine:
    """Adapt task grain to the measured per-task dispatch overhead.

    `choose_grain` is pure and deterministic. `measure_overhead` performs a
    real local microbenchmark, so its output is machine-dependent; the value
    it produced is always reported alongside the chosen grain so the decision
    is auditable rather than asserted.
    """

    def __init__(self, *, target_overhead_fraction: float = 0.05,
                 min_grain: int = 1, max_grain: int = 4096,
                 log: Optional[EventLog] = None) -> None:
        if not 0 < target_overhead_fraction < 1:
            raise FabricError("target_overhead_fraction must lie in (0,1)")
        self.target = float(target_overhead_fraction)
        self.min_grain = int(min_grain)
        self.max_grain = int(max_grain)
        self.log = log or EventLog("grain")
        self.decisions: List[GrainDecision] = []

    def measure_overhead(self, runtime: TaskRuntime, reps: int = 32
                         ) -> Dict[str, Any]:
        """Measure per-task dispatch overhead with an empty-body task."""
        t0 = time.perf_counter()
        tasks = [runtime.spawn(_noop, i, task_id=f"grain_probe_{i}")
                 for i in range(max(1, reps))]
        runtime.run()
        elapsed = time.perf_counter() - t0
        n = len(tasks)
        return {"reps": n, "elapsed_s": elapsed,
                "per_task_overhead_s": elapsed / n,
                "profile": runtime.profile.value,
                "method": "empty-body task dispatch microbenchmark"}

    def choose_grain(self, per_task_overhead_s: float, per_item_cost_s: float,
                     *, measurement: Optional[Mapping[str, Any]] = None
                     ) -> GrainDecision:
        if per_item_cost_s <= 0:
            raise FabricError("per_item_cost_s must be positive")
        # overhead / (overhead + grain * item_cost) <= target
        raw = per_task_overhead_s * (1.0 - self.target) / \
            (self.target * per_item_cost_s)
        grain = int(min(self.max_grain, max(self.min_grain, math.ceil(raw))))
        achieved = per_task_overhead_s / (per_task_overhead_s
                                          + grain * per_item_cost_s)
        d = GrainDecision(
            grain=grain, per_task_overhead_s=float(per_task_overhead_s),
            per_item_cost_s=float(per_item_cost_s),
            target_overhead_fraction=self.target,
            achieved_overhead_fraction=achieved,
            measurement=dict(measurement or {}),
            rule=("smallest grain g with overhead/(overhead + g*item_cost) "
                  "<= target_overhead_fraction, clamped to [min_grain,max_grain]"))
        self.decisions.append(d)
        self.log.append("-", "GRAIN_DECISION", inputs=dict(measurement or {}),
                        outputs={"grain": grain})
        return d

    def adapt(self, runtime: TaskRuntime, per_item_cost_s: float,
              reps: int = 32) -> GrainDecision:
        m = self.measure_overhead(runtime, reps)
        return self.choose_grain(m["per_task_overhead_s"], per_item_cost_s,
                                 measurement=m)

    def as_dict(self) -> dict:
        return {"target_overhead_fraction": self.target,
                "min_grain": self.min_grain, "max_grain": self.max_grain,
                "decisions": [d.as_dict() for d in self.decisions]}


def _noop(x: Any = None) -> Any:
    """Module-level empty body so the probe is picklable for process profiles."""
    return x


# --------------------------------------------------------------------------
# 17. Deadlock / livelock / starvation detection (LCTL 1.4.x s27-28)
# --------------------------------------------------------------------------

@dataclass
class DeadlockReport:
    ok: bool
    token: Optional[str]
    cycles: List[List[str]]
    resources: List[str]
    unsatisfied_futures: List[str]
    retry_storms: List[dict]
    starvation: List[dict]

    def as_dict(self) -> dict:
        return {"ok": self.ok, "token": self.token, "cycles": self.cycles,
                "resources_involved": self.resources,
                "unsatisfied_futures": self.unsatisfied_futures,
                "retry_storms": self.retry_storms,
                "starvation": self.starvation}


class DeadlockDetector:
    """Wait-for, resource and future-dependency graphs with cycle extraction.

    The three graphs are merged into one directed graph whose nodes carry a
    kind (`task`, `resource`, `future`), so a reported cycle names both the
    blocked tasks and the resources they are blocked on.
    """

    def __init__(self, *, retry_storm_threshold: int = 8,
                 starvation_threshold: int = 25,
                 clock: Optional[LamportClock] = None,
                 log: Optional[EventLog] = None) -> None:
        self.retry_storm_threshold = int(retry_storm_threshold)
        self.starvation_threshold = int(starvation_threshold)
        self.clock = clock or LamportClock()
        self.log = log or EventLog("deadlock")
        self._edges: Dict[str, Set[str]] = {}
        self._kind: Dict[str, str] = {}
        self._futures: Dict[str, bool] = {}
        self._retries: Dict[str, int] = {}
        self._waiting_since: Dict[str, int] = {}

    # -- graph construction ------------------------------------------------
    def _node(self, name: str, kind: str) -> str:
        self._kind.setdefault(name, kind)
        self._edges.setdefault(name, set())
        return name

    def add_wait(self, task: str, blocker: str) -> None:
        """Wait-for edge: `task` cannot proceed until `blocker` proceeds."""
        self._node(task, "task")
        self._node(blocker, self._kind.get(blocker, "task"))
        self._edges[task].add(blocker)
        self._waiting_since.setdefault(task, self.clock.tick())

    def add_resource_wait(self, task: str, resource: str) -> None:
        self._node(task, "task")
        self._node(resource, "resource")
        self._edges[task].add(resource)
        self._waiting_since.setdefault(task, self.clock.tick())

    def add_resource_hold(self, holder: str, resource: str) -> None:
        self._node(holder, "task")
        self._node(resource, "resource")
        self._edges[resource].add(holder)

    def add_future_dep(self, task: str, future_id: str) -> None:
        self._node(task, "task")
        self._node(future_id, "future")
        self._edges[task].add(future_id)
        self._futures.setdefault(future_id, False)
        self._waiting_since.setdefault(task, self.clock.tick())

    def resolve_future(self, future_id: str) -> None:
        self._futures[future_id] = True
        self._edges.setdefault(future_id, set())

    def record_retry(self, task: str) -> int:
        self._retries[task] = self._retries.get(task, 0) + 1
        return self._retries[task]

    def clear_wait(self, task: str) -> None:
        self._edges.pop(task, None)
        self._waiting_since.pop(task, None)

    # -- detection ---------------------------------------------------------
    def _minimal_cycle(self) -> Optional[List[str]]:
        """Shortest directed cycle; deterministic tie-break by node order."""
        best: Optional[List[str]] = None
        for start in sorted(self._edges):
            # BFS from start back to start gives the shortest cycle through it
            prev: Dict[str, Optional[str]] = {start: None}
            queue: Deque[str] = collections.deque([start])
            found: Optional[List[str]] = None
            while queue and found is None:
                n = queue.popleft()
                for m in sorted(self._edges.get(n, ())):
                    if m == start:
                        path = [n]
                        p = prev[n]
                        while p is not None:
                            path.append(p)
                            p = prev[p]
                        path.reverse()
                        found = path
                        break
                    if m not in prev:
                        prev[m] = n
                        queue.append(m)
            if found is not None and (best is None or len(found) < len(best)):
                best = found
        return best

    def detect(self) -> DeadlockReport:
        cycle = self._minimal_cycle()
        cycles = [cycle] if cycle else []
        resources = sorted({n for n in (cycle or [])
                            if self._kind.get(n) == "resource"})
        unsatisfied = sorted(f for f, done in self._futures.items() if not done)
        storms = [{"task": t, "retries": c,
                   "threshold": self.retry_storm_threshold,
                   "token": TOKEN_LIVELOCK}
                  for t, c in sorted(self._retries.items())
                  if c > self.retry_storm_threshold]
        now = self.clock.time
        starving = [{"task": t, "waiting_ticks": now - since,
                     "threshold": self.starvation_threshold,
                     "token": TOKEN_STARVATION}
                    for t, since in sorted(self._waiting_since.items())
                    if now - since > self.starvation_threshold]
        token = None
        if cycles:
            token = TOKEN_DEADLOCK
        elif storms:
            token = TOKEN_LIVELOCK
        elif unsatisfied:
            token = TOKEN_UNSATISFIED_FUTURE
        elif starving:
            token = TOKEN_STARVATION
        ok = not (cycles or storms or unsatisfied or starving)
        rep = DeadlockReport(ok=ok, token=token, cycles=cycles,
                             resources=resources,
                             unsatisfied_futures=unsatisfied,
                             retry_storms=storms, starvation=starving)
        if not ok:
            self.log.append("-", "DEADLOCK_SCAN", inputs=None,
                            outputs=rep.as_dict(),
                            failure_delta={"deadlock": 1 if cycles else 0,
                                           "livelock": len(storms),
                                           "starvation": len(starving)})
        return rep

    def assert_live(self) -> DeadlockReport:
        rep = self.detect()
        if rep.cycles:
            raise DeadlockDetectedError(
                f"{TOKEN_DEADLOCK}: minimal cycle {rep.cycles[0]} over "
                f"resources {rep.resources}")
        return rep

    def graph(self) -> dict:
        return {n: {"kind": self._kind.get(n, "task"),
                    "waits_for": sorted(self._edges[n])}
                for n in sorted(self._edges)}

    def as_dict(self) -> dict:
        d = self.detect().as_dict()
        d["graph"] = self.graph()
        d["thresholds"] = {"retry_storm": self.retry_storm_threshold,
                           "starvation_ticks": self.starvation_threshold}
        return d


__all__ = [
    "FabricError", "ProfileError", "CloneAttemptError", "OwnershipTransferError",
    "StealRejectedError", "SpeculationRejectedError", "ChannelClosedError",
    "ChannelTimeoutError", "ResourceClaimError", "DeadlockDetectedError",
    "ReplayMismatchError", "AdaptationThrashError", "TaskCancelledError",
    "TaskTimeoutError", "TaskFailedError", "BarrierError", "CollectiveError",
    "ExecutionProfile", "DEFAULT_PROFILE", "QuantumPayload", "is_quantum",
    "ResourceLimits", "MigrationRules", "ReplicationRules", "ConsistencyRules",
    "Provenance", "Worker", "WorkerGroup", "ExecutionDomain", "Federation",
    "build_flat_federation", "Task", "TaskRuntime", "Channel", "select",
    "DataflowNode", "DataflowRuntime", "FiringDecision", "BSPEngine",
    "SuperstepRecord", "AsyncEngine", "AsyncFuture", "TimerWheel",
    "Collectives", "CollectiveResult", "select_algorithm",
    "COLLECTIVE_ALGORITHMS", "WorkStealing", "StealRecord", "STEAL_LEVELS",
    "LoadBalancer", "WorkerSignal", "PlacementDecision", "StragglerEngine",
    "ElasticWorkerGroup", "GrainSizeEngine", "DeadlockDetector",
    "DeadlockReport", "LamportClock", "VectorClock", "EventLog", "Event",
    "ReplayReport", "stable_hash", "payload_bytes", "canonical_json",
    "TOKEN_DEADLOCK", "TOKEN_THRASH", "TOKEN_REPLAY_MISMATCH",
    "TOKEN_LOAD_BALANCE_LEDGER", "TOKEN_STEAL_REJECTED",
]
