"""DF command-line surface. Every DF container's BUILD / VERIFY / RUN launcher
delegates here:

  node containers
    python3 -B adapter/dfabric/cli.py node-attest            adapter attestation, topology, limits, feature classes
    python3 -B adapter/dfabric/cli.py node-build             build the embedded VM in place (vm/<pkg>/.build)
    python3 -B adapter/dfabric/cli.py node-verify [--full]   the node gate battery; exit non-zero on any FAIL
    python3 -B adapter/dfabric/cli.py node-run <program>     .pal -> witness on this node; native dialect -> run it

  the fabric container
    python3 -B adapter/dfabric/cli.py fabric-attest
    python3 -B adapter/dfabric/cli.py fabric-build           BUILD every sibling node container
    python3 -B adapter/dfabric/cli.py fabric-verify [--full]
    python3 -B adapter/dfabric/cli.py fabric-run [bundle.pal] [--profile P] [--placement static|dynamic]

Exit codes: 0 PASS/ok · 1 FAIL · 2 REJECTED (bad input) · 3 BLOCKED/refused (a
stated reason; nothing was executed for it). A command never prints a success
record for work it did not do.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CONTAINER = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (os.path.join(CONTAINER, "adapter"), os.path.join(CONTAINER, "core", "reference")):
    if p not in sys.path:
        sys.path.insert(0, p)

from dfabric import DF_RELEASE, AdapterRefusal, NODE_IDS  # noqa: E402
from dfabric.nodes import NODE_SPECS, make_adapter  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_REJECTED, EXIT_BLOCKED = 0, 1, 2, 3


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _emit(obj: Any, code: int = EXIT_OK, out: Optional[str] = None) -> int:
    text = json.dumps(obj, indent=2, sort_keys=True, default=str)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return code


def _node_id_of_container() -> Optional[str]:
    p = os.path.join(CONTAINER, "node", "NODE_DESCRIPTOR.json")
    if os.path.isfile(p):
        return json.load(open(p, encoding="utf-8"))["node_id"]
    return None


def _runs_dir() -> str:
    d = os.path.join(CONTAINER, "_runs")
    os.makedirs(d, exist_ok=True)
    return d


def _pacore():
    from pacore import lang  # noqa
    return lang


# --------------------------------------------------------------------------
# node commands
# --------------------------------------------------------------------------

def cmd_node_attest(a: argparse.Namespace) -> int:
    nid = a.node or _node_id_of_container()
    if not nid:
        return _emit({"rejected": True, "reason": "not a node container and --node not given"}, EXIT_REJECTED)
    try:
        ad = make_adapter(nid, a.root)
    except AdapterRefusal as exc:
        return _emit({"blocked": True, "node_id": nid, **exc.as_dict()}, EXIT_BLOCKED)
    return _emit({"schema": "DF/NODE_ATTEST/1", "df_release": DF_RELEASE,
                  "attestation": ad.attest(), "topology": ad.topology(), "calibration": ad.calibration(),
                  "native_gate_set": sorted(ad.native_gate_set()), "limits": ad.limits(),
                  "feature_classes": ad.feature_table(),
                  "physical_qpu": False, "physical_parallel": False, "physical_distributed": False},
                 out=a.out)


def _build_node(nid: str, root: str) -> Dict[str, Any]:
    from dfabric.gates import preflight, sh
    pf = preflight(nid)
    rec: Dict[str, Any] = {"node_id": nid, "root": root, "preflight": pf, "steps": []}
    if nid == "N_XLARGE":
        rec["note"] = ("nothing to compile: the QUORUM VM is Python; the bundled LCTL 1.6.1-RC1 column "
                       "verifier needs a Java runtime" + ("" if pf.get("java_available") else " (not found: compile will run with --skip-lctl-verify and say so)"))
        rec["ok"] = True
        return rec
    if not pf["can_build"]:
        rec["ok"] = False
        rec["blocked"] = f"no C toolchain: missing {pf['missing']} (see REQUIREMENTS.txt)"
        return rec
    cmds = {"N_SMALL": [["make"]], "N_MEDIUM": [["make"]], "N_LARGE": [["make"], ["make", "brctl-dev"]]}[nid]
    ok = True
    for c in cmds:
        r = sh(c, root)
        rec["steps"].append({"argv": c, "returncode": r["returncode"], "seconds": r["seconds"],
                             "tail": "\n".join(r["stdout"].strip().splitlines()[-8:]),
                             "stderr_tail": "\n".join(r["stderr"].strip().splitlines()[-8:])})
        ok = ok and r["returncode"] == 0
    rec["ok"] = ok
    if ok:
        try:
            rec["attest"] = make_adapter(nid, root).attest()["toolchain"]
        except AdapterRefusal as exc:
            rec["ok"] = False
            rec["blocked"] = exc.reason
    return rec


def cmd_node_build(a: argparse.Namespace) -> int:
    nid = a.node or _node_id_of_container()
    if not nid:
        return _emit({"rejected": True, "reason": "not a node container"}, EXIT_REJECTED)
    root = a.root or os.path.join(CONTAINER, "vm", NODE_SPECS[nid]["vm_dirname"])
    rec = _build_node(nid, root)
    rec["schema"] = "DF/NODE_BUILD/1"
    if rec.get("blocked"):
        return _emit(rec, EXIT_BLOCKED)
    return _emit(rec, EXIT_OK if rec["ok"] else EXIT_FAIL)


def cmd_node_verify(a: argparse.Namespace) -> int:
    from dfabric.gates import run_node_battery
    nid = a.node or _node_id_of_container()
    if not nid:
        return _emit({"rejected": True, "reason": "not a node container"}, EXIT_REJECTED)
    stamp = _utc()
    out_json = a.out or os.path.join(_runs_dir(), f"VERIFY_{stamp}.json")
    log_dir = os.path.join(os.path.dirname(out_json), f"VERIFY_{stamp}_logs")
    print(f"== DF VERIFY {NODE_SPECS[nid]['container']} ({nid}) -- {DF_RELEASE}", flush=True)
    res = run_node_battery(CONTAINER, nid, full=a.full, log_dir=log_dir,
                           adapter_root_override=a.root, skip_own_gates=a.skip_own_gates)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    t = res["totals"]
    print(f"== {res['verdict']}: {t['passed']} passed, {t['failed']} failed, {t['skipped']} skipped "
          f"in {t['wall_seconds']}s -> {out_json}", flush=True)
    return EXIT_OK if res["verdict"] == "PASS" else EXIT_FAIL


def cmd_node_run(a: argparse.Namespace) -> int:
    nid = a.node or _node_id_of_container()
    if not nid:
        return _emit({"rejected": True, "reason": "not a node container"}, EXIT_REJECTED)
    prog_path = a.program
    if not os.path.isfile(prog_path):
        return _emit({"rejected": True, "reason": f"no such program {prog_path}"}, EXIT_REJECTED)
    try:
        ad = make_adapter(nid, a.root)
    except AdapterRefusal as exc:
        return _emit({"blocked": True, "node_id": nid, **exc.as_dict(),
                      "hint": "run ./BUILD first"}, EXIT_BLOCKED)
    stamp = _utc()
    out = a.out or os.path.join(_runs_dir(), f"RUN_{stamp}_{os.path.basename(prog_path)}.json")
    try:
        if prog_path.endswith(".pal"):
            lang = _pacore()
            prog, diags = lang.parse(open(prog_path, encoding="utf-8").read())
            if prog is None:
                return _emit({"rejected": True, "reason": "program did not parse; nothing was executed",
                              "diagnostics": [d.as_dict() for d in diags]}, EXIT_REJECTED)
            v = lang.verify(prog)
            if not v.ok:
                return _emit({"rejected": True, "reason": "program did not verify; nothing was executed",
                              "diagnostics": [d.as_dict() for d in v.diagnostics]}, EXIT_REJECTED)
            r = ad.submit(prog, seed=a.seed)
            return _emit({"schema": "DF/NODE_RUN/1", "kind": "row_sequence_witness", "source": os.path.abspath(prog_path),
                          "result": r, "provenance": ad.provenance(prog)}, out=out)
        r = ad.run_native(prog_path, max_steps=a.max_steps)
        return _emit({"schema": "DF/NODE_RUN/1", "kind": "native_guest_program", "source": os.path.abspath(prog_path),
                      "result": r, "provenance": ad.provenance()}, out=out)
    except AdapterRefusal as exc:
        return _emit({"blocked": True, "node_id": nid, **exc.as_dict()}, EXIT_BLOCKED, out=out)


# --------------------------------------------------------------------------
# fabric commands
# --------------------------------------------------------------------------

def _fabric_roots(a: argparse.Namespace):
    from dfabric.gates import locate_nodes, bindable_roots
    located = locate_nodes(CONTAINER, a.nodes_root)
    return located, bindable_roots(located)


def cmd_fabric_attest(a: argparse.Namespace) -> int:
    from dfabric import fabric_runtime as FR
    located, roots = _fabric_roots(a)
    att = {}
    for nid, root in roots.items():
        att[nid] = make_adapter(nid, root).attest()
    fed = FR.build_federation(NODE_IDS, {n: (n in roots) for n in NODE_IDS})
    return _emit({"schema": "DF/FABRIC_ATTEST/1", "df_release": DF_RELEASE,
                  "nodes": {n: {"present": r["present"], "sums_match": r["sums_match"], "bound": n in roots,
                                "root": r["root"]} for n, r in located.items()},
                  "attestations": att, "federation": FR.federation_record(fed),
                  "physical_qpu": False, "physical_parallel": False, "physical_distributed": False},
                 out=a.out)


def cmd_fabric_build(a: argparse.Namespace) -> int:
    from dfabric.gates import locate_nodes
    located = locate_nodes(CONTAINER, a.nodes_root)
    recs = {}
    ok_all = True
    for nid, rec in located.items():
        if not rec["present"]:
            recs[nid] = {"ok": False, "skipped": f"container {rec['container']} not found beside DF_Fabric"}
            continue
        recs[nid] = _build_node(nid, rec["root"])
        ok_all = ok_all and recs[nid]["ok"]
    return _emit({"schema": "DF/FABRIC_BUILD/1", "nodes": recs}, EXIT_OK if ok_all else EXIT_FAIL)


def cmd_fabric_verify(a: argparse.Namespace) -> int:
    from dfabric.gates import run_fabric_battery
    stamp = _utc()
    out_json = a.out or os.path.join(_runs_dir(), f"VERIFY_{stamp}.json")
    log_dir = os.path.join(os.path.dirname(out_json), f"VERIFY_{stamp}_logs")
    print(f"== DF VERIFY DF_Fabric -- {DF_RELEASE}", flush=True)
    res = run_fabric_battery(CONTAINER, nodes_root=a.nodes_root, log_dir=log_dir, full=a.full)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    t = res["totals"]
    print(f"== {res['verdict']}: {t['passed']} passed, {t['failed']} failed, {t['skipped']} skipped "
          f"in {t['wall_seconds']}s -> {out_json}", flush=True)
    return EXIT_OK if res["verdict"] == "PASS" else EXIT_FAIL


def cmd_fabric_run(a: argparse.Namespace) -> int:
    from dfabric import fabric_runtime as FR
    lang = _pacore()
    located, roots = _fabric_roots(a)
    prog_path = a.program or os.path.join(CONTAINER, "fabric", "FABRIC.pal")
    if not os.path.isfile(prog_path):
        return _emit({"rejected": True, "reason": f"no such bundle {prog_path}"}, EXIT_REJECTED)
    prog, diags = lang.parse(open(prog_path, encoding="utf-8").read())
    if prog is None:
        return _emit({"rejected": True, "reason": "program did not parse; nothing was executed",
                      "diagnostics": [d.as_dict() for d in diags]}, EXIT_REJECTED)
    v = lang.verify(prog)
    if not v.ok:
        return _emit({"rejected": True, "reason": "program did not verify; nothing was executed",
                      "diagnostics": [d.as_dict() for d in v.diagnostics]}, EXIT_REJECTED)
    if not roots:
        return _emit({"blocked": True, "reason": "no fabric node is bound (node containers absent or not built)",
                      "nodes": {n: {"present": r["present"], "root": r["root"]} for n, r in located.items()},
                      "hint": "extract DF_Small/DF_Medium/DF_Large/DF_Xtra_Large beside DF_Fabric and run ./BUILD"},
                     EXIT_BLOCKED)
    stamp = _utc()
    out = a.out or os.path.join(_runs_dir(), f"RUN_{stamp}_{os.path.basename(prog_path)}.json")
    programs = tuple(p.strip() for p in a.programs.split(",")) if a.programs else ("replica", "pipeline", "bsp")
    try:
        run = FR.FabricRun(roots, profile=a.profile, max_workers=a.max_workers,
                           required_nodes=(NODE_IDS if a.strict else None))
        res = run.run_program(prog, programs=programs, placement=a.placement,
                              source_name=os.path.abspath(prog_path))
    except AdapterRefusal as exc:
        return _emit({"blocked": True, **exc.as_dict()}, EXIT_BLOCKED, out=out)
    if a.event_log:
        with open(a.event_log, "w", encoding="utf-8") as fh:
            fh.write(run.log.canonical())
        res["event_log"]["written_to"] = os.path.abspath(a.event_log)
    code = EXIT_OK if res["verdict"] == FR.TOKEN_AGREE else EXIT_FAIL
    return _emit(res, code, out=out)


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dfabric", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--node", choices=NODE_IDS, help="node id (defaults to this container's node)")
        p.add_argument("--root", help="VM package root override (default: vm/<package> in this container)")
        p.add_argument("--out", help="write the JSON record here (default: _runs/)")

    p = sp.add_parser("node-attest"); common(p); p.set_defaults(fn=cmd_node_attest)
    p = sp.add_parser("node-build"); common(p); p.set_defaults(fn=cmd_node_build)
    p = sp.add_parser("node-verify"); common(p)
    p.add_argument("--full", action="store_true", help="also run the VM's longer optional gates (sanitize/qualify/br500)")
    p.add_argument("--skip-own-gates", action="store_true", help="skip the VM's own build+gate (adapter battery only)")
    p.set_defaults(fn=cmd_node_verify)
    p = sp.add_parser("node-run"); common(p)
    p.add_argument("program", help=".pal bundle (row-sequence witness) or a native guest program")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-steps", type=int, default=None)
    p.set_defaults(fn=cmd_node_run)

    def fcommon(p):
        p.add_argument("--nodes-root", help="directory holding DF_Small/DF_Medium/DF_Large/DF_Xtra_Large (default: beside this container)")
        p.add_argument("--out")

    p = sp.add_parser("fabric-attest"); fcommon(p); p.set_defaults(fn=cmd_fabric_attest)
    p = sp.add_parser("fabric-build"); fcommon(p); p.set_defaults(fn=cmd_fabric_build)
    p = sp.add_parser("fabric-verify"); fcommon(p)
    p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_fabric_verify)
    p = sp.add_parser("fabric-run"); fcommon(p)
    p.add_argument("program", nargs="?", help=".pal bundle (default fabric/FABRIC.pal)")
    p.add_argument("--profile", default="single_process_deterministic",
                   choices=("single_process_deterministic", "multi_thread_deterministic",
                            "multi_process_deterministic", "multi_process_throughput"))
    p.add_argument("--placement", default="static", choices=("static", "dynamic"))
    p.add_argument("--programs", help="comma list of replica,pipeline,bsp (default all)")
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--strict", action="store_true", help="refuse unless all four nodes are bound")
    p.add_argument("--event-log", help="also write the canonical event log JSON here")
    p.set_defaults(fn=cmd_fabric_run)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
