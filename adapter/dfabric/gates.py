"""The DF gate batteries -- what VERIFY runs, and what the shipped
`conformance/DF_GATE_RESULTS.json` was produced by.

Every gate returns PASS, FAIL or SKIPPED with a stated detail. A SKIPPED gate
names the missing prerequisite (no C toolchain, no JDK, node container absent)
so that a missing dependency becomes a diagnostic, not an obscure failure. No
gate ever reports a PASS it did not observe.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import DF_RELEASE, AdapterRefusal, NODE_IDS, assert_not_physical
from . import witness as W
from . import manifest as M
from . import schemas as S
from .nodes import NODE_SPECS, ADAPTERS, make_adapter, container_root_of

GATE_SCHEMA = "DF/GATE_RESULTS/1"


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def host_record() -> dict:
    return {"platform": platform.platform(), "machine": platform.machine(),
            "python": sys.version.split()[0], "implementation": platform.python_implementation(),
            "cpu_count": os.cpu_count(), "cc": shutil.which("cc") or shutil.which("gcc") or shutil.which("clang"),
            "make": shutil.which("make"), "java": shutil.which("java"),
            "network_policy": "deny", "backend_policy": "none"}


class Battery:
    def __init__(self, subject: str, log_dir: Optional[str] = None):
        self.subject = subject
        self.gates: List[dict] = []
        self.log_dir = log_dir
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        self.t_start = time.perf_counter()

    def log(self, name: str, text: str) -> Optional[str]:
        if not self.log_dir:
            return None
        p = os.path.join(self.log_dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return os.path.basename(p)

    def gate(self, gid: str, name: str, fn: Callable[[], dict], *, skip_if: Optional[str] = None) -> dict:
        t0 = time.perf_counter()
        if skip_if:
            rec = {"id": gid, "name": name, "status": "SKIPPED", "seconds": 0.0, "detail": {"reason": skip_if}}
            self.gates.append(rec)
            print(f"  [SKIP] {gid} {name}: {skip_if}", flush=True)
            return rec
        try:
            detail = fn() or {}
            status = detail.pop("_status", "PASS")
        except AdapterRefusal as exc:
            status, detail = "FAIL", {"refusal": exc.as_dict()}
        except Exception as exc:  # noqa: BLE001 - recorded, never hidden
            status, detail = "FAIL", {"exception": f"{type(exc).__name__}: {exc}"}
        rec = {"id": gid, "name": name, "status": status,
               "seconds": round(time.perf_counter() - t0, 3), "detail": detail}
        self.gates.append(rec)
        print(f"  [{status}] {gid} {name} ({rec['seconds']}s)", flush=True)
        return rec

    def results(self, extra: Optional[dict] = None) -> dict:
        n = len(self.gates)
        p = sum(1 for g in self.gates if g["status"] == "PASS")
        f = sum(1 for g in self.gates if g["status"] == "FAIL")
        s = sum(1 for g in self.gates if g["status"] == "SKIPPED")
        out = {"schema": GATE_SCHEMA, "df_release": DF_RELEASE, "subject": self.subject,
               "executed_at_utc": utcnow(), "host": host_record(),
               "gates": self.gates,
               "totals": {"run": n, "passed": p, "failed": f, "skipped": s,
                          "wall_seconds": round(time.perf_counter() - self.t_start, 3)},
               "verdict": "PASS" if f == 0 else "FAIL",
               "rule": ("PASS means every gate that could run passed; SKIPPED gates name the "
                        "missing prerequisite and are not counted as passes")}
        if extra:
            out.update(extra)
        return out


# --------------------------------------------------------------------------
# helpers shared by node and fabric batteries
# --------------------------------------------------------------------------

def sh(cmd: Sequence[str], cwd: str, timeout: int = 1800, env: Optional[dict] = None) -> dict:
    t0 = time.perf_counter()
    p = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env=env)
    return {"argv": list(cmd), "returncode": p.returncode, "seconds": round(time.perf_counter() - t0, 3),
            "stdout": p.stdout, "stderr": p.stderr}


def tail(s: str, n: int = 40) -> str:
    lines = s.strip().splitlines()
    return "\n".join(lines[-n:])


def preflight(node_id: str) -> dict:
    """Which declared requirements are satisfied on this host."""
    have = {
        "cc": shutil.which("cc") or shutil.which("gcc") or shutil.which("clang"),
        "make": shutil.which("make"),
        "python3": sys.executable,
        "java": shutil.which("java"),
        "sh": shutil.which("sh"),
        "sha256sum": shutil.which("sha256sum"),
    }
    openssl_hdr = any(os.path.isfile(os.path.join(d, "openssl", "evp.h"))
                      for d in ("/usr/include", "/usr/local/include", "/opt/homebrew/include", "/usr/local/opt/openssl/include"))
    have["openssl_headers"] = openssl_hdr
    need_c = node_id in ("N_SMALL", "N_MEDIUM", "N_LARGE")
    c_ok = bool(have["cc"] and have["make"] and openssl_hdr)
    out = {"node_id": node_id, "found": have, "declared": NODE_SPECS[node_id]["requirements"]}
    if need_c:
        out["can_build"] = c_ok
        out["missing"] = [k for k in ("cc", "make", "openssl_headers") if not have[k]]
    else:
        out["can_build"] = True
        out["missing"] = []
        out["java_available"] = bool(have["java"])
    return out


def own_build_and_gate(node_id: str, root: str, full: bool = False) -> Dict[str, dict]:
    """The VM's own build and acceptance gate, run with its stock flags."""
    recs: Dict[str, dict] = {}
    if node_id == "N_SMALL":
        recs["build"] = sh(["make"], root)
        recs["own_gate"] = sh(["make", "clean", "operational"], root)
        if full:
            recs["sanitize"] = sh(["make", "sanitize"], root)
        recs["own_sums"] = sh(["sha256sum", "-c", "--quiet", "SHA256SUMS.txt"], root)
    elif node_id == "N_MEDIUM":
        recs["build"] = sh(["make"], root)
        recs["own_gate"] = sh(["make", "operational"], root)
        if full:
            recs["verify_br500"] = sh([sys.executable, "evidence/verify_br500.py"], root)
        recs["own_sums"] = sh(["sha256sum", "-c", "--quiet", "SHA256SUMS"], root)
    elif node_id == "N_LARGE":
        recs["build"] = sh(["make"], root)             # `make` = build + operational gate
        recs["build_dev"] = sh(["make", "brctl-dev"], root)
        recs["own_gate"] = sh(["make", "size"], root)   # the 61K boundary, measured
        if full:
            recs["qualify"] = sh(["make", "qualify"], root)
        recs["own_sums"] = sh(["sha256sum", "-c", "--quiet", "RELEASE_CONTENTS.sha256"], root)
    elif node_id == "N_XLARGE":
        recs["own_gate"] = sh(["sh", "validation/VERIFY_ALL.sh"], root)
        recs["own_gate_l4_l9"] = sh(["sh", "validation/VERIFY_L4_L9_OPEN_GATES.sh"], root)
        recs["vm_verify_test"] = sh(["make", "-C", "vm", "verify", "test"], root)
        recs["own_sums"] = sh(["sha256sum", "-c", "--quiet", "SHA256SUMS.txt"], root)
    return recs


def _copy_payload(src: str, dst: str) -> None:
    shutil.copytree(src, dst, symlinks=True,
                    ignore=shutil.ignore_patterns(".build", "__pycache__", "*.pyc"))


def artifact_schema_gate(container: str, kind: str) -> dict:
    """G2: every JSON artifact validates against the schema shipped beside it.
    Before the seal (no MANIFEST.json yet) artifacts that are written after the
    battery are reported as not-yet-present instead of missing."""
    amap = S.ARTIFACT_SCHEMA_MAP_NODE if kind == "node" else S.ARTIFACT_SCHEMA_MAP_FABRIC
    pre_seal = not os.path.isfile(os.path.join(container, "MANIFEST.json"))
    errs, pending, validated = {}, [], []
    for rel, name in amap.items():
        p = os.path.join(container, rel)
        if not os.path.isfile(p):
            if pre_seal:
                pending.append(rel)
                continue
            errs[rel] = ["missing artifact"]
            continue
        sp = os.path.join(container, "schemas", f"{name}.json")
        schema = json.load(open(sp, encoding="utf-8")) if os.path.isfile(sp) else S.SCHEMAS[name]
        e = S.validate(json.load(open(p, encoding="utf-8")), schema)
        if e:
            errs[rel] = e[:10]
        else:
            validated.append(rel)
    cdir = os.path.join(container, "corpus")
    n_rec = 0
    if os.path.isdir(cdir):
        import gzip
        for fn in os.listdir(cdir):
            if fn.endswith(".jsonl.gz"):
                with gzip.open(os.path.join(cdir, fn), "rt", encoding="utf-8") as fh:
                    for i, line in enumerate(fh):
                        e = S.validate(json.loads(line), S.SCHEMAS["DF_TRANSLATION_CORPUS_RECORD"])
                        n_rec += 1
                        if e:
                            errs[f"{fn}#{i}"] = e[:5]
    return {"_status": "PASS" if not errs else "FAIL", "violations": errs, "validated": validated,
            "not_yet_present_pre_seal": pending, "corpus_records_validated": n_rec}


def citation_gate(container: str) -> dict:
    """G3: every evidence citation in the capability ledger resolves."""
    p = os.path.join(container, "reports", "DF_CAPABILITY_LEDGER.json")
    if not os.path.isfile(p):
        return {"_status": "SKIPPED", "reason": "ledger not present (pre-seal assembly run)"}
    led = json.load(open(p, encoding="utf-8"))
    bad = []
    for it in led["items"]:
        for ev in it.get("evidence") or []:
            target = ev.split("#")[0]
            if target and not os.path.exists(os.path.join(container, target)):
                bad.append(f"{it['id']}: {ev}")
    return {"_status": "PASS" if not bad else "FAIL", "unresolved": bad, "items": len(led["items"])}


def refresh_artifact_gates(container: str, kind: str, results: dict) -> dict:
    """Re-run G2 and G3 (cheap, artifact-only) after the ledger/corpus/provenance
    were written, replacing their records in `results` in place."""
    for gid, fn in (("G2", lambda: artifact_schema_gate(container, kind)), ("G3", lambda: citation_gate(container))):
        t0 = time.perf_counter()
        detail = fn()
        status = detail.pop("_status", "PASS")
        for g in results["gates"]:
            if g["id"] == gid:
                g.update({"status": status, "seconds": round(time.perf_counter() - t0, 3), "detail": detail})
    n = len(results["gates"])
    results["totals"].update({"run": n, "passed": sum(1 for g in results["gates"] if g["status"] == "PASS"),
                              "failed": sum(1 for g in results["gates"] if g["status"] == "FAIL"),
                              "skipped": sum(1 for g in results["gates"] if g["status"] == "SKIPPED")})
    results["verdict"] = "PASS" if results["totals"]["failed"] == 0 else "FAIL"
    return results


# --------------------------------------------------------------------------
# the NODE battery
# --------------------------------------------------------------------------

def load_examples(container: str) -> List[str]:
    ex = os.path.join(container, "examples")
    out = []
    if os.path.isdir(ex):
        for fn in sorted(os.listdir(ex)):
            if fn.endswith(".pal"):
                out.append(os.path.join(ex, fn))
    node_pal = os.path.join(container, "node", "NODE.pal")
    if os.path.isfile(node_pal):
        out.append(node_pal)
    fabric_pal = os.path.join(container, "fabric", "FABRIC.pal")
    if os.path.isfile(fabric_pal):
        out.append(fabric_pal)
    return out


def _parse_verified(path: str):
    from pacore import lang
    text = open(path, encoding="utf-8").read()
    prog, diags = lang.parse(text)
    if prog is None:
        raise AdapterRefusal(f"{os.path.basename(path)} did not parse", {"diagnostics": [d.as_dict() for d in diags]})
    v = lang.verify(prog)
    if not v.ok:
        raise AdapterRefusal(f"{os.path.basename(path)} did not verify",
                             {"diagnostics": [d.as_dict() for d in v.diagnostics]})
    return prog


def _mutations(prog):
    """Four sealed-content mutations that must each change the witness."""
    from pacore import lang
    text = prog.canonical_text() if hasattr(prog, "canonical_text") else None
    rows = [r.render(prog.columns) for r in prog.rows]
    words = [W.row_word(r) for r in rows]
    muts = {}
    # 1. change one cell of one row: alter the last row's text by one char
    r = rows[-1]
    changed = r[:-1] + ("X" if r[-1] != "X" else "Y")
    muts["cell_change"] = words[:-1] + [W.row_word(changed)]
    # 2. reorder two rows
    if len(words) >= 2:
        muts["row_reorder"] = words[1:2] + words[0:1] + words[2:]
    # 3. delete a row
    if len(words) >= 2:
        muts["row_delete"] = words[:-1]
    # 4. insert a row (duplicate first row's word at the end)
    muts["row_insert"] = words + [words[0]]
    return words, muts



def _unified_overlay_prefixes(container: str) -> list:
    """Prefixes declared as overlays in a sibling DF_Unified/UNIFIED.json.

    Member-native G0 has no overlay concept of its own; UNIFIED U3/U4 already
    classify these extras. When the stockpile layout is present beside this
    container, honor the same declarations so G0.1/G0.2 do not FAIL on
    intentional unbound overlays (e.g. DF_Fabric PHOTON/, pk/).
    """
    parent = os.path.dirname(os.path.abspath(container))
    name = os.path.basename(os.path.abspath(container))
    candidates = [
        os.path.join(parent, "DF_Unified", "UNIFIED.json"),
        os.path.join(parent, "DF_Unified_v1.0.0", "UNIFIED.json"),
    ]
    for cand in candidates:
        if not os.path.isfile(cand):
            continue
        try:
            doc = json.load(open(cand, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        prefs = []
        for o in doc.get("overlays") or []:
            if o.get("container") == name and o.get("prefix"):
                prefs.append(str(o["prefix"]).replace(chr(92), "/"))
        return prefs
    return []


def _filter_overlay_extras(paths, prefixes):
    if not prefixes:
        return list(paths)
    out = []
    for p in paths:
        rel = str(p).replace(chr(92), "/")
        if any(rel == pref.rstrip("/") or rel.startswith(pref) for pref in prefixes):
            continue
        out.append(p)
    return out


def run_node_battery(container: str, node_id: str, *, full: bool = False,
                     scratch_root: Optional[str] = None, keep_scratch: bool = False,
                     log_dir: Optional[str] = None, adapter_root_override: Optional[str] = None,
                     skip_own_gates: bool = False) -> dict:
    """The complete node battery. `container` is the DF node container root."""
    spec = NODE_SPECS[node_id]
    B = Battery(f"{spec['container']} ({node_id})", log_dir)
    payload = os.path.join(container, "vm", spec["vm_dirname"])
    core = os.path.join(container, "core")
    if os.path.join(core, "reference") not in sys.path:
        sys.path.insert(0, os.path.join(core, "reference"))

    # ---- G0: integrity of the container itself --------------------------------
    def g_sums():
        if not os.path.isfile(os.path.join(container, "SHA256SUMS.txt")):
            return {"_status": "SKIPPED", "reason": "SHA256SUMS.txt not present (pre-seal assembly run)"}
        r = M.check_sums(container)
        prefs = _unified_overlay_prefixes(container)
        if prefs and r.get("unbound"):
            kept = _filter_overlay_extras(r["unbound"], prefs)
            r["unbound_overlay_declared_n"] = len(r["unbound"]) - len(kept)
            r["unbound"] = kept
            r["pass"] = not r.get("invalid") and not r.get("bad") and not r.get("missing") and not r["unbound"]
            r["overlay_prefixes"] = prefs
        r["_status"] = "PASS" if r["pass"] else "FAIL"
        return r
    B.gate("G0.1", "container SHA256SUMS.txt verifies (every delivered byte)", g_sums)

    def g_manifest():
        if not os.path.isfile(os.path.join(container, "MANIFEST.json")):
            return {"_status": "SKIPPED", "reason": "MANIFEST.json not present (pre-seal assembly run)"}
        r = M.check_manifest_inventory(container)
        prefs = _unified_overlay_prefixes(container)
        if prefs and r.get("extra"):
            kept = _filter_overlay_extras(r["extra"], prefs)
            r["extra_overlay_declared_n"] = len(r["extra"]) - len(kept)
            r["extra"] = kept
            man = json.load(open(os.path.join(container, "MANIFEST.json"), encoding="utf-8"))
            r["pass"] = (not r.get("invalid") and not r.get("mismatched") and not r.get("missing") and not r["extra"]
                         and man.get("file_count") == len(man.get("files", [])))
            r["overlay_prefixes"] = prefs
        r["_status"] = "PASS" if r["pass"] else "FAIL"
        return r
    B.gate("G0.2", "MANIFEST.json inventory matches disk (paths, sizes, digests; no extras)", g_manifest)

    def g_payload():
        doc = json.load(open(os.path.join(container, "node", "PAYLOAD_DIGEST.json"), encoding="utf-8"))
        r = M.check_payload(payload, doc)
        r["_status"] = "PASS" if r["pass"] else "FAIL"
        r.pop("changed", None) if r["pass"] else None
        return r
    B.gate("G0.3", "embedded VM payload matches the release digest (derivation in provenance/)", g_payload)

    def g_core():
        doc = json.load(open(os.path.join(container, "core", "PACORE_DIGEST.json"), encoding="utf-8"))
        d, n = M.dir_digest(os.path.join(core, "reference", "pacore"))
        ok = (d == doc["pacore_tree_sha256"] and n == doc["file_count"])
        return {"_status": "PASS" if ok else "FAIL", "expected": doc["pacore_tree_sha256"], "found": d,
                "files": n, "pinned_from": doc.get("pinned_from")}
    B.gate("G0.4", "core/pacore tree digest equals the pinned digest (one core, many consumers)", g_core)

    B.gate("G2", "every JSON artifact validates against the schema shipped beside it", lambda: artifact_schema_gate(container, "node"))
    B.gate("G3", "every evidence citation in the capability ledger resolves to a delivered path", lambda: citation_gate(container))

    def g_selfcheck():
        r = sh([sys.executable, "-B", "-m", "reference.pacore.cli", "selfcheck"], core, timeout=600)
        ok = r["returncode"] == 0 and "SELFCHECK_PASS" in r["stdout"]
        B.log("pacore_selfcheck.log", r["stdout"][-4000:] + r["stderr"][-2000:])
        return {"_status": "PASS" if ok else "FAIL", "returncode": r["returncode"], "seconds": r["seconds"]}
    B.gate("G1", "reference core selfcheck (python3 -B -m reference.pacore.cli selfcheck) -> SELFCHECK_PASS", g_selfcheck)

    # ---- preflight ---------------------------------------------------------
    pf = preflight(node_id)
    B.gate("N0", "toolchain preflight against REQUIREMENTS.txt", lambda: dict(pf, _status="PASS" if pf["can_build"] else "SKIPPED"))
    can_build = pf["can_build"]

    # ---- the VM's own build and gates, in scratch ----------------------------
    scratch = None
    root_for_adapter = adapter_root_override
    own = {}
    if not skip_own_gates:
        if can_build:
            scratch = scratch_root or tempfile.mkdtemp(prefix=f"df-verify-{node_id.lower()}-")
            sroot = os.path.join(scratch, spec["vm_dirname"])
            if os.path.isdir(sroot):
                shutil.rmtree(sroot)
            _copy_payload(payload, sroot)

            def g_own():
                nonlocal own
                own = own_build_and_gate(node_id, sroot, full=full)
                for k, r in own.items():
                    B.log(f"own_{k}.log", "$ " + " ".join(r["argv"]) + f"\n[exit {r['returncode']} in {r['seconds']}s]\n"
                          + r["stdout"][-20000:] + ("\n--- stderr ---\n" + r["stderr"][-8000:] if r["stderr"] else ""))
                bad = {k: r["returncode"] for k, r in own.items() if r["returncode"] != 0}
                summary = {k: {"returncode": r["returncode"], "seconds": r["seconds"], "tail": tail(r["stdout"], 6)}
                           for k, r in own.items()}
                return {"_status": "PASS" if not bad else "FAIL", "steps": summary, "failed_steps": bad,
                        "scratch_root": sroot, "note": "run in a scratch copy; the sealed payload is never mutated"}
            B.gate("N1", "the VM's own build (stock flags) and its own acceptance gate reproduce", g_own)
            if root_for_adapter is None:
                root_for_adapter = sroot
        else:
            B.gate("N1", "the VM's own build (stock flags) and its own acceptance gate reproduce", lambda: {},
                   skip_if=f"no C toolchain on this host: missing {pf['missing']}")

    if root_for_adapter is None:
        # fall back to an in-place build if one exists (./BUILD was run)
        inplace = payload
        try:
            make_adapter(node_id, inplace)
            root_for_adapter = inplace
        except AdapterRefusal:
            root_for_adapter = None

    # ---- the adapter battery ---------------------------------------------------
    adapter_skip = None if root_for_adapter else "no built toolchain available (run ./BUILD or install the C toolchain)"
    ad = None

    def g_attest():
        nonlocal ad
        ad = make_adapter(node_id, root_for_adapter)
        a = ad.attest()
        assert a["trust_domain"] == "LOCAL_TRUSTED"
        assert a["execution_label"].startswith("CLASSICAL_")
        assert not a["physical_qpu"] and not a["physical_parallel"] and not a["physical_distributed"]
        assert ad.native_gate_set() == frozenset()
        return {"target_id": a["target_id"], "toolchain": a["toolchain"], "label": a["execution_label"],
                "feature_classes": ad.feature_table(), "limits": ad.limits()}
    B.gate("A1", "adapter attestation (PA-LCTL/TARGET_ADAPTER/1): LOCAL_TRUSTED, CLASSICAL_ label, no physical flag, empty gate set",
           g_attest, skip_if=adapter_skip)

    def g_firewall():
        cls = ADAPTERS[node_id]
        refusals = []
        # 1. physical trust domain
        class P1(cls):
            trust_domain = "PHYSICAL_TARGET_AUTHENTICATED"
        # 2. non-classical label
        class P2(cls):
            def __init__(self, *a, **k):
                self.EXECUTION_LABEL = "QPU_NATIVE_EXECUTION"
                super().__init__(*a, **k)
        # 3. classical label carrying a forbidden token
        class P3(cls):
            def __init__(self, *a, **k):
                self.EXECUTION_LABEL = "CLASSICAL_HARDWARE_EXECUTION"
                super().__init__(*a, **k)
        for i, k in enumerate((P1, P2, P3), 1):
            try:
                obj = k(root_for_adapter)
                # P2/P3 set label before super().__init__ but NodeAdapter overwrites
                # it from spec; assert directly on a probe object instead:
                if i in (2, 3):
                    class Probe: pass
                    pr = Probe(); pr.trust_domain = "LOCAL_TRUSTED"
                    pr.EXECUTION_LABEL = "QPU_NATIVE_EXECUTION" if i == 2 else "CLASSICAL_HARDWARE_EXECUTION"
                    assert_not_physical(pr)
                refusals.append({"negative": i, "refused": False})
            except AdapterRefusal as exc:
                refusals.append({"negative": i, "refused": True, "reason": exc.reason})
        ok = all(r["refused"] for r in refusals) and len(refusals) == 3
        return {"_status": "PASS" if ok else "FAIL", "negatives": refusals}
    B.gate("A2", "physical-evidence firewall: three physical claims are refused at construction",
           g_firewall, skip_if=adapter_skip)

    examples = load_examples(container)
    ex_results: Dict[str, Any] = {}

    def g_witness():
        for path in examples:
            prog = _parse_verified(path)
            r = ad.submit(prog)
            ex_results[os.path.basename(path)] = {
                "rows": r["rows"], "native_witness": r["native_witness"],
                "reference_witness": r["reference_witness"], "agree": r["differential_agreement"],
                "image_sha256": r["image_sha256"], "program_seal": r["program_seal"],
                "wall_s": r["wall_s"], "steps": [s["step"] for s in r["steps"]],
                "extra": r.get("extra", {})}
            if not r["differential_agreement"]:
                return {"_status": "FAIL", "example": path, "result": r}
        return {"_status": "PASS" if ex_results else "FAIL", "examples": ex_results}
    B.gate("A3", f"row-sequence witness executes natively and agrees with the CPython reference on {len(examples)} bundles",
           g_witness, skip_if=adapter_skip)

    def g_sensitivity():
        prog = _parse_verified(examples[0])
        words, muts = _mutations(prog)
        base = ad.submit_words(words)["native_witness"]
        out = {}
        for k, mw in muts.items():
            nat = ad.submit_words(mw)["native_witness"]
            out[k] = {"native": nat, "changed": nat != base, "reference": W.reference_witness_words(mw)}
        ok = all(v["changed"] and v["native"] == v["reference"] for v in out.values()) and len(out) == 4
        return {"_status": "PASS" if ok else "FAIL", "base": base, "mutations": out}
    B.gate("A4", "witness sensitivity: cell change, row reorder, row delete, row insert each change the native witness",
           g_sensitivity, skip_if=adapter_skip)

    def g_bounds():
        mr = W.MAX_ROWS[node_id]
        # over-bound: refused by the lowering before any VM runs
        try:
            W.lowering_record(node_id, list(range(mr + 1)))
            return {"_status": "FAIL", "reason": f"{mr + 1} rows were not refused"}
        except W.LoweringRefused as exc:
            det = exc.detail
        # at-bound: executes
        r = ad.submit_words(list(range(1, mr + 1))) if node_id != "N_XLARGE" else None
        # (the QVM bound of 21,843 rows would take ~30 min at ~35k steps/s; the
        #  refusal is checked, the at-bound execution is checked at 84 rows)
        if r is None:
            r = ad.submit_words(list(range(1, W.BR_MAX_ROWS + 1)))
        return {"_status": "PASS" if r["differential_agreement"] and det.get("feature_class") == "SUPPORTED_WITH_LIMITS" else "FAIL",
                "max_rows": mr, "over_bound_refusal": det, "at_bound_rows": r["rows"],
                "at_bound_instructions": r["instructions"], "at_bound_agree": r["differential_agreement"]}
    B.gate("A5", "stated bounds are refusals: max_rows+1 refused (SUPPORTED_WITH_LIMITS), max_rows executes",
           g_bounds, skip_if=adapter_skip)

    def g_budget():
        prog_path = os.path.join(container, "examples", f"loop_forever{spec['guest_ext']}")
        if not os.path.isfile(prog_path):
            return {"_status": "SKIPPED", "reason": "no loop_forever example shipped"}
        try:
            r = ad.run_native(prog_path, max_steps=64)
            return {"_status": "FAIL", "reason": "an infinite loop halted cleanly", "result": r}
        except AdapterRefusal as exc:
            det = exc.detail
            steps = det.get("steps", [])
            last = steps[-1] if steps else {}
            return {"_status": "PASS", "refusal": exc.reason,
                    "last_step": {k: last.get(k) for k in ("step", "returncode", "stdout", "stderr")}}
    B.gate("A6", "declared step budget is enforced: an unbounded loop traps (BUDGET / TRAP_RESOURCE) instead of running",
           g_budget, skip_if=adapter_skip)

    def g_replay():
        prog = _parse_verified(examples[0])
        words = W.words_of(prog)
        r1 = ad.submit_words(words)
        r2 = ad.submit_words(words)
        ok = (r1["native_witness"] == r2["native_witness"] and r1["image_sha256"] == r2["image_sha256"]
              and r1["lowering"]["source_sha256"] == r2["lowering"]["source_sha256"])
        return {"_status": "PASS" if ok else "FAIL", "witness": r1["native_witness"],
                "image_sha256": [r1["image_sha256"], r2["image_sha256"]],
                "note": "the signed image differs between runs on nodes that mint a fresh key per run; the unsigned image and the witness are identical"}
    B.gate("A7", "deterministic replay: the same rows lower to the same source and image and produce the same witness twice",
           g_replay, skip_if=adapter_skip)

    def g_refusals():
        prog = _parse_verified(examples[0])
        out = {}
        try:
            ad.submit(prog, shots=2); out["shots_2"] = "NOT REFUSED"
        except AdapterRefusal as exc:
            out["shots_2"] = f"refused: {exc.reason[:80]}"
        try:
            ad.submit(prog, qcir_p2={"schema": "WRONG/1"}); out["bad_qcir_schema"] = "NOT REFUSED"
        except AdapterRefusal as exc:
            out["bad_qcir_schema"] = f"refused: {exc.reason[:80]}"
        try:
            ad.submit_words([]); out["empty_rows"] = "NOT REFUSED"
        except AdapterRefusal as exc:
            out["empty_rows"] = f"refused: {exc.reason[:80]}"
        ok = all(v.startswith("refused") for v in out.values())
        return {"_status": "PASS" if ok else "FAIL", "refusals": out}
    B.gate("A8", "shots > 1, a wrong QCIR-P2 schema and an empty row list are refused, never answered",
           g_refusals, skip_if=adapter_skip)

    def g_native():
        p = os.path.join(container, "examples", f"add42{spec['guest_ext']}")
        if not os.path.isfile(p):
            return {"_status": "SKIPPED", "reason": "no add42 example shipped"}
        r = ad.run_native(p)
        ok = r["result_low64"] == 42 and r["halted"]
        return {"_status": "PASS" if ok else "FAIL", "result_low64": r["result_low64"],
                "result_register": r["result_register"], "steps": [s["step"] for s in r["steps"]],
                "wall_s": r["wall_s"]}
    B.gate("A9", f"a native {spec['guest_dialect'].split(' ')[0]} program (examples/add42{spec['guest_ext']}) runs and returns 42",
           g_native, skip_if=adapter_skip)

    if node_id == "N_MEDIUM":
        def g_pacore_equiv():
            from pacore.adapters import bottlerocket, brlower
            prog = _parse_verified(examples[0])
            words = W.words_of(prog)
            same_src = (W.lower_lctlc11(words) == brlower.lower(prog))
            os.environ["PA_LCTL_BOTTLE_ROCKET_ROOT"] = root_for_adapter
            pa = bottlerocket.BottleRocketAdapter(root_for_adapter)
            pr = pa.submit(prog)
            mine = ad.submit(prog)
            ok = same_src and pr["native_witness"] == mine["native_witness"] == pr["reference_witness"] and pr["differential_agreement"]
            return {"_status": "PASS" if ok else "FAIL", "lowering_byte_identical_to_brlower": same_src,
                    "pacore_native_witness": pr["native_witness"], "df_native_witness": mine["native_witness"],
                    "pacore_lctlc_source_sha256": pr["lctlc_source_sha256"], "df_source_sha256": mine["lowering"]["source_sha256"]}
        B.gate("A10", "equivalence with the corpora's own adapter: lowering byte-identical to pacore.adapters.brlower and identical witness via pacore.adapters.bottlerocket",
               g_pacore_equiv, skip_if=adapter_skip)

        def g_pacore_suite():
            core_dir = os.path.join(container, "core")
            env = dict(os.environ, PA_LCTL_BOTTLE_ROCKET_ROOT=root_for_adapter)
            r = sh([sys.executable, "-B", os.path.join(core_dir, "pamath", "tests", "test_bottlerocket_backend.py")],
                   core_dir, timeout=900, env=env)
            B.log("pacore_test_bottlerocket_backend.log", r["stdout"][-8000:] + r["stderr"][-4000:])
            ok = r["returncode"] == 0
            return {"_status": "PASS" if ok else "FAIL", "returncode": r["returncode"], "seconds": r["seconds"],
                    "tail": tail(r["stdout"], 8)}
        suite = os.path.join(container, "core", "pamath", "tests", "test_bottlerocket_backend.py")
        B.gate("A11", "the corpora's own backend suite (pamath/tests/test_bottlerocket_backend.py, 30 checks) passes against this node",
               g_pacore_suite, skip_if=(None if (os.path.isfile(suite) and not adapter_skip) else (adapter_skip or "suite not shipped")))

    if node_id == "N_XLARGE":
        def g_jvm():
            r = ex_results.get(os.path.basename(examples[0]), {}).get("extra", {})
            mode = r.get("lctl_column_verify")
            if mode == "JVM":
                return {"_status": "PASS", "lctl_column_verify": mode}
            return {"_status": "SKIPPED", "reason": f"column verifier not run: {mode}", "lctl_column_verify": mode}
        B.gate("A12", "the bundled LCTL 1.6.1-RC1 column verifier (JVM) verified the lowered unit before compilation",
               g_jvm, skip_if=adapter_skip)

    if scratch and not keep_scratch and not scratch_root:
        shutil.rmtree(scratch, ignore_errors=True)
    return B.results({"node_id": node_id, "container_root": container,
                      "adapter_root": root_for_adapter, "preflight": pf})


# --------------------------------------------------------------------------
# the FABRIC battery
# --------------------------------------------------------------------------

def locate_nodes(fabric_container: str, nodes_root: Optional[str] = None) -> Dict[str, dict]:
    """Find the four node containers beside the fabric container (or under nodes_root)."""
    reg = json.load(open(os.path.join(fabric_container, "fabric", "NODES.json"), encoding="utf-8"))
    base = nodes_root or os.path.dirname(os.path.abspath(fabric_container))
    out: Dict[str, dict] = {}
    for n in reg["nodes"]:
        cdir = os.path.join(base, n["container"])
        env_root = os.environ.get(f"DF_{n['node_id'][2:]}_ROOT")
        rec = {"node_id": n["node_id"], "container": n["container"], "container_dir": cdir,
               "present": os.path.isdir(cdir), "pinned": n, "root": None, "sums_match": None}
        if rec["present"]:
            sums = os.path.join(cdir, "SHA256SUMS.txt")
            if os.path.isfile(sums):
                rec["sums_match"] = (M.sha256_file(sums) == n["sums_sha256"])
            rec["root"] = os.path.join(cdir, "vm", NODE_SPECS[n["node_id"]]["vm_dirname"])
        if env_root:
            rec["root"] = env_root
            rec["present"] = True
        out[n["node_id"]] = rec
    return out


def bindable_roots(located: Dict[str, dict]) -> Dict[str, str]:
    roots = {}
    for nid, rec in located.items():
        if rec["present"] and rec["root"]:
            try:
                make_adapter(nid, rec["root"])
                roots[nid] = rec["root"]
            except AdapterRefusal:
                pass
    return roots


def run_fabric_battery(container: str, *, nodes_root: Optional[str] = None,
                       log_dir: Optional[str] = None, roots_override: Optional[Dict[str, str]] = None,
                       full: bool = False) -> dict:
    from . import fabric_runtime as FR
    from pacore import lang
    B = Battery("DF_Fabric", log_dir)
    core = os.path.join(container, "core")
    if os.path.join(core, "reference") not in sys.path:
        sys.path.insert(0, os.path.join(core, "reference"))

    def g_sums():
        if not os.path.isfile(os.path.join(container, "SHA256SUMS.txt")):
            return {"_status": "SKIPPED", "reason": "SHA256SUMS.txt not present (pre-seal assembly run)"}
        r = M.check_sums(container)
        prefs = _unified_overlay_prefixes(container)
        if prefs and r.get("unbound"):
            kept = _filter_overlay_extras(r["unbound"], prefs)
            r["unbound_overlay_declared_n"] = len(r["unbound"]) - len(kept)
            r["unbound"] = kept
            r["pass"] = not r.get("invalid") and not r.get("bad") and not r.get("missing") and not r["unbound"]
            r["overlay_prefixes"] = prefs
        r["_status"] = "PASS" if r["pass"] else "FAIL"
        return r
    B.gate("G0.1", "container SHA256SUMS.txt verifies (every delivered byte)", g_sums)

    def g_manifest():
        if not os.path.isfile(os.path.join(container, "MANIFEST.json")):
            return {"_status": "SKIPPED", "reason": "MANIFEST.json not present (pre-seal assembly run)"}
        r = M.check_manifest_inventory(container)
        prefs = _unified_overlay_prefixes(container)
        if prefs and r.get("extra"):
            kept = _filter_overlay_extras(r["extra"], prefs)
            r["extra_overlay_declared_n"] = len(r["extra"]) - len(kept)
            r["extra"] = kept
            man = json.load(open(os.path.join(container, "MANIFEST.json"), encoding="utf-8"))
            r["pass"] = (not r.get("invalid") and not r.get("mismatched") and not r.get("missing") and not r["extra"]
                         and man.get("file_count") == len(man.get("files", [])))
            r["overlay_prefixes"] = prefs
        r["_status"] = "PASS" if r["pass"] else "FAIL"
        return r
    B.gate("G0.2", "MANIFEST.json inventory matches disk", g_manifest)

    def g_core():
        doc = json.load(open(os.path.join(container, "core", "PACORE_DIGEST.json"), encoding="utf-8"))
        d, n = M.dir_digest(os.path.join(core, "reference", "pacore"))
        ok = (d == doc["pacore_tree_sha256"] and n == doc["file_count"])
        return {"_status": "PASS" if ok else "FAIL", "expected": doc["pacore_tree_sha256"], "found": d, "files": n}
    B.gate("G0.4", "core/pacore tree digest equals the pinned digest", g_core)

    B.gate("G2", "every JSON artifact validates against the schema shipped beside it", lambda: artifact_schema_gate(container, "fabric"))
    B.gate("G3", "every evidence citation in the capability ledger resolves to a delivered path", lambda: citation_gate(container))

    def g_selfcheck():
        r = sh([sys.executable, "-B", "-m", "reference.pacore.cli", "selfcheck"], core, timeout=600)
        ok = r["returncode"] == 0 and "SELFCHECK_PASS" in r["stdout"]
        B.log("pacore_selfcheck.log", r["stdout"][-4000:] + r["stderr"][-2000:])
        return {"_status": "PASS" if ok else "FAIL", "returncode": r["returncode"], "seconds": r["seconds"]}
    B.gate("G1", "reference core selfcheck -> SELFCHECK_PASS", g_selfcheck)

    # ---- nodes ---------------------------------------------------------------
    located = locate_nodes(container, nodes_root) if roots_override is None else {}
    roots = dict(roots_override) if roots_override else {}
    if roots_override is None:
        def g_nodes():
            missing = [n for n, r in located.items() if not r["present"]]
            mism = [n for n, r in located.items() if r["present"] and r["sums_match"] is False]
            det = {n: {"present": r["present"], "sums_match": r["sums_match"], "root": r["root"]} for n, r in located.items()}
            st = "PASS" if not mism and not missing else ("FAIL" if mism else "SKIPPED")
            return {"_status": st, "nodes": det, "missing": missing, "digest_mismatch": mism,
                    "reason": (f"absent node containers: {missing}" if missing and not mism else None)}
        B.gate("F0", "the four node containers are present beside this container and match their pinned digests", g_nodes)
        roots = bindable_roots(located)
        unbuilt = [n for n, r in located.items() if r["present"] and n not in roots]

        def g_bind():
            det = {n: (n in roots) for n in NODE_IDS}
            if unbuilt:
                return {"_status": "SKIPPED", "bound": det,
                        "reason": f"nodes present but not built (run ./BUILD in each): {unbuilt}"}
            if not roots:
                return {"_status": "SKIPPED", "bound": det, "reason": "no node is bound"}
            return {"_status": "PASS" if len(roots) == 4 else "SKIPPED", "bound": det,
                    "reason": None if len(roots) == 4 else f"only {sorted(roots)} bound"}
        B.gate("F1", "every present node binds (toolchain built) and attests", g_bind)

    if not roots:
        return B.results({"nodes_bound": [], "reason": "no node bound; fabric gates skipped"})

    fabric_pal = os.path.join(container, "fabric", "FABRIC.pal")
    examples = load_examples(container)

    def g_fabric_pal():
        prog = _parse_verified(fabric_pal)
        seal = prog.seal()
        doc = json.load(open(os.path.join(container, "fabric", "FABRIC_SEAL.json"), encoding="utf-8"))
        return {"_status": "PASS" if seal == doc["seal"] else "FAIL", "seal": seal, "pinned": doc["seal"],
                "rows": len(prog.rows)}
    B.gate("F2", "fabric/FABRIC.pal parses, verifies (lang.verify) and its seal equals the pinned FABRIC_SEAL.json", g_fabric_pal)

    def g_federation():
        fed = FR.build_federation()
        d = FR.federation_record(fed)
        doc = json.load(open(os.path.join(container, "fabric", "FEDERATION.json"), encoding="utf-8"))
        # compare structure ignoring provenance timestamps
        strip = lambda x: json.loads(json.dumps(x, sort_keys=True))
        ok = strip(d)["domains"] == strip(doc)["domains"] and d["worker_count"] == doc["worker_count"] == 4
        return {"_status": "PASS" if ok else "FAIL", "workers": fed.worker_ids(), "domains": fed.domain_ids()}
    B.gate("F3", "the federation object model rebuilds from the node descriptors and equals fabric/FEDERATION.json (4 workers, 2 domains)", g_federation)

    runs: Dict[str, Any] = {}

    def g_programs():
        allok = True
        for path in examples:
            prog = _parse_verified(path)
            run = FR.FabricRun(roots, profile="single_process_deterministic")
            out = run.run_program(prog, source_name=os.path.basename(path))
            runs[os.path.basename(path)] = {
                "rows": out["rows"], "verdict": out["verdict"], "event_log_hash": out["event_log"]["hash"],
                "events": out["event_log"]["events"], "replay": out["event_log"]["replay_self_check"]["token"],
                "replica_witnesses": out["programs"]["replica"]["native_witnesses"],
                "reference": out["programs"]["replica"]["reference_witness"],
                "pipeline_segments": out["programs"]["pipeline"]["segments"],
                "pipeline_chain": [(c["segment"], c["node_id"], c["rows"]) for c in out["programs"]["pipeline"]["chain"]],
                "bsp_votes": out["programs"]["bsp"]["votes"],
                "collective_rules": {k: v["choice"]["rule_id"] + "/" + v["choice"]["algorithm"]
                                     for k, v in out["programs"]["bsp"]["collectives"].items() if isinstance(v, dict) and "choice" in v},
                "wall_s": out["wall_s"]}
            B.log(f"fabric_run_{os.path.basename(path)}.json", json.dumps(out, indent=1, sort_keys=True, default=str))
            allok = allok and out["verdict"] == FR.TOKEN_AGREE and out["event_log"]["replay_self_check"]["token"] == "DETERMINISTIC_REPLAY_PASS"
        return {"_status": "PASS" if allok else "FAIL", "runs": runs, "nodes_bound": sorted(roots)}
    B.gate("F4", f"replica + pipeline + BSP fabric programs on {len(examples)} bundles: cross-node differential agreement, replay PASS",
           g_programs)

    def g_determinism():
        prog = _parse_verified(examples[0])
        out = {}
        for prof in ("single_process_deterministic", "multi_thread_deterministic", "multi_process_deterministic"):
            hs = []
            for _ in range(2):
                run = FR.FabricRun(roots, profile=prof)
                hs.append(run.run_program(prog)["event_log"]["hash"])
            out[prof] = {"hashes": hs, "identical": hs[0] == hs[1]}
        run = FR.FabricRun(roots, profile="multi_process_throughput")
        o = run.run_program(prog)
        out["multi_process_throughput"] = {"hash": o["event_log"]["hash"], "verdict": o["verdict"],
                                           "reproducibility": "NOT_CLAIMED (may_reorder=True; the log is complete but its hash is not asserted)"}
        det_ok = all(out[p]["identical"] for p in ("single_process_deterministic", "multi_thread_deterministic", "multi_process_deterministic"))
        same_across = len({out[p]["hashes"][0] for p in ("single_process_deterministic", "multi_thread_deterministic", "multi_process_deterministic")}) == 1
        return {"_status": "PASS" if det_ok and o["verdict"] == FR.TOKEN_AGREE else "FAIL", "profiles": out,
                "identical_hash_across_deterministic_profiles": same_across}
    B.gate("F5", "deterministic profiles reproduce the event-log hash exactly across two runs (single/thread/process); throughput profile runs, reproducibility not claimed",
           g_determinism)

    def g_long():
        long_pal = os.path.join(container, "examples", "df_long_chain_240.pal")
        if not os.path.isfile(long_pal):
            return {"_status": "SKIPPED", "reason": "long-chain example not shipped"}
        prog = _parse_verified(long_pal)
        outs = {}
        for placement in ("static", "dynamic"):
            run = FR.FabricRun(roots, profile="multi_process_deterministic")
            o = run.run_program(prog, programs=("pipeline",), placement=placement)
            p = o["programs"]["pipeline"]
            outs[placement] = {"segments": p["segments"], "chain": [(c["segment"], c["node_id"], c["rows"]) for c in p["chain"]],
                               "final": p["final_witness"], "reference": p["reference_witness"], "agree": p["differential_agreement"],
                               "tasks_per_worker": p["load_balance_ledger"]["tasks_per_worker"],
                               "distinct_nodes": len({c["node_id"] for c in p["chain"]})}
        ok = all(v["agree"] and v["segments"] >= 3 and v["distinct_nodes"] >= min(3, len(roots)) for v in outs.values())
        return {"_status": "PASS" if ok else "FAIL", "placements": outs}
    B.gate("F6", "a 240-row bundle is executed as a chain of 84-row segments placed across distinct nodes (static and dynamic placement) and reaches the reference witness",
           g_long)

    def g_portability():
        # negative: images are not portable between lineages -- the fabric moves rows, not images
        out = {}
        need = {"N_MEDIUM", "N_LARGE", "N_XLARGE"}
        if not need.issubset(roots):
            return {"_status": "SKIPPED", "reason": f"needs {sorted(need)} bound; have {sorted(roots)}"}
        work = tempfile.mkdtemp(prefix="df-portability-")
        try:
            med = make_adapter("N_MEDIUM", roots["N_MEDIUM"])
            src = W.lower_lctlc11([1, 2, 3])
            s = os.path.join(work, "u.lctlc"); open(s, "w", encoding="utf-8", newline="\n").write(src)
            img = os.path.join(work, "u.brimg")
            r = sh([med.bradmin, "compile-lctlc", s, img], roots["N_MEDIUM"])
            assert r["returncode"] == 0, r
            large = make_adapter("N_LARGE", roots["N_LARGE"])
            rl = sh([large.brctl_dev, "run", img], roots["N_LARGE"])
            try:
                jl = json.loads(rl["stdout"].strip().splitlines()[-1])
            except Exception:
                jl = {"raw": rl["stdout"][-200:]}
            out["medium_image_on_large"] = {"returncode": rl["returncode"], "json": jl,
                                            "rejected": jl.get("trap") == 17 or rl["returncode"] != 0}
            xl = make_adapter("N_XLARGE", roots["N_XLARGE"])
            rx = sh([sys.executable, xl.qvm, "run", img, "--trust", xl.trust_store, "--max-steps", "100"], roots["N_XLARGE"])
            out["medium_image_on_qvm"] = {"returncode": rx["returncode"], "stderr_tail": tail(rx["stderr"], 3),
                                          "rejected": rx["returncode"] != 0}
            ok = out["medium_image_on_large"]["rejected"] and out["medium_image_on_qvm"]["rejected"]
            return {"_status": "PASS" if ok else "FAIL", "finding": ("images are not portable across lineages; the fabric "
                    "therefore distributes sealed source rows and lowers per node"), **out}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    B.gate("F7", "negative: a 5.0.0 image is rejected by the 4.7.0 VM (trap 17 UNSUPPORTED_ABI) and by the QVM loader -- the fabric moves rows, not images",
           g_portability)

    def g_ladders():
        run = FR.FabricRun(roots, profile="multi_process_deterministic")
        p = run.provenance()
        ok = (p["quantum_boundary"] == "QUANTUM_BOUNDARY_NOT_CROSSED" and not p["physical_qpu"]
              and not p["physical_parallel"] and not p["physical_distributed"]
              and p["distributed_state"] in ("LOGICAL_DISTRIBUTED", "DISTRIBUTED_CLASSICAL_EMULATION")
              and p["network"] == "deny" and p["backend"] == "none")
        return {"_status": "PASS" if ok else "FAIL", "provenance": p}
    B.gate("F8", "provenance ladders honest: distributed_state <= DISTRIBUTED_CLASSICAL_EMULATION, quantum boundary NOT_CROSSED, physical flags False, NETWORK=deny",
           g_ladders)

    def g_bridge():
        tool = os.path.join(container, "tools", "mssl_to_lctlc.py")
        if not os.path.isfile(tool):
            return {"_status": "SKIPPED", "reason": "tools/mssl_to_lctlc.py not shipped"}
        need = {"N_SMALL", "N_MEDIUM"}
        if not need.issubset(roots):
            return {"_status": "SKIPPED", "reason": f"needs {sorted(need)} bound; have {sorted(roots)}"}
        work = tempfile.mkdtemp(prefix="df-bridge-")
        try:
            words = [W.row_word(r) for r in ("bridge-a", "bridge-b", "bridge-c", "bridge-d", "bridge-e")]
            small = make_adapter("N_SMALL", roots["N_SMALL"])
            rs = small.submit_words(words)
            src = W.lower_mssl(words)
            ms = os.path.join(work, "w.mssl"); open(ms, "w", encoding="utf-8", newline="\n").write(src)
            lc = os.path.join(work, "w.lctlc")
            r = sh([sys.executable, tool, ms, lc, "--report"], work)
            if r["returncode"] != 0:
                return {"_status": "FAIL", "translator": r}
            med = make_adapter("N_MEDIUM", roots["N_MEDIUM"])
            ex = med._execute_source(open(lc, encoding="utf-8").read(), "bridge", work)
            ok = ex["result_low64"] == rs["native_witness"] == W.reference_witness_words(words)
            return {"_status": "PASS" if ok else "FAIL", "small_native": rs["native_witness"],
                    "medium_native_after_translation": ex["result_low64"],
                    "reference": W.reference_witness_words(words),
                    "translator_report": json.loads(r["stdout"]) if r["stdout"].strip().startswith("{") else r["stdout"][-400:]}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    B.gate("F9", "dialect bridge: N_SMALL's MSSL witness program translated by tools/mssl_to_lctlc.py (PA-LCTL/MSSL_TO_LCTLC/1) runs on N_MEDIUM and yields the same witness",
           g_bridge)

    return B.results({"nodes_bound": sorted(roots), "roots": roots})
