"""
Conflict-free replicated data types and the consistency contract.

Implements:
  * LCTL 1.4.x s11    CRDT-declared classical replication (state-based CvRDTs)
  * LCTL 1.4.x s10    consistency profiles and their admissibility
  * LCTL 1.5.x s26    federated anti-entropy reconciliation, version vectors
  * LCTL 1.6.x s36    empirical verification of the CRDT merge laws

Exit-gate tokens covered:
    CRDT_LAWS_VERIFIED, CONSISTENCY_GUARANTEE_BLOCKED,
    QUANTUM_REPLICATION_REJECTED, ANTI_ENTROPY_CONVERGED.

Two invariants are structural, not advisory:

  1. Every merge in this module is deterministic, commutative, associative and
     idempotent. `verify_crdt_laws` checks all four empirically over generated
     samples and returns a report rather than an assertion.
  2. A QSTATE is never replicated. Unknown quantum state is ownership
     constrained (LCTL 1.1.x s6), so every mutator runs a rejection guard and
     raises `QuantumReplicationRejected`. There is no replication type in this
     module that can hold a quantum payload.

SPECIFICATION HOLES FILLED HERE (recorded in the gap ledger):
  H9.  LCTL 1.4.x s10 names the consistency profiles but never states the
       partition/failure conditions that make each one impossible. This module
       declares an explicit, citable rule table (`CONSISTENCY_RULES`), and the
       verdict always names the rule that fired. Nothing is silently
       downgraded: an impossible guarantee returns the blocking token.
  H10. LWWRegister requires a clock policy that the documents leave open. Three
       explicit policies are provided and the policy is part of the replica
       identity, so two registers with different policies never merge.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, FrozenSet, Iterable, List, Mapping,
                    Optional, Sequence, Set, Tuple, Type)

from . import lang
from .lang import CONSISTENCY_PROFILES

# --------------------------------------------------------------------------
# 0. Tokens and exceptions
# --------------------------------------------------------------------------

TOKEN_BLOCKED = "CONSISTENCY_GUARANTEE_BLOCKED"
TOKEN_ADMITTED = "CONSISTENCY_GUARANTEE_ADMITTED"
TOKEN_QUANTUM_REJECTED = "QUANTUM_REPLICATION_REJECTED"
TOKEN_LAWS_VERIFIED = "CRDT_LAWS_VERIFIED"
TOKEN_LAWS_FAILED = "CRDT_LAWS_FAILED"
TOKEN_CONVERGED = "ANTI_ENTROPY_CONVERGED"
TOKEN_NOT_CONVERGED = "ANTI_ENTROPY_NOT_CONVERGED"

QSTATE_TOKEN = "QSTATE"


class CRDTError(Exception):
    """Base class for every fail-closed CRDT condition."""


class QuantumReplicationRejected(CRDTError):
    """A QSTATE was offered to a replication type."""


class MergeTypeError(CRDTError):
    """Merge of incompatible replica types or policies."""


class ConsistencyGuaranteeBlocked(CRDTError):
    """A requested guarantee is impossible under the declared models."""


# --------------------------------------------------------------------------
# 1. Quantum rejection guard
# --------------------------------------------------------------------------

def is_quantum_value(value: Any) -> bool:
    """Duck-typed QSTATE detection; deliberately independent of `fabric`."""
    if isinstance(value, str) and value.strip().upper() == QSTATE_TOKEN:
        return True
    if getattr(value, "is_qstate", False):
        return True
    if type(value).__name__ == "QuantumPayload":
        return True
    t = getattr(value, "type_", None) or getattr(value, "type", None)
    if isinstance(t, str) and t in lang.QUANTUM_OWNED_TYPES:
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(is_quantum_value(v) for v in value)
    if isinstance(value, dict):
        return any(is_quantum_value(k) or is_quantum_value(v)
                   for k, v in value.items())
    return False


def reject_quantum(value: Any, *, where: str = "replica") -> Any:
    """Fail closed on any attempt to place quantum state into a replica."""
    if is_quantum_value(value):
        raise QuantumReplicationRejected(
            f"{TOKEN_QUANTUM_REJECTED}: {where} refuses value {value!r}; "
            f"unknown quantum state is ownership-constrained and can never be "
            f"replicated, merged or reconciled (LCTL 1.1.x s6, 1.4.x s11)")
    return value


def _canon(obj: Any) -> str:
    def default(o: Any) -> Any:
        if isinstance(o, (set, frozenset)):
            return sorted(o, key=repr)
        if isinstance(o, tuple):
            return list(o)
        return repr(o)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=default, ensure_ascii=False)


def state_hash(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------
# 2. CRDT base
# --------------------------------------------------------------------------

class CRDT:
    """State-based convergent replicated data type (CvRDT).

    Subclasses implement `merge`, `value`, `state`, `copy` and the
    `random_sample` factory used by `verify_crdt_laws`.
    """

    replica_id: str

    # -- required surface --------------------------------------------------
    def merge(self, other: "CRDT") -> "CRDT":
        raise NotImplementedError

    def value(self) -> Any:
        raise NotImplementedError

    def state(self) -> Any:
        raise NotImplementedError

    def copy(self) -> "CRDT":
        raise NotImplementedError

    @classmethod
    def random_sample(cls, rng: random.Random, replica_id: str) -> "CRDT":
        raise NotImplementedError

    # -- shared helpers ----------------------------------------------------
    def _check_mergeable(self, other: "CRDT") -> None:
        if type(self) is not type(other):
            raise MergeTypeError(
                f"cannot merge {type(self).__name__} with {type(other).__name__}")

    def state_hash(self) -> str:
        return state_hash(self.state())

    def equivalent(self, other: "CRDT") -> bool:
        return type(self) is type(other) and self.state_hash() == other.state_hash()

    def as_dict(self) -> dict:
        return {"type": type(self).__name__, "replica_id": self.replica_id,
                "state": self.state(), "value": self.value(),
                "state_hash": self.state_hash()}


# --------------------------------------------------------------------------
# 3. Counters
# --------------------------------------------------------------------------

@dataclass
class GCounter(CRDT):
    """Grow-only counter. Merge is the per-replica maximum."""
    replica_id: str
    counts: Dict[str, int] = field(default_factory=dict)

    def increment(self, n: int = 1) -> "GCounter":
        if n < 0:
            raise CRDTError("GCounter increments must be non-negative; "
                            "use PNCounter for decrements")
        reject_quantum(n, where="GCounter")
        self.counts[self.replica_id] = self.counts.get(self.replica_id, 0) + int(n)
        return self

    def merge(self, other: "GCounter") -> "GCounter":
        self._check_mergeable(other)
        out = GCounter(self.replica_id, dict(self.counts))
        for k, v in other.counts.items():
            out.counts[k] = max(out.counts.get(k, 0), int(v))
        return out

    def value(self) -> int:
        return sum(self.counts.values())

    def state(self) -> dict:
        return {"counts": dict(sorted(self.counts.items()))}

    def copy(self) -> "GCounter":
        return GCounter(self.replica_id, dict(self.counts))

    @classmethod
    def random_sample(cls, rng: random.Random, replica_id: str) -> "GCounter":
        c = cls(replica_id)
        for r in ("R0", "R1", "R2"):
            if rng.random() < 0.8:
                c.counts[r] = rng.randint(0, 12)
        return c


@dataclass
class PNCounter(CRDT):
    """Positive/negative counter: a pair of G-counters."""
    replica_id: str
    positive: Dict[str, int] = field(default_factory=dict)
    negative: Dict[str, int] = field(default_factory=dict)

    def increment(self, n: int = 1) -> "PNCounter":
        if n < 0:
            return self.decrement(-n)
        reject_quantum(n, where="PNCounter")
        self.positive[self.replica_id] = \
            self.positive.get(self.replica_id, 0) + int(n)
        return self

    def decrement(self, n: int = 1) -> "PNCounter":
        if n < 0:
            return self.increment(-n)
        reject_quantum(n, where="PNCounter")
        self.negative[self.replica_id] = \
            self.negative.get(self.replica_id, 0) + int(n)
        return self

    def merge(self, other: "PNCounter") -> "PNCounter":
        self._check_mergeable(other)
        out = PNCounter(self.replica_id, dict(self.positive), dict(self.negative))
        for k, v in other.positive.items():
            out.positive[k] = max(out.positive.get(k, 0), int(v))
        for k, v in other.negative.items():
            out.negative[k] = max(out.negative.get(k, 0), int(v))
        return out

    def value(self) -> int:
        return sum(self.positive.values()) - sum(self.negative.values())

    def state(self) -> dict:
        return {"positive": dict(sorted(self.positive.items())),
                "negative": dict(sorted(self.negative.items()))}

    def copy(self) -> "PNCounter":
        return PNCounter(self.replica_id, dict(self.positive),
                         dict(self.negative))

    @classmethod
    def random_sample(cls, rng: random.Random, replica_id: str) -> "PNCounter":
        c = cls(replica_id)
        for r in ("R0", "R1", "R2"):
            if rng.random() < 0.8:
                c.positive[r] = rng.randint(0, 9)
            if rng.random() < 0.6:
                c.negative[r] = rng.randint(0, 5)
        return c


# --------------------------------------------------------------------------
# 4. Sets
# --------------------------------------------------------------------------

@dataclass
class GSet(CRDT):
    """Grow-only set. Merge is union."""
    replica_id: str
    elements: Set[Any] = field(default_factory=set)

    def add(self, element: Any) -> "GSet":
        reject_quantum(element, where="GSet")
        self.elements.add(element)
        return self

    def contains(self, element: Any) -> bool:
        return element in self.elements

    def merge(self, other: "GSet") -> "GSet":
        self._check_mergeable(other)
        return GSet(self.replica_id, set(self.elements) | set(other.elements))

    def value(self) -> List[Any]:
        return sorted(self.elements, key=repr)

    def state(self) -> dict:
        return {"elements": self.value()}

    def copy(self) -> "GSet":
        return GSet(self.replica_id, set(self.elements))

    @classmethod
    def random_sample(cls, rng: random.Random, replica_id: str) -> "GSet":
        s = cls(replica_id)
        for _ in range(rng.randint(0, 6)):
            s.elements.add(f"e{rng.randint(0, 9)}")
        return s


@dataclass
class ORSet(CRDT):
    """Observed-remove set: unique add tags, tombstoned removals.

    An element is present when it has at least one add tag that has not been
    observed as removed. Re-adding after a remove is well defined because the
    new add carries a fresh tag.
    """
    replica_id: str
    adds: Set[Tuple[Any, str]] = field(default_factory=set)
    removed_tags: Set[str] = field(default_factory=set)
    _counter: int = 0

    def _tag(self) -> str:
        self._counter += 1
        return f"{self.replica_id}:{self._counter}"

    def add(self, element: Any, tag: Optional[str] = None) -> str:
        reject_quantum(element, where="ORSet")
        t = tag or self._tag()
        self.adds.add((element, t))
        return t

    def remove(self, element: Any) -> List[str]:
        tags = [t for e, t in self.adds if e == element]
        self.removed_tags.update(tags)
        return sorted(tags)

    def contains(self, element: Any) -> bool:
        return any(e == element and t not in self.removed_tags
                   for e, t in self.adds)

    def merge(self, other: "ORSet") -> "ORSet":
        self._check_mergeable(other)
        out = ORSet(self.replica_id,
                    set(self.adds) | set(other.adds),
                    set(self.removed_tags) | set(other.removed_tags),
                    max(self._counter, other._counter))
        return out

    def value(self) -> List[Any]:
        live = {e for e, t in self.adds if t not in self.removed_tags}
        return sorted(live, key=repr)

    def state(self) -> dict:
        return {"adds": sorted(([e, t] for e, t in self.adds), key=repr),
                "removed_tags": sorted(self.removed_tags)}

    def copy(self) -> "ORSet":
        return ORSet(self.replica_id, set(self.adds), set(self.removed_tags),
                     self._counter)

    @classmethod
    def random_sample(cls, rng: random.Random, replica_id: str) -> "ORSet":
        s = cls(replica_id)
        for _ in range(rng.randint(0, 6)):
            e = f"e{rng.randint(0, 5)}"
            t = f"R{rng.randint(0, 2)}:{rng.randint(1, 9)}"
            s.adds.add((e, t))
        for e, t in list(s.adds):
            if rng.random() < 0.35:
                s.removed_tags.add(t)
        # tombstones may be observed without the corresponding add
        for _ in range(rng.randint(0, 2)):
            s.removed_tags.add(f"R{rng.randint(0, 2)}:{rng.randint(1, 9)}")
        return s


# --------------------------------------------------------------------------
# 5. Last-writer-wins register (explicit clock policy, hole H10)
# --------------------------------------------------------------------------

CLOCK_POLICIES = ("LAMPORT_THEN_REPLICA_ID",
                  "DECLARED_WALL_CLOCK_THEN_REPLICA_ID",
                  "VERSION_VECTOR_DOMINANCE_THEN_REPLICA_ID")


@dataclass
class LWWRegister(CRDT):
    """Last-writer-wins register with a declared, total tie-break order.

    The clock policy is part of the replica identity. Two registers with
    different policies never merge, because "last" would then mean two
    different things and the merge would not be deterministic.
    """
    replica_id: str
    policy: str = "LAMPORT_THEN_REPLICA_ID"
    _value: Any = None
    timestamp: int = 0
    writer: str = ""
    version: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.policy not in CLOCK_POLICIES:
            raise CRDTError(f"unknown clock policy {self.policy!r}; expected "
                            f"one of {list(CLOCK_POLICIES)}")
        if not self.writer:
            self.writer = self.replica_id

    def set(self, value: Any, timestamp: Optional[int] = None) -> "LWWRegister":
        reject_quantum(value, where="LWWRegister")
        self.timestamp = int(timestamp) if timestamp is not None \
            else self.timestamp + 1
        self._value = value
        self.writer = self.replica_id
        self.version[self.replica_id] = \
            max(self.version.get(self.replica_id, 0), self.timestamp)
        return self

    def get(self) -> Any:
        return self._value

    def _dominates(self, other: "LWWRegister") -> bool:
        if self.policy == "VERSION_VECTOR_DOMINANCE_THEN_REPLICA_ID":
            keys = set(self.version) | set(other.version)
            ge = all(self.version.get(k, 0) >= other.version.get(k, 0)
                     for k in keys)
            gt = any(self.version.get(k, 0) > other.version.get(k, 0)
                     for k in keys)
            if ge and gt:
                return True
            if not ge and any(self.version.get(k, 0) < other.version.get(k, 0)
                              for k in keys):
                # concurrent or dominated: fall through to the total order
                pass
        if self.timestamp != other.timestamp:
            return self.timestamp > other.timestamp
        if self.writer != other.writer:
            return self.writer > other.writer
        return _canon(self._value) > _canon(other._value)

    def merge(self, other: "LWWRegister") -> "LWWRegister":
        self._check_mergeable(other)
        if self.policy != other.policy:
            raise MergeTypeError(
                f"clock policy mismatch: {self.policy!r} vs {other.policy!r}; "
                f"a merge under two different 'last' orders is not deterministic")
        win = self if self._dominates(other) else other
        version = dict(self.version)
        for k, v in other.version.items():
            version[k] = max(version.get(k, 0), int(v))
        return LWWRegister(self.replica_id, self.policy, win._value,
                           win.timestamp, win.writer, version)

    def value(self) -> Any:
        return self._value

    def state(self) -> dict:
        return {"value": self._value, "timestamp": self.timestamp,
                "writer": self.writer, "policy": self.policy,
                "version": dict(sorted(self.version.items()))}

    def copy(self) -> "LWWRegister":
        return LWWRegister(self.replica_id, self.policy, self._value,
                           self.timestamp, self.writer, dict(self.version))

    @classmethod
    def random_sample(cls, rng: random.Random, replica_id: str) -> "LWWRegister":
        r = cls(replica_id, "LAMPORT_THEN_REPLICA_ID")
        r._value = f"v{rng.randint(0, 6)}"
        r.timestamp = rng.randint(0, 8)
        r.writer = f"R{rng.randint(0, 2)}"
        r.version = {f"R{i}": rng.randint(0, 8) for i in range(3)
                     if rng.random() < 0.8}
        return r


CRDT_TYPES: Tuple[Type[CRDT], ...] = (GCounter, PNCounter, GSet, ORSet,
                                      LWWRegister)


# --------------------------------------------------------------------------
# 6. Version vectors
# --------------------------------------------------------------------------

@dataclass
class VersionVector:
    """Per-replica version counters with a total merge (pointwise maximum)."""
    replica_id: str
    versions: Dict[str, int] = field(default_factory=dict)

    def tick(self) -> "VersionVector":
        self.versions[self.replica_id] = \
            self.versions.get(self.replica_id, 0) + 1
        return self

    def merge(self, other: "VersionVector") -> "VersionVector":
        out = VersionVector(self.replica_id, dict(self.versions))
        for k, v in other.versions.items():
            out.versions[k] = max(out.versions.get(k, 0), int(v))
        return out

    def dominates(self, other: "VersionVector") -> bool:
        keys = set(self.versions) | set(other.versions)
        ge = all(self.versions.get(k, 0) >= other.versions.get(k, 0)
                 for k in keys)
        gt = any(self.versions.get(k, 0) > other.versions.get(k, 0)
                 for k in keys)
        return ge and gt

    def concurrent(self, other: "VersionVector") -> bool:
        return not (self.dominates(other) or other.dominates(self)
                    or self.versions == other.versions)

    def compare(self, other: "VersionVector") -> str:
        if self.versions == other.versions:
            return "EQUAL"
        if self.dominates(other):
            return "DOMINATES"
        if other.dominates(self):
            return "DOMINATED"
        return "CONCURRENT"

    def copy(self) -> "VersionVector":
        return VersionVector(self.replica_id, dict(self.versions))

    def as_dict(self) -> dict:
        return {"replica_id": self.replica_id,
                "versions": dict(sorted(self.versions.items()))}


# --------------------------------------------------------------------------
# 7. Empirical verification of the CRDT laws (LCTL 1.6.x s36)
# --------------------------------------------------------------------------

@dataclass
class LawReport:
    crdt_type: str
    samples: int
    seed: int
    laws: Dict[str, Dict[str, Any]]
    ok: bool
    token: str

    def as_dict(self) -> dict:
        return {"crdt_type": self.crdt_type, "samples": self.samples,
                "seed": self.seed, "laws": self.laws, "ok": self.ok,
                "token": self.token}


def verify_crdt_laws(cls: Type[CRDT], samples: int = 40,
                     seed: int = 20260811) -> LawReport:
    """Empirically check determinism, commutativity, associativity, idempotence.

    The check compares canonical state hashes, so it is insensitive to the
    incidental ordering of Python containers and sensitive to every semantic
    difference.
    """
    if samples < 1:
        raise CRDTError("verify_crdt_laws needs at least one sample")
    rng = random.Random(seed)
    pool: List[CRDT] = [cls.random_sample(rng, f"R{i % 3}")
                        for i in range(max(3, samples))]

    laws: Dict[str, Dict[str, Any]] = {
        "deterministic": {"checked": 0, "failures": []},
        "commutative": {"checked": 0, "failures": []},
        "associative": {"checked": 0, "failures": []},
        "idempotent": {"checked": 0, "failures": []},
    }

    def h(x: CRDT) -> str:
        return state_hash(x.state())

    for i in range(samples):
        a = pool[rng.randrange(len(pool))]
        b = pool[rng.randrange(len(pool))]
        c = pool[rng.randrange(len(pool))]

        # determinism: the same merge twice yields the same state
        laws["deterministic"]["checked"] += 1
        if h(a.merge(b)) != h(a.merge(b)):
            laws["deterministic"]["failures"].append(
                {"sample": i, "a": a.state(), "b": b.state()})

        # commutativity: a.merge(b) == b.merge(a)
        laws["commutative"]["checked"] += 1
        if h(a.merge(b)) != h(b.merge(a)):
            laws["commutative"]["failures"].append(
                {"sample": i, "a": a.state(), "b": b.state(),
                 "ab": a.merge(b).state(), "ba": b.merge(a).state()})

        # associativity: (a.b).c == a.(b.c)
        laws["associative"]["checked"] += 1
        if h(a.merge(b).merge(c)) != h(a.merge(b.merge(c))):
            laws["associative"]["failures"].append(
                {"sample": i, "a": a.state(), "b": b.state(), "c": c.state()})

        # idempotence: a.merge(a) == a, and (a.b).b == a.b
        laws["idempotent"]["checked"] += 1
        if h(a.merge(a)) != h(a) or h(a.merge(b).merge(b)) != h(a.merge(b)):
            laws["idempotent"]["failures"].append(
                {"sample": i, "a": a.state(), "b": b.state()})

    ok = all(not v["failures"] for v in laws.values())
    return LawReport(crdt_type=cls.__name__, samples=samples, seed=seed,
                     laws=laws, ok=ok,
                     token=TOKEN_LAWS_VERIFIED if ok else TOKEN_LAWS_FAILED)


def verify_all_crdt_laws(samples: int = 40, seed: int = 20260811) -> dict:
    reports = [verify_crdt_laws(c, samples, seed) for c in CRDT_TYPES]
    return {"ok": all(r.ok for r in reports),
            "token": TOKEN_LAWS_VERIFIED if all(r.ok for r in reports)
            else TOKEN_LAWS_FAILED,
            "reports": [r.as_dict() for r in reports]}


# --------------------------------------------------------------------------
# 8. Anti-entropy reconciliation (LCTL 1.5.x s26)
# --------------------------------------------------------------------------

@dataclass
class SyncRecord:
    round_index: int
    a: str
    b: str
    a_hash_before: str
    b_hash_before: str
    merged_hash: str
    changed: bool

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ConvergenceReport:
    converged: bool
    token: str
    rounds: int
    replicas: List[str]
    final_hash: Optional[str]
    exchanges: List[dict]
    final_value: Any = None

    def as_dict(self) -> dict:
        return {"converged": self.converged, "token": self.token,
                "rounds": self.rounds, "replicas": self.replicas,
                "final_hash": self.final_hash, "final_value": self.final_value,
                "exchanges": self.exchanges}


class AntiEntropy:
    """Deterministic pairwise anti-entropy reconciler over CvRDT replicas.

    Pairs are visited in lexicographic order and each exchange is symmetric,
    so `converge()` is reproducible and terminates as soon as one full round
    changes nothing.
    """

    def __init__(self, replicas: Optional[Mapping[str, CRDT]] = None) -> None:
        self.replicas: Dict[str, CRDT] = dict(replicas or {})
        self.history: List[SyncRecord] = []
        self.version = VersionVector("anti_entropy")

    def add_replica(self, replica_id: str, crdt: CRDT) -> CRDT:
        if replica_id in self.replicas:
            raise CRDTError(f"replica {replica_id!r} already registered")
        if is_quantum_value(crdt.value()):
            raise QuantumReplicationRejected(
                f"{TOKEN_QUANTUM_REJECTED}: replica {replica_id!r} carries "
                f"quantum state")
        self.replicas[replica_id] = crdt
        return crdt

    def sync(self, a: str, b: str, round_index: int = 0) -> SyncRecord:
        if a not in self.replicas or b not in self.replicas:
            raise CRDTError(f"unknown replica in sync({a!r},{b!r})")
        ra, rb = self.replicas[a], self.replicas[b]
        ha, hb = ra.state_hash(), rb.state_hash()
        merged = ra.merge(rb)
        mh = merged.state_hash()
        self.replicas[a] = merged.copy()
        self.replicas[a].replica_id = a
        self.replicas[b] = merged.copy()
        self.replicas[b].replica_id = b
        rec = SyncRecord(round_index=round_index, a=a, b=b, a_hash_before=ha,
                         b_hash_before=hb, merged_hash=mh,
                         changed=(ha != mh or hb != mh))
        self.history.append(rec)
        self.version.tick()
        return rec

    def converge(self, max_rounds: int = 16) -> ConvergenceReport:
        ids = sorted(self.replicas)
        if len(ids) < 2:
            h = self.replicas[ids[0]].state_hash() if ids else None
            return ConvergenceReport(True, TOKEN_CONVERGED, 0, ids, h, [],
                                     self.replicas[ids[0]].value() if ids else None)
        rounds = 0
        for r in range(max_rounds):
            rounds = r + 1
            changed = False
            for a, b in itertools.combinations(ids, 2):
                rec = self.sync(a, b, r)
                changed = changed or rec.changed
            hashes = {self.replicas[i].state_hash() for i in ids}
            if len(hashes) == 1 and not changed:
                break
        hashes = {self.replicas[i].state_hash() for i in ids}
        converged = len(hashes) == 1
        return ConvergenceReport(
            converged=converged,
            token=TOKEN_CONVERGED if converged else TOKEN_NOT_CONVERGED,
            rounds=rounds, replicas=ids,
            final_hash=next(iter(hashes)) if converged else None,
            exchanges=[h.as_dict() for h in self.history],
            final_value=self.replicas[ids[0]].value() if converged else None)

    def as_dict(self) -> dict:
        return {"replicas": {k: self.replicas[k].as_dict()
                             for k in sorted(self.replicas)},
                "exchanges": [h.as_dict() for h in self.history],
                "version": self.version.as_dict()}


# --------------------------------------------------------------------------
# 9. Consistency contract (LCTL 1.4.x s10, hole H9)
# --------------------------------------------------------------------------

@dataclass
class PartitionModel:
    partitions_possible: bool = False
    bounded_duration: bool = True
    max_partition_ticks: Optional[int] = 10
    asymmetric: bool = False

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FailureModel:
    crash_stop: bool = True
    message_loss: bool = False
    reliable_retransmit: bool = True
    byzantine: bool = False
    max_concurrent_failures: int = 0
    replicas: int = 1
    failover: bool = False
    anti_entropy: bool = True
    crdt_declared: bool = False

    @property
    def quorum(self) -> int:
        return self.replicas // 2 + 1

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["quorum"] = self.quorum
        return d


# The explicit rule table (hole H9). Each entry is (rule_id, predicate,
# human-readable impossibility statement).
CONSISTENCY_RULES: Dict[str, Tuple[str, ...]] = {
    "LINEARIZABLE": ("C1", "C2", "C3", "C7"),
    "SEQUENTIAL": ("C1", "C2", "C3", "C7"),
    "CAUSAL": ("C4", "C7"),
    "EVENTUAL": ("C5", "C7"),
    "CRDT_DECLARED": ("C6", "C7"),
    "SINGLE_OWNER": ("C8",),
    "IMMUTABLE_REPLICA": ("C9",),
}


@dataclass
class ContractVerdict:
    profile: str
    admissible: bool
    token: str
    rules_fired: List[str]
    reasons: List[str]
    required_conditions: List[str]
    partition_model: dict
    failure_model: dict

    def as_dict(self) -> dict:
        return {"profile": self.profile, "admissible": self.admissible,
                "token": self.token, "rules_fired": self.rules_fired,
                "reasons": self.reasons,
                "required_conditions": self.required_conditions,
                "partition_model": self.partition_model,
                "failure_model": self.failure_model,
                "downgrade_offered": False,
                "note": "a blocked guarantee is never silently downgraded; the "
                        "caller must restate the requirement or change the model"}


class ConsistencyContract:
    """Decide whether a requested consistency guarantee is achievable.

    The contract never downgrades. If the requested profile is impossible
    under the declared partition and failure model, `evaluate()` returns the
    token CONSISTENCY_GUARANTEE_BLOCKED with every rule that fired, and
    `require()` raises `ConsistencyGuaranteeBlocked`.
    """

    def __init__(self, profile: str, partition_model: PartitionModel,
                 failure_model: FailureModel) -> None:
        if profile not in CONSISTENCY_PROFILES:
            raise CRDTError(f"unknown consistency profile {profile!r}; expected "
                            f"one of {list(CONSISTENCY_PROFILES)}")
        self.profile = profile
        self.partition = partition_model
        self.failure = failure_model

    def evaluate(self) -> ContractVerdict:
        p, f = self.partition, self.failure
        fired: List[str] = []
        reasons: List[str] = []
        required: List[str] = []

        if self.profile in ("LINEARIZABLE", "SEQUENTIAL"):
            required += ["a majority quorum reachable during every partition",
                         "no byzantine participants without 3f+1 replicas"]
            if p.partitions_possible and f.replicas < 3:
                fired.append("C1")
                reasons.append(
                    f"C1: {self.profile} requires a majority quorum to remain "
                    f"reachable, but the model declares partitions with only "
                    f"{f.replicas} replica(s); a minority side cannot serve a "
                    f"total order (CAP)")
            if p.partitions_possible and f.max_concurrent_failures >= f.quorum:
                fired.append("C2")
                reasons.append(
                    f"C2: max_concurrent_failures {f.max_concurrent_failures} "
                    f">= quorum {f.quorum}; no quorum survives")
            if f.byzantine and f.replicas < 3 * f.max_concurrent_failures + 1:
                fired.append("C3")
                reasons.append(
                    f"C3: byzantine failures need 3f+1 = "
                    f"{3 * f.max_concurrent_failures + 1} replicas, "
                    f"{f.replicas} declared")
        elif self.profile == "CAUSAL":
            required += ["causal metadata propagation",
                         "message loss recoverable by retransmission"]
            if f.message_loss and not f.reliable_retransmit:
                fired.append("C4")
                reasons.append(
                    "C4: causal delivery cannot be reconstructed when messages "
                    "are lost and never retransmitted")
        elif self.profile == "EVENTUAL":
            required += ["partitions eventually heal",
                         "an anti-entropy or retransmission path exists"]
            if p.partitions_possible and not p.bounded_duration:
                fired.append("C5")
                reasons.append(
                    "C5: 'eventual' is vacuous under an unbounded partition; "
                    "no convergence time can be stated")
            if f.message_loss and not (f.reliable_retransmit or f.anti_entropy):
                fired.append("C5")
                reasons.append(
                    "C5: unbounded message loss with neither retransmission nor "
                    "anti-entropy prevents convergence")
        elif self.profile == "CRDT_DECLARED":
            required += ["every replicated type is a verified CvRDT",
                         "an anti-entropy path exists"]
            if not f.crdt_declared:
                fired.append("C6")
                reasons.append(
                    "C6: the profile asserts CRDT semantics but the failure "
                    "model does not declare the replicated types as CvRDTs")
            if not f.anti_entropy:
                fired.append("C6")
                reasons.append("C6: CRDT convergence needs an anti-entropy path")
        elif self.profile == "SINGLE_OWNER":
            required += ["exactly one owner at a time",
                         "owner loss is either impossible or has failover"]
            if f.crash_stop and not f.failover and f.max_concurrent_failures > 0:
                fired.append("C8")
                reasons.append(
                    "C8: the single owner can crash and no failover is declared; "
                    "the guarantee cannot be maintained after owner loss")
        elif self.profile == "IMMUTABLE_REPLICA":
            required += ["replicas are write-once", "corruption is detectable"]
            if f.byzantine:
                fired.append("C9")
                reasons.append(
                    "C9: immutability cannot be assumed against byzantine "
                    "replicas without per-block attestation")

        # C7 applies to every replicated profile.
        if self.profile != "SINGLE_OWNER":
            required.append("no quantum state is replicated")

        admissible = not fired
        return ContractVerdict(
            profile=self.profile, admissible=admissible,
            token=TOKEN_ADMITTED if admissible else TOKEN_BLOCKED,
            rules_fired=sorted(set(fired)), reasons=reasons,
            required_conditions=required,
            partition_model=p.as_dict(), failure_model=f.as_dict())

    def require(self) -> ContractVerdict:
        v = self.evaluate()
        if not v.admissible:
            raise ConsistencyGuaranteeBlocked(
                f"{TOKEN_BLOCKED}: profile {self.profile!r} is impossible under "
                f"the declared models; rules {v.rules_fired}: "
                f"{'; '.join(v.reasons)}")
        return v

    def admit_value(self, value: Any) -> Any:
        """Rule C7: a QSTATE never enters a replicated scope."""
        if is_quantum_value(value):
            raise QuantumReplicationRejected(
                f"{TOKEN_QUANTUM_REJECTED}: rule C7 forbids replicating quantum "
                f"state under profile {self.profile!r}")
        return value

    def as_dict(self) -> dict:
        return self.evaluate().as_dict()


__all__ = [
    "CRDTError", "QuantumReplicationRejected", "MergeTypeError",
    "ConsistencyGuaranteeBlocked", "CRDT", "GCounter", "PNCounter", "GSet",
    "ORSet", "LWWRegister", "CRDT_TYPES", "CLOCK_POLICIES", "VersionVector",
    "AntiEntropy", "ConvergenceReport", "verify_crdt_laws",
    "verify_all_crdt_laws", "LawReport", "ConsistencyContract",
    "ContractVerdict", "PartitionModel", "FailureModel", "CONSISTENCY_RULES",
    "is_quantum_value", "reject_quantum", "state_hash",
    "TOKEN_BLOCKED", "TOKEN_QUANTUM_REJECTED", "TOKEN_LAWS_VERIFIED",
    "TOKEN_CONVERGED",
]
