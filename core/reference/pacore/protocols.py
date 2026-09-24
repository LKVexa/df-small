"""
Distributed quantum protocol fabric with executable state transitions.

Implements:
  * LCTL 1.2.x s11   distributed quantum primitives (TELEPORT, REMOTE_CNOT,
                     ENTANGLEMENT_SWAP, PURIFY, HERALD, CLASSICAL_FEEDBACK)
  * LCTL 1.3.x s23   EPR / ebit lifecycle and the ENTANGLEMENT_INVENTORY_LEDGER
  * LCTL 1.3.x s24   classical feedback fabric: latency, timeout, retry,
                     happens-before edges, deterministic replay
  * LCTL 1.4.x s40   teleportation checklist: numerically verified against the
                     ideal reference, ownership moved and source invalidated
  * LCTL 1.4.x s42   purification validity domain: a recurrence is never
                     applied outside its stated noise model
  * LCTL 1.4.x s43   protocol compilation to a schedulable step DAG
  * LCTL 1.5.x s45   federated protocol scheduling records
  * LCTL 1.5.x s46   remote-CNOT reference equivalence
                     (token REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS)

Exit gates covered: EBIT_LIFECYCLE_OPERATIONAL, TELEPORT_REFERENCE_PASS,
REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS, ENTANGLEMENT_SWAP_PASS,
PURIFICATION_DOMAIN_PASS, CLASSICAL_FEEDBACK_OPERATIONAL,
PROTOCOL_COMPILER_OPERATIONAL.

Honesty invariants enforced here:
  * Every protocol result is produced by running pacore.simulator engines on
    the real state, never by asserting a textbook outcome.
  * Every fail-closed condition raises ProtocolError. No lifecycle transition
    is ever silently coerced.
  * No routine in this module contacts a network or a device. The "nodes" and
    "links" are logical names inside a single deterministic process.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import lang, simulator
from .lang import EPR_STATES, NULL_CELL
from .simulator import STATE_FIDELITY_TOL, StatevectorEngine

# --------------------------------------------------------------------------
# 0. Exceptions and thresholds
# --------------------------------------------------------------------------


class ProtocolError(Exception):
    """Raised for every fail-closed condition in the protocol fabric."""


# LCTL 1.4.x s40 / 1.5.x s46 reference-equivalence thresholds. Explicit, as
# LCTL 1.3.x s56 requires.
PROTOCOL_FIDELITY_TOL = 1e-9
TELEPORT_PASS_TOKEN = "TELEPORT_REFERENCE_EQUIVALENCE_PASS"
REMOTE_CNOT_PASS_TOKEN = "REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS"
SWAP_PASS_TOKEN = "ENTANGLEMENT_SWAP_REFERENCE_EQUIVALENCE_PASS"
FAIL_TOKEN = "REFERENCE_EQUIVALENCE_FAIL"

# BBPSSW is defined for Werner states; below F = 1/2 the recurrence does not
# increase fidelity and the model's derivation does not hold.
BBPSSW_MIN_FIDELITY = 0.5

_S2 = 1.0 / math.sqrt(2.0)
BELL_PHI_PLUS = np.array([_S2, 0.0, 0.0, _S2], dtype=complex)


def _normalize(vec: Sequence[complex], label: str) -> np.ndarray:
    arr = np.asarray(vec, dtype=complex).ravel()
    norm = float(np.linalg.norm(arr))
    if norm <= 0.0:
        raise ProtocolError(f"{label} has zero norm and is not a valid state")
    return arr / norm


# --------------------------------------------------------------------------
# 1. Ebit ledger (LCTL 1.3.x s23)
# --------------------------------------------------------------------------

# Admitted lifecycle transitions. Anything not listed fails closed.
EBIT_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "REQUESTED":  ("GENERATING", "FAILED", "EXPIRED"),
    "GENERATING": ("HERALDED", "FAILED", "EXPIRED"),
    "HERALDED":   ("RESERVED", "CONSUMED", "RELEASED", "EXPIRED"),
    "RESERVED":   ("CONSUMED", "RELEASED", "EXPIRED"),
    "CONSUMED":   (),
    "EXPIRED":    (),
    "FAILED":     (),
    "RELEASED":   (),
}
TERMINAL_EBIT_STATES = frozenset(s for s, nxt in EBIT_TRANSITIONS.items() if not nxt)
USABLE_EBIT_STATES = ("HERALDED", "RESERVED")

if set(EBIT_TRANSITIONS) != set(EPR_STATES):
    raise ProtocolError(
        "the ebit transition table does not cover lang.EPR_STATES exactly: "
        f"{sorted(set(EBIT_TRANSITIONS) ^ set(EPR_STATES))}")


@dataclass
class EbitRecord:
    """LCTL 1.3.x s23 ebit record. Field names are normative."""
    ebit_id: str
    endpoint_a: str
    endpoint_b: str
    creation_epoch: int
    fidelity: float
    expiry: int
    owner_protocol: str
    state: str
    provenance: List[dict] = field(default_factory=list)

    def endpoints(self) -> Tuple[str, str]:
        return tuple(sorted((self.endpoint_a, self.endpoint_b)))  # type: ignore

    def as_dict(self) -> dict:
        return {"ebit_id": self.ebit_id, "endpoint_a": self.endpoint_a,
                "endpoint_b": self.endpoint_b,
                "creation_epoch": self.creation_epoch,
                "fidelity": self.fidelity, "expiry": self.expiry,
                "owner_protocol": self.owner_protocol, "state": self.state,
                "provenance": list(self.provenance)}


class EbitLedger:
    """Authoritative entanglement inventory with a fail-closed lifecycle.

    Every transition is recorded in the record's `provenance`. A transition
    that is not in EBIT_TRANSITIONS raises ProtocolError; nothing is coerced.
    """

    def __init__(self, prefix: str = "EB") -> None:
        self.prefix = prefix
        self.records: Dict[str, EbitRecord] = {}
        self._counter = 0
        self.events: List[dict] = []

    # -- helpers -----------------------------------------------------------
    def _new_id(self) -> str:
        self._counter += 1
        return f"{self.prefix}-{self._counter:06d}"

    def get(self, ebit_id: str) -> EbitRecord:
        rec = self.records.get(ebit_id)
        if rec is None:
            raise ProtocolError(f"unknown ebit {ebit_id!r}")
        return rec

    def _transition(self, rec: EbitRecord, new_state: str, epoch: int,
                    note: str) -> None:
        if new_state not in EPR_STATES:
            raise ProtocolError(f"{new_state!r} is not an admitted EPR state")
        allowed = EBIT_TRANSITIONS[rec.state]
        if new_state not in allowed:
            raise ProtocolError(
                f"illegal ebit transition {rec.state} -> {new_state} for "
                f"{rec.ebit_id}; admitted next states are {list(allowed)}")
        rec.provenance.append({"from": rec.state, "to": new_state,
                               "epoch": int(epoch), "note": note})
        rec.state = new_state
        self.events.append({"ebit_id": rec.ebit_id, "state": new_state,
                            "epoch": int(epoch), "note": note})

    # -- lifecycle ---------------------------------------------------------
    def request(self, endpoint_a: str, endpoint_b: str, epoch: int = 0,
                expiry: int = 1 << 30, owner_protocol: str = NULL_CELL,
                target_fidelity: float = 1.0) -> str:
        if endpoint_a == endpoint_b:
            raise ProtocolError(
                f"an ebit needs two distinct endpoints; got {endpoint_a!r} twice")
        if expiry < epoch:
            raise ProtocolError(
                f"expiry {expiry} precedes creation epoch {epoch}")
        if not 0.0 <= target_fidelity <= 1.0:
            raise ProtocolError(
                f"target fidelity {target_fidelity} outside [0,1]")
        ebit_id = self._new_id()
        rec = EbitRecord(ebit_id=ebit_id, endpoint_a=endpoint_a,
                         endpoint_b=endpoint_b, creation_epoch=int(epoch),
                         fidelity=float(target_fidelity), expiry=int(expiry),
                         owner_protocol=owner_protocol, state="REQUESTED",
                         provenance=[{"from": NULL_CELL, "to": "REQUESTED",
                                      "epoch": int(epoch), "note": "request"}])
        self.records[ebit_id] = rec
        self.events.append({"ebit_id": ebit_id, "state": "REQUESTED",
                            "epoch": int(epoch), "note": "request"})
        return ebit_id

    def generate(self, ebit_id: str, epoch: int = 0) -> EbitRecord:
        rec = self.get(ebit_id)
        self._transition(rec, "GENERATING", epoch, "generation started")
        return rec

    def herald(self, ebit_id: str, fidelity: Optional[float] = None,
               epoch: int = 0) -> EbitRecord:
        rec = self.get(ebit_id)
        if fidelity is not None:
            if not 0.0 <= fidelity <= 1.0:
                raise ProtocolError(f"heralded fidelity {fidelity} outside [0,1]")
            rec.fidelity = float(fidelity)
        self._transition(rec, "HERALDED", epoch, "heralded by the link layer")
        return rec

    def reserve(self, ebit_id: str, owner_protocol: str, epoch: int = 0
                ) -> EbitRecord:
        rec = self.get(ebit_id)
        if rec.state != "HERALDED":
            raise ProtocolError(
                f"cannot reserve ebit {ebit_id} in state {rec.state}; only a "
                f"HERALDED ebit carries confirmed entanglement")
        self._check_live(rec, epoch)
        rec.owner_protocol = owner_protocol
        self._transition(rec, "RESERVED", epoch, f"reserved by {owner_protocol}")
        return rec

    def consume(self, ebit_id: str, epoch: int = 0,
                owner_protocol: Optional[str] = None) -> EbitRecord:
        rec = self.get(ebit_id)
        if rec.state == "CONSUMED":
            raise ProtocolError(
                f"double consume of ebit {ebit_id}: entanglement is not a "
                f"copyable resource (LCTL 1.1.x s6)")
        if rec.state == "EXPIRED":
            raise ProtocolError(
                f"ebit {ebit_id} expired at epoch {rec.expiry} and cannot be "
                f"consumed")
        if rec.state not in USABLE_EBIT_STATES:
            raise ProtocolError(
                f"cannot consume ebit {ebit_id} in state {rec.state}")
        self._check_live(rec, epoch)
        if owner_protocol is not None:
            rec.owner_protocol = owner_protocol
        self._transition(rec, "CONSUMED", epoch,
                         f"consumed by {rec.owner_protocol}")
        return rec

    def release(self, ebit_id: str, epoch: int = 0) -> EbitRecord:
        rec = self.get(ebit_id)
        self._transition(rec, "RELEASED", epoch, "released unused")
        return rec

    def fail(self, ebit_id: str, epoch: int = 0, reason: str = "generation failed"
             ) -> EbitRecord:
        rec = self.get(ebit_id)
        self._transition(rec, "FAILED", epoch, reason)
        return rec

    def expire(self, ebit_id: Optional[str] = None, epoch: int = 0
               ) -> List[str]:
        """Expire one ebit, or every non-terminal ebit past its expiry."""
        if ebit_id is not None:
            rec = self.get(ebit_id)
            self._transition(rec, "EXPIRED", epoch, "explicit expiry")
            return [ebit_id]
        expired: List[str] = []
        for rid in sorted(self.records):
            rec = self.records[rid]
            if rec.state in TERMINAL_EBIT_STATES:
                continue
            if epoch > rec.expiry:
                self._transition(rec, "EXPIRED", epoch,
                                 f"epoch {epoch} > expiry {rec.expiry}")
                expired.append(rid)
        return expired

    def _check_live(self, rec: EbitRecord, epoch: int) -> None:
        if epoch > rec.expiry:
            raise ProtocolError(
                f"ebit {rec.ebit_id} expired at epoch {rec.expiry}; the "
                f"request is at epoch {epoch}")

    # -- queries -----------------------------------------------------------
    def inventory(self, node_a: str, node_b: str, epoch: int = 0) -> List[str]:
        """Usable ebit ids between two nodes at `epoch`, in creation order."""
        want = tuple(sorted((node_a, node_b)))
        return [rid for rid in sorted(self.records)
                if self.records[rid].state in USABLE_EBIT_STATES
                and self.records[rid].endpoints() == want
                and epoch <= self.records[rid].expiry]

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in EPR_STATES}
        for rec in self.records.values():
            out[rec.state] += 1
        return out

    def as_dict(self) -> dict:
        """ENTANGLEMENT_INVENTORY_LEDGER emission (LCTL 1.3.x s23)."""
        return {
            "schema": "PA-LCTL/ENTANGLEMENT_INVENTORY_LEDGER/1",
            "admitted_states": list(EPR_STATES),
            "transition_table": {k: list(v) for k, v in
                                 sorted(EBIT_TRANSITIONS.items())},
            "ebits": [self.records[k].as_dict() for k in sorted(self.records)],
            "state_counts": self.counts(),
            "events": list(self.events),
        }


# --------------------------------------------------------------------------
# 2. Ownership records
# --------------------------------------------------------------------------

@dataclass
class OwnershipTransfer:
    """LCTL 1.1.x s6 / 1.4.x s40: an ownership move, never a copy."""
    object_key: str
    from_node: str
    to_node: str
    lineage: str
    epoch: int
    source_invalidated: bool
    reason: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _lineage_of(amplitudes: np.ndarray, tag: str) -> str:
    digest = hashlib.sha256(
        (tag + "|" + ";".join(f"{c.real:.12f},{c.imag:.12f}"
                              for c in amplitudes)).encode("utf-8")).hexdigest()
    return "LIN-" + digest[:16]


# --------------------------------------------------------------------------
# 3. Teleportation (LCTL 1.2.x s11, 1.4.x s40)
# --------------------------------------------------------------------------

def teleport(state_amplitudes: Sequence[complex], ebit_ledger: EbitLedger,
             ebit_id: str, src_node: str, dst_node: str, epoch: int = 0,
             seed: int = 0) -> dict:
    """Numerically execute single-qubit teleportation on a 3-qubit register.

    The ebit is looked up in `ebit_ledger`, must be usable, and is CONSUMED.
    Everything reported below is measured from the executed state, not
    asserted from the textbook: the Bell outcome is a Born-rule draw, the
    Pauli correction is applied from the classical bits, and the destination
    amplitudes are read back out of the collapsed register.
    """
    rec = ebit_ledger.get(ebit_id)
    if rec.state not in USABLE_EBIT_STATES:
        raise ProtocolError(
            f"teleport requires a HERALDED or RESERVED ebit; {ebit_id} is "
            f"{rec.state}")
    if set(rec.endpoints()) != set((src_node, dst_node)):
        raise ProtocolError(
            f"ebit {ebit_id} spans {rec.endpoints()}, not "
            f"({src_node}, {dst_node})")

    source = _normalize(state_amplitudes, "teleport source state")
    if source.shape[0] != 2:
        raise ProtocolError(
            f"teleport carries one qubit; got {source.shape[0]} amplitudes")

    eng = StatevectorEngine(["q_src", "q_ea", "q_eb"], seed=seed)
    eng.set_single_qubit_state("q_src", source)
    eng.apply_1q("H", "q_ea")
    eng.apply_2q("CX", "q_ea", "q_eb")

    # Bell measurement on (payload, local ebit half).
    eng.apply_2q("CX", "q_src", "q_ea")
    eng.apply_1q("H", "q_src")
    m_src = eng.measure("q_src", "Z")
    m_ea = eng.measure("q_ea", "Z")

    correction: List[str] = []
    if m_ea == 1:
        eng.apply_1q("X", "q_eb")
        correction.append("X")
    if m_src == 1:
        eng.apply_1q("Z", "q_eb")
        correction.append("Z")

    psi = eng.state()
    base = (m_src << 2) | (m_ea << 1)
    dest = np.array([psi[base], psi[base | 1]], dtype=complex)
    norm = float(np.linalg.norm(dest))
    if norm <= simulator.MAX_AMPLITUDE_ERROR_TOL:
        raise ProtocolError(
            "teleport destination branch carries no amplitude; the collapse "
            "bookkeeping is inconsistent")
    dest = dest / norm

    fidelity = float(abs(np.vdot(source, dest)) ** 2)

    ebit_ledger.consume(ebit_id, epoch=epoch, owner_protocol="TELEPORT")

    lineage = _lineage_of(source, "teleport")
    transfer = OwnershipTransfer(
        object_key="q_src", from_node=src_node, to_node=dst_node,
        lineage=lineage, epoch=int(epoch), source_invalidated=True,
        reason="TELEPORT moves the state; the source qubit was destructively "
               "measured and holds no copy (no-cloning)")

    passed = fidelity >= 1.0 - PROTOCOL_FIDELITY_TOL
    return {
        "protocol": "TELEPORT",
        "source_state": [complex(c) for c in source],
        "bell_measurement": {"q_src": m_src, "q_ea": m_ea},
        "classical_bits": [m_src, m_ea],
        "correction": correction,
        "correction_string": "".join(correction) or "I",
        "destination_state": [complex(c) for c in dest],
        "fidelity": fidelity,
        "fidelity_tolerance": PROTOCOL_FIDELITY_TOL,
        "ownership_transferred": True,
        "source_ownership_invalidated": True,
        "ownership_record": transfer.as_dict(),
        "ebit_id": ebit_id,
        "ebit_state_after": ebit_ledger.get(ebit_id).state,
        "ebits_consumed": 1,
        "src_node": src_node,
        "dst_node": dst_node,
        "epoch": int(epoch),
        "verdict": TELEPORT_PASS_TOKEN if passed else FAIL_TOKEN,
        "label": simulator.LOCAL_STATEVECTOR_LABEL,
    }


# --------------------------------------------------------------------------
# 4. Remote CNOT (LCTL 1.2.x s11, 1.5.x s46)
# --------------------------------------------------------------------------

def remote_cnot(control_amp: Sequence[complex],
                target_amp: Optional[Sequence[complex]],
                ebit_ledger: EbitLedger, ebit_id: str,
                control_node: str, target_node: str, epoch: int = 0,
                seed: int = 0) -> dict:
    """Cat-entangler / cat-disentangler nonlocal CNOT on one ebit + 2 cbits.

    `control_amp` is either a 2-amplitude single-qubit state (with
    `target_amp` giving the other qubit) or a 4-amplitude arbitrary two-qubit
    state (with `target_amp=None`). The executed result is compared against
    the ideal LOCAL CNOT applied to the same input.
    """
    rec = ebit_ledger.get(ebit_id)
    if rec.state not in USABLE_EBIT_STATES:
        raise ProtocolError(
            f"remote_cnot requires a HERALDED or RESERVED ebit; {ebit_id} is "
            f"{rec.state}")
    if set(rec.endpoints()) != set((control_node, target_node)):
        raise ProtocolError(
            f"ebit {ebit_id} spans {rec.endpoints()}, not "
            f"({control_node}, {target_node})")

    ctrl = np.asarray(control_amp, dtype=complex).ravel()
    if target_amp is None:
        if ctrl.shape[0] != 4:
            raise ProtocolError(
                "with target_amp=None, control_amp must be a 4-amplitude "
                "two-qubit state ordered |control,target>")
        joint = _normalize(ctrl, "remote_cnot input state")
    else:
        tgt = np.asarray(target_amp, dtype=complex).ravel()
        if ctrl.shape[0] != 2 or tgt.shape[0] != 2:
            raise ProtocolError(
                "single-qubit inputs need exactly 2 amplitudes each")
        joint = _normalize(np.kron(_normalize(ctrl, "control"),
                                   _normalize(tgt, "target")),
                           "remote_cnot input state")

    # Ideal local reference: CNOT|c,t> = |c, t xor c>.
    ideal = np.zeros(4, dtype=complex)
    for c in (0, 1):
        for t in (0, 1):
            ideal[(c << 1) | (t ^ c)] = joint[(c << 1) | t]

    # Register order: control(0), ebit half at control node(1),
    # ebit half at target node(2), target(3).
    qubits = ["q_c", "q_eA", "q_eB", "q_t"]
    eng = StatevectorEngine(qubits, seed=seed)
    psi = np.zeros(16, dtype=complex)
    for c in (0, 1):
        for t in (0, 1):
            amp = joint[(c << 1) | t]
            if amp == 0:
                continue
            for e in (0, 1):                       # |Phi+> on (q_eA, q_eB)
                idx = (c << 3) | (e << 2) | (e << 1) | t
                psi[idx] += amp * _S2
    eng.set_state(psi)

    # Cat-entangler: copy the control's Z-basis value onto the remote half.
    eng.apply_2q("CX", "q_c", "q_eA")
    m1 = eng.measure("q_eA", "Z")
    if m1 == 1:
        eng.apply_1q("X", "q_eB")

    # Remote application of the CNOT using the shared cat state.
    eng.apply_2q("CX", "q_eB", "q_t")

    # Cat-disentangler: remove the remote half without disturbing the control.
    eng.apply_1q("H", "q_eB")
    m2 = eng.measure("q_eB", "Z")
    if m2 == 1:
        eng.apply_1q("Z", "q_c")

    out = eng.state()
    final = np.zeros(4, dtype=complex)
    for c in (0, 1):
        for t in (0, 1):
            final[(c << 1) | t] = out[(c << 3) | (m1 << 2) | (m2 << 1) | t]
    norm = float(np.linalg.norm(final))
    if norm <= simulator.MAX_AMPLITUDE_ERROR_TOL:
        raise ProtocolError(
            "remote_cnot output branch carries no amplitude; the collapse "
            "bookkeeping is inconsistent")
    final = final / norm

    fidelity = float(abs(np.vdot(ideal, final)) ** 2)
    max_amp_err = simulator.max_amplitude_error(
        ideal, final * np.exp(-1j * np.angle(np.vdot(ideal, final))))

    ebit_ledger.consume(ebit_id, epoch=epoch, owner_protocol="REMOTE_CNOT")

    passed = fidelity >= 1.0 - PROTOCOL_FIDELITY_TOL
    ownership = OwnershipTransfer(
        object_key="q_c", from_node=control_node, to_node=control_node,
        lineage=_lineage_of(joint, "remote_cnot"), epoch=int(epoch),
        source_invalidated=False,
        reason="REMOTE_CNOT is a nonlocal unitary: both qubits stay with "
               "their owners, only the ebit is consumed")
    return {
        "protocol": "REMOTE_CNOT",
        "input_state": [complex(c) for c in joint],
        "ideal_state": [complex(c) for c in ideal],
        "final_state": [complex(c) for c in final],
        "fidelity": fidelity,
        "max_amplitude_error": float(max_amp_err),
        "fidelity_tolerance": PROTOCOL_FIDELITY_TOL,
        "ebits_consumed": 1,
        "ebit_id": ebit_id,
        "ebit_state_after": ebit_ledger.get(ebit_id).state,
        "measurements": {"q_eA": m1, "q_eB": m2},
        "classical_bits": [m1, m2],
        "classical_bits_sent": 2,
        "ownership_record": ownership.as_dict(),
        "control_node": control_node,
        "target_node": target_node,
        "epoch": int(epoch),
        "verdict": REMOTE_CNOT_PASS_TOKEN if passed else FAIL_TOKEN,
        "label": simulator.LOCAL_STATEVECTOR_LABEL,
    }


# --------------------------------------------------------------------------
# 5. Entanglement swapping (LCTL 1.2.x s11)
# --------------------------------------------------------------------------

def entanglement_swap(ebit_ledger: EbitLedger, ebit_ab: str, ebit_bc: str,
                      node_a: str, node_b: str, node_c: str, epoch: int = 0,
                      seed: int = 0, expiry: Optional[int] = None) -> dict:
    """Bell-measure at the middle node to herald a fresh A-C ebit.

    Both input ebits are CONSUMED. The resulting A-C pair is registered in
    the ledger and heralded only after the numerical correlation check
    passes; a failed check raises ProtocolError rather than heralding a pair
    that does not exist.
    """
    rec_ab = ebit_ledger.get(ebit_ab)
    rec_bc = ebit_ledger.get(ebit_bc)
    for rec, want in ((rec_ab, (node_a, node_b)), (rec_bc, (node_b, node_c))):
        if rec.state not in USABLE_EBIT_STATES:
            raise ProtocolError(
                f"entanglement_swap requires usable ebits; {rec.ebit_id} is "
                f"{rec.state}")
        if set(rec.endpoints()) != set(want):
            raise ProtocolError(
                f"ebit {rec.ebit_id} spans {rec.endpoints()}, not {want}")
    if node_a == node_c:
        raise ProtocolError("swap endpoints A and C must be distinct")

    # Register order: A(0), B1(1) [half of A-B], B2(2) [half of B-C], C(3).
    eng = StatevectorEngine(["q_A", "q_B1", "q_B2", "q_C"], seed=seed)
    eng.apply_1q("H", "q_A")
    eng.apply_2q("CX", "q_A", "q_B1")
    eng.apply_1q("H", "q_B2")
    eng.apply_2q("CX", "q_B2", "q_C")

    eng.apply_2q("CX", "q_B1", "q_B2")
    eng.apply_1q("H", "q_B1")
    m1 = eng.measure("q_B1", "Z")
    m2 = eng.measure("q_B2", "Z")

    correction: List[str] = []
    if m2 == 1:
        eng.apply_1q("X", "q_C")
        correction.append("X")
    if m1 == 1:
        eng.apply_1q("Z", "q_C")
        correction.append("Z")

    psi = eng.state()
    out = np.zeros(4, dtype=complex)
    for a in (0, 1):
        for c in (0, 1):
            out[(a << 1) | c] = psi[(a << 3) | (m1 << 2) | (m2 << 1) | c]
    norm = float(np.linalg.norm(out))
    if norm <= simulator.MAX_AMPLITUDE_ERROR_TOL:
        raise ProtocolError(
            "entanglement_swap output branch carries no amplitude")
    out = out / norm

    fidelity = float(abs(np.vdot(BELL_PHI_PLUS, out)) ** 2)
    # Independent correlation check on the executed state: <ZZ> and <XX>.
    check = StatevectorEngine(["q_A", "q_C"], seed=seed)
    check.set_state(out)
    zz = check.expectation("ZZ", ["q_A", "q_C"])
    xx = check.expectation("XX", ["q_A", "q_C"])
    correlations_ok = (abs(zz - 1.0) <= PROTOCOL_FIDELITY_TOL
                       and abs(xx - 1.0) <= PROTOCOL_FIDELITY_TOL)

    ebit_ledger.consume(ebit_ab, epoch=epoch, owner_protocol="ENTANGLEMENT_SWAP")
    ebit_ledger.consume(ebit_bc, epoch=epoch, owner_protocol="ENTANGLEMENT_SWAP")

    passed = fidelity >= 1.0 - PROTOCOL_FIDELITY_TOL and correlations_ok
    if not passed:
        raise ProtocolError(
            f"entanglement_swap produced fidelity {fidelity:.12f} with "
            f"<ZZ>={zz:.12f}, <XX>={xx:.12f}; refusing to herald an A-C pair "
            f"that the numerics do not support")

    new_expiry = expiry if expiry is not None else min(rec_ab.expiry, rec_bc.expiry)
    new_id = ebit_ledger.request(
        node_a, node_c, epoch=epoch, expiry=new_expiry,
        owner_protocol="ENTANGLEMENT_SWAP",
        target_fidelity=min(rec_ab.fidelity, rec_bc.fidelity))
    ebit_ledger.generate(new_id, epoch=epoch)
    ebit_ledger.herald(new_id, fidelity=min(rec_ab.fidelity, rec_bc.fidelity),
                       epoch=epoch)
    ebit_ledger.get(new_id).provenance.append(
        {"from": "SWAP", "to": "HERALDED", "epoch": int(epoch),
         "note": f"swapped from {ebit_ab} and {ebit_bc} at {node_b}"})

    return {
        "protocol": "ENTANGLEMENT_SWAP",
        "consumed_ebits": [ebit_ab, ebit_bc],
        "new_ebit_id": new_id,
        "new_ebit_state": ebit_ledger.get(new_id).state,
        "bell_measurement": {"q_B1": m1, "q_B2": m2},
        "classical_bits": [m1, m2],
        "correction": correction,
        "resulting_state": [complex(c) for c in out],
        "fidelity": fidelity,
        "zz_correlation": zz,
        "xx_correlation": xx,
        "correlations_ok": correlations_ok,
        "fidelity_tolerance": PROTOCOL_FIDELITY_TOL,
        "nodes": [node_a, node_b, node_c],
        "epoch": int(epoch),
        "verdict": SWAP_PASS_TOKEN,
        "label": simulator.LOCAL_STATEVECTOR_LABEL,
    }


# --------------------------------------------------------------------------
# 6. Purification (LCTL 1.4.x s42)
# --------------------------------------------------------------------------

PURIFICATION_MODELS = ("BBPSSW",)

BBPSSW_ASSUMPTIONS = (
    "both input pairs are Bell-diagonal and Werner-twirled with the stated "
    "fidelity F relative to |Phi+>",
    "the two input pairs are statistically independent (no correlated noise)",
    "local operations (bilateral CNOT) and measurements are noiseless",
    "classical communication of the two measurement outcomes is reliable",
    "the recurrence increases fidelity only for F > 1/2; outside that domain "
    "the model is not applied",
    "one output pair is produced per two input pairs, and only on success",
)


def purify(pair1_fidelity: float, pair2_fidelity: float,
           model: str = "BBPSSW") -> dict:
    """One BBPSSW recurrence step on two Werner pairs.

    REFUSES (ProtocolError) outside the model's stated validity domain. LCTL
    1.4.x s42 forbids applying a purification formula outside its assumed
    noise model, so no clamped or extrapolated value is ever returned.
    """
    model = model.upper()
    if model not in PURIFICATION_MODELS:
        raise ProtocolError(
            f"purification model {model!r} is not implemented; admitted "
            f"models are {list(PURIFICATION_MODELS)}")

    for name, f in (("pair1_fidelity", pair1_fidelity),
                    ("pair2_fidelity", pair2_fidelity)):
        if not isinstance(f, (int, float)) or isinstance(f, bool):
            raise ProtocolError(f"{name} must be a real number, got {f!r}")
        if math.isnan(f):
            raise ProtocolError(f"{name} is NaN")
        if not 0.0 <= f <= 1.0:
            raise ProtocolError(
                f"{name}={f} is outside the probability domain [0,1]")
        if f <= BBPSSW_MIN_FIDELITY:
            raise ProtocolError(
                f"{name}={f} is outside the BBPSSW validity domain "
                f"(F > {BBPSSW_MIN_FIDELITY}); the recurrence does not "
                f"increase fidelity there and this module refuses to "
                f"extrapolate it (LCTL 1.4.x s42)")

    f1, f2 = float(pair1_fidelity), float(pair2_fidelity)
    a1, a2 = f1, f2
    b1 = c1 = d1 = (1.0 - f1) / 3.0
    b2 = c2 = d2 = (1.0 - f2) / 3.0

    p_success = (a1 + d1) * (a2 + d2) + (b1 + c1) * (b2 + c2)
    if p_success <= 0.0:
        raise ProtocolError(
            "BBPSSW success probability evaluated to zero; the inputs are "
            "outside the model's domain")
    f_out = (a1 * a2 + d1 * d2) / p_success

    if not 0.0 <= f_out <= 1.0 + 1e-12:
        raise ProtocolError(
            f"BBPSSW produced an out-of-range output fidelity {f_out}")

    return {
        "model": "BBPSSW",
        "input_fidelities": [f1, f2],
        "output_fidelity": float(min(f_out, 1.0)),
        "success_probability": float(p_success),
        "improved": bool(f_out > max(f1, f2)),
        "pairs_consumed": 2,
        "pairs_produced_on_success": 1,
        "validity_domain": f"F > {BBPSSW_MIN_FIDELITY} for both input pairs",
        "assumptions": list(BBPSSW_ASSUMPTIONS),
        "regime": "APPROXIMATE",
        "regime_note": "the recurrence is exact for Werner states only; any "
                       "real pair must be twirled first, which itself loses "
                       "fidelity information",
    }


# --------------------------------------------------------------------------
# 7. Classical feedback fabric (LCTL 1.3.x s24)
# --------------------------------------------------------------------------

MESSAGE_STATES = ("SENT", "IN_FLIGHT", "DELIVERED", "TIMED_OUT", "DROPPED")


@dataclass
class ClassicalMessage:
    msg_id: str
    src: str
    dst: str
    payload: dict
    send_time: float
    latency: float
    arrival_time: float
    state: str
    attempts: int = 1
    depends_on: Tuple[str, ...] = ()
    deliver_time: Optional[float] = None

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["depends_on"] = list(self.depends_on)
        return d


class ClassicalFeedbackFabric:
    """Deterministic classical channel model with happens-before enforcement.

    A remote conditional quantum operation must call `assert_ready(msg_id)`
    before it executes. `assert_ready` raises ProtocolError unless the
    message has been delivered, so a correction can never run ahead of the
    classical bit it depends on.
    """

    def __init__(self, default_latency: float = 1.0, timeout: float = 8.0,
                 max_retries: int = 2, prefix: str = "MSG") -> None:
        if default_latency < 0.0:
            raise ProtocolError("default_latency must be non-negative")
        if timeout <= 0.0:
            raise ProtocolError("timeout must be positive")
        self.default_latency = float(default_latency)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.prefix = prefix
        self.clock = 0.0
        self.messages: Dict[str, ClassicalMessage] = {}
        self.order: List[str] = []
        self.happens_before: List[Tuple[str, str]] = []
        self.event_log: List[dict] = []
        self._counter = 0

    # -- helpers -----------------------------------------------------------
    def _new_id(self) -> str:
        self._counter += 1
        return f"{self.prefix}-{self._counter:06d}"

    def get(self, msg_id: str) -> ClassicalMessage:
        msg = self.messages.get(msg_id)
        if msg is None:
            raise ProtocolError(f"unknown classical message {msg_id!r}")
        return msg

    def _log(self, event: str, msg: ClassicalMessage, **extra) -> None:
        rec = {"event": event, "msg_id": msg.msg_id, "src": msg.src,
               "dst": msg.dst, "clock": self.clock, "state": msg.state}
        rec.update(extra)
        self.event_log.append(rec)

    # -- transport ---------------------------------------------------------
    def send(self, src: str, dst: str, payload: dict,
             depends_on: Sequence[str] = (), latency: Optional[float] = None,
             at_time: Optional[float] = None) -> str:
        if src == dst:
            raise ProtocolError(
                f"classical send from {src!r} to itself is not a fabric event")
        for dep in depends_on:
            dmsg = self.get(dep)
            if dmsg.state != "DELIVERED":
                raise ProtocolError(
                    f"message depends on {dep} which is {dmsg.state}, not "
                    f"DELIVERED; the happens-before edge would be violated")
        lat = self.default_latency if latency is None else float(latency)
        if lat < 0.0:
            raise ProtocolError("latency must be non-negative")
        send_time = self.clock if at_time is None else float(at_time)
        if send_time < self.clock:
            raise ProtocolError(
                f"send time {send_time} precedes the fabric clock {self.clock}")
        msg_id = self._new_id()
        msg = ClassicalMessage(
            msg_id=msg_id, src=src, dst=dst, payload=dict(payload),
            send_time=send_time, latency=lat, arrival_time=send_time + lat,
            state="SENT", attempts=1, depends_on=tuple(depends_on))
        self.messages[msg_id] = msg
        self.order.append(msg_id)
        for dep in depends_on:
            self.happens_before.append((dep, msg_id))
        self._log("SEND", msg, latency=lat, arrival_time=msg.arrival_time)
        return msg_id

    def receive(self, msg_id: str) -> dict:
        """Deliver a message, advancing the fabric clock to its arrival.

        A message whose in-flight time exceeds the fabric timeout is marked
        TIMED_OUT and raises; the caller must `retry` explicitly.
        """
        msg = self.get(msg_id)
        if msg.state == "DELIVERED":
            return dict(msg.payload)
        if msg.state in ("TIMED_OUT", "DROPPED"):
            raise ProtocolError(
                f"message {msg_id} is {msg.state}; it cannot be received")
        if msg.latency > self.timeout:
            msg.state = "TIMED_OUT"
            self.clock = max(self.clock, msg.send_time + self.timeout)
            self._log("TIMEOUT", msg, timeout=self.timeout)
            raise ProtocolError(
                f"message {msg_id} exceeded the {self.timeout} timeout "
                f"(latency {msg.latency}); a dependent quantum correction "
                f"must not proceed")
        self.clock = max(self.clock, msg.arrival_time)
        msg.state = "DELIVERED"
        msg.deliver_time = self.clock
        self._log("DELIVER", msg, deliver_time=msg.deliver_time)
        return dict(msg.payload)

    def retry(self, msg_id: str, latency: Optional[float] = None) -> str:
        """Re-send a timed-out message as a new message, bounded by
        `max_retries`. The original id stays TIMED_OUT in the replay log."""
        msg = self.get(msg_id)
        if msg.state != "TIMED_OUT":
            raise ProtocolError(
                f"retry requires a TIMED_OUT message; {msg_id} is {msg.state}")
        if msg.attempts > self.max_retries:
            msg.state = "DROPPED"
            self._log("DROP", msg, attempts=msg.attempts)
            raise ProtocolError(
                f"message {msg_id} exhausted {self.max_retries} retries and "
                f"is dropped; the dependent protocol must fail closed")
        new_id = self._new_id()
        lat = self.default_latency if latency is None else float(latency)
        new = ClassicalMessage(
            msg_id=new_id, src=msg.src, dst=msg.dst, payload=dict(msg.payload),
            send_time=self.clock, latency=lat, arrival_time=self.clock + lat,
            state="SENT", attempts=msg.attempts + 1,
            depends_on=msg.depends_on)
        self.messages[new_id] = new
        self.order.append(new_id)
        self.happens_before.append((msg_id, new_id))
        self._log("RETRY", new, of=msg_id, attempts=new.attempts)
        return new_id

    def drop(self, msg_id: str) -> None:
        msg = self.get(msg_id)
        msg.state = "DROPPED"
        self._log("DROP", msg)

    # -- happens-before ----------------------------------------------------
    def assert_ready(self, msg_id: str) -> dict:
        """Gate a remote conditional quantum operation on a classical bit."""
        msg = self.get(msg_id)
        if msg.state != "DELIVERED":
            raise ProtocolError(
                f"classical dependency {msg_id} is {msg.state}; a remote "
                f"conditional quantum operation must not execute before its "
                f"classical dependency is satisfied (LCTL 1.3.x s24)")
        return dict(msg.payload)

    def add_happens_before(self, before: str, after: str) -> None:
        self.get(before)
        self.get(after)
        if before == after:
            raise ProtocolError("a message cannot precede itself")
        self.happens_before.append((before, after))

    def check_acyclic(self) -> bool:
        adj: Dict[str, List[str]] = {}
        for a, b in self.happens_before:
            adj.setdefault(a, []).append(b)
        color: Dict[str, int] = {}

        def visit(n: str) -> bool:
            color[n] = 1
            for m in adj.get(n, ()):
                c = color.get(m, 0)
                if c == 1:
                    return False
                if c == 0 and not visit(m):
                    return False
            color[n] = 2
            return True

        for n in self.order:
            if color.get(n, 0) == 0 and not visit(n):
                raise ProtocolError(
                    "the classical happens-before relation contains a cycle")
        return True

    # -- deterministic replay ---------------------------------------------
    def replay(self) -> List[dict]:
        """The event log in deterministic order. Two runs of the same program
        produce byte-identical replays."""
        return [dict(e) for e in self.event_log]

    def replay_hash(self) -> str:
        payload = ";".join(
            f"{e['event']}:{e['msg_id']}:{e['src']}->{e['dst']}:"
            f"{e['clock']:.6f}:{e['state']}" for e in self.event_log)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        return {"schema": "PA-LCTL/CLASSICAL_FEEDBACK_FABRIC/1",
                "default_latency": self.default_latency,
                "timeout": self.timeout, "max_retries": self.max_retries,
                "clock": self.clock,
                "messages": [self.messages[m].as_dict() for m in self.order],
                "happens_before": [list(e) for e in self.happens_before],
                "replay": self.replay(),
                "replay_hash": self.replay_hash()}


# --------------------------------------------------------------------------
# 8. Protocol compiler (LCTL 1.4.x s43, 1.5.x s45)
# --------------------------------------------------------------------------

STEP_KINDS = ("EPR_GENERATE", "EPR_HERALD", "LOCAL_BELL", "MEASURE",
              "CLASSICAL_SEND", "CLASSICAL_RECV", "PAULI_CORRECT",
              "OWNERSHIP_TRANSFER")


@dataclass
class ProtocolStep:
    step_id: str
    kind: str
    node: str
    link: str
    depends_on: Tuple[str, ...]
    ebit_required: bool
    classical_required: bool
    row: str = NULL_CELL
    operation: str = NULL_CELL
    detail: str = ""

    def as_dict(self) -> dict:
        return {"step_id": self.step_id, "kind": self.kind, "node": self.node,
                "link": self.link, "depends_on": list(self.depends_on),
                "ebit_required": self.ebit_required,
                "classical_required": self.classical_required,
                "row": self.row, "operation": self.operation,
                "detail": self.detail}


@dataclass
class ProtocolPlan:
    steps: List[ProtocolStep]
    rows: List[str]
    program_seal: str = ""

    def by_id(self, step_id: str) -> ProtocolStep:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        raise ProtocolError(f"unknown step {step_id!r}")

    def topological_order(self) -> List[str]:
        ids = [s.step_id for s in self.steps]
        pred = {s.step_id: set(s.depends_on) for s in self.steps}
        for sid, deps in pred.items():
            for d in deps:
                if d not in pred:
                    raise ProtocolError(
                        f"step {sid} depends on unknown step {d!r}")
        out: List[str] = []
        done: Set[str] = set()
        remaining = list(ids)
        while remaining:
            progressed = False
            for sid in list(remaining):
                if pred[sid] <= done:
                    out.append(sid)
                    done.add(sid)
                    remaining.remove(sid)
                    progressed = True
            if not progressed:
                raise ProtocolError(
                    f"protocol step DAG has a cycle among {remaining}")
        return out

    def validate(self) -> dict:
        """Fail-closed structural checks on the compiled DAG."""
        order = self.topological_order()
        index = {sid: i for i, sid in enumerate(order)}
        for s in self.steps:
            if s.kind not in STEP_KINDS:
                raise ProtocolError(
                    f"step {s.step_id} has kind {s.kind!r}, not in {STEP_KINDS}")
            for d in s.depends_on:
                if index[d] >= index[s.step_id]:
                    raise ProtocolError(
                        f"step {s.step_id} does not follow its dependency {d}")
            if s.kind == "PAULI_CORRECT" and not s.classical_required:
                raise ProtocolError(
                    f"step {s.step_id} is a Pauli correction that declares no "
                    f"classical dependency; a correction may never precede "
                    f"its classical bit")
            if s.kind == "PAULI_CORRECT":
                if not any(self.by_id(d).kind == "CLASSICAL_RECV"
                           for d in s.depends_on):
                    raise ProtocolError(
                        f"step {s.step_id} corrects without depending on a "
                        f"CLASSICAL_RECV step")
            if s.kind == "CLASSICAL_RECV":
                if not any(self.by_id(d).kind == "CLASSICAL_SEND"
                           for d in s.depends_on):
                    raise ProtocolError(
                        f"step {s.step_id} receives without a matching "
                        f"CLASSICAL_SEND dependency")
        return {"steps": len(self.steps), "topological_order": order,
                "acyclic": True, "correction_gating_ok": True}

    def as_dict(self) -> dict:
        return {"schema": "PA-LCTL/PROTOCOL_STEP_DAG/1",
                "program_seal": self.program_seal,
                "protocol_rows": list(self.rows),
                "step_kinds": list(STEP_KINDS),
                "steps": [s.as_dict() for s in self.steps]}


class ProtocolCompiler:
    """Compile PROTOCOL-face rows into a schedulable step DAG.

    The compiler is total over the templates it declares and fails closed on
    anything else: an unrecognized distributed primitive raises ProtocolError
    rather than emitting an empty or guessed schedule.
    """

    #: Distributed primitives with a compiled template.
    SUPPORTED_OPS = (
        "ENTANGLE_LINK", "EPR_RESERVE", "EPR_RELEASE", "HERALD",
        "TELEPORT", "REMOTE_CNOT", "REMOTE_CONTROL", "REMOTE_MEASURE",
        "ENTANGLEMENT_SWAP", "PURIFY", "CLASSICAL_FEEDBACK",
        "QCHANNEL_SEND", "QCHANNEL_RECEIVE", "SYNC_QCLOCK",
    )

    def __init__(self) -> None:
        self._counter = 0

    def _sid(self, row_id: str, kind: str) -> str:
        self._counter += 1
        return f"S{self._counter:04d}:{row_id}:{kind}"

    def compile(self, program: lang.Program, vr=None) -> ProtocolPlan:
        self._counter = 0
        steps: List[ProtocolStep] = []
        rows: List[str] = []
        last_step_for_link: Dict[str, str] = {}

        for row in program.rows:
            if row.face != "PROTOCOL" and row.op not in lang.OPS_DISTRIBUTED_Q:
                continue
            if row.op not in self.SUPPORTED_OPS:
                raise ProtocolError(
                    f"row {row.row_id}: distributed primitive {row.op!r} has "
                    f"no compilation template; admitted primitives are "
                    f"{list(self.SUPPORTED_OPS)}")
            rows.append(row.row_id)
            node_a = row.node if row.node != NULL_CELL else "N_LOCAL"
            node_b = self._peer_node(row, program, node_a)
            link = row.link
            prior = last_step_for_link.get(link) if link != NULL_CELL else None
            emitted = self._template(row, node_a, node_b, link, prior)
            steps.extend(emitted)
            if link != NULL_CELL and emitted:
                last_step_for_link[link] = emitted[-1].step_id

        plan = ProtocolPlan(steps=steps, rows=rows,
                            program_seal=program.seal())
        plan.validate()
        return plan

    # -- templates ---------------------------------------------------------
    @staticmethod
    def _peer_node(row: lang.Row, program: lang.Program, node_a: str) -> str:
        """Resolve the remote endpoint from the declared LINK, else a
        deterministic placeholder. Never guesses a physical topology."""
        if row.link != NULL_CELL:
            for r in program.rows:
                if r.op == "DECLARE_LINK" and r.out == row.link:
                    ends = [r.a, r.b]
                    for e in ends:
                        if e != NULL_CELL and e != node_a:
                            return e
        return "N_REMOTE"

    def _template(self, row: lang.Row, node_a: str, node_b: str, link: str,
                  prior: Optional[str]) -> List[ProtocolStep]:
        op = row.op
        out: List[ProtocolStep] = []

        def add(kind: str, node: str, deps: Sequence[str],
                ebit: bool = False, classical: bool = False,
                detail: str = "") -> str:
            sid = self._sid(row.row_id, kind)
            base = tuple(d for d in deps if d)
            if prior and not base and not out:
                base = (prior,)
            out.append(ProtocolStep(
                step_id=sid, kind=kind, node=node, link=link,
                depends_on=base, ebit_required=ebit,
                classical_required=classical, row=row.row_id,
                operation=op, detail=detail))
            return sid

        if op in ("ENTANGLE_LINK", "EPR_RESERVE"):
            g = add("EPR_GENERATE", node_a, (), detail="request an ebit on the link")
            add("EPR_HERALD", node_b, (g,), ebit=True,
                detail="heralding confirms the pair exists")
            return out

        if op == "HERALD":
            add("EPR_HERALD", node_a, (), ebit=True, detail="explicit herald row")
            return out

        if op == "EPR_RELEASE":
            add("OWNERSHIP_TRANSFER", node_a, (), ebit=True,
                detail="release the reserved ebit back to the pool")
            return out

        if op == "TELEPORT":
            g = add("EPR_GENERATE", node_a, ())
            h = add("EPR_HERALD", node_b, (g,), ebit=True)
            lb = add("LOCAL_BELL", node_a, (h,), ebit=True,
                     detail="CNOT + H on (payload, local ebit half)")
            m = add("MEASURE", node_a, (lb,), ebit=True,
                    detail="destructive Z measurement of both local qubits")
            s = add("CLASSICAL_SEND", node_a, (m,),
                    detail="two classical bits to the destination")
            r = add("CLASSICAL_RECV", node_b, (s,), classical=True)
            c = add("PAULI_CORRECT", node_b, (r,), classical=True,
                    detail="X^m1 then Z^m0 on the destination half")
            add("OWNERSHIP_TRANSFER", node_b, (c,),
                detail="ownership moves; the source qubit is invalidated")
            return out

        if op in ("REMOTE_CNOT", "REMOTE_CONTROL"):
            g = add("EPR_GENERATE", node_a, ())
            h = add("EPR_HERALD", node_b, (g,), ebit=True)
            e1 = add("LOCAL_BELL", node_a, (h,), ebit=True,
                     detail="cat-entangler: CNOT(control, local ebit half)")
            m1 = add("MEASURE", node_a, (e1,), ebit=True)
            s1 = add("CLASSICAL_SEND", node_a, (m1,))
            r1 = add("CLASSICAL_RECV", node_b, (s1,), classical=True)
            c1 = add("PAULI_CORRECT", node_b, (r1,), classical=True,
                     detail="X^m1 on the remote ebit half")
            e2 = add("LOCAL_BELL", node_b, (c1,), ebit=True,
                     detail="apply the CNOT using the shared cat state, then H")
            m2 = add("MEASURE", node_b, (e2,), ebit=True)
            s2 = add("CLASSICAL_SEND", node_b, (m2,))
            r2 = add("CLASSICAL_RECV", node_a, (s2,), classical=True)
            add("PAULI_CORRECT", node_a, (r2,), classical=True,
                detail="cat-disentangler: Z^m2 on the control")
            return out

        if op == "REMOTE_MEASURE":
            m = add("MEASURE", node_a, (), detail="local destructive measurement")
            s = add("CLASSICAL_SEND", node_a, (m,))
            add("CLASSICAL_RECV", node_b, (s,), classical=True)
            return out

        if op == "ENTANGLEMENT_SWAP":
            g1 = add("EPR_GENERATE", node_a, (), detail="A-B pair")
            h1 = add("EPR_HERALD", node_b, (g1,), ebit=True)
            g2 = add("EPR_GENERATE", node_b, (h1,), detail="B-C pair")
            h2 = add("EPR_HERALD", node_b, (g2,), ebit=True)
            lb = add("LOCAL_BELL", node_b, (h2,), ebit=True,
                     detail="Bell measurement at the middle node")
            m = add("MEASURE", node_b, (lb,), ebit=True)
            s = add("CLASSICAL_SEND", node_b, (m,))
            r = add("CLASSICAL_RECV", node_a, (s,), classical=True)
            c = add("PAULI_CORRECT", node_a, (r,), classical=True,
                    detail="X^m2 then Z^m1 on the far end")
            add("EPR_HERALD", node_a, (c,), ebit=True,
                detail="herald the swapped A-C pair")
            return out

        if op == "PURIFY":
            g1 = add("EPR_GENERATE", node_a, (), detail="first input pair")
            h1 = add("EPR_HERALD", node_b, (g1,), ebit=True)
            g2 = add("EPR_GENERATE", node_a, (h1,), detail="second input pair")
            h2 = add("EPR_HERALD", node_b, (g2,), ebit=True)
            lb = add("LOCAL_BELL", node_a, (h2,), ebit=True,
                     detail="bilateral CNOT across the two pairs")
            m = add("MEASURE", node_a, (lb,), ebit=True)
            s = add("CLASSICAL_SEND", node_a, (m,))
            r = add("CLASSICAL_RECV", node_b, (s,), classical=True)
            add("EPR_HERALD", node_b, (r,), ebit=True,
                detail="herald the surviving pair only on outcome agreement")
            return out

        if op == "CLASSICAL_FEEDBACK":
            s = add("CLASSICAL_SEND", node_a, ())
            r = add("CLASSICAL_RECV", node_b, (s,), classical=True)
            add("PAULI_CORRECT", node_b, (r,), classical=True,
                detail="conditional Pauli gated on the delivered bit")
            return out

        if op in ("QCHANNEL_SEND", "QCHANNEL_RECEIVE"):
            add("OWNERSHIP_TRANSFER", node_a if op == "QCHANNEL_SEND" else node_b,
                (), detail="quantum channel move; ownership is transferred, "
                           "never duplicated")
            return out

        if op == "SYNC_QCLOCK":
            s = add("CLASSICAL_SEND", node_a, ())
            add("CLASSICAL_RECV", node_b, (s,), classical=True,
                detail="clock synchronization is classical only")
            return out

        raise ProtocolError(
            f"row {row.row_id}: no template for {op!r} (this is a compiler "
            f"bug: SUPPORTED_OPS and _template are out of step)")
