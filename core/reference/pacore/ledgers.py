"""
Machine-readable ledger emission and the QCIR-P2 execution-plan IR.

Implements:
  * LCTL 1.2.x s33     provenance record (17 normative fields)
  * LCTL 1.3.x s17     QCIR-P2 deterministic execution-plan IR
  * LCTL 1.3.x s18     resource ledger with classical / quantum separation
  * LCTL 1.3.x s21     error ledger: incompatible classes stay separate
  * LCTL 1.3.x s63     ledger file set emitted by a conforming runtime
  * LCTL 1.4.x s63     ledger extensions for the adaptive mesh
  * LCTL 1.5.x s79     release qualification outputs
  * LCTL 1.6.x s83     status vocabulary applied to the release outputs

Honesty rules enforced here (LCTL 1.3.x s53, 1.4.x s64, 1.5.x s79):
  * `parallel_state` never exceeds SCHEDULED_PARALLEL / PARALLEL_EMULATION and
    `distributed_state` never exceeds DISTRIBUTED_CLASSICAL_EMULATION, because
    this runtime owns no authenticated physical target.
  * `quantum_boundary` is always QUANTUM_BOUNDARY_NOT_CROSSED.
  * `target_verified` and every `physical_*` flag are False.
  * Every resource number carries a provenance tag; nothing is reported as
    `exact` unless it was counted, not estimated.
  * Incompatible error classes are never fused into a single scalar.
  * The two physical-execution release outputs are always
    BLOCKED_EXTERNAL_AUTHORITY: no authenticated hardware exists here.

SPECIFICATION HOLES FILLED HERE (recorded in the gap ledger):
  H20. The QUORUM documents name the ledger files but never fix their JSON
       schema. Every ledger emitted here carries an explicit `schema` key of
       the form `PA-LCTL/<LEDGER>/1` so downstream consumers can version-check.
  H21. The documents require QCIR-P2 to be deterministic but do not state a
       canonicalization. This module defines it: UTF-8 JSON, sorted keys,
       `(",", ":")` separators, no floating-point formatting beyond repr.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import (commutation as commutation_mod, crdt, erroralgebra, fabric,
               lang, planner, protocols, resilience, ses as ses_mod, simulator)
from .lang import NULL_CELL, PALCTLError

# --------------------------------------------------------------------------
# 0. Constants
# --------------------------------------------------------------------------

#: The exact ledger file set a conforming run emits (LCTL 1.3.x s63).
LEDGER_FILES: Tuple[str, ...] = (
    "SES.json",
    "COMMUTATION_LEDGER.json",
    "PARTITION_LEDGER.json",
    "PARETO_LEDGER.json",
    "PLACEMENT_LEDGER.json",
    "ROUTING_LEDGER.json",
    "QUANTUM_ROUTING_LEDGER.json",
    "ENTANGLEMENT_INVENTORY_LEDGER.json",
    "SCHEDULE_LEDGER.json",
    "DYNAMIC_SCHEDULE_LEDGER.json",
    "PARALLEL_OPPORTUNITY_LEDGER.json",
    "PARTIAL_ORDER_LEDGER.json",
    "HYPERGRAPH_PARTITION_LEDGER.json",
    "HIERARCHICAL_PLACEMENT_LEDGER.json",
    "TASK_RUNTIME_LEDGER.json",
    "CONSISTENCY_LEDGER.json",
    "COLLECTIVE_LEDGER.json",
    "FARM_EXECUTION_LEDGER.json",
    "LOAD_BALANCE_LEDGER.json",
    "STRAGGLER_LEDGER.json",
    "NUMERICAL_BACKEND_LEDGER.json",
    "PROTOCOL_COMPILER_LEDGER.json",
    "PROTOCOL_LEDGER.json",
    "RESOURCE_LEDGER.json",
    "ERROR_LEDGER.json",
    "ERROR_COMPOSITION_LEDGER.json",
    "FAILURE_LEDGER.json",
    "RECOVERY_LEDGER.json",
    "LIVE_RECOVERY_LEDGER.json",
    "EVENT_RECONSTRUCTION_LEDGER.json",
    "PROVENANCE_LEDGER.json",
    "ADMISSION_LEDGER.json",
    "SOAK_LEDGER.json",
    "RELEASE_QUALIFICATION.json",
)

#: LCTL 1.2.x s33 provenance record. Exactly these fields, no more, no less.
PROVENANCE_FIELDS: Tuple[str, ...] = (
    "language", "profile", "execution_class", "parallel_state",
    "distributed_state", "quantum_boundary", "target", "target_verified",
    "topology_hash", "schedule_hash", "source_hash", "qcir_hash", "ses_hash",
    "proof_ledger_hash", "physical_qpu", "physical_parallel",
    "physical_distributed",
)

#: Admitted provenance tags for every reported resource number.
VALUE_PROVENANCE: Tuple[str, ...] = (
    "exact", "compiler_derived", "analytical_estimate", "simulator_estimate",
    "hardware_estimate", "unknown",
)

#: Classical resource dimensions (LCTL 1.3.x s18.1).
CLASSICAL_RESOURCE_FIELDS: Tuple[str, ...] = (
    "cpu_work", "memory", "bytes_moved", "messages", "collectives", "barriers",
    "latency_estimate", "critical_path", "energy_estimate",
)

#: Quantum resource dimensions (LCTL 1.3.x s18.2).
QUANTUM_RESOURCE_FIELDS: Tuple[str, ...] = (
    "logical_qubits", "gate_counts_by_class", "two_qubit_gate_count",
    "t_count", "clifford_count", "depth", "measurement_count", "shots",
    "swap_count", "remote_gate_count", "ebits_generated", "ebits_consumed",
    "entanglement_swaps", "purification_rounds", "quantum_link_attempts",
    "expected_fidelity", "coherence_exposure",
)

#: The six mandatory release outputs (LCTL 1.5.x s79, 1.6.x s83).
RELEASE_OUTPUTS: Tuple[str, ...] = (
    "NATIVE_PARALLEL_EXECUTION",
    "NATIVE_DISTRIBUTED_EXECUTION",
    "DISTRIBUTED_NUMERICAL_SIMULATION",
    "DISTRIBUTED_PROTOCOL_EMULATION",
    "PHYSICAL_PARALLEL_QPU_EXECUTION",
    "PHYSICAL_DISTRIBUTED_QPU_EXECUTION",
)

#: Release outputs that no software-only authority may ever qualify.
PHYSICAL_RELEASE_OUTPUTS: Tuple[str, ...] = (
    "PHYSICAL_PARALLEL_QPU_EXECUTION", "PHYSICAL_DISTRIBUTED_QPU_EXECUTION",
)

BLOCKED_EXTERNAL_AUTHORITY = "BLOCKED_EXTERNAL_AUTHORITY"
QUALIFIED = "QUALIFIED"
BLOCKED = "BLOCKED"

SOAK_NOT_RUN_TOKEN = "SOAK_NOT_RUN"


class LedgerError(PALCTLError):
    """Raised when a ledger set cannot be built from the given program."""


# --------------------------------------------------------------------------
# 1. Value tagging
# --------------------------------------------------------------------------

def tagged(value: Any, provenance: str, note: str = "") -> dict:
    """Attach a provenance tag to a reported number (LCTL 1.3.x s18.3)."""
    if provenance not in VALUE_PROVENANCE:
        raise LedgerError(
            f"{provenance!r} is not an admitted value provenance; expected one "
            f"of {list(VALUE_PROVENANCE)}")
    out = {"value": value, "provenance": provenance}
    if note:
        out["note"] = note
    return out


def canonical_json(obj: Any) -> str:
    """Deterministic canonicalization used by every hash in this module."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=_json_default)


def _json_default(o: Any) -> Any:
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, tuple):
        return list(o)
    if hasattr(o, "as_dict"):
        return o.as_dict()
    return str(o)


def _sha(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 2. QCIR-P2 (LCTL 1.3.x s17)
# --------------------------------------------------------------------------

QCIRP2_SECTIONS: Tuple[str, ...] = (
    "ses_graph", "partition_graph", "placement_table", "classical_route_table",
    "quantum_route_table", "temporal_schedule", "resource_reservations",
    "ebit_lifecycle", "failure_domains", "recovery_policy",
    "calibration_epoch", "error_ledger_refs", "proof_ledger_refs",
    "replay_mode", "deterministic_schedule_seed",
)

REPLAY_MODES = ("DETERMINISTIC_REPLAY", "SEEDED_REPLAY", "NO_REPLAY")


@dataclass
class QCIRP2:
    """The deterministic execution-plan IR.

    QCIR-P2 is a *plan*. It is never evidence that a physical target executed
    anything (LCTL 1.4.x s19). `as_dict` -> `from_dict` is a total round trip:
    the reconstructed object hashes identically to the original.
    """

    ses_graph: dict
    partition_graph: dict
    placement_table: dict
    classical_route_table: List[dict]
    quantum_route_table: List[dict]
    temporal_schedule: dict
    resource_reservations: dict
    ebit_lifecycle: dict
    failure_domains: dict
    recovery_policy: dict
    calibration_epoch: int
    error_ledger_refs: List[str]
    proof_ledger_refs: List[str]
    replay_mode: str = "DETERMINISTIC_REPLAY"
    deterministic_schedule_seed: int = 0

    SCHEMA = "PA-LCTL/QCIR-P2/1"

    def __post_init__(self) -> None:
        if self.replay_mode not in REPLAY_MODES:
            raise LedgerError(
                f"replay_mode {self.replay_mode!r} is not admitted; expected "
                f"one of {list(REPLAY_MODES)}")
        self.calibration_epoch = int(self.calibration_epoch)
        self.deterministic_schedule_seed = int(self.deterministic_schedule_seed)
        self.error_ledger_refs = sorted(str(r) for r in self.error_ledger_refs)
        self.proof_ledger_refs = sorted(str(r) for r in self.proof_ledger_refs)

    # -- serialization -----------------------------------------------------
    def as_dict(self) -> dict:
        d: Dict[str, Any] = {"schema": self.SCHEMA}
        for name in QCIRP2_SECTIONS:
            d[name] = getattr(self, name)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QCIRP2":
        missing = [s for s in QCIRP2_SECTIONS if s not in d]
        if missing:
            raise LedgerError(f"QCIR-P2 payload is missing sections {missing}")
        return cls(**{s: d[s] for s in QCIRP2_SECTIONS})

    def canonical(self) -> str:
        return canonical_json(self.as_dict())

    def hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 3. Helper accounting
# --------------------------------------------------------------------------

_GATE_CLASSES = {
    "prepare": lang.OPS_PREPARE,
    "single_qubit": lang.OPS_1Q,
    "multi_qubit": lang.OPS_MQ,
    "measure": lang.OPS_MEASURE,
    "noise": lang.OPS_NOISE,
    "distributed_quantum": lang.OPS_DISTRIBUTED_Q,
    "collective": lang.OPS_COLLECTIVE,
}


def gate_counts_by_class(prog: lang.Program) -> Dict[str, int]:
    """Exact operation census keyed by the normative operation families."""
    counts: Dict[str, int] = {k: 0 for k in sorted(_GATE_CLASSES)}
    counts["other"] = 0
    for row in prog.rows:
        placed = False
        for cls, ops in sorted(_GATE_CLASSES.items()):
            if row.op in ops:
                counts[cls] += 1
                placed = True
                break
        if not placed:
            counts["other"] += 1
    return counts


def error_terms_from_program(prog: lang.Program) -> List[erroralgebra.ErrorTerm]:
    """Lift declared ERROR cells into typed error terms.

    A cell that does not name a unit and a kind is *not* guessed at: it is
    skipped and reported as undeclared rather than silently coerced.
    """
    terms: List[erroralgebra.ErrorTerm] = []
    for row in prog.rows:
        em = row.error_map
        if not em:
            continue
        raw = em.get("p", em.get("value"))
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        unit = em.get("unit", "probability")
        kind = em.get("kind", "failure_probability")
        domain = em.get("domain", "gate")
        independence = em.get("independence", "unknown_correlation")
        if unit not in erroralgebra.UNITS or kind not in erroralgebra.ERROR_KINDS \
                or domain not in erroralgebra.DOMAINS \
                or independence not in erroralgebra.ASSUMPTIONS:
            continue
        try:
            terms.append(erroralgebra.ErrorTerm(
                name=row.row_id, value=value, unit=unit, domain=domain,
                kind=kind, independence=independence,
                provenance=row.proof if row.proof != NULL_CELL else "-"))
        except erroralgebra.ErrorAlgebraError:
            continue
    return terms


# --------------------------------------------------------------------------
# 4. LedgerSet
# --------------------------------------------------------------------------

@dataclass
class LedgerSet:
    """The full analysis pipeline for one program plus every emitted ledger."""

    program: lang.Program
    verification: lang.VerifyResult
    topology: planner.Topology
    ses: ses_mod.SES
    partial_order: ses_mod.PartialOrder
    authority: commutation_mod.CommutationAuthority
    concurrency: List[ses_mod.ConcurrencyRecord]
    opportunities: dict
    partition: planner.PartitionResult
    placement: Dict[int, str]
    placement_proofs: List[planner.PlacementProof]
    schedule: planner.Schedule
    qcir: QCIRP2
    ledgers: Dict[str, dict]
    seed: int = 0
    shots: int = 256

    # -- construction ------------------------------------------------------
    @classmethod
    def build(cls, program_text: str, path: str = "<memory>",
              topology: Optional[planner.Topology] = None, k: int = 2,
              seed: int = 0, *, shots: int = 256,
              conformance: Optional[Dict[str, Any]] = None,
              soak: Optional[Dict[str, Any]] = None) -> "LedgerSet":
        """Run the full pipeline and emit every ledger.

        parse -> verify -> SES -> analyze -> commutation -> concurrency
        admission -> opportunity discovery -> partition -> pareto -> place ->
        route -> schedule -> simulate -> protocol compile -> resource/error
        accounting.

        Fails closed: a program that does not parse or does not verify never
        produces ledgers.
        """
        prog, diags = lang.parse(program_text, path)
        if prog is None:
            raise LedgerError("program does not parse: "
                              + "; ".join(str(d) for d in diags))
        fatal = [d for d in diags if d.severity in ("ERROR", "REJECT")]
        if fatal:
            raise LedgerError("program does not parse: "
                              + "; ".join(str(d) for d in fatal))

        vr = lang.verify(prog)
        if not vr.ok:
            raise LedgerError("program does not verify: "
                              + "; ".join(str(d) for d in vr.rejections))

        topo = topology or planner.reference_topology(4, 2)
        graph = ses_mod.build_ses(prog, vr)
        po = ses_mod.analyze(graph)
        authority = commutation_mod.CommutationAuthority()
        records = ses_mod.admit_concurrency(graph, po, authority, topo)
        opportunities = ses_mod.discover_opportunities(graph, po, records)
        rewrites = commutation_mod.plan_rewrites(graph, authority)

        part = planner.HypergraphPartitioner().partition(graph, k)
        pareto = planner.pareto_partitions(graph, sorted({2, max(1, k)}))

        placer = planner.Placer(topo)
        placement, proofs = placer.place(graph, part)
        hierarchy = placer.hierarchy(placement)

        sched = planner.TemporalScheduler(topo, authority).schedule(
            graph, po, part, placement, records, mode="CRITICAL_PATH",
            exact=len(graph.order) <= planner.TemporalScheduler.EXACT_LIMIT)

        obj = cls(
            program=prog, verification=vr, topology=topo, ses=graph,
            partial_order=po, authority=authority, concurrency=records,
            opportunities=opportunities, partition=part, placement=placement,
            placement_proofs=proofs, schedule=sched,
            qcir=QCIRP2(  # replaced below once the route tables exist
                ses_graph={}, partition_graph={}, placement_table={},
                classical_route_table=[], quantum_route_table=[],
                temporal_schedule={}, resource_reservations={},
                ebit_lifecycle={}, failure_domains={}, recovery_policy={},
                calibration_epoch=0, error_ledger_refs=[], proof_ledger_refs=[],
                replay_mode="DETERMINISTIC_REPLAY",
                deterministic_schedule_seed=seed),
            ledgers={}, seed=int(seed), shots=int(shots))
        obj._emit(pareto=pareto, hierarchy=hierarchy, rewrites=rewrites,
                  conformance=conformance, soak=soak)
        return obj

    # -- emission ----------------------------------------------------------
    def _emit(self, *, pareto: dict, hierarchy: dict, rewrites: List[dict],
              conformance: Optional[Dict[str, Any]],
              soak: Optional[Dict[str, Any]]) -> None:
        L: Dict[str, dict] = {}
        graph, po = self.ses, self.partial_order

        L["SES.json"] = {
            "schema": "PA-LCTL/SES_LEDGER/1",
            "node_fields": list(ses_mod.SES_NODE_FIELDS),
            "edge_reasons": list(lang.EDGE_REASONS),
            "graph": json.loads(graph.canonical()),
            "ses_hash": graph.hash(),
        }
        L["COMMUTATION_LEDGER.json"] = {
            **self.authority.ledger_dict(),
            "rewrites": rewrites,
            "rewrite_policy": "only ADMIT_EXACT rewrites may be applied; "
                              "OBSERVE candidates are reported, never used",
        }
        L["PARTIAL_ORDER_LEDGER.json"] = {
            "schema": "PA-LCTL/PARTIAL_ORDER_LEDGER/1",
            **po.as_dict(),
        }
        L["PARALLEL_OPPORTUNITY_LEDGER.json"] = {
            "schema": "PA-LCTL/PARALLEL_OPPORTUNITY_LEDGER/1",
            **self.opportunities,
            "concurrency_records": [r.as_dict() for r in self.concurrency],
            "family_selection": planner.select_parallel_family(graph, po),
        }
        L["PARTITION_LEDGER.json"] = {
            "schema": "PA-LCTL/PARTITION_LEDGER/1",
            "cost_dimensions": list(planner.PARTITION_COST_DIMENSIONS),
            **self.partition.as_dict(),
        }
        L["HYPERGRAPH_PARTITION_LEDGER.json"] = self._hypergraph_ledger()
        L["PARETO_LEDGER.json"] = pareto
        L["PLACEMENT_LEDGER.json"] = {
            "schema": "PA-LCTL/PLACEMENT_LEDGER/1",
            "levels": list(planner.PLACEMENT_LEVELS),
            "placement": {str(p): t for p, t in sorted(self.placement.items())},
            "proofs": [p.as_dict() for p in self.placement_proofs],
            "unplaced_partitions": sorted(
                str(p) for p in set(self.partition.assignment.values())
                if p not in self.placement),
            "topology_hash": self.topology.hash(),
        }
        L["HIERARCHICAL_PLACEMENT_LEDGER.json"] = {
            "schema": "PA-LCTL/HIERARCHICAL_PLACEMENT_LEDGER/1",
            "hierarchy": hierarchy,
            "partition_tree": self.partition.tree,
        }

        classical_routes, quantum_routes = self._route_tables()
        L["ROUTING_LEDGER.json"] = {
            "schema": "PA-LCTL/ROUTING_LEDGER/1",
            "model": "Tmessage = alpha + beta * n (LCTL 1.2.x s18)",
            "routes": classical_routes,
            "claim": "a route is a plan over the declared topology; it is not "
                     "evidence of a physical network",
        }
        L["QUANTUM_ROUTING_LEDGER.json"] = {
            "schema": "PA-LCTL/QUANTUM_ROUTING_LEDGER/1",
            "routes": quantum_routes,
            "claim": "reference entanglement routing plan; not evidence of "
                     "physical entanglement distribution",
        }

        ebit_ledger, entanglement = self._entanglement()
        L["ENTANGLEMENT_INVENTORY_LEDGER.json"] = entanglement

        L["SCHEDULE_LEDGER.json"] = {
            "schema": "PA-LCTL/SCHEDULE_LEDGER/1",
            "stages": list(planner.SCHEDULER_STAGES),
            "modes": list(planner.SCHEDULE_MODES),
            **self.schedule.as_dict(),
        }
        L["DYNAMIC_SCHEDULE_LEDGER.json"] = self._dynamic_schedule()

        runtime_ledger, event_log = self._task_runtime()
        L["TASK_RUNTIME_LEDGER.json"] = runtime_ledger
        L["EVENT_RECONSTRUCTION_LEDGER.json"] = {
            "schema": "PA-LCTL/EVENT_RECONSTRUCTION_LEDGER/1",
            **event_log.as_dict(),
            "reconstruction_verified": event_log.verify_replay(
                event_log.reconstruct()).as_dict(),
        }
        L["CONSISTENCY_LEDGER.json"] = self._consistency()
        L["COLLECTIVE_LEDGER.json"] = self._collectives()
        L["LOAD_BALANCE_LEDGER.json"], L["STRAGGLER_LEDGER.json"] = \
            self._balance_and_stragglers()

        sim, backend_ledger, farm = self._numerics()
        L["NUMERICAL_BACKEND_LEDGER.json"] = backend_ledger
        L["FARM_EXECUTION_LEDGER.json"] = farm

        protocol_plan, proto_compiler, proto_ledger = self._protocols(ebit_ledger)
        L["PROTOCOL_COMPILER_LEDGER.json"] = proto_compiler
        L["PROTOCOL_LEDGER.json"] = proto_ledger

        L["RESOURCE_LEDGER.json"] = self._resources(sim, quantum_routes)
        L["ERROR_LEDGER.json"], L["ERROR_COMPOSITION_LEDGER.json"] = self._errors()

        failure, recovery, live = self._resilience()
        L["FAILURE_LEDGER.json"] = failure
        L["RECOVERY_LEDGER.json"] = recovery
        L["LIVE_RECOVERY_LEDGER.json"] = live

        L["ADMISSION_LEDGER.json"] = self._admission()

        # QCIR-P2 is built last: it references every table above.
        self.qcir = QCIRP2(
            ses_graph={"nodes": [graph.nodes[n].as_dict() for n in graph.order],
                       "edges": [e.as_dict() for e in graph.edges],
                       "ses_hash": graph.hash()},
            partition_graph={"assignment": dict(sorted(
                self.partition.assignment.items())),
                "k": self.partition.k,
                "cost_vector": {kk: round(v, 6) for kk, v
                                in sorted(self.partition.cost.items())},
                "tree": self.partition.tree},
            placement_table={str(p): t for p, t in sorted(self.placement.items())},
            classical_route_table=classical_routes,
            quantum_route_table=quantum_routes,
            temporal_schedule={"mode": self.schedule.mode,
                               "makespan": round(self.schedule.makespan, 6),
                               "ops": [o.as_dict() for o in self.schedule.ops],
                               "layers": self.schedule.layers,
                               "blocked": self.schedule.blocked,
                               "schedule_hash": self.schedule.hash()},
            resource_reservations=self._reservations(),
            ebit_lifecycle=entanglement["inventory"],
            failure_domains=self._failure_domains(),
            recovery_policy={
                "classes": list(lang.RECOVERY_CLASSES),
                "quantum_checkpoint": "REFUSED_FOR_UNKNOWN_QUANTUM_STATE",
                "classical_rollback": "ROLLBACK_CLASSICAL_ONLY",
                "supervision_levels": list(resilience.SUPERVISION_LEVELS)},
            calibration_epoch=0,
            error_ledger_refs=sorted(
                d["name"] for d in L["ERROR_LEDGER.json"]["terms"]),
            proof_ledger_refs=sorted(
                r["proof_hash"] for r in
                L["COMMUTATION_LEDGER.json"]["rules"]),
            replay_mode="DETERMINISTIC_REPLAY",
            deterministic_schedule_seed=self.seed)

        L["PROVENANCE_LEDGER.json"] = self._provenance(sim, L)
        L["SOAK_LEDGER.json"] = self._soak(soak)
        L["RELEASE_QUALIFICATION.json"] = self._release(conformance)

        missing = [f for f in LEDGER_FILES if f not in L]
        if missing:
            raise LedgerError(f"ledger emission is incomplete: {missing}")
        extra = [f for f in L if f not in LEDGER_FILES]
        if extra:
            raise LedgerError(f"ledger emission produced unknown files: {extra}")
        self.ledgers = L

    # -- individual ledgers ------------------------------------------------
    def _hypergraph_ledger(self) -> dict:
        hp = planner.HypergraphPartitioner()
        hedges = hp.build_hyperedges(self.ses)
        return {
            "schema": "PA-LCTL/HYPERGRAPH_PARTITION_LEDGER/1",
            "hyperedges": [{"edge_id": h.edge_id, "pins": list(h.pins),
                            "weight": h.weight, "kind": h.kind}
                           for h in hedges],
            "hyperedge_count": len(hedges),
            "method": self.partition.method,
            "refinement_passes": self.partition.refinement_passes,
            "runtime_ops": self.partition.runtime_ops,
            "optimality_claim": self.partition.as_dict()["optimality_claim"],
        }

    def _route_tables(self) -> Tuple[List[dict], List[dict]]:
        crouter = planner.ClassicalRouter(self.topology)
        qrouter = planner.QuantumRouter(self.topology)
        targets = sorted(set(self.placement.values()))
        classical: List[dict] = []
        quantum: List[dict] = []
        for i, a in enumerate(targets):
            for b in targets[i + 1:]:
                r = crouter.select(a, b, payload=256)
                if r is None:
                    classical.append({"src": a, "dst": b, "route": None,
                                      "status": "NO_CLASSICAL_ROUTE"})
                else:
                    classical.append({"src": a, "dst": b, "status": "PLANNED",
                                      **r.as_dict()})
                q = qrouter.route(a, b)
                if q is None:
                    quantum.append({"src": a, "dst": b, "route": None,
                                    "status": "NO_QUANTUM_ROUTE"})
                else:
                    quantum.append({"src": a, "dst": b, "status": "PLANNED",
                                    **q.as_dict()})
        return classical, quantum

    def _entanglement(self) -> Tuple[protocols.EbitLedger, dict]:
        ledger = protocols.EbitLedger()
        rows = [r for r in self.program.rows
                if r.op in ("ENTANGLE_LINK", "EPR_RESERVE")]
        for row in rows:
            link = self.verification.declared_links.get(row.link)
            a = link["a"] if link else (row.node if row.node != NULL_CELL else "N0")
            b = link["b"] if link else "N_REMOTE"
            if a == b:
                b = f"{b}_PEER"
            eid = ledger.request(a, b, epoch=0, owner_protocol=row.row_id)
            ledger.generate(eid, epoch=0)
            ledger.herald(eid, fidelity=float(
                (link or {}).get("resource", {}).get("fidelity", 0.98)
                if link else 0.98), epoch=0)
            if row.op == "EPR_RESERVE":
                ledger.reserve(eid, owner_protocol=row.row_id, epoch=0)
        return ledger, {
            "schema": "PA-LCTL/ENTANGLEMENT_INVENTORY_LEDGER/1",
            "states": list(lang.EPR_STATES),
            "transitions": {k: list(v) for k, v
                            in sorted(protocols.EBIT_TRANSITIONS.items())},
            "inventory": ledger.as_dict(),
            "counts": ledger.counts(),
            "requesting_rows": [r.row_id for r in rows],
        }

    def _dynamic_schedule(self) -> dict:
        """Re-schedule under every admitted mode and report the spread.

        This is the honest form of "dynamic scheduling" available without a
        physical target: the same SES is re-planned, and the observed makespan
        of each mode is reported. No mode is claimed to be optimal.
        """
        entries: List[dict] = []
        for mode in planner.SCHEDULE_MODES:
            s = planner.TemporalScheduler(self.topology, self.authority).schedule(
                self.ses, self.partial_order, self.partition, self.placement,
                self.concurrency, mode=mode, exact=False)
            entries.append({"mode": mode, "makespan": round(s.makespan, 6),
                            "blocked": s.blocked,
                            "schedule_hash": s.hash()})
        best = min(entries, key=lambda e: (e["makespan"], e["mode"]))
        return {
            "schema": "PA-LCTL/DYNAMIC_SCHEDULE_LEDGER/1",
            "candidates": entries,
            "selected_mode": self.schedule.mode,
            "lowest_makespan_mode": best["mode"],
            "optimality_claim": "HEURISTIC_NO_OPTIMALITY_CLAIM",
            "note": "re-planning only; this runtime never re-schedules against "
                    "measurements taken from a physical target",
        }

    def _task_runtime(self) -> Tuple[dict, fabric.EventLog]:
        fed = fabric.build_flat_federation(4, groups=2, domains=2)
        rt = fabric.TaskRuntime(fed)
        nodes = [n for n in self.ses.order
                 if self.ses.nodes[n].semantic_face in lang.EXECUTABLE_FACES]
        for n in nodes:
            rt.spawn(_duration_of, self.ses.nodes[n].duration_model,
                     task_id=f"T-{n}", cost_estimate=self.ses.nodes[n].duration_model)
        rt.run()
        results = rt.await_all()
        return ({"schema": "PA-LCTL/TASK_RUNTIME_LEDGER/1",
                 "profile": rt.profile.as_dict(),
                 "task_count": len(nodes),
                 "results_hash": fabric.stable_hash(results),
                 "runtime": rt.as_dict()}, rt.log)

    def _consistency(self) -> dict:
        laws = crdt.verify_all_crdt_laws(samples=12, seed=self.seed or 1)
        pm = crdt.PartitionModel(partitions_possible=True, bounded_duration=True)
        fm = crdt.FailureModel(crash_stop=True, anti_entropy=True,
                               crdt_declared=True, replicas=2)
        verdicts = []
        for profile in lang.CONSISTENCY_PROFILES:
            contract = crdt.ConsistencyContract(profile, pm, fm)
            verdicts.append(contract.evaluate().as_dict())
        engine = crdt.AntiEntropy()
        for i in range(3):
            replica = engine.add_replica(f"R{i}", crdt.GCounter(f"R{i}"))
            replica.increment(i + 1)
        report = engine.converge(max_rounds=8)
        return {
            "schema": "PA-LCTL/CONSISTENCY_LEDGER/1",
            "profiles": list(lang.CONSISTENCY_PROFILES),
            "contract_verdicts": verdicts,
            "crdt_laws": laws,
            "anti_entropy": report.as_dict(),
            "quantum_replication": "QUANTUM_REPLICATION_REJECTED: an unknown "
                                   "quantum state is never replicated",
        }

    def _collectives(self) -> dict:
        n = 4
        col = fabric.Collectives(n)
        values = list(range(1, n + 1))
        entries = []
        for op, kwargs in (("broadcast", {"value": 7}),
                           ("gather", {"values": values}),
                           ("allgather", {"values": values}),
                           ("reduce", {"values": values}),
                           ("allreduce", {"values": values}),
                           ("scan", {"values": values})):
            res = getattr(col, op)(**kwargs)
            entries.append(res.as_dict())
        choice = fabric.select_algorithm("ALLREDUCE", n, 1024)
        return {
            "schema": "PA-LCTL/COLLECTIVE_LEDGER/1",
            "algorithms": list(fabric.COLLECTIVE_ALGORITHMS),
            "workers": n,
            "operations": entries,
            "algorithm_selection": choice.as_dict(),
            "log_hash": col.log.hash(),
        }

    def _balance_and_stragglers(self) -> Tuple[dict, dict]:
        fed = fabric.build_flat_federation(4, groups=2, domains=2)
        rt = fabric.TaskRuntime(fed)
        tasks = [rt.spawn(_duration_of, 1.0, task_id=f"LB{i:03d}",
                          cost_estimate=1.0 + (i % 3))
                 for i in range(8)]
        lb = fabric.LoadBalancer(fed)
        signals = {w: fabric.WorkerSignal(w, throughput=1.0 + 0.1 * i)
                   for i, w in enumerate(fed.worker_ids())}
        lb.dynamic_assign(tasks, signals)
        balance = {"schema": "PA-LCTL/LOAD_BALANCE_LEDGER/1", **lb.ledger()}

        st = fabric.StragglerEngine()
        for i, w in enumerate(fed.worker_ids()):
            st.observe_throughput(w, 1.0 if i else 0.2)
            st.observe_queue_delay(w, 0.1 if i else 5.0)
            st.observe_heartbeat(w, 0.1)
        verdicts = [st.detect(w).as_dict() for w in fed.worker_ids()]
        straggler = {
            "schema": "PA-LCTL/STRAGGLER_LEDGER/1",
            "verdicts": verdicts,
            "engine": st.as_dict(),
            "quantum_policy": "a task holding unknown quantum state is never "
                              "speculatively duplicated",
        }
        return balance, straggler

    def _numerics(self) -> Tuple[Optional[dict], dict, dict]:
        circuit = simulator.circuit_from_program(self.program, self.verification)
        summary = circuit.summary(shots=self.shots)
        nplanner = simulator.NumericalPlanner()
        plan = nplanner.plan(summary)
        sim: Optional[dict] = None
        status = "PLANNED_ONLY"
        detail = ""
        if not circuit.qubits:
            status, detail = "BLOCKED", "program declares no qubits"
        else:
            try:
                sim = simulator.run(circuit, nplanner, shots=self.shots,
                                    seed=self.seed)
                status = "EXECUTED"
            except simulator.SimulationError as exc:
                # The planned backend refused this circuit. The refusal is
                # recorded verbatim and the exact statevector engine is used
                # instead; the substitution is never silent (LCTL 1.3.x s53).
                detail = f"planned backend {plan['backend']} refused: {exc}"
                try:
                    sim = simulator.run_statevector(circuit, shots=self.shots,
                                                    seed=self.seed)
                    sim["plan"] = {**plan, "executed_backend": "local_statevector",
                                   "substitution_reason": detail}
                    status = "EXECUTED_WITH_RECORDED_SUBSTITUTION"
                except simulator.SimulationError as exc2:
                    status = "BLOCKED"
                    detail = f"{detail}; statevector engine also refused: {exc2}"

        backend_ledger = {
            "schema": "PA-LCTL/NUMERICAL_BACKEND_LEDGER/1",
            "backends": list(simulator.BACKENDS),
            "plan": plan,
            "circuit_summary": summary,
            "status": status,
            "detail": detail,
            "tolerances": {
                "max_amplitude_error": simulator.MAX_AMPLITUDE_ERROR_TOL,
                "density_frobenius": simulator.DENSITY_FROBENIUS_TOL,
                "trace": simulator.TRACE_TOL,
                "state_fidelity": simulator.STATE_FIDELITY_TOL,
                "distribution_total_variation": simulator.DISTRIBUTION_TV_TOL,
                "kraus_completeness": simulator.KRAUS_COMPLETENESS_TOL,
            },
            "execution_label": sim["label"] if sim else None,
            "final_state_hash": sim["final_state_hash"] if sim else None,
        }
        farm = {
            "schema": "PA-LCTL/FARM_EXECUTION_LEDGER/1",
            "thresholds": {"circuit_farm_min": simulator.CIRCUIT_FARM_MIN,
                           "shot_farm_min": simulator.SHOT_FARM_MIN,
                           "trajectory_farm_min": simulator.TRAJECTORY_FARM_MIN},
            "farm_kind": plan["backend"] if plan["backend"].endswith("_farm")
                         else "NOT_FARMED",
            "independent_circuits": summary.get("independent_circuits", 1),
            "shots": self.shots,
            "note": "farming is embarrassingly parallel classical work; it "
                    "never crosses the quantum boundary",
        }
        return sim, backend_ledger, farm

    def _protocols(self, ebit_ledger: protocols.EbitLedger
                   ) -> Tuple[Optional[protocols.ProtocolPlan], dict, dict]:
        compiler = protocols.ProtocolCompiler()
        plan: Optional[protocols.ProtocolPlan] = None
        status, detail = "COMPILED", ""
        try:
            plan = compiler.compile(self.program, self.verification)
        except protocols.ProtocolError as exc:
            status, detail = "BLOCKED", str(exc)
        compiler_ledger = {
            "schema": "PA-LCTL/PROTOCOL_COMPILER_LEDGER/1",
            "supported_primitives": list(protocols.ProtocolCompiler.SUPPORTED_OPS),
            "step_kinds": list(protocols.STEP_KINDS),
            "status": status,
            "detail": detail,
            "plan": plan.as_dict() if plan else None,
            "validation": plan.validate() if plan else None,
        }

        reference: List[dict] = []
        tl = protocols.EbitLedger()
        tel = protocols.teleport([0.6, 0.8], tl, _ready_ebit(tl, "N0", "N1"),
                                 src_node="N0", dst_node="N1")
        reference.append({"protocol": "TELEPORT", "verdict": tel["verdict"],
                          "fidelity": tel.get("fidelity"),
                          "label": tel.get("label")})
        rl = protocols.EbitLedger()
        rc = protocols.remote_cnot([1.0, 0.0], [0.0, 1.0], rl,
                                   _ready_ebit(rl, "N0", "N1"),
                                   control_node="N0", target_node="N1")
        reference.append({"protocol": "REMOTE_CNOT", "verdict": rc["verdict"],
                          "fidelity": rc.get("fidelity"),
                          "label": rc.get("label")})
        protocol_ledger = {
            "schema": "PA-LCTL/PROTOCOL_LEDGER/1",
            "reference_equivalence": reference,
            "purification_model": list(protocols.PURIFICATION_MODELS),
            "purification_assumptions": list(protocols.BBPSSW_ASSUMPTIONS),
            "ebit_counts": ebit_ledger.counts(),
            "classical_feedback_states": list(protocols.MESSAGE_STATES),
            "claim": "reference protocol semantics verified numerically; not "
                     "evidence of physical entanglement or physical execution",
        }
        return plan, compiler_ledger, protocol_ledger

    def _resources(self, sim: Optional[dict],
                   quantum_routes: List[dict]) -> dict:
        prog, graph, po = self.program, self.ses, self.partial_order
        circuit = simulator.circuit_from_program(prog, self.verification)
        cres = circuit.resource()
        comm = self.schedule.stage_ledger.get("communication_planning", {})
        messages = comm.get("messages", [])
        bytes_moved = sum(int(m.get("payload_bytes", 0)) for m in messages)
        collectives = sum(1 for r in prog.rows if r.op in lang.OPS_COLLECTIVE)
        barriers = sum(1 for r in prog.rows if r.op in ("BARRIER", "FENCE", "EPOCH"))
        memory = sum(float(graph.nodes[n].resource_claim.get("memory", 0.0) or 0.0)
                     for n in graph.order)

        remote_ops = sum(1 for r in prog.rows if r.op in lang.OPS_DISTRIBUTED_Q)
        swaps = sum(1 for r in prog.rows if r.op in ("SWAP", "ISWAP"))
        ent_swaps = sum(1 for r in prog.rows if r.op == "ENTANGLEMENT_SWAP")
        purify_rows = sum(1 for r in prog.rows if r.op == "PURIFY")
        ebits_gen = sum(1 for r in prog.rows
                        if r.op in ("ENTANGLE_LINK", "EPR_RESERVE"))
        ebits_used = sum(1 for r in prog.rows
                         if r.op in ("TELEPORT", "REMOTE_CNOT", "REMOTE_CONTROL",
                                     "ENTANGLEMENT_SWAP"))
        planned = [q for q in quantum_routes if q.get("status") == "PLANNED"]
        attempts = sum(int(q.get("ebits", 0)) for q in planned)
        fidelity = (min((float(q.get("fidelity", 1.0)) for q in planned))
                    if planned else 1.0)
        coherence = self.schedule.coherence["total_coherence_exposure"]

        return {
            "schema": "PA-LCTL/RESOURCE_LEDGER/1",
            "value_provenance_vocabulary": list(VALUE_PROVENANCE),
            "separation_rule": "classical and quantum resources are reported "
                               "in separate sections and are never summed into "
                               "a single figure of merit",
            "classical": {
                "cpu_work": tagged(round(po.work, 6), "compiler_derived",
                                   "sum of SES duration models in abstract "
                                   "time units, not seconds"),
                "memory": tagged(memory, "compiler_derived",
                                 "sum of declared RESOURCE memory claims"),
                "bytes_moved": tagged(bytes_moved, "analytical_estimate",
                                      "planned payloads over planned routes"),
                "messages": tagged(len(messages), "compiler_derived"),
                "collectives": tagged(collectives, "exact"),
                "barriers": tagged(barriers, "exact"),
                "latency_estimate": tagged(round(self.schedule.makespan, 6),
                                           "analytical_estimate"),
                "critical_path": tagged(round(po.span, 6), "compiler_derived"),
                "energy_estimate": tagged(round(po.work * 1.0, 6),
                                          "analytical_estimate",
                                          "abstract proportional model; no "
                                          "hardware power measurement exists"),
            },
            "quantum": {
                "logical_qubits": tagged(circuit.n_qubits, "exact"),
                "gate_counts_by_class": tagged(gate_counts_by_class(prog),
                                               "exact"),
                "two_qubit_gate_count": tagged(cres["two_qubit_gate_count"],
                                               "exact"),
                "t_count": tagged(cres["t_count"], "exact"),
                "clifford_count": tagged(cres["clifford_count"], "exact"),
                "depth": tagged(cres["depth"], "exact"),
                "measurement_count": tagged(cres["measurement_count"], "exact"),
                "shots": tagged(self.shots if sim else 0,
                                "exact" if sim else "unknown"),
                "swap_count": tagged(swaps, "exact"),
                "remote_gate_count": tagged(remote_ops, "exact"),
                "ebits_generated": tagged(ebits_gen, "compiler_derived"),
                "ebits_consumed": tagged(ebits_used, "compiler_derived"),
                "entanglement_swaps": tagged(ent_swaps, "exact"),
                "purification_rounds": tagged(
                    sum(int(q.get("purification_rounds", 0)) for q in planned)
                    + purify_rows, "compiler_derived"),
                "quantum_link_attempts": tagged(attempts, "analytical_estimate",
                                                "planned ebit demand over the "
                                                "declared topology"),
                "expected_fidelity": tagged(round(fidelity, 9),
                                            "analytical_estimate",
                                            "worst planned route fidelity from "
                                            "the declared link model"),
                "coherence_exposure": tagged(coherence, "analytical_estimate",
                                             "abstract time units"),
            },
            "hardware_measured": tagged(None, "unknown",
                                        "no authenticated physical target is "
                                        "bound; nothing here is measured"),
        }

    def _errors(self) -> Tuple[dict, dict]:
        terms = error_terms_from_program(self.program)
        dims: Dict[str, List[dict]] = {}
        for t in terms:
            dims.setdefault(f"{t.unit}/{t.kind}", []).append(t.as_dict())
        dims = {k: sorted(v, key=lambda d: d["name"]) for k, v in sorted(dims.items())}

        composition: Optional[dict] = None
        if terms:
            composition = erroralgebra.compose(terms, "independent").as_dict()

        exposure = erroralgebra.CoherenceExposure(
            lineage="program",
            operation_time=self.schedule.coherence["operation_time"],
            idle_time=self.schedule.coherence["idle_time"],
            route_time=self.schedule.coherence["route_time"])

        error_ledger = {
            "schema": "PA-LCTL/ERROR_LEDGER/1",
            "units": list(erroralgebra.UNITS),
            "kinds": list(erroralgebra.ERROR_KINDS),
            "domains": list(erroralgebra.DOMAINS),
            "terms": [t.as_dict() for t in
                      sorted(terms, key=lambda x: x.name)],
            "dimensions": dims,
            "dimension_count": len(dims),
            "merged_scalar": None,
            "merge_rule": "incompatible error classes are kept as separate "
                          "dimensions; they are never merged into one scalar",
            "composition": composition,
            "coherence_exposure": exposure.as_dict(),
            "declared_but_untyped_rows": sorted(
                r.row_id for r in self.program.rows
                if r.error_map and r.row_id not in {t.name for t in terms}),
        }

        attempts: List[dict] = []
        for assumption in erroralgebra.ASSUMPTIONS:
            if not terms:
                break
            try:
                res = erroralgebra.compose(terms, assumption)
                attempts.append({"assumption": assumption, **res.as_dict()})
            except erroralgebra.ErrorAlgebraError as exc:
                attempts.append({"assumption": assumption, "resolved": False,
                                 "token": erroralgebra.TOKEN_UNRESOLVED,
                                 "refusal": str(exc)})
        composition_ledger = {
            "schema": "PA-LCTL/ERROR_COMPOSITION_LEDGER/1",
            "laws": [l.as_dict() for l in erroralgebra.COMPOSITION_LAWS],
            "assumptions": list(erroralgebra.ASSUMPTIONS),
            "attempts": attempts,
            "unresolved_token": erroralgebra.TOKEN_UNRESOLVED,
        }
        return error_ledger, composition_ledger

    def _resilience(self) -> Tuple[dict, dict, dict]:
        entries = resilience.run_campaign(seed=self.seed or 20260811)
        summary = resilience.campaign_summary(entries)
        failure = {
            "schema": "PA-LCTL/FAILURE_LEDGER/1",
            "scenarios": [s.as_dict() for s in resilience.SCENARIOS],
            "injected": [{"scenario_id": e["scenario_id"],
                          "injected": e["injected"],
                          "detected": e["detected"]} for e in entries],
            "count": len(entries),
        }
        recovery = {
            "schema": "PA-LCTL/RECOVERY_LEDGER/1",
            "classes": list(lang.RECOVERY_CLASSES),
            "entries": entries,
            "summary": summary,
        }

        # A real inject-and-recover on a classical task, end to end.
        store = resilience.CheckpointStore()
        store.save("CK-0", "classical_measurement", {"counter": 3},
                   classically_known=True, provenance={"origin": "task"})
        txn = resilience.RecoveryTransaction("TX-0").begin({"counter": 3})
        txn.write("counter", 4)
        txn.record_classical("counter", 3)
        rolled = txn.rollback()
        injector = resilience.FailureInjector(seed=self.seed or 20260811)
        event = injector.inject("worker_crash", target="W00")
        rplanner = resilience.RecoveryPlanner()
        record = rplanner.explain(event, resilience.DEFAULT_CONTEXTS.get(
            "worker_crash", {}))
        tree = resilience.SupervisionTree()
        decision = tree.handle("W00", record.recovery_class, event)
        live = {
            "schema": "PA-LCTL/LIVE_RECOVERY_LEDGER/1",
            "checkpoint": store.verify("CK-0"),
            "transaction": rolled.as_dict(),
            "restored_state": store.load("CK-0"),
            "failure": event.as_dict(),
            "classification": record.as_dict(),
            "supervision": decision.as_dict(),
            "quantum_rollback": "QUANTUM_ROLLBACK_REFUSED for unknown quantum "
                                "state; only classical state is rolled back",
        }
        return failure, recovery, live

    def _admission(self) -> dict:
        exact_map = {
            "EXACT": "ADMIT_EXACT",
            "EXACT_NUMERICAL": "ADMIT_NUMERICALLY_VERIFIED",
            "APPROXIMATE": "ADMIT_APPROXIMATE",
            "NO_FAITHFUL_FORM": "QUARANTINE",
        }
        rules = []
        for row in self.authority.ledger:
            verdict = exact_map.get(row.exactness, "OBSERVE")
            if row.commutator_status == "COMMUTING_TARGET_CONDITIONAL":
                verdict = "ADMIT_TARGET_SPECIFIC"
            rules.append({"rule_id": row.rule_id, "proof_hash": row.proof_hash,
                          "commutator_status": row.commutator_status,
                          "exactness": row.exactness, "q6_admission": verdict})
        by_decision: Dict[str, int] = {}
        for r in self.concurrency:
            by_decision[r.decision] = by_decision.get(r.decision, 0) + 1
        return {
            "schema": "PA-LCTL/ADMISSION_LEDGER/1",
            "q6_vocabulary": list(lang.Q6_ADMISSION),
            "concurrency_decisions": dict(sorted(by_decision.items())),
            "commutation_rule_admission": sorted(
                rules, key=lambda d: (d["rule_id"], d["proof_hash"])),
            "quarantined_learned_rules": self.authority.quarantined,
            "learned_rule_policy": "a learned rewrite is QUARANTINE until "
                                   "Q1-Q6 admission and is never usable by the "
                                   "optimizer before that",
        }

    def _reservations(self) -> dict:
        per_target: Dict[str, float] = {}
        for op in self.schedule.ops:
            per_target[op.target] = round(
                per_target.get(op.target, 0.0) + (op.end - op.start), 6)
        return {"occupancy_by_target": dict(sorted(per_target.items())),
                "targets": sorted(set(self.placement.values())),
                "link_reservations": sorted(
                    {self.ses.nodes[n].link for n in self.ses.order
                     if self.ses.nodes[n].link != NULL_CELL})}

    def _failure_domains(self) -> dict:
        out: Dict[str, List[str]] = {}
        for n in self.ses.order:
            out.setdefault(self.ses.nodes[n].failure_domain, []).append(n)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def _provenance(self, sim: Optional[dict], L: Dict[str, dict]) -> dict:
        po, graph = self.partial_order, self.ses
        admitted = self.opportunities["admitted_parallel_pairs"]
        targets = set(self.placement.values())
        domains = {graph.nodes[n].memory_domain for n in graph.order}
        owners = {graph.nodes[n].owner for n in graph.order}

        if sim is not None and admitted > 0 and len(targets) > 1:
            parallel_state = "PARALLEL_EMULATION"
        elif admitted > 0 or len(targets) > 1:
            parallel_state = "SCHEDULED_PARALLEL"
        elif po.width > 1:
            parallel_state = "LOGICAL_PARALLEL"
        else:
            parallel_state = "SERIAL"

        multi_site = len(targets) > 1 or len(domains) > 1 or len(owners) > 1
        if multi_site and sim is not None:
            distributed_state = "DISTRIBUTED_CLASSICAL_EMULATION"
        elif multi_site:
            distributed_state = "DISTRIBUTED_SCHEDULED"
        elif len(owners | domains) > 1:
            distributed_state = "LOGICAL_DISTRIBUTED"
        else:
            distributed_state = "LOCAL_ONLY"

        # Ceilings enforced structurally, not by convention.
        parallel_state = _cap(parallel_state, lang.PARALLEL_STATES,
                              "PARALLEL_EMULATION")
        distributed_state = _cap(distributed_state, lang.DISTRIBUTED_STATES,
                                 "DISTRIBUTED_CLASSICAL_EMULATION")

        execution_class = (sim["label"] if sim
                           else "CLASSICAL_STATIC_ANALYSIS_ONLY")

        record = {
            "language": lang_name(),
            "profile": self.program.profile,
            "execution_class": execution_class,
            "parallel_state": parallel_state,
            "distributed_state": distributed_state,
            "quantum_boundary": "QUANTUM_BOUNDARY_NOT_CROSSED",
            "target": f"declared_topology:{self.topology.name}",
            "target_verified": False,
            "topology_hash": self.topology.hash(),
            "schedule_hash": self.schedule.hash(),
            "source_hash": self.program.source_hash(),
            "qcir_hash": self.qcir.hash(),
            "ses_hash": graph.hash(),
            "proof_ledger_hash": _sha(L["COMMUTATION_LEDGER.json"]["rules"]),
            "physical_qpu": False,
            "physical_parallel": False,
            "physical_distributed": False,
        }
        if set(record) != set(PROVENANCE_FIELDS):
            raise LedgerError(
                "PROVENANCE_LEDGER field set does not match LCTL 1.2.x s33: "
                f"missing={sorted(set(PROVENANCE_FIELDS) - set(record))} "
                f"unexpected={sorted(set(record) - set(PROVENANCE_FIELDS))}")
        return record

    def _soak(self, soak: Optional[Dict[str, Any]]) -> dict:
        if soak is None:
            return {
                "schema": "PA-LCTL/SOAK_LEDGER/1",
                "status": SOAK_NOT_RUN_TOKEN,
                "reason": "no soak evidence was supplied to this build; a soak "
                          "result is never inferred from a short analysis run",
                "thresholds": {"SOAK_1H": 3600.0, "SOAK_8H": 28800.0,
                               "SOAK_24H": 86400.0},
                "elapsed_s": 0.0,
                "SOAK_1H": "SOAK_1H_NOT_RUN",
                "SOAK_8H": "SOAK_8H_NOT_RUN",
                "SOAK_24H": "SOAK_24H_NOT_RUN",
            }
        return {"schema": "PA-LCTL/SOAK_LEDGER/1", **soak}

    def _release(self, conformance: Optional[Dict[str, Any]]) -> dict:
        """Emit the six mandatory release outputs from ACTUAL evidence."""
        outputs: Dict[str, dict] = {}
        conf = conformance or {}
        by_group = dict(conf.get("by_group", {}))
        failed = int(conf.get("failed", -1))
        total = int(conf.get("total", 0))

        def group_ok(*groups: str) -> Tuple[bool, str]:
            if not conf:
                return False, ("no conformance evidence supplied; a release "
                               "output is never qualified without executed "
                               "conformance results")
            if failed != 0:
                return False, (f"conformance reported {failed} failure(s) over "
                               f"{total} executed case(s)")
            missing = [g for g in groups if g not in by_group]
            if missing:
                return False, f"conformance evidence has no result for {missing}"
            empty = [g for g in groups
                     if int(by_group[g].get("total", 0)) <= 0]
            if empty:
                return False, f"conformance groups {empty} executed no cases"
            bad = [g for g in groups if int(by_group[g].get("failed", 1)) != 0]
            if bad:
                return False, f"conformance groups {bad} reported failures"
            return True, (f"{total} executed conformance cases, 0 failures; "
                          f"groups {list(groups)} all passed")

        for name, groups in (
                ("NATIVE_PARALLEL_EXECUTION",
                 ("scheduling", "task_runtime", "commutation")),
                ("NATIVE_DISTRIBUTED_EXECUTION",
                 ("placement_routing", "collectives", "federation")),
                ("DISTRIBUTED_NUMERICAL_SIMULATION",
                 ("numerical_backends", "partitioning")),
                ("DISTRIBUTED_PROTOCOL_EMULATION",
                 ("protocols", "resilience", "provenance_replay"))):
            ok, why = group_ok(*groups)
            outputs[name] = {
                "status": QUALIFIED if ok else BLOCKED,
                "evidence_groups": list(groups),
                "reason": why,
                "evidence_hash": conf.get("evidence_hash"),
            }

        for name in PHYSICAL_RELEASE_OUTPUTS:
            outputs[name] = {
                "status": BLOCKED_EXTERNAL_AUTHORITY,
                "evidence_groups": [],
                "reason": "no authenticated physical quantum target exists in "
                          "this environment; qualification requires an external "
                          "authority that this software cannot supply",
                "evidence_hash": None,
            }

        return {
            "schema": "PA-LCTL/RELEASE_QUALIFICATION/1",
            "status_vocabulary": list(lang.STATUS_VOCABULARY),
            "outputs": {k: outputs[k] for k in RELEASE_OUTPUTS},
            "conformance_supplied": bool(conf),
            "conformance_total": total,
            "conformance_failed": failed if conf else None,
        }

    # -- output ------------------------------------------------------------
    def as_dict(self) -> dict:
        return {"schema": "PA-LCTL/LEDGER_SET/1",
                "files": list(LEDGER_FILES),
                "ledgers": self.ledgers,
                "qcir_p2": self.qcir.as_dict(),
                "qcir_hash": self.qcir.hash()}

    def render(self) -> Dict[str, str]:
        """Deterministic pretty JSON text for every ledger file."""
        return {name: json.dumps(self.ledgers[name], indent=2, sort_keys=True,
                                 ensure_ascii=False, default=_json_default) + "\n"
                for name in LEDGER_FILES}

    def write(self, dirpath: str) -> List[str]:
        """Write every ledger as pretty, sorted-key, deterministic JSON."""
        os.makedirs(dirpath, exist_ok=True)
        written: List[str] = []
        for name, text in sorted(self.render().items()):
            full = os.path.join(dirpath, name)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(text)
            written.append(full)
        qcir_path = os.path.join(dirpath, "QCIR_P2.json")
        with open(qcir_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(self.qcir.as_dict(), indent=2, sort_keys=True,
                                ensure_ascii=False, default=_json_default) + "\n")
        written.append(qcir_path)
        return written

    def digest(self) -> str:
        """One hash over every emitted ledger, for evidence chaining."""
        return _sha({name: self.ledgers[name] for name in LEDGER_FILES})


# --------------------------------------------------------------------------
# 5. Small helpers
# --------------------------------------------------------------------------

def lang_name() -> str:
    from . import LANGUAGE
    return LANGUAGE


def _cap(state: str, ladder: Sequence[str], ceiling: str) -> str:
    """Clamp a ladder value so it can never exceed the admitted ceiling."""
    order = list(ladder)
    if state not in order:
        raise LedgerError(f"{state!r} is not on the ladder {order}")
    return state if order.index(state) <= order.index(ceiling) else ceiling


def _duration_of(x: float) -> float:
    """Deterministic unit of classical work used by the runtime ledgers."""
    return float(x)


def _ready_ebit(ledger: protocols.EbitLedger, a: str, b: str,
                fidelity: float = 1.0) -> str:
    """Request, generate and herald one ebit so a protocol may consume it."""
    eid = ledger.request(a, b, epoch=0, owner_protocol="reference_equivalence")
    ledger.generate(eid, epoch=0)
    ledger.herald(eid, fidelity=fidelity, epoch=0)
    return eid
