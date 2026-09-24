"""
Exact classical numerical engines and the numerical backend planner.

Implements:
  * LCTL 1.3.x s14   numerical backend selection (rule-based, explainable)
  * LCTL 1.3.x s15   dense statevector engine (exact, deterministic)
  * LCTL 1.3.x s16   density-matrix engine with explicit Kraus channels
  * LCTL 1.3.x s17   stabilizer / binary-symplectic engine (CHP tableau)
  * LCTL 1.3.x s19.3 truthful execution labelling: every run produced by this
                     module is a CLASSICAL simulation and is labelled as such
  * LCTL 1.3.x s56   explicit numerical pass thresholds (no implicit tolerance)
  * LCTL 1.4.x s34   adaptive backend re-planning inputs
  * LCTL 1.5.x s37   federated / farm backend classes

Exit gates covered: NUMERICAL_CORE_PASS, STATEVECTOR_ENGINE_OPERATIONAL,
DENSITY_ENGINE_OPERATIONAL, STABILIZER_ENGINE_OPERATIONAL,
BACKEND_PLANNER_OPERATIONAL, EXECUTION_LABEL_HONESTY_PASS.

Honesty invariants enforced here (LCTL 1.3.x s19.3):
  * No result dictionary produced by this module may carry a label implying
    physical quantum execution. Distributed / sharded runs are labelled
    exactly `CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION`.
  * Every failure path raises a named exception. Nothing is silently repaired.

Conventions fixed by this module (stated because the LCTL documents do not
fix them):
  * Qubit order is the caller-supplied list; index 0 is the MOST significant
    bit of the basis-state index and the leftmost character of a bitstring.
  * `DEPOLARIZE(p)` means rho -> (1-p) rho + p * I/2 on the target qubit.
  * `BIT_FLIP(p)` / `PHASE_FLIP(p)` / `DEPHASE(p)` mean rho -> (1-p) rho +
    p * A rho A with A = X / Z / Z respectively.
  * von Neumann entropy is reported in bits (log base 2).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import commutation, lang
from .lang import NULL_CELL

# --------------------------------------------------------------------------
# 0. Explicit numerical thresholds (LCTL 1.3.x s56)
# --------------------------------------------------------------------------

MAX_AMPLITUDE_ERROR_TOL = 1e-9
DENSITY_FROBENIUS_TOL = 1e-9
TRACE_TOL = 1e-9
STATE_FIDELITY_TOL = 1e-9
DISTRIBUTION_TV_TOL = 1e-9

# Kraus completeness is a structural property of a declared channel, not a
# convergence tolerance; it is checked far tighter than the pass thresholds.
KRAUS_COMPLETENESS_TOL = 1e-10

# Probabilities below this are omitted from reported distributions.
PROBABILITY_FLOOR = 1e-15

# Amplitude rounding used for the reproducible final-state hash.
STATE_HASH_DECIMALS = 12


class SimulationError(Exception):
    """Raised for any fail-closed condition in a numerical engine."""


class BackendPlanError(Exception):
    """Raised when no admitted backend can serve a circuit summary."""


# --------------------------------------------------------------------------
# 1. Operator helpers
# --------------------------------------------------------------------------

_I2 = commutation.I2
_X = commutation.PAULI_X
_Y = commutation.PAULI_Y
_Z = commutation.PAULI_Z
_H = commutation.H_GATE
_SDG = commutation.SDG_GATE

_PAULI_BY_CHAR = {"I": _I2, "X": _X, "Y": _Y, "Z": _Z}

# Basis rotations. `measure` applies these and does NOT invert them: an LCTL
# MEASURE row is destructive (lang.DESTRUCTIVE_OPS) and the post-measurement
# frame is the rotated one.
_BASIS_ROTATION = {
    "Z": (),
    "X": ("H",),
    "Y": ("SDG", "H"),
}


def three_qubit_matrix(op: str) -> Optional[np.ndarray]:
    """Exact 8x8 operators for the three-qubit catalog entries of
    lang.GATE_ARITY. Qubit `a` is the most significant of the triple."""
    if op in ("CCX", "TOFFOLI"):
        m = np.eye(8, dtype=complex)
        m[6, 6] = m[7, 7] = 0.0
        m[6, 7] = m[7, 6] = 1.0
        return m
    if op == "CSWAP":
        m = np.eye(8, dtype=complex)
        m[5, 5] = m[6, 6] = 0.0
        m[5, 6] = m[6, 5] = 1.0
        return m
    return None


def _apply_operator(psi: np.ndarray, mat: np.ndarray,
                    positions: Sequence[int], n: int) -> np.ndarray:
    """Apply `mat` (2^k x 2^k) to wires `positions` of a 2^n amplitude vector.

    Axis 0 of the reshaped tensor is the most significant qubit, matching the
    bitstring convention documented in the module docstring.
    """
    k = len(positions)
    if mat.shape != (1 << k, 1 << k):
        raise SimulationError(
            f"operator shape {mat.shape} does not match {k} wire(s)")
    if len(set(positions)) != k:
        raise SimulationError(f"repeated wire in operator target {positions}")
    tens = psi.reshape([2] * n)
    op = mat.reshape([2] * (2 * k))
    out = np.tensordot(op, tens, axes=(list(range(k, 2 * k)), list(positions)))
    remaining = [ax for ax in range(n) if ax not in positions]
    source_axes = list(positions) + remaining
    inverse = np.argsort(source_axes)
    return np.ascontiguousarray(out.transpose(inverse)).reshape(1 << n)


def _apply_operator_density(rho: np.ndarray, mat: np.ndarray,
                            positions: Sequence[int], n: int) -> np.ndarray:
    """rho -> U rho U^dagger with U = `mat` embedded on `positions`."""
    dim = 1 << n
    k = len(positions)
    tens = rho.reshape([2] * (2 * n))
    op = mat.reshape([2] * (2 * k))
    # left multiply on the ket indices (axes 0..n-1)
    out = np.tensordot(op, tens, axes=(list(range(k, 2 * k)), list(positions)))
    remaining = [ax for ax in range(2 * n) if ax not in positions]
    inverse = np.argsort(list(positions) + remaining)
    tens = out.transpose(inverse)
    # right multiply on the bra indices (axes n..2n-1) by U^dagger
    bra_pos = [n + p for p in positions]
    opc = np.conjugate(op)
    out = np.tensordot(opc, tens, axes=(list(range(k, 2 * k)), bra_pos))
    remaining = [ax for ax in range(2 * n) if ax not in bra_pos]
    inverse = np.argsort(bra_pos + remaining)
    tens = out.transpose(inverse)
    return np.ascontiguousarray(tens).reshape(dim, dim)


def _bitstring(index: int, n: int) -> str:
    return format(index, f"0{n}b") if n else ""


def _hash_array(arr: np.ndarray) -> str:
    """Reproducible hash of an amplitude / density array.

    Values are rounded to STATE_HASH_DECIMALS decimals and negative zero is
    normalized, so the hash is stable across equivalent computations.
    """
    flat = np.asarray(arr, dtype=complex).ravel()
    re = np.round(flat.real, STATE_HASH_DECIMALS)
    im = np.round(flat.imag, STATE_HASH_DECIMALS)
    re[re == 0.0] = 0.0
    im[im == 0.0] = 0.0
    payload = ";".join(f"{a:.{STATE_HASH_DECIMALS}f}|{b:.{STATE_HASH_DECIMALS}f}"
                       for a, b in zip(re, im))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 2. Statevector engine (LCTL 1.3.x s15)
# --------------------------------------------------------------------------

class StatevectorEngine:
    """Dense exact statevector engine over complex128 amplitudes."""

    backend_name = "local_statevector"

    def __init__(self, qubits: List[str], seed: int = 0) -> None:
        if not qubits:
            raise SimulationError("StatevectorEngine requires at least one qubit")
        if len(set(qubits)) != len(qubits):
            raise SimulationError(f"duplicate qubit name in {qubits}")
        self.qubits: List[str] = list(qubits)
        self.n: int = len(self.qubits)
        self.index: Dict[str, int] = {q: i for i, q in enumerate(self.qubits)}
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.psi = np.zeros(1 << self.n, dtype=np.complex128)
        self.psi[0] = 1.0
        self.measurement_log: List[dict] = []

    # -- wire resolution ---------------------------------------------------
    def wire(self, target: str) -> int:
        if target not in self.index:
            raise SimulationError(f"unknown qubit {target!r}; "
                                  f"declared qubits are {self.qubits}")
        return self.index[target]

    # -- state initialization ----------------------------------------------
    def set_state(self, amplitudes: Sequence[complex]) -> None:
        vec = np.asarray(amplitudes, dtype=np.complex128).ravel()
        if vec.shape[0] != (1 << self.n):
            raise SimulationError(
                f"state has {vec.shape[0]} amplitudes, expected {1 << self.n}")
        norm = float(np.linalg.norm(vec))
        if norm <= 0.0:
            raise SimulationError("cannot set a zero-norm state")
        self.psi = vec / norm

    def set_single_qubit_state(self, target: str,
                               amplitudes: Sequence[complex]) -> None:
        """Place a normalized single-qubit state on `target`, which must be
        unentangled and currently |0>. Fails closed otherwise."""
        amp = np.asarray(amplitudes, dtype=np.complex128).ravel()
        if amp.shape[0] != 2:
            raise SimulationError("single-qubit state needs exactly 2 amplitudes")
        norm = float(np.linalg.norm(amp))
        if norm <= 0.0:
            raise SimulationError("cannot set a zero-norm single-qubit state")
        amp = amp / norm
        p = self.wire(target)
        tens = self.psi.reshape([2] * self.n)
        weight_one = float(np.linalg.norm(np.take(tens, 1, axis=p)))
        if weight_one > MAX_AMPLITUDE_ERROR_TOL:
            raise SimulationError(
                f"qubit {target!r} is not in |0>; refusing to overwrite "
                f"unknown quantum state (LCTL 1.1.x s6 no-cloning)")
        zero_slice = np.take(tens, 0, axis=p)
        new = np.stack([amp[0] * zero_slice, amp[1] * zero_slice], axis=p)
        self.psi = np.ascontiguousarray(new).reshape(1 << self.n)

    # -- gate application ---------------------------------------------------
    def apply_1q(self, name: str, target: str,
                 params: Sequence[float] = ()) -> None:
        mat = commutation.one_qubit_matrix(name, list(params))
        if mat is None:
            raise SimulationError(
                f"{name!r} is not an admitted single-qubit operator "
                f"(missing PARAM angle?)")
        self.psi = _apply_operator(self.psi, mat, [self.wire(target)], self.n)

    def apply_2q(self, name: str, a: str, b: str) -> None:
        mat = commutation.two_qubit_matrix(name)
        if mat is None:
            raise SimulationError(f"{name!r} is not an admitted two-qubit operator")
        self.psi = _apply_operator(self.psi, mat,
                                   [self.wire(a), self.wire(b)], self.n)

    def apply_3q(self, name: str, a: str, b: str, c: str) -> None:
        mat = three_qubit_matrix(name)
        if mat is None:
            raise SimulationError(f"{name!r} is not an admitted three-qubit operator")
        self.psi = _apply_operator(self.psi, mat,
                                   [self.wire(a), self.wire(b), self.wire(c)],
                                   self.n)

    def apply_gate(self, name: str, targets: Sequence[str],
                   params: Sequence[float] = ()) -> None:
        """Arity-dispatching helper used by the circuit runner."""
        k = len(targets)
        if k == 1:
            self.apply_1q(name, targets[0], params)
        elif k == 2:
            self.apply_2q(name, targets[0], targets[1])
        elif k == 3:
            self.apply_3q(name, targets[0], targets[1], targets[2])
        else:
            raise SimulationError(f"unsupported gate arity {k} for {name!r}")

    def apply_matrix(self, mat: np.ndarray, targets: Sequence[str]) -> None:
        self.psi = _apply_operator(self.psi, np.asarray(mat, dtype=complex),
                                   [self.wire(t) for t in targets], self.n)

    # -- measurement --------------------------------------------------------
    def _marginal_one(self, wire: int) -> float:
        tens = self.psi.reshape([2] * self.n)
        one = np.take(tens, 1, axis=wire)
        return float(np.vdot(one, one).real)

    def measure(self, target: str, basis: str = "Z") -> int:
        """True Born-rule measurement with collapse.

        Bases Z, X, Y are supported. The basis rotation is applied and NOT
        inverted: LCTL measurement is destructive of the pre-measurement frame.
        """
        basis = basis.upper()
        if basis not in _BASIS_ROTATION:
            raise SimulationError(
                f"unsupported measurement basis {basis!r}; "
                f"admitted bases are {sorted(_BASIS_ROTATION)}")
        for g in _BASIS_ROTATION[basis]:
            self.apply_1q(g, target)

        w = self.wire(target)
        p1 = self._marginal_one(w)
        p1 = min(max(p1, 0.0), 1.0)
        draw = float(self.rng.random())
        outcome = 1 if draw < p1 else 0
        prob = p1 if outcome == 1 else 1.0 - p1
        if prob <= PROBABILITY_FLOOR:
            raise SimulationError(
                f"measurement of {target!r} selected an outcome of probability "
                f"{prob:.3e}; numerical state is degenerate")

        tens = self.psi.reshape([2] * self.n).copy()
        kill = 1 - outcome
        idx: List[object] = [slice(None)] * self.n
        idx[w] = kill
        tens[tuple(idx)] = 0.0
        vec = np.ascontiguousarray(tens).reshape(1 << self.n)
        self.psi = vec / math.sqrt(prob)
        self.measurement_log.append(
            {"qubit": target, "basis": basis, "outcome": outcome,
             "p1": p1, "draw": draw})
        return outcome

    def reset(self, target: str) -> None:
        """Return `target` to |0>.

        Implemented as a Born-rule projection followed by the classically
        conditioned X that maps the |1> branch back to |0>. A bare projection
        onto |0> is not trace preserving and would silently discard amplitude;
        this module never silently discards amplitude.
        """
        outcome = self.measure(target, "Z")
        if outcome == 1:
            self.apply_1q("X", target)

    # -- observables --------------------------------------------------------
    def probabilities(self) -> Dict[str, float]:
        probs = np.abs(self.psi) ** 2
        return {_bitstring(i, self.n): float(p)
                for i, p in enumerate(probs) if p > PROBABILITY_FLOOR}

    def expectation(self, pauli_string: str, qubits: List[str]) -> float:
        """<psi| P |psi> for a Pauli string over the named qubits."""
        pauli_string = pauli_string.upper()
        if len(pauli_string) != len(qubits):
            raise SimulationError(
                f"Pauli string {pauli_string!r} has length {len(pauli_string)} "
                f"but {len(qubits)} qubits were named")
        work = self.psi
        for ch, q in zip(pauli_string, qubits):
            if ch not in _PAULI_BY_CHAR:
                raise SimulationError(f"{ch!r} is not a Pauli factor")
            if ch == "I":
                continue
            work = _apply_operator(work, _PAULI_BY_CHAR[ch], [self.wire(q)], self.n)
        val = complex(np.vdot(self.psi, work))
        if abs(val.imag) > MAX_AMPLITUDE_ERROR_TOL:
            raise SimulationError(
                f"Pauli expectation has imaginary part {val.imag:.3e}; "
                f"the operator is not Hermitian as embedded")
        return float(val.real)

    def state(self) -> np.ndarray:
        return self.psi.copy()

    def fidelity_with(self, other) -> float:
        vec = other.psi if isinstance(other, StatevectorEngine) \
            else np.asarray(other, dtype=complex).ravel()
        if vec.shape[0] != self.psi.shape[0]:
            raise SimulationError(
                f"fidelity between dimension {self.psi.shape[0]} and "
                f"{vec.shape[0]} states is undefined")
        return float(abs(np.vdot(self.psi, vec)) ** 2)

    def to_density(self) -> np.ndarray:
        return np.outer(self.psi, np.conjugate(self.psi))

    def partial_trace(self, keep: List[str]) -> np.ndarray:
        """Reduced density matrix over `keep`, in the order given."""
        for q in keep:
            self.wire(q)
        if len(set(keep)) != len(keep):
            raise SimulationError(f"duplicate qubit in partial_trace keep={keep}")
        kept = [self.wire(q) for q in keep]
        traced = [i for i in range(self.n) if i not in kept]
        tens = self.psi.reshape([2] * self.n).transpose(kept + traced)
        k = len(kept)
        mat = np.ascontiguousarray(tens).reshape(1 << k, 1 << (self.n - k))
        return mat @ mat.conjugate().T

    def marginal(self, target: str) -> Tuple[float, float]:
        p1 = self._marginal_one(self.wire(target))
        return 1.0 - p1, p1

    def state_hash(self) -> str:
        return _hash_array(self.psi)


# --------------------------------------------------------------------------
# 3. Density-matrix engine (LCTL 1.3.x s16)
# --------------------------------------------------------------------------

def _kraus_depolarize(p: float) -> List[np.ndarray]:
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"DEPOLARIZE probability {p} outside [0,1]")
    return [math.sqrt(1.0 - 3.0 * p / 4.0) * _I2,
            math.sqrt(p / 4.0) * _X,
            math.sqrt(p / 4.0) * _Y,
            math.sqrt(p / 4.0) * _Z]


def _kraus_pauli_pair(p: float, op: np.ndarray, name: str) -> List[np.ndarray]:
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"{name} probability {p} outside [0,1]")
    return [math.sqrt(1.0 - p) * _I2, math.sqrt(p) * op]


def _kraus_amplitude_damp(g: float) -> List[np.ndarray]:
    if not 0.0 <= g <= 1.0:
        raise ValueError(f"AMPLITUDE_DAMP rate {g} outside [0,1]")
    k0 = np.array([[1.0, 0.0], [0.0, math.sqrt(1.0 - g)]], dtype=complex)
    k1 = np.array([[0.0, math.sqrt(g)], [0.0, 0.0]], dtype=complex)
    return [k0, k1]


def _kraus_phase_damp(l: float) -> List[np.ndarray]:
    if not 0.0 <= l <= 1.0:
        raise ValueError(f"PHASE_DAMP rate {l} outside [0,1]")
    k0 = np.array([[1.0, 0.0], [0.0, math.sqrt(1.0 - l)]], dtype=complex)
    k1 = np.array([[0.0, 0.0], [0.0, math.sqrt(l)]], dtype=complex)
    return [k0, k1]


def _kraus_pauli_channel(px: float, py: float, pz: float) -> List[np.ndarray]:
    for nm, v in (("px", px), ("py", py), ("pz", pz)):
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"PAULI_CHANNEL {nm}={v} outside [0,1]")
    tot = px + py + pz
    if tot > 1.0 + KRAUS_COMPLETENESS_TOL:
        raise ValueError(
            f"PAULI_CHANNEL probabilities sum to {tot} > 1")
    return [math.sqrt(max(0.0, 1.0 - tot)) * _I2,
            math.sqrt(px) * _X, math.sqrt(py) * _Y, math.sqrt(pz) * _Z]


def kraus_operators(name: str, params: Sequence) -> List[np.ndarray]:
    """Explicit Kraus set for an lang.OPS_NOISE channel.

    Fails closed with ValueError naming the channel for unknown channels,
    out-of-domain parameters, or an incomplete operator set.
    """
    name = name.upper()
    ps = list(params)
    try:
        if name == "DEPOLARIZE":
            ops = _kraus_depolarize(float(ps[0]))
        elif name in ("DEPHASE", "PHASE_FLIP"):
            ops = _kraus_pauli_pair(float(ps[0]), _Z, name)
        elif name == "BIT_FLIP":
            ops = _kraus_pauli_pair(float(ps[0]), _X, name)
        elif name == "AMPLITUDE_DAMP":
            ops = _kraus_amplitude_damp(float(ps[0]))
        elif name == "PHASE_DAMP":
            ops = _kraus_phase_damp(float(ps[0]))
        elif name == "PAULI_CHANNEL":
            if len(ps) < 3:
                raise ValueError("PAULI_CHANNEL requires px;py;pz")
            ops = _kraus_pauli_channel(float(ps[0]), float(ps[1]), float(ps[2]))
        elif name == "KRAUS_CHANNEL":
            if not ps:
                raise ValueError("KRAUS_CHANNEL requires an explicit operator list")
            raw = ps[0] if isinstance(ps[0], (list, tuple)) else ps
            ops = [np.asarray(k, dtype=complex) for k in raw]
            if not ops:
                raise ValueError("KRAUS_CHANNEL operator list is empty")
        else:
            raise ValueError(f"{name} is not an admitted Kraus channel")
    except IndexError as exc:
        raise ValueError(f"{name} is missing a required parameter") from exc

    dim = ops[0].shape[0]
    for k in ops:
        if k.shape != (dim, dim):
            raise ValueError(f"{name} Kraus operators have inconsistent shapes")
    total = sum(k.conjugate().T @ k for k in ops)
    err = float(np.max(np.abs(total - np.eye(dim, dtype=complex))))
    if err > KRAUS_COMPLETENESS_TOL:
        raise ValueError(
            f"{name} Kraus set is not complete: max|sum K^dag K - I| = "
            f"{err:.3e} > {KRAUS_COMPLETENESS_TOL:g}")
    return ops


class DensityEngine:
    """Exact density-matrix engine with explicit Kraus noise channels."""

    backend_name = "local_density"

    def __init__(self, qubits: List[str], seed: int = 0) -> None:
        if not qubits:
            raise SimulationError("DensityEngine requires at least one qubit")
        if len(set(qubits)) != len(qubits):
            raise SimulationError(f"duplicate qubit name in {qubits}")
        self.qubits: List[str] = list(qubits)
        self.n: int = len(self.qubits)
        self.index: Dict[str, int] = {q: i for i, q in enumerate(self.qubits)}
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        dim = 1 << self.n
        self.rho = np.zeros((dim, dim), dtype=np.complex128)
        self.rho[0, 0] = 1.0
        self.measurement_log: List[dict] = []
        self.channel_log: List[dict] = []

    # -- wire resolution ---------------------------------------------------
    def wire(self, target: str) -> int:
        if target not in self.index:
            raise SimulationError(f"unknown qubit {target!r}; "
                                  f"declared qubits are {self.qubits}")
        return self.index[target]

    def set_density(self, rho: np.ndarray) -> None:
        m = np.asarray(rho, dtype=np.complex128)
        dim = 1 << self.n
        if m.shape != (dim, dim):
            raise SimulationError(f"density matrix shape {m.shape} != {(dim, dim)}")
        self.rho = m.copy()

    def set_from_statevector(self, psi: Sequence[complex]) -> None:
        vec = np.asarray(psi, dtype=np.complex128).ravel()
        if vec.shape[0] != (1 << self.n):
            raise SimulationError("statevector dimension mismatch")
        norm = float(np.linalg.norm(vec))
        if norm <= 0.0:
            raise SimulationError("cannot seed from a zero-norm state")
        vec = vec / norm
        self.rho = np.outer(vec, np.conjugate(vec))

    # -- unitary application ------------------------------------------------
    def apply_1q(self, name: str, target: str,
                 params: Sequence[float] = ()) -> None:
        mat = commutation.one_qubit_matrix(name, list(params))
        if mat is None:
            raise SimulationError(
                f"{name!r} is not an admitted single-qubit operator "
                f"(missing PARAM angle?)")
        self.rho = _apply_operator_density(self.rho, mat, [self.wire(target)],
                                           self.n)

    def apply_2q(self, name: str, a: str, b: str) -> None:
        mat = commutation.two_qubit_matrix(name)
        if mat is None:
            raise SimulationError(f"{name!r} is not an admitted two-qubit operator")
        self.rho = _apply_operator_density(self.rho, mat,
                                           [self.wire(a), self.wire(b)], self.n)

    def apply_3q(self, name: str, a: str, b: str, c: str) -> None:
        mat = three_qubit_matrix(name)
        if mat is None:
            raise SimulationError(f"{name!r} is not an admitted three-qubit operator")
        self.rho = _apply_operator_density(
            self.rho, mat, [self.wire(a), self.wire(b), self.wire(c)], self.n)

    def apply_gate(self, name: str, targets: Sequence[str],
                   params: Sequence[float] = ()) -> None:
        k = len(targets)
        if k == 1:
            self.apply_1q(name, targets[0], params)
        elif k == 2:
            self.apply_2q(name, targets[0], targets[1])
        elif k == 3:
            self.apply_3q(name, targets[0], targets[1], targets[2])
        else:
            raise SimulationError(f"unsupported gate arity {k} for {name!r}")

    # -- channels -----------------------------------------------------------
    def apply_channel(self, name: str, target: str,
                      params: Sequence = ()) -> None:
        """Apply a declared noise channel via its explicit Kraus set.

        Kraus completeness (sum K^dag K = I) is verified to
        KRAUS_COMPLETENESS_TOL and a violation raises ValueError naming the
        channel. Nothing is renormalized to hide an incomplete set.
        """
        ops = kraus_operators(name, params)
        k_wires = int(round(math.log2(ops[0].shape[0])))
        if k_wires != 1:
            raise SimulationError(
                f"{name} was declared on {k_wires} wires; only single-qubit "
                f"channel placement is implemented")
        w = self.wire(target)
        acc = np.zeros_like(self.rho)
        for k in ops:
            acc += _apply_operator_density(self.rho, k, [w], self.n)
        self.rho = acc
        self.channel_log.append({"channel": name.upper(), "qubit": target,
                                 "kraus_rank": len(ops),
                                 "params": [p for p in params
                                            if isinstance(p, (int, float))]})

    # -- measurement --------------------------------------------------------
    def measure(self, target: str, basis: str = "Z") -> int:
        basis = basis.upper()
        if basis not in _BASIS_ROTATION:
            raise SimulationError(
                f"unsupported measurement basis {basis!r}; "
                f"admitted bases are {sorted(_BASIS_ROTATION)}")
        for g in _BASIS_ROTATION[basis]:
            self.apply_1q(g, target)

        w = self.wire(target)
        p1 = float(self._projector_weight(w, 1))
        p1 = min(max(p1, 0.0), 1.0)
        draw = float(self.rng.random())
        outcome = 1 if draw < p1 else 0
        prob = p1 if outcome == 1 else 1.0 - p1
        if prob <= PROBABILITY_FLOOR:
            raise SimulationError(
                f"measurement of {target!r} selected an outcome of probability "
                f"{prob:.3e}; numerical state is degenerate")
        proj = np.zeros((2, 2), dtype=complex)
        proj[outcome, outcome] = 1.0
        self.rho = _apply_operator_density(self.rho, proj, [w], self.n) / prob
        self.measurement_log.append(
            {"qubit": target, "basis": basis, "outcome": outcome,
             "p1": p1, "draw": draw})
        return outcome

    def _projector_weight(self, wire: int, value: int) -> float:
        tens = self.rho.reshape([2] * (2 * self.n))
        idx: List[object] = [slice(None)] * (2 * self.n)
        idx[wire] = value
        idx[self.n + wire] = value
        block = tens[tuple(idx)]
        m = 1 << (self.n - 1)
        return float(np.trace(np.ascontiguousarray(block).reshape(m, m)).real)

    def reset(self, target: str) -> None:
        """Trace-preserving reset: project onto both outcomes and fold the
        |1> branch back to |0>. Unlike the statevector engine this needs no
        random draw, because the mixture is representable."""
        w = self.wire(target)
        p0 = np.zeros((2, 2), dtype=complex)
        p0[0, 0] = 1.0
        fold = np.zeros((2, 2), dtype=complex)
        fold[0, 1] = 1.0
        self.rho = (_apply_operator_density(self.rho, p0, [w], self.n)
                    + _apply_operator_density(self.rho, fold, [w], self.n))

    # -- validation and observables ----------------------------------------
    def validate(self) -> dict:
        tr = complex(np.trace(self.rho))
        herm = float(np.max(np.abs(self.rho - self.rho.conjugate().T)))
        evals = np.linalg.eigvalsh((self.rho + self.rho.conjugate().T) / 2.0)
        minev = float(np.min(evals.real))
        return {
            "trace": complex(tr),
            "trace_real": float(tr.real),
            "trace_imag": float(tr.imag),
            "hermiticity_error": herm,
            "min_eigenvalue": minev,
            "tolerance": TRACE_TOL,
            "trace_ok": abs(tr - 1.0) <= TRACE_TOL,
            "hermitian_ok": herm <= TRACE_TOL,
            "psd_ok": minev >= -TRACE_TOL,
        }

    def assert_valid(self) -> None:
        v = self.validate()
        if not (v["trace_ok"] and v["hermitian_ok"] and v["psd_ok"]):
            raise SimulationError(
                f"density matrix invalid: trace={v['trace_real']:.12f} "
                f"hermiticity_error={v['hermiticity_error']:.3e} "
                f"min_eigenvalue={v['min_eigenvalue']:.3e} "
                f"(tolerance {TRACE_TOL:g})")

    def purity(self) -> float:
        return float(np.trace(self.rho @ self.rho).real)

    def von_neumann_entropy(self) -> float:
        """S(rho) = -tr(rho log2 rho), in bits."""
        evals = np.linalg.eigvalsh((self.rho + self.rho.conjugate().T) / 2.0).real
        acc = 0.0
        for lam in evals:
            if lam > PROBABILITY_FLOOR:
                acc -= lam * math.log2(lam)
        return float(acc)

    def probabilities(self) -> Dict[str, float]:
        diag = np.real(np.diag(self.rho))
        return {_bitstring(i, self.n): float(p)
                for i, p in enumerate(diag) if p > PROBABILITY_FLOOR}

    def to_density(self) -> np.ndarray:
        return self.rho.copy()

    def partial_trace(self, keep: List[str]) -> np.ndarray:
        for q in keep:
            self.wire(q)
        if len(set(keep)) != len(keep):
            raise SimulationError(f"duplicate qubit in partial_trace keep={keep}")
        kept = [self.wire(q) for q in keep]
        traced = [i for i in range(self.n) if i not in kept]
        perm = kept + traced + [self.n + i for i in kept] + \
            [self.n + i for i in traced]
        tens = self.rho.reshape([2] * (2 * self.n)).transpose(perm)
        k, m = len(kept), self.n - len(kept)
        blk = np.ascontiguousarray(tens).reshape(1 << k, 1 << m, 1 << k, 1 << m)
        return np.einsum("aibi->ab", blk)

    def state_hash(self) -> str:
        return _hash_array(self.rho)


# --------------------------------------------------------------------------
# 4. Stabilizer engine (LCTL 1.3.x s17)
# --------------------------------------------------------------------------

class StabilizerEngine:
    """CHP binary-symplectic tableau (Aaronson-Gottesman, quant-ph/0406196).

    Layout: rows 0..n-1 are destabilizers, rows n..2n-1 are stabilizers, row
    2n is the scratch row used by the deterministic-measurement path. Columns
    are [x_0..x_{n-1} | z_0..z_{n-1} | r], so the live tableau is
    2n x (2n+1) as required, with one extra scratch row.

    The (x, z) pair of a column encodes the Hermitian Pauli factor
    (0,0)=I, (1,0)=X, (1,1)=Y, (0,1)=Z, and r is the sign exponent.
    """

    backend_name = "stabilizer"

    CLIFFORD_1Q = ("H", "S", "SDG", "X", "Y", "Z", "I")
    CLIFFORD_2Q = ("CX", "CNOT", "CZ", "SWAP")

    def __init__(self, qubits: List[str], seed: int = 0) -> None:
        if not qubits:
            raise SimulationError("StabilizerEngine requires at least one qubit")
        if len(set(qubits)) != len(qubits):
            raise SimulationError(f"duplicate qubit name in {qubits}")
        self.qubits: List[str] = list(qubits)
        self.n: int = len(self.qubits)
        self.index: Dict[str, int] = {q: i for i, q in enumerate(self.qubits)}
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        n = self.n
        self.x = np.zeros((2 * n + 1, n), dtype=np.int8)
        self.z = np.zeros((2 * n + 1, n), dtype=np.int8)
        self.r = np.zeros(2 * n + 1, dtype=np.int8)
        for i in range(n):
            self.x[i, i] = 1              # destabilizers: X_i
            self.z[n + i, i] = 1          # stabilizers:   Z_i
        self.measurement_log: List[dict] = []

    # -- wire resolution ---------------------------------------------------
    def wire(self, target: str) -> int:
        if target not in self.index:
            raise SimulationError(f"unknown qubit {target!r}; "
                                  f"declared qubits are {self.qubits}")
        return self.index[target]

    @property
    def tableau(self) -> np.ndarray:
        """The live 2n x (2n+1) tableau (scratch row excluded)."""
        n = self.n
        return np.concatenate(
            [self.x[:2 * n], self.z[:2 * n], self.r[:2 * n, None]], axis=1)

    # -- Clifford gates -----------------------------------------------------
    def h(self, target: str) -> None:
        a = self.wire(target)
        self.r ^= (self.x[:, a] & self.z[:, a])
        self.x[:, a], self.z[:, a] = self.z[:, a].copy(), self.x[:, a].copy()

    def s(self, target: str) -> None:
        a = self.wire(target)
        self.r ^= (self.x[:, a] & self.z[:, a])
        self.z[:, a] ^= self.x[:, a]

    def sdg(self, target: str) -> None:
        # S^dag = Z S (up to a global phase the tableau does not track)
        self.s(target)
        self.zgate(target)

    def xgate(self, target: str) -> None:
        a = self.wire(target)
        self.r ^= self.z[:, a]

    def zgate(self, target: str) -> None:
        a = self.wire(target)
        self.r ^= self.x[:, a]

    def ygate(self, target: str) -> None:
        a = self.wire(target)
        self.r ^= (self.x[:, a] ^ self.z[:, a])

    def cx(self, control: str, target: str) -> None:
        a, b = self.wire(control), self.wire(target)
        if a == b:
            raise SimulationError("CX control and target must differ")
        self.r ^= (self.x[:, a] & self.z[:, b]
                   & (self.x[:, b] ^ self.z[:, a] ^ 1))
        self.x[:, b] ^= self.x[:, a]
        self.z[:, a] ^= self.z[:, b]

    def cz(self, a: str, b: str) -> None:
        self.h(b)
        self.cx(a, b)
        self.h(b)

    def swap(self, a: str, b: str) -> None:
        self.cx(a, b)
        self.cx(b, a)
        self.cx(a, b)

    def apply_1q(self, name: str, target: str,
                 params: Sequence[float] = ()) -> None:
        name = name.upper()
        if params:
            raise SimulationError(
                f"{name} with parameters is not a Clifford gate; the "
                f"stabilizer engine refuses it (LCTL 1.3.x s17)")
        if name == "I":
            return
        if name == "H":
            self.h(target)
        elif name == "S":
            self.s(target)
        elif name == "SDG":
            self.sdg(target)
        elif name == "X":
            self.xgate(target)
        elif name == "Y":
            self.ygate(target)
        elif name == "Z":
            self.zgate(target)
        else:
            raise SimulationError(
                f"{name!r} is outside the stabilizer engine's Clifford "
                f"catalog {self.CLIFFORD_1Q}")

    def apply_2q(self, name: str, a: str, b: str) -> None:
        name = name.upper()
        if name in ("CX", "CNOT"):
            self.cx(a, b)
        elif name == "CZ":
            self.cz(a, b)
        elif name == "SWAP":
            self.swap(a, b)
        else:
            raise SimulationError(
                f"{name!r} is outside the stabilizer engine's Clifford "
                f"catalog {self.CLIFFORD_2Q}")

    def apply_gate(self, name: str, targets: Sequence[str],
                   params: Sequence[float] = ()) -> None:
        if len(targets) == 1:
            self.apply_1q(name, targets[0], params)
        elif len(targets) == 2:
            self.apply_2q(name, targets[0], targets[1])
        else:
            raise SimulationError(
                f"{name!r} on {len(targets)} wires is not a supported "
                f"stabilizer operation")

    # -- CHP row algebra ----------------------------------------------------
    @staticmethod
    def _g(x1: int, z1: int, x2: int, z2: int) -> int:
        if x1 == 0 and z1 == 0:
            return 0
        if x1 == 1 and z1 == 1:
            return int(z2) - int(x2)
        if x1 == 1 and z1 == 0:
            return int(z2) * (2 * int(x2) - 1)
        return int(x2) * (1 - 2 * int(z2))

    def _rowsum(self, h: int, i: int) -> None:
        acc = 2 * int(self.r[h]) + 2 * int(self.r[i])
        for j in range(self.n):
            acc += self._g(int(self.x[i, j]), int(self.z[i, j]),
                           int(self.x[h, j]), int(self.z[h, j]))
        acc %= 4
        if acc == 0:
            self.r[h] = 0
        elif acc == 2:
            self.r[h] = 1
        else:
            raise SimulationError(
                f"CHP rowsum produced phase {acc}, which is impossible for a "
                f"well-formed tableau")
        self.x[h] ^= self.x[i]
        self.z[h] ^= self.z[i]

    # -- measurement --------------------------------------------------------
    def measure(self, target: str) -> Tuple[int, bool]:
        """Measure Z on `target`. Returns (outcome, deterministic_flag)."""
        a = self.wire(target)
        n = self.n
        p = -1
        for i in range(n, 2 * n):
            if self.x[i, a]:
                p = i
                break

        if p >= 0:
            for i in range(2 * n):
                if i != p and self.x[i, a]:
                    self._rowsum(i, p)
            self.x[p - n] = self.x[p].copy()
            self.z[p - n] = self.z[p].copy()
            self.r[p - n] = self.r[p]
            self.x[p] = 0
            self.z[p] = 0
            self.z[p, a] = 1
            outcome = int(self.rng.integers(0, 2))
            self.r[p] = outcome
            self.measurement_log.append(
                {"qubit": target, "outcome": outcome, "deterministic": False})
            return outcome, False

        # deterministic branch: accumulate into the scratch row
        self.x[2 * n] = 0
        self.z[2 * n] = 0
        self.r[2 * n] = 0
        for i in range(n):
            if self.x[i, a]:
                self._rowsum(2 * n, i + n)
        outcome = int(self.r[2 * n])
        self.measurement_log.append(
            {"qubit": target, "outcome": outcome, "deterministic": True})
        return outcome, True

    def peek_z(self, target: str) -> Optional[int]:
        """Deterministic Z outcome without disturbing the tableau, or None
        when the outcome is random."""
        a = self.wire(target)
        n = self.n
        for i in range(n, 2 * n):
            if self.x[i, a]:
                return None
        saved = (self.x[2 * n].copy(), self.z[2 * n].copy(), int(self.r[2 * n]))
        self.x[2 * n] = 0
        self.z[2 * n] = 0
        self.r[2 * n] = 0
        for i in range(n):
            if self.x[i, a]:
                self._rowsum(2 * n, i + n)
        out = int(self.r[2 * n])
        self.x[2 * n], self.z[2 * n], self.r[2 * n] = saved[0], saved[1], saved[2]
        return out

    def expectation_z(self, target: str) -> float:
        """<Z_target>. Exactly +/-1 when determined, exactly 0 otherwise."""
        out = self.peek_z(target)
        if out is None:
            return 0.0
        return 1.0 if out == 0 else -1.0

    def reset(self, target: str) -> None:
        out, _det = self.measure(target)
        if out == 1:
            self.xgate(target)

    # -- exact cross-check surface -----------------------------------------
    def row_pauli(self, row: int) -> np.ndarray:
        """Dense 2^n x 2^n Hermitian Pauli operator of tableau row `row`."""
        op = np.array([[1.0 + 0j]])
        for j in range(self.n):
            xj, zj = int(self.x[row, j]), int(self.z[row, j])
            factor = {(0, 0): _I2, (1, 0): _X, (1, 1): _Y, (0, 1): _Z}[(xj, zj)]
            op = np.kron(op, factor)
        return op * (-1.0 if self.r[row] else 1.0)

    def to_statevector(self, trial_seed: int = 20240101) -> np.ndarray:
        """Exact statevector of the stabilizer state, up to a global phase.

        Built by applying the stabilizer projectors (I + g_i)/2 to a fixed
        deterministic trial vector. Cost is O(n * 4^n); intended for
        verification at small n, not for production sampling.
        """
        dim = 1 << self.n
        if self.n > 14:
            raise SimulationError(
                f"to_statevector refuses n={self.n} (>14): the dense form "
                f"defeats the purpose of the stabilizer engine")
        gen = np.random.default_rng(trial_seed)
        vec = (gen.standard_normal(dim) + 1j * gen.standard_normal(dim))
        for i in range(self.n, 2 * self.n):
            g = self.row_pauli(i)
            vec = 0.5 * (vec + g @ vec)
        norm = float(np.linalg.norm(vec))
        if norm <= MAX_AMPLITUDE_ERROR_TOL:
            raise SimulationError(
                "stabilizer projection collapsed the trial vector; the "
                "tableau is not a valid stabilizer group")
        vec = vec / norm
        # Fix the global phase deterministically: first significant amplitude
        # is made real and positive.
        for amp in vec:
            if abs(amp) > 1e-12:
                vec = vec * (abs(amp) / amp)
                break
        return vec

    def probabilities(self) -> Dict[str, float]:
        psi = self.to_statevector()
        probs = np.abs(psi) ** 2
        return {_bitstring(i, self.n): float(p)
                for i, p in enumerate(probs) if p > PROBABILITY_FLOOR}

    def canonical(self) -> str:
        n = self.n
        rows = []
        for i in range(2 * n):
            rows.append("".join(str(int(v)) for v in self.x[i])
                        + "".join(str(int(v)) for v in self.z[i])
                        + str(int(self.r[i])))
        return "|".join(rows)

    def state_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 5. Metrics (LCTL 1.3.x s56 -- thresholds are module constants above)
# --------------------------------------------------------------------------

def max_amplitude_error(a, b) -> float:
    """max_i |a_i - b_i| over two amplitude vectors."""
    va = np.asarray(a, dtype=complex).ravel()
    vb = np.asarray(b, dtype=complex).ravel()
    if va.shape != vb.shape:
        raise SimulationError(
            f"amplitude vectors have shapes {va.shape} and {vb.shape}")
    return float(np.max(np.abs(va - vb))) if va.size else 0.0


def frobenius_error(a, b) -> float:
    """||A - B||_F over two matrices (or vectors)."""
    ma = np.asarray(a, dtype=complex)
    mb = np.asarray(b, dtype=complex)
    if ma.shape != mb.shape:
        raise SimulationError(f"shapes {ma.shape} and {mb.shape} differ")
    return float(np.linalg.norm(ma - mb))


def state_fidelity(a, b) -> float:
    """|<a|b>|^2 for pure states; tr(A B) is used when either side is a
    density matrix and the other is pure; the general mixed-mixed case uses
    the Uhlmann formula computed from eigen-decompositions."""
    ma = np.asarray(a, dtype=complex)
    mb = np.asarray(b, dtype=complex)
    if ma.ndim == 1 and mb.ndim == 1:
        return float(abs(np.vdot(ma, mb)) ** 2)
    if ma.ndim == 1:
        return float(np.real(np.vdot(ma, mb @ ma)))
    if mb.ndim == 1:
        return float(np.real(np.vdot(mb, ma @ mb)))
    evals, evecs = np.linalg.eigh((ma + ma.conjugate().T) / 2.0)
    evals = np.clip(evals.real, 0.0, None)
    root = evecs @ np.diag(np.sqrt(evals)) @ evecs.conjugate().T
    inner = root @ mb @ root
    iev = np.linalg.eigvalsh((inner + inner.conjugate().T) / 2.0).real
    iev = np.clip(iev, 0.0, None)
    return float(np.sum(np.sqrt(iev)) ** 2)


def trace_distance(rho, sigma) -> float:
    """(1/2) ||rho - sigma||_1."""
    d = np.asarray(rho, dtype=complex) - np.asarray(sigma, dtype=complex)
    if d.ndim != 2 or d.shape[0] != d.shape[1]:
        raise SimulationError("trace_distance requires square matrices")
    herm = (d + d.conjugate().T) / 2.0
    skew = float(np.max(np.abs(d - herm)))
    if skew > 1e-9:
        # Not Hermitian: use singular values, which is the correct 1-norm.
        sv = np.linalg.svd(d, compute_uv=False)
        return float(0.5 * np.sum(sv))
    ev = np.linalg.eigvalsh(herm).real
    return float(0.5 * np.sum(np.abs(ev)))


def total_variation(p: Dict[str, float], q: Dict[str, float]) -> float:
    """(1/2) sum_k |p_k - q_k| over the union of the two supports."""
    keys = set(p) | set(q)
    return float(0.5 * sum(abs(float(p.get(k, 0.0)) - float(q.get(k, 0.0)))
                           for k in keys))


# --------------------------------------------------------------------------
# 6. Numerical planner (LCTL 1.3.x s14, 1.4.x s34, 1.5.x s37)
# --------------------------------------------------------------------------

BACKENDS = (
    "local_statevector", "distributed_statevector",
    "local_density", "distributed_density",
    "stabilizer", "tensor_network",
    "shot_farm", "circuit_farm", "trajectory_farm",
)

DEFAULT_MEMORY_BUDGET = 512 * 1024 * 1024
LOCAL_STATEVECTOR_MAX_QUBITS = 20
COMPLEX128_BYTES = 16

# Farm thresholds. Stated explicitly because LCTL 1.5.x s37 requires the
# planner to be reproducible and reviewable, not tuned at runtime.
CIRCUIT_FARM_MIN = 8
SHOT_FARM_MIN = 1024
TRAJECTORY_FARM_MIN = 64


class NumericalPlanner:
    """Rule-based, fully explainable numerical backend selection.

    `plan` consumes a circuit summary dictionary and returns
    {"backend", "reasons", "rejected", "memory_estimate_bytes"}. Every backend
    that was not chosen appears in `rejected` with a stated reason: the
    planner never returns an unexplained choice, and it never invents a
    backend outside BACKENDS.

    Recognized summary keys (all optional except `qubits`):
        qubits                int or list[str]
        all_clifford          bool
        gate_names            iterable[str]        (used if all_clifford absent)
        has_channels          bool
        channel_count         int
        t_count               int
        shots                 int
        independent_circuits  int
        trajectories          int
        classical_feedback    bool
        max_bond_dimension    int or None          (tensor-network hint)
    """

    def __init__(self, memory_budget: int = DEFAULT_MEMORY_BUDGET,
                 local_statevector_max_qubits: int = LOCAL_STATEVECTOR_MAX_QUBITS
                 ) -> None:
        if memory_budget <= 0:
            raise BackendPlanError("memory_budget must be positive")
        self.memory_budget = int(memory_budget)
        self.local_max_qubits = int(local_statevector_max_qubits)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _qubit_count(summary: dict) -> int:
        q = summary.get("qubits")
        if q is None:
            raise BackendPlanError("circuit summary is missing `qubits`")
        if isinstance(q, int):
            n = q
        else:
            n = len(list(q))
        if n < 0:
            raise BackendPlanError(f"invalid qubit count {n}")
        return n

    @staticmethod
    def _is_all_clifford(summary: dict) -> bool:
        if "all_clifford" in summary:
            return bool(summary["all_clifford"])
        names = summary.get("gate_names")
        if names is None:
            return False
        allowed = set(StabilizerEngine.CLIFFORD_1Q) | set(StabilizerEngine.CLIFFORD_2Q)
        return all(str(g).upper() in allowed for g in names)

    def statevector_bytes(self, n: int) -> int:
        return (1 << n) * COMPLEX128_BYTES

    def density_bytes(self, n: int) -> int:
        return (1 << (2 * n)) * COMPLEX128_BYTES

    # -- the rule cascade --------------------------------------------------
    def plan(self, circuit_summary: dict) -> dict:
        return _planner_plan(self, circuit_summary)


def _planner_plan(self: "NumericalPlanner", circuit_summary: dict) -> dict:
    """The rule cascade behind NumericalPlanner.plan, kept at module scope so
    that it reads top-to-bottom without class-body indentation."""
    s = dict(circuit_summary)
    n = NumericalPlanner._qubit_count(s)
    clifford = NumericalPlanner._is_all_clifford(s)
    channels = bool(s.get("has_channels", int(s.get("channel_count", 0)) > 0))
    shots = int(s.get("shots", 0) or 0)
    circuits = int(s.get("independent_circuits", 1) or 1)
    trajectories = int(s.get("trajectories", 0) or 0)
    feedback = bool(s.get("classical_feedback", False))
    bond = s.get("max_bond_dimension")
    t_count = int(s.get("t_count", 0) or 0)

    sv_bytes = self.statevector_bytes(n)
    dm_bytes = self.density_bytes(n)
    tab_bytes = (2 * n + 1) * (2 * n + 1)

    reasons: List[str] = [
        f"qubits={n}", f"all_clifford={clifford}", f"noise_channels={channels}",
        f"t_count={t_count}", f"shots={shots}", f"independent_circuits={circuits}",
        f"trajectories={trajectories}", f"classical_feedback={feedback}",
        f"memory_budget={self.memory_budget}",
        f"statevector_bytes={sv_bytes}", f"density_bytes={dm_bytes}",
    ]
    rejected: Dict[str, str] = {}

    def reject(name: str, why: str) -> None:
        rejected.setdefault(name, why)

    # Rule 1 -- all-Clifford and no channels: exact polynomial tableau.
    if clifford and not channels:
        backend = "stabilizer"
        reasons.append("RULE-1: all operations are Clifford and no noise "
                       "channel is declared -> exact stabilizer tableau")
        mem = tab_bytes
        for b in BACKENDS:
            if b != backend:
                reject(b, "stabilizer tableau is exact and asymptotically "
                          "cheaper for an all-Clifford channel-free circuit")
        return {"backend": backend, "reasons": reasons, "rejected": rejected,
                "memory_estimate_bytes": int(mem)}
    if not clifford:
        reject("stabilizer", f"circuit contains non-Clifford operations "
                             f"(t_count={t_count})")
    else:
        reject("stabilizer", "noise channels are declared; the stabilizer "
                             "tableau represents no mixed state")

    # Rule 2 -- noise channels present: density matrix (or trajectory farm).
    if channels:
        if trajectories >= TRAJECTORY_FARM_MIN:
            reasons.append(
                f"RULE-2a: {trajectories} independent noise trajectories "
                f">= {TRAJECTORY_FARM_MIN} -> trajectory farm over pure-state "
                f"unravellings")
            for b in ("local_statevector", "distributed_statevector",
                      "local_density", "distributed_density", "tensor_network",
                      "shot_farm", "circuit_farm"):
                reject(b, "a trajectory ensemble is the declared workload; "
                          "a single-instance backend would serialize it")
            return {"backend": "trajectory_farm", "reasons": reasons,
                    "rejected": rejected,
                    "memory_estimate_bytes": int(sv_bytes)}
        if dm_bytes > self.memory_budget:
            backend = "distributed_density"
            reasons.append(
                f"RULE-2b: noise channels require a density matrix and "
                f"2^(2n)*{COMPLEX128_BYTES} = {dm_bytes} bytes exceeds the "
                f"{self.memory_budget}-byte budget -> shard the density matrix")
            reject("local_density", f"{dm_bytes} bytes exceeds the "
                                    f"{self.memory_budget}-byte budget")
        else:
            backend = "local_density"
            reasons.append(
                f"RULE-2b: noise channels require a density matrix and "
                f"{dm_bytes} bytes fits the {self.memory_budget}-byte budget")
            reject("distributed_density",
                   f"{dm_bytes} bytes fits in one memory domain; sharding "
                   f"would add communication with no capacity benefit")
        for b in ("local_statevector", "distributed_statevector",
                  "tensor_network", "shot_farm", "circuit_farm",
                  "trajectory_farm"):
            if b == "trajectory_farm":
                reject(b, f"only {trajectories} trajectories declared "
                          f"(< {TRAJECTORY_FARM_MIN})")
            elif b in ("local_statevector", "distributed_statevector"):
                reject(b, "a statevector cannot represent the mixed state "
                          "produced by the declared channels")
            else:
                reject(b, "declared noise channels require an explicit "
                          "density-matrix representation")
        return {"backend": backend, "reasons": reasons, "rejected": rejected,
                "memory_estimate_bytes": int(dm_bytes)}

    reject("local_density", "no noise channel is declared; a density matrix "
                            "would square the memory cost for no benefit")
    reject("distributed_density", "no noise channel is declared")
    reject("trajectory_farm", "no noise channel is declared; there is nothing "
                              "to unravel into trajectories")

    # Rule 3 -- many independent circuits: circuit farm.
    if circuits >= CIRCUIT_FARM_MIN:
        reasons.append(
            f"RULE-3: {circuits} independent circuits >= {CIRCUIT_FARM_MIN} "
            f"-> circuit farm (CIRCUIT_PARALLEL family)")
        for b in ("local_statevector", "distributed_statevector",
                  "tensor_network", "shot_farm"):
            reject(b, "the workload is an independent-circuit ensemble; a "
                      "single-instance backend would serialize it")
        return {"backend": "circuit_farm", "reasons": reasons,
                "rejected": rejected,
                "memory_estimate_bytes": int(sv_bytes * min(circuits, 64))}
    reject("circuit_farm", f"only {circuits} independent circuit(s) "
                           f"(< {CIRCUIT_FARM_MIN})")

    # Rule 4 -- many independent shots with no mid-circuit feedback.
    if shots >= SHOT_FARM_MIN and not feedback:
        reasons.append(
            f"RULE-4: {shots} shots >= {SHOT_FARM_MIN} with no classical "
            f"feedback -> shot farm (SHOT_PARALLEL family)")
        for b in ("local_statevector", "distributed_statevector",
                  "tensor_network"):
            reject(b, "the workload is an independent-shot ensemble; a "
                      "single-instance backend would serialize sampling")
        return {"backend": "shot_farm", "reasons": reasons,
                "rejected": rejected,
                "memory_estimate_bytes": int(sv_bytes)}
    if shots < SHOT_FARM_MIN:
        reject("shot_farm", f"only {shots} shot(s) (< {SHOT_FARM_MIN})")
    else:
        reject("shot_farm", "mid-circuit classical feedback makes shots "
                            "non-independent")

    # Rule 5 -- declared low bond dimension beyond the dense statevector limit.
    if n > self.local_max_qubits and bond is not None and int(bond) > 0:
        b_int = int(bond)
        tn_bytes = n * b_int * b_int * 2 * COMPLEX128_BYTES
        if tn_bytes <= self.memory_budget:
            reasons.append(
                f"RULE-5: n={n} exceeds the dense limit {self.local_max_qubits} "
                f"and a bond dimension of {b_int} is declared "
                f"({tn_bytes} bytes) -> tensor network")
            for b in ("local_statevector", "distributed_statevector"):
                reject(b, f"dense amplitudes need {sv_bytes} bytes; the "
                          f"declared bond dimension {b_int} is far cheaper")
            return {"backend": "tensor_network", "reasons": reasons,
                    "rejected": rejected, "memory_estimate_bytes": int(tn_bytes)}
        reject("tensor_network",
               f"declared bond dimension {b_int} needs {tn_bytes} bytes, "
               f"over the {self.memory_budget}-byte budget")
    else:
        reject("tensor_network",
               "no max_bond_dimension declared, so no entanglement bound can "
               "be assumed; a dense representation is the honest choice"
               if bond is None else
               f"n={n} is within the dense limit {self.local_max_qubits}")

    # Rule 6 / 7 -- dense statevector, local or sharded.
    if n <= self.local_max_qubits and sv_bytes <= self.memory_budget:
        reasons.append(
            f"RULE-6: n={n} <= {self.local_max_qubits} and {sv_bytes} bytes "
            f"fits the {self.memory_budget}-byte budget -> local statevector")
        reject("distributed_statevector",
               f"{sv_bytes} bytes fits in one memory domain; sharding would "
               f"add communication with no capacity benefit")
        return {"backend": "local_statevector", "reasons": reasons,
                "rejected": rejected, "memory_estimate_bytes": int(sv_bytes)}

    reasons.append(
        f"RULE-7: n={n} exceeds the local limit {self.local_max_qubits} or "
        f"{sv_bytes} bytes exceeds the {self.memory_budget}-byte budget "
        f"-> sharded statevector")
    reject("local_statevector",
           f"n={n} > {self.local_max_qubits} or {sv_bytes} bytes > "
           f"{self.memory_budget}-byte budget")
    return {"backend": "distributed_statevector", "reasons": reasons,
            "rejected": rejected, "memory_estimate_bytes": int(sv_bytes)}



# --------------------------------------------------------------------------
# 7. Circuit extraction from an LCTL program
# --------------------------------------------------------------------------

OP_KINDS = ("gate", "measure", "channel", "prepare", "reset", "protocol")

# Preparation ops mapped to the gate sequence that builds them from |0>.
PREPARE_SEQUENCE = {
    "PREP0": (),
    "PREP1": ("X",),
    "PREP_PLUS": ("H",),
    "PREP_MINUS": ("X", "H"),
}


@dataclass
class Circuit:
    """An ordered, engine-agnostic operation list extracted from a Program."""
    qubits: List[str]
    operations: List[dict]
    classical_bits: List[str]
    program_seal: str = ""
    source_path: str = "<memory>"

    def __len__(self) -> int:
        return len(self.operations)

    @property
    def n_qubits(self) -> int:
        return len(self.qubits)

    def gate_names(self) -> List[str]:
        return [o["op"] for o in self.operations if o["kind"] == "gate"]

    def resource(self) -> dict:
        """Deterministic resource report (LCTL 1.2.x s8 accounting units)."""
        clifford_1q = set(StabilizerEngine.CLIFFORD_1Q)
        clifford_2q = set(StabilizerEngine.CLIFFORD_2Q)
        gate_count = two_q = t_count = clifford = meas = 0
        frontier: Dict[str, int] = {q: 0 for q in self.qubits}
        for op in self.operations:
            targets = [t for t in op["targets"] if t in frontier]
            if op["kind"] == "measure":
                meas += 1
            if op["kind"] == "gate":
                gate_count += 1
                if len(op["targets"]) >= 2:
                    two_q += 1
                if op["op"] in ("T", "TDG"):
                    t_count += 1
                if (op["op"] in clifford_1q and len(op["targets"]) == 1) or \
                        (op["op"] in clifford_2q and len(op["targets"]) == 2):
                    clifford += 1
            if targets:
                layer = max(frontier[t] for t in targets) + 1
                for t in targets:
                    frontier[t] = layer
        depth = max(frontier.values()) if frontier else 0
        return {"gate_count": gate_count, "two_qubit_gate_count": two_q,
                "t_count": t_count, "clifford_count": clifford,
                "measurement_count": meas, "depth": int(depth)}

    def summary(self, shots: int = 0, independent_circuits: int = 1,
                trajectories: int = 0) -> dict:
        res = self.resource()
        clifford_ok = all(
            (o["op"] in set(StabilizerEngine.CLIFFORD_1Q) and len(o["targets"]) == 1)
            or (o["op"] in set(StabilizerEngine.CLIFFORD_2Q) and len(o["targets"]) == 2)
            for o in self.operations if o["kind"] == "gate")
        channels = any(o["kind"] == "channel" for o in self.operations)
        feedback = any(o["kind"] == "protocol" for o in self.operations)
        return {
            "qubits": self.n_qubits,
            "all_clifford": clifford_ok,
            "gate_names": self.gate_names(),
            "has_channels": channels,
            "channel_count": sum(1 for o in self.operations
                                 if o["kind"] == "channel"),
            "t_count": res["t_count"],
            "shots": shots,
            "independent_circuits": independent_circuits,
            "trajectories": trajectories,
            "classical_feedback": feedback,
            "max_bond_dimension": None,
        }

    def as_dict(self) -> dict:
        return {"schema": "PA-LCTL/CIRCUIT/1", "qubits": list(self.qubits),
                "classical_bits": list(self.classical_bits),
                "program_seal": self.program_seal,
                "source_path": self.source_path,
                "operations": self.operations,
                "resource": self.resource()}


def _row_targets(row: lang.Row) -> List[str]:
    """Operand qubit keys in canonical order: CTRL, A, B."""
    keys: List[str] = []
    for cell in (row.ctrl, row.a, row.b):
        ref = lang.parse_ref(cell)
        if ref is None:
            continue
        for k in ref.keys():
            if k not in keys:
                keys.append(k)
    return keys


def circuit_from_program(prog: lang.Program, vr=None) -> Circuit:
    """Walk executable-FACE rows of `prog` and produce an ordered Circuit.

    Rows whose FACE is not in lang.EXECUTABLE_FACES are analytic or
    declarative and contribute no operation. Rows whose OP is executable but
    whose operands cannot be resolved raise SimulationError: the extractor
    never silently drops an executable row.
    """
    qubits: List[str] = []
    cbits: List[str] = []
    ops: List[dict] = []

    def note_qubits(keys: Sequence[str]) -> None:
        for k in keys:
            if k not in qubits:
                qubits.append(k)

    for row in prog.rows:
        if row.face not in lang.EXECUTABLE_FACES:
            continue
        op = row.op
        targets = _row_targets(row)
        params = list(row.params)
        basis = row.basis if row.basis != NULL_CELL else "Z"

        if op in lang.GATE_ARITY:
            arity = lang.GATE_ARITY[op]
            if len(targets) != arity:
                raise SimulationError(
                    f"row {row.row_id}: {op} needs {arity} qubit operand(s), "
                    f"found {targets}")
            if op in lang.PARAMETRIC_GATES and not params:
                raise SimulationError(
                    f"row {row.row_id}: {op} requires a PARAM angle")
            note_qubits(targets)
            ops.append({"row": row.row_id, "op": op, "targets": targets,
                        "params": params, "basis": NULL_CELL, "kind": "gate"})
            continue

        if op in lang.DESTRUCTIVE_OPS or op in ("SAMPLE", "EXPECT", "VARIANCE"):
            if op == "MEASURE_X":
                basis = "X"
            elif op == "MEASURE_Y":
                basis = "Y"
            elif op in ("MEASURE_Z",):
                basis = "Z"
            if not targets:
                raise SimulationError(
                    f"row {row.row_id}: {op} has no qubit operand")
            note_qubits(targets)
            if row.out != NULL_CELL and row.out not in cbits:
                cbits.append(row.out)
            kind = "measure" if op in lang.DESTRUCTIVE_OPS else "protocol"
            ops.append({"row": row.row_id, "op": op, "targets": targets,
                        "params": params, "basis": basis.upper(),
                        "kind": kind, "out": row.out})
            continue

        if op in lang.OPS_NOISE:
            if not targets:
                raise SimulationError(
                    f"row {row.row_id}: channel {op} has no qubit operand")
            note_qubits(targets)
            ops.append({"row": row.row_id, "op": op, "targets": targets,
                        "params": params, "basis": NULL_CELL, "kind": "channel"})
            continue

        if op in lang.OPS_PREPARE:
            dst = lang.parse_ref(row.out)
            keys = list(dst.keys()) if dst is not None else targets
            if not keys:
                raise SimulationError(
                    f"row {row.row_id}: {op} has no destination qubit")
            note_qubits(keys)
            ops.append({"row": row.row_id, "op": op, "targets": keys,
                        "params": params,
                        "basis": basis.upper() if op == "PREP_BASIS" else NULL_CELL,
                        "kind": "reset" if op == "RESET" else "prepare"})
            continue

        # Pure classical control flow. It has no quantum action of its own;
        # it gates later rows and is recorded so the trace stays complete.
        if op in ("CLASSICAL_IF", "CLASSICAL_SWITCH", "REPEAT", "UNTIL",
                  "WHILE", "SHOT_LOOP", "PARAM_BIND") \
                and op not in lang.OPS_DISTRIBUTED_Q:
            cond = row.ctrl if row.ctrl != NULL_CELL else row.a
            ops.append({"row": row.row_id, "op": op, "targets": [],
                        "params": params, "basis": NULL_CELL,
                        "kind": "control", "condition": cond,
                        "out": row.out})
            continue

        if op in lang.OPS_DISTRIBUTED_Q or row.face in ("PROTOCOL", "COMM",
                                                        "CONTROL"):
            note_qubits(targets)
            if row.out != NULL_CELL and row.out not in cbits \
                    and row.face in ("COMM", "CONTROL"):
                cbits.append(row.out)
            ops.append({"row": row.row_id, "op": op, "targets": targets,
                        "params": params, "basis": NULL_CELL,
                        "kind": "protocol", "node": row.node, "link": row.link,
                        "out": row.out})
            continue

        # Executable face, catalogued op, but not simulable by these engines.
        raise SimulationError(
            f"row {row.row_id}: operation {op!r} on FACE {row.face} has no "
            f"admitted numerical realization in this module")

    return Circuit(qubits=qubits, operations=ops, classical_bits=cbits,
                   program_seal=prog.seal(), source_path=prog.path)


# --------------------------------------------------------------------------
# 8. Runners (LCTL 1.3.x s19.3 truthful labelling)
# --------------------------------------------------------------------------

DISTRIBUTED_LABEL = "CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION"
LOCAL_STATEVECTOR_LABEL = "CLASSICAL_LOCAL_STATEVECTOR_SIMULATION"
LOCAL_DENSITY_LABEL = "CLASSICAL_LOCAL_DENSITY_MATRIX_SIMULATION"
STABILIZER_LABEL = "CLASSICAL_STABILIZER_TABLEAU_SIMULATION"

# Every label this module may emit. None of them implies physical quantum
# execution; the conformance layer asserts against this tuple.
EXECUTION_LABELS = (DISTRIBUTED_LABEL, LOCAL_STATEVECTOR_LABEL,
                    LOCAL_DENSITY_LABEL, STABILIZER_LABEL)

_FORBIDDEN_LABEL_TOKENS = ("QPU", "HARDWARE", "PHYSICAL", "DEVICE", "QUANTUM_EXECUTION")


def execution_label(backend: str) -> str:
    """Truthful execution label for a backend (LCTL 1.3.x s19.3)."""
    if backend in ("distributed_statevector", "distributed_density",
                   "shot_farm", "circuit_farm", "trajectory_farm"):
        return DISTRIBUTED_LABEL
    if backend in ("local_density",):
        return LOCAL_DENSITY_LABEL
    if backend == "stabilizer":
        return STABILIZER_LABEL
    return LOCAL_STATEVECTOR_LABEL


def _assert_label_honest(label: str) -> None:
    """LCTL 1.3.x s19.3: a result label may never imply physical execution."""
    if label not in EXECUTION_LABELS:
        raise SimulationError(f"execution label {label!r} is not admitted")
    up = label.upper()
    if not up.startswith("CLASSICAL_"):
        raise SimulationError(
            f"execution label {label!r} does not declare itself classical")
    for tok in _FORBIDDEN_LABEL_TOKENS:
        if tok in up:
            raise SimulationError(
                f"execution label {label!r} contains {tok!r} and implies "
                f"physical quantum execution")


def _counts_from_probabilities(probs: Dict[str, float], shots: int,
                               rng: np.random.Generator) -> Dict[str, int]:
    keys = sorted(probs)
    if not keys:
        return {}
    weights = np.array([probs[k] for k in keys], dtype=float)
    total = float(weights.sum())
    if total <= 0.0:
        raise SimulationError("cannot sample from a zero-weight distribution")
    weights = weights / total
    draws = rng.choice(len(keys), size=shots, p=weights)
    counts: Dict[str, int] = {}
    for d in draws:
        counts[keys[int(d)]] = counts.get(keys[int(d)], 0) + 1
    return dict(sorted(counts.items()))


def _result_skeleton(backend: str, circuit: Circuit, distributed: bool) -> dict:
    label = DISTRIBUTED_LABEL if distributed else execution_label(backend)
    _assert_label_honest(label)
    return {"backend": backend, "qubits": list(circuit.qubits),
            "label": label, "resource": circuit.resource()}


def run_statevector(circuit: Circuit, shots: int = 0, seed: int = 0,
                    distributed: bool = False) -> dict:
    """Execute a Circuit on the exact statevector engine."""
    if not circuit.qubits:
        raise SimulationError("circuit declares no qubits")
    eng = StatevectorEngine(circuit.qubits, seed=seed)
    measurements: List[dict] = []
    for op in circuit.operations:
        _dispatch(eng, op, measurements, allow_channels=False)
    probs = eng.probabilities()
    out = _result_skeleton("distributed_statevector" if distributed
                           else "local_statevector", circuit, distributed)
    out["final_state_hash"] = eng.state_hash()
    out["probabilities"] = probs
    out["measurements"] = measurements
    if shots > 0:
        out["counts"] = _counts_from_probabilities(
            probs, int(shots), np.random.default_rng(seed + 1))
    return out


def run_density(circuit: Circuit, shots: int = 0, seed: int = 0,
                distributed: bool = False) -> dict:
    """Execute a Circuit on the exact density-matrix engine."""
    if not circuit.qubits:
        raise SimulationError("circuit declares no qubits")
    eng = DensityEngine(circuit.qubits, seed=seed)
    measurements: List[dict] = []
    for op in circuit.operations:
        _dispatch(eng, op, measurements, allow_channels=True)
    eng.assert_valid()
    probs = eng.probabilities()
    out = _result_skeleton("distributed_density" if distributed
                           else "local_density", circuit, distributed)
    out["final_state_hash"] = eng.state_hash()
    out["probabilities"] = probs
    out["measurements"] = measurements
    out["validation"] = {k: v for k, v in eng.validate().items() if k != "trace"}
    out["purity"] = eng.purity()
    out["von_neumann_entropy_bits"] = eng.von_neumann_entropy()
    if shots > 0:
        out["counts"] = _counts_from_probabilities(
            probs, int(shots), np.random.default_rng(seed + 1))
    return out


def run_stabilizer(circuit: Circuit, shots: int = 0, seed: int = 0,
                   distributed: bool = False) -> dict:
    """Execute an all-Clifford Circuit on the CHP tableau engine."""
    if not circuit.qubits:
        raise SimulationError("circuit declares no qubits")
    eng = StabilizerEngine(circuit.qubits, seed=seed)
    measurements: List[dict] = []
    for op in circuit.operations:
        kind = op["kind"]
        if kind == "gate":
            eng.apply_gate(op["op"], op["targets"], op["params"])
        elif kind == "measure":
            if op.get("basis", "Z") not in ("Z", NULL_CELL):
                raise SimulationError(
                    f"row {op['row']}: the stabilizer engine measures Z only; "
                    f"rotate explicitly with H / SDG for other bases")
            for t in op["targets"]:
                val, det = eng.measure(t)
                measurements.append({"row": op["row"], "qubit": t,
                                     "basis": "Z", "outcome": val,
                                     "deterministic": det,
                                     "out": op.get("out", NULL_CELL)})
        elif kind == "reset":
            for t in op["targets"]:
                eng.reset(t)
        elif kind == "prepare":
            seq = PREPARE_SEQUENCE.get(op["op"])
            if seq is None:
                raise SimulationError(
                    f"row {op['row']}: {op['op']} is not a Clifford preparation")
            for t in op["targets"]:
                eng.reset(t)
                for g in seq:
                    eng.apply_1q(g, t)
        elif kind == "channel":
            raise SimulationError(
                f"row {op['row']}: the stabilizer engine has no mixed-state "
                f"representation for channel {op['op']}")
        elif kind == "protocol":
            raise SimulationError(
                f"row {op['row']}: protocol operation {op['op']} must be "
                f"compiled by pacore.protocols before numerical execution")
        else:
            raise SimulationError(f"row {op['row']}: unknown operation kind {kind!r}")

    out = _result_skeleton("stabilizer", circuit, distributed)
    out["final_state_hash"] = eng.state_hash()
    out["probabilities"] = (eng.probabilities() if eng.n <= 14 else {})
    out["measurements"] = measurements
    out["tableau_canonical"] = eng.canonical()
    if shots > 0:
        out["counts"] = _counts_from_probabilities(
            out["probabilities"], int(shots), np.random.default_rng(seed + 1))
    return out


def _dispatch(eng, op: dict, measurements: List[dict],
              allow_channels: bool) -> None:
    """Apply one extracted operation to a statevector/density engine."""
    kind = op["kind"]
    if kind == "gate":
        eng.apply_gate(op["op"], op["targets"], op["params"])
    elif kind == "measure":
        basis = op.get("basis") or "Z"
        if basis == NULL_CELL:
            basis = "Z"
        for t in op["targets"]:
            val = eng.measure(t, basis)
            measurements.append({"row": op["row"], "qubit": t, "basis": basis,
                                 "outcome": val, "out": op.get("out", NULL_CELL)})
    elif kind == "channel":
        if not allow_channels:
            raise SimulationError(
                f"row {op['row']}: channel {op['op']} requires the density "
                f"engine; a statevector cannot represent the resulting mixture")
        for t in op["targets"]:
            eng.apply_channel(op["op"], t, op["params"])
    elif kind == "reset":
        for t in op["targets"]:
            eng.reset(t)
    elif kind == "prepare":
        seq = PREPARE_SEQUENCE.get(op["op"])
        if seq is None:
            raise SimulationError(
                f"row {op['row']}: preparation {op['op']} has no admitted "
                f"deterministic realization in this module")
        for t in op["targets"]:
            eng.reset(t)
            for g in seq:
                eng.apply_1q(g, t)
    elif kind == "control":
        # Classical control is recorded, not simulated as a quantum action.
        # Its causal effect is already carried by the SES measurement and
        # classical-control edges; executing it here would double-count.
        cond = op.get("condition")
        bound = {m.get("out") for m in measurements}
        if cond and cond != NULL_CELL and cond not in bound:
            raise SimulationError(
                f"row {op['row']}: classical control depends on {cond!r} which "
                f"no prior measurement produced")
    elif kind == "protocol":
        raise SimulationError(
            f"row {op['row']}: protocol operation {op['op']} must be compiled "
            f"by pacore.protocols before numerical execution")
    else:
        raise SimulationError(f"row {op['row']}: unknown operation kind {kind!r}")


def run(circuit: Circuit, planner: Optional[NumericalPlanner] = None,
        shots: int = 0, seed: int = 0) -> dict:
    """Plan a backend for `circuit` and run it, recording the plan."""
    planner = planner or NumericalPlanner()
    plan = planner.plan(circuit.summary(shots=shots))
    backend = plan["backend"]
    if backend in ("stabilizer",):
        res = run_stabilizer(circuit, shots=shots, seed=seed)
    elif backend in ("local_density", "distributed_density", "trajectory_farm"):
        res = run_density(circuit, shots=shots, seed=seed,
                          distributed=backend != "local_density")
    else:
        res = run_statevector(circuit, shots=shots, seed=seed,
                              distributed=backend != "local_statevector")
    res["plan"] = plan
    return res
