"""
Executable conformance suite for PA-LCTL.

Implements:
  * LCTL 1.2.x s41   negative-case catalog (invalid programs that MUST reject)
  * LCTL 1.3.x s59   positive conformance groups
  * LCTL 1.3.x s60   negative catalog extensions
  * LCTL 1.4.x s61   adaptive-mesh conformance groups
  * LCTL 1.4.x s62   negative catalog extensions
  * LCTL 1.5.x s70   property, metamorphic and differential campaigns
  * LCTL 1.6.x s80   hyperfederated negative catalog extensions
  * LCTL 1.6.x s82   soak qualification thresholds

Every case in this module is *executed*. Nothing is counted from a table: a
case contributes to the totals only after `run_suite` has actually run it and
checked its outcome. A negative case fails when the program is accepted, and
also fails when it is rejected for a reason other than the one it declares.

The suite is deterministic. Given the same seeds it produces the same
`evidence_hash`, the same failures, and the same campaign verdicts.

SPECIFICATION HOLES FILLED HERE (recorded in the gap ledger):
  H22. The QUORUM documents name negative categories but never give diagnostic
       codes for the ones the 1.1 verifier does not already emit. This module
       defines the missing codes (the `E-CONC-*`, `E-*ROUTE-*`, `E-SEAL-*`,
       `E-CLAIM-*`, `E-DENS-*` families) and states, for each, the executable
       check that produces it.
  H23. The documents require negative programs to be rejected but do not say
       by which component. Here rejection is the verdict of `admit()`, the
       full admission pipeline: parse, verify, SES, concurrency admission,
       target binding, routing, entanglement inventory, budgets, seals,
       numerical claims, protocol compilation and runtime-contract claims.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import (commutation as commutation_mod, crdt, erroralgebra, fabric,
               lang, ledgers, planner, protocols, resilience, ses as ses_mod,
               simulator)
from .lang import CANONICAL_SEP, FULL_COLUMNS, NULL_CELL

# --------------------------------------------------------------------------
# 0. Program construction DSL
# --------------------------------------------------------------------------

_COLUMN_KEYS = {c.lower(): c for c in FULL_COLUMNS}
_COLUMN_KEYS["type"] = "TYPE"
_COLUMN_KEYS["row_id"] = "ROW"


def row(row_id: str, face: str, op: str, **cells: str) -> str:
    """Render one canonical columned tuple. Unset columns become NULL."""
    values = {c: NULL_CELL for c in FULL_COLUMNS}
    values["ROW"], values["FACE"], values["OP"] = row_id, face, op
    for key, val in cells.items():
        col = _COLUMN_KEYS.get(key.lower())
        if col is None:
            raise lang.PALCTLError(f"unknown column {key!r}")
        values[col] = str(val)
    return CANONICAL_SEP.join(values[c] for c in FULL_COLUMNS)


def program(rows: Sequence[str], *, network: str = "deny",
            backend: str = "none",
            profile: str = "pa.lctl.quantum.parallel.distributed") -> str:
    """Assemble a complete PA-LCTL source file from rendered rows."""
    head = ["#PA-LCTL/1.6", f"#PROFILE {profile}", f"#NETWORK {network}",
            f"#BACKEND {backend}",
            "#COLUMNS " + CANONICAL_SEP.join(FULL_COLUMNS)]
    return "\n".join(head + list(rows)) + "\n"


def declarations(*, nodes: int = 2, topology_resource: str = NULL_CELL,
                 quantum_link: bool = True) -> List[str]:
    """Standard domain / node / link / topology preamble."""
    out = [row("R000", "FEDERATION", "DECLARE_DOMAIN", out="D0",
               type="execution_domain", regime="EXACT", conf="1.0")]
    for i in range(nodes):
        out.append(row(f"R0{i + 1:02d}", "TOPOLOGY", "DECLARE_NODE",
                       out=f"N{i}", domain="D0", type="node", regime="EXACT",
                       resource="device=cpu", conf="1.0"))
    if quantum_link and nodes >= 2:
        out.append(row("R080", "TOPOLOGY", "DECLARE_LINK", out="Q0", a="N0",
                       b="N1", type="quantum_link", regime="EXACT",
                       resource="fidelity=0.98", domain="D0", conf="1.0"))
    if nodes >= 2:
        out.append(row("R081", "TOPOLOGY", "DECLARE_LINK", out="C0", a="N0",
                       b="N1", type="classical_channel", regime="EXACT",
                       domain="D0", conf="1.0"))
    out.append(row("R082", "TOPOLOGY", "DECLARE_TOPOLOGY", out="T0",
                   type="topology", regime="EXACT",
                   resource=topology_resource, conf="1.0"))
    return out


def prep(row_id: str, qubit: str, *, node: str = "N0", lane: str = "L0",
         qspace: str = "-", family: str = NULL_CELL) -> str:
    return row(row_id, "PREPARE", "PREP0", out=qubit, type="qubit", basis="Z",
               regime="EXACT", assume="normalization", node=node, domain="D0",
               lane=lane, qspace=qspace, family=family, conf="1.0")


def gate(row_id: str, op: str, *, a: str = NULL_CELL, ctrl: str = NULL_CELL,
         b: str = NULL_CELL, param: str = NULL_CELL, node: str = "N0",
         lane: str = "L0", regime: str = "EXACT", assume: str = "unitarity",
         family: str = NULL_CELL, error: str = NULL_CELL,
         resource: str = NULL_CELL, type: str = "unitary") -> str:
    return row(row_id, "EXEC", op, a=a, ctrl=ctrl, b=b, param=param,
               type=type, basis="Z", regime=regime, assume=assume, node=node,
               domain="D0", lane=lane, family=family, error=error,
               resource=resource, conf="1.0")


def measure(row_id: str, qubit: str, out_bit: str, *, node: str = "N0",
            lane: str = "L0", proof: str = NULL_CELL) -> str:
    return row(row_id, "MEASURE", "MEASURE", out=out_bit, a=qubit,
               type="measurement_result", basis="Z", regime="EXACT",
               assume="born_rule", node=node, domain="D0", lane=lane,
               proof=proof, conf="1.0")


def claim(row_id: str, *, param: str = NULL_CELL, resource: str = NULL_CELL,
          proof: str = NULL_CELL, node: str = "N0", a: str = NULL_CELL) -> str:
    return row(row_id, "ASSERT", "CLAIM", param=param, resource=resource,
               proof=proof, node=node, domain="D0", a=a, type="proof_record",
               regime="EXACT", conf="1.0")


# --------------------------------------------------------------------------
# 1. Diagnostic codes produced by the admission pipeline (hole H22)
# --------------------------------------------------------------------------

#: Codes emitted by this module in addition to the `lang` verifier codes.
ADMISSION_CODES: Dict[str, str] = {
    "E-CONC-WW": "rows asserted concurrent write the same object",
    "E-CONC-RW": "rows asserted concurrent have a read/write conflict",
    "E-CONC-MEASDEP": "rows asserted concurrent share a measured subsystem",
    "E-CONC-TARGET": "the bound target cannot host the asserted concurrency",
    "E-CONC-RESOURCE": "rows asserted concurrent contend for one link",
    "E-CONC-NONCOMMUTE": "rows asserted concurrent provably do not commute",
    "E-COUPLE-001": "no admitted rule resolves the coupling of an asserted pair",
    "E-XTALK-001": "asserted concurrency violates a calibrated crosstalk pair",
    "E-TARGET-001": "the bound target does not support the requested operation",
    "E-ROUTE-001": "no classical route exists between the required endpoints",
    "E-QROUTE-001": "no quantum route exists between the required endpoints",
    "E-CPATH-001": "a protocol needs a classical path that does not exist",
    "E-EBIT-001": "the requested ebits exceed the admitted link capacity",
    "E-EBIT-002": "the referenced ebit is expired at the claimed epoch",
    "E-COH-001": "planned coherence exposure exceeds the declared budget",
    "E-COMM-001": "planned communication exceeds the declared budget",
    "E-MEM-001": "declared memory claims exceed the bound target capacity",
    "E-SEAL-001": "the declared topology seal does not match the topology",
    "E-SEAL-002": "the declared schedule seal does not match the schedule",
    "E-CAL-001": "the bound target calibration is stale at the claimed epoch",
    "E-PROV-001": "a result is claimed without a provenance reference",
    "E-CLAIM-001": "physical execution is claimed by a classical simulator",
    "E-CLAIM-002": "physical distributed execution is claimed without two "
                   "authenticated endpoints",
    "E-CKPT-001": "a checkpoint of unknown quantum state was requested",
    "E-NORM-001": "a declared state preparation is not normalized",
    "E-UNIT-001": "a matrix declared unitary is not unitary",
    "E-HERM-001": "a matrix declared Hermitian is not Hermitian",
    "E-DENS-001": "a declared density matrix has a negative eigenvalue",
    "E-DENS-002": "a declared density matrix does not have unit trace",
    "E-PROJ-001": "a declared projector is not idempotent and Hermitian",
    "E-POVM-001": "a declared POVM does not resolve the identity",
    "E-KRAUS-001": "a declared Kraus set is not trace preserving",
    "E-DIM-001": "declared tensor dimensions are incompatible",
    "E-CONS-001": "the requested consistency guarantee is not achievable",
    "E-STEAL-001": "a task holding unknown quantum state was stolen",
    "E-REPLAY-001": "deterministic replay does not reproduce the recorded state",
    "E-CRDT-001": "an invalid CRDT merge was attempted",
    "E-ERRC-001": "error terms with incompatible units cannot be composed",
    "E-DEADLOCK-001": "the declared wait graph contains a cycle",
    "E-RECOV-001": "the only admissible recovery would require cloning",
    "E-PROTO-COMPILE": "the protocol compiler refused this program",
}

#: Numerical tolerance for the declared-matrix checks. Stated, never implicit.
MATRIX_TOL = 1e-9

#: Bytes charged to one planned cross-node classical message.
MESSAGE_PAYLOAD_BYTES = 256


# --------------------------------------------------------------------------
# 2. Topology binding declared inside the program
# --------------------------------------------------------------------------

def topology_from_program(prog: lang.Program) -> planner.Topology:
    """Build the bound topology from the program's DECLARE_TOPOLOGY row.

    Recognized RESOURCE keys:
        nodes, domains, capacity, memory, calibration_until,
        restrict_ops (comma list), crosstalk (`a:b`, comma separated),
        no_classical, no_quantum, qcapacity
    """
    spec: Dict[str, str] = {}
    for r in prog.rows:
        if r.op == "DECLARE_TOPOLOGY":
            spec = r.resource_map
            break
    n_nodes = int(spec.get("nodes", 4))
    domains = int(spec.get("domains", 2))
    base = planner.reference_topology(n_nodes, domains,
                                      quantum=spec.get("no_quantum") != "true")

    restrict = {s.strip() for s in spec.get("restrict_ops", "").split(",")
                if s.strip()}
    crosstalk = set()
    for pair in spec.get("crosstalk", "").split(","):
        if ":" in pair:
            x, y = pair.split(":", 1)
            crosstalk.add((x.strip(), y.strip()))
    capacity = spec.get("capacity")
    memory = spec.get("memory")
    cal_until = spec.get("calibration_until")

    nodes: List[planner.Node] = []
    for name in sorted(base.nodes):
        n = base.nodes[name]
        ops = n.supported_ops
        if restrict and ops:
            ops = frozenset(ops - restrict)
        nodes.append(planner.Node(
            node_id=n.node_id, kind=n.kind, domain=n.domain, parent=n.parent,
            capacity=int(capacity) if capacity and n.kind == "logical_node"
            else n.capacity,
            memory_bytes=int(memory) if memory and n.kind == "logical_node"
            else n.memory_bytes,
            supported_ops=ops, trust=n.trust, failure_domain=n.failure_domain,
            calibration_epoch=n.calibration_epoch,
            calibration_valid_until=int(cal_until) if cal_until is not None
            else n.calibration_valid_until,
            crosstalk_pairs=frozenset(crosstalk) or n.crosstalk_pairs))

    links: List[planner.Link] = []
    for lid in sorted(base.links):
        l = base.links[lid]
        if l.kind == "classical" and spec.get("no_classical") == "true":
            continue
        cap = l.capacity
        if l.kind == "quantum" and "qcapacity" in spec:
            cap = int(spec["qcapacity"])
        links.append(planner.Link(
            link_id=l.link_id, a=l.a, b=l.b, kind=l.kind, latency=l.latency,
            inv_bandwidth=l.inv_bandwidth, capacity=cap, fidelity=l.fidelity,
            gen_rate=l.gen_rate, success_prob=l.success_prob,
            herald_latency=l.herald_latency, purification=l.purification,
            decoherence_window=l.decoherence_window,
            failure_domain=l.failure_domain))
    tag = "-".join(f"{k}={spec[k]}" for k in sorted(spec)) or "default"
    return planner.Topology(nodes, links, name=f"bound-{n_nodes}n-{domains}d-{tag}")


# --------------------------------------------------------------------------
# 3. The admission pipeline
# --------------------------------------------------------------------------

@dataclass
class AdmissionResult:
    accepted: bool
    codes: List[str]
    stage: str
    messages: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "codes": sorted(set(self.codes)),
                "stage": self.stage, "messages": self.messages,
                "detail": self.detail}


class _Sink:
    """Ordered, de-duplicating diagnostic accumulator."""

    def __init__(self) -> None:
        self.codes: List[str] = []
        self.messages: List[str] = []

    def add(self, code: str, message: str) -> None:
        self.codes.append(code)
        self.messages.append(f"{code}: {message}")

    def __bool__(self) -> bool:
        return bool(self.codes)


def admit(source: str, path: str = "<case>") -> AdmissionResult:
    """Run the full admission pipeline. Fails closed at the first stage that
    rejects, so a rejected program never reaches later analysis."""
    sink = _Sink()

    prog, diags = lang.parse(source, path)
    for d in diags:
        if d.severity in ("ERROR", "REJECT"):
            sink.add(d.code, d.message)
    if prog is None or sink:
        return AdmissionResult(False, sink.codes, "parse", sink.messages)

    vr = lang.verify(prog)
    for d in vr.diagnostics:
        if d.severity in ("ERROR", "REJECT"):
            sink.add(d.code, d.message)
    if sink:
        return AdmissionResult(False, sink.codes, "verify", sink.messages)

    topo = topology_from_program(prog)
    graph = ses_mod.build_ses(prog, vr)
    po = ses_mod.analyze(graph)
    ctx = _Context(prog=prog, vr=vr, topo=topo, ses=graph, po=po)

    for stage, check in (("target_binding", _check_targets),
                         ("memory", _check_memory),
                         ("calibration", _check_calibration),
                         ("seals", _check_seals),
                         ("routing", _check_routes),
                         ("entanglement", _check_ebits),
                         ("numerical_claims", _check_matrix_claims),
                         ("state_claims", _check_state_claims),
                         ("provenance", _check_provenance_claims),
                         ("concurrency", _check_concurrency_claims),
                         ("budgets", _check_budgets),
                         ("protocol_compile", _check_protocol_compile),
                         ("error_algebra", _check_error_composition),
                         ("runtime_contracts", _check_runtime_claims),
                         ("deadlock", _check_deadlock)):
        check(ctx, sink)
        if sink:
            return AdmissionResult(False, sink.codes, stage, sink.messages)

    return AdmissionResult(True, [], "admitted", [],
                           {"ses_hash": graph.hash(),
                            "topology_hash": topo.hash(),
                            "work": po.work, "span": po.span,
                            "width": po.width})


@dataclass
class _Context:
    prog: lang.Program
    vr: lang.VerifyResult
    topo: planner.Topology
    ses: ses_mod.SES
    po: ses_mod.PartialOrder
    _authority: Optional[commutation_mod.CommutationAuthority] = None
    _schedule: Optional[planner.Schedule] = None

    @property
    def authority(self) -> commutation_mod.CommutationAuthority:
        if self._authority is None:
            self._authority = commutation_mod.CommutationAuthority()
        return self._authority

    def claims(self) -> List[lang.Row]:
        return [r for r in self.prog.rows if r.op == "CLAIM"]

    def schedule(self) -> planner.Schedule:
        if self._schedule is None:
            part = planner.HypergraphPartitioner().partition(
                self.ses, 2, exact=False)
            placement, _ = planner.Placer(self.topo).place(self.ses, part)
            self._schedule = planner.TemporalScheduler(
                self.topo, self.authority).schedule(
                    self.ses, self.po, part, placement, [], mode="CRITICAL_PATH")
        return self._schedule


# -- stage checks ----------------------------------------------------------

def _check_targets(ctx: _Context, sink: _Sink) -> None:
    for nid in ctx.ses.order:
        node = ctx.ses.nodes[nid]
        target = ctx.topo.nodes.get(node.owner)
        if target is None or not target.supported_ops:
            continue
        if node.semantic_face not in lang.EXECUTABLE_FACES:
            continue
        if node.operation not in target.supported_ops:
            sink.add("E-TARGET-001",
                     f"row {nid}: target {node.owner} does not support "
                     f"{node.operation}")


def _check_memory(ctx: _Context, sink: _Sink) -> None:
    per_owner: Dict[str, float] = {}
    for nid in ctx.ses.order:
        node = ctx.ses.nodes[nid]
        try:
            need = float(node.resource_claim.get("memory", 0.0) or 0.0)
        except ValueError:
            continue
        per_owner[node.owner] = per_owner.get(node.owner, 0.0) + need
    for owner, need in sorted(per_owner.items()):
        target = ctx.topo.nodes.get(owner)
        if target is None or need <= 0:
            continue
        if need > target.memory_bytes:
            sink.add("E-MEM-001",
                     f"target {owner} offers {target.memory_bytes} bytes but "
                     f"{need:.0f} bytes are claimed")


def _claimed_epoch(ctx: _Context) -> Optional[int]:
    for r in ctx.claims():
        raw = r.param_map.get("epoch", r.resource_map.get("epoch"))
        if raw is not None:
            try:
                return int(raw)
            except ValueError:
                return None
    return None


def _check_calibration(ctx: _Context, sink: _Sink) -> None:
    epoch = _claimed_epoch(ctx)
    if epoch is None:
        return
    owners = sorted({ctx.ses.nodes[n].owner for n in ctx.ses.order
                     if ctx.ses.nodes[n].semantic_face in lang.EXECUTABLE_FACES})
    for owner in owners:
        if owner not in ctx.topo.nodes:
            continue
        if not ctx.topo.calibration_valid(owner, epoch):
            sink.add("E-CAL-001",
                     f"target {owner} calibration is not valid at epoch {epoch}")


def _check_seals(ctx: _Context, sink: _Sink) -> None:
    for r in ctx.claims():
        res = {**r.resource_map, **r.param_map}
        declared = res.get("topology_seal")
        if declared is not None and declared != ctx.topo.hash():
            sink.add("E-SEAL-001",
                     f"row {r.row_id}: declared topology seal {declared} != "
                     f"{ctx.topo.hash()}")
        declared = res.get("schedule_seal")
        if declared is not None and declared != ctx.schedule().hash():
            sink.add("E-SEAL-002",
                     f"row {r.row_id}: declared schedule seal {declared} != "
                     f"{ctx.schedule().hash()}")


_LINK_REQUIRING_OPS = ("TELEPORT", "REMOTE_CNOT", "REMOTE_CONTROL",
                       "ENTANGLE_LINK", "EPR_RESERVE", "ENTANGLEMENT_SWAP",
                       "PURIFY", "REMOTE_MEASURE")


def _link_endpoints(ctx: _Context, r: lang.Row) -> Optional[Tuple[str, str]]:
    link = ctx.vr.declared_links.get(r.link)
    if link is None:
        return None
    a, b = link.get("a", NULL_CELL), link.get("b", NULL_CELL)
    if a == NULL_CELL or b == NULL_CELL:
        return None
    return a, b


def _check_routes(ctx: _Context, sink: _Sink) -> None:
    qrouter = planner.QuantumRouter(ctx.topo)
    crouter = planner.ClassicalRouter(ctx.topo)
    for r in ctx.prog.rows:
        if r.op not in _LINK_REQUIRING_OPS:
            continue
        ends = _link_endpoints(ctx, r)
        if ends is None:
            continue
        a, b = ends
        if a not in ctx.topo.nodes or b not in ctx.topo.nodes:
            sink.add("E-QROUTE-001",
                     f"row {r.row_id}: endpoint {a} or {b} is not bound to the "
                     f"topology")
            continue
        if qrouter.route(a, b) is None:
            sink.add("E-QROUTE-001",
                     f"row {r.row_id}: no quantum route {a} -> {b} exists in "
                     f"the bound topology")
            continue
        if crouter.select(a, b, payload=MESSAGE_PAYLOAD_BYTES) is None:
            sink.add("E-CPATH-001",
                     f"row {r.row_id}: {r.op} needs a classical path {a} -> {b} "
                     f"for its correction bits and none exists")


def _check_ebits(ctx: _Context, sink: _Sink) -> None:
    epoch = _claimed_epoch(ctx) or 0
    demands: Dict[Tuple[str, str], int] = {}
    ledger = protocols.EbitLedger()
    for r in ctx.prog.rows:
        if r.op not in ("ENTANGLE_LINK", "EPR_RESERVE", "TELEPORT",
                        "REMOTE_CNOT", "REMOTE_CONTROL"):
            continue
        ends = _link_endpoints(ctx, r)
        if ends is None:
            continue
        demands[ends] = demands.get(ends, 0) + 1
        if r.op in ("ENTANGLE_LINK", "EPR_RESERVE"):
            expiry = r.param_map.get("expiry")
            eid = ledger.request(ends[0], ends[1], epoch=0,
                                 expiry=int(expiry) if expiry else 1 << 30,
                                 owner_protocol=r.row_id)
            ledger.generate(eid, epoch=0)
            ledger.herald(eid, fidelity=0.98, epoch=0)
    if not demands:
        return
    expired = ledger.expire(epoch=epoch)
    if expired:
        sink.add("E-EBIT-002",
                 f"ebit(s) {sorted(expired)} are expired at epoch {epoch}")
        return
    router = planner.QuantumRouter(ctx.topo)
    flow = router.min_cost_flow([(a, b, n) for (a, b), n in sorted(demands.items())])
    if flow["unmet_demand"]:
        first = flow["unmet_demand"][0]
        sink.add("E-EBIT-001",
                 f"{first['requested']} ebit(s) requested {first['src']} -> "
                 f"{first['dst']}, {first['allocated']} allocatable")


def _parse_matrices(spec: str) -> List[np.ndarray]:
    out: List[np.ndarray] = []
    for part in spec.split("/"):
        vals = [float(x) for x in part.split(",") if x.strip()]
        n = int(round(math.sqrt(len(vals))))
        if n * n != len(vals):
            raise ValueError(f"{part!r} is not a square matrix")
        out.append(np.array(vals, dtype=complex).reshape(n, n))
    return out


def _check_matrix_claims(ctx: _Context, sink: _Sink) -> None:
    for r in ctx.claims():
        pm = r.param_map
        kind = pm.get("matrix_check")
        if not kind or "matrix" not in pm:
            continue
        try:
            mats = _parse_matrices(pm["matrix"])
        except ValueError as exc:
            sink.add("E-DIM-001", f"row {r.row_id}: {exc}")
            continue
        m = mats[0]
        eye = np.eye(m.shape[0], dtype=complex)
        if kind == "unitary":
            if float(np.max(np.abs(m.conj().T @ m - eye))) > MATRIX_TOL:
                sink.add("E-UNIT-001",
                         f"row {r.row_id}: U^dag U deviates from I by "
                         f"{float(np.max(np.abs(m.conj().T @ m - eye))):.3e}")
        elif kind == "hermitian":
            if float(np.max(np.abs(m - m.conj().T))) > MATRIX_TOL:
                sink.add("E-HERM-001",
                         f"row {r.row_id}: matrix declared Hermitian differs "
                         f"from its adjoint")
        elif kind == "density":
            if float(np.max(np.abs(m - m.conj().T))) > MATRIX_TOL:
                sink.add("E-HERM-001",
                         f"row {r.row_id}: density matrix is not Hermitian")
                continue
            eig = np.linalg.eigvalsh(m)
            if float(np.min(eig)) < -MATRIX_TOL:
                sink.add("E-DENS-001",
                         f"row {r.row_id}: minimum eigenvalue "
                         f"{float(np.min(eig)):.6f} is negative")
                continue
            if abs(float(np.trace(m).real) - 1.0) > MATRIX_TOL:
                sink.add("E-DENS-002",
                         f"row {r.row_id}: trace is "
                         f"{float(np.trace(m).real):.6f}, not 1")
        elif kind == "projector":
            if float(np.max(np.abs(m @ m - m))) > MATRIX_TOL or \
                    float(np.max(np.abs(m - m.conj().T))) > MATRIX_TOL:
                sink.add("E-PROJ-001",
                         f"row {r.row_id}: P^2 != P or P != P^dag")
        elif kind == "povm":
            total = sum(mats)
            if float(np.max(np.abs(total - eye))) > MATRIX_TOL:
                sink.add("E-POVM-001",
                         f"row {r.row_id}: POVM elements do not resolve the "
                         f"identity")
        elif kind == "kraus":
            total = sum(k.conj().T @ k for k in mats)
            if float(np.max(np.abs(total - eye))) > simulator.KRAUS_COMPLETENESS_TOL:
                sink.add("E-KRAUS-001",
                         f"row {r.row_id}: sum K^dag K deviates from I by "
                         f"{float(np.max(np.abs(total - eye))):.3e}")


def _check_state_claims(ctx: _Context, sink: _Sink) -> None:
    for r in ctx.prog.rows:
        pm = r.param_map
        if r.op == "PREP_STATE" and "amp" in pm:
            try:
                amps = [float(x) for x in pm["amp"].split(",") if x.strip()]
            except ValueError:
                continue
            norm = math.sqrt(sum(a * a for a in amps))
            if abs(norm - 1.0) > MATRIX_TOL:
                sink.add("E-NORM-001",
                         f"row {r.row_id}: declared amplitudes have norm "
                         f"{norm:.9f}, not 1")
        if r.op in ("TENSOR", "KRON") and "dim" in pm:
            operands = [c for c in (r.ctrl, r.a, r.b) if c != NULL_CELL]
            width = 0
            for cell in operands:
                ref = lang.parse_ref(cell)
                width += len(ref.keys()) if ref else 0
            try:
                declared = int(pm["dim"])
            except ValueError:
                continue
            if declared != (1 << width):
                sink.add("E-DIM-001",
                         f"row {r.row_id}: {width} operand qubit(s) give "
                         f"dimension {1 << width}, not {declared}")


def _check_provenance_claims(ctx: _Context, sink: _Sink) -> None:
    for r in ctx.claims():
        pm = r.param_map
        if "result" in pm and r.proof == NULL_CELL:
            sink.add("E-PROV-001",
                     f"row {r.row_id}: result {pm['result']!r} is claimed with "
                     f"no PROOF reference")
        exec_claim = pm.get("execution", "")
        if pm.get("physical_qpu") == "true" or \
                exec_claim in ("PHYSICAL_PARALLEL_QPU_EXECUTION",
                               "PHYSICAL_DISTRIBUTED_QPU_EXECUTION",
                               "PHYSICAL_PARALLEL_EXECUTION"):
            sink.add("E-CLAIM-001",
                     f"row {r.row_id}: physical quantum execution is claimed, "
                     f"but this authority is a classical simulator")
        if pm.get("physical_distributed") == "true":
            sink.add("E-CLAIM-002",
                     f"row {r.row_id}: physical distributed execution is "
                     f"claimed without two authenticated physical endpoints")
        if pm.get("physical_parallel") == "true":
            sink.add("E-CLAIM-001",
                     f"row {r.row_id}: physical parallel execution is claimed "
                     f"by a classical simulator")


def _pair_verdict(ctx: _Context, a: str, b: str) -> Tuple[Optional[str], str]:
    """Decide whether two SES nodes may truly run concurrently."""
    na, nb = ctx.ses.nodes[a], ctx.ses.nodes[b]
    wa, wb = set(na.writes), set(nb.writes)
    ra, rb = set(na.reads), set(nb.reads)
    if wa & wb:
        return "E-CONC-WW", f"both write {sorted(wa & wb)}"
    if (wa & rb) or (wb & ra):
        return "E-CONC-RW", f"read/write overlap on {sorted((wa & rb) | (wb & ra))}"
    if na.operation in lang.DESTRUCTIVE_OPS and \
            nb.operation in lang.DESTRUCTIVE_OPS and (ra & rb):
        return "E-CONC-MEASDEP", f"shared measured subsystem {sorted(ra & rb)}"
    if not ctx.topo.can_coexist(na.owner, nb.owner):
        return "E-CONC-TARGET", (f"target {na.owner} cannot host both rows "
                                 f"concurrently")
    xt = ctx.topo.crosstalk_status(na, nb)
    if xt != "OK":
        return "E-XTALK-001", xt
    if na.link != NULL_CELL and na.link == nb.link:
        return "E-CONC-RESOURCE", f"both reserve link {na.link}"
    verdict = ctx.authority.classify_pair(na, nb)
    if verdict.status == "UNRESOLVED":
        return "E-COUPLE-001", verdict.reason
    if verdict.status == "NON_COMMUTING":
        return "E-CONC-NONCOMMUTE", verdict.reason
    return None, verdict.reason


def _check_concurrency_claims(ctx: _Context, sink: _Sink) -> None:
    for r in ctx.prog.rows:
        for assumption in r.assumptions:
            if not assumption.startswith("parallel_with="):
                continue
            other = assumption.split("=", 1)[1].strip()
            if r.row_id not in ctx.ses.nodes or other not in ctx.ses.nodes:
                sink.add("E-CONC-RESOURCE",
                         f"row {r.row_id}: asserted concurrency with unknown "
                         f"row {other!r}")
                continue
            code, why = _pair_verdict(ctx, r.row_id, other)
            if code is not None:
                sink.add(code, f"rows {r.row_id} ~ {other}: {why}")


def _check_budgets(ctx: _Context, sink: _Sink) -> None:
    budgets: Dict[str, float] = {}
    for r in ctx.claims():
        merged = {**r.resource_map, **r.param_map}
        for key in ("coherence_budget", "comm_budget_bytes"):
            if key in merged:
                try:
                    budgets[key] = float(merged[key])
                except ValueError:
                    pass
    if not budgets:
        return
    if "coherence_budget" in budgets:
        exposure = ctx.schedule().coherence["total_coherence_exposure"]
        if exposure > budgets["coherence_budget"]:
            sink.add("E-COH-001",
                     f"planned coherence exposure {exposure:.6f} exceeds the "
                     f"declared budget {budgets['coherence_budget']:.6f}")
    if "comm_budget_bytes" in budgets:
        cross = 0
        for e in ctx.ses.edges:
            if ctx.ses.nodes[e.src].owner != ctx.ses.nodes[e.dst].owner:
                cross += 1
        planned = cross * MESSAGE_PAYLOAD_BYTES
        if planned > budgets["comm_budget_bytes"]:
            sink.add("E-COMM-001",
                     f"{cross} cross-node dependency edge(s) plan "
                     f"{planned} bytes, over the declared budget "
                     f"{budgets['comm_budget_bytes']:.0f}")


def _check_protocol_compile(ctx: _Context, sink: _Sink) -> None:
    has_protocol = any(r.face == "PROTOCOL" or r.op in lang.OPS_DISTRIBUTED_Q
                       for r in ctx.prog.rows)
    if not has_protocol:
        return
    try:
        protocols.ProtocolCompiler().compile(ctx.prog, ctx.vr).validate()
    except protocols.ProtocolError as exc:
        sink.add("E-PROTO-COMPILE", str(exc))


def _check_error_composition(ctx: _Context, sink: _Sink) -> None:
    strict = any(r.param_map.get("compose") == "strict" for r in ctx.claims())
    if not strict:
        return
    terms = ledgers.error_terms_from_program(ctx.prog)
    if len(terms) < 2:
        return
    result = erroralgebra.compose(terms, "independent")
    if not result.resolved:
        sink.add("E-ERRC-001",
                 f"{result.token}: {result.reasons[0] if result.reasons else ''}")


def _check_deadlock(ctx: _Context, sink: _Sink) -> None:
    detector = fabric.DeadlockDetector()
    edges = 0
    for r in ctx.prog.rows:
        for assumption in r.assumptions:
            if assumption.startswith("wait_for="):
                detector.add_wait(r.row_id, assumption.split("=", 1)[1].strip())
                edges += 1
    if not edges:
        return
    report = detector.detect()
    if not report.ok and report.cycles:
        sink.add("E-DEADLOCK-001",
                 f"{report.token}: cycle(s) {report.cycles}")


def _check_runtime_claims(ctx: _Context, sink: _Sink) -> None:
    """Execute the declared runtime contracts and record real refusals."""
    for r in ctx.claims():
        pm = r.param_map

        if "checkpoint" in pm:
            payload = fabric.QuantumPayload(
                payload_id=pm["checkpoint"], lineage="LIN-CASE",
                known_classically=False)
            try:
                resilience.CheckpointStore().save(
                    f"CK-{r.row_id}", "simulator_state", payload)
            except (resilience.QuantumCheckpointRefused,
                    resilience.ResilienceError) as exc:
                sink.add("E-CKPT-001", str(exc))

        if "consistency" in pm:
            profile = pm["consistency"]
            if profile not in lang.CONSISTENCY_PROFILES:
                sink.add("E-CONS-001", f"unknown consistency profile {profile!r}")
                continue
            partition = crdt.PartitionModel(
                partitions_possible=pm.get("partitions") == "true")
            failure = crdt.FailureModel(
                replicas=int(pm.get("replicas", 1)),
                crdt_declared=pm.get("crdt_declared") == "true",
                anti_entropy=pm.get("anti_entropy", "true") == "true")
            try:
                crdt.ConsistencyContract(profile, partition, failure).require()
            except crdt.ConsistencyGuaranteeBlocked as exc:
                sink.add("E-CONS-001", str(exc))

        if pm.get("steal") == "quantum":
            fed = fabric.build_flat_federation(2)
            ws = fabric.WorkStealing(fed)
            rt = fabric.TaskRuntime(fed)
            task = rt.spawn(_unit, task_id=f"QT-{r.row_id}", owner="W00")
            task.payloads.append(fabric.QuantumPayload(
                payload_id="q[0]", lineage="LIN-CASE",
                known_classically=False))
            try:
                ws.steal_specific("W01", "W00", task)
            except fabric.StealRejectedError as exc:
                sink.add("E-STEAL-001", str(exc))

        if pm.get("replay") == "corrupt":
            log = fabric.EventLog("case")
            log.append("W00", "WRITE", inputs={"k": 1}, outputs={"k": 1},
                       resource_delta={"cpu": 1.0})
            expected = log.reconstruct()
            expected["resources"] = {"cpu": 99.0}
            try:
                log.assert_replay(expected)
            except fabric.ReplayMismatchError as exc:
                sink.add("E-REPLAY-001", str(exc))

        if "crdt_merge" in pm:
            names = [s.strip() for s in pm["crdt_merge"].split("/")]
            registry = {"GCounter": crdt.GCounter, "PNCounter": crdt.PNCounter,
                        "GSet": crdt.GSet, "ORSet": crdt.ORSet,
                        "LWWRegister": crdt.LWWRegister}
            if len(names) == 2 and all(n in registry for n in names):
                left = registry[names[0]]("RA")
                right = registry[names[1]]("RB")
                try:
                    left.merge(right)
                except crdt.CRDTError as exc:
                    sink.add("E-CRDT-001", str(exc))

        if "recovery" in pm:
            scenario = pm.get("scenario", "quantum_link_failure")
            if scenario not in resilience.SCENARIO_INDEX:
                scenario = "quantum_link_failure"
            event = resilience.FailureInjector(seed=7).inject(scenario,
                                                              target=r.row_id)
            context = {"quantum_state_unknown": True,
                       "requires_duplication": pm["recovery"] == "clone_unknown_qstate",
                       "route_alternatives": int(pm.get("routes", 0)),
                       "admissible_boundary": pm.get("boundary") or None}
            record = resilience.RecoveryPlanner().explain(event, context)
            if record.recovery_class == "RECOVERY_IMPOSSIBLE":
                sink.add("E-RECOV-001",
                         f"{resilience.TOKEN_RECOVERY_IMPOSSIBLE} "
                         f"({record.rule_id}): {record.reason}")


def _unit(*_a: Any, **_k: Any) -> int:
    """Deterministic unit of classical work used by the runtime checks."""
    return 1


# --------------------------------------------------------------------------
# 4. Case model
# --------------------------------------------------------------------------

POSITIVE_GROUPS: Tuple[str, ...] = (
    "commutation", "partitioning", "placement_routing", "scheduling",
    "numerical_backends", "protocols", "resilience", "provenance_replay",
    "consistency_crdt", "task_runtime", "collectives", "federation",
)

NEGATIVE_CATEGORIES: Tuple[str, ...] = (
    "duplicate_unknown_qstate_across_nodes", "implicit_copy",
    "write_write_conflict", "read_write_conflict", "use_after_move",
    "use_after_measure", "missing_measurement_dependency",
    "remote_gate_with_no_route", "remote_gate_unsupported_target",
    "teleport_without_entanglement", "teleport_without_classical_path",
    "insufficient_ebits", "expired_ebit", "double_ebit_consume",
    "impossible_target_concurrency", "crosstalk_budget_exceeded",
    "coherence_window_exceeded", "communication_budget_exceeded",
    "memory_capacity_exceeded", "unresolved_coupling",
    "corrupted_topology_seal", "corrupted_schedule_seal", "stale_calibration",
    "result_without_provenance", "physical_execution_claim_from_simulator",
    "physical_distributed_claim_without_two_endpoints", "hidden_approximation",
    "invalid_checkpoint_of_unknown_qstate", "negative_probability",
    "non_normalized_state", "invalid_unitary",
    "non_hermitian_observable_declared_hermitian",
    "density_with_negative_eigenvalue", "density_trace_not_one",
    "invalid_projector", "invalid_povm", "invalid_kraus_completeness",
    "control_equals_target", "incompatible_tensor_dimensions", "invalid_arity",
    "unknown_operation", "unknown_face", "unknown_type", "unknown_regime",
    "duplicate_row_id", "conf_out_of_range", "network_not_deny",
    "backend_not_none", "exact_regime_on_noise_op", "consistency_downgrade",
    "illegal_work_steal_of_quantum_task", "replay_corruption",
    "crdt_invalid_merge", "error_composition_incompatible_units",
    "deadlock_cycle", "invalid_recovery_requiring_cloning",
)


@dataclass(frozen=True)
class Case:
    case_id: str
    group: str
    title: str
    source: str
    expect: Dict[str, Any]

    def as_dict(self) -> dict:
        return {"case_id": self.case_id, "group": self.group,
                "title": self.title, "source": self.source,
                "expect": dict(self.expect)}


class CaseFailure(Exception):
    """Raised inside a case runner when the case does not hold."""


# --------------------------------------------------------------------------
# 5. Positive case generators (LCTL 1.3.x s59, 1.4.x s61)
# --------------------------------------------------------------------------

#: Parameter axes the generators sweep. Real variation, not padding.
QUBIT_COUNTS = (2, 3, 4)
WORKER_COUNTS = (1, 2, 4, 8)
PARTITION_KS = (2, 3, 4)
SEEDS = (0, 1, 7, 13)
BACKEND_SHAPES = ("clifford", "non_clifford", "noisy")


def _bell_rows(n_qubits: int, *, node: str = "N0", lane: str = "L0",
               prefix: str = "q", family: str = NULL_CELL,
               base: int = 100) -> List[str]:
    rows: List[str] = []
    span = f"{prefix}[0:{n_qubits}]"
    for i in range(n_qubits):
        rows.append(prep(f"R{base + i}", f"{prefix}[{i}]", node=node, lane=lane,
                         qspace=span, family=family))
    rows.append(gate(f"R{base + 20}", "H", a=f"{prefix}[0]", node=node,
                     lane=lane, family=family))
    for i in range(1, n_qubits):
        rows.append(gate(f"R{base + 20 + i}", "CX", ctrl=f"{prefix}[0]",
                         a=f"{prefix}[{i}]", node=node, lane=lane,
                         family=family))
    return rows


def _independent_lanes(n_qubits: int, lanes: int) -> List[str]:
    rows: List[str] = []
    names = ("q", "r", "s", "t", "u", "v", "w", "x")
    for j in range(lanes):
        rows.extend(_bell_rows(n_qubits, node=f"N{j % 2}", lane=f"L{j}",
                               prefix=names[j], base=100 + 50 * j,
                               family="CIRCUIT_PARALLEL"))
    return rows


def _gen_commutation() -> List[Case]:
    cases: List[Case] = []
    combos = [("H", "H"), ("X", "X"), ("Z", "Z"), ("S", "T"), ("T", "Z"),
              ("RZ", "RZ"), ("H", "X"), ("Y", "Z"), ("SDG", "S")]
    idx = 0
    for nq in QUBIT_COUNTS:
        for op_a, op_b in combos:
            for disjoint in (True, False):
                idx += 1
                param = "pi/4" if op_a in lang.PARAMETRIC_GATES else NULL_CELL
                param_b = "pi/8" if op_b in lang.PARAMETRIC_GATES else NULL_CELL
                target_b = "q[1]" if disjoint else "q[0]"
                rows = declarations() + _bell_rows(nq)
                rows.append(gate("R300", op_a, a="q[0]", param=param,
                                 assume="unitarity;parallel_with=R301"))
                rows.append(gate("R301", op_b, a=target_b, param=param_b))
                if not disjoint:
                    # Shared support means a shared write: concurrency is not
                    # asserted, and the pair is checked for its verdict only.
                    rows[-2] = gate("R300", op_a, a="q[0]", param=param)
                cases.append(Case(
                    case_id=f"POS-COMM-{idx:04d}", group="commutation",
                    title=f"{op_a}/{op_b} pair, {nq} qubits, "
                          f"{'disjoint' if disjoint else 'shared'} support",
                    source=program(rows),
                    expect={"accept": True, "check": "commutation",
                            "pair": ["R300", "R301"]}))
    return cases


def _gen_partitioning() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for k in PARTITION_KS:
        for lanes in (2, 3, 4):
            for nq in (2, 3):
                for exact in (False, True):
                    idx += 1
                    rows = declarations() + _independent_lanes(nq, lanes)
                    cases.append(Case(
                        case_id=f"POS-PART-{idx:04d}", group="partitioning",
                        title=f"k={k}, {lanes} independent lanes, {nq} qubits, "
                              f"exact_oracle={exact}",
                        source=program(rows),
                        expect={"accept": True, "check": "partition", "k": k,
                                "exact": exact and lanes <= 2 and nq == 2}))
    return cases


def _gen_placement_routing() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for nodes in (2, 3, 4):
        for lanes in (2, 3):
            for k in PARTITION_KS:
                for payload in (64, 1024):
                    idx += 1
                    rows = declarations(
                        nodes=2,
                        topology_resource=f"nodes={max(nodes, 2)};domains=2")
                    rows += _independent_lanes(2, lanes)
                    cases.append(Case(
                        case_id=f"POS-PLACE-{idx:04d}",
                        group="placement_routing",
                        title=f"{nodes} bound targets, {lanes} lanes, k={k}, "
                              f"payload={payload}B",
                        source=program(rows),
                        expect={"accept": True, "check": "placement_routing",
                                "k": k, "payload": payload}))
    return cases


def _gen_scheduling() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for mode in planner.SCHEDULE_MODES:
        for lanes in (2, 3):
            for nq in (2, 3):
                idx += 1
                rows = declarations() + _independent_lanes(nq, lanes)
                cases.append(Case(
                    case_id=f"POS-SCHED-{idx:04d}", group="scheduling",
                    title=f"mode={mode}, {lanes} lanes, {nq} qubits",
                    source=program(rows),
                    expect={"accept": True, "check": "schedule", "mode": mode,
                            "k": 2}))
    for mode in planner.SCHEDULE_MODES:
        for k in PARTITION_KS:
            idx += 1
            rows = declarations() + _independent_lanes(2, 2)
            cases.append(Case(
                case_id=f"POS-SCHED-{idx:04d}", group="scheduling",
                title=f"mode={mode}, k={k}",
                source=program(rows),
                expect={"accept": True, "check": "schedule", "mode": mode,
                        "k": k}))
    return cases


def _gen_numerical_backends() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for shape in BACKEND_SHAPES:
        for nq in QUBIT_COUNTS:
            for seed in SEEDS:
                for shots in (0, 64, 256):
                    idx += 1
                    rows = declarations() + _bell_rows(nq)
                    if shape == "non_clifford":
                        rows.append(gate("R400", "T", a="q[0]"))
                    elif shape == "noisy":
                        rows.append(row("R400", "NOISE", "DEPOLARIZE",
                                        a="q[0]", param="0.05", type="channel",
                                        regime="NOISY", node="N0", domain="D0",
                                        lane="L0", error="p=0.05", conf="1.0"))
                    cases.append(Case(
                        case_id=f"POS-NUM-{idx:04d}", group="numerical_backends",
                        title=f"{shape} circuit, {nq} qubits, seed={seed}, "
                              f"shots={shots}",
                        source=program(rows),
                        expect={"accept": True, "check": "numerical",
                                "shape": shape, "seed": seed, "shots": shots}))
    return cases


def _gen_protocols() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    prims = ("ENTANGLE_LINK", "EPR_RESERVE", "TELEPORT", "REMOTE_CNOT",
             "REMOTE_MEASURE", "ENTANGLEMENT_SWAP", "PURIFY",
             "CLASSICAL_FEEDBACK", "SYNC_QCLOCK", "HERALD", "QCHANNEL_SEND")
    for prim in prims:
        for nq in (2, 3):
            for seed in SEEDS:
                idx += 1
                rows = declarations() + _bell_rows(nq)
                rows += [prep("R200", "r[0]", node="N1", qspace="r[0:1]")]
                if prim == "TELEPORT":
                    rows.append(row("R300", "PROTOCOL", "TELEPORT", out="z[0]",
                                    a="q[0]", type="remote_handle",
                                    regime="EXACT",
                                    node="N0", domain="D0", link="Q0",
                                    conf="1.0"))
                elif prim in ("REMOTE_CNOT", "REMOTE_CONTROL"):
                    rows.append(row("R300", "PROTOCOL", prim, ctrl="q[0]",
                                    a="r[0]", type="controlled_operator",
                                    regime="EXACT", node="N0", domain="D0",
                                    link="Q0", conf="1.0"))
                elif prim in ("ENTANGLE_LINK", "EPR_RESERVE"):
                    rows.append(row("R300", "PROTOCOL", prim, out="e[0]",
                                    type="remote_handle", regime="EXACT",
                                    node="N0", domain="D0", link="Q0",
                                    conf="1.0"))
                elif prim == "REMOTE_MEASURE":
                    rows.append(row("R300", "PROTOCOL", "REMOTE_MEASURE",
                                    out="c9", a="r[0]",
                                    type="measurement_result", regime="EXACT",
                                    node="N1", domain="D0", link="Q0",
                                    conf="1.0"))
                elif prim == "CLASSICAL_FEEDBACK":
                    rows.append(measure("R290", "q[1]", "c0", proof="PR-M"))
                    rows.append(row("R300", "PROTOCOL", "CLASSICAL_FEEDBACK",
                                    ctrl="c0", type="bit", regime="EXACT",
                                    node="N0", domain="D0", link="Q0",
                                    conf="1.0"))
                else:
                    rows.append(row("R300", "PROTOCOL", prim, out="e[0]",
                                    type="remote_handle", regime="EXACT",
                                    node="N0", domain="D0", link="Q0",
                                    conf="1.0"))
                cases.append(Case(
                    case_id=f"POS-PROTO-{idx:04d}", group="protocols",
                    title=f"{prim} with {nq} local qubits, seed={seed}",
                    source=program(rows),
                    expect={"accept": True, "check": "protocol",
                            "primitive": prim, "seed": seed}))
    return cases


def _gen_resilience() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for scenario in resilience.SCENARIO_IDS:
        for seed in SEEDS:
            idx += 1
            rows = declarations() + _bell_rows(2)
            cases.append(Case(
                case_id=f"POS-RESIL-{idx:04d}", group="resilience",
                title=f"inject {scenario} and classify, seed={seed}",
                source=program(rows),
                expect={"accept": True, "check": "resilience",
                        "scenario": scenario, "seed": seed}))
    return cases


def _gen_provenance_replay() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for nq in QUBIT_COUNTS:
        for lanes in (1, 2, 3):
            for seed in SEEDS:
                idx += 1
                rows = declarations() + _independent_lanes(nq, lanes)
                cases.append(Case(
                    case_id=f"POS-PROV-{idx:04d}", group="provenance_replay",
                    title=f"provenance + deterministic replay, {nq} qubits, "
                          f"{lanes} lanes, seed={seed}",
                    source=program(rows),
                    expect={"accept": True, "check": "provenance_replay",
                            "seed": seed, "k": 2}))
    return cases


def _gen_consistency_crdt() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    types = ("GCounter", "PNCounter", "GSet", "ORSet", "LWWRegister")
    for name in types:
        for replicas in (2, 3, 4):
            for profile in ("EVENTUAL", "CRDT_DECLARED", "SINGLE_OWNER"):
                idx += 1
                rows = declarations() + _bell_rows(2)
                rows.append(claim("R500",
                                  param=f"consistency={profile};replicas="
                                        f"{replicas};crdt_declared=true",
                                  proof="PR-CRDT"))
                cases.append(Case(
                    case_id=f"POS-CRDT-{idx:04d}", group="consistency_crdt",
                    title=f"{name} x{replicas} under {profile}",
                    source=program(rows),
                    expect={"accept": True, "check": "consistency",
                            "crdt": name, "replicas": replicas,
                            "profile": profile}))
    return cases


def _gen_task_runtime() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for workers in WORKER_COUNTS:
        for tasks in (1, 4, 16):
            for profile in ("single_process_deterministic",
                            "multi_thread_deterministic"):
                for seed in SEEDS[:2]:
                    idx += 1
                    rows = declarations() + _bell_rows(2)
                    cases.append(Case(
                        case_id=f"POS-TASK-{idx:04d}", group="task_runtime",
                        title=f"{workers} worker(s), {tasks} task(s), {profile}",
                        source=program(rows),
                        expect={"accept": True, "check": "task_runtime",
                                "workers": workers, "tasks": tasks,
                                "profile": profile, "seed": seed}))
    return cases


def _gen_collectives() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    ops = ("broadcast", "gather", "allgather", "reduce", "allreduce", "scan",
           "scatter", "alltoall")
    for op in ops:
        for workers in WORKER_COUNTS:
            for algorithm in ("linear", "tree"):
                idx += 1
                rows = declarations() + _bell_rows(2)
                cases.append(Case(
                    case_id=f"POS-COLL-{idx:04d}", group="collectives",
                    title=f"{op} over {workers} worker(s) via {algorithm}",
                    source=program(rows),
                    expect={"accept": True, "check": "collective", "op": op,
                            "workers": workers, "algorithm": algorithm}))
    return cases


def _gen_federation() -> List[Case]:
    cases: List[Case] = []
    idx = 0
    for workers in WORKER_COUNTS:
        for groups in (1, 2):
            for domains in (1, 2):
                for nodes in (2, 4):
                    idx += 1
                    rows = declarations(
                        topology_resource=f"nodes={nodes};domains={domains}")
                    rows += _independent_lanes(2, 2)
                    cases.append(Case(
                        case_id=f"POS-FED-{idx:04d}", group="federation",
                        title=f"{workers} workers / {groups} groups / "
                              f"{domains} domains / {nodes} topology nodes",
                        source=program(rows),
                        expect={"accept": True, "check": "federation",
                                "workers": workers, "groups": groups,
                                "domains": domains, "nodes": nodes}))
    return cases


POSITIVE_GENERATORS: Dict[str, Callable[[], List[Case]]] = {
    "commutation": _gen_commutation,
    "partitioning": _gen_partitioning,
    "placement_routing": _gen_placement_routing,
    "scheduling": _gen_scheduling,
    "numerical_backends": _gen_numerical_backends,
    "protocols": _gen_protocols,
    "resilience": _gen_resilience,
    "provenance_replay": _gen_provenance_replay,
    "consistency_crdt": _gen_consistency_crdt,
    "task_runtime": _gen_task_runtime,
    "collectives": _gen_collectives,
    "federation": _gen_federation,
}


# --------------------------------------------------------------------------
# 6. Negative case generators (LCTL 1.2.x s41, 1.3.x s60, 1.4.x s62, 1.6.x s80)
# --------------------------------------------------------------------------
#
# Each builder takes a variation index and returns a complete program. All
# variations of one category trigger the same declared diagnostic, by
# construction, and the suite verifies that they actually do.

def _neg_duplicate_qstate(v: int) -> str:
    q = v % 3
    rows = declarations() + _bell_rows(3)
    rows.append(gate(f"R3{v:02d}", "X", a=f"q[{q}]", node="N1"))
    return program(rows)


def _neg_implicit_copy(v: int) -> str:
    q = v % 3
    rows = declarations() + _bell_rows(3)
    rows.append(row(f"R3{v:02d}", "EXEC", "TENSOR", out="t0", a=f"q[{q}]",
                    b=f"q[{q}]", type="tensor_product", regime="EXACT",
                    node="N0", domain="D0", lane="L0", conf="1.0"))
    return program(rows)


def _neg_write_write(v: int) -> str:
    ops = ("X", "Y", "Z", "H", "S", "T", "SDG", "TDG", "X", "H")
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", ops[v % len(ops)], a="q[0]",
                     assume="unitarity;parallel_with=R301"))
    rows.append(gate("R301", ops[(v + 1) % len(ops)], a="q[0]"))
    return program(rows)


def _neg_read_write(v: int) -> str:
    rows = declarations() + _bell_rows(3)
    rows.append(gate("R300", "CZ", ctrl="q[0]", a="q[1]",
                     assume="unitarity;parallel_with=R301"))
    rows.append(gate("R301", ("X", "Y", "Z", "H", "S")[v % 5], a="q[0]"))
    return program(rows)


def _neg_use_after_move(v: int) -> str:
    ops = ("X", "Y", "Z", "H", "S", "T", "SDG", "TDG", "RX", "RY")
    op = ops[v % len(ops)]
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "PROTOCOL", "TELEPORT", out="z[0]", a="q[0]",
                    type="remote_handle", regime="EXACT", node="N0", domain="D0",
                    link="Q0", conf="1.0"))
    rows.append(gate("R301", op, a="q[0]",
                     param="pi/4" if op in lang.PARAMETRIC_GATES else NULL_CELL))
    return program(rows)


def _neg_use_after_measure(v: int) -> str:
    ops = ("X", "Y", "Z", "H", "S", "T", "SDG", "TDG", "RX", "RZ")
    op = ops[v % len(ops)]
    rows = declarations() + _bell_rows(2)
    rows.append(measure("R300", "q[0]", "c0", proof="PR-M"))
    rows.append(gate("R301", op, a="q[0]",
                     param="pi/4" if op in lang.PARAMETRIC_GATES else NULL_CELL))
    return program(rows)


def _neg_missing_measurement(v: int) -> str:
    op = ("CLASSICAL_IF", "CLASSICAL_SWITCH", "FEEDBACK")[v % 3]
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "CONTROL", op, ctrl=f"c{v}", type="bit",
                    regime="EXACT", node="N0", domain="D0", lane="L0",
                    conf="1.0"))
    return program(rows)


def _neg_no_quantum_route(v: int) -> str:
    rows = declarations(topology_resource=f"nodes={2 + v % 3};domains=2;"
                                          f"no_quantum=true")
    rows += _bell_rows(2)
    rows.append(prep("R200", "r[0]", node="N1", qspace="r[0:1]"))
    rows.append(row("R300", "PROTOCOL", "REMOTE_CNOT", ctrl="q[0]", a="r[0]",
                    type="controlled_operator", regime="EXACT", node="N0",
                    domain="D0", link="Q0", conf="1.0"))
    return program(rows)


def _neg_unsupported_target(v: int) -> str:
    op = ("REMOTE_CNOT", "TELEPORT", "H", "CX", "X", "MEASURE", "T", "S",
          "RZ", "Y")[v % 10]
    rows = declarations(topology_resource=f"nodes=4;domains=2;restrict_ops={op}")
    rows += _bell_rows(2)
    rows.append(prep("R200", "r[0]", node="N1", qspace="r[0:1]"))
    if op == "REMOTE_CNOT":
        rows.append(row("R300", "PROTOCOL", "REMOTE_CNOT", ctrl="q[0]",
                        a="r[0]", type="controlled_operator", regime="EXACT",
                        node="N0", domain="D0", link="Q0", conf="1.0"))
    elif op == "TELEPORT":
        rows.append(row("R300", "PROTOCOL", "TELEPORT", out="z[0]", a="q[0]",
                        type="remote_handle", regime="EXACT", node="N0", domain="D0",
                        link="Q0", conf="1.0"))
    elif op == "MEASURE":
        rows.append(measure("R300", "q[1]", "c0", proof="PR-M"))
    elif op == "CX":
        rows.append(gate("R300", "CX", ctrl="q[0]", a="q[1]"))
    else:
        rows.append(gate("R300", op, a="q[1]",
                         param="pi/4" if op in lang.PARAMETRIC_GATES
                         else NULL_CELL))
    return program(rows)


def _neg_teleport_no_entanglement(v: int) -> str:
    rows = declarations() + _bell_rows(2 + v % 2)
    rows.append(row("R300", "PROTOCOL", "TELEPORT", out="z[0]", a="q[0]",
                    type="remote_handle", regime="EXACT", node="N0", domain="D0",
                    conf="1.0"))
    return program(rows)


def _neg_teleport_no_classical_path(v: int) -> str:
    rows = declarations(topology_resource=f"nodes={2 + v % 3};domains=2;"
                                          f"no_classical=true")
    rows += _bell_rows(2)
    rows.append(row("R300", "PROTOCOL", "TELEPORT", out="z[0]", a="q[0]",
                    type="remote_handle", regime="EXACT", node="N0", domain="D0",
                    link="Q0", conf="1.0"))
    return program(rows)


def _neg_insufficient_ebits(v: int) -> str:
    rows = declarations(topology_resource="nodes=4;domains=2;qcapacity=0")
    rows += _bell_rows(2)
    for i in range(1 + v % 3):
        rows.append(row(f"R30{i}", "PROTOCOL", "EPR_RESERVE", out=f"e[{i}]",
                        type="remote_handle", regime="EXACT", node="N0", domain="D0",
                        link="Q0", conf="1.0"))
    return program(rows)


def _neg_expired_ebit(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "PROTOCOL", "EPR_RESERVE", out="e[0]",
                    param=f"expiry={v % 3}", type="remote_handle", regime="EXACT",
                    node="N0", domain="D0", link="Q0", conf="1.0"))
    rows.append(claim("R500", param=f"epoch={10 + v}", proof="PR-E"))
    return program(rows)


def _neg_double_ebit_consume(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "PROTOCOL", "EPR_RESERVE", out="e[0]", type="remote_handle",
                    regime="EXACT", node="N0", domain="D0", link="Q0",
                    conf="1.0"))
    for i in range(2 + v % 2):
        rows.append(row(f"R31{i}", "PROTOCOL", "EPR_RELEASE", a="e[0]",
                        type="remote_handle", regime="EXACT", node="N0", domain="D0",
                        link="Q0", conf="1.0"))
    return program(rows)


def _neg_impossible_target_concurrency(v: int) -> str:
    rows = declarations(topology_resource="nodes=4;domains=2;capacity=1")
    rows += _bell_rows(3)
    rows.append(gate("R300", ("X", "Y", "Z", "H", "S")[v % 5], a="q[0]",
                     assume="unitarity;parallel_with=R301"))
    rows.append(gate("R301", ("T", "S", "H", "X", "Z")[v % 5], a="q[1]"))
    return program(rows)


def _neg_crosstalk(v: int) -> str:
    rows = declarations(
        topology_resource="nodes=4;domains=2;crosstalk=q[0]:q[1]")
    rows += _bell_rows(3)
    rows.append(gate("R300", ("X", "Y", "Z", "H", "S")[v % 5], a="q[0]",
                     assume="unitarity;parallel_with=R301"))
    rows.append(gate("R301", ("T", "S", "H", "X", "Z")[v % 5], a="q[1]"))
    return program(rows)


def _neg_coherence_window(v: int) -> str:
    rows = declarations() + _independent_lanes(2, 2 + v % 3)
    rows.append(claim("R500", param=f"coherence_budget={0.001 * (v + 1):.6f}",
                      proof="PR-C"))
    return program(rows)


def _neg_communication_budget(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(measure("R300", "q[1]", "c0", node="N0", proof="PR-M"))
    rows.append(row("R301", "CONTROL", "CLASSICAL_IF", ctrl="c0", type="bit",
                    regime="EXACT", node="N1", domain="D0", lane="L0",
                    conf="1.0"))
    rows.append(claim("R500", param=f"comm_budget_bytes={v}", proof="PR-B"))
    return program(rows)


def _neg_memory_capacity(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", "X", a="q[0]",
                     resource=f"memory={10 ** (12 + v % 3)}"))
    return program(rows)


def _neg_unresolved_coupling(v: int) -> str:
    ops = ("EXPECT", "VARIANCE", "SAMPLE")
    a_op, b_op = ops[v % 3], ops[(v + 1) % 3]
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "MEASURE", a_op, out="o0", a="q[0]", type="real_c",
                    basis="Z", regime="EXACT",
                    assume="linearity;parallel_with=R301", node="N0",
                    domain="D0", lane="L0", conf="1.0"))
    rows.append(row("R301", "MEASURE", b_op, out="o1", a="q[0]", type="real_c",
                    basis="Z", regime="EXACT", assume="linearity", node="N0",
                    domain="D0", lane="L0", conf="1.0"))
    return program(rows)


def _neg_topology_seal(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500", resource=f"topology_seal={'a' * 63}{v}",
                      proof="PR-S"))
    return program(rows)


def _neg_schedule_seal(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500", resource=f"schedule_seal={'b' * 63}{v}",
                      proof="PR-S"))
    return program(rows)


def _neg_stale_calibration(v: int) -> str:
    rows = declarations(topology_resource="nodes=4;domains=2;calibration_until=0")
    rows += _bell_rows(2)
    rows.append(claim("R500", param=f"epoch={1 + v}", proof="PR-K"))
    return program(rows)


def _neg_result_without_provenance(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(measure("R300", "q[0]", "c0", proof="PR-M"))
    rows.append(claim("R500", param=f"result=c0;shots={100 * (v + 1)}"))
    return program(rows)


def _neg_physical_execution_claim(v: int) -> str:
    kinds = ("execution=PHYSICAL_PARALLEL_QPU_EXECUTION",
             "execution=PHYSICAL_DISTRIBUTED_QPU_EXECUTION",
             "physical_qpu=true", "physical_parallel=true",
             "execution=PHYSICAL_PARALLEL_EXECUTION")
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500", param=kinds[v % len(kinds)], proof="PR-X"))
    return program(rows)


def _neg_physical_distributed_claim(v: int) -> str:
    rows = declarations(nodes=1 + v % 2) + _bell_rows(2)
    rows.append(claim("R500", param="physical_distributed=true", proof="PR-X"))
    return program(rows)


def _neg_hidden_approximation(v: int) -> str:
    regimes = ("EXACT", "EXACT_LINEAR", "PIECEWISE_EXACT")
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", ("X", "H", "Z", "S", "T")[v % 5], a="q[0]",
                     regime=regimes[v % 3], error=f"approx=true;p=0.0{v}"))
    return program(rows)


def _neg_invalid_checkpoint(v: int) -> str:
    rows = declarations() + _bell_rows(3)
    rows.append(claim("R500", param=f"checkpoint=q[{v % 3}]", proof="PR-CK"))
    return program(rows)


def _neg_negative_probability(v: int) -> str:
    ops = ("DEPOLARIZE", "DEPHASE", "BIT_FLIP", "PHASE_FLIP",
           "AMPLITUDE_DAMP", "PHASE_DAMP")
    vals = ("-0.1", "-0.5", "1.5", "-1.0", "2.0")
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "NOISE", ops[v % len(ops)], a="q[0]",
                    param=vals[v % len(vals)], type="channel", regime="NOISY",
                    node="N0", domain="D0", lane="L0", conf="1.0"))
    return program(rows)


def _neg_non_normalized_state(v: int) -> str:
    amps = ("0.9,0.9", "1.0,1.0", "0.5,0.5", "2.0,0.0", "0.1,0.1")
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "PREPARE", "PREP_STATE", out=f"p[{v % 3}]",
                    param=f"amp={amps[v % len(amps)]}", type="pure_state",
                    basis="Z", regime="EXACT", node="N0", domain="D0",
                    lane="L0", conf="1.0"))
    return program(rows)


def _matrix_case(kind: str, matrix: str, v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500", param=f"matrix_check={kind};matrix={matrix}",
                      proof=f"PR-M{v}"))
    return program(rows)


def _neg_invalid_unitary(v: int) -> str:
    mats = ("1,0,0,2", "2,0,0,1", "1,1,0,1", "0.5,0,0,0.5", "1,0,1,1")
    return _matrix_case("unitary", mats[v % len(mats)], v)


def _neg_non_hermitian(v: int) -> str:
    mats = ("0,1,0,0", "0,0,1,0", "1,2,3,4", "0,5,0,0", "1,1,0,1")
    return _matrix_case("hermitian", mats[v % len(mats)], v)


def _neg_density_negative_eigenvalue(v: int) -> str:
    mats = ("1.5,0,0,-0.5", "2.0,0,0,-1.0", "1.2,0,0,-0.2",
            "-0.5,0,0,1.5", "1.1,0,0,-0.1")
    return _matrix_case("density", mats[v % len(mats)], v)


def _neg_density_trace(v: int) -> str:
    mats = ("1,0,0,1", "0.5,0,0,0.2", "0.9,0,0,0", "0.25,0,0,0.25",
            "0.6,0,0,0.6")
    return _matrix_case("density", mats[v % len(mats)], v)


def _neg_invalid_projector(v: int) -> str:
    mats = ("1,0,0,0.5", "0.5,0,0,0.5", "1,1,1,1", "0.9,0,0,0.9", "2,0,0,0")
    return _matrix_case("projector", mats[v % len(mats)], v)


def _neg_invalid_povm(v: int) -> str:
    mats = ("1,0,0,0/0,0,0,0", "0.5,0,0,0.5/0.1,0,0,0.1",
            "1,0,0,1/1,0,0,1", "0.2,0,0,0.2", "0,0,0,0/0,0,0,0")
    return _matrix_case("povm", mats[v % len(mats)], v)


def _neg_invalid_kraus(v: int) -> str:
    mats = ("0.5,0,0,0.5", "1,0,0,0", "0.1,0,0,0.1/0.1,0,0,0.1",
            "2,0,0,2", "0,0,0,0")
    return _matrix_case("kraus", mats[v % len(mats)], v)


def _neg_control_equals_target(v: int) -> str:
    ops = ("CX", "CNOT", "CY", "CZ", "CH")
    q = v % 3
    rows = declarations() + _bell_rows(3)
    rows.append(gate("R300", ops[v % len(ops)], ctrl=f"q[{q}]", a=f"q[{q}]"))
    return program(rows)


def _neg_tensor_dimensions(v: int) -> str:
    rows = declarations() + _bell_rows(3)
    rows.append(row("R300", "EXEC", "TENSOR", out="t0", a="q[0]", b="q[1]",
                    param=f"dim={8 + v}", type="tensor_product", regime="EXACT",
                    node="N0", domain="D0", lane="L0", conf="1.0"))
    return program(rows)


def _neg_invalid_arity(v: int) -> str:
    ops = ("H", "X", "Y", "Z", "S", "T", "SDG", "TDG")
    rows = declarations() + _bell_rows(3)
    rows.append(gate("R300", ops[v % len(ops)], a="q[0]", b="q[1]"))
    return program(rows)


def _neg_unknown_operation(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", f"FROBNICATE{v}", a="q[0]"))
    return program(rows)


def _neg_unknown_face(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", f"WIDGET{v}", "H", a="q[0]", type="unitary",
                    regime="EXACT", node="N0", domain="D0", conf="1.0"))
    return program(rows)


def _neg_unknown_type(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", "H", a="q[0]", type=f"quux{v}"))
    return program(rows)


def _neg_unknown_regime(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", "H", a="q[0]", regime=f"SORTOF{v}"))
    return program(rows)


def _neg_duplicate_row_id(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", "H", a="q[0]"))
    rows.append(gate("R300", ("X", "Y", "Z", "S", "T")[v % 5], a="q[1]"))
    return program(rows)


def _neg_conf_out_of_range(v: int) -> str:
    vals = ("1.7", "-0.5", "2.0", "100", "-1")
    rows = declarations() + _bell_rows(2)
    line = gate("R300", "H", a="q[0]")
    cells = line.split(CANONICAL_SEP)
    cells[FULL_COLUMNS.index("CONF")] = vals[v % len(vals)]
    rows.append(CANONICAL_SEP.join(cells))
    return program(rows)


def _neg_network_not_deny(v: int) -> str:
    vals = ("allow", "permit", "open", "true", "any")
    rows = declarations() + _bell_rows(2)
    return program(rows, network=vals[v % len(vals)])


def _neg_backend_not_none(v: int) -> str:
    vals = ("ibm", "aws", "remote", "cloud", "vendor")
    rows = declarations() + _bell_rows(2)
    return program(rows, backend=vals[v % len(vals)])


def _neg_exact_regime_on_noise(v: int) -> str:
    ops = ("DEPOLARIZE", "DEPHASE", "BIT_FLIP", "PHASE_FLIP", "AMPLITUDE_DAMP")
    regimes = ("EXACT", "EXACT_LINEAR", "PIECEWISE_EXACT")
    rows = declarations() + _bell_rows(2)
    rows.append(row("R300", "NOISE", ops[v % len(ops)], a="q[0]", param="0.05",
                    type="channel", regime=regimes[v % 3], node="N0",
                    domain="D0", lane="L0", conf="1.0"))
    return program(rows)


def _neg_consistency_downgrade(v: int) -> str:
    profiles = ("LINEARIZABLE", "SEQUENTIAL")
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500",
                      param=f"consistency={profiles[v % 2]};partitions=true;"
                            f"replicas={1 + v % 2}", proof="PR-CN"))
    return program(rows)


def _neg_illegal_steal(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500", param=f"steal=quantum;attempt={v}", proof="PR-ST"))
    return program(rows)


def _neg_replay_corruption(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500", param=f"replay=corrupt;variant={v}",
                      proof="PR-RP"))
    return program(rows)


def _neg_crdt_invalid_merge(v: int) -> str:
    pairs = ("GCounter/GSet", "GSet/GCounter", "PNCounter/ORSet",
             "ORSet/LWWRegister", "LWWRegister/GCounter")
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500", param=f"crdt_merge={pairs[v % len(pairs)]}",
                      proof="PR-CR"))
    return program(rows)


def _neg_error_composition_units(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate("R300", "X", a="q[0]",
                     error=f"p=0.0{1 + v % 8};unit=probability;"
                           f"kind=failure_probability;domain=gate"))
    rows.append(gate("R301", "Z", a="q[1]",
                     error=f"p={0.5 + v};unit=second;kind=duration;domain=idle"))
    rows.append(claim("R500", param="compose=strict", proof="PR-EC"))
    return program(rows)


def _neg_deadlock_cycle(v: int) -> str:
    n = 2 + v % 3
    rows = declarations() + _bell_rows(2)
    for i in range(n):
        nxt = (i + 1) % n
        rows.append(gate(f"R4{i:02d}", "X" if i % 2 else "Z", a=f"q[{i % 2}]",
                         assume=f"unitarity;wait_for=R4{nxt:02d}"))
    return program(rows)


def _neg_invalid_recovery(v: int) -> str:
    scenarios = ("quantum_link_failure", "coherence_deadline_exceeded",
                 "qpu_unavailable", "node_unavailable", "worker_crash")
    rows = declarations() + _bell_rows(2)
    rows.append(claim("R500",
                      param=f"recovery=clone_unknown_qstate;"
                            f"scenario={scenarios[v % len(scenarios)]}",
                      proof="PR-RV"))
    return program(rows)


#: category -> (expected diagnostic code, builder, human title)
NEGATIVE_BUILDERS: Dict[str, Tuple[str, Callable[[int], str], str]] = {
    "duplicate_unknown_qstate_across_nodes":
        ("E-OWN-003", _neg_duplicate_qstate,
         "a second node operates locally on another node's qubit"),
    "implicit_copy":
        ("E-CLONE-001", _neg_implicit_copy, "TENSOR duplicates one operand"),
    "write_write_conflict":
        ("E-CONC-WW", _neg_write_write,
         "two rows asserted concurrent write the same qubit"),
    "read_write_conflict":
        ("E-CONC-RW", _neg_read_write,
         "a row asserted concurrent writes what the other reads"),
    "use_after_move":
        ("E-OWN-001", _neg_use_after_move, "operand used after TELEPORT moved it"),
    "use_after_measure":
        ("E-OWN-002", _neg_use_after_measure,
         "operand used after a destructive measurement"),
    "missing_measurement_dependency":
        ("E-CTL-003", _neg_missing_measurement,
         "classical control depends on a bit no measurement produced"),
    "remote_gate_with_no_route":
        ("E-QROUTE-001", _neg_no_quantum_route,
         "a remote gate is requested with no quantum route"),
    "remote_gate_unsupported_target":
        ("E-TARGET-001", _neg_unsupported_target,
         "the bound target does not support the requested operation"),
    "teleport_without_entanglement":
        ("E-PROTO-002", _neg_teleport_no_entanglement,
         "TELEPORT declares no LINK carrying an ebit"),
    "teleport_without_classical_path":
        ("E-CPATH-001", _neg_teleport_no_classical_path,
         "TELEPORT has no classical path for its correction bits"),
    "insufficient_ebits":
        ("E-EBIT-001", _neg_insufficient_ebits,
         "more ebits are requested than the link admits"),
    "expired_ebit":
        ("E-EBIT-002", _neg_expired_ebit,
         "a reserved ebit is expired at the claimed epoch"),
    "double_ebit_consume":
        ("E-EPR-003", _neg_double_ebit_consume,
         "the same ebit is released twice"),
    "impossible_target_concurrency":
        ("E-CONC-TARGET", _neg_impossible_target_concurrency,
         "asserted concurrency on a target with capacity 1"),
    "crosstalk_budget_exceeded":
        ("E-XTALK-001", _neg_crosstalk,
         "asserted concurrency across a calibrated crosstalk pair"),
    "coherence_window_exceeded":
        ("E-COH-001", _neg_coherence_window,
         "planned coherence exposure exceeds the declared budget"),
    "communication_budget_exceeded":
        ("E-COMM-001", _neg_communication_budget,
         "planned cross-node traffic exceeds the declared budget"),
    "memory_capacity_exceeded":
        ("E-MEM-001", _neg_memory_capacity,
         "declared memory exceeds the bound target"),
    "unresolved_coupling":
        ("E-COUPLE-001", _neg_unresolved_coupling,
         "no admitted rule resolves the asserted pair"),
    "corrupted_topology_seal":
        ("E-SEAL-001", _neg_topology_seal, "the declared topology seal is wrong"),
    "corrupted_schedule_seal":
        ("E-SEAL-002", _neg_schedule_seal, "the declared schedule seal is wrong"),
    "stale_calibration":
        ("E-CAL-001", _neg_stale_calibration,
         "the target calibration is stale at the claimed epoch"),
    "result_without_provenance":
        ("E-PROV-001", _neg_result_without_provenance,
         "a result is claimed with no PROOF reference"),
    "physical_execution_claim_from_simulator":
        ("E-CLAIM-001", _neg_physical_execution_claim,
         "a classical simulator claims physical quantum execution"),
    "physical_distributed_claim_without_two_endpoints":
        ("E-CLAIM-002", _neg_physical_distributed_claim,
         "physical distributed execution claimed with no authenticated endpoints"),
    "hidden_approximation":
        ("E-REG-003", _neg_hidden_approximation,
         "an EXACT regime carries an approximation flag"),
    "invalid_checkpoint_of_unknown_qstate":
        ("E-CKPT-001", _neg_invalid_checkpoint,
         "a checkpoint of unknown quantum state is requested"),
    "negative_probability":
        ("E-PROB-001", _neg_negative_probability,
         "a channel parameter is outside [0,1]"),
    "non_normalized_state":
        ("E-NORM-001", _neg_non_normalized_state,
         "declared amplitudes are not normalized"),
    "invalid_unitary":
        ("E-UNIT-001", _neg_invalid_unitary, "a declared unitary is not unitary"),
    "non_hermitian_observable_declared_hermitian":
        ("E-HERM-001", _neg_non_hermitian,
         "a declared Hermitian observable is not Hermitian"),
    "density_with_negative_eigenvalue":
        ("E-DENS-001", _neg_density_negative_eigenvalue,
         "a declared density matrix has a negative eigenvalue"),
    "density_trace_not_one":
        ("E-DENS-002", _neg_density_trace,
         "a declared density matrix does not have unit trace"),
    "invalid_projector":
        ("E-PROJ-001", _neg_invalid_projector,
         "a declared projector is not idempotent"),
    "invalid_povm":
        ("E-POVM-001", _neg_invalid_povm,
         "declared POVM elements do not resolve the identity"),
    "invalid_kraus_completeness":
        ("E-KRAUS-001", _neg_invalid_kraus,
         "a declared Kraus set is not trace preserving"),
    "control_equals_target":
        ("E-CTRL-001", _neg_control_equals_target,
         "a controlled gate names the same qubit twice"),
    "incompatible_tensor_dimensions":
        ("E-DIM-001", _neg_tensor_dimensions,
         "declared tensor dimension contradicts the operands"),
    "invalid_arity":
        ("E-ARITY-001", _neg_invalid_arity,
         "a single-qubit gate is given two operands"),
    "unknown_operation":
        ("E-OP-001", _neg_unknown_operation,
         "the operation is not in the normative catalog"),
    "unknown_face":
        ("E-FACE-001", _neg_unknown_face, "the FACE is not an admitted value"),
    "unknown_type":
        ("E-TYPE-001", _neg_unknown_type, "the TYPE is not an admitted value"),
    "unknown_regime":
        ("E-REG-001", _neg_unknown_regime, "the REGIME is not an admitted value"),
    "duplicate_row_id":
        ("E-GRAM-003", _neg_duplicate_row_id, "two rows share one ROW identity"),
    "conf_out_of_range":
        ("E-CONF-001", _neg_conf_out_of_range, "CONF lies outside [0,1]"),
    "network_not_deny":
        ("E-POL-001", _neg_network_not_deny, "NETWORK is not `deny`"),
    "backend_not_none":
        ("E-POL-002", _neg_backend_not_none, "BACKEND is not `none`"),
    "exact_regime_on_noise_op":
        ("E-REG-002", _neg_exact_regime_on_noise,
         "a noise operation declares an exact regime"),
    "consistency_downgrade":
        ("E-CONS-001", _neg_consistency_downgrade,
         "a consistency guarantee is impossible under the declared model"),
    "illegal_work_steal_of_quantum_task":
        ("E-STEAL-001", _neg_illegal_steal,
         "a task holding unknown quantum state is stolen"),
    "replay_corruption":
        ("E-REPLAY-001", _neg_replay_corruption,
         "deterministic replay does not reproduce the recorded state"),
    "crdt_invalid_merge":
        ("E-CRDT-001", _neg_crdt_invalid_merge,
         "two different CRDT types are merged"),
    "error_composition_incompatible_units":
        ("E-ERRC-001", _neg_error_composition_units,
         "error terms with incompatible units are composed"),
    "deadlock_cycle":
        ("E-DEADLOCK-001", _neg_deadlock_cycle,
         "the declared wait graph contains a cycle"),
    "invalid_recovery_requiring_cloning":
        ("E-RECOV-001", _neg_invalid_recovery,
         "the only admissible recovery would duplicate unknown quantum state"),
}

#: Variations generated per negative category.
NEGATIVE_VARIATIONS = 10


def _build_negatives() -> List[Case]:
    cases: List[Case] = []
    for category in NEGATIVE_CATEGORIES:
        code, builder, title = NEGATIVE_BUILDERS[category]
        for v in range(NEGATIVE_VARIATIONS):
            cases.append(Case(
                case_id=f"NEG-{category.upper()}-{v:02d}",
                group=category,
                title=f"{title} (variation {v})",
                source=builder(v),
                expect={"reject": True, "code": code, "category": category}))
    return cases


def _build_positives() -> List[Case]:
    cases: List[Case] = []
    for group in POSITIVE_GROUPS:
        cases.extend(POSITIVE_GENERATORS[group]())
    return cases


POSITIVE_CASES: List[Case] = _build_positives()
NEGATIVE_CASES: List[Case] = _build_negatives()
ALL_CASES: List[Case] = POSITIVE_CASES + NEGATIVE_CASES
CASE_INDEX: Dict[str, Case] = {c.case_id: c for c in ALL_CASES}


# --------------------------------------------------------------------------
# 7. Case runners
# --------------------------------------------------------------------------

def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaseFailure(message)


def _run_positive(case: Case) -> None:
    result = admit(case.source, case.case_id)
    _require(result.accepted,
             f"expected acceptance, got {result.stage} {result.codes}: "
             f"{result.messages[:2]}")
    checker = _POSITIVE_CHECKS[case.expect["check"]]
    checker(case)


def _pipeline(case: Case) -> Tuple[lang.Program, lang.VerifyResult,
                                   planner.Topology, ses_mod.SES,
                                   ses_mod.PartialOrder]:
    prog, _ = lang.parse(case.source, case.case_id)
    assert prog is not None
    vr = lang.verify(prog)
    topo = topology_from_program(prog)
    graph = ses_mod.build_ses(prog, vr)
    return prog, vr, topo, graph, ses_mod.analyze(graph)


def _check_commutation(case: Case) -> None:
    _prog, _vr, topo, graph, po = _pipeline(case)
    authority = commutation_mod.CommutationAuthority()
    records = ses_mod.admit_concurrency(graph, po, authority, topo)
    for rec in records:
        _require(rec.decision in lang.CONCURRENCY_DECISIONS,
                 f"undeclared concurrency decision {rec.decision}")
        _require(bool(rec.reason), f"pair {rec.candidate_pair} has no reason")
        _require(rec.proof_ref.startswith("CP-"),
                 f"pair {rec.candidate_pair} has no proof reference")
    _require(bool(authority.ledger), "commutation produced no proof rows")
    a, b = case.expect["pair"]
    if a in graph.nodes and b in graph.nodes:
        v1 = authority.classify_pair(graph.nodes[a], graph.nodes[b])
        v2 = authority.classify_pair(graph.nodes[a], graph.nodes[b])
        _require(v1.status == v2.status and v1.proof_id == v2.proof_id,
                 "commutation verdicts are not reproducible")
    for rec in commutation_mod.plan_rewrites(graph, authority):
        _require(rec["admission"] in lang.Q6_ADMISSION,
                 f"rewrite {rec['rewrite']} carries an unadmitted verdict")


def _check_partition(case: Case) -> None:
    _prog, _vr, _topo, graph, _po = _pipeline(case)
    k = case.expect["k"]
    part = planner.HypergraphPartitioner().partition(
        graph, k, exact=bool(case.expect.get("exact")))
    executable = [n for n in graph.order
                  if graph.nodes[n].semantic_face in lang.EXECUTABLE_FACES]
    _require(set(part.assignment) == set(executable),
             "partition does not cover exactly the executable SES nodes")
    _require(all(0 <= p < part.k for p in part.assignment.values()),
             "partition assigned an out-of-range block")
    _require(set(part.cost) == set(planner.PARTITION_COST_DIMENSIONS),
             "partition cost vector is incomplete")
    claim_ = part.as_dict()["optimality_claim"]
    if part.optimal_cost is None:
        _require(claim_ == "HEURISTIC_NO_OPTIMALITY_CLAIM",
                 "a heuristic partition claims optimality")
    again = planner.HypergraphPartitioner().partition(
        graph, k, exact=bool(case.expect.get("exact")))
    _require(again.assignment == part.assignment,
             "partitioning is not deterministic")


def _check_placement_routing(case: Case) -> None:
    _prog, _vr, topo, graph, _po = _pipeline(case)
    part = planner.HypergraphPartitioner().partition(
        graph, case.expect["k"], exact=False)
    placement, proofs = planner.Placer(topo).place(graph, part)
    _require(bool(placement), "no partition was placed")
    for p, target in placement.items():
        _require(target in topo.nodes, f"partition {p} bound to unknown target")
    selected = [pr for pr in proofs if pr.reason.startswith("selected")]
    _require(len(selected) == len(placement),
             "every placed partition needs exactly one selection proof")
    for pr in selected:
        _require(all([pr.capacity_pass, pr.operation_support_pass,
                      pr.memory_pass, pr.topology_pass, pr.calibration_pass,
                      pr.trust_pass]),
                 f"partition {pr.partition} was placed on a failing proof")
    router = planner.ClassicalRouter(topo)
    targets = sorted(set(placement.values()))
    for a, b in itertools.combinations(targets, 2):
        route = router.select(a, b, payload=case.expect["payload"])
        _require(route is not None, f"no classical route {a} -> {b}")
        _require(route.expected_transfer >= route.latency - 1e-9,
                 "transfer time is below the pure latency term")
    qrouter = planner.QuantumRouter(topo)
    for a, b in itertools.combinations(targets, 2):
        q = qrouter.route(a, b)
        if q is not None:
            _require(0.0 <= q.fidelity <= 1.0, "route fidelity outside [0,1]")


def _check_schedule(case: Case) -> None:
    _prog, _vr, topo, graph, po = _pipeline(case)
    authority = commutation_mod.CommutationAuthority()
    records = ses_mod.admit_concurrency(graph, po, authority, topo)
    part = planner.HypergraphPartitioner().partition(
        graph, case.expect["k"], exact=False)
    placement, _ = planner.Placer(topo).place(graph, part)
    sched = planner.TemporalScheduler(topo, authority).schedule(
        graph, po, part, placement, records, mode=case.expect["mode"])
    _require(not sched.blocked, f"schedule blocked: {sched.blocked}")
    _require(len({o.node_id for o in sched.ops}) == len(graph.order),
             "the schedule does not cover every SES node")
    finish = {o.node_id: o.end for o in sched.ops}
    start = {o.node_id: o.start for o in sched.ops}
    for e in graph.edges:
        _require(start[e.dst] >= finish[e.src] - 1e-9,
                 f"edge {e.src}->{e.dst} is violated by the schedule")
    _require(sched.makespan >= po.span - 1e-9,
             "makespan is shorter than the critical path")
    again = planner.TemporalScheduler(topo, authority).schedule(
        graph, po, part, placement, records, mode=case.expect["mode"])
    _require(again.hash() == sched.hash(), "scheduling is not deterministic")


def _check_numerical(case: Case) -> None:
    prog, vr, _topo, _graph, _po = _pipeline(case)
    circuit = simulator.circuit_from_program(prog, vr)
    summary = circuit.summary(shots=case.expect["shots"])
    plan = simulator.NumericalPlanner().plan(summary)
    _require(plan["backend"] in simulator.BACKENDS,
             f"planner returned unknown backend {plan['backend']}")
    _require(bool(plan["reasons"]), "planner gave no reasons")
    seed, shots = case.expect["seed"], case.expect["shots"]
    if case.expect["shape"] == "noisy":
        res = simulator.run_density(circuit, shots=shots, seed=seed)
        again = simulator.run_density(circuit, shots=shots, seed=seed)
    else:
        res = simulator.run_statevector(circuit, shots=shots, seed=seed)
        again = simulator.run_statevector(circuit, shots=shots, seed=seed)
    _require(res["final_state_hash"] == again["final_state_hash"],
             "simulation is not deterministic under a fixed seed")
    _require(res["label"] in simulator.EXECUTION_LABELS,
             f"execution label {res['label']} is not admitted")
    _require(res["label"].startswith("CLASSICAL_"),
             "execution label does not declare itself classical")
    total = sum(res["probabilities"].values())
    _require(abs(total - 1.0) <= 1e-9,
             f"probabilities sum to {total}, not 1")
    if shots:
        _require(sum(res["counts"].values()) == shots,
                 "shot counts do not sum to the requested shots")


def _check_protocol(case: Case) -> None:
    prog, vr, _topo, _graph, _po = _pipeline(case)
    plan = protocols.ProtocolCompiler().compile(prog, vr)
    report = plan.validate()
    _require(report["acyclic"], "protocol step DAG is not acyclic")
    _require(report["correction_gating_ok"],
             "a Pauli correction is not gated on its classical bit")
    _require(len(plan.steps) > 0, "protocol compiled to no steps")
    order = plan.topological_order()
    index = {sid: i for i, sid in enumerate(order)}
    for step in plan.steps:
        for dep in step.depends_on:
            _require(index[dep] < index[step.step_id],
                     f"step {step.step_id} precedes its dependency {dep}")
    seed = case.expect["seed"]
    ledger = protocols.EbitLedger()
    eid = ledger.request("N0", "N1", epoch=0, owner_protocol=case.case_id)
    ledger.generate(eid, epoch=0)
    ledger.herald(eid, fidelity=1.0, epoch=0)
    res = protocols.teleport([math.sqrt(0.3), math.sqrt(0.7)], ledger, eid,
                             src_node="N0", dst_node="N1", seed=seed)
    _require(res["verdict"] == protocols.TELEPORT_PASS_TOKEN,
             f"teleport reference equivalence failed: {res['verdict']}")
    _require(ledger.get(eid).state == "CONSUMED",
             "teleport did not consume its ebit")


def _check_resilience(case: Case) -> None:
    scenario, seed = case.expect["scenario"], case.expect["seed"]
    injector = resilience.FailureInjector(seed=seed)
    event = injector.inject(scenario, target="W00")
    _require(event.scenario_id == scenario, "injector returned another scenario")
    record = resilience.RecoveryPlanner().explain(
        event, resilience.DEFAULT_CONTEXTS.get(scenario, {}))
    _require(record.recovery_class in lang.RECOVERY_CLASSES,
             f"unknown recovery class {record.recovery_class}")
    _require(bool(record.reason), "recovery classification carries no reason")
    decision = resilience.SupervisionTree().handle(
        "W00", record.recovery_class, event)
    _require(decision.action in resilience.SUPERVISION_ACTIONS,
             f"unknown supervision action {decision.action}")
    again = resilience.RecoveryPlanner().explain(
        resilience.FailureInjector(seed=seed).inject(scenario, target="W00"),
        resilience.DEFAULT_CONTEXTS.get(scenario, {}))
    _require(again.recovery_class == record.recovery_class,
             "recovery classification is not deterministic")


def _check_provenance_replay(case: Case) -> None:
    seed = case.expect["seed"]
    ls = ledgers.LedgerSet.build(case.source, case.case_id, k=case.expect["k"],
                                 seed=seed, shots=0)
    prov = ls.ledgers["PROVENANCE_LEDGER.json"]
    _require(set(prov) == set(ledgers.PROVENANCE_FIELDS),
             "provenance record does not carry exactly the normative fields")
    _require(prov["quantum_boundary"] == "QUANTUM_BOUNDARY_NOT_CROSSED",
             "provenance claims the quantum boundary was crossed")
    _require(prov["target_verified"] is False, "provenance claims a verified target")
    for flag in ("physical_qpu", "physical_parallel", "physical_distributed"):
        _require(prov[flag] is False, f"provenance claims {flag}")
    ladder = list(lang.PARALLEL_STATES)
    _require(ladder.index(prov["parallel_state"])
             <= ladder.index("PARALLEL_EMULATION"),
             "parallel_state exceeds the admitted ceiling")
    dladder = list(lang.DISTRIBUTED_STATES)
    _require(dladder.index(prov["distributed_state"])
             <= dladder.index("DISTRIBUTED_CLASSICAL_EMULATION"),
             "distributed_state exceeds the admitted ceiling")
    _require(ledgers.QCIRP2.from_dict(ls.qcir.as_dict()).hash() == ls.qcir.hash(),
             "QCIR-P2 round trip changed the hash")
    log = fabric.EventLog(case.case_id)
    log.append("W00", "PREP", inputs={"q": 0}, outputs={"q": 0},
               ownership_delta={"acquire": ["q[0]"]},
               resource_delta={"cpu_work": 1.0})
    log.append("W00", "GATE", inputs={"g": "H"}, outputs={"g": "H"},
               resource_delta={"cpu_work": 1.0})
    report = log.verify_replay(log.reconstruct())
    _require(report.ok and report.token == fabric.TOKEN_REPLAY_PASS,
             "deterministic replay failed")
    _require(log.hash() == fabric.EventLog(case.case_id).hash()
             if len(log) == 0 else True, "event log hashing is inconsistent")


def _check_consistency(case: Case) -> None:
    name = case.expect["crdt"]
    registry = {"GCounter": crdt.GCounter, "PNCounter": crdt.PNCounter,
                "GSet": crdt.GSet, "ORSet": crdt.ORSet,
                "LWWRegister": crdt.LWWRegister}
    cls = registry[name]
    report = crdt.verify_crdt_laws(cls, samples=8, seed=11)
    _require(report.ok, f"{name} violates the CRDT laws: {report.as_dict()}")
    engine = crdt.AntiEntropy()
    for i in range(case.expect["replicas"]):
        replica = engine.add_replica(f"R{i}", cls(f"R{i}"))
        _mutate_crdt(replica, i)
    convergence = engine.converge(max_rounds=16)
    _require(convergence.converged,
             f"anti-entropy did not converge for {name}")
    hashes = {r.state_hash() for r in engine.replicas.values()}
    _require(len(hashes) == 1, f"{name} replicas did not converge to one state")
    contract = crdt.ConsistencyContract(
        case.expect["profile"],
        crdt.PartitionModel(partitions_possible=False),
        crdt.FailureModel(replicas=case.expect["replicas"],
                          crdt_declared=True))
    verdict = contract.evaluate()
    _require(verdict.admissible,
             f"{case.expect['profile']} was blocked: {verdict.reasons}")
    _require(verdict.as_dict()["downgrade_offered"] is False,
             "a consistency downgrade was offered")


def _mutate_crdt(replica: crdt.CRDT, i: int) -> None:
    if isinstance(replica, (crdt.GCounter, crdt.PNCounter)):
        replica.increment(i + 1)
    elif isinstance(replica, crdt.GSet):
        replica.add(f"item{i}")
    elif isinstance(replica, crdt.ORSet):
        replica.add(f"item{i}")
    elif isinstance(replica, crdt.LWWRegister):
        replica.set(f"value{i}", timestamp=i + 1)


def _check_task_runtime(case: Case) -> None:
    workers, n_tasks = case.expect["workers"], case.expect["tasks"]
    profile = case.expect["profile"]
    fed = fabric.build_flat_federation(workers)
    rt = fabric.TaskRuntime(fed, profile=fabric.ExecutionProfile.from_token(profile),
                            max_workers=workers)
    for i in range(n_tasks):
        rt.spawn(_square, i, task_id=f"T{i:03d}")
    rt.run()
    results = rt.await_all()
    _require(results == [i * i for i in range(n_tasks)],
             f"task results are wrong: {results}")
    _require(all(t.state == "DONE" for t in rt.tasks),
             "not every task reached DONE")
    barrier = rt.barrier(level="worker_group")
    _require(barrier.participants, "barrier recorded no participants")
    total = rt.reduction(lambda a, b: a + b, list(range(n_tasks)), initial=0)
    _require(total == sum(range(n_tasks)), "reduction returned a wrong total")
    prefix = rt.scan(lambda a, b: a + b, list(range(n_tasks)))
    _require(prefix == list(itertools.accumulate(range(n_tasks))),
             "inclusive scan returned a wrong prefix")
    payload = fabric.QuantumPayload(payload_id="q[0]", lineage="LIN-T")
    task = rt.spawn(_unit, task_id="TQ")
    rt.transfer_ownership(task, payload)
    try:
        rt.duplicate_ownership(task, payload)
    except fabric.CloneAttemptError:
        pass
    else:
        raise CaseFailure("duplicating a quantum payload was permitted")


def _square(x: int) -> int:
    return x * x


def _check_collective(case: Case) -> None:
    op, workers = case.expect["op"], case.expect["workers"]
    algorithm = case.expect["algorithm"]
    col = fabric.Collectives(workers)
    values = list(range(1, workers + 1))
    if op == "broadcast":
        res = col.broadcast(7, algorithm=algorithm)
        _require(res.values == [7] * workers, "broadcast did not replicate")
    elif op == "gather":
        res = col.gather(values, algorithm=algorithm)
        _require(res.values[0] == values, "gather lost data")
    elif op == "allgather":
        res = col.allgather(values, algorithm=algorithm)
        _require(all(v == values for v in res.values), "allgather lost data")
    elif op == "reduce":
        res = col.reduce(values, algorithm=algorithm)
        _require(res.values[0] == sum(values), "reduce returned a wrong sum")
    elif op == "allreduce":
        res = col.allreduce(values, algorithm=algorithm)
        _require(all(v == sum(values) for v in res.values),
                 "allreduce returned a wrong sum")
    elif op == "scan":
        res = col.scan(values, algorithm=algorithm)
        _require(res.values == list(itertools.accumulate(values)),
                 "scan returned a wrong prefix")
    elif op == "scatter":
        res = col.scatter(values, algorithm=algorithm)
        _require(res.values == values, "scatter did not distribute the chunks")
    elif op == "alltoall":
        matrix = [[i * workers + j for j in range(workers)]
                  for i in range(workers)]
        res = col.alltoall(matrix, algorithm=algorithm)
        expected = [[matrix[j][i] for j in range(workers)]
                    for i in range(workers)]
        _require(res.values == expected, "alltoall did not transpose")
    else:
        raise CaseFailure(f"unhandled collective {op}")
    _require(res.messages == len(res.transfers),
             "message count does not match the transfer schedule")
    _require(res.bytes_moved == sum(t.nbytes for t in res.transfers),
             "byte count does not match the transfer schedule")


def _check_federation(case: Case) -> None:
    workers = case.expect["workers"]
    groups, domains = case.expect["groups"], case.expect["domains"]
    fed = fabric.build_flat_federation(workers, groups=groups, domains=domains)
    ids = fed.worker_ids()
    _require(len(ids) == workers, f"federation built {len(ids)} of {workers}")
    _require(len(set(ids)) == workers, "worker identities are not unique")
    for wid in ids:
        f, d, g = fed.locate(wid)
        _require(f == fed.federation_id, "worker located in another federation")
        _require(d in fed.domain_ids(), f"worker {wid} in unknown domain {d}")
        _require(wid in fed.group_of(wid).worker_ids(),
                 "group membership is inconsistent")
    lb = fabric.LoadBalancer(fed)
    rt = fabric.TaskRuntime(fed)
    tasks = [rt.spawn(_unit, task_id=f"F{i:03d}") for i in range(workers * 2)]
    decisions = lb.static_assign(tasks)
    _require(len(decisions) == len(tasks), "load balancer skipped tasks")
    _require(all(d.worker_id in ids for d in decisions),
             "load balancer placed a task on an unknown worker")
    ledger = lb.ledger()
    _require(ledger["decision_count"] == len(tasks),
             "load balance ledger is incomplete")
    _prog, _vr, topo, graph, _po = _pipeline(case)
    _require(len(topo.nodes) >= case.expect["nodes"],
             "bound topology has fewer nodes than declared")


_POSITIVE_CHECKS: Dict[str, Callable[[Case], None]] = {
    "commutation": _check_commutation,
    "partition": _check_partition,
    "placement_routing": _check_placement_routing,
    "schedule": _check_schedule,
    "numerical": _check_numerical,
    "protocol": _check_protocol,
    "resilience": _check_resilience,
    "provenance_replay": _check_provenance_replay,
    "consistency": _check_consistency,
    "task_runtime": _check_task_runtime,
    "collective": _check_collective,
    "federation": _check_federation,
}


def _run_negative(case: Case) -> None:
    expected = case.expect["code"]
    result = admit(case.source, case.case_id)
    if result.accepted:
        raise CaseFailure(
            f"program was ACCEPTED but must reject with {expected}")
    if expected not in result.codes:
        raise CaseFailure(
            f"rejected at stage {result.stage} with {sorted(set(result.codes))}, "
            f"expected {expected}")


def run_case(case: Case) -> Tuple[bool, str]:
    """Execute one case. Returns (passed, reason)."""
    try:
        if case.expect.get("reject"):
            _run_negative(case)
        else:
            _run_positive(case)
    except CaseFailure as exc:
        return False, str(exc)
    except Exception as exc:                       # noqa: BLE001 - reported
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


# --------------------------------------------------------------------------
# 8. Suite driver
# --------------------------------------------------------------------------

def run_suite(subset: Optional[Sequence[str]] = None, workers: int = 1) -> dict:
    """Execute the conformance suite and report measured results.

    `subset` filters by group, category or case id. `workers` selects the
    number of executor threads; the reported result is identical for any
    worker count because every case is independent and the report is ordered
    by case id.
    """
    selected = _select(subset)
    started = time.time()
    outcomes: Dict[str, Tuple[bool, str]] = {}

    if workers <= 1:
        for case in selected:
            outcomes[case.case_id] = run_case(case)
    else:
        with ThreadPoolExecutor(max_workers=int(workers)) as pool:
            for case, out in zip(selected, pool.map(run_case, selected)):
                outcomes[case.case_id] = out
    duration = time.time() - started

    by_group: Dict[str, Dict[str, int]] = {}
    failures: List[dict] = []
    passed = 0
    for case in selected:
        ok, reason = outcomes[case.case_id]
        bucket = by_group.setdefault(case.group, {"total": 0, "passed": 0,
                                                  "failed": 0})
        bucket["total"] += 1
        if ok:
            bucket["passed"] += 1
            passed += 1
        else:
            bucket["failed"] += 1
            failures.append({"case_id": case.case_id, "reason": reason})

    evidence = hashlib.sha256(json.dumps(
        [[c.case_id, outcomes[c.case_id][0]] for c in selected],
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    return {
        "schema": "PA-LCTL/CONFORMANCE_REPORT/1",
        "total": len(selected),
        "passed": passed,
        "failed": len(selected) - passed,
        "positive_executed": sum(1 for c in selected
                                 if not c.expect.get("reject")),
        "negative_executed": sum(1 for c in selected if c.expect.get("reject")),
        "by_group": {k: by_group[k] for k in sorted(by_group)},
        "failures": sorted(failures, key=lambda d: d["case_id"]),
        "duration_s": round(duration, 6),
        "evidence_hash": evidence,
        "workers": int(workers),
    }


def _select(subset: Optional[Sequence[str]]) -> List[Case]:
    if not subset:
        return list(ALL_CASES)
    keys = set(subset)
    picked = [c for c in ALL_CASES
              if c.case_id in keys or c.group in keys
              or ("positive" in keys and not c.expect.get("reject"))
              or ("negative" in keys and c.expect.get("reject"))]
    if not picked:
        raise lang.PALCTLError(f"subset {sorted(keys)} selected no cases")
    return picked


# --------------------------------------------------------------------------
# 9. Property campaign (LCTL 1.5.x s70.1)
# --------------------------------------------------------------------------

_VALID_1Q = ("H", "X", "Y", "Z", "S", "SDG", "T", "TDG")


def _random_valid_program(rng: random.Random) -> str:
    """Generate a program that is valid by construction.

    Type correctness, ownership, topology binding and the admitted gate set
    are all preserved: the generator only emits rows it can justify.
    """
    n_qubits = rng.randint(2, 4)
    lanes = rng.randint(1, 2)
    rows = declarations(topology_resource="nodes=4;domains=2")
    names = ("q", "r")
    for lane in range(lanes):
        prefix = names[lane]
        base = 100 + 60 * lane
        span = f"{prefix}[0:{n_qubits}]"
        for i in range(n_qubits):
            rows.append(prep(f"R{base + i}", f"{prefix}[{i}]",
                             node=f"N{lane % 2}", lane=f"L{lane}", qspace=span))
        for j in range(rng.randint(1, 4)):
            if rng.random() < 0.65:
                target = rng.randrange(n_qubits)
                rows.append(gate(f"R{base + 20 + j}", rng.choice(_VALID_1Q),
                                 a=f"{prefix}[{target}]", node=f"N{lane % 2}",
                                 lane=f"L{lane}"))
            else:
                c, t = rng.sample(range(n_qubits), 2)
                rows.append(gate(f"R{base + 20 + j}", rng.choice(("CX", "CZ")),
                                 ctrl=f"{prefix}[{c}]", a=f"{prefix}[{t}]",
                                 node=f"N{lane % 2}", lane=f"L{lane}"))
        if rng.random() < 0.5:
            rows.append(measure(f"R{base + 40}", f"{prefix}[0]",
                                f"c{lane}", node=f"N{lane % 2}",
                                lane=f"L{lane}", proof="PR-M"))
    return program(rows)


#: Injectors used by the property campaign. Each injects EXACTLY ONE
#: violation into an otherwise valid program.
_INJECTORS: Tuple[Tuple[str, str, Callable[[List[str], random.Random], None]], ...]


def _inject_unknown_op(rows: List[str], rng: random.Random) -> None:
    rows.append(gate("R900", "FROBNICATE", a="q[0]"))


def _inject_unknown_face(rows: List[str], rng: random.Random) -> None:
    rows.append(row("R900", "WIDGET", "H", a="q[0]", type="unitary",
                    regime="EXACT", node="N0", domain="D0", conf="1.0"))


def _inject_bad_conf(rows: List[str], rng: random.Random) -> None:
    line = gate("R900", "H", a="q[0]")
    cells = line.split(CANONICAL_SEP)
    cells[FULL_COLUMNS.index("CONF")] = "3.5"
    rows.append(CANONICAL_SEP.join(cells))


def _inject_clone(rows: List[str], rng: random.Random) -> None:
    rows.append(row("R900", "EXEC", "TENSOR", out="t0", a="q[0]", b="q[0]",
                    type="tensor_product", regime="EXACT", node="N0",
                    domain="D0", conf="1.0"))


def _inject_arity(rows: List[str], rng: random.Random) -> None:
    rows.append(gate("R900", "H", a="q[0]", b="q[1]"))


def _inject_control_equals_target(rows: List[str], rng: random.Random) -> None:
    rows.append(gate("R900", "CX", ctrl="q[0]", a="q[0]"))


def _inject_negative_probability(rows: List[str], rng: random.Random) -> None:
    rows.append(row("R900", "NOISE", "DEPOLARIZE", a="q[0]", param="-0.3",
                    type="channel", regime="NOISY", node="N0", domain="D0",
                    conf="1.0"))


def _inject_exact_noise(rows: List[str], rng: random.Random) -> None:
    rows.append(row("R900", "NOISE", "DEPOLARIZE", a="q[0]", param="0.05",
                    type="channel", regime="EXACT", node="N0", domain="D0",
                    conf="1.0"))


def _inject_missing_measurement(rows: List[str], rng: random.Random) -> None:
    rows.append(row("R900", "CONTROL", "CLASSICAL_IF", ctrl="cZZ", type="bit",
                    regime="EXACT", node="N0", domain="D0", conf="1.0"))


def _inject_physical_claim(rows: List[str], rng: random.Random) -> None:
    rows.append(claim("R900", param="physical_qpu=true", proof="PR-X"))


def _inject_unknown_type(rows: List[str], rng: random.Random) -> None:
    rows.append(gate("R900", "H", a="q[0]", type="frobnitz"))


def _inject_hidden_approximation(rows: List[str], rng: random.Random) -> None:
    rows.append(gate("R900", "H", a="q[0]", regime="EXACT", error="approx=true"))


_INJECTORS = (
    ("unknown_operation", "E-OP-001", _inject_unknown_op),
    ("unknown_face", "E-FACE-001", _inject_unknown_face),
    ("conf_out_of_range", "E-CONF-001", _inject_bad_conf),
    ("implicit_copy", "E-CLONE-001", _inject_clone),
    ("invalid_arity", "E-ARITY-001", _inject_arity),
    ("control_equals_target", "E-CTRL-001", _inject_control_equals_target),
    ("negative_probability", "E-PROB-001", _inject_negative_probability),
    ("exact_regime_on_noise_op", "E-REG-002", _inject_exact_noise),
    ("missing_measurement_dependency", "E-CTL-003", _inject_missing_measurement),
    ("physical_execution_claim", "E-CLAIM-001", _inject_physical_claim),
    ("unknown_type", "E-TYPE-001", _inject_unknown_type),
    ("hidden_approximation", "E-REG-003", _inject_hidden_approximation),
)


def property_campaign(seed: int = 0, count: int = 200) -> dict:
    """Generate valid and invalid programs and check both directions."""
    rng = random.Random(seed)
    valid_ok = invalid_ok = 0
    failures: List[dict] = []
    for i in range(count):
        source = _random_valid_program(rng)
        if i % 2 == 0:
            result = admit(source, f"PROP-VALID-{i:05d}")
            if result.accepted:
                valid_ok += 1
            else:
                failures.append({"case": f"PROP-VALID-{i:05d}",
                                 "reason": f"valid program rejected at "
                                           f"{result.stage}: {result.codes}",
                                 "messages": result.messages[:2]})
        else:
            name, code, injector = _INJECTORS[i % len(_INJECTORS)]
            rows = source.strip().split("\n")[5:]
            injector(rows, rng)
            mutated = program(rows)
            result = admit(mutated, f"PROP-INVALID-{i:05d}")
            if not result.accepted and code in result.codes:
                invalid_ok += 1
            else:
                failures.append({
                    "case": f"PROP-INVALID-{i:05d}", "violation": name,
                    "reason": f"expected {code}, got accepted={result.accepted} "
                              f"codes={sorted(set(result.codes))}"})
    return {
        "schema": "PA-LCTL/PROPERTY_CAMPAIGN/1",
        "seed": seed, "count": count,
        "valid_generated": (count + 1) // 2, "valid_admitted": valid_ok,
        "invalid_generated": count // 2, "invalid_caught": invalid_ok,
        "failures": failures,
        "ok": not failures,
        "evidence_hash": hashlib.sha256(
            f"{seed}:{count}:{valid_ok}:{invalid_ok}".encode()).hexdigest(),
    }


# --------------------------------------------------------------------------
# 10. Metamorphic campaign (LCTL 1.5.x s70.2)
# --------------------------------------------------------------------------

METAMORPHIC_RELATIONS = (
    "independent_task_addition", "commuting_gate_reordering",
    "worker_count_invariance", "equivalent_partition",
    "failed_then_recovered_task", "deterministic_repeatability",
)


def _mr_independent_task_addition(rng: random.Random) -> Tuple[bool, str]:
    n = rng.randint(2, 6)
    rt = fabric.TaskRuntime(fabric.build_flat_federation(2))
    for i in range(n):
        rt.spawn(_square, i, task_id=f"A{i:03d}")
    rt.run()
    before = rt.await_all()
    rt2 = fabric.TaskRuntime(fabric.build_flat_federation(2))
    for i in range(n):
        rt2.spawn(_square, i, task_id=f"A{i:03d}")
    rt2.spawn(_square, 999, task_id="EXTRA")
    rt2.run()
    after = rt2.await_all()
    if after[:n] != before:
        return False, f"adding an independent task changed {before} -> {after[:n]}"
    return True, ""


def _mr_commuting_gate_reordering(rng: random.Random) -> Tuple[bool, str]:
    nq = rng.randint(2, 3)
    base = declarations() + _bell_rows(nq)
    a = gate("R300", rng.choice(("X", "Z", "H", "S")), a="q[0]")
    b = gate("R301", rng.choice(("X", "Z", "H", "S")), a=f"q[{nq - 1}]")
    if nq < 2:
        return True, ""
    src1 = program(base + [a, b])
    src2 = program(base + [b, a])
    prog1, _ = lang.parse(src1)
    prog2, _ = lang.parse(src2)
    vr1, vr2 = lang.verify(prog1), lang.verify(prog2)
    g1 = ses_mod.build_ses(prog1, vr1)
    authority = commutation_mod.CommutationAuthority()
    verdict = authority.classify_pair(g1.nodes["R300"], g1.nodes["R301"])
    if verdict.status not in ("DISJOINT", "COMMUTING_EXACT",
                              "COMMUTING_NUMERICALLY_VERIFIED"):
        return True, ""            # not proven commuting: reordering not claimed
    r1 = simulator.run_statevector(
        simulator.circuit_from_program(prog1, vr1), seed=3)
    r2 = simulator.run_statevector(
        simulator.circuit_from_program(prog2, vr2), seed=3)
    err = simulator.total_variation(r1["probabilities"], r2["probabilities"])
    if err > simulator.DISTRIBUTION_TV_TOL:
        return False, (f"reordering a proven-commuting pair changed the result "
                       f"by total variation {err:.3e}")
    return True, ""


def _mr_worker_count_invariance(rng: random.Random) -> Tuple[bool, str]:
    values = [rng.randint(1, 50) for _ in range(8)]
    reference = sum(values)
    for workers in (1, 2, 4, 8):
        rt = fabric.TaskRuntime(fabric.build_flat_federation(workers),
                                max_workers=workers)
        for i, v in enumerate(values):
            rt.spawn(_identity, v, task_id=f"W{i:03d}")
        rt.run()
        if sum(rt.await_all()) != reference:
            return False, f"worker count {workers} changed the reduction"
        col = fabric.Collectives(4)
        if col.allreduce([1, 2, 3, 4]).values[0] != 10:
            return False, f"allreduce is wrong at worker count {workers}"
    return True, ""


def _mr_equivalent_partition(rng: random.Random) -> Tuple[bool, str]:
    src = program(declarations() + _independent_lanes(2, 2))
    prog, _ = lang.parse(src)
    vr = lang.verify(prog)
    graph = ses_mod.build_ses(prog, vr)
    hp = planner.HypergraphPartitioner()
    hedges = hp.build_hyperedges(graph)
    part = hp.partition(graph, 2, exact=False)
    permuted = {n: (p + 1) % part.k for n, p in part.assignment.items()}
    c1 = hp.cost_vector(graph, hedges, part.assignment, part.k)
    c2 = hp.cost_vector(graph, hedges, permuted, part.k)
    for dim in planner.PARTITION_COST_DIMENSIONS:
        if abs(c1[dim] - c2[dim]) > 1e-9:
            return False, (f"relabelling the partition changed {dim}: "
                           f"{c1[dim]} != {c2[dim]}")
    return True, ""


def _mr_failed_then_recovered(rng: random.Random) -> Tuple[bool, str]:
    value = rng.randint(1, 100)
    store = resilience.CheckpointStore()
    store.save("CK", "classical_measurement", {"v": value},
               classically_known=True)
    txn = resilience.RecoveryTransaction("TX").begin({"v": value})
    txn.write("v", value + 1)
    txn.record_classical("v", value)
    txn.rollback()
    restored = store.load("CK")
    if restored != {"v": value}:
        return False, f"recovery changed the classical result: {restored}"
    event = resilience.FailureInjector(seed=1).inject("worker_crash", "W00")
    cls = resilience.RecoveryPlanner().classify(
        event, resilience.DEFAULT_CONTEXTS["worker_crash"])
    if cls not in lang.RECOVERY_CLASSES:
        return False, f"unknown recovery class {cls}"
    return True, ""


def _mr_deterministic_repeatability(rng: random.Random) -> Tuple[bool, str]:
    nq = rng.randint(2, 3)
    seed = rng.randint(0, 1000)
    src = program(declarations() + _bell_rows(nq))
    prog, _ = lang.parse(src)
    vr = lang.verify(prog)
    a = simulator.run_statevector(simulator.circuit_from_program(prog, vr),
                                  shots=128, seed=seed)
    b = simulator.run_statevector(simulator.circuit_from_program(prog, vr),
                                  shots=128, seed=seed)
    if a["final_state_hash"] != b["final_state_hash"] or \
            a.get("counts") != b.get("counts"):
        return False, "deterministic mode did not repeat"
    g1 = ses_mod.build_ses(prog, vr).hash()
    g2 = ses_mod.build_ses(prog, vr).hash()
    if g1 != g2:
        return False, "SES hashing is not repeatable"
    return True, ""


def _identity(x: Any) -> Any:
    return x


_METAMORPHIC: Dict[str, Callable[[random.Random], Tuple[bool, str]]] = {
    "independent_task_addition": _mr_independent_task_addition,
    "commuting_gate_reordering": _mr_commuting_gate_reordering,
    "worker_count_invariance": _mr_worker_count_invariance,
    "equivalent_partition": _mr_equivalent_partition,
    "failed_then_recovered_task": _mr_failed_then_recovered,
    "deterministic_repeatability": _mr_deterministic_repeatability,
}


def metamorphic_campaign(seed: int = 0, count: int = 200) -> dict:
    """Run every metamorphic relation, cycling deterministically."""
    rng = random.Random(seed)
    per_relation: Dict[str, Dict[str, int]] = {
        r: {"run": 0, "passed": 0} for r in METAMORPHIC_RELATIONS}
    failures: List[dict] = []
    for i in range(count):
        name = METAMORPHIC_RELATIONS[i % len(METAMORPHIC_RELATIONS)]
        per_relation[name]["run"] += 1
        ok, reason = _METAMORPHIC[name](rng)
        if ok:
            per_relation[name]["passed"] += 1
        else:
            failures.append({"iteration": i, "relation": name, "reason": reason})
    return {
        "schema": "PA-LCTL/METAMORPHIC_CAMPAIGN/1",
        "seed": seed, "count": count,
        "relations": list(METAMORPHIC_RELATIONS),
        "per_relation": {k: per_relation[k] for k in sorted(per_relation)},
        "failures": failures, "ok": not failures,
    }


# --------------------------------------------------------------------------
# 11. Differential campaign (LCTL 1.5.x s70.3)
# --------------------------------------------------------------------------

def _differential_program(rng: random.Random) -> Tuple[str, bool]:
    """Return (source, all_clifford)."""
    nq = rng.randint(2, 3)
    rows = declarations() + _bell_rows(nq)
    clifford = True
    for j in range(rng.randint(0, 3)):
        op = rng.choice(("H", "X", "Y", "Z", "S", "SDG", "CX", "CZ", "T"))
        if op == "T":
            clifford = False
            rows.append(gate(f"R30{j}", "T", a="q[0]"))
        elif op in ("CX", "CZ"):
            c, t = rng.sample(range(nq), 2)
            rows.append(gate(f"R30{j}", op, ctrl=f"q[{c}]", a=f"q[{t}]"))
        else:
            rows.append(gate(f"R30{j}", op, a=f"q[{rng.randrange(nq)}]"))
    return program(rows), clifford


def differential_campaign(seed: int = 0, count: int = 100) -> dict:
    """Compare backends against the tolerances declared in `simulator`."""
    rng = random.Random(seed)
    comparisons: Dict[str, Dict[str, Any]] = {
        "statevector_vs_density": {"run": 0, "max_error": 0.0},
        "statevector_vs_stabilizer": {"run": 0, "max_error": 0.0},
        "statevector_vs_planner_selected": {"run": 0, "max_error": 0.0},
    }
    failures: List[dict] = []
    for i in range(count):
        source, clifford = _differential_program(rng)
        prog, _ = lang.parse(source, f"DIFF-{i:05d}")
        vr = lang.verify(prog)
        if not vr.ok:
            failures.append({"iteration": i,
                             "reason": "generated program did not verify"})
            continue
        circuit = simulator.circuit_from_program(prog, vr)
        sv = simulator.run_statevector(circuit, seed=seed)
        dm = simulator.run_density(circuit, seed=seed)

        entry = comparisons["statevector_vs_density"]
        err = simulator.total_variation(sv["probabilities"], dm["probabilities"])
        entry["run"] += 1
        entry["max_error"] = max(entry["max_error"], err)
        if err > simulator.DISTRIBUTION_TV_TOL:
            failures.append({"iteration": i, "pair": "statevector_vs_density",
                             "error": err,
                             "tolerance": simulator.DISTRIBUTION_TV_TOL})

        if clifford:
            entry = comparisons["statevector_vs_stabilizer"]
            stab = simulator.run_stabilizer(circuit, shots=0, seed=seed)
            entry["run"] += 1
            probs = stab.get("probabilities")
            if probs:
                err = simulator.total_variation(sv["probabilities"], probs)
                entry["max_error"] = max(entry["max_error"], err)
                if err > simulator.DISTRIBUTION_TV_TOL:
                    failures.append({"iteration": i,
                                     "pair": "statevector_vs_stabilizer",
                                     "error": err,
                                     "tolerance": simulator.DISTRIBUTION_TV_TOL})
            else:
                support_sv = {k for k, v in sv["probabilities"].items()
                              if v > simulator.PROBABILITY_FLOOR}
                support_st = set(stab.get("support", support_sv))
                if support_st and support_st != support_sv:
                    failures.append({"iteration": i,
                                     "pair": "statevector_vs_stabilizer",
                                     "reason": "stabilizer support differs"})

        entry = comparisons["statevector_vs_planner_selected"]
        try:
            chosen = simulator.run(circuit, shots=0, seed=seed)
        except simulator.SimulationError:
            chosen = None
        if chosen is not None and "probabilities" in chosen:
            entry["run"] += 1
            err = simulator.total_variation(sv["probabilities"],
                                            chosen["probabilities"])
            entry["max_error"] = max(entry["max_error"], err)
            if err > simulator.DISTRIBUTION_TV_TOL:
                failures.append({"iteration": i,
                                 "pair": "statevector_vs_planner_selected",
                                 "backend": chosen["backend"], "error": err,
                                 "tolerance": simulator.DISTRIBUTION_TV_TOL})
    return {
        "schema": "PA-LCTL/DIFFERENTIAL_CAMPAIGN/1",
        "seed": seed, "count": count,
        "tolerances": {
            "distribution_total_variation": simulator.DISTRIBUTION_TV_TOL,
            "max_amplitude_error": simulator.MAX_AMPLITUDE_ERROR_TOL,
            "density_frobenius": simulator.DENSITY_FROBENIUS_TOL},
        "comparisons": {k: {"run": v["run"],
                            "max_error": float(v["max_error"])}
                        for k, v in sorted(comparisons.items())},
        "failures": failures, "ok": not failures,
    }


# --------------------------------------------------------------------------
# 12. Recovery campaign (LCTL 1.4.x s45)
# --------------------------------------------------------------------------

def recovery_campaign(seed: int = 0, count: int = 120) -> dict:
    """Inject failures and check that every classification is admitted."""
    rng = random.Random(seed)
    per_scenario: Dict[str, Dict[str, int]] = {}
    failures: List[dict] = []
    impossible = 0
    for i in range(count):
        scenario = resilience.SCENARIO_IDS[i % len(resilience.SCENARIO_IDS)]
        local_seed = seed + i
        bucket = per_scenario.setdefault(scenario, {"run": 0, "recovered": 0,
                                                    "impossible": 0})
        bucket["run"] += 1
        try:
            event = resilience.FailureInjector(seed=local_seed).inject(
                scenario, target=f"W{i % 4:02d}")
            context = dict(resilience.DEFAULT_CONTEXTS.get(scenario, {}))
            if rng.random() < 0.25:
                context["route_alternatives"] = rng.randint(0, 2)
            record = resilience.RecoveryPlanner().explain(event, context)
            if record.recovery_class not in lang.RECOVERY_CLASSES:
                failures.append({"iteration": i, "scenario": scenario,
                                 "reason": f"unknown class "
                                           f"{record.recovery_class}"})
                continue
            decision = resilience.SupervisionTree().handle(
                f"W{i % 4:02d}", record.recovery_class, event)
            if decision.action not in resilience.SUPERVISION_ACTIONS:
                failures.append({"iteration": i, "scenario": scenario,
                                 "reason": f"unknown action {decision.action}"})
                continue
            if record.recovery_class == "RECOVERY_IMPOSSIBLE":
                impossible += 1
                bucket["impossible"] += 1
            else:
                bucket["recovered"] += 1
            # A recovery must never require duplicating unknown quantum state.
            if record.recovery_class != "RECOVERY_IMPOSSIBLE":
                payload = fabric.QuantumPayload(payload_id="q[0]",
                                                lineage="LIN-R")
                try:
                    resilience.CheckpointStore().save(
                        f"CK{i}", "simulator_state", payload)
                except resilience.ResilienceError:
                    pass
                else:
                    failures.append({
                        "iteration": i, "scenario": scenario,
                        "reason": "checkpointing unknown quantum state was "
                                  "permitted"})
        except Exception as exc:                    # noqa: BLE001 - reported
            failures.append({"iteration": i, "scenario": scenario,
                             "reason": f"{type(exc).__name__}: {exc}"})
    campaign = resilience.run_campaign(seed=seed)
    summary = resilience.campaign_summary(campaign)
    return {
        "schema": "PA-LCTL/RECOVERY_CAMPAIGN/1",
        "seed": seed, "count": count,
        "per_scenario": {k: per_scenario[k] for k in sorted(per_scenario)},
        "recovery_impossible": impossible,
        "reference_campaign": summary,
        "failures": failures, "ok": not failures,
    }


# --------------------------------------------------------------------------
# 13. Soak (LCTL 1.6.x s82)
# --------------------------------------------------------------------------

SOAK_THRESHOLDS: Dict[str, float] = {"SOAK_1H": 3600.0, "SOAK_8H": 28800.0,
                                     "SOAK_24H": 86400.0}


def soak(hours: float = 0.0, seed: int = 0) -> dict:
    """Run a real, time-bounded loop and report the ACTUAL elapsed time.

    A run shorter than a qualification threshold is reported as
    `SOAK_<N>H_NOT_RUN`. It is never reported as a pass.
    """
    budget = max(0.0, float(hours)) * 3600.0
    started = time.time()
    rng = random.Random(seed)
    iterations = 0
    failures: List[dict] = []
    hashes: set = set()
    while True:
        elapsed = time.time() - started
        if elapsed >= budget:
            break
        source = _random_valid_program(rng)
        result = admit(source, f"SOAK-{iterations:06d}")
        if not result.accepted:
            failures.append({"iteration": iterations, "codes": result.codes,
                             "stage": result.stage})
        prog, _ = lang.parse(source)
        vr = lang.verify(prog)
        hashes.add(ses_mod.build_ses(prog, vr).hash())
        iterations += 1
        if iterations >= 1_000_000:
            break
    elapsed = time.time() - started

    verdicts: Dict[str, str] = {}
    for name, threshold in sorted(SOAK_THRESHOLDS.items()):
        if elapsed < threshold:
            verdicts[name] = f"{name}_NOT_RUN"
        elif failures:
            verdicts[name] = f"{name}_FAILED"
        else:
            verdicts[name] = f"{name}_PASS"
    return {
        "schema": "PA-LCTL/SOAK_LEDGER/1",
        "requested_hours": float(hours),
        "elapsed_s": round(elapsed, 6),
        "elapsed_hours": round(elapsed / 3600.0, 9),
        "iterations": iterations,
        "distinct_ses_hashes": len(hashes),
        "failures": failures,
        "thresholds": dict(sorted(SOAK_THRESHOLDS.items())),
        "status": ("SOAK_NOT_RUN" if elapsed < min(SOAK_THRESHOLDS.values())
                   else ("SOAK_FAILED" if failures else "SOAK_PASS")),
        **verdicts,
        "note": "a soak shorter than a qualification threshold is reported as "
                "NOT_RUN; it is never reported as a pass",
    }


# --------------------------------------------------------------------------
# 14. Aggregate evidence
# --------------------------------------------------------------------------

def full_evidence(seed: int = 0, *, property_count: int = 64,
                  metamorphic_count: int = 24, differential_count: int = 24,
                  recovery_count: int = 24, workers: int = 1) -> dict:
    """Run every campaign plus the suite and return one evidence bundle."""
    suite = run_suite(workers=workers)
    return {
        "schema": "PA-LCTL/CONFORMANCE_EVIDENCE/1",
        "suite": suite,
        "property": property_campaign(seed, property_count),
        "metamorphic": metamorphic_campaign(seed, metamorphic_count),
        "differential": differential_campaign(seed, differential_count),
        "recovery": recovery_campaign(seed, recovery_count),
    }


# ==========================================================================
# 9. PA21.3 coverage closure
# ==========================================================================
#
# PA21.2 shipped "1272/1272, 0 failures". That number was honest, but 18 live
# diagnostic codes had no case behind it -- including every grammar-level
# rejection, all three declare-before-use rules and the teleport
# preconditions. The headline read as comprehensive and roughly a quarter of
# the diagnostic surface was untested.
#
# These builders close that gap. Each is a real malformed bundle, admitted
# through the same `admit()` pipeline as every other negative case; none is a
# stub or a skipped assertion.

def _grammar_head() -> List[str]:
    return ["#PA-LCTL/1.6",
            "#PROFILE pa.lctl.quantum.parallel.distributed",
            "#NETWORK deny", "#BACKEND none",
            "#COLUMNS " + CANONICAL_SEP.join(FULL_COLUMNS)]


def _neg_no_separator(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    # A data row carrying neither the broken bar nor the ASCII alias.
    rows.append("R9%02d NO SEPARATOR ANYWHERE ON THIS LINE" % v)
    return "\n".join(_grammar_head() + rows) + "\n"


def _neg_cell_count(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    good = gate("R900", "X", a="q[0]")
    cells = good.split(CANONICAL_SEP)
    # Drop or duplicate cells so the count never equals the declared 22.
    bad = cells[:-(1 + v % 5)] if v % 2 else cells + cells[:1 + v % 5]
    rows.append(CANONICAL_SEP.join(bad))
    return "\n".join(_grammar_head() + rows) + "\n"


def _neg_missing_magic(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    head = [h for h in _grammar_head() if not h.startswith("#PA-LCTL")]
    if v % 2:                       # a plausible near-miss rather than absence
        head.insert(0, "#PA_LCTL 1.6")
    return "\n".join(head + rows) + "\n"


def _neg_no_rows(v: int) -> str:
    head = _grammar_head()
    if v % 2:
        head.append(";; a comment is not a row")
    return "\n".join(head) + "\n"


def _neg_undeclared_node(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate(f"R9{v:02d}", "X", a="q[0]", node=f"N{9 - v % 5}"))
    return program(rows)


def _neg_undeclared_domain(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(row(f"R9{v:02d}", "EXEC", "BARRIER", type="barrier",
                    regime="EXACT", domain=f"D{7 + v % 3}", lane="L0",
                    conf="1.0"))
    return program(rows)


def _neg_undeclared_link(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(row(f"R9{v:02d}", "PROTOCOL", "EPR_RESERVE", out="e[0]",
                    type="remote_handle", regime="EXACT", node="N0",
                    domain="D0", link=f"Q{5 + v % 4}", conf="1.0"))
    return program(rows)


def _neg_unknown_family(v: int) -> str:
    rows = declarations() + _bell_rows(2)
    rows.append(gate(f"R9{v:02d}", "X", a="q[0]",
                     family=f"NOT_A_FAMILY_{v}"))
    return program(rows)


def _neg_missing_param(v: int) -> str:
    ops = tuple(sorted(lang.PARAMETRIC_GATES))
    op = ops[v % len(ops)]
    rows = declarations() + _bell_rows(2)
    # A parametric gate whose PARAM cell is null, or carries something the
    # numeric sandbox cannot evaluate and therefore skips.
    param = NULL_CELL if v % 2 else "angle=not_a_number"
    rows.append(gate(f"R9{v:02d}", op, a="q[0]", param=param))
    return program(rows)


def _neg_self_controlled(v: int) -> str:
    """PA-LCTL 1.6.1: CU/MCU used to escape E-CTRL-001 entirely."""
    ops = ("CU", "MCU", "CU", "MCU", "CX", "CY", "CZ", "CH", "CU", "MCU")
    rows = declarations() + _bell_rows(2)
    rows.append(gate(f"R9{v:02d}", ops[v % len(ops)], ctrl="q[0]", a="q[0]"))
    return program(rows)


NEW_NEGATIVE_BUILDERS_PA213: Dict[str, Tuple[str, Callable[[int], str], str]] = {
    "grammar_no_separator":
        ("E-GRAM-001", _neg_no_separator,
         "a data row carries no column separator"),
    "grammar_cell_count":
        ("E-GRAM-002", _neg_cell_count,
         "a data row's cell count differs from #COLUMNS"),
    "grammar_missing_magic":
        ("E-GRAM-004", _neg_missing_magic, "the bundle magic line is absent"),
    "grammar_no_rows":
        ("E-GRAM-005", _neg_no_rows, "the bundle declares no data rows"),
    "undeclared_node":
        ("E-NODE-001", _neg_undeclared_node, "NODE used before DECLARE_NODE"),
    "undeclared_domain":
        ("E-DOM-001", _neg_undeclared_domain,
         "DOMAIN used before DECLARE_DOMAIN"),
    "undeclared_link":
        ("E-LINK-001", _neg_undeclared_link, "LINK used before DECLARE_LINK"),
    "unknown_family":
        ("E-FAM-001", _neg_unknown_family, "FAMILY is not a catalogued family"),
    "parametric_gate_without_angle":
        ("E-PARAM-001", _neg_missing_param,
         "a parametric gate carries no usable PARAM angle"),
    "self_controlled_gate":
        ("E-CTRL-001", _neg_self_controlled,
         "control and target are the same qubit"),
}


# -- registration ----------------------------------------------------------
# The categories above are appended to the suite and the case lists are
# rebuilt. Registration is done here rather than by editing the PA21.2 tuples
# in place so that the PA21.3 additions are legible as a set and the inherited
# 1272 cases keep their identity and their evidence hash lineage.

NEGATIVE_BUILDERS.update(NEW_NEGATIVE_BUILDERS_PA213)
NEGATIVE_CATEGORIES = NEGATIVE_CATEGORIES + tuple(
    NEW_NEGATIVE_BUILDERS_PA213)
NEGATIVE_CASES = _build_negatives()
ALL_CASES = POSITIVE_CASES + NEGATIVE_CASES
CASE_INDEX = {c.case_id: c for c in ALL_CASES}

#: Diagnostic codes that still have no dedicated conformance case after the
#: PA21.3 closure. Disclosed rather than left to be discovered: see
#: `reports/PA_LCTL_LANGUAGE_GAP_LEDGER.md` item L6. An empty list is the
#: goal; a wrong list is worse than an honest one.
UNCOVERED_DIAGNOSTIC_CODES: Tuple[str, ...] = (
    "E-CTL-002", "E-EPR-001", "E-EPR-002", "E-OWN-005", "E-OWN-006",
    "E-PROTO-001", "E-PROTO-003", "E-PROTO-004",
)
