"""
Failure injection, recovery classification, supervision and checkpointing.

Implements:
  * LCTL 1.2.x s26-28   failure model, failure domains, recovery vocabulary
  * LCTL 1.3.x s28-29   deterministic failure injection and recovery classes
  * LCTL 1.4.x s44-45   supervision hierarchy and recovery transactions
  * LCTL 1.5.x s48-50   federated checkpoint scopes and campaign evidence
  * LCTL 1.6.x s81      reproducible resilience campaigns

Exit-gate tokens covered:
    FAILURE_INJECTION_OPERATIONAL, RECOVERY_CLASSIFICATION_PASS,
    RECOVERY_IMPOSSIBLE, SUPERVISION_TREE_OPERATIONAL,
    CHECKPOINT_SCOPE_ENFORCED, RESILIENCE_CAMPAIGN_REPRODUCIBLE.

The single structural rule of this module: unknown physical quantum state can
never be reconstructed from a classical checkpoint, rolled back, or
duplicated. Every path that would require it returns RECOVERY_IMPOSSIBLE or
raises, and no path anywhere silently substitutes an approximation.

SPECIFICATION HOLES FILLED HERE (recorded in the gap ledger):
  H11. The documents enumerate failure kinds in prose without stable ids. This
       module fixes a canonical 22-entry scenario catalog with stable ids, so
       campaign results are comparable across runs and across versions.
  H12. Reproducibility is defined here as: the pair (scenario_id, seed)
       determines every injected value. The seed is derived by hashing the
       scenario id with the campaign seed, so adding a scenario never
       perturbs the draws of the others.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence,
                    Set, Tuple)

from . import lang
from .lang import RECOVERY_CLASSES

# --------------------------------------------------------------------------
# 0. Tokens and exceptions
# --------------------------------------------------------------------------

TOKEN_RECOVERY_IMPOSSIBLE = "RECOVERY_IMPOSSIBLE"
TOKEN_CHECKPOINT_REFUSED = "QUANTUM_CHECKPOINT_REFUSED"
TOKEN_ROLLBACK_REFUSED = "QUANTUM_ROLLBACK_REFUSED"
TOKEN_CAMPAIGN_COMPLETE = "RESILIENCE_CAMPAIGN_COMPLETE"
TOKEN_CHECKSUM_MISMATCH = "CHECKPOINT_CHECKSUM_MISMATCH"


class ResilienceError(Exception):
    """Base class for every fail-closed resilience condition."""


class UnknownScenarioError(ResilienceError):
    """The scenario id is not in the canonical catalog."""


class QuantumCheckpointRefused(ResilienceError):
    """Attempt to checkpoint arbitrary unknown quantum state."""


class QuantumRollbackRefused(ResilienceError):
    """Attempt to roll back unknown physical quantum state."""


class TransactionStateError(ResilienceError):
    """Illegal recovery-transaction lifecycle transition."""


class SupervisionEscalation(ResilienceError):
    """Escalation past the federation supervisor: nothing can absorb it."""


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=repr, ensure_ascii=False)


def evidence_hash(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()[:32]


def _derive_seed(scenario_id: str, seed: int) -> int:
    """Hole H12: per-scenario seeds are independent of catalog order."""
    h = hashlib.sha256(f"{scenario_id}|{int(seed)}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


# --------------------------------------------------------------------------
# 1. Canonical scenario catalog (hole H11)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    kind: str                 # CLASSICAL | QUANTUM | STORAGE | TOPOLOGY
    subject: str              # what the failure attaches to
    detectable: bool          # can the local detector observe it directly
    quantum_state_at_risk: bool
    description: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


SCENARIOS: Tuple[Scenario, ...] = (
    Scenario("worker_crash", "CLASSICAL", "worker", True, False,
             "a worker process thread dies mid-task"),
    Scenario("process_crash", "CLASSICAL", "process", True, False,
             "an OS process in the local pool exits abnormally"),
    Scenario("domain_loss", "CLASSICAL", "domain", True, False,
             "every worker in an execution domain becomes unreachable"),
    Scenario("node_unavailable", "CLASSICAL", "node", True, False,
             "a declared node stops accepting placements"),
    Scenario("memory_exhaustion", "CLASSICAL", "worker", True, False,
             "an allocation exceeds the declared memory limit"),
    Scenario("classical_link_timeout", "CLASSICAL", "link", True, False,
             "a classical channel exceeds its declared deadline"),
    Scenario("classical_packet_loss", "CLASSICAL", "link", True, False,
             "a fraction of classical messages is dropped in transit"),
    Scenario("dropped_message", "CLASSICAL", "message", True, False,
             "a single message never arrives"),
    Scenario("duplicate_message", "CLASSICAL", "message", True, False,
             "a message is delivered twice"),
    Scenario("corrupt_message", "CLASSICAL", "message", True, False,
             "a message arrives with a payload checksum mismatch"),
    Scenario("quantum_link_failure", "QUANTUM", "quantum_link", True, True,
             "a quantum link drops while carrying an in-flight protocol"),
    Scenario("ebit_generation_failure", "QUANTUM", "ebit", True, False,
             "entanglement generation fails to herald"),
    Scenario("ebit_expiration", "QUANTUM", "ebit", True, False,
             "a reserved ebit expires before it is consumed"),
    Scenario("stale_calibration", "QUANTUM", "device", False, True,
             "the calibration epoch used by the plan is no longer current"),
    Scenario("qpu_unavailable", "QUANTUM", "qpu", True, True,
             "the target QPU stops accepting circuits"),
    Scenario("route_invalidation", "TOPOLOGY", "route", True, True,
             "a compiled route ceases to exist"),
    Scenario("route_degradation", "TOPOLOGY", "route", False, True,
             "a route's fidelity or bandwidth falls below the claim"),
    Scenario("coherence_deadline_exceeded", "QUANTUM", "qstate", True, True,
             "the accumulated coherence exposure passes the declared ceiling"),
    Scenario("checkpoint_corruption", "STORAGE", "checkpoint", True, False,
             "a checkpoint block fails its checksum"),
    Scenario("shard_corruption", "STORAGE", "shard", True, False,
             "a numerical shard fails its checksum"),
    Scenario("stale_topology", "TOPOLOGY", "topology", False, False,
             "the scheduler is planning against a superseded topology"),
    Scenario("stale_reservation", "TOPOLOGY", "reservation", True, False,
             "a resource reservation has already been released elsewhere"),
)

SCENARIO_INDEX: Dict[str, Scenario] = {s.scenario_id: s for s in SCENARIOS}
SCENARIO_IDS: Tuple[str, ...] = tuple(s.scenario_id for s in SCENARIOS)


# --------------------------------------------------------------------------
# 2. Failure injection
# --------------------------------------------------------------------------

@dataclass
class FailureEvent:
    scenario_id: str
    kind: str
    subject: str
    target: str
    seed: int
    derived_seed: int
    detected: bool
    quantum_state_at_risk: bool
    detail: Dict[str, Any] = field(default_factory=dict)
    logical_time: int = 0

    def as_dict(self) -> dict:
        return {"scenario_id": self.scenario_id, "kind": self.kind,
                "subject": self.subject, "target": self.target,
                "seed": self.seed, "derived_seed": self.derived_seed,
                "detected": self.detected,
                "quantum_state_at_risk": self.quantum_state_at_risk,
                "detail": self.detail, "logical_time": self.logical_time}


class FailureInjector:
    """Deterministic failure injection reproducible by (scenario_id, seed).

    Nothing here touches a network or a real device: an injection produces a
    `FailureEvent` describing what a runtime would observe, and the campaign
    drives the recovery machinery from that description.
    """

    def __init__(self, seed: int = 20260811) -> None:
        self.seed = int(seed)
        self.events: List[FailureEvent] = []
        self._t = 0

    def _rng(self, scenario_id: str) -> random.Random:
        return random.Random(_derive_seed(scenario_id, self.seed))

    def inject(self, scenario_id: str, target: str = "-",
               context: Optional[Mapping[str, Any]] = None) -> FailureEvent:
        if scenario_id not in SCENARIO_INDEX:
            raise UnknownScenarioError(
                f"unknown scenario {scenario_id!r}; expected one of "
                f"{list(SCENARIO_IDS)}")
        sc = SCENARIO_INDEX[scenario_id]
        rng = self._rng(scenario_id)
        ctx = dict(context or {})
        self._t += 1
        detail: Dict[str, Any] = {"description": sc.description}

        if scenario_id in ("classical_packet_loss",):
            detail["loss_fraction"] = round(rng.uniform(0.01, 0.4), 6)
            detail["messages_lost"] = rng.randint(1, 12)
        elif scenario_id in ("classical_link_timeout",):
            detail["deadline_s"] = round(rng.uniform(0.05, 1.0), 6)
            detail["observed_s"] = round(rng.uniform(1.1, 4.0), 6)
        elif scenario_id in ("memory_exhaustion",):
            detail["requested_bytes"] = rng.randint(1 << 30, 1 << 34)
            detail["limit_bytes"] = 1 << 30
        elif scenario_id in ("ebit_generation_failure", "ebit_expiration"):
            detail["attempts"] = rng.randint(1, 8)
            detail["epr_state"] = ("FAILED" if scenario_id.endswith("failure")
                                   else "EXPIRED")
        elif scenario_id in ("stale_calibration",):
            detail["plan_epoch"] = rng.randint(1, 50)
            detail["device_epoch"] = detail["plan_epoch"] + rng.randint(1, 5)
        elif scenario_id in ("coherence_deadline_exceeded",):
            detail["budget_s"] = round(rng.uniform(1e-5, 1e-4), 12)
            detail["exposure_s"] = round(detail["budget_s"]
                                         * rng.uniform(1.05, 3.0), 12)
        elif scenario_id in ("checkpoint_corruption", "shard_corruption"):
            detail["block_index"] = rng.randint(0, 15)
            detail["expected_checksum"] = evidence_hash(("expect", rng.random()))
            detail["actual_checksum"] = evidence_hash(("actual", rng.random()))
        elif scenario_id in ("route_degradation",):
            detail["claimed_fidelity"] = round(rng.uniform(0.9, 0.99), 6)
            detail["observed_fidelity"] = round(rng.uniform(0.3, 0.85), 6)
        elif scenario_id in ("duplicate_message",):
            detail["duplicates"] = rng.randint(1, 3)
        elif scenario_id in ("corrupt_message",):
            detail["corrupt_bytes"] = rng.randint(1, 64)
        else:
            detail["nonce"] = rng.randint(0, 1 << 30)

        detected = sc.detectable or bool(ctx.get("detector_extended", False))
        ev = FailureEvent(scenario_id=scenario_id, kind=sc.kind,
                          subject=sc.subject,
                          target=target if target != "-" else sc.subject,
                          seed=self.seed,
                          derived_seed=_derive_seed(scenario_id, self.seed),
                          detected=detected,
                          quantum_state_at_risk=sc.quantum_state_at_risk
                          or bool(ctx.get("quantum_state_unknown", False)),
                          detail=detail, logical_time=self._t)
        self.events.append(ev)
        return ev

    def inject_all(self, targets: Optional[Mapping[str, str]] = None
                   ) -> List[FailureEvent]:
        tg = dict(targets or {})
        return [self.inject(s, tg.get(s, "-")) for s in SCENARIO_IDS]

    def as_dict(self) -> dict:
        return {"seed": self.seed, "scenarios": len(SCENARIOS),
                "events": [e.as_dict() for e in self.events]}


# --------------------------------------------------------------------------
# 3. Recovery classification (LCTL 1.3.x s29)
# --------------------------------------------------------------------------

@dataclass
class ClassificationRecord:
    scenario_id: str
    recovery_class: str
    rule_id: str
    reason: str
    context_keys: List[str]

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class RecoveryPlanner:
    """Map a failure plus its context to exactly one recovery class.

    Rule order matters: the impossibility rules are evaluated first, so no
    later rule can ever promote an impossible recovery into an optimistic one.
    """

    def __init__(self) -> None:
        self.records: List[ClassificationRecord] = []

    @staticmethod
    def _ctx(context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        return dict(context or {})

    def classify(self, failure: FailureEvent,
                 context: Optional[Mapping[str, Any]] = None) -> str:
        c = self._ctx(context)
        sid = failure.scenario_id
        unknown_q = bool(c.get("quantum_state_unknown",
                               failure.quantum_state_at_risk))
        known_classically = bool(c.get("state_classically_known", False))
        from_classical_checkpoint = bool(c.get("restore_from_classical_checkpoint",
                                               False))
        requires_duplication = bool(c.get("requires_duplication", False))
        routes = int(c.get("route_alternatives", 0))
        boundary = c.get("admissible_boundary")
        idempotent = bool(c.get("idempotent", False))
        classical_only = bool(c.get("classical_only", not unknown_q))
        target_specific = bool(c.get("target_specific", False))

        rule, reason, cls = "-", "-", None

        # --- impossibility first (never overridden) ---
        if unknown_q and not known_classically and from_classical_checkpoint:
            rule = "I1"
            reason = ("recovery would require reconstructing unknown quantum "
                      "state from a classical checkpoint, which no classical "
                      "record can contain")
            cls = "RECOVERY_IMPOSSIBLE"
        elif unknown_q and not known_classically and requires_duplication:
            rule = "I2"
            reason = ("recovery would require duplicating unknown quantum "
                      "state, which the no-cloning rule forbids")
            cls = "RECOVERY_IMPOSSIBLE"
        elif (sid == "coherence_deadline_exceeded" and unknown_q
              and not known_classically and boundary is None):
            rule = "I3"
            reason = ("the coherence ceiling was breached while holding unknown "
                      "quantum state and no admissible restart boundary exists")
            cls = "RECOVERY_IMPOSSIBLE"
        elif sid == "quantum_link_failure" and unknown_q and routes == 0 \
                and boundary is None and not known_classically:
            rule = "I4"
            reason = ("the in-flight quantum protocol lost its only link with "
                      "no alternative route and no admissible restart boundary")
            cls = "RECOVERY_IMPOSSIBLE"

        # --- ordinary classes ---
        if cls is None:
            if sid in ("ebit_generation_failure", "ebit_expiration"):
                rule, cls = "R1", "REGENERATE_ENTANGLEMENT"
                reason = ("entanglement is a regenerable resource; no unknown "
                          "state is lost by regenerating it")
            elif known_classically and sid in (
                    "worker_crash", "process_crash", "qpu_unavailable",
                    "stale_calibration", "coherence_deadline_exceeded",
                    "quantum_link_failure"):
                rule, cls = "R2", "REPREPARE_KNOWN_STATE"
                reason = ("the state preparation is classically known, so it can "
                          "be re-prepared exactly")
            elif sid in ("route_invalidation", "route_degradation",
                         "quantum_link_failure", "node_unavailable",
                         "domain_loss") and routes > 0:
                rule, cls = "R3", "REROUTE_SAFE"
                reason = (f"{routes} alternative route(s) exist and no unknown "
                          f"state has to move to use them")
            elif target_specific:
                rule, cls = "R4", "RECOVERY_TARGET_SPECIFIC"
                reason = ("recovery depends on a device-specific capability and "
                          "cannot be stated portably")
            elif classical_only and boundary is not None:
                rule, cls = "R5", "RESTART_FROM_CLASSICAL_BOUNDARY"
                reason = (f"execution can resume from the classical boundary "
                          f"{boundary!r} without reconstructing quantum state")
            elif classical_only and sid in ("checkpoint_corruption",
                                            "shard_corruption",
                                            "memory_exhaustion",
                                            "worker_crash", "process_crash"):
                rule, cls = "R6", "ROLLBACK_CLASSICAL_ONLY"
                reason = ("the damaged state is classical, so a classical "
                          "rollback restores it exactly")
            elif idempotent or sid in ("dropped_message", "duplicate_message",
                                       "corrupt_message",
                                       "classical_packet_loss",
                                       "classical_link_timeout",
                                       "stale_reservation", "stale_topology"):
                rule, cls = "R7", "RETRY_SAFE"
                reason = ("the operation is classical and idempotent, so a "
                          "retry cannot change the observable result")
            elif unknown_q and boundary is not None:
                rule, cls = "R8", "RESTART_FROM_CLASSICAL_BOUNDARY"
                reason = (f"unknown quantum state is discarded and execution "
                          f"restarts from the admissible boundary {boundary!r}")
            elif unknown_q:
                rule = "I5"
                reason = ("unknown quantum state is held, no route, boundary, "
                          "or classical description exists")
                cls = "RECOVERY_IMPOSSIBLE"
            else:
                rule, cls = "R9", "RESTART_FROM_CLASSICAL_BOUNDARY"
                reason = ("default classical remedy: restart from the last "
                          "classical boundary")

        assert cls in RECOVERY_CLASSES, cls
        self.records.append(ClassificationRecord(
            scenario_id=sid, recovery_class=cls, rule_id=rule, reason=reason,
            context_keys=sorted(c)))
        return cls

    def explain(self, failure: FailureEvent,
                context: Optional[Mapping[str, Any]] = None
                ) -> ClassificationRecord:
        self.classify(failure, context)
        return self.records[-1]

    def as_dict(self) -> dict:
        return {"classifications": [r.as_dict() for r in self.records]}


# --------------------------------------------------------------------------
# 4. Supervision tree (LCTL 1.4.x s44)
# --------------------------------------------------------------------------

SUPERVISION_LEVELS = ("TASK", "WORKER", "DOMAIN", "FEDERATION")
SUPERVISION_ACTIONS = ("retry", "restart", "reroute", "escalate", "abort")


@dataclass
class SupervisionDecision:
    level: str
    subject: str
    action: str
    recovery_class: str
    attempt: int
    reason: str
    escalated_from: Optional[str] = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class Supervisor:
    """One node of the supervision hierarchy."""

    def __init__(self, level: str, max_attempts: int = 2,
                 parent: Optional["Supervisor"] = None) -> None:
        if level not in SUPERVISION_LEVELS:
            raise ResilienceError(f"unknown supervision level {level!r}")
        self.level = level
        self.max_attempts = int(max_attempts)
        self.parent = parent
        self.attempts: Dict[str, int] = {}

    def decide(self, subject: str, recovery_class: str,
               failure: FailureEvent) -> SupervisionDecision:
        n = self.attempts.get(subject, 0) + 1
        self.attempts[subject] = n
        if recovery_class == "RECOVERY_IMPOSSIBLE":
            return SupervisionDecision(
                self.level, subject, "abort", recovery_class, n,
                "recovery is impossible; aborting is the only honest action")
        if n > self.max_attempts:
            return SupervisionDecision(
                self.level, subject, "escalate", recovery_class, n,
                f"attempt {n} exceeds the level budget {self.max_attempts}")
        if recovery_class in ("REROUTE_SAFE",):
            action = "reroute"
        elif recovery_class in ("RETRY_SAFE", "REGENERATE_ENTANGLEMENT"):
            action = "retry"
        elif recovery_class in ("RESTART_FROM_CLASSICAL_BOUNDARY",
                                "REPREPARE_KNOWN_STATE",
                                "ROLLBACK_CLASSICAL_ONLY"):
            action = "restart"
        else:                                # RECOVERY_TARGET_SPECIFIC
            action = "escalate"
        return SupervisionDecision(
            self.level, subject, action, recovery_class, n,
            f"{self.level} supervisor maps {recovery_class} to {action}")


class SupervisionTree:
    """TASK -> WORKER -> DOMAIN -> FEDERATION supervision.

    Escalation walks strictly upward. Escalating past FEDERATION is a
    fail-closed condition, not a silent drop.
    """

    def __init__(self, budgets: Optional[Mapping[str, int]] = None) -> None:
        b = {"TASK": 2, "WORKER": 2, "DOMAIN": 1, "FEDERATION": 1}
        b.update({k: int(v) for k, v in (budgets or {}).items()})
        self.nodes: Dict[str, Supervisor] = {}
        parent: Optional[Supervisor] = None
        for lvl in reversed(SUPERVISION_LEVELS):
            parent = Supervisor(lvl, b[lvl], parent)
            self.nodes[lvl] = parent
        # link downward: TASK's parent is WORKER, etc.
        for i, lvl in enumerate(SUPERVISION_LEVELS[:-1]):
            self.nodes[lvl].parent = self.nodes[SUPERVISION_LEVELS[i + 1]]
        self.decisions: List[SupervisionDecision] = []

    def handle(self, subject: str, recovery_class: str, failure: FailureEvent,
               start_level: str = "TASK") -> SupervisionDecision:
        if start_level not in SUPERVISION_LEVELS:
            raise ResilienceError(f"unknown start level {start_level!r}")
        level = start_level
        prev: Optional[str] = None
        while True:
            sup = self.nodes[level]
            d = sup.decide(subject, recovery_class, failure)
            d.escalated_from = prev
            self.decisions.append(d)
            if d.action != "escalate":
                return d
            if sup.parent is None:
                raise SupervisionEscalation(
                    f"escalation past the FEDERATION supervisor for "
                    f"{subject!r} under {recovery_class}; no supervisor can "
                    f"absorb this failure")
            prev, level = level, sup.parent.level

    def as_dict(self) -> dict:
        return {"levels": list(SUPERVISION_LEVELS),
                "budgets": {k: self.nodes[k].max_attempts
                            for k in SUPERVISION_LEVELS},
                "decisions": [d.as_dict() for d in self.decisions]}


# --------------------------------------------------------------------------
# 5. Checkpoint store (LCTL 1.5.x s49)
# --------------------------------------------------------------------------

CHECKPOINT_KINDS = ("classical_compiler", "source", "ir", "scheduler",
                    "classical_measurement", "classically_known_state_prep",
                    "simulator_state")


@dataclass
class CheckpointBlock:
    index: int
    checksum: str
    nbytes: int

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Checkpoint:
    checkpoint_id: str
    kind: str
    scope: str
    payload: Any
    blocks: List[CheckpointBlock]
    logical_time: int
    provenance: Dict[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        return evidence_hash([b.as_dict() for b in self.blocks])

    def as_dict(self) -> dict:
        return {"checkpoint_id": self.checkpoint_id, "kind": self.kind,
                "scope": self.scope, "logical_time": self.logical_time,
                "blocks": [b.as_dict() for b in self.blocks],
                "digest": self.digest(), "provenance": self.provenance}


def _is_quantum_payload(value: Any) -> bool:
    if getattr(value, "is_qstate", False):
        return True
    if type(value).__name__ == "QuantumPayload":
        return True
    t = getattr(value, "type_", None) or getattr(value, "type", None)
    if isinstance(t, str) and t in lang.QUANTUM_OWNED_TYPES:
        return True
    if isinstance(value, str) and value.strip().upper() == "QSTATE":
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_is_quantum_payload(v) for v in value)
    if isinstance(value, dict):
        return any(_is_quantum_payload(v) for v in value.values())
    return False


class CheckpointStore:
    """Checkpoints of classically representable state only.

    Every checkpoint is split into blocks and each block carries its own
    checksum, so corruption is localized rather than invalidating the whole
    record. Arbitrary unknown quantum state is refused at `save()`.
    """

    BLOCK_BYTES = 256

    def __init__(self) -> None:
        self.store: Dict[str, Checkpoint] = {}
        self._t = 0

    def _blocks(self, payload: Any) -> List[CheckpointBlock]:
        raw = _canon(payload).encode("utf-8")
        out: List[CheckpointBlock] = []
        for i in range(0, max(len(raw), 1), self.BLOCK_BYTES):
            chunk = raw[i:i + self.BLOCK_BYTES]
            out.append(CheckpointBlock(
                index=len(out),
                checksum=hashlib.sha256(chunk).hexdigest()[:32],
                nbytes=len(chunk)))
        return out

    def save(self, checkpoint_id: str, kind: str, payload: Any, *,
             scope: str = "worker",
             classically_known: bool = False,
             provenance: Optional[Mapping[str, Any]] = None) -> Checkpoint:
        if kind not in CHECKPOINT_KINDS:
            raise ResilienceError(
                f"unknown checkpoint kind {kind!r}; expected one of "
                f"{list(CHECKPOINT_KINDS)}")
        if _is_quantum_payload(payload) and not (
                kind == "classically_known_state_prep" and classically_known):
            raise QuantumCheckpointRefused(
                f"{TOKEN_CHECKPOINT_REFUSED}: checkpoint {checkpoint_id!r} of "
                f"kind {kind!r} carries unknown quantum state; a classical "
                f"record can never represent it (LCTL 1.5.x s49)")
        if kind == "classically_known_state_prep" and not classically_known:
            raise QuantumCheckpointRefused(
                f"{TOKEN_CHECKPOINT_REFUSED}: kind "
                f"'classically_known_state_prep' requires an explicit "
                f"classically_known=True assertion")
        self._t += 1
        cp = Checkpoint(checkpoint_id=checkpoint_id, kind=kind, scope=scope,
                        payload=payload, blocks=self._blocks(payload),
                        logical_time=self._t,
                        provenance=dict(provenance or {}))
        self.store[checkpoint_id] = cp
        return cp

    def verify(self, checkpoint_id: str) -> dict:
        cp = self.load_record(checkpoint_id)
        expected = self._blocks(cp.payload)
        bad = [e.index for e, a in zip(expected, cp.blocks)
               if e.checksum != a.checksum]
        if len(expected) != len(cp.blocks):
            bad.append(-1)
        return {"checkpoint_id": checkpoint_id, "ok": not bad,
                "corrupt_blocks": bad,
                "token": None if not bad else TOKEN_CHECKSUM_MISMATCH,
                "blocks": len(cp.blocks)}

    def corrupt(self, checkpoint_id: str, block_index: int) -> Checkpoint:
        """Deterministically corrupt one block (used by the campaign)."""
        cp = self.load_record(checkpoint_id)
        if not 0 <= block_index < len(cp.blocks):
            raise ResilienceError(f"block {block_index} outside checkpoint "
                                  f"{checkpoint_id!r}")
        b = cp.blocks[block_index]
        cp.blocks[block_index] = CheckpointBlock(
            b.index, hashlib.sha256(b"corrupt" + b.checksum.encode()).hexdigest()[:32],
            b.nbytes)
        return cp

    def load_record(self, checkpoint_id: str) -> Checkpoint:
        if checkpoint_id not in self.store:
            raise ResilienceError(f"unknown checkpoint {checkpoint_id!r}")
        return self.store[checkpoint_id]

    def load(self, checkpoint_id: str) -> Any:
        v = self.verify(checkpoint_id)
        if not v["ok"]:
            raise ResilienceError(
                f"{TOKEN_CHECKSUM_MISMATCH}: checkpoint {checkpoint_id!r} "
                f"blocks {v['corrupt_blocks']} failed verification")
        return self.load_record(checkpoint_id).payload

    def as_dict(self) -> dict:
        return {"kinds": list(CHECKPOINT_KINDS),
                "checkpoints": [self.store[k].as_dict()
                                for k in sorted(self.store)]}


# --------------------------------------------------------------------------
# 6. Recovery transactions (LCTL 1.4.x s45)
# --------------------------------------------------------------------------

@dataclass
class TransactionRecord:
    transaction_id: str
    state: str                      # OPEN | COMMITTED | ROLLED_BACK | REFUSED
    classical_keys: List[str]
    quantum_keys: List[str]
    reason: str = "-"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class RecoveryTransaction:
    """Begin/commit/rollback over classical state only.

    Classical entries are journalled with their prior value and can be
    restored exactly. A quantum entry can be *registered* (so the transaction
    knows what it touched) but never rolled back: `rollback()` fails closed if
    any registered quantum entry is unknown state.
    """

    def __init__(self, transaction_id: str) -> None:
        self.transaction_id = transaction_id
        self.state = "OPEN"
        self._journal: Dict[str, Any] = {}
        self._quantum: Dict[str, Any] = {}
        self._live: Dict[str, Any] = {}
        self.reason = "-"

    def begin(self, snapshot: Optional[Mapping[str, Any]] = None
              ) -> "RecoveryTransaction":
        if self.state != "OPEN":
            raise TransactionStateError(
                f"transaction {self.transaction_id!r} is {self.state}")
        for k, v in dict(snapshot or {}).items():
            self.record_classical(k, v)
        return self

    def record_classical(self, key: str, prior_value: Any) -> None:
        if self.state != "OPEN":
            raise TransactionStateError(
                f"transaction {self.transaction_id!r} is {self.state}")
        if _is_quantum_payload(prior_value):
            raise QuantumRollbackRefused(
                f"{TOKEN_ROLLBACK_REFUSED}: key {key!r} carries quantum state; "
                f"use register_quantum, which is journal-only")
        self._journal.setdefault(key, prior_value)
        self._live[key] = prior_value

    def write(self, key: str, value: Any) -> None:
        if self.state != "OPEN":
            raise TransactionStateError(
                f"transaction {self.transaction_id!r} is {self.state}")
        if _is_quantum_payload(value):
            raise QuantumRollbackRefused(
                f"{TOKEN_ROLLBACK_REFUSED}: refusing to journal quantum state "
                f"under key {key!r}")
        self._journal.setdefault(key, self._live.get(key))
        self._live[key] = value

    def register_quantum(self, key: str, payload: Any,
                         *, classically_known: bool = False) -> None:
        """Record that the transaction touched quantum state."""
        self._quantum[key] = {"classically_known": bool(classically_known),
                              "repr": type(payload).__name__}

    def commit(self) -> TransactionRecord:
        if self.state != "OPEN":
            raise TransactionStateError(
                f"transaction {self.transaction_id!r} is {self.state}")
        self.state = "COMMITTED"
        return self.record()

    def rollback(self) -> TransactionRecord:
        if self.state != "OPEN":
            raise TransactionStateError(
                f"transaction {self.transaction_id!r} is {self.state}")
        unknown = sorted(k for k, v in self._quantum.items()
                         if not v["classically_known"])
        if unknown:
            self.state = "REFUSED"
            self.reason = (f"unknown physical quantum state {unknown} can never "
                           f"be rolled back; only a classical-only rollback is "
                           f"permitted")
            raise QuantumRollbackRefused(
                f"{TOKEN_ROLLBACK_REFUSED}: transaction "
                f"{self.transaction_id!r}: {self.reason}")
        for k, v in self._journal.items():
            self._live[k] = v
        self.state = "ROLLED_BACK"
        self.reason = "classical-only rollback completed from the journal"
        return self.record()

    def live_state(self) -> Dict[str, Any]:
        return dict(sorted(self._live.items()))

    def record(self) -> TransactionRecord:
        return TransactionRecord(
            transaction_id=self.transaction_id, state=self.state,
            classical_keys=sorted(self._journal),
            quantum_keys=sorted(self._quantum), reason=self.reason)

    def as_dict(self) -> dict:
        d = self.record().as_dict()
        d["live_state_hash"] = evidence_hash(self.live_state())
        d["quantum_registrations"] = {k: self._quantum[k]
                                      for k in sorted(self._quantum)}
        return d


# --------------------------------------------------------------------------
# 7. Campaign (LCTL 1.6.x s81)
# --------------------------------------------------------------------------

DEFAULT_CONTEXTS: Dict[str, Dict[str, Any]] = {
    "worker_crash": {"classical_only": True, "admissible_boundary": "B_CLASSICAL"},
    "process_crash": {"classical_only": True, "admissible_boundary": "B_CLASSICAL"},
    "domain_loss": {"classical_only": True, "route_alternatives": 1},
    "node_unavailable": {"classical_only": True, "route_alternatives": 1},
    "memory_exhaustion": {"classical_only": True},
    "classical_link_timeout": {"classical_only": True, "idempotent": True},
    "classical_packet_loss": {"classical_only": True, "idempotent": True},
    "dropped_message": {"classical_only": True, "idempotent": True},
    "duplicate_message": {"classical_only": True, "idempotent": True},
    "corrupt_message": {"classical_only": True, "idempotent": True},
    "quantum_link_failure": {"quantum_state_unknown": True,
                             "route_alternatives": 1},
    "ebit_generation_failure": {},
    "ebit_expiration": {},
    "stale_calibration": {"state_classically_known": True},
    "qpu_unavailable": {"quantum_state_unknown": True,
                        "admissible_boundary": "B_PREP"},
    "route_invalidation": {"route_alternatives": 2},
    "route_degradation": {"route_alternatives": 1},
    "coherence_deadline_exceeded": {"quantum_state_unknown": True,
                                    "admissible_boundary": "B_PREP"},
    "checkpoint_corruption": {"classical_only": True},
    "shard_corruption": {"classical_only": True},
    "stale_topology": {"classical_only": True, "idempotent": True},
    "stale_reservation": {"classical_only": True, "idempotent": True},
}


@dataclass
class CampaignEntry:
    scenario_id: str
    injected: bool
    detected: bool
    classification: str
    action: str
    recovered: bool
    evidence_hash: str
    rule_id: str
    reason: str

    def as_dict(self) -> dict:
        return {"scenario_id": self.scenario_id, "injected": self.injected,
                "detected": self.detected, "classification": self.classification,
                "action": self.action, "recovered": self.recovered,
                "evidence_hash": self.evidence_hash, "rule_id": self.rule_id,
                "reason": self.reason}


def run_campaign(scenarios: Optional[Sequence[str]] = None,
                 seed: int = 20260811,
                 contexts: Optional[Mapping[str, Mapping[str, Any]]] = None
                 ) -> List[dict]:
    """Inject, detect, classify, act and record evidence for each scenario.

    Reproducible by (scenario_id, seed): the same arguments always produce the
    same entries, including the evidence hashes.
    """
    ids = list(scenarios) if scenarios is not None else list(SCENARIO_IDS)
    for s in ids:
        if s not in SCENARIO_INDEX:
            raise UnknownScenarioError(f"unknown scenario {s!r}")
    ctxs = {k: dict(v) for k, v in DEFAULT_CONTEXTS.items()}
    for k, v in dict(contexts or {}).items():
        ctxs.setdefault(k, {}).update(dict(v))

    injector = FailureInjector(seed)
    planner = RecoveryPlanner()
    tree = SupervisionTree()
    out: List[dict] = []

    for sid in ids:
        ev = injector.inject(sid, target=SCENARIO_INDEX[sid].subject,
                             context=ctxs.get(sid))
        rec = planner.explain(ev, ctxs.get(sid))
        cls = rec.recovery_class
        try:
            decision = tree.handle(f"{sid}:{ev.target}", cls, ev)
            action = decision.action
        except SupervisionEscalation as exc:
            action = "abort"
            rec.reason += f" | escalation exhausted: {exc}"
        recovered = ev.detected and action in ("retry", "restart", "reroute")
        entry = CampaignEntry(
            scenario_id=sid, injected=True, detected=ev.detected,
            classification=cls, action=action, recovered=recovered,
            evidence_hash=evidence_hash({"event": ev.as_dict(),
                                         "class": cls, "action": action}),
            rule_id=rec.rule_id, reason=rec.reason)
        out.append(entry.as_dict())
    return out


def campaign_summary(entries: Sequence[Mapping[str, Any]]) -> dict:
    by_class: Dict[str, int] = {}
    for e in entries:
        by_class[e["classification"]] = by_class.get(e["classification"], 0) + 1
    return {"token": TOKEN_CAMPAIGN_COMPLETE,
            "scenarios": len(entries),
            "detected": sum(1 for e in entries if e["detected"]),
            "recovered": sum(1 for e in entries if e["recovered"]),
            "impossible": by_class.get("RECOVERY_IMPOSSIBLE", 0),
            "by_class": dict(sorted(by_class.items())),
            "campaign_hash": evidence_hash(list(entries))}


__all__ = [
    "ResilienceError", "UnknownScenarioError", "QuantumCheckpointRefused",
    "QuantumRollbackRefused", "TransactionStateError", "SupervisionEscalation",
    "Scenario", "SCENARIOS", "SCENARIO_IDS", "SCENARIO_INDEX",
    "FailureInjector", "FailureEvent", "RecoveryPlanner",
    "ClassificationRecord", "SupervisionTree", "Supervisor",
    "SupervisionDecision", "SUPERVISION_LEVELS", "SUPERVISION_ACTIONS",
    "CheckpointStore", "Checkpoint", "CheckpointBlock", "CHECKPOINT_KINDS",
    "RecoveryTransaction", "TransactionRecord", "run_campaign",
    "campaign_summary", "evidence_hash", "TOKEN_RECOVERY_IMPOSSIBLE",
]
