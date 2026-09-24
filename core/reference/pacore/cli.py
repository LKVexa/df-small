"""
PA-LCTL runtime command surface.

    python3 -m pacore.cli <command> [args]

Implements the command set required by LCTL 1.3.x s62, 1.4.x PART III and
1.5/1.6. Every command is one of:

  * OPERATIONAL - it does the work locally and prints machine-readable JSON;
  * REJECTED    - the program was analyzed and refused, with diagnostics;
  * BLOCKED     - the capability does not exist in this environment.

There is no fourth outcome. A command never prints a success record for work
it did not do, and a blocked or rejected command always exits non-zero
(LCTL 1.3.x s53 honesty rules, 1.5.x s79 release gating).

`adapter-check` and `qualify-distributed-target` are permanently
BLOCKED_EXTERNAL_AUTHORITY: no authenticated physical quantum target exists
here, and no software in this package can create one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import (BACKEND, CORE_VERSION, LANGUAGE, NETWORK, PROFILE,
               commutation as commutation_mod, conformance, crdt, erroralgebra,
               fabric, lang, ledgers, planner, protocols, redesignate,
               resilience, ses as ses_mod, simulator)
from . import adapters
from .adapters import bottlerocket

# --------------------------------------------------------------------------
# 0. Exit codes and outcome tokens
# --------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REJECTED = 2
EXIT_BLOCKED = 3
EXIT_FAILED = 4

BLOCKED_EXTERNAL_AUTHORITY = "BLOCKED_EXTERNAL_AUTHORITY"
BLOCKED_CAPABILITY_ABSENT = "BLOCKED_CAPABILITY_ABSENT"
PROGRAM_REJECTED = "PROGRAM_REJECTED"


class CommandBlocked(Exception):
    """The requested capability does not exist in this environment."""

    def __init__(self, token: str, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.token = token
        self.detail = detail or {}


class CommandRejected(Exception):
    """The program was analyzed and refused."""

    def __init__(self, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.detail = detail or {}


#: A minimal, fully valid reference program used by `--example`.
EXAMPLE_PROGRAM = conformance.program(
    conformance.declarations() + conformance._independent_lanes(2, 2))

#: A reference program carrying distributed protocol rows, used by `--example`
#: for the protocol commands (the plain example declares none).
EXAMPLE_PROTOCOL_PROGRAM = conformance.program(
    conformance.declarations(
        topology_resource="nodes=4;domains=2;qcapacity=4")
    + conformance._bell_rows(2)
    + [conformance.prep("R200", "r[0]", node="N1", qspace="r[0:1]"),
       conformance.row("R300", "PROTOCOL", "EPR_RESERVE", out="e[0]",
                       type="remote_handle", regime="EXACT", node="N0",
                       domain="D0", link="Q0", conf="1.0"),
       conformance.row("R301", "PROTOCOL", "TELEPORT", out="z[0]", a="q[0]",
                       type="remote_handle", regime="EXACT", node="N0",
                       domain="D0", link="Q0", conf="1.0"),
       conformance.row("R302", "PROTOCOL", "REMOTE_CNOT", ctrl="q[1]",
                       a="r[0]", type="controlled_operator", regime="EXACT",
                       node="N0", domain="D0", link="Q0", conf="1.0")])

#: Commands whose `--example` needs distributed protocol rows.
PROTOCOL_EXAMPLE_COMMANDS = frozenset({"protocol-compile", "protocol-execute",
                                       "qroute", "qroute2"})


# --------------------------------------------------------------------------
# 1. Argument parsing
# --------------------------------------------------------------------------

COMMANDS: Tuple[str, ...] = (
    "verify", "ses", "commute-plan", "partition", "hyperpartition",
    "pareto-partition", "place", "place2", "route", "route2", "qroute",
    "qroute2", "schedule", "schedule2", "qcir-p2", "parallel-opportunities",
    "partial-order", "execute-local", "simulate-distributed",
    "simulate-density-distributed", "simulate-stabilizer", "simulate-tensor",
    "simulate-ooc", "protocol-compile", "protocol-run", "protocol-execute",
    "run-task", "run-dataflow", "run-bsp", "run-async", "run-collective",
    "run-farm", "load-balance", "failure-inject", "inject-and-recover",
    "recover", "replay", "reconstruct", "consistency-check", "property-test",
    "metamorphic-test", "differential-test", "conformance", "benchmark",
    "soak", "ledgers", "adapter-check", "qualify-distributed-target",
    "redesignate", "selfcheck", "backend-run",
)

#: Commands that require a PA-LCTL program.
PROGRAM_COMMANDS = frozenset({
    "verify", "ses", "commute-plan", "partition", "hyperpartition",
    "pareto-partition", "place", "place2", "route", "route2", "qroute",
    "qroute2", "schedule", "schedule2", "qcir-p2", "parallel-opportunities",
    "partial-order", "execute-local", "simulate-distributed",
    "simulate-density-distributed", "simulate-stabilizer", "simulate-tensor",
    "simulate-ooc", "protocol-compile", "protocol-run", "protocol-execute",
    "ledgers", "backend-run",
})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m pacore.cli",
        description="PA-LCTL reference runtime command surface")
    p.add_argument("command", choices=COMMANDS, help="command to run")
    p.add_argument("program", nargs="?", default=None,
                   help="path to a PA-LCTL source file (`-` reads stdin)")
    p.add_argument("--example", action="store_true",
                   help="use the bundled reference program instead of a file")
    p.add_argument("--out", metavar="DIR", default=None,
                   help="write ledgers / results into DIR")
    p.add_argument("-k", "--partitions", type=int, default=2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--shots", type=int, default=256)
    p.add_argument("--mode", default="CRITICAL_PATH")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--count", type=int, default=64)
    p.add_argument("--hours", type=float, default=0.0)
    p.add_argument("--scenario", default="worker_crash")
    p.add_argument("--op", default="ALLREDUCE")
    p.add_argument("--algorithm", default="linear")
    p.add_argument("--profile", default="EVENTUAL")
    p.add_argument("--target", default="-",
                   help="physical target identifier for adapter checks")
    p.add_argument("--text", default=None,
                   help="literal text for `redesignate`")
    p.add_argument("--subset", default=None,
                   help="comma-separated conformance subset")
    p.add_argument("--audit", metavar="DIR", default=None,
                   help="walk a delivered tree and fail on any unrewritten "
                        "designation token outside the provenance allowlist")
    p.add_argument("--indent", type=int, default=2)
    return p


def load_program(args: argparse.Namespace) -> Tuple[str, str]:
    if args.example:
        if args.command in PROTOCOL_EXAMPLE_COMMANDS:
            return EXAMPLE_PROTOCOL_PROGRAM, "<example-protocol>"
        return EXAMPLE_PROGRAM, "<example>"
    if args.program is None:
        raise CommandRejected(
            f"command {args.command!r} requires a PA-LCTL program; pass a file "
            f"path or --example. Nothing was analyzed and nothing is claimed.")
    if args.program == "-":
        return sys.stdin.read(), "<stdin>"
    if not os.path.isfile(args.program):
        raise CommandRejected(f"program file {args.program!r} does not exist")
    with open(args.program, "r", encoding="utf-8") as fh:
        return fh.read(), args.program


# --------------------------------------------------------------------------
# 2. Shared analysis front end
# --------------------------------------------------------------------------

class Analysis:
    """Parse + verify + SES + partial order, failing closed at each step."""

    def __init__(self, source: str, path: str,
                 topology: Optional[planner.Topology] = None) -> None:
        self.source, self.path = source, path
        prog, diags = lang.parse(source, path)
        fatal = [d.as_dict() for d in diags
                 if d.severity in ("ERROR", "REJECT")]
        if prog is None or fatal:
            raise CommandRejected("program does not parse",
                                  {"diagnostics": fatal})
        self.program = prog
        self.verification = lang.verify(prog)
        if not self.verification.ok:
            raise CommandRejected(
                "program does not verify",
                {"diagnostics": [d.as_dict()
                                 for d in self.verification.rejections]})
        self.topology = topology or conformance.topology_from_program(prog)
        self.ses = ses_mod.build_ses(prog, self.verification)
        self.po = ses_mod.analyze(self.ses)
        self.authority = commutation_mod.CommutationAuthority()

    def concurrency(self) -> List[ses_mod.ConcurrencyRecord]:
        return ses_mod.admit_concurrency(self.ses, self.po, self.authority,
                                         self.topology)

    def partition(self, k: int, exact: Optional[bool] = None
                  ) -> planner.PartitionResult:
        return planner.HypergraphPartitioner().partition(self.ses, k, exact=exact)

    def placement(self, k: int) -> Tuple[planner.PartitionResult,
                                         Dict[int, str],
                                         List[planner.PlacementProof]]:
        part = self.partition(k, exact=False)
        placement, proofs = planner.Placer(self.topology).place(self.ses, part)
        if not placement:
            raise CommandRejected(
                "no partition could be placed on the bound topology",
                {"proofs": [p.as_dict() for p in proofs]})
        return part, placement, proofs

    def schedule(self, k: int, mode: str) -> Tuple[planner.PartitionResult,
                                                   Dict[int, str],
                                                   planner.Schedule]:
        if mode not in planner.SCHEDULE_MODES:
            raise CommandRejected(
                f"schedule mode {mode!r} is not admitted",
                {"admitted_modes": list(planner.SCHEDULE_MODES)})
        part, placement, _ = self.placement(k)
        sched = planner.TemporalScheduler(self.topology, self.authority).schedule(
            self.ses, self.po, part, placement, self.concurrency(), mode=mode)
        return part, placement, sched

    def circuit(self) -> simulator.Circuit:
        circ = simulator.circuit_from_program(self.program, self.verification)
        if not circ.qubits:
            raise CommandRejected(
                "program declares no qubits; there is nothing to simulate")
        return circ


# --------------------------------------------------------------------------
# 3. Command implementations
# --------------------------------------------------------------------------

def cmd_verify(a: argparse.Namespace) -> dict:
    source, path = load_program(a)
    prog, diags = lang.parse(source, path)
    fatal = [d.as_dict() for d in diags if d.severity in ("ERROR", "REJECT")]
    if prog is None or fatal:
        raise CommandRejected("program does not parse", {"diagnostics": fatal})
    vr = lang.verify(prog)
    admission = conformance.admit(source, path)
    if not vr.ok or not admission.accepted:
        raise CommandRejected(
            "program is not admitted",
            {"verifier": [d.as_dict() for d in vr.diagnostics],
             "admission": admission.as_dict()})
    return {"verdict": "ADMITTED", "path": path, "seal": prog.seal(),
            "source_hash": prog.source_hash(), "rows": len(prog.rows),
            "policy": {"NETWORK": prog.network_policy,
                       "BACKEND": prog.backend_policy},
            "warnings": [d.as_dict() for d in vr.diagnostics
                         if d.severity == "WARN"],
            "admission": admission.as_dict()}


def cmd_ses(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    return {"node_fields": list(ses_mod.SES_NODE_FIELDS),
            "edge_reasons": list(lang.EDGE_REASONS),
            "graph": json.loads(an.ses.canonical()),
            "ses_hash": an.ses.hash()}


def cmd_commute_plan(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    records = an.concurrency()
    rewrites = commutation_mod.plan_rewrites(an.ses, an.authority)
    return {"concurrency_records": [r.as_dict() for r in records],
            "rewrites": rewrites,
            "proof_ledger": an.authority.ledger_dict(),
            "policy": "only ADMIT_EXACT rewrites are applicable; OBSERVE "
                      "candidates are reported and never applied"}


def cmd_partition(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    part = an.partition(a.partitions)
    return {"cost_dimensions": list(planner.PARTITION_COST_DIMENSIONS),
            **part.as_dict()}


def cmd_hyperpartition(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    hp = planner.HypergraphPartitioner()
    hedges = hp.build_hyperedges(an.ses)
    part = hp.partition(an.ses, a.partitions)
    return {"hyperedges": [{"edge_id": h.edge_id, "pins": list(h.pins),
                            "weight": h.weight, "kind": h.kind}
                           for h in hedges],
            "hyperedge_count": len(hedges),
            "partition": part.as_dict()}


def cmd_pareto_partition(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    ks = sorted({2, max(1, a.partitions)})
    return planner.pareto_partitions(an.ses, ks)


def cmd_place(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    part, placement, proofs = an.placement(a.partitions)
    return {"levels": list(planner.PLACEMENT_LEVELS),
            "placement": {str(k): v for k, v in sorted(placement.items())},
            "proofs": [p.as_dict() for p in proofs],
            "topology_hash": an.topology.hash(),
            "partition_k": part.k}


def cmd_place2(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    part, placement, proofs = an.placement(a.partitions)
    placer = planner.Placer(an.topology)
    return {"hierarchy": placer.hierarchy(placement),
            "partition_tree": part.tree,
            "placement": {str(k): v for k, v in sorted(placement.items())},
            "proofs": [p.as_dict() for p in proofs]}


def cmd_route(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    _part, placement, _ = an.placement(a.partitions)
    router = planner.ClassicalRouter(an.topology)
    targets = sorted(set(placement.values()))
    routes: List[dict] = []
    missing: List[str] = []
    for i, x in enumerate(targets):
        for y in targets[i + 1:]:
            r = router.select(x, y, payload=1024)
            if r is None:
                missing.append(f"{x}->{y}")
            else:
                routes.append(r.as_dict())
    if missing:
        raise CommandRejected("no classical route exists for required pairs",
                              {"missing": missing})
    return {"model": "Tmessage = alpha + beta * n",
            "routes": routes, "targets": targets,
            "claim": "a route is a plan over the declared topology, not "
                     "evidence of a physical network"}


def cmd_route2(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    _part, placement, _ = an.placement(a.partitions)
    router = planner.ClassicalRouter(an.topology)
    targets = sorted(set(placement.values()))
    root = targets[0]
    k_shortest = {f"{root}->{t}": [r.as_dict() for r in
                                   router.k_shortest(root, t, 3, 1024)]
                  for t in targets[1:]}
    return {"k_shortest": k_shortest,
            "multicast_tree": router.multicast_tree(root, targets[1:], 1024),
            "hysteresis": router.hysteresis,
            "reservations": dict(sorted(router.reservations.items()))}


def cmd_qroute(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    _part, placement, _ = an.placement(a.partitions)
    router = planner.QuantumRouter(an.topology)
    targets = sorted(set(placement.values()))
    routes, missing = [], []
    for i, x in enumerate(targets):
        for y in targets[i + 1:]:
            r = router.route(x, y)
            if r is None:
                missing.append(f"{x}->{y}")
            else:
                routes.append(r.as_dict())
    if not routes and missing:
        raise CommandRejected(
            "no quantum route exists between any placed target pair",
            {"missing": missing})
    return {"routes": routes, "unroutable": missing,
            "claim": "reference entanglement routing plan; not evidence of "
                     "physical entanglement distribution"}


def cmd_qroute2(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    _part, placement, _ = an.placement(a.partitions)
    router = planner.QuantumRouter(an.topology)
    targets = sorted(set(placement.values()))
    demands = [(targets[i], targets[i + 1], 1) for i in range(len(targets) - 1)]
    if not demands:
        raise CommandRejected(
            "a min-cost entanglement flow needs at least two placed targets")
    return {"demands": [list(d) for d in demands],
            **router.min_cost_flow(demands)}


def cmd_schedule(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    _part, _placement, sched = an.schedule(a.partitions, a.mode)
    if sched.blocked:
        raise CommandRejected("the schedule is blocked",
                              {"blocked": sched.blocked})
    return {"stages": list(planner.SCHEDULER_STAGES), **sched.as_dict()}


def cmd_schedule2(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    part, placement, _ = an.placement(a.partitions)
    records = an.concurrency()
    entries = []
    for mode in planner.SCHEDULE_MODES:
        s = planner.TemporalScheduler(an.topology, an.authority).schedule(
            an.ses, an.po, part, placement, records, mode=mode)
        entries.append({"mode": mode, "makespan": round(s.makespan, 6),
                        "blocked": s.blocked, "schedule_hash": s.hash()})
    best = min(entries, key=lambda e: (e["makespan"], e["mode"]))
    return {"candidates": entries, "lowest_makespan_mode": best["mode"],
            "optimality_claim": "HEURISTIC_NO_OPTIMALITY_CLAIM",
            "note": "re-planning only; no measurement from a physical target "
                    "is used"}


def cmd_qcir_p2(a: argparse.Namespace) -> dict:
    source, path = load_program(a)
    ls = ledgers.LedgerSet.build(source, path, k=a.partitions, seed=a.seed,
                                 shots=a.shots)
    round_trip = ledgers.QCIRP2.from_dict(ls.qcir.as_dict())
    if round_trip.hash() != ls.qcir.hash():
        raise CommandRejected("QCIR-P2 round trip is not hash stable")
    return {"qcir_p2": ls.qcir.as_dict(), "qcir_hash": ls.qcir.hash(),
            "sections": list(ledgers.QCIRP2_SECTIONS),
            "round_trip_hash_stable": True}


def cmd_parallel_opportunities(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    records = an.concurrency()
    return {**ses_mod.discover_opportunities(an.ses, an.po, records),
            "family_selection": planner.select_parallel_family(an.ses, an.po),
            "communication_avoidance_order":
                list(planner.COMMUNICATION_AVOIDANCE_ORDER)}


def cmd_partial_order(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    return an.po.as_dict()


def cmd_execute_local(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    circuit = an.circuit()
    try:
        result = simulator.run(circuit, shots=a.shots, seed=a.seed)
    except simulator.SimulationError as exc:
        raise CommandRejected(f"local execution refused: {exc}")
    return _sim_report(result, a)


def cmd_simulate_distributed(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    try:
        result = simulator.run_statevector(an.circuit(), shots=a.shots,
                                           seed=a.seed, distributed=True)
    except simulator.SimulationError as exc:
        raise CommandRejected(f"distributed statevector refused: {exc}")
    return _sim_report(result, a)


def cmd_simulate_density_distributed(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    try:
        result = simulator.run_density(an.circuit(), shots=a.shots,
                                       seed=a.seed, distributed=True)
    except simulator.SimulationError as exc:
        raise CommandRejected(f"distributed density simulation refused: {exc}")
    return _sim_report(result, a)


def cmd_simulate_stabilizer(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    try:
        result = simulator.run_stabilizer(an.circuit(), shots=a.shots,
                                          seed=a.seed)
    except simulator.SimulationError as exc:
        raise CommandRejected(f"stabilizer simulation refused: {exc}")
    return _sim_report(result, a)


def cmd_simulate_tensor(a: argparse.Namespace) -> dict:
    Analysis(*load_program(a))
    raise CommandBlocked(
        BLOCKED_CAPABILITY_ABSENT,
        "no tensor-network contraction engine exists in this package. "
        "`tensor_network` appears in simulator.BACKENDS as a planning target "
        "only; there is no engine behind it, so no result can be produced.",
        {"implemented_engines": ["StatevectorEngine", "DensityEngine",
                                 "StabilizerEngine"],
         "requested_engine": "tensor_network"})


def cmd_simulate_ooc(a: argparse.Namespace) -> dict:
    Analysis(*load_program(a))
    raise CommandBlocked(
        BLOCKED_CAPABILITY_ABSENT,
        "no out-of-core state engine exists in this package. Out-of-core "
        "execution requires a paging state store that is not implemented, so "
        "no result can be produced.",
        {"implemented_engines": ["StatevectorEngine", "DensityEngine",
                                 "StabilizerEngine"],
         "requested_engine": "out_of_core"})


def _sim_report(result: dict, a: argparse.Namespace) -> dict:
    label = result["label"]
    if not label.startswith("CLASSICAL_"):
        raise CommandRejected(f"execution label {label!r} is not honest")
    return {"backend": result["backend"], "label": label,
            "qubits": result["qubits"],
            "final_state_hash": result["final_state_hash"],
            "probabilities": result["probabilities"],
            "counts": result.get("counts"),
            "measurements": result.get("measurements"),
            "resource": result["resource"],
            "plan": result.get("plan"),
            "seed": a.seed, "shots": a.shots,
            "claim": "classical simulation of the declared program; this is "
                     "not physical quantum execution"}


def cmd_protocol_compile(a: argparse.Namespace) -> dict:
    an = Analysis(*load_program(a))
    try:
        plan = protocols.ProtocolCompiler().compile(an.program, an.verification)
    except protocols.ProtocolError as exc:
        raise CommandRejected(f"protocol compilation refused: {exc}")
    return {"supported_primitives":
                list(protocols.ProtocolCompiler.SUPPORTED_OPS),
            "step_kinds": list(protocols.STEP_KINDS),
            "plan": plan.as_dict(), "validation": plan.validate()}


def cmd_protocol_run(a: argparse.Namespace) -> dict:
    Analysis(*load_program(a))
    out: List[dict] = []
    tl = protocols.EbitLedger()
    eid = _ready_ebit(tl)
    out.append(protocols.teleport([0.6, 0.8], tl, eid, src_node="N0",
                                  dst_node="N1", seed=a.seed))
    rl = protocols.EbitLedger()
    out.append(protocols.remote_cnot([1.0, 0.0], [0.0, 1.0], rl,
                                     _ready_ebit(rl), control_node="N0",
                                     target_node="N1", seed=a.seed))
    sl = protocols.EbitLedger()
    ab, bc = _ready_ebit(sl, "N0", "N1"), _ready_ebit(sl, "N1", "N2")
    out.append(protocols.entanglement_swap(sl, ab, bc, node_a="N0",
                                           node_b="N1", node_c="N2",
                                           seed=a.seed))
    out.append(protocols.purify(0.9, 0.9))
    failed = [r for r in out
              if r.get("verdict") == protocols.FAIL_TOKEN]
    if failed:
        raise CommandRejected("a reference protocol failed its equivalence "
                              "check", {"failed": failed})
    return {"results": out,
            "claim": "reference protocol semantics verified numerically; not "
                     "evidence of physical entanglement"}


def cmd_protocol_execute(a: argparse.Namespace) -> dict:
    """Execute a compiled protocol plan in the classical reference emulation."""
    an = Analysis(*load_program(a))
    try:
        plan = protocols.ProtocolCompiler().compile(an.program, an.verification)
    except protocols.ProtocolError as exc:
        raise CommandRejected(f"protocol compilation refused: {exc}")
    if not plan.steps:
        raise CommandRejected(
            "the program contains no distributed protocol rows, so there is "
            "nothing to execute")
    ledger = protocols.EbitLedger()
    fabric_ = protocols.ClassicalFeedbackFabric()
    trace: List[dict] = []
    pending: Dict[str, str] = {}
    for sid in plan.topological_order():
        step = plan.by_id(sid)
        record: Dict[str, Any] = {"step_id": sid, "kind": step.kind,
                                  "node": step.node, "row": step.row}
        if step.kind == "EPR_GENERATE":
            eid = ledger.request(step.node, "N_PEER", epoch=0,
                                 owner_protocol=step.row)
            ledger.generate(eid, epoch=0)
            pending[step.row] = eid
            record["ebit_id"] = eid
        elif step.kind == "EPR_HERALD":
            eid = pending.get(step.row)
            if eid and ledger.get(eid).state == "GENERATING":
                ledger.herald(eid, fidelity=0.98, epoch=0)
            record["ebit_id"] = eid
        elif step.kind == "CLASSICAL_SEND":
            mid = fabric_.send(step.node, "N_PEER", {"step": sid})
            pending[f"msg:{sid}"] = mid
            record["message_id"] = mid
        elif step.kind == "CLASSICAL_RECV":
            sends = [k for k in pending if k.startswith("msg:")]
            if sends:
                mid = pending.pop(sorted(sends)[0])
                record["payload"] = fabric_.receive(mid)
        record["state"] = "EMULATED"
        trace.append(record)
    return {"steps_executed": len(trace), "trace": trace,
            "ebit_inventory": ledger.counts(),
            "classical_fabric": {"replay": fabric_.replay(),
                                 "replay_hash": fabric_.replay_hash(),
                                 "acyclic": fabric_.check_acyclic()},
            "execution_class": "CLASSICAL_DISTRIBUTED_PROTOCOL_EMULATION",
            "claim": "the compiled protocol DAG was executed by the classical "
                     "reference emulator; no physical link was used"}


def _ready_ebit(ledger: protocols.EbitLedger, a: str = "N0",
                b: str = "N1") -> str:
    eid = ledger.request(a, b, epoch=0, owner_protocol="cli")
    ledger.generate(eid, epoch=0)
    ledger.herald(eid, fidelity=1.0, epoch=0)
    return eid


# -- runtime commands ------------------------------------------------------

def cmd_run_task(a: argparse.Namespace) -> dict:
    fed = fabric.build_flat_federation(max(1, a.workers))
    rt = fabric.TaskRuntime(fed, max_workers=max(1, a.workers))
    n = max(1, a.count)
    for i in range(n):
        rt.spawn(_square, i, task_id=f"T{i:05d}")
    rt.run()
    results = rt.await_all()
    if results != [i * i for i in range(n)]:
        raise CommandRejected("task runtime produced incorrect results")
    return {"profile": rt.profile.as_dict(), "tasks": n,
            "results_hash": fabric.stable_hash(results),
            "reduction": rt.reduction(lambda x, y: x + y, results, initial=0),
            "log_hash": rt.log.hash(),
            "runtime": rt.as_dict()}


def _square(x: int) -> int:
    return x * x


def cmd_run_dataflow(a: argparse.Namespace) -> dict:
    rt = fabric.DataflowRuntime()
    src = rt.channel("src", capacity=16)
    mid = rt.channel("mid", capacity=16)
    dst = rt.channel("dst", capacity=16)
    rt.add_node(fabric.DataflowNode("double", lambda x: x * 2,
                                    inputs=[src], outputs=[mid]))
    rt.add_node(fabric.DataflowNode("increment", lambda x: x + 1,
                                    inputs=[mid], outputs=[dst]))
    items = list(range(min(8, max(1, a.count))))
    for i in items:
        src.send(i)
    decisions = rt.run(max_steps=64)
    produced = []
    while True:
        ok, value = dst.try_receive()
        if not ok:
            break
        produced.append(value)
    expected = [i * 2 + 1 for i in items]
    if produced != expected:
        raise CommandRejected("dataflow produced incorrect results",
                              {"expected": expected, "produced": produced})
    return {"firings": [d.as_dict() for d in decisions],
            "produced": produced, "runtime": rt.as_dict()}


def cmd_run_bsp(a: argparse.Namespace) -> dict:
    fed = fabric.build_flat_federation(max(2, a.workers))
    engine = fabric.BSPEngine(fed)
    workers = fed.worker_ids()
    records = []
    for step in range(3):
        fns = {w: (lambda inbox, s=step: s) for w in workers}
        plan = [(workers[i], workers[(i + 1) % len(workers)], step)
                for i in range(len(workers))]
        records.append(engine.superstep(fns, plan).as_dict())
    return {"supersteps": records, "totals": engine.totals(),
            "barrier_levels": list(fabric.BARRIER_LEVELS),
            "engine": engine.as_dict()}


def cmd_run_async(a: argparse.Namespace) -> dict:
    engine = fabric.AsyncEngine()
    futures = [engine.submit(_square, i) for i in range(min(8, max(1, a.count)))]
    completed = engine.drain()
    values = [f.result() for f in futures]
    if values != [i * i for i in range(len(futures))]:
        raise CommandRejected("async engine produced incorrect results")
    engine.retry("F0", 1)
    engine.route_change("F0", "R0", "R1")
    engine.measurement_control("F0", 1)
    return {"completed": completed, "values": values,
            "event_kinds": list(fabric.ASYNC_EVENT_KINDS),
            "happens_before": engine.happens_before_graph(),
            "engine": engine.as_dict()}


def cmd_run_collective(a: argparse.Namespace) -> dict:
    workers = max(1, a.workers)
    op = a.op.upper()
    if op not in lang.OPS_COLLECTIVE:
        raise CommandRejected(
            f"{op!r} is not an admitted collective",
            {"admitted": list(lang.OPS_COLLECTIVE)})
    if a.algorithm not in fabric.COLLECTIVE_ALGORITHMS:
        raise CommandRejected(
            f"{a.algorithm!r} is not an admitted collective algorithm",
            {"admitted": list(fabric.COLLECTIVE_ALGORITHMS)})
    col = fabric.Collectives(workers)
    values = list(range(1, workers + 1))
    data = 7 if op == "BROADCAST" else values
    if op == "ALLTOALL":
        data = [[i * workers + j for j in range(workers)]
                for i in range(workers)]
    result = col.run(op, data, algorithm=a.algorithm)
    return {"operation": op, "workers": workers,
            "algorithm_selection": fabric.select_algorithm(
                op, workers, 1024).as_dict(),
            "result": result.as_dict(), "log_hash": col.log.hash()}


def cmd_run_farm(a: argparse.Namespace) -> dict:
    source, path = load_program(a) if (a.program or a.example) \
        else (EXAMPLE_PROGRAM, "<example>")
    an = Analysis(source, path)
    circuit = an.circuit()
    shots = max(1, a.shots)
    workers = max(1, a.workers)
    per_worker = [shots // workers + (1 if i < shots % workers else 0)
                  for i in range(workers)]
    fed = fabric.build_flat_federation(workers)
    rt = fabric.TaskRuntime(fed, max_workers=workers)
    for i, n in enumerate(per_worker):
        rt.spawn(_farm_shard, an.source, an.path, n, a.seed + i,
                 task_id=f"FARM{i:03d}")
    rt.run()
    shards = rt.await_all()
    merged: Dict[str, int] = {}
    for shard in shards:
        for key, value in shard.items():
            merged[key] = merged.get(key, 0) + value
    if sum(merged.values()) != shots:
        raise CommandRejected("shot farming lost or invented shots",
                              {"expected": shots,
                               "observed": sum(merged.values())})
    return {"farm_kind": "SHOT_PARALLEL", "workers": workers, "shots": shots,
            "shots_per_worker": per_worker,
            "counts": dict(sorted(merged.items())),
            "qubits": circuit.n_qubits,
            "claim": "embarrassingly parallel classical shot farming; the "
                     "quantum boundary is never crossed"}


def _farm_shard(source: str, path: str, shots: int, seed: int) -> Dict[str, int]:
    prog, _ = lang.parse(source, path)
    vr = lang.verify(prog)
    circuit = simulator.circuit_from_program(prog, vr)
    if shots <= 0:
        return {}
    res = simulator.run_statevector(circuit, shots=shots, seed=seed)
    return dict(res.get("counts", {}))


def cmd_load_balance(a: argparse.Namespace) -> dict:
    workers = max(1, a.workers)
    fed = fabric.build_flat_federation(workers)
    rt = fabric.TaskRuntime(fed)
    tasks = [rt.spawn(_square, i, task_id=f"LB{i:04d}",
                      cost_estimate=1.0 + (i % 3))
             for i in range(max(1, a.count))]
    lb = fabric.LoadBalancer(fed)
    signals = {w: fabric.WorkerSignal(w, throughput=1.0 + 0.1 * i)
               for i, w in enumerate(fed.worker_ids())}
    dynamic = lb.dynamic_assign(tasks, signals)
    straggler = fabric.StragglerEngine()
    for i, w in enumerate(fed.worker_ids()):
        straggler.observe_throughput(w, 1.0 if i else 0.25)
        straggler.observe_queue_delay(w, 0.1 if i else 4.0)
    return {"decisions": [d.as_dict() for d in dynamic],
            "ledger": lb.ledger(),
            "straggler_verdicts": [straggler.detect(w).as_dict()
                                   for w in fed.worker_ids()],
            "steal_levels": list(fabric.STEAL_LEVELS)}


def cmd_failure_inject(a: argparse.Namespace) -> dict:
    if a.scenario not in resilience.SCENARIO_INDEX:
        raise CommandRejected(
            f"unknown failure scenario {a.scenario!r}",
            {"admitted": list(resilience.SCENARIO_IDS)})
    injector = resilience.FailureInjector(seed=a.seed)
    event = injector.inject(a.scenario, target="W00")
    return {"scenario": a.scenario, "event": event.as_dict(),
            "injector": injector.as_dict()}


def cmd_inject_and_recover(a: argparse.Namespace) -> dict:
    if a.scenario not in resilience.SCENARIO_INDEX:
        raise CommandRejected(
            f"unknown failure scenario {a.scenario!r}",
            {"admitted": list(resilience.SCENARIO_IDS)})
    store = resilience.CheckpointStore()
    store.save("CK-0", "classical_measurement", {"counter": 41},
               classically_known=True)
    event = resilience.FailureInjector(seed=a.seed).inject(a.scenario, "W00")
    record = resilience.RecoveryPlanner().explain(
        event, resilience.DEFAULT_CONTEXTS.get(a.scenario, {}))
    decision = resilience.SupervisionTree().handle("W00", record.recovery_class,
                                                   event)
    restored = store.load("CK-0")
    return {"failure": event.as_dict(), "classification": record.as_dict(),
            "supervision": decision.as_dict(),
            "checkpoint_verification": store.verify("CK-0"),
            "restored_state": restored,
            "quantum_policy": "unknown quantum state is never checkpointed, "
                              "rolled back or duplicated"}


def cmd_recover(a: argparse.Namespace) -> dict:
    entries = resilience.run_campaign(seed=a.seed)
    summary = resilience.campaign_summary(entries)
    return {"classes": list(lang.RECOVERY_CLASSES),
            "entries": entries, "summary": summary}


def cmd_replay(a: argparse.Namespace) -> dict:
    log = fabric.EventLog("cli-replay")
    for i in range(max(1, min(64, a.count))):
        log.append(f"W{i % 4:02d}", "STEP", inputs={"i": i}, outputs={"i": i},
                   resource_delta={"cpu_work": 1.0})
    report = log.verify_replay(log.reconstruct())
    if not report.ok:
        raise CommandRejected("deterministic replay failed",
                              {"report": report.as_dict()})
    return {"events": len(log), "log_hash": log.hash(),
            "report": report.as_dict(),
            "token": fabric.TOKEN_REPLAY_PASS}


def cmd_reconstruct(a: argparse.Namespace) -> dict:
    log = fabric.EventLog("cli-reconstruct")
    log.append("W00", "PREP", inputs={"q": 0}, outputs={"q": 0},
               ownership_delta={"acquire": ["q[0]"]},
               resource_delta={"cpu_work": 1.0})
    log.append("W00", "MOVE", inputs={"q": 0}, outputs={"q": 0},
               ownership_delta={"move": [["q[0]", "W00", "W01"]]})
    log.append("W01", "RELEASE", ownership_delta={"release": ["q[0]"]},
               resource_delta={"cpu_work": 2.0})
    state = log.reconstruct()
    return {"final_state": state, "log_hash": log.hash(),
            "events": log.replay_trace()}


def cmd_consistency_check(a: argparse.Namespace) -> dict:
    if a.profile not in lang.CONSISTENCY_PROFILES:
        raise CommandRejected(
            f"unknown consistency profile {a.profile!r}",
            {"admitted": list(lang.CONSISTENCY_PROFILES)})
    laws = crdt.verify_all_crdt_laws(samples=16, seed=a.seed or 1)
    partition = crdt.PartitionModel(partitions_possible=True)
    failure = crdt.FailureModel(replicas=3, crdt_declared=True)
    verdicts = {p: crdt.ConsistencyContract(p, partition, failure)
                .evaluate().as_dict()
                for p in lang.CONSISTENCY_PROFILES}
    engine = crdt.AntiEntropy()
    for i in range(3):
        engine.add_replica(f"R{i}", crdt.GCounter(f"R{i}")).increment(i + 1)
    convergence = engine.converge(max_rounds=16)
    if not convergence.converged:
        raise CommandRejected("anti-entropy did not converge",
                              {"report": convergence.as_dict()})
    return {"requested_profile": a.profile,
            "requested_verdict": verdicts[a.profile],
            "all_verdicts": verdicts, "crdt_laws": laws,
            "anti_entropy": convergence.as_dict()}


def cmd_property_test(a: argparse.Namespace) -> dict:
    report = conformance.property_campaign(seed=a.seed, count=max(1, a.count))
    if not report["ok"]:
        raise CommandRejected("property campaign failed", report)
    return report


def cmd_metamorphic_test(a: argparse.Namespace) -> dict:
    report = conformance.metamorphic_campaign(seed=a.seed,
                                              count=max(1, a.count))
    if not report["ok"]:
        raise CommandRejected("metamorphic campaign failed", report)
    return report


def cmd_differential_test(a: argparse.Namespace) -> dict:
    report = conformance.differential_campaign(seed=a.seed,
                                               count=max(1, a.count))
    if not report["ok"]:
        raise CommandRejected("differential campaign failed", report)
    return report


def cmd_conformance(a: argparse.Namespace) -> dict:
    subset = [s.strip() for s in a.subset.split(",")] if a.subset else None
    report = conformance.run_suite(subset=subset, workers=max(1, a.workers))
    if report["failed"]:
        raise CommandRejected("conformance failures", report)
    return report


def cmd_benchmark(a: argparse.Namespace) -> dict:
    """Measure this host, and say so. No number here is a hardware claim."""
    source = EXAMPLE_PROGRAM if not (a.program or a.example) \
        else load_program(a)[0]
    measurements: List[dict] = []

    def timed(name: str, fn: Callable[[], Any], reps: int) -> None:
        started = time.perf_counter()
        for _ in range(reps):
            fn()
        elapsed = time.perf_counter() - started
        measurements.append({"name": name, "repetitions": reps,
                             "total_s": round(elapsed, 6),
                             "per_call_s": round(elapsed / reps, 9)})

    prog, _ = lang.parse(source, "<benchmark>")
    vr = lang.verify(prog)
    graph = ses_mod.build_ses(prog, vr)
    po = ses_mod.analyze(graph)
    circuit = simulator.circuit_from_program(prog, vr)
    reps = max(1, min(200, a.count))
    timed("parse", lambda: lang.parse(source, "<benchmark>"), reps)
    timed("verify", lambda: lang.verify(prog), reps)
    timed("build_ses", lambda: ses_mod.build_ses(prog, vr), reps)
    timed("analyze", lambda: ses_mod.analyze(graph), reps)
    timed("partition_k2",
          lambda: planner.HypergraphPartitioner().partition(graph, 2,
                                                            exact=False), reps)
    timed("statevector",
          lambda: simulator.run_statevector(circuit, seed=a.seed), reps)
    return {"measurements": measurements,
            "work_W": round(po.work, 6), "span_D": round(po.span, 6),
            "qubits": circuit.n_qubits,
            "claim": "wall-clock measurements of this classical host only; "
                     "they are not quantum hardware performance figures"}


def cmd_soak(a: argparse.Namespace) -> dict:
    report = conformance.soak(hours=a.hours, seed=a.seed)
    if report["failures"]:
        raise CommandRejected("soak observed failures", report)
    return report


def cmd_ledgers(a: argparse.Namespace) -> dict:
    source, path = load_program(a)
    conf = None
    if a.subset:
        conf = conformance.run_suite(
            subset=[s.strip() for s in a.subset.split(",")],
            workers=max(1, a.workers))
    ls = ledgers.LedgerSet.build(source, path, k=a.partitions, seed=a.seed,
                                 shots=a.shots, conformance=conf)
    written: List[str] = []
    if a.out:
        written = ls.write(a.out)
    return {"files": list(ledgers.LEDGER_FILES),
            "written": written,
            "digest": ls.digest(),
            "qcir_hash": ls.qcir.hash(),
            "provenance": ls.ledgers["PROVENANCE_LEDGER.json"],
            "release_qualification": ls.ledgers["RELEASE_QUALIFICATION.json"],
            "ledgers": None if a.out else ls.ledgers}


def cmd_adapter_check(a: argparse.Namespace) -> dict:
    # PA21.3: a *classical* adapter now exists and may be checked. A physical
    # quantum adapter still does not, and asking for one is still blocked.
    # The two paths are kept deliberately separate: binding a classical VM
    # must never become a route to a physical claim.
    if a.target in ("bottle-rocket", bottlerocket.TARGET_ID):
        try:
            ad = bottlerocket.BottleRocketAdapter()
        except adapters.AdapterRefusal as exc:
            raise CommandRejected(exc.reason, exc.detail) from None
        return {"adapter_abi": adapters.ADAPTER_ABI,
                "target_class": "CLASSICAL_LOCAL_VM",
                "attestation": ad.attest(),
                "topology": ad.topology(),
                "calibration": ad.calibration(),
                "native_gate_set": sorted(ad.native_gate_set()),
                "limits": ad.limits(),
                "feature_classes": {
                    f: ad.feature_class(f) for f in
                    ("row_sequence_witness", "bounded_termination",
                     "deterministic_replay", "integer_arithmetic",
                     "declared_step_budget", "image_signing",
                     "independent_image_verification",
                     "CX", "H", "MEASURE", "TELEPORT", "PREP0")},
                "physical_release_outputs": {
                    name: BLOCKED_EXTERNAL_AUTHORITY
                    for name in ledgers.PHYSICAL_RELEASE_OUTPUTS},
                "physical_qpu": False, "physical_parallel": False,
                "physical_distributed": False}
    raise CommandBlocked(
        BLOCKED_EXTERNAL_AUTHORITY,
        f"no authenticated physical quantum target adapter exists for "
        f"{a.target!r}. Binding a physical target requires an external "
        f"authority with authenticated hardware; this package cannot supply "
        f"one and will not simulate the answer. (A classical adapter is "
        f"available as --target bottle-rocket; it is not a substitute and "
        f"does not lift this blocker.)",
        {"requested_target": a.target,
         "NETWORK": NETWORK, "BACKEND": BACKEND,
         "classical_adapters": [bottlerocket.TARGET_ID],
         "physical_qpu": False, "physical_parallel": False,
         "physical_distributed": False,
         "target_verified": False})


def cmd_backend_run(a: argparse.Namespace) -> dict:
    """Execute a bundle's row-sequence witness natively on a bound target.

    This is a *classical* execution. It carries no quantum claim: see
    `spec/PA_LCTL_BOTTLE_ROCKET_BACKEND.md` s1.
    """
    text, source_name = load_program(a)
    prog, diags = lang.parse(text)
    if prog is None:
        raise CommandRejected(
            "program did not parse; nothing was executed",
            {"diagnostics": [d.as_dict() for d in diags]})
    v = lang.verify(prog)
    if not v.ok:
        raise CommandRejected(
            "program did not verify; nothing was executed",
            {"diagnostics": [d.as_dict() for d in v.diagnostics]})
    try:
        ad = bottlerocket.BottleRocketAdapter()
        result = ad.submit(prog, shots=1, seed=a.seed)
    except adapters.AdapterRefusal as exc:
        raise CommandRejected(exc.reason, exc.detail) from None
    return {"source": source_name,
            "adapter_abi": adapters.ADAPTER_ABI,
            "result": result,
            "provenance": ad.provenance(prog)}


def cmd_qualify_distributed_target(a: argparse.Namespace) -> dict:
    raise CommandBlocked(
        BLOCKED_EXTERNAL_AUTHORITY,
        f"qualification of the distributed physical target {a.target!r} "
        f"requires two authenticated physical endpoints and a measured "
        f"entanglement link. None exist in this environment, so "
        f"PHYSICAL_PARALLEL_QPU_EXECUTION and "
        f"PHYSICAL_DISTRIBUTED_QPU_EXECUTION remain blocked.",
        {"requested_target": a.target,
         "outputs": {name: BLOCKED_EXTERNAL_AUTHORITY
                     for name in ledgers.PHYSICAL_RELEASE_OUTPUTS},
         "target_verified": False})


def cmd_redesignate(a: argparse.Namespace) -> dict:
    failures = redesignate.self_check()
    if failures:
        raise CommandRejected("redesignation self-check failed",
                              {"failures": failures})
    r = redesignate.Redesignator()
    text = a.text
    if text is None and (a.program or a.example):
        text = load_program(a)[0]
    result: Dict[str, Any] = {"self_check": "PASS",
                              "protected_words": list(redesignate.PROTECTED_WORDS)}
    if getattr(a, "audit", None):
        # PA21.3 (ship-plan #2). Before this, `selfcheck`'s redesignation gate
        # ran self_check() only -- a unit test of the rewriter against a fixed
        # 24-token vocabulary. It never looked at a delivered byte, which is
        # how PA21.2 shipped 292 capability-ledger items whose `target` field
        # previously read a JA-era designation. This walks what is on disk.
        audit = redesignate.audit_tree(a.audit)
        result["audit"] = audit
        if audit["unrewritten_files"]:
            raise CommandRejected(
                f"redesignation audit found {audit['unrewritten_tokens']} "
                f"designation token(s) in {len(audit['unrewritten_files'])} "
                f"delivered file(s) outside the provenance allowlist",
                {"audit": audit})
        return result
    if text is not None:
        result["mapped_text"] = r.map_text(text)
        result["report"] = r.report()
    return result


def cmd_selfcheck(a: argparse.Namespace) -> dict:
    """Prove the core is internally consistent, end to end, right now."""
    checks: List[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    source = EXAMPLE_PROGRAM
    prog, diags = lang.parse(source, "<selfcheck>")
    record("parse", prog is not None and not [d for d in diags
                                              if d.severity != "WARN"])
    vr = lang.verify(prog)
    record("verify", vr.ok, "; ".join(str(d) for d in vr.rejections))
    graph = ses_mod.build_ses(prog, vr)
    record("ses_hash_stable", graph.hash() == ses_mod.build_ses(prog, vr).hash())
    po = ses_mod.analyze(graph)
    record("work_span", po.work >= po.span > 0)
    circuit = simulator.circuit_from_program(prog, vr)
    r1 = simulator.run_statevector(circuit, shots=64, seed=5)
    r2 = simulator.run_statevector(circuit, shots=64, seed=5)
    record("simulation_deterministic",
           r1["final_state_hash"] == r2["final_state_hash"])
    record("execution_label_honest",
           r1["label"] in simulator.EXECUTION_LABELS
           and r1["label"].startswith("CLASSICAL_"))
    ls = ledgers.LedgerSet.build(source, "<selfcheck>", k=2, seed=0, shots=0)
    record("ledger_files", set(ls.ledgers) == set(ledgers.LEDGER_FILES))
    record("qcir_round_trip",
           ledgers.QCIRP2.from_dict(ls.qcir.as_dict()).hash() == ls.qcir.hash())
    prov = ls.ledgers["PROVENANCE_LEDGER.json"]
    record("provenance_fields", set(prov) == set(ledgers.PROVENANCE_FIELDS))
    record("no_physical_claim",
           prov["physical_qpu"] is False and prov["physical_parallel"] is False
           and prov["physical_distributed"] is False
           and prov["target_verified"] is False
           and prov["quantum_boundary"] == "QUANTUM_BOUNDARY_NOT_CROSSED")
    release = ls.ledgers["RELEASE_QUALIFICATION.json"]["outputs"]
    record("physical_outputs_blocked",
           all(release[n]["status"] == BLOCKED_EXTERNAL_AUTHORITY
               for n in ledgers.PHYSICAL_RELEASE_OUTPUTS))
    record("redesignation", not redesignate.self_check())
    record("policy_constants", NETWORK == "deny" and BACKEND == "none")
    record("case_registries",
           len(conformance.POSITIVE_CASES) >= 512
           and len(conformance.NEGATIVE_CASES) >= 512,
           f"{len(conformance.POSITIVE_CASES)} positive / "
           f"{len(conformance.NEGATIVE_CASES)} negative")

    failed = [c for c in checks if not c["ok"]]
    if failed:
        raise CommandRejected("selfcheck failed", {"checks": checks})
    return {"language": LANGUAGE, "profile": PROFILE, "version": CORE_VERSION,
            "NETWORK": NETWORK, "BACKEND": BACKEND,
            "checks": checks, "verdict": "SELFCHECK_PASS"}


HANDLERS: Dict[str, Callable[[argparse.Namespace], dict]] = {
    "verify": cmd_verify, "ses": cmd_ses, "commute-plan": cmd_commute_plan,
    "partition": cmd_partition, "hyperpartition": cmd_hyperpartition,
    "pareto-partition": cmd_pareto_partition, "place": cmd_place,
    "place2": cmd_place2, "route": cmd_route, "route2": cmd_route2,
    "qroute": cmd_qroute, "qroute2": cmd_qroute2, "schedule": cmd_schedule,
    "schedule2": cmd_schedule2, "qcir-p2": cmd_qcir_p2,
    "parallel-opportunities": cmd_parallel_opportunities,
    "partial-order": cmd_partial_order, "execute-local": cmd_execute_local,
    "simulate-distributed": cmd_simulate_distributed,
    "simulate-density-distributed": cmd_simulate_density_distributed,
    "simulate-stabilizer": cmd_simulate_stabilizer,
    "simulate-tensor": cmd_simulate_tensor, "simulate-ooc": cmd_simulate_ooc,
    "protocol-compile": cmd_protocol_compile,
    "protocol-run": cmd_protocol_run, "protocol-execute": cmd_protocol_execute,
    "run-task": cmd_run_task, "run-dataflow": cmd_run_dataflow,
    "run-bsp": cmd_run_bsp, "run-async": cmd_run_async,
    "run-collective": cmd_run_collective, "run-farm": cmd_run_farm,
    "load-balance": cmd_load_balance, "failure-inject": cmd_failure_inject,
    "inject-and-recover": cmd_inject_and_recover, "recover": cmd_recover,
    "replay": cmd_replay, "reconstruct": cmd_reconstruct,
    "consistency-check": cmd_consistency_check,
    "property-test": cmd_property_test,
    "metamorphic-test": cmd_metamorphic_test,
    "differential-test": cmd_differential_test,
    "conformance": cmd_conformance, "benchmark": cmd_benchmark,
    "soak": cmd_soak, "ledgers": cmd_ledgers,
    "adapter-check": cmd_adapter_check,
    "qualify-distributed-target": cmd_qualify_distributed_target,
    "redesignate": cmd_redesignate, "selfcheck": cmd_selfcheck,
    "backend-run": cmd_backend_run,
}

# Every declared command must have a handler; a missing one is a build error,
# never a silent no-op.
_missing = [c for c in COMMANDS if c not in HANDLERS]
if _missing:                                        # pragma: no cover
    raise lang.PALCTLError(f"cli commands without handlers: {_missing}")


# --------------------------------------------------------------------------
# 4. Entry point
# --------------------------------------------------------------------------

def _emit(payload: dict, indent: int) -> None:
    sys.stdout.write(json.dumps(payload, indent=indent, sort_keys=True,
                                ensure_ascii=False,
                                default=ledgers._json_default) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None
                                 else sys.argv[1:])
    except SystemExit as exc:
        return int(exc.code or EXIT_USAGE)

    handler = HANDLERS[args.command]
    envelope: Dict[str, Any] = {
        "command": args.command, "language": LANGUAGE, "version": CORE_VERSION,
        "NETWORK": NETWORK, "BACKEND": BACKEND,
    }
    try:
        result = handler(args)
    except CommandBlocked as exc:
        envelope.update({"outcome": exc.token, "message": str(exc),
                         "detail": exc.detail})
        _emit(envelope, args.indent)
        return EXIT_BLOCKED
    except CommandRejected as exc:
        envelope.update({"outcome": PROGRAM_REJECTED, "message": str(exc),
                         "detail": exc.detail})
        _emit(envelope, args.indent)
        return EXIT_REJECTED
    except (lang.PALCTLError, simulator.SimulationError,
            protocols.ProtocolError, fabric.FabricError, crdt.CRDTError,
            resilience.ResilienceError,
            erroralgebra.ErrorAlgebraError) as exc:
        envelope.update({"outcome": "COMMAND_FAILED",
                         "error": f"{type(exc).__name__}: {exc}"})
        _emit(envelope, args.indent)
        return EXIT_FAILED
    except Exception as exc:                        # noqa: BLE001 - reported
        envelope.update({"outcome": "COMMAND_FAILED",
                         "error": f"{type(exc).__name__}: {exc}"})
        _emit(envelope, args.indent)
        return EXIT_FAILED

    envelope.update({"outcome": "OK", "result": result})
    if args.out and args.command != "ledgers":
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, f"{args.command}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope, indent=2, sort_keys=True,
                                ensure_ascii=False,
                                default=ledgers._json_default) + "\n")
        envelope["written"] = [path]
    _emit(envelope, args.indent)
    return EXIT_OK


if __name__ == "__main__":                          # pragma: no cover
    sys.exit(main())
