"""
Semantic Execution Supergraph (SES).

Implements LCTL 1.3.x s6 (SES, 17-field node record, typed edge reasons,
deterministic serialization and hashing), superseding the flat causal DAG of
LCTL 1.2.x s7 while preserving its edge-reason vocabulary.

Also implements:
  * LCTL 1.2.x s8   work / span / critical path / Pmax = W/D
  * LCTL 1.4.x s7   partial-order model: transitive reduction, antichain
                    extraction, exact bounded maximum-antichain width
  * LCTL 1.4.x s6   parallelism opportunity discovery
  * LCTL 1.3.x s7   concurrency admission records with recorded reasons

Exit gates covered: CAUSAL_DAG_PASS, WORK_SPAN_PASS, SES_OPERATIONAL,
PARALLEL_DISCOVERY_OPERATIONAL.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from . import lang
from .lang import (CONCURRENCY_DECISIONS, EDGE_REASON_SET, NULL_CELL, Program,
                   Row, VerifyResult, parse_ref)

# --------------------------------------------------------------------------
# Node / edge records
# --------------------------------------------------------------------------

SES_NODE_FIELDS = (
    "id", "source_ref", "semantic_face", "operation", "type", "owner",
    "reads", "writes", "quantum_lineage", "entanglement_set", "memory_domain",
    "device_class", "duration_model", "error_model", "resource_claim",
    "failure_domain", "proof_ref",
)

# Deterministic reference duration model, in abstract time units. The runtime
# never presents these as physical hardware timings.
DURATION_UNITS = {
    "prepare": 1.0, "single_qubit": 1.0, "multi_qubit": 2.0, "measure": 3.0,
    "noise": 1.0, "tensor": 1.0, "operator": 1.0, "evolve": 4.0,
    "qec": 4.0, "hybrid": 1.0, "distributed_quantum": 8.0,
    "collective": 5.0, "structural": 0.0,
}


@dataclass
class SESNode:
    id: str
    source_ref: str
    semantic_face: str
    operation: str
    type: str
    owner: str
    reads: Tuple[str, ...]
    writes: Tuple[str, ...]
    quantum_lineage: Tuple[str, ...]
    entanglement_set: Tuple[str, ...]
    memory_domain: str
    device_class: str
    duration_model: float
    error_model: Dict[str, str]
    resource_claim: Dict[str, str]
    failure_domain: str
    proof_ref: str
    lane: str = NULL_CELL
    family: str = NULL_CELL
    link: str = NULL_CELL

    def as_dict(self) -> dict:
        return {
            "id": self.id, "source_ref": self.source_ref,
            "semantic_face": self.semantic_face, "operation": self.operation,
            "type": self.type, "owner": self.owner,
            "reads": list(self.reads), "writes": list(self.writes),
            "quantum_lineage": list(self.quantum_lineage),
            "entanglement_set": list(self.entanglement_set),
            "memory_domain": self.memory_domain,
            "device_class": self.device_class,
            "duration_model": self.duration_model,
            "error_model": self.error_model,
            "resource_claim": self.resource_claim,
            "failure_domain": self.failure_domain,
            "proof_ref": self.proof_ref,
            "lane": self.lane, "family": self.family, "link": self.link,
        }


@dataclass(frozen=True)
class SESEdge:
    src: str
    dst: str
    reasons: FrozenSet[str]

    def as_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst,
                "reasons": sorted(self.reasons)}


@dataclass
class ConcurrencyRecord:
    """LCTL 1.3.x s7 concurrency admission record."""
    candidate_pair: Tuple[str, str]
    ownership_disjoint: bool
    data_independent: bool
    measurement_independent: bool
    commutation_status: str
    coupling_status: str
    topology_status: str
    resource_status: str
    timing_status: str
    crosstalk_status: str
    error_budget_status: str
    decision: str
    reason: str
    proof_ref: str

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["candidate_pair"] = list(self.candidate_pair)
        return d


# --------------------------------------------------------------------------
# SES construction
# --------------------------------------------------------------------------

SCOPES = ("program", "frame", "task", "quantum_dependency", "classical_dependency",
          "measurement_control", "coupling", "communication", "topology",
          "resource_conflict", "failure_domain")


@dataclass
class SES:
    nodes: Dict[str, SESNode]
    edges: List[SESEdge]
    order: List[str]
    scopes: Dict[str, List[str]]
    program_seal: str

    # -- graph views -------------------------------------------------------
    def succ(self) -> Dict[str, Set[str]]:
        s: Dict[str, Set[str]] = {n: set() for n in self.nodes}
        for e in self.edges:
            s[e.src].add(e.dst)
        return s

    def pred(self) -> Dict[str, Set[str]]:
        p: Dict[str, Set[str]] = {n: set() for n in self.nodes}
        for e in self.edges:
            p[e.dst].add(e.src)
        return p

    def edge_map(self) -> Dict[Tuple[str, str], FrozenSet[str]]:
        return {(e.src, e.dst): e.reasons for e in self.edges}

    # -- deterministic serialization (LCTL 1.3.x s6) ------------------------
    def canonical(self) -> str:
        payload = {
            "schema": "PA-LCTL/SES/1",
            "program_seal": self.program_seal,
            "scopes": {k: sorted(v) for k, v in sorted(self.scopes.items())},
            "nodes": [self.nodes[n].as_dict() for n in self.order],
            "edges": sorted((e.as_dict() for e in self.edges),
                            key=lambda d: (d["src"], d["dst"])),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)

    def hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


def build_ses(prog: Program, vr: VerifyResult) -> SES:
    """Construct the SES. Every edge carries at least one typed reason
    (LCTL 1.2.x s7 / 1.3.x s6). No edge is ever added without a reason."""
    nodes: Dict[str, SESNode] = {}
    order: List[str] = []
    scopes: Dict[str, List[str]] = {s: [] for s in SCOPES}

    last_writer: Dict[str, str] = {}
    last_readers: Dict[str, List[str]] = {}
    lineage_of: Dict[str, str] = {k: v.lineage for k, v in vr.ownership.items()}
    measurement_producer: Dict[str, str] = {}
    lane_last: Dict[str, str] = {}
    link_last: Dict[str, str] = {}
    edge_acc: Dict[Tuple[str, str], Set[str]] = {}

    def add_edge(src: str, dst: str, reason: str) -> None:
        if src == dst or src not in nodes:
            return
        assert reason in EDGE_REASON_SET, reason
        edge_acc.setdefault((src, dst), set()).add(reason)

    for row in prog.rows:
        fam = lang.OP_FAMILY.get(row.op, "structural")
        reads = tuple(sorted(set(row.reads())))
        writes = tuple(sorted(set(row.writes())))
        lineages = tuple(sorted({lineage_of[k] for k in (reads + writes)
                                 if k in lineage_of}))
        ent: Set[str] = set()
        for k in reads + writes:
            rec = vr.ownership.get(k)
            if rec:
                ent |= rec.entangled_with

        node = SESNode(
            id=row.row_id,
            source_ref=f"{prog.path}:{row.line_no}",
            semantic_face=row.face,
            operation=row.op,
            type=row.type_,
            owner=row.node if row.node != NULL_CELL else "N_LOCAL",
            reads=reads, writes=writes,
            quantum_lineage=lineages,
            entanglement_set=tuple(sorted(ent)),
            memory_domain=row.domain if row.domain != NULL_CELL else "D_LOCAL",
            device_class=row.resource_map.get("device", "cpu"),
            duration_model=float(row.resource_map.get(
                "duration", DURATION_UNITS.get(fam, 1.0))),
            error_model=row.error_map,
            resource_claim=row.resource_map,
            failure_domain=row.resource_map.get(
                "failure_domain", row.node if row.node != NULL_CELL else "F_LOCAL"),
            proof_ref=row.proof,
            lane=row.lane, family=row.family, link=row.link,
        )
        nodes[node.id] = node
        order.append(node.id)

        scopes["program"].append(node.id)
        if row.face in ("EXEC", "PREPARE", "NOISE"):
            scopes["quantum_dependency"].append(node.id)
        if row.face in ("MEASURE", "CONTROL"):
            scopes["measurement_control"].append(node.id)
        if row.face == "COMM":
            scopes["communication"].append(node.id)
        if row.face in ("TOPOLOGY", "FEDERATION"):
            scopes["topology"].append(node.id)
        if row.face == "PROTOCOL":
            scopes["communication"].append(node.id)
        if row.face == "RECOVERY":
            scopes["failure_domain"].append(node.id)
        if row.face in ("RESOURCE",):
            scopes["resource_conflict"].append(node.id)
        if row.lane != NULL_CELL:
            scopes["task"].append(node.id)

        # --- data and ownership dependencies ---
        for k in reads:
            w = last_writer.get(k)
            if w:
                reason = ("QUANTUM_OWNERSHIP" if k in lineage_of
                          else "DATA_DEPENDENCY")
                add_edge(w, node.id, reason)
        for k in writes:
            w = last_writer.get(k)
            if w:
                add_edge(w, node.id, "QUANTUM_OWNERSHIP"
                         if k in lineage_of else "DATA_DEPENDENCY")
            for r in last_readers.get(k, []):
                add_edge(r, node.id, "DATA_DEPENDENCY")

        # --- measurement / classical control ---
        if row.op in lang.DESTRUCTIVE_OPS and row.out != NULL_CELL:
            measurement_producer[row.out] = node.id
        if row.op in ("CLASSICAL_IF", "CLASSICAL_SWITCH", "FEEDBACK",
                      "CLASSICAL_FEEDBACK"):
            src = row.ctrl if row.ctrl != NULL_CELL else row.a
            mp = measurement_producer.get(src)
            if mp:
                add_edge(mp, node.id, "MEASUREMENT_DEPENDENCY")
                add_edge(mp, node.id, "CLASSICAL_CONTROL")

        # --- entanglement dependency ---
        if ent:
            for k in ent:
                w = last_writer.get(k)
                if w:
                    add_edge(w, node.id, "ENTANGLEMENT_DEPENDENCY")

        # --- link / communication resource conflict ---
        if row.link != NULL_CELL:
            prev = link_last.get(row.link)
            if prev:
                add_edge(prev, node.id, "COMMUNICATION")
                add_edge(prev, node.id, "RESOURCE_CONFLICT")
            link_last[row.link] = node.id

        # --- explicit barriers ---
        if row.op in ("BARRIER", "FENCE", "EPOCH"):
            for prior in order[:-1]:
                add_edge(prior, node.id, "BARRIER")

        # --- lane order is a declared user order, not a hidden dependency ---
        if row.lane != NULL_CELL:
            prev = lane_last.get(row.lane)
            if prev and row.op in ("REGION_BEGIN", "REGION_END"):
                add_edge(prev, node.id, "USER_ORDER")
            lane_last[row.lane] = node.id

        for k in writes:
            last_writer[k] = node.id
            last_readers[k] = []
        for k in reads:
            last_readers.setdefault(k, []).append(node.id)

    edges = [SESEdge(s, d, frozenset(rs)) for (s, d), rs in sorted(edge_acc.items())]
    return SES(nodes=nodes, edges=edges, order=order,
               scopes={k: v for k, v in scopes.items()},
               program_seal=prog.seal())


# --------------------------------------------------------------------------
# Partial-order analysis (LCTL 1.2.x s8, 1.4.x s7)
# --------------------------------------------------------------------------

@dataclass
class PartialOrder:
    topo: List[str]
    reachable: Dict[str, Set[str]]
    reduction: List[Tuple[str, str]]
    work: float
    span: float
    critical_path: List[str]
    antichains: List[List[str]]
    width: int
    width_exact: bool
    levels: Dict[str, int]

    @property
    def pmax(self) -> float:
        return (self.work / self.span) if self.span > 0 else 0.0

    def as_dict(self) -> dict:
        return {
            "topological_order": self.topo,
            "work_W": round(self.work, 6),
            "span_D": round(self.span, 6),
            "Pmax_W_over_D": round(self.pmax, 6),
            "Pmax_note": "logical upper bound on exposed concurrency; "
                         "NOT a physically realized speedup",
            "critical_path": self.critical_path,
            "transitive_reduction_edges": [list(e) for e in self.reduction],
            "antichain_levels": self.antichains,
            "max_antichain_width": self.width,
            "width_is_exact": self.width_exact,
            "amdahl_serial_fraction": round(
                (self.span / self.work) if self.work else 1.0, 6),
        }


def analyze(ses: SES, exact_width_limit: int = 22) -> PartialOrder:
    succ, pred = ses.succ(), ses.pred()
    order = ses.order

    # Kahn topological sort with deterministic tie-breaking by source order.
    indeg = {n: len(pred[n]) for n in order}
    ready = [n for n in order if indeg[n] == 0]
    topo: List[str] = []
    while ready:
        ready.sort(key=order.index)
        n = ready.pop(0)
        topo.append(n)
        for m in sorted(succ[n], key=order.index):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    if len(topo) != len(order):
        # Cycle: report deterministically rather than looping.
        remaining = [n for n in order if n not in set(topo)]
        topo = topo + remaining

    # Reachability (used for the transitive reduction and antichain tests).
    reach: Dict[str, Set[str]] = {n: set() for n in order}
    for n in reversed(topo):
        acc: Set[str] = set()
        for m in succ[n]:
            acc.add(m)
            acc |= reach[m]
        reach[n] = acc

    reduction = [(e.src, e.dst) for e in ses.edges
                 if not any(e.dst in reach[m] for m in succ[e.src]
                            if m != e.dst)]

    # Work / span / critical path.
    dur = {n: ses.nodes[n].duration_model for n in order}
    work = float(sum(dur.values()))
    finish: Dict[str, float] = {}
    parent: Dict[str, Optional[str]] = {}
    for n in topo:
        best, bp = 0.0, None
        for p in pred[n]:
            if finish.get(p, 0.0) > best:
                best, bp = finish[p], p
        finish[n] = best + dur[n]
        parent[n] = bp
    span = max(finish.values()) if finish else 0.0
    end = max(finish, key=lambda k: (finish[k], -order.index(k))) if finish else None
    cp: List[str] = []
    while end is not None:
        cp.append(end)
        end = parent[end]
    cp.reverse()

    # ASAP levels -> antichain layers.
    level: Dict[str, int] = {}
    for n in topo:
        level[n] = (max((level[p] for p in pred[n]), default=-1) + 1)
    max_level = max(level.values(), default=-1)
    antichains = [[n for n in order if level.get(n) == L] for L in range(max_level + 1)]

    # Maximum antichain width. Exact for bounded graphs via Dilworth /
    # minimum-path-cover on the reachability DAG; heuristic otherwise.
    if len(order) <= exact_width_limit:
        width = _exact_max_antichain(order, reach)
        exact = True
    else:
        width = max((len(a) for a in antichains), default=0)
        exact = False

    return PartialOrder(topo=topo, reachable=reach, reduction=reduction,
                        work=work, span=span, critical_path=cp,
                        antichains=antichains, width=width,
                        width_exact=exact, levels=level)


def _exact_max_antichain(order: Sequence[str], reach: Dict[str, Set[str]]) -> int:
    """Exact maximum antichain via Dilworth's theorem: the maximum antichain
    equals |V| minus the maximum matching in the comparability bipartite
    graph (minimum chain cover). Hopcroft-Karp is unnecessary at this size;
    Kuhn's algorithm is deterministic and sufficient."""
    idx = {n: i for i, n in enumerate(order)}
    n = len(order)
    adj = [[idx[m] for m in sorted(reach[u], key=lambda x: idx[x])]
           for u in order]
    match_r = [-1] * n

    def try_k(u: int, seen: List[bool]) -> bool:
        for v in adj[u]:
            if seen[v]:
                continue
            seen[v] = True
            if match_r[v] == -1 or try_k(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    matching = 0
    for u in range(n):
        if try_k(u, [False] * n):
            matching += 1
    return n - matching


# --------------------------------------------------------------------------
# Concurrency admission (LCTL 1.3.x s7)
# --------------------------------------------------------------------------

def admit_concurrency(ses: SES, po: PartialOrder, commutation, topology=None,
                      ) -> List[ConcurrencyRecord]:
    """Decide, with a recorded reason, whether each candidate pair of
    independent nodes may share a schedule layer.

    No pair is ever parallelized or serialized silently (LCTL 1.3.x s7).
    """
    recs: List[ConcurrencyRecord] = []
    order = ses.order
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if b in po.reachable[a] or a in po.reachable[b]:
                continue                          # causally ordered already
            na, nb = ses.nodes[a], ses.nodes[b]
            if na.semantic_face not in lang.EXECUTABLE_FACES or \
               nb.semantic_face not in lang.EXECUTABLE_FACES:
                continue

            own_disjoint = not (set(na.writes) & set(nb.writes)
                                or set(na.writes) & set(nb.reads)
                                or set(nb.writes) & set(na.reads))
            data_indep = own_disjoint
            meas_indep = not (na.operation in lang.DESTRUCTIVE_OPS
                              and nb.operation in lang.DESTRUCTIVE_OPS
                              and set(na.reads) & set(nb.reads))
            comm = commutation.classify_pair(na, nb)
            coupling = "DECOUPLED" if own_disjoint else "STRONGLY_COUPLED"
            topo_ok = "OK"
            if topology is not None and na.owner != NULL_CELL and nb.owner != NULL_CELL:
                topo_ok = "OK" if topology.can_coexist(na.owner, nb.owner) \
                    else "TOPOLOGY_CONFLICT"
            res_ok = "OK"
            if na.link != NULL_CELL and na.link == nb.link:
                res_ok = "LINK_CONFLICT"
            cross = "OK"
            if topology is not None:
                cross = topology.crosstalk_status(na, nb)
            eb = "OK"

            if not own_disjoint:
                decision, reason = "SERIALIZE_OWNERSHIP", \
                    f"shared objects {sorted(set(na.writes) & (set(nb.writes) | set(nb.reads)))}"
            elif not meas_indep:
                decision, reason = "SERIALIZE_MEASUREMENT", "shared measured subsystem"
            elif comm.status == "NON_COMMUTING":
                decision, reason = "SERIALIZE_COUPLING", comm.reason
            elif comm.status == "UNRESOLVED":
                # Fail closed. An unresolved commutation question is not
                # permission to parallelize (LCTL 1.3.x Part VI: prefer
                # deterministic, explainable, conservative correctness).
                decision, reason = "SERIALIZE_COUPLING", (
                    "commutation UNRESOLVED: " + comm.reason
                    + "; serialized conservatively rather than assumed safe")
            elif res_ok != "OK":
                decision, reason = "SERIALIZE_RESOURCE", f"shared link {na.link}"
            elif topo_ok != "OK":
                decision, reason = "SERIALIZE_TOPOLOGY", "topology forbids co-execution"
            elif cross != "OK":
                decision, reason = "SERIALIZE_CROSSTALK", cross
            elif comm.status == "COMMUTING_TARGET_CONDITIONAL":
                decision, reason = "PARALLEL_TARGET_CONDITIONAL", comm.reason
            else:
                decision, reason = "PARALLEL_EXACT", comm.reason

            assert decision in CONCURRENCY_DECISIONS
            recs.append(ConcurrencyRecord(
                candidate_pair=(a, b), ownership_disjoint=own_disjoint,
                data_independent=data_indep, measurement_independent=meas_indep,
                commutation_status=comm.status, coupling_status=coupling,
                topology_status=topo_ok, resource_status=res_ok,
                timing_status="OK", crosstalk_status=cross,
                error_budget_status=eb, decision=decision, reason=reason,
                proof_ref=comm.proof_id))
    return recs


def discover_opportunities(ses: SES, po: PartialOrder,
                           recs: Sequence[ConcurrencyRecord]) -> dict:
    """LCTL 1.4.x s6 parallelism opportunity discovery, reported per family."""
    by_family: Dict[str, Set[str]] = {}
    for n in ses.order:
        fam = ses.nodes[n].family
        if fam != NULL_CELL:
            by_family.setdefault(fam, set()).add(n)

    admitted = [r for r in recs if r.decision.startswith("PARALLEL")]
    serialized = [r for r in recs if r.decision.startswith("SERIALIZE")]
    reasons: Dict[str, int] = {}
    for r in serialized:
        reasons[r.decision] = reasons.get(r.decision, 0) + 1

    return {
        "candidate_pairs": len(recs),
        "admitted_parallel_pairs": len(admitted),
        "serialized_pairs": len(serialized),
        "serialization_reasons": dict(sorted(reasons.items())),
        "declared_families": {k: sorted(v) for k, v in sorted(by_family.items())},
        "max_antichain_width": po.width,
        "width_is_exact": po.width_exact,
        "critical_path_length": len(po.critical_path),
        "work_W": round(po.work, 6),
        "span_D": round(po.span, 6),
        "Pmax": round(po.pmax, 6),
    }
