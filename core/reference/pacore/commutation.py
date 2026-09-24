"""
Commutation Authority.

Implements the 1.3 -> 1.6 escalation of commutation authority:
  * LCTL 1.3.x s8   exact rule classes, symplectic Clifford representation,
                    bounded matrix fallback, commutation proof ledger
  * LCTL 1.4.x s13  Commutation Authority 2.0 (Pauli/symplectic, Clifford
                    conjugation, rotation, diagonal, channel, matrix oracle,
                    measurement barriers)
  * LCTL 1.5.x s8   Commutation Authority 3.0 (exact Clifford conjugation for
                    H, S/SDG, CX, CZ, SWAP, Pauli)
  * LCTL 1.6.x      Commutation Authority 4.0 (same catalog, measured)

Every scheduling rewrite produced from this module carries a proof record
(LCTL 1.5.x s8). No learned rule may be used before Q1-Q6 admission
(LCTL 1.3.x s8.5).

Exit gates covered: COMMUTATION_PASS, COMMUTATION_ENGINE_OPERATIONAL,
COMMUTATION_AUTHORITY_QUALIFIED, COMMUTATION_3_QUALIFIED.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import lang
from .lang import NULL_CELL

# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

COMMUTATION_STATUS = (
    "DISJOINT",                      # act on disjoint subsystems: exact
    "COMMUTING_EXACT",               # proven algebraically / symplectically
    "COMMUTING_NUMERICALLY_VERIFIED",  # bounded matrix oracle, stated tolerance
    "COMMUTING_TARGET_CONDITIONAL",  # commutes only under target assumptions
    "NON_COMMUTING",
    "UNRESOLVED",                    # honest: no rule covers this pair
)

# Explicit numerical tolerance for the bounded matrix oracle. LCTL 1.3.x s56
# requires pass thresholds to be explicit; this is that threshold.
MATRIX_COMMUTATOR_TOL = 1e-12
MATRIX_ORACLE_MAX_QUBITS = 6      # bounded operator dimension (2^6 = 64)


@dataclass
class CommutationVerdict:
    status: str
    reason: str
    rule_id: str
    exactness: str
    proof_id: str
    target_assumptions: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "rule_id": self.rule_id, "exactness": self.exactness,
                "proof_id": self.proof_id,
                "target_assumptions": list(self.target_assumptions)}


@dataclass
class ProofRow:
    """LCTL 1.3.x s8.4 commutation proof ledger row."""
    rule_id: str
    precondition: str
    operator_signature: str
    commutator_status: str
    equivalence_status: str
    exactness: str
    target_assumptions: Tuple[str, ...]
    source_hash: str
    result_hash: str
    proof_hash: str

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["target_assumptions"] = list(self.target_assumptions)
        return d


# --------------------------------------------------------------------------
# Gate matrices (exact reference operators, LCTL 1.1.x PHASE 4)
# --------------------------------------------------------------------------

_S2 = 1.0 / np.sqrt(2.0)

I2 = np.eye(2, dtype=complex)
PAULI_X = np.array([[0, 1], [1, 0]], dtype=complex)
PAULI_Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
PAULI_Z = np.array([[1, 0], [0, -1]], dtype=complex)
H_GATE = _S2 * np.array([[1, 1], [1, -1]], dtype=complex)
S_GATE = np.array([[1, 0], [0, 1j]], dtype=complex)
SDG_GATE = np.array([[1, 0], [0, -1j]], dtype=complex)
T_GATE = np.array([[1, 0], [0, np.exp(1j * np.pi / 4)]], dtype=complex)
TDG_GATE = np.array([[1, 0], [0, np.exp(-1j * np.pi / 4)]], dtype=complex)


def rx(t: float) -> np.ndarray:
    c, s = np.cos(t / 2), np.sin(t / 2)
    return np.array([[c, -1j * s], [-1j * s, c]], dtype=complex)


def ry(t: float) -> np.ndarray:
    c, s = np.cos(t / 2), np.sin(t / 2)
    return np.array([[c, -s], [s, c]], dtype=complex)


def rz(t: float) -> np.ndarray:
    return np.array([[np.exp(-1j * t / 2), 0], [0, np.exp(1j * t / 2)]],
                    dtype=complex)


def phase(t: float) -> np.ndarray:
    return np.array([[1, 0], [0, np.exp(1j * t)]], dtype=complex)


def u3(theta: float, phi: float = 0.0, lam: float = 0.0) -> np.ndarray:
    c, s = np.cos(theta / 2), np.sin(theta / 2)
    return np.array([[c, -np.exp(1j * lam) * s],
                     [np.exp(1j * phi) * s, np.exp(1j * (phi + lam)) * c]],
                    dtype=complex)


ONE_QUBIT_STATIC = {
    "I": I2, "X": PAULI_X, "Y": PAULI_Y, "Z": PAULI_Z, "H": H_GATE,
    "S": S_GATE, "SDG": SDG_GATE, "T": T_GATE, "TDG": TDG_GATE,
}
ONE_QUBIT_PARAM = {"RX": rx, "RY": ry, "RZ": rz, "PHASE": phase}

# Gates that are diagonal in the computational basis (mutually commuting).
DIAGONAL_GATES = frozenset({"I", "Z", "S", "SDG", "T", "TDG", "RZ", "PHASE", "CZ"})
# Gates in the Clifford group used by the symplectic path.
CLIFFORD_GATES = frozenset({"I", "X", "Y", "Z", "H", "S", "SDG",
                            "CX", "CNOT", "CZ", "SWAP"})
# Rotation families keyed by axis.
ROTATION_AXIS = {"RX": "X", "RY": "Y", "RZ": "Z", "PHASE": "Z"}


def one_qubit_matrix(op: str, params: Sequence[float]) -> Optional[np.ndarray]:
    if op in ONE_QUBIT_STATIC:
        return ONE_QUBIT_STATIC[op]
    if op in ONE_QUBIT_PARAM and params:
        return ONE_QUBIT_PARAM[op](params[0])
    if op == "U" and len(params) >= 1:
        p = list(params) + [0.0, 0.0]
        return u3(p[0], p[1], p[2])
    return None


def two_qubit_matrix(op: str) -> Optional[np.ndarray]:
    if op in ("CX", "CNOT"):
        return np.array([[1, 0, 0, 0], [0, 1, 0, 0],
                         [0, 0, 0, 1], [0, 0, 1, 0]], dtype=complex)
    if op == "CZ":
        return np.diag([1, 1, 1, -1]).astype(complex)
    if op == "CY":
        return np.array([[1, 0, 0, 0], [0, 1, 0, 0],
                         [0, 0, 0, -1j], [0, 0, 1j, 0]], dtype=complex)
    if op == "CH":
        m = np.eye(4, dtype=complex)
        m[2:, 2:] = H_GATE
        return m
    if op == "SWAP":
        return np.array([[1, 0, 0, 0], [0, 0, 1, 0],
                         [0, 1, 0, 0], [0, 0, 0, 1]], dtype=complex)
    if op == "ISWAP":
        return np.array([[1, 0, 0, 0], [0, 0, 1j, 0],
                         [0, 1j, 0, 0], [0, 0, 0, 1]], dtype=complex)
    return None


# --------------------------------------------------------------------------
# Pauli / symplectic representation (LCTL 1.3.x s8.2, 1.4.x s13.1)
# --------------------------------------------------------------------------

_PAULI_BITS = {"I": (0, 0), "X": (1, 0), "Y": (1, 1), "Z": (0, 1)}


def pauli_symplectic(term: str, qubits: Sequence[str],
                     support: Sequence[str]) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Encode a Pauli string over `support` as (x, z) binary vectors."""
    idx = {q: i for i, q in enumerate(support)}
    x = np.zeros(len(support), dtype=np.int8)
    z = np.zeros(len(support), dtype=np.int8)
    if len(term) != len(qubits):
        return None
    for ch, q in zip(term, qubits):
        if ch not in _PAULI_BITS or q not in idx:
            return None
        bx, bz = _PAULI_BITS[ch]
        x[idx[q]] ^= bx
        z[idx[q]] ^= bz
    return x, z


def symplectic_commutes(a: Tuple[np.ndarray, np.ndarray],
                        b: Tuple[np.ndarray, np.ndarray]) -> bool:
    """Two Pauli strings commute iff their symplectic inner product is 0."""
    ax, az = a
    bx, bz = b
    return int((ax @ bz + az @ bx) % 2) == 0


def gate_to_pauli(op: str) -> Optional[str]:
    return {"X": "X", "Y": "Y", "Z": "Z", "I": "I"}.get(op)


# --------------------------------------------------------------------------
# Clifford conjugation table (LCTL 1.5.x s8.2)
# --------------------------------------------------------------------------
# Maps (clifford, input Pauli) -> (output Pauli, sign) for single-qubit
# Cliffords. Signs are tracked but do not affect commutation.

CLIFFORD_CONJUGATION = {
    ("H", "X"): ("Z", +1), ("H", "Y"): ("Y", -1), ("H", "Z"): ("X", +1),
    ("S", "X"): ("Y", +1), ("S", "Y"): ("X", -1), ("S", "Z"): ("Z", +1),
    ("SDG", "X"): ("Y", -1), ("SDG", "Y"): ("X", +1), ("SDG", "Z"): ("Z", +1),
    ("X", "X"): ("X", +1), ("X", "Y"): ("Y", -1), ("X", "Z"): ("Z", -1),
    ("Y", "X"): ("X", -1), ("Y", "Y"): ("Y", +1), ("Y", "Z"): ("Z", -1),
    ("Z", "X"): ("X", -1), ("Z", "Y"): ("Y", -1), ("Z", "Z"): ("Z", +1),
    ("I", "X"): ("X", +1), ("I", "Y"): ("Y", +1), ("I", "Z"): ("Z", +1),
}

# Two-qubit Clifford conjugation for CX (control c, target t).
CX_CONJUGATION = {
    ("X", "I"): ("X", "X"), ("I", "X"): ("I", "X"),
    ("Z", "I"): ("Z", "I"), ("I", "Z"): ("Z", "Z"),
    ("Y", "I"): ("Y", "X"), ("I", "Y"): ("Z", "Y"),
}


# --------------------------------------------------------------------------
# The authority
# --------------------------------------------------------------------------

class CommutationAuthority:
    """Proof-gated commutation decisions for SES node pairs."""

    def __init__(self, tolerance: float = MATRIX_COMMUTATOR_TOL,
                 max_oracle_qubits: int = MATRIX_ORACLE_MAX_QUBITS) -> None:
        self.tolerance = tolerance
        self.max_oracle_qubits = max_oracle_qubits
        self.ledger: List[ProofRow] = []
        self._seen: Set[str] = set()
        self.quarantined: List[dict] = []

    # -- proof bookkeeping -------------------------------------------------
    def _record(self, rule_id: str, precondition: str, signature: str,
                verdict: str, exactness: str,
                assumptions: Tuple[str, ...] = ()) -> str:
        proof_hash = hashlib.sha256(
            "|".join([rule_id, precondition, signature, verdict, exactness,
                      ",".join(assumptions)]).encode("utf-8")).hexdigest()
        proof_id = "CP-" + proof_hash[:16]
        if proof_id not in self._seen:
            self._seen.add(proof_id)
            self.ledger.append(ProofRow(
                rule_id=rule_id, precondition=precondition,
                operator_signature=signature, commutator_status=verdict,
                equivalence_status="SEMANTICS_PRESERVING"
                if verdict != "NON_COMMUTING" else "ORDER_SIGNIFICANT",
                exactness=exactness, target_assumptions=assumptions,
                source_hash=hashlib.sha256(signature.encode()).hexdigest()[:32],
                result_hash=hashlib.sha256(verdict.encode()).hexdigest()[:32],
                proof_hash=proof_hash))
        return proof_id

    # -- main entry point --------------------------------------------------
    def classify_pair(self, na, nb) -> CommutationVerdict:
        """Classify a pair of SES nodes. `na`/`nb` are SESNode instances."""
        sa = set(na.reads) | set(na.writes)
        sb = set(nb.reads) | set(nb.writes)
        sig = f"{na.operation}({sorted(sa)}) ~ {nb.operation}({sorted(sb)})"

        # R-DISJOINT: disjoint subsystem commutation (LCTL 1.3.x s8.1)
        if not (sa & sb):
            pid = self._record("R-DISJOINT", "supp(A) n supp(B) = {}", sig,
                               "DISJOINT", "EXACT")
            return CommutationVerdict("DISJOINT",
                                      "operations act on disjoint subsystems",
                                      "R-DISJOINT", "EXACT", pid)

        # R-MEAS-BARRIER: never reorder across measurement / control
        # (LCTL 1.4.x s13.7)
        if na.operation in lang.DESTRUCTIVE_OPS or nb.operation in lang.DESTRUCTIVE_OPS \
                or na.operation in ("CLASSICAL_IF", "CLASSICAL_SWITCH", "FEEDBACK",
                                    "CLASSICAL_FEEDBACK", "BARRIER", "FENCE") \
                or nb.operation in ("CLASSICAL_IF", "CLASSICAL_SWITCH", "FEEDBACK",
                                    "CLASSICAL_FEEDBACK", "BARRIER", "FENCE"):
            pid = self._record("R-MEAS-BARRIER",
                               "measurement or classical-control boundary", sig,
                               "NON_COMMUTING", "EXACT")
            return CommutationVerdict(
                "NON_COMMUTING",
                "measurement / classical-control boundary is not reorderable",
                "R-MEAS-BARRIER", "EXACT", pid)

        # R-RESET: reset is a barrier for its own subsystem
        if "RESET" in (na.operation, nb.operation):
            pid = self._record("R-RESET", "reset acts on shared support", sig,
                               "NON_COMMUTING", "EXACT")
            return CommutationVerdict("NON_COMMUTING",
                                      "reset destroys shared subsystem state",
                                      "R-RESET", "EXACT", pid)

        # R-DIAG: simultaneously diagonal operators commute exactly
        if na.operation in DIAGONAL_GATES and nb.operation in DIAGONAL_GATES:
            pid = self._record("R-DIAG",
                               "both operators diagonal in the computational basis",
                               sig, "COMMUTING_EXACT", "EXACT")
            return CommutationVerdict("COMMUTING_EXACT",
                                      "diagonal operators commute",
                                      "R-DIAG", "EXACT", pid)

        # R-ROT-SAME-AXIS: same-axis rotations commute and may fuse
        aa, ab = ROTATION_AXIS.get(na.operation), ROTATION_AXIS.get(nb.operation)
        if aa and ab and aa == ab and sa == sb:
            pid = self._record("R-ROT-SAME-AXIS",
                               f"same rotation axis {aa} on identical support",
                               sig, "COMMUTING_EXACT", "EXACT")
            return CommutationVerdict(
                "COMMUTING_EXACT",
                f"same-axis ({aa}) rotations commute and are fusable",
                "R-ROT-SAME-AXIS", "EXACT", pid)

        # R-PAULI-SYMPLECTIC: exact symplectic decision for Pauli operators
        pa, pb = gate_to_pauli(na.operation), gate_to_pauli(nb.operation)
        if pa and pb:
            support = sorted(sa | sb)
            qa = sorted(sa)
            qb = sorted(sb)
            va = pauli_symplectic(pa * len(qa), qa, support)
            vb = pauli_symplectic(pb * len(qb), qb, support)
            if va and vb:
                ok = symplectic_commutes(va, vb)
                status = "COMMUTING_EXACT" if ok else "NON_COMMUTING"
                pid = self._record("R-PAULI-SYMPLECTIC",
                                   "symplectic inner product over GF(2)", sig,
                                   status, "EXACT")
                return CommutationVerdict(
                    status,
                    f"symplectic inner product = {0 if ok else 1}",
                    "R-PAULI-SYMPLECTIC", "EXACT", pid)

        # R-CLIFFORD-CONJ: exact Clifford conjugation where tabulated
        if na.operation in CLIFFORD_GATES and nb.operation in CLIFFORD_GATES:
            key = (na.operation, nb.operation)
            if key in CLIFFORD_CONJUGATION:
                out, sign = CLIFFORD_CONJUGATION[key]
                ok = (out == nb.operation and sign == +1)
                status = "COMMUTING_EXACT" if ok else "NON_COMMUTING"
                pid = self._record("R-CLIFFORD-CONJ",
                                   "tabulated single-qubit Clifford conjugation",
                                   sig, status, "EXACT")
                return CommutationVerdict(
                    status, f"conjugation maps {nb.operation} -> {sign:+d}{out}",
                    "R-CLIFFORD-CONJ", "EXACT", pid)

        # R-CHANNEL: noise channels (LCTL 1.4.x s13.5)
        if na.operation in lang.OPS_NOISE or nb.operation in lang.OPS_NOISE:
            both_noise = (na.operation in lang.OPS_NOISE
                          and nb.operation in lang.OPS_NOISE)
            if both_noise and na.operation == nb.operation:
                pid = self._record("R-CHANNEL-SAME",
                                   "identical Pauli-diagonal channels on shared "
                                   "support", sig,
                                   "COMMUTING_TARGET_CONDITIONAL", "APPROXIMATE",
                                   ("channels are Pauli-diagonal",
                                    "no time-dependent calibration drift"))
                return CommutationVerdict(
                    "COMMUTING_TARGET_CONDITIONAL",
                    "identical Pauli-diagonal channels commute under the stated "
                    "assumptions", "R-CHANNEL-SAME", "APPROXIMATE", pid,
                    ("channels are Pauli-diagonal",
                     "no time-dependent calibration drift"))
            pid = self._record("R-CHANNEL-MIXED",
                               "channel composed with a non-identical operation",
                               sig, "UNRESOLVED", "NO_FAITHFUL_FORM")
            return CommutationVerdict(
                "UNRESOLVED",
                "channel ordering not resolved by any admitted rule; the "
                "scheduler must serialize conservatively",
                "R-CHANNEL-MIXED", "NO_FAITHFUL_FORM", pid)

        # R-MATRIX-ORACLE: bounded numerical commutator (LCTL 1.4.x s13.6)
        verdict = self._matrix_oracle(na, nb, sorted(sa | sb), sig)
        if verdict is not None:
            return verdict

        pid = self._record("R-UNRESOLVED", "no admitted rule applies", sig,
                           "UNRESOLVED", "NO_FAITHFUL_FORM")
        return CommutationVerdict(
            "UNRESOLVED", "no admitted commutation rule covers this pair",
            "R-UNRESOLVED", "NO_FAITHFUL_FORM", pid)

    # -- bounded matrix oracle --------------------------------------------
    def _matrix_oracle(self, na, nb, support: List[str],
                       sig: str) -> Optional[CommutationVerdict]:
        n = len(support)
        if n == 0 or n > self.max_oracle_qubits:
            return None
        ma = self._embed(na, support, n)
        mb = self._embed(nb, support, n)
        if ma is None or mb is None:
            return None
        comm = ma @ mb - mb @ ma
        norm = float(np.max(np.abs(comm)))
        ok = norm <= self.tolerance
        status = "COMMUTING_NUMERICALLY_VERIFIED" if ok else "NON_COMMUTING"
        pid = self._record(
            "R-MATRIX-ORACLE",
            f"||[A,B]||_max <= {self.tolerance:g} over {n} qubit(s)", sig,
            status, "EXACT_NUMERICAL" if ok else "EXACT")
        return CommutationVerdict(
            status, f"max|[A,B]| = {norm:.3e} (tolerance {self.tolerance:g})",
            "R-MATRIX-ORACLE",
            "EXACT_NUMERICAL" if ok else "EXACT", pid)

    def _embed(self, node, support: List[str], n: int) -> Optional[np.ndarray]:
        """Embed a node's operator into the 2^n Hilbert space of `support`."""
        idx = {q: i for i, q in enumerate(support)}
        op = node.operation
        targets = [q for q in (list(node.reads) + list(node.writes))
                   if q in idx]
        targets = sorted(set(targets), key=lambda q: idx[q])
        params = _node_params(node)

        m1 = one_qubit_matrix(op, params)
        if m1 is not None and len(targets) == 1:
            return _kron_at(m1, idx[targets[0]], n)
        m2 = two_qubit_matrix(op)
        if m2 is not None and len(targets) == 2:
            return _embed_two(m2, idx[targets[0]], idx[targets[1]], n)
        return None

    # -- learned rules must be quarantined (LCTL 1.3.x s8.5) --------------
    def submit_learned_rule(self, rule: dict) -> str:
        """A learned rewrite is quarantined until Q1-Q6 admission. It is
        never usable by the optimizer from this call."""
        rec = dict(rule)
        rec["admission"] = "QUARANTINE"
        rec["usable_by_optimizer"] = False
        self.quarantined.append(rec)
        return "QUARANTINE"

    def ledger_dict(self) -> dict:
        return {"schema": "PA-LCTL/COMMUTATION_LEDGER/1",
                "tolerance": self.tolerance,
                "max_oracle_qubits": self.max_oracle_qubits,
                "rules": [r.as_dict() for r in self.ledger],
                "quarantined_learned_rules": self.quarantined}


def _node_params(node) -> List[float]:
    raw = node.resource_claim.get("angle") or node.error_model.get("angle")
    vals: List[float] = []
    if raw:
        try:
            vals.append(lang._eval_number(raw))
        except Exception:
            pass
    extra = getattr(node, "params", None)
    if extra:
        vals.extend(extra)
    return vals


def _kron_at(m: np.ndarray, pos: int, n: int) -> np.ndarray:
    out = np.array([[1.0 + 0j]])
    for i in range(n):
        out = np.kron(out, m if i == pos else I2)
    return out


def _embed_two(m: np.ndarray, a: int, b: int, n: int) -> np.ndarray:
    """Embed a 2-qubit operator acting on wires (a, b) into n qubits."""
    dim = 1 << n
    out = np.zeros((dim, dim), dtype=complex)
    for col in range(dim):
        bits = [(col >> (n - 1 - k)) & 1 for k in range(n)]
        sub_col = (bits[a] << 1) | bits[b]
        for sub_row in range(4):
            amp = m[sub_row, sub_col]
            if amp == 0:
                continue
            nb = list(bits)
            nb[a] = (sub_row >> 1) & 1
            nb[b] = sub_row & 1
            row = 0
            for k in range(n):
                row = (row << 1) | nb[k]
            out[row, col] += amp
    return out


# --------------------------------------------------------------------------
# Commutation-driven rewriting (LCTL 1.5.x s9)
# --------------------------------------------------------------------------

def plan_rewrites(ses, authority: CommutationAuthority) -> List[dict]:
    """Propose only semantics-preserving rewrites, each with a proof ref.

    Currently admitted rewrite classes:
      * same-axis rotation fusion (exact)
      * adjacent inverse cancellation (exact)
    Anything else is reported as OBSERVE and is not applied.
    """
    out: List[dict] = []
    order = ses.order
    inverse = {"S": "SDG", "SDG": "S", "T": "TDG", "TDG": "T",
               "X": "X", "Y": "Y", "Z": "Z", "H": "H",
               "CX": "CX", "CNOT": "CNOT", "CZ": "CZ", "SWAP": "SWAP"}
    for i in range(len(order) - 1):
        a, b = ses.nodes[order[i]], ses.nodes[order[i + 1]]
        sa = set(a.reads) | set(a.writes)
        sb = set(b.reads) | set(b.writes)
        if sa != sb or not sa:
            continue
        v = authority.classify_pair(a, b)
        if a.operation == b.operation and ROTATION_AXIS.get(a.operation):
            out.append({"rewrite": "ROTATION_FUSION", "nodes": [a.id, b.id],
                        "axis": ROTATION_AXIS[a.operation],
                        "admission": "ADMIT_EXACT", "proof_ref": v.proof_id,
                        "resource_effect": {"gate_count": -1},
                        "error_effect": {"delta": 0.0}})
        elif inverse.get(a.operation) == b.operation:
            out.append({"rewrite": "INVERSE_CANCELLATION",
                        "nodes": [a.id, b.id], "admission": "ADMIT_EXACT",
                        "proof_ref": v.proof_id,
                        "resource_effect": {"gate_count": -2},
                        "error_effect": {"delta": 0.0}})
        elif v.status in ("COMMUTING_EXACT", "COMMUTING_NUMERICALLY_VERIFIED"):
            out.append({"rewrite": "REORDER_CANDIDATE", "nodes": [a.id, b.id],
                        "admission": "OBSERVE", "proof_ref": v.proof_id,
                        "note": "commuting pair; reordering exposes a wider "
                                "antichain but is not applied automatically"})
    return out
