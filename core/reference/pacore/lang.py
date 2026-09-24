"""
PA-LCTL language layer: enumerations, type system, columned tuple grammar,
deterministic parser, ownership/no-cloning analysis.

Implements:
  * LCTL 1.1.x s4.3  canonical columned tuple schema
  * LCTL 1.1.x s5    semantic type system
  * LCTL 1.1.x s6    quantum ownership / no-cloning type rules
  * LCTL 1.1.x s7    normative operation catalog
  * LCTL 1.2.x s2-3  parallel / distributed state ladders and exactness regimes
  * LCTL 1.2.x s4-5  parallel and distributed semantic kernel objects
  * LCTL 1.5.x s4-5  federation objects and the 17-value parallel family model

SPECIFICATION HOLES FILLED HERE (recorded in the gap ledger):
  H1. The 1.2.x-1.6.x documents never restate the tuple schema and never
      enumerate FACE values, while 1.3.x s6 requires a `semantic_face` field
      on every SES node. This module defines the canonical FACE enumeration
      and the 1.2+ distributed column extension, both deterministically.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# 0. Canonical separators and normalization
# --------------------------------------------------------------------------

CANONICAL_SEP = "¦"          # BROKEN BAR, as written in LCTL 1.1.x s4.3
ALIAS_SEPS = ("|",)               # accepted on input, normalized on output
NULL_CELL = "-"                   # explicit "no value"; empty cell is an error


def normalize_cell(raw: str) -> str:
    """Deterministic cell normalization (LCTL 1.1.x PHASE 3 action 4)."""
    s = unicodedata.normalize("NFC", raw)
    s = s.replace("\t", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s if s else NULL_CELL


# --------------------------------------------------------------------------
# 1. FACE enumeration  (specification hole H1)
# --------------------------------------------------------------------------

FACES = (
    "EXEC",       # executable quantum or classical operation
    "PREPARE",    # state preparation / reset
    "MEASURE",    # measurement, sampling, expectation
    "NOISE",      # channel / noise / decoherence model row
    "CONTROL",    # classical control flow, feedback, binding
    "COMM",       # classical communication
    "PROTOCOL",   # distributed quantum protocol step
    "REGION",     # parallel / distributed semantic region marker
    "TOPOLOGY",   # topology or hardware-model declaration
    "FEDERATION", # federation / execution-domain declaration
    "MODEL",      # non-executable analytic or model-only statement
    "ASSERT",     # verifiable claim checked by the verifier
    "EVIDENCE",   # proof / provenance record reference
    "LEARN",      # learned rule awaiting Q1-Q6 admission
    "RESOURCE",   # resource claim / budget declaration
    "RECOVERY",   # failure / recovery policy row
    "LEDGER",     # ledger emission directive
)
FACE_SET = frozenset(FACES)

# Faces whose rows are physically executed by a runtime fabric.
EXECUTABLE_FACES = frozenset({"EXEC", "PREPARE", "MEASURE", "NOISE",
                              "CONTROL", "COMM", "PROTOCOL"})

# --------------------------------------------------------------------------
# 2. Column schema  (LCTL 1.1.x s4.3 core; 1.2+ distributed extension)
# --------------------------------------------------------------------------

CORE_COLUMNS = (
    "ROW", "FACE", "LANE", "QSPACE", "OP", "OUT", "CTRL", "A", "B", "PARAM",
    "TYPE", "BASIS", "REGIME", "ASSUME", "ERROR", "RESOURCE", "CONF", "PROOF",
)

# The 1.2+ profile adds exactly four columns. They are additive and optional:
# a 1.1 core program parses unchanged and receives NULL_CELL for each.
DISTRIBUTED_COLUMNS = ("DOMAIN", "NODE", "LINK", "FAMILY")

FULL_COLUMNS = CORE_COLUMNS + DISTRIBUTED_COLUMNS

COLUMN_MEANING = {
    "ROW": "stable row identity",
    "FACE": "semantic face; one of FACES",
    "LANE": "scheduling or subsystem lane",
    "QSPACE": "Hilbert/subsystem/register domain",
    "OP": "operation",
    "OUT": "destination quantum/classical object",
    "CTRL": "control qubit/register/operator",
    "A": "primary operand",
    "B": "secondary operand",
    "PARAM": "angle, time, coefficient, probability, index, or target parameter",
    "TYPE": "quantum/classical semantic type",
    "BASIS": "computational/X/Y/Z/custom/eigenbasis",
    "REGIME": "exactness regime; one of REGIMES",
    "ASSUME": "validity assumptions, semicolon separated",
    "ERROR": "declared error envelope, key=value pairs",
    "RESOURCE": "resource/cost annotation, key=value pairs",
    "CONF": "confidence in [0,1] or NULL",
    "PROOF": "evidence/provenance identifier",
    "DOMAIN": "execution domain identity (1.5 FEDERATION model)",
    "NODE": "logical node identity (1.2 distributed kernel)",
    "LINK": "classical channel or quantum link identity",
    "FAMILY": "declared parallel family; one of PARALLEL_FAMILIES",
}

# --------------------------------------------------------------------------
# 3. Regimes, ladders, verdicts  (LCTL 1.2.x s3)
# --------------------------------------------------------------------------

REGIMES = (
    "EXACT", "EXACT_LINEAR", "PIECEWISE_EXACT", "PERTURBATIVE", "LINEARIZED",
    "VARIATIONAL", "STOCHASTIC", "ASYMPTOTIC", "APPROXIMATE", "NOISY",
    "HARDWARE_CALIBRATED", "TARGET_SPECIFIC", "NO_FAITHFUL_FORM",
    "UNSUPPORTED", "IMPOSSIBLE",
)
REGIME_SET = frozenset(REGIMES)

# An approximate regime may never be promoted to an exact one.
EXACT_REGIMES = frozenset({"EXACT", "EXACT_LINEAR", "PIECEWISE_EXACT"})

PARALLEL_STATES = ("SERIAL", "LOGICAL_PARALLEL", "SCHEDULED_PARALLEL",
                   "PARALLEL_EMULATION", "PHYSICAL_PARALLEL_CANDIDATE",
                   "PHYSICAL_PARALLEL_EXECUTION")

DISTRIBUTED_STATES = ("LOCAL_ONLY", "LOGICAL_DISTRIBUTED", "DISTRIBUTED_SCHEDULED",
                      "DISTRIBUTED_CLASSICAL_EMULATION", "DISTRIBUTED_COMPILED",
                      "PHYSICAL_DISTRIBUTED_CANDIDATE", "PHYSICAL_DISTRIBUTED_EXECUTION")

QUANTUM_BOUNDARY_STATES = ("QUANTUM_BOUNDARY_NOT_CROSSED",
                           "QUANTUM_BOUNDARY_CANDIDATE",
                           "QUANTUM_BOUNDARY_CROSSED")

# LCTL 1.3.x s7 concurrency decisions (supersedes the 1.2.x PARALLEL_* tokens).
CONCURRENCY_DECISIONS = (
    "PARALLEL_EXACT", "PARALLEL_TARGET_CONDITIONAL",
    "SERIALIZE_DATA", "SERIALIZE_OWNERSHIP", "SERIALIZE_MEASUREMENT",
    "SERIALIZE_COUPLING", "SERIALIZE_RESOURCE", "SERIALIZE_TOPOLOGY",
    "SERIALIZE_TIMING", "SERIALIZE_CROSSTALK", "SERIALIZE_ERROR_BUDGET",
    "REJECT_INVALID",
)

COUPLING_VERDICTS = ("DECOUPLED", "WEAKLY_COUPLED", "MODERATELY_COUPLED",
                     "STRONGLY_COUPLED", "MONOLITHIC_REQUIRED", "COUPLING_UNRESOLVED")

# LCTL 1.3.x s58 Q6 admission (9 values; adds ADMIT_NUMERICALLY_VERIFIED).
Q6_ADMISSION = (
    "ADMIT_EXACT", "ADMIT_NUMERICALLY_VERIFIED", "ADMIT_APPROXIMATE",
    "ADMIT_TARGET_SPECIFIC", "OBSERVE", "QUARANTINE",
    "REJECT_INVALID", "REJECT_NO_FAITHFUL_FORM", "REJECT_IMPOSSIBLE",
)

# LCTL 1.3.x s65 / 1.4 s67 / 1.5 s80 / 1.6 s83 status vocabulary.
STATUS_VOCABULARY = ("BLOCKED", "SPECIFIED", "SCAFFOLDED", "IMPLEMENTED",
                     "VERIFIED", "OPERATIONAL", "QUALIFIED")

GAP_TERMINAL_STATES = ("QUALIFIED", "OPERATIONAL", "VERIFIED", "IMPLEMENTED",
                       "IMPLEMENTED_PARTIAL", "BLOCKED_EXTERNAL_AUTHORITY",
                       "REJECTED_NO_FAITHFUL_FORM")

RECOVERY_CLASSES = ("RETRY_SAFE", "RESTART_FROM_CLASSICAL_BOUNDARY", "REROUTE_SAFE",
                    "REGENERATE_ENTANGLEMENT", "REPREPARE_KNOWN_STATE",
                    "ROLLBACK_CLASSICAL_ONLY", "RECOVERY_TARGET_SPECIFIC",
                    "RECOVERY_IMPOSSIBLE")

TRUST_DOMAINS = ("LOCAL_TRUSTED", "LOCAL_UNTRUSTED", "REMOTE_AUTHENTICATED",
                 "REMOTE_UNAUTHENTICATED", "PHYSICAL_TARGET_AUTHENTICATED")

EPR_STATES = ("REQUESTED", "GENERATING", "HERALDED", "RESERVED",
              "CONSUMED", "EXPIRED", "FAILED", "RELEASED")

EXECUTION_PROFILES = ("single_process_deterministic", "multi_thread_deterministic",
                      "multi_process_deterministic", "multi_process_throughput")

# LCTL 1.5.x s5 parallel family model.
PARALLEL_FAMILIES = (
    "INSTRUCTION_PARALLEL", "TENSOR_FACTOR_PARALLEL", "CIRCUIT_PARALLEL",
    "SHOT_PARALLEL", "PARAMETER_PARALLEL", "OBSERVABLE_PARALLEL",
    "TRAJECTORY_PARALLEL", "TASK_PARALLEL", "PIPELINE_PARALLEL",
    "DATA_PARALLEL", "NUMERICAL_SHARD_PARALLEL", "STABILIZER_BATCH_PARALLEL",
    "TENSOR_CONTRACTION_PARALLEL", "CLASSICAL_REPLICA_PARALLEL",
    "PROTOCOL_PARALLEL", "SUBTREE_PARALLEL", "EXCHANGE_PIPELINE_PARALLEL",
)
PARALLEL_FAMILY_SET = frozenset(PARALLEL_FAMILIES)

# LCTL 1.4.x s5 semantic regions.
PARALLEL_REGIONS = ("PARALLEL_REGION", "DISTRIBUTED_REGION", "DATAFLOW_REGION",
                    "ASYNC_REGION", "BSP_REGION", "ENSEMBLE_REGION",
                    "NUMERICAL_SHARD_REGION", "QNETWORK_REGION")

# LCTL 1.4.x s10 consistency profiles (classical state only).
CONSISTENCY_PROFILES = ("SINGLE_OWNER", "EVENTUAL", "CAUSAL", "SEQUENTIAL",
                        "LINEARIZABLE", "IMMUTABLE_REPLICA", "CRDT_DECLARED")

# LCTL 1.2.x s7 DAG edge reason codes.
EDGE_REASONS = (
    "DATA_DEPENDENCY", "QUANTUM_OWNERSHIP", "ENTANGLEMENT_DEPENDENCY",
    "MEASUREMENT_DEPENDENCY", "CLASSICAL_CONTROL", "RESOURCE_CONFLICT",
    "TOPOLOGY_CONSTRAINT", "COMMUNICATION", "BARRIER", "COUPLING",
    "MEMORY_ORDER", "TARGET_RESTRICTION", "ERROR_BUDGET", "USER_ORDER",
)
EDGE_REASON_SET = frozenset(EDGE_REASONS)

# --------------------------------------------------------------------------
# 4. Type system  (LCTL 1.1.x s5)
# --------------------------------------------------------------------------

SCALAR_TYPES = ("amplitude", "probability", "phase", "angle", "complex",
                "real", "time", "frequency", "energy")

QUANTUM_STATE_TYPES = ("qubit", "qudit", "qreg", "ket", "bra", "pure_state",
                       "mixed_state", "density", "subsystem", "bipartite_state",
                       "multipartite_state", "entangled_state", "separable_state")

OPERATOR_TYPES = ("operator", "linear_operator", "hermitian", "unitary",
                  "projector", "povm", "hamiltonian", "kraus_set", "channel",
                  "cptp", "isometry", "permutation", "controlled_operator", "oracle")

STRUCTURAL_TYPES = ("tensor", "tensor_product", "graph", "coupling_graph",
                    "basis", "spectrum", "eigensystem", "sparse_operator",
                    "block_operator", "pauli_string", "stabilizer", "syndrome")

CLASSICAL_TYPES = ("bit", "integer", "real_c", "complex_c", "vector", "matrix",
                   "distribution", "measurement_result", "histogram",
                   "confidence_interval", "resource_report", "proof_record")

# LCTL 1.2.x s4 parallel kernel objects.
PARALLEL_TYPES = ("task", "qtask", "ctask", "lane", "epoch", "tick", "event",
                  "future", "promise", "dependency", "barrier", "fence",
                  "critical_path", "work", "span", "parallel_region", "pipeline",
                  "stage", "partition", "shard", "replica", "reduction", "scan",
                  "collective", "scheduler_hint", "placement_constraint",
                  "locality_constraint", "resource_claim")

# LCTL 1.2.x s5 distributed kernel objects.
DISTRIBUTED_TYPES = ("node", "cluster", "device", "cpu", "gpu", "qpu",
                     "memory_domain", "numa_domain", "network", "link",
                     "classical_channel", "quantum_link", "communicator",
                     "route", "topology", "region", "distributed_partition",
                     "remote_handle", "remote_result", "remote_event",
                     "consistency_scope", "failure_domain", "checkpoint_scope",
                     "ebit")

# LCTL 1.5.x s4 federation objects.
FEDERATION_TYPES = ("federation", "execution_domain", "domain_group",
                    "worker_group", "placement_set", "route_set",
                    "trust_domain", "consistency_domain", "calibration_domain",
                    "resource_pool", "ebit_pool", "memory_pool", "task_pool",
                    "shot_pool", "parameter_pool", "circuit_pool")

ALL_TYPES = frozenset(
    SCALAR_TYPES + QUANTUM_STATE_TYPES + OPERATOR_TYPES + STRUCTURAL_TYPES
    + CLASSICAL_TYPES + PARALLEL_TYPES + DISTRIBUTED_TYPES + FEDERATION_TYPES
)

# Types that carry unknown quantum state and are therefore ownership-governed
# (LCTL 1.1.x s6; 1.2.x s6 rules 1-8).
QUANTUM_OWNED_TYPES = frozenset({
    "qubit", "qudit", "qreg", "ket", "pure_state", "mixed_state", "density",
    "subsystem", "bipartite_state", "multipartite_state", "entangled_state",
    "separable_state", "ebit",
})

# --------------------------------------------------------------------------
# 5. Operation catalog  (LCTL 1.1.x s7, LCTL 1.2.x s11/s20)
# --------------------------------------------------------------------------

OPS_PREPARE = ("PREP0", "PREP1", "PREP_PLUS", "PREP_MINUS", "PREP_BASIS",
               "PREP_STATE", "RESET")

OPS_1Q = ("I", "X", "Y", "Z", "H", "S", "SDG", "T", "TDG",
          "RX", "RY", "RZ", "PHASE", "U")

OPS_MQ = ("CX", "CNOT", "CY", "CZ", "CH", "SWAP", "ISWAP", "CSWAP",
          "CCX", "TOFFOLI", "CU", "MCU")

OPS_MEASURE = ("MEASURE", "MEASURE_Z", "MEASURE_X", "MEASURE_Y",
               "MEASURE_BASIS", "POVM", "SAMPLE", "EXPECT", "VARIANCE")

OPS_TENSOR = ("TENSOR", "PARTIAL_TRACE", "REDUCE", "PERMUTE_QUBITS",
              "PARTITION", "MERGE", "ENTANGLE")

OPS_OPERATOR = ("KRON", "COMPOSE", "ADJOINT", "EXP_OPERATOR", "COMMUTATOR",
                "ANTICOMMUTATOR", "PROJECT", "SPECTRAL", "DIAGONALIZE",
                "PAULI_DECOMPOSE")

OPS_EVOLVE = ("EVOLVE", "HAMILTONIAN", "UNITARY_EVOLVE", "TROTTER",
              "SUZUKI", "ADIABATIC", "PULSE")

OPS_NOISE = ("DEPOLARIZE", "DEPHASE", "BIT_FLIP", "PHASE_FLIP",
             "AMPLITUDE_DAMP", "PHASE_DAMP", "PAULI_CHANNEL",
             "KRAUS_CHANNEL", "READOUT_ERROR", "LEAKAGE_MODEL", "CROSSTALK_MODEL")

OPS_QEC = ("ENCODE", "SYNDROME", "DETECT", "CORRECT", "DECODE",
           "STABILIZER", "LOGICAL")

OPS_HYBRID = ("CLASSICAL_IF", "CLASSICAL_SWITCH", "REPEAT", "UNTIL", "WHILE",
              "SHOT_LOOP", "PARAM_BIND", "FEEDBACK")

# LCTL 1.2.x s11 distributed quantum primitives.
OPS_DISTRIBUTED_Q = ("ENTANGLE_LINK", "EPR_RESERVE", "EPR_RELEASE", "TELEPORT",
                     "REMOTE_CONTROL", "REMOTE_CNOT", "REMOTE_MEASURE",
                     "CLASSICAL_FEEDBACK", "ENTANGLEMENT_SWAP", "PURIFY",
                     "HERALD", "SYNC_QCLOCK", "QCHANNEL_SEND", "QCHANNEL_RECEIVE")

# LCTL 1.2.x s20 / 1.3.x s40 classical collectives.
OPS_COLLECTIVE = ("BROADCAST", "SCATTER", "GATHER", "ALLGATHER", "REDUCE",
                  "ALLREDUCE", "SCAN", "ALLTOALL")

# Structural / region / declaration ops.
OPS_STRUCTURAL = ("REGION_BEGIN", "REGION_END", "BARRIER", "FENCE", "EPOCH",
                  "SPAWN", "AWAIT", "CHANNEL_SEND", "CHANNEL_RECV",
                  "DECLARE_NODE", "DECLARE_DOMAIN", "DECLARE_LINK",
                  "DECLARE_TOPOLOGY", "DECLARE_FEDERATION", "CLAIM",
                  "ASSERT_INVARIANT", "EMIT_LEDGER", "NOTE")

OP_CATALOG: Dict[str, Tuple[str, ...]] = {
    "prepare": OPS_PREPARE, "single_qubit": OPS_1Q, "multi_qubit": OPS_MQ,
    "measure": OPS_MEASURE, "tensor": OPS_TENSOR, "operator": OPS_OPERATOR,
    "evolve": OPS_EVOLVE, "noise": OPS_NOISE, "qec": OPS_QEC,
    "hybrid": OPS_HYBRID, "distributed_quantum": OPS_DISTRIBUTED_Q,
    "collective": OPS_COLLECTIVE, "structural": OPS_STRUCTURAL,
}
ALL_OPS = frozenset(op for fam in OP_CATALOG.values() for op in fam)

OP_FAMILY = {op: fam for fam, ops in OP_CATALOG.items() for op in ops}

# Gate arity, used by the verifier and the simulator.
GATE_ARITY = {
    **{g: 1 for g in ("I", "X", "Y", "Z", "H", "S", "SDG", "T", "TDG",
                      "RX", "RY", "RZ", "PHASE", "U")},
    **{g: 2 for g in ("CX", "CNOT", "CY", "CZ", "CH", "SWAP", "ISWAP")},
    **{g: 3 for g in ("CSWAP", "CCX", "TOFFOLI")},
}
PARAMETRIC_GATES = frozenset({"RX", "RY", "RZ", "PHASE", "U"})
CONTROLLED_GATES = frozenset({"CX", "CNOT", "CY", "CZ", "CH", "CSWAP",
                              "CCX", "TOFFOLI", "CU", "MCU"})

# Destructive operations consume quantum ownership of their operand.
DESTRUCTIVE_OPS = frozenset({"MEASURE", "MEASURE_Z", "MEASURE_X", "MEASURE_Y",
                             "MEASURE_BASIS", "POVM", "REMOTE_MEASURE"})
# Operations that re-establish a known state and therefore restore ownership.
REINIT_OPS = frozenset(OPS_PREPARE)


# --------------------------------------------------------------------------
# 6. Diagnostics
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str          # ERROR | REJECT | WARN | INFO
    row: Optional[str]
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "row": self.row, "message": self.message}

    def __str__(self) -> str:
        loc = self.row or "-"
        return f"[{self.severity}] {self.code} @{loc}: {self.message}"


class PALCTLError(Exception):
    """Raised only for malformed invocation, never for program rejection."""


# --------------------------------------------------------------------------
# 7. Row and Program
# --------------------------------------------------------------------------

def _parse_kv(cell: str) -> Dict[str, str]:
    """Parse `k=v;k=v` metadata cells deterministically."""
    if cell == NULL_CELL:
        return {}
    out: Dict[str, str] = {}
    for part in cell.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        else:
            out[part] = "true"
    return out


def _split_list(cell: str) -> List[str]:
    if cell == NULL_CELL:
        return []
    return [p.strip() for p in cell.split(";") if p.strip()]


_QREF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)(?::(\d+))?\])?$")


@dataclass
class QRef:
    """A reference to a quantum or classical object, optionally indexed."""
    name: str
    lo: Optional[int] = None
    hi: Optional[int] = None
    raw: str = ""

    @property
    def indices(self) -> List[int]:
        if self.lo is None:
            return []
        if self.hi is None:
            return [self.lo]
        return list(range(self.lo, self.hi))

    def key(self) -> str:
        return self.name if self.lo is None else f"{self.name}[{self.lo}]"

    def keys(self) -> List[str]:
        if self.lo is None:
            return [self.name]
        return [f"{self.name}[{i}]" for i in self.indices]

    def __str__(self) -> str:
        return self.raw or self.key()


def parse_ref(cell: str) -> Optional[QRef]:
    if cell == NULL_CELL:
        return None
    m = _QREF.match(cell)
    if not m:
        return None
    name, lo, hi = m.group(1), m.group(2), m.group(3)
    return QRef(name,
                int(lo) if lo is not None else None,
                int(hi) if hi is not None else None,
                raw=cell)


@dataclass
class Row:
    row_id: str
    face: str
    lane: str
    qspace: str
    op: str
    out: str
    ctrl: str
    a: str
    b: str
    param: str
    type_: str
    basis: str
    regime: str
    assume: str
    error: str
    resource: str
    conf: str
    proof: str
    domain: str = NULL_CELL
    node: str = NULL_CELL
    link: str = NULL_CELL
    family: str = NULL_CELL
    line_no: int = 0
    # Extension cells (PA-LCTL 1.6.1). Column names declared by #COLUMNS that
    # are not in FULL_COLUMNS are carried here verbatim, in declaration order,
    # so that render()/canonical_text()/seal() round-trip them instead of
    # raising KeyError. The verifier never reads them; they are opaque payload
    # covered by the seal, which is what makes an extension tamper-evident.
    extra: Dict[str, str] = field(default_factory=dict)

    # -- derived views -----------------------------------------------------
    @property
    def assumptions(self) -> List[str]:
        return _split_list(self.assume)

    @property
    def error_map(self) -> Dict[str, str]:
        return _parse_kv(self.error)

    @property
    def resource_map(self) -> Dict[str, str]:
        return _parse_kv(self.resource)

    @property
    def confidence(self) -> Optional[float]:
        if self.conf == NULL_CELL:
            return None
        try:
            return float(self.conf)
        except ValueError:
            return None

    @property
    def params(self) -> List[float]:
        vals: List[float] = []
        for p in _split_list(self.param):
            if "=" in p:
                p = p.split("=", 1)[1]
            try:
                vals.append(_eval_number(p))
            except Exception:
                pass
        return vals

    @property
    def param_map(self) -> Dict[str, str]:
        return _parse_kv(self.param)

    def cells(self, columns: Sequence[str] = FULL_COLUMNS) -> List[str]:
        m = {
            "ROW": self.row_id, "FACE": self.face, "LANE": self.lane,
            "QSPACE": self.qspace, "OP": self.op, "OUT": self.out,
            "CTRL": self.ctrl, "A": self.a, "B": self.b, "PARAM": self.param,
            "TYPE": self.type_, "BASIS": self.basis, "REGIME": self.regime,
            "ASSUME": self.assume, "ERROR": self.error,
            "RESOURCE": self.resource, "CONF": self.conf, "PROOF": self.proof,
            "DOMAIN": self.domain, "NODE": self.node, "LINK": self.link,
            "FAMILY": self.family,
        }
        # Unknown column names resolve through `extra` (PA-LCTL 1.6.1). Before
        # 1.6.1 this was `m[c]`, so a bundle using the extension affordance the
        # grammar documents parsed and verified cleanly and then raised
        # KeyError inside render()/canonical_text()/seal().
        return [m[c] if c in m else self.extra.get(c, NULL_CELL)
                for c in columns]

    def render(self, columns: Sequence[str] = FULL_COLUMNS) -> str:
        return CANONICAL_SEP.join(self.cells(columns))

    # -- operand analysis --------------------------------------------------
    def operand_refs(self) -> List[QRef]:
        refs = []
        for cell in (self.out, self.ctrl, self.a, self.b):
            r = parse_ref(cell)
            if r is not None:
                refs.append(r)
        return refs

    def reads(self) -> List[str]:
        """Object keys read by this row."""
        keys: List[str] = []
        for cell in (self.ctrl, self.a, self.b):
            r = parse_ref(cell)
            if r is not None:
                keys.extend(r.keys())
        return keys

    def writes(self) -> List[str]:
        """Object keys written by this row."""
        r = parse_ref(self.out)
        keys = list(r.keys()) if r is not None else []
        # In-place unitary gates write their operands as well as reading them.
        # CONTROLLED_GATES is included from 1.6.1: CU and MCU are controlled
        # gates with no entry in GATE_ARITY, so before 1.6.1 they never marked
        # their target written and ownership was silently under-tracked
        # through them.
        if (self.op in GATE_ARITY or self.op in OPS_NOISE
                or self.op in CONTROLLED_GATES):
            for cell in (self.a, self.b, self.ctrl):
                rr = parse_ref(cell)
                if rr is not None and self.op not in CONTROLLED_GATES:
                    keys.extend(rr.keys())
                elif rr is not None and cell is self.a:
                    keys.extend(rr.keys())
        return sorted(set(keys))


_NUM_TOKENS = {
    "pi": 3.141592653589793, "PI": 3.141592653589793,
    "tau": 6.283185307179586, "e": 2.718281828459045,
}


def _eval_number(tok: str) -> float:
    """Deterministic, sandboxed numeric literal evaluation.

    Accepts decimal literals and the forms `pi`, `-pi`, `pi/2`, `3*pi/4`.
    No general expression evaluation is performed and no name lookup other
    than the fixed table above is possible.
    """
    t = tok.strip()
    if not t:
        raise ValueError("empty numeric token")
    if not re.fullmatch(r"[-+0-9eE.*/ ]*(pi|PI|tau|e)?[-+0-9eE.*/ ]*", t):
        # fall through to plain float parse; will raise if invalid
        return float(t)
    sign = 1.0
    if t.startswith("-"):
        sign, t = -1.0, t[1:]
    elif t.startswith("+"):
        t = t[1:]
    total = None
    for factor in re.split(r"(?=[*/])", t):
        factor = factor.strip()
        if not factor:
            continue
        opch = "*"
        if factor[0] in "*/":
            opch, factor = factor[0], factor[1:].strip()
        val = _NUM_TOKENS.get(factor)
        if val is None:
            val = float(factor)
        if total is None:
            total = val
        elif opch == "*":
            total *= val
        else:
            total /= val
    if total is None:
        raise ValueError(f"unparsable numeric token {tok!r}")
    return sign * total


@dataclass
class Program:
    magic: str
    directives: Dict[str, str]
    columns: Tuple[str, ...]
    rows: List[Row]
    source_text: str
    path: str = "<memory>"

    @property
    def profile(self) -> str:
        return self.directives.get("PROFILE", "pa.lctl.core")

    @property
    def network_policy(self) -> str:
        return self.directives.get("NETWORK", "deny")

    @property
    def backend_policy(self) -> str:
        return self.directives.get("BACKEND", "none")

    def source_hash(self) -> str:
        return hashlib.sha256(self.source_text.encode("utf-8")).hexdigest()

    def canonical_text(self) -> str:
        """Deterministic canonical serialization (used for sealing)."""
        lines = [self.magic]
        for k in sorted(self.directives):
            lines.append(f"#{k} {self.directives[k]}")
        lines.append("#COLUMNS " + CANONICAL_SEP.join(self.columns))
        lines.extend(r.render(self.columns) for r in self.rows)
        return "\n".join(lines) + "\n"

    def seal(self) -> str:
        return hashlib.sha256(self.canonical_text().encode("utf-8")).hexdigest()

    def by_id(self, row_id: str) -> Optional[Row]:
        for r in self.rows:
            if r.row_id == row_id:
                return r
        return None


# --------------------------------------------------------------------------
# 8. Parser  (LCTL 1.1.x PHASE 3, exit gate GRAMMAR_PASS)
# --------------------------------------------------------------------------

MAGIC_RE = re.compile(r"^#\s*(PA-LCTL|LCTL|QCTL)\s*/\s*(\d+\.\d+)\s*$")


def parse(text: str, path: str = "<memory>") -> Tuple[Optional[Program], List[Diagnostic]]:
    """Parse PA-LCTL source. Returns (program|None, diagnostics).

    The parser is total: it never raises on malformed input, it reports.
    """
    diags: List[Diagnostic] = []
    magic: Optional[str] = None
    directives: Dict[str, str] = {}
    columns: Optional[Tuple[str, ...]] = None
    rows: List[Row] = []
    seen_ids: Dict[str, int] = {}

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith(";;"):          # full-line comment
            continue
        if line.lstrip().startswith("#"):
            body = line.lstrip()
            m = MAGIC_RE.match(body)
            if m and magic is None:
                magic = f"{m.group(1)}/{m.group(2)}"
                continue
            parts = body[1:].split(None, 1)
            if not parts:
                continue
            key = parts[0].upper()
            val = parts[1].strip() if len(parts) > 1 else "true"
            if key == "COLUMNS":
                sep = CANONICAL_SEP if CANONICAL_SEP in val else "|"
                cols = tuple(c.strip().upper() for c in val.split(sep) if c.strip())
                columns = cols
                # PA-LCTL 1.6.1: an extension column is carried, sealed and
                # never interpreted. Announce it so the author knows the
                # verifier is not reading it.
                for c in cols:
                    if c not in FULL_COLUMNS:
                        diags.append(Diagnostic(
                            "W-COL-001", "WARN", None,
                            f"line {lineno}: extension column {c!r} is carried "
                            f"verbatim and covered by the seal, but no "
                            f"normative rule interprets it"))
            else:
                directives[key] = val
            continue

        # data row
        if columns is None:
            columns = FULL_COLUMNS
        sep = CANONICAL_SEP if CANONICAL_SEP in line else ("|" if "|" in line else None)
        if sep is None:
            diags.append(Diagnostic("E-GRAM-001", "ERROR", None,
                                    f"line {lineno}: no column separator found"))
            continue
        cells = [normalize_cell(c) for c in line.split(sep)]
        if len(cells) != len(columns):
            diags.append(Diagnostic(
                "E-GRAM-002", "ERROR", cells[0] if cells else None,
                f"line {lineno}: expected {len(columns)} cells, found {len(cells)}"))
            continue
        cmap = dict(zip(columns, cells))
        for missing in FULL_COLUMNS:
            cmap.setdefault(missing, NULL_CELL)
        row = Row(
            row_id=cmap["ROW"], face=cmap["FACE"], lane=cmap["LANE"],
            qspace=cmap["QSPACE"], op=cmap["OP"], out=cmap["OUT"],
            ctrl=cmap["CTRL"], a=cmap["A"], b=cmap["B"], param=cmap["PARAM"],
            type_=cmap["TYPE"], basis=cmap["BASIS"], regime=cmap["REGIME"],
            assume=cmap["ASSUME"], error=cmap["ERROR"],
            resource=cmap["RESOURCE"], conf=cmap["CONF"], proof=cmap["PROOF"],
            domain=cmap["DOMAIN"], node=cmap["NODE"], link=cmap["LINK"],
            family=cmap["FAMILY"], line_no=lineno,
            extra={c: cmap[c] for c in columns if c not in FULL_COLUMNS},
        )
        if row.row_id in seen_ids:
            diags.append(Diagnostic("E-GRAM-003", "REJECT", row.row_id,
                                    f"duplicate ROW identity (first at line "
                                    f"{seen_ids[row.row_id]})"))
        seen_ids[row.row_id] = lineno
        rows.append(row)

    if magic is None:
        diags.append(Diagnostic("E-GRAM-004", "ERROR", None,
                                "missing bundle magic, expected `#PA-LCTL/1.6`"))
        return None, diags
    if not rows:
        diags.append(Diagnostic("E-GRAM-005", "ERROR", None, "program has no rows"))

    prog = Program(magic=magic, directives=directives,
                   columns=tuple(columns or FULL_COLUMNS),
                   rows=rows, source_text=text, path=path)
    return prog, diags


def parse_file(path: str) -> Tuple[Optional[Program], List[Diagnostic]]:
    with open(path, "r", encoding="utf-8") as fh:
        return parse(fh.read(), path=path)


# --------------------------------------------------------------------------
# 9. Verifier: schema, types, operators, ownership
#    (LCTL 1.1.x PHASE 2 / PHASE 4, gates QUANTUM_TYPE_SYSTEM_PASS,
#     OPERATOR_CORE_PASS; LCTL 1.2.x PHASE 3 GLOBAL_OWNERSHIP_PASS)
# --------------------------------------------------------------------------

@dataclass
class OwnershipRecord:
    key: str
    lineage: str
    state: str                 # LIVE | MOVED | MEASURED | RELEASED
    owner_node: str
    owner_domain: str
    last_row: str
    entangled_with: set = field(default_factory=set)


@dataclass
class VerifyResult:
    ok: bool
    diagnostics: List[Diagnostic]
    ownership: Dict[str, OwnershipRecord]
    declared_nodes: Dict[str, dict]
    declared_domains: Dict[str, dict]
    declared_links: Dict[str, dict]

    @property
    def rejections(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity in ("REJECT", "ERROR")]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "ownership": {
                k: {"lineage": v.lineage, "state": v.state,
                    "owner_node": v.owner_node, "owner_domain": v.owner_domain,
                    "last_row": v.last_row,
                    "entangled_with": sorted(v.entangled_with)}
                for k, v in sorted(self.ownership.items())
            },
            "declared_nodes": self.declared_nodes,
            "declared_domains": self.declared_domains,
            "declared_links": self.declared_links,
        }


def verify(prog: Program) -> VerifyResult:
    """Full static verification.

    Fails closed: any REJECT diagnostic makes the program invalid. The
    verifier never silently repairs a program (LCTL 1.1.x s23).
    """
    d: List[Diagnostic] = []
    own: Dict[str, OwnershipRecord] = {}
    nodes: Dict[str, dict] = {}
    domains: Dict[str, dict] = {}
    links: Dict[str, dict] = {}
    classical_bits: Dict[str, str] = {}
    lineage_counter = [0]

    def new_lineage() -> str:
        lineage_counter[0] += 1
        return f"LIN{lineage_counter[0]:04d}"

    def err(code, row, msg, sev="REJECT"):
        d.append(Diagnostic(code, sev, row.row_id if row else None, msg))

    # --- policy invariants (LCTL 1.3.x s50) ---
    if prog.network_policy != "deny":
        d.append(Diagnostic("E-POL-001", "REJECT", None,
                            f"NETWORK must be `deny` for the reference profile, "
                            f"found {prog.network_policy!r}"))
    if prog.backend_policy != "none":
        d.append(Diagnostic("E-POL-002", "REJECT", None,
                            f"BACKEND must be `none` for the reference profile, "
                            f"found {prog.backend_policy!r}"))

    for row in prog.rows:
        # --- schema-level checks ---
        if row.face not in FACE_SET:
            err("E-FACE-001", row, f"unknown FACE {row.face!r}")
            continue
        if row.regime != NULL_CELL and row.regime not in REGIME_SET:
            err("E-REG-001", row, f"unknown REGIME {row.regime!r}")
        if row.op not in ALL_OPS:
            err("E-OP-001", row, f"operation {row.op!r} is not in the "
                                 f"normative operation catalog")
            continue
        if row.type_ != NULL_CELL:
            base = row.type_.split("[")[0]
            if base not in ALL_TYPES:
                err("E-TYPE-001", row, f"unknown TYPE {row.type_!r}")
        if row.family != NULL_CELL and row.family not in PARALLEL_FAMILY_SET:
            err("E-FAM-001", row, f"unknown parallel FAMILY {row.family!r}")
        c = row.confidence
        if c is not None and not (0.0 <= c <= 1.0):
            err("E-CONF-001", row, f"CONF {row.conf!r} outside [0,1]")

        # --- declarations ---
        if row.op == "DECLARE_NODE":
            nodes[row.out] = {"node": row.out, "domain": row.domain,
                              "resource": row.resource_map, "row": row.row_id}
            continue
        if row.op == "DECLARE_DOMAIN":
            domains[row.out] = {"domain": row.out, "resource": row.resource_map,
                                "row": row.row_id}
            continue
        if row.op == "DECLARE_LINK":
            links[row.out] = {"link": row.out, "a": row.a, "b": row.b,
                              "kind": row.type_, "resource": row.resource_map,
                              "row": row.row_id}
            continue
        if row.op in ("DECLARE_TOPOLOGY", "DECLARE_FEDERATION", "NOTE",
                      "EMIT_LEDGER", "CLAIM", "ASSERT_INVARIANT"):
            continue

        # --- node / domain references must be declared ---
        if row.node != NULL_CELL and row.node not in nodes:
            err("E-NODE-001", row, f"NODE {row.node!r} used before DECLARE_NODE")
        if row.domain != NULL_CELL and row.domain not in domains \
                and row.node == NULL_CELL:
            err("E-DOM-001", row, f"DOMAIN {row.domain!r} used before DECLARE_DOMAIN")
        if row.link != NULL_CELL and row.link not in links:
            err("E-LINK-001", row, f"LINK {row.link!r} used before DECLARE_LINK")

        # --- gate arity and parameters ---
        if row.op in GATE_ARITY:
            arity = GATE_ARITY[row.op]
            operands = [c for c in (row.ctrl, row.a, row.b) if c != NULL_CELL]
            n_targets = 0
            for cell in operands:
                r = parse_ref(cell)
                n_targets += len(r.keys()) if r else 0
            if n_targets != arity:
                err("E-ARITY-001", row,
                    f"{row.op} requires {arity} qubit operand(s), found {n_targets}")
            if row.op in PARAMETRIC_GATES and not row.params:
                err("E-PARAM-001", row, f"{row.op} requires a PARAM angle")

        # --- control/target disjointness (PA-LCTL 1.6.1) ---
        # OPERATOR_SEMANTICS s5.3 states this unconditionally for every member
        # of CONTROLLED_GATES. Before 1.6.1 the check sat inside the
        # `row.op in GATE_ARITY` block above, so CU and MCU -- catalogued as
        # controlled but deliberately absent from GATE_ARITY because they have
        # no reference matrix -- escaped it entirely and a self-controlled CU
        # verified clean. The check is now keyed on CONTROLLED_GATES, which is
        # what the specification always said.
        if row.op in CONTROLLED_GATES:
            cr, ar = parse_ref(row.ctrl), parse_ref(row.a)
            if cr and ar and set(cr.keys()) & set(ar.keys()):
                err("E-CTRL-001", row,
                    "control and target must be disjoint qubits")

        # --- probability domain for noise ---
        if row.op in OPS_NOISE:
            for p in row.params:
                if not (0.0 <= p <= 1.0):
                    err("E-PROB-001", row,
                        f"noise parameter {p} outside probability domain [0,1]")

        # --- ownership / no-cloning (LCTL 1.1.x s6, 1.2.x s6) ---
        writes = set(row.writes())
        reads = set(row.reads())

        if row.op in REINIT_OPS:
            for k in writes or {row.out}:
                if k == NULL_CELL:
                    continue
                rec = own.get(k)
                if rec is None:
                    own[k] = OwnershipRecord(k, new_lineage(), "LIVE",
                                             row.node, row.domain, row.row_id)
                else:
                    rec.state = "LIVE"
                    rec.lineage = new_lineage()
                    rec.owner_node, rec.owner_domain = row.node, row.domain
                    rec.last_row = row.row_id
                    rec.entangled_with.clear()
            continue

        quantum_operands = [k for k in (reads | writes)
                            if k in own or _looks_quantum(row, k)]

        for k in sorted(reads | writes):
            rec = own.get(k)
            if rec is None:
                if _looks_quantum(row, k):
                    err("E-OWN-005", row,
                        f"quantum object {k!r} used before preparation")
                continue
            if rec.state == "MOVED":
                err("E-OWN-001", row,
                    f"use-after-move of quantum object {k!r} "
                    f"(moved at row {rec.last_row})")
            elif rec.state == "MEASURED" and row.op not in REINIT_OPS:
                err("E-OWN-002", row,
                    f"use-after-destructive-measure of {k!r} "
                    f"(measured at row {rec.last_row}); RESET or PREP required")
            elif rec.state == "RELEASED":
                err("E-OWN-006", row, f"use of released resource {k!r}")
            elif rec.owner_node not in (NULL_CELL, row.node) \
                    and row.op not in OPS_DISTRIBUTED_Q:
                err("E-OWN-003", row,
                    f"local operation on {k!r} whose authoritative owner is "
                    f"node {rec.owner_node!r}, not {row.node!r}; a distributed "
                    f"protocol primitive is required")

        # explicit clone attempt: two distinct live quantum objects, one
        # assigned from the other by a non-protocol copy
        if row.op in ("TENSOR", "MERGE") and row.a != NULL_CELL and row.b != NULL_CELL:
            ar, br = parse_ref(row.a), parse_ref(row.b)
            if ar and br and set(ar.keys()) & set(br.keys()):
                err("E-CLONE-001", row,
                    f"operation {row.op} would duplicate quantum object(s) "
                    f"{sorted(set(ar.keys()) & set(br.keys()))}")

        # --- measurement consumes ownership ---
        if row.op in DESTRUCTIVE_OPS:
            src = parse_ref(row.a) or parse_ref(row.ctrl)
            if src:
                for k in src.keys():
                    rec = own.get(k)
                    if rec:
                        rec.state = "MEASURED"
                        rec.last_row = row.row_id
            if row.out != NULL_CELL:
                classical_bits[row.out] = row.row_id

        # --- distributed protocols move ownership ---
        if row.op == "TELEPORT":
            src, dst = parse_ref(row.a), parse_ref(row.out)
            if src is None or dst is None:
                err("E-PROTO-001", row, "TELEPORT requires A (source) and OUT (destination)")
            elif row.link == NULL_CELL:
                err("E-PROTO-002", row, "TELEPORT requires a LINK carrying an ebit")
            else:
                for sk, dk in zip(src.keys(), dst.keys()):
                    srec = own.get(sk)
                    if srec is None:
                        err("E-PROTO-003", row, f"TELEPORT source {sk!r} unprepared")
                        continue
                    lineage = srec.lineage
                    srec.state = "MOVED"
                    srec.last_row = row.row_id
                    own[dk] = OwnershipRecord(dk, lineage, "LIVE", row.node,
                                              row.domain, row.row_id)

        if row.op in ("ENTANGLE_LINK", "EPR_RESERVE"):
            dst = parse_ref(row.out)
            if row.link == NULL_CELL:
                err("E-EPR-001", row, f"{row.op} requires a LINK")
            if dst:
                for k in dst.keys():
                    own[k] = OwnershipRecord(k, new_lineage(), "LIVE", row.node,
                                             row.domain, row.row_id)

        if row.op == "EPR_RELEASE":
            src = parse_ref(row.a)
            if src:
                for k in src.keys():
                    rec = own.get(k)
                    if rec is None:
                        err("E-EPR-002", row, f"release of unknown ebit {k!r}")
                    elif rec.state in ("RELEASED", "CONSUMED"):
                        err("E-EPR-003", row, f"double release/consume of {k!r}")
                    else:
                        rec.state = "RELEASED"
                        rec.last_row = row.row_id

        if row.op in ("REMOTE_CNOT", "REMOTE_CONTROL"):
            if row.link == NULL_CELL:
                err("E-PROTO-004", row, f"{row.op} requires a LINK")
            cr, ar = parse_ref(row.ctrl), parse_ref(row.a)
            if cr and ar:
                cn = own.get(cr.key())
                an = own.get(ar.key())
                if cn and an and cn.owner_node == an.owner_node:
                    d.append(Diagnostic(
                        "W-PROTO-005", "WARN", row.row_id,
                        "remote gate applied to co-located qubits; a local gate "
                        "is cheaper and semantically identical"))
                if cn and an:
                    cn.entangled_with.add(an.key)
                    an.entangled_with.add(cn.key)

        # --- classical control must reference an existing measurement ---
        if row.op in ("CLASSICAL_IF", "CLASSICAL_SWITCH", "FEEDBACK",
                      "CLASSICAL_FEEDBACK"):
            src = row.ctrl if row.ctrl != NULL_CELL else row.a
            if src == NULL_CELL:
                err("E-CTL-002", row, f"{row.op} requires a classical condition")
            elif src not in classical_bits:
                err("E-CTL-003", row,
                    f"{row.op} depends on classical value {src!r} that no prior "
                    f"measurement produced")

        # --- establish ownership for newly created quantum outputs ---
        if row.op not in DESTRUCTIVE_OPS and row.op not in OPS_DISTRIBUTED_Q:
            for k in writes:
                if k in own:
                    own[k].last_row = row.row_id
                elif _looks_quantum(row, k):
                    own[k] = OwnershipRecord(k, new_lineage(), "LIVE",
                                             row.node, row.domain, row.row_id)

        # --- exactness honesty (LCTL 1.2.x s3.4) ---
        if row.regime in EXACT_REGIMES and row.op in OPS_NOISE:
            err("E-REG-002", row,
                f"noise operation {row.op} cannot be declared {row.regime}")
        if row.regime in EXACT_REGIMES and row.error_map.get("approx") == "true":
            err("E-REG-003", row, "row declares EXACT regime but carries an "
                                  "approximation flag")

    ok = not any(x.severity in ("REJECT", "ERROR") for x in d)
    return VerifyResult(ok, d, own, nodes, domains, links)


_QUANTUM_HINT = re.compile(r"^(q|qr|anc|ebit|psi|rho|log)")


def _looks_quantum(row: Row, key: str) -> bool:
    base = row.type_.split("[")[0]
    if base in QUANTUM_OWNED_TYPES:
        return True
    if row.op in GATE_ARITY or row.op in OPS_PREPARE or row.op in OPS_NOISE:
        return bool(_QUANTUM_HINT.match(key))
    return False
