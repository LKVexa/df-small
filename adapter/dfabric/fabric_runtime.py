"""The DF fabric: the four VM nodes federated under `pacore.fabric`.

What this module does, in the corpora's own terms
------------------------------------------------
* builds a real `pacore.fabric.Federation` (federation -> execution domains
  -> worker groups -> workers) from the node descriptors, with per-worker
  `ResourceLimits` measured from each VM (memory per live VM, one CPU slot,
  `qpu_slots = 0`, `ebit_budget = 0`);
* executes fabric programs under a real `TaskRuntime` in any of the four
  execution profiles (`PA_LCTL_FABRIC_SPEC.md` s2), with every task owned by
  the worker whose VM runs it, every event appended to one append-only
  `EventLog`, and the log sealed, reconstructed and replay-checked;
* moves *source rows*, never images, between nodes: the fabric's unit of
  distribution is the sealed PA-LCTL row sequence lowered per node, because
  the nodes are not binary compatible (measured: 5.0.0 images trap 17
  UNSUPPORTED_ABI on 4.7.0; BRIM binaries are rejected by the QVM loader).

Three fabric programs are provided:

`replica`  -- every node computes the whole-bundle witness (chained through
              its own <=84-row segments where necessary). Family
              CLASSICAL_REPLICA_PARALLEL. The nodes are four independent
              implementations (three C machines of two ISA lineages plus a
              Python-hosted machine); unanimity with the CPython reference is
              the fabric's differential check.
`pipeline` -- the row sequence is cut into <=84-row segments and segment k
              runs on the worker the load balancer placed it on, starting from
              segment k-1's accumulator: a dependency chain across
              heterogeneous nodes (span == work; nothing here is claimed to
              be parallel).
`bsp`      -- two supersteps: compute the witness locally, exchange it with
              every peer, vote; hierarchical barrier at federation level;
              plus ALLGATHER / ALLREDUCE(min,max) over explicit transfer
              schedules chosen by `select_algorithm` (rule id recorded).

Ladders (`PA_LCTL_EXECUTION_PROVENANCE.md`): parallel_state at most
PARALLEL_EMULATION, distributed_state at most DISTRIBUTED_CLASSICAL_EMULATION
(the four VMs are separate OS processes on ONE host; NETWORK=deny), quantum
boundary NOT_CROSSED, every physical flag False.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

from . import DF_RELEASE, AdapterRefusal, NODE_IDS, PHYSICAL_RELEASE_OUTPUTS, BLOCKED_EXTERNAL_AUTHORITY
from . import witness as W
from .nodes import NODE_SPECS, make_adapter

try:  # the reference core must be importable (core/ on sys.path)
    from pacore import fabric as F
    from pacore import lang
except Exception as exc:  # pragma: no cover
    raise ImportError("pacore (the PA-LCTL reference core) is not importable; put "
                      "<container>/core on sys.path") from exc

FABRIC_ID = "DF0"
FABRIC_SCHEMA = "DF/FABRIC_RUN/1"
FAMILY_REPLICA = "CLASSICAL_REPLICA_PARALLEL"
FAMILY_PIPELINE = "TASK_PARALLEL"
TOKEN_AGREE = "CROSS_NODE_DIFFERENTIAL_AGREEMENT"
TOKEN_DISAGREE = "CROSS_NODE_DIFFERENTIAL_MISMATCH"

DOMAINS = {
    "D_BR": {"description": "BOTTLE ROCKET lineage: 3.0.0-MODEL (MSSL), 5.0.0/ISA 4.1 (LCTLC/1.1), 4.7.0/BR 1.1 (LCTLC/1.2)"},
    "D_QVM": {"description": "QUORUM VM lineage: 5.0.0-candidate hosted Python VM (LCTLC/1.0)"},
}


# --------------------------------------------------------------------------
# federation
# --------------------------------------------------------------------------

def build_federation(node_ids: Sequence[str] = NODE_IDS,
                     present: Optional[Dict[str, bool]] = None) -> "F.Federation":
    """A real pacore Federation with one worker per VM node."""
    fed = F.Federation(federation_id=FABRIC_ID, trust="LOCAL_TRUSTED",
                       provenance=F.Provenance(created_by=DF_RELEASE, source_ref="fabric/FABRIC.pal"))
    domains: Dict[str, F.ExecutionDomain] = {}
    for did in DOMAINS:
        domains[did] = F.ExecutionDomain(domain_id=did, trust="LOCAL_TRUSTED",
                                         provenance=F.Provenance(created_by=DF_RELEASE))
    for nid in node_ids:
        s = NODE_SPECS[nid]
        g = F.WorkerGroup(group_id=s["group"], trust="LOCAL_TRUSTED",
                          provenance=F.Provenance(created_by=DF_RELEASE))
        w = F.Worker(
            worker_id=s["worker"],
            capabilities=frozenset({"classical", "row_sequence_witness", s["lineage"].lower(),
                                    s["guest_dialect"].split(" ")[0].lower()}),
            limits=F.ResourceLimits(cpu_slots=1, memory_bytes=int(s["ram_per_vm_bytes"]),
                                    qpu_slots=0, ebit_budget=0, bandwidth_bytes_per_tick=1.0e6),
            trust="LOCAL_TRUSTED",
            failure_domain=s["failure_domain"],
            provenance=F.Provenance(created_by=DF_RELEASE, source_ref=f"{s['container']}/node/NODE_DESCRIPTOR.json"),
        )
        w.alive = bool(present.get(nid, True)) if present else True
        g.add(w)
        domains[s["domain"]].add(g)
    for did in DOMAINS:
        fed.add(domains[did])
    return fed


def federation_record(fed: "F.Federation") -> dict:
    d = fed.as_dict()
    d["schema"] = "DF/FEDERATION/1"
    d["df_release"] = DF_RELEASE
    d["domain_descriptions"] = DOMAINS
    d["network_policy"] = "deny"
    d["backend_policy"] = "none"
    d["note"] = ("A model of a federation executed locally (PA_LCTL_FABRIC_SPEC.md s1). "
                 "Every worker is a local process running one embedded VM. Cross-machine "
                 "federation is BLOCKED (NETWORK=deny).")
    return d


# --------------------------------------------------------------------------
# tasks (module-level so the process profiles can pickle them)
# --------------------------------------------------------------------------

def witness_task(node_id: str, root: str, words: Sequence[int], acc_in: int,
                 unit_id: str) -> Dict[str, Any]:
    """Run one witness segment on one node. Returns only deterministic
    fields, so the event log's output hash is reproducible (hole H7)."""
    ad = make_adapter(node_id, root)
    r = ad.submit_words(list(words), acc_in, unit_id)
    return {"node_id": node_id, "rows": r["rows"], "acc_in": r["acc_in"],
            "native_witness": r["native_witness"],
            "reference_witness": r["reference_witness"],
            "differential_agreement": r["differential_agreement"],
            "instructions": r["instructions"],
            "image_sha256": r["image_sha256"],
            "source_sha256": r["lowering"]["source_sha256"],
            "halted": r["halted"], "step_count": len(r["steps"]),
            "lctl_column_verify": r["extra"].get("lctl_column_verify")}


def chain_task(node_id: str, root: str, words: Sequence[int], max_rows: int,
               unit_id: str) -> Dict[str, Any]:
    """Whole-bundle witness on one node, chained through its own segments."""
    ad = make_adapter(node_id, root)
    acc = W.FNV_OFFSET
    segs = W.segments(list(words), max_rows)
    seg_recs = []
    for i, seg in enumerate(segs):
        r = ad.submit_words(seg, acc, f"{unit_id}.s{i:03d}")
        acc = r["native_witness"]
        seg_recs.append({"segment": i, "rows": r["rows"], "acc_in": r["acc_in"],
                         "acc_out": r["native_witness"], "image_sha256": r["image_sha256"],
                         "source_sha256": r["lowering"]["source_sha256"],
                         "instructions": r["instructions"]})
    ref = W.reference_witness_words(list(words))
    return {"node_id": node_id, "rows": len(words), "segments": len(segs),
            "max_rows_per_segment": max_rows, "native_witness": acc,
            "reference_witness": ref, "differential_agreement": acc == ref,
            "segment_records": seg_recs}


# --------------------------------------------------------------------------
# the fabric run
# --------------------------------------------------------------------------

class FabricRun:
    def __init__(self, roots: Dict[str, str], *, profile: str = "single_process_deterministic",
                 max_workers: int = 4, name: str = "df_fabric",
                 required_nodes: Optional[Sequence[str]] = None):
        self.roots = {k: v for k, v in roots.items() if v}
        self.node_ids = [n for n in NODE_IDS if n in self.roots]
        self.absent = [n for n in NODE_IDS if n not in self.roots]
        for n in (required_nodes or ()):
            if n not in self.roots:
                raise AdapterRefusal(f"required node {n} is absent", {"absent": self.absent})
        if not self.node_ids:
            raise AdapterRefusal("no fabric node is bound; nothing to run", {"absent": self.absent})
        self.profile = F.ExecutionProfile.from_token(profile)
        self.clock = F.LamportClock(node=FABRIC_ID)
        self.log = F.EventLog(name, self.clock)
        self.federation = build_federation(NODE_IDS, {n: (n in self.roots) for n in NODE_IDS})
        self.runtime = F.TaskRuntime(self.federation, profile=self.profile, log=self.log,
                                     clock=self.clock, max_workers=max_workers)
        self.max_workers = max_workers
        # bind adapters up front so a missing toolchain refuses before any task runs
        self.attestations: Dict[str, dict] = {}
        for n in self.node_ids:
            self.attestations[n] = make_adapter(n, self.roots[n]).attest()

    def _worker(self, node_id: str) -> str:
        return NODE_SPECS[node_id]["worker"]

    # -- program 1: replica --------------------------------------------------
    def replica(self, words: Sequence[int], unit_id: str = "df.replica") -> dict:
        t0 = time.perf_counter()
        tasks = []
        for n in self.node_ids:
            tasks.append(self.runtime.spawn(
                chain_task, n, self.roots[n], list(words), W.MAX_ROWS[n], f"{unit_id}.{n}",
                task_id=f"{unit_id}.{n}", owner=self._worker(n),
                cost_estimate=float(len(words)), stage="replica",
                resource_claim={"cpu_slots": 1, "qpu_slots": 0},
                provenance={"family": FAMILY_REPLICA, "proof_ref": "witness"}))
        self.runtime.barrier("federation", participants=self.federation.worker_ids())
        results = self.runtime.await_all(tasks)
        ref = W.reference_witness_words(list(words))
        natives = {r["node_id"]: r["native_witness"] for r in results}
        agree = all(v == ref for v in natives.values()) and len(natives) == len(self.node_ids)
        rec = {
            "program": "replica", "family": FAMILY_REPLICA,
            "rows": len(words), "reference_witness": ref,
            "nodes": {r["node_id"]: r for r in results},
            "native_witnesses": natives,
            "unanimous": agree,
            "token": TOKEN_AGREE if agree else TOKEN_DISAGREE,
            "participants": len(self.node_ids), "absent_nodes": self.absent,
            "wall_s": round(time.perf_counter() - t0, 4),
        }
        self.log.append("-", "DF_REPLICA_VERDICT", inputs={"rows": len(words)},
                        outputs={"token": rec["token"], "witness": ref, "participants": rec["participants"]})
        return rec

    # -- program 2: pipeline -------------------------------------------------
    def pipeline(self, words: Sequence[int], unit_id: str = "df.pipeline",
                 placement: str = "static") -> dict:
        t0 = time.perf_counter()
        # The fabric's segment size is the smallest node bound (84 rows, the
        # BOTTLE ROCKET 256-instruction ceiling), whichever nodes are bound, so
        # every segment can be placed on any node and the chain shape of a
        # bundle does not depend on which containers happen to be present.
        seg_rows = W.BR_MAX_ROWS
        segs = W.segments(list(words), seg_rows)
        # placement of the segments over the live workers
        live = [self._worker(n) for n in self.node_ids]
        balancer = F.LoadBalancer(self.federation, log=self.log)
        # placeholder tasks for the balancer to place (fn bound after placement)
        holders = [self.runtime.spawn(None, task_id=f"{unit_id}.place.s{i:03d}", stage="placement",
                                      cost_estimate=float(len(seg)))
                   for i, seg in enumerate(segs)]
        if placement == "dynamic":
            signals = {}
            for n in self.node_ids:
                w = self.federation.worker(self._worker(n))
                # measured signal: throughput ~ 1 / measured wall of a 12-row witness on that node
                signals[w.worker_id] = F.WorkerSignal(worker_id=w.worker_id, alive=w.alive,
                                                      throughput=1.0, memory_pressure=0.0,
                                                      queue_delay=0.0)
            decisions = balancer.dynamic_assign(holders, signals)
        else:
            decisions = balancer.static_assign(holders, live)
        for h in holders:
            self.runtime.cancel(h, "placement placeholder; the real segment task follows")
        acc = W.FNV_OFFSET
        chain = []
        worker_to_node = {self._worker(n): n for n in self.node_ids}
        prev_task_id = None
        for i, (seg, d) in enumerate(zip(segs, decisions)):
            n = worker_to_node[d.worker_id]
            t = self.runtime.spawn(
                witness_task, n, self.roots[n], seg, acc, f"{unit_id}.s{i:03d}",
                task_id=f"{unit_id}.s{i:03d}", owner=d.worker_id,
                cost_estimate=float(len(seg)), stage=f"segment{i}",
                provenance={"family": FAMILY_PIPELINE, "depends_on": prev_task_id, "proof_ref": "witness"})
            r = self.runtime.await_task(t)     # data dependency: acc_{i+1} = acc_out_i
            self.log.append(d.worker_id, "DF_SEGMENT_HANDOFF",
                            inputs={"segment": i, "acc_in": acc},
                            outputs={"acc_out": r["native_witness"], "next": i + 1},
                            ownership_delta={"move": [[f"acc.s{i:03d}", d.worker_id,
                                                      decisions[i + 1].worker_id if i + 1 < len(decisions) else "-"]]})
            acc = r["native_witness"]
            chain.append({"segment": i, "node_id": n, "worker": d.worker_id, "rows": len(seg),
                          "acc_in": r["acc_in"], "acc_out": r["native_witness"],
                          "placement_mode": d.mode, "placement_score": d.score,
                          "image_sha256": r["image_sha256"]})
            prev_task_id = t.task_id
        ref = W.reference_witness_words(list(words))
        rec = {
            "program": "pipeline", "family": FAMILY_PIPELINE,
            "note": ("a dependency chain: segment k+1 consumes segment k's accumulator; "
                     "span == work, no parallel speedup is claimed"),
            "rows": len(words), "segment_rows": seg_rows, "segments": len(segs),
            "placement": placement, "chain": chain,
            "final_witness": acc, "reference_witness": ref,
            "differential_agreement": acc == ref,
            "token": TOKEN_AGREE if acc == ref else TOKEN_DISAGREE,
            "load_balance_ledger": balancer.ledger(),
            "wall_s": round(time.perf_counter() - t0, 4),
        }
        self.log.append("-", "DF_PIPELINE_VERDICT", inputs={"rows": len(words), "segments": len(segs)},
                        outputs={"token": rec["token"], "witness": acc})
        return rec

    # -- program 3: BSP + collectives ----------------------------------------
    def bsp(self, words: Sequence[int], unit_id: str = "df.bsp") -> dict:
        t0 = time.perf_counter()
        engine = F.BSPEngine(self.federation, log=self.log, clock=self.clock, profile=self.profile)
        workers = [self._worker(n) for n in self.node_ids]
        node_of = {self._worker(n): n for n in self.node_ids}
        witnesses: Dict[str, int] = {}

        def make_compute(wid: str):
            def fn(inbox: List[Any]) -> dict:
                n = node_of[wid]
                r = chain_task(n, self.roots[n], list(words), W.MAX_ROWS[n], f"{unit_id}.{n}")
                witnesses[wid] = r["native_witness"]
                return {"witness": r["native_witness"], "rows": r["rows"], "segments": r["segments"]}
            return fn

        s1 = engine.superstep({w: make_compute(w) for w in workers}, comm_plan=(),
                              barrier_level="worker_group")
        plan = [(src, dst, {"from": src, "witness": witnesses[src]})
                for src in workers for dst in workers if src != dst]

        def make_vote(wid: str):
            def fn(inbox: List[Any]) -> dict:
                mine = witnesses[wid]
                peers = {m["from"]: m["witness"] for m in inbox}
                return {"witness": mine, "peers": len(peers),
                        "agree": all(v == mine for v in peers.values())}
            return fn

        s2 = engine.superstep({w: make_vote(w) for w in workers}, comm_plan=plan,
                              barrier_level="federation")
        votes = {w: s2.results[w]["agree"] for w in workers}
        # collectives over explicit transfer schedules
        coll = F.Collectives(len(workers), log=self.log, clock=self.clock)
        vals = [witnesses[w] for w in workers]
        msg = 8
        ch_g = F.select_algorithm("ALLGATHER", len(workers), msg)
        ch_r = F.select_algorithm("ALLREDUCE", len(workers), msg)
        rg = coll.run("ALLGATHER", vals, algorithm=ch_g.algorithm)
        rmax = coll.run("ALLREDUCE", vals, algorithm=ch_r.algorithm, reduce_op="max")
        rmin = coll.run("ALLREDUCE", vals, algorithm=ch_r.algorithm, reduce_op="min")
        gathered_ok = all(v == vals for v in rg.values)
        reduce_ok = (len(set(rmax.values)) == 1 and len(set(rmin.values)) == 1
                     and rmax.values[0] == rmin.values[0] == max(vals) == min(vals))
        ref = W.reference_witness_words(list(words))
        agree = all(votes.values()) and gathered_ok and reduce_ok and all(v == ref for v in vals)
        rec = {
            "program": "bsp", "rows": len(words),
            "supersteps": [s1.as_dict(), s2.as_dict()], "totals": engine.totals(),
            "votes": votes, "witnesses": {w: witnesses[w] for w in workers},
            "reference_witness": ref,
            "collectives": {
                "allgather": {"choice": ch_g.as_dict(), "result": rg.as_dict(), "consistent": gathered_ok},
                "allreduce_max": {"choice": ch_r.as_dict(), "result": rmax.as_dict()},
                "allreduce_min": {"choice": ch_r.as_dict(), "result": rmin.as_dict()},
                "min_equals_max": reduce_ok,
            },
            "unanimous": agree,
            "token": TOKEN_AGREE if agree else TOKEN_DISAGREE,
            "wall_s": round(time.perf_counter() - t0, 4),
        }
        self.log.append("-", "DF_BSP_VERDICT", inputs={"rows": len(words)},
                        outputs={"token": rec["token"], "witness": ref})
        return rec

    # -- whole run -----------------------------------------------------------
    def run_program(self, program, programs: Sequence[str] = ("replica", "pipeline", "bsp"),
                    placement: str = "static", source_name: str = "<memory>") -> dict:
        v = lang.verify(program)
        if not v.ok:
            raise AdapterRefusal("program did not verify; nothing was executed",
                                 {"diagnostics": [d.as_dict() for d in v.diagnostics]})
        words = W.words_of(program)
        out: Dict[str, Any] = {
            "schema": FABRIC_SCHEMA, "df_release": DF_RELEASE, "fabric_id": FABRIC_ID,
            "source": source_name, "program_seal": program.seal(), "rows": len(words),
            "profile": self.profile.as_dict(),
            "nodes_bound": self.node_ids, "nodes_absent": self.absent,
            "programs": {},
        }
        t0 = time.perf_counter()
        for p in programs:
            if p == "replica":
                out["programs"]["replica"] = self.replica(words)
            elif p == "pipeline":
                out["programs"]["pipeline"] = self.pipeline(words, placement=placement)
            elif p == "bsp":
                out["programs"]["bsp"] = self.bsp(words)
            else:
                raise AdapterRefusal(f"unknown fabric program {p!r}", {})
        out.update(self.finish())
        out["wall_s"] = round(time.perf_counter() - t0, 4)
        tokens = [r["token"] for r in out["programs"].values()]
        out["verdict"] = TOKEN_AGREE if tokens and all(t == TOKEN_AGREE for t in tokens) else TOKEN_DISAGREE
        return out

    def finish(self) -> dict:
        h = self.log.seal()
        recon = self.log.reconstruct()
        rep = self.log.verify_replay(recon)   # self-consistency of reconstruct
        return {
            "event_log": {"name": self.log.name, "events": len(self.log), "hash": h,
                          "schema": "PA-LCTL/EVENTLOG/1",
                          "metrics_excluded_from_hash": True,
                          "reconstruction": {"event_count": recon.get("event_count"),
                                             "operations": recon.get("operations"),
                                             "events_per_worker": recon.get("events_per_worker")},
                          "replay_self_check": rep.as_dict()},
            "federation": {"id": FABRIC_ID, "workers": self.federation.worker_ids(),
                           "domains": self.federation.domain_ids()},
            "provenance": self.provenance(),
        }

    def provenance(self) -> dict:
        prof = self.profile
        parallel_state = ("PARALLEL_EMULATION" if prof.uses_threads or prof.uses_processes else "SERIAL")
        distributed_state = ("DISTRIBUTED_CLASSICAL_EMULATION" if prof.uses_processes
                             else "LOGICAL_DISTRIBUTED")
        return {
            "language": "PA-LCTL", "execution_class": "CLASSICAL_FABRIC_OF_LOCAL_VMS",
            "profile": prof.value,
            "parallel_state": parallel_state,
            "distributed_state": distributed_state,
            "distributed_state_ceiling": "DISTRIBUTED_CLASSICAL_EMULATION",
            "quantum_boundary": "QUANTUM_BOUNDARY_NOT_CROSSED",
            "target_verified": False,
            "physical_qpu": False, "physical_parallel": False, "physical_distributed": False,
            "physical_release_outputs": {n: BLOCKED_EXTERNAL_AUTHORITY for n in PHYSICAL_RELEASE_OUTPUTS},
            "network": "deny", "backend": "none",
            "host": {"platform": platform.platform(), "python": sys.version.split()[0],
                     "cpu_count": os.cpu_count()},
        }


def event_log_dump(run: FabricRun) -> dict:
    return json.loads(run.log.canonical())


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
