"""The four DF node adapters -- one per embedded virtual machine.

Each class implements the PA-LCTL target-adapter ABI
(`PA_LCTL_TARGET_ADAPTER_SPEC.md` s2: identity/authentication, capability
description, execution, refusal) over a *local, offline, deterministic,
classical* virtual machine, in the same shape as the corpora's own
`pacore.adapters.bottlerocket.BottleRocketAdapter`, and adds one method the
fabric needs: `submit_words(words, acc_in)`, which executes one witness
segment natively and returns the node's low-64-bit result.

Every adapter:

* declares `trust_domain = LOCAL_TRUSTED` (never PHYSICAL_TARGET_AUTHENTICATED);
* has an empty `native_gate_set()` -- none of these machines has a qubit;
* classifies every quantum feature `UNSUPPORTED`;
* runs `assert_not_physical()` in its constructor;
* fails closed: every method returns a complete, evidenced answer or raises
  `AdapterRefusal` with a stated reason. Nothing is truncated, estimated, or
  silently substituted (no interpreter fallback when a toolchain is unbuilt).

Result convention on every node: the witness (or a native program's result)
is the low 64 bits of the node's result register -- R2 on the three BOTTLE
ROCKET machines (their CLI convention), R0 on the QUORUM VM (its convention;
the lowering also copies R0 into R2, and both are read back and compared).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

from . import (ADAPTER_ABI, AdapterRefusal, BLOCKED_EXTERNAL_AUTHORITY,
               DF_RELEASE, PHYSICAL_RELEASE_OUTPUTS, assert_not_physical)
from . import witness as W

MASK64 = W.MASK64
ADAPTER_VERSION = "1.0.1"
SUBPROCESS_TIMEOUT_S = 300


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def container_root_of(module_file: str) -> str:
    """<container>/adapter/dfabric/<module>.py -> <container>."""
    return os.path.abspath(os.path.join(os.path.dirname(module_file), "..", ".."))


# --------------------------------------------------------------------------
# node specifications (static facts, measured from each package)
# --------------------------------------------------------------------------

NODE_SPECS: Dict[str, Dict[str, Any]] = {
    "N_SMALL": {
        "container": "DF_Small",
        "vm_dirname": "BOTTLE_ROCKET_3.0.0_MODEL_OPERATIONAL_110K",
        "vm_name": "BOTTLE ROCKET 3.0.0-MODEL (SIM-core model VM)",
        "target_id": "bottle-rocket-3.0.0-model-local",
        "lineage": "BOTTLE_ROCKET",
        "isa": {"name": "BRIM/1-v3 + APDU/1 + MSSL/ASM-1", "opcodes": 24, "registers": 16,
                "word_bits": 1048576, "stack_words": 256, "memory_bytes": 4096,
                "arith_modes": ["WRAP", "CHECKED", "SATURATE", "TRAPPING"], "capability_slots": 16},
        "guest_dialect": "MSSL/ASM-1",
        "guest_ext": ".mssl",
        "image_format": "BRIM/1-v3 (80-byte header, SHA-256 seal, Q17-MODEL-OPS-3 tag, optional Ed25519 signature; <= 51200 bytes)",
        "runtime_contract": "BRIM/1-v3 + APDU/1 + MSSL/ASM-1; word 1,048,576 bits; 16 registers; 24 opcodes; 4 arithmetic modes; classification MODEL_OPERATIONAL",
        "result_register": "R2",
        "domain": "D_BR", "group": "G_BR3", "worker": "W_SMALL", "failure_domain": "FD_SMALL",
        "ram_per_vm_bytes": 35926544,
        "cli_step_budget": 4096,
        "requirements": ["C11 compiler (cc/gcc/clang)", "GNU make", "OpenSSL 3 libcrypto headers+lib (-lcrypto)", "POSIX sh"],
        "label": "CLASSICAL_NATIVE_VM_BOUNDED_EXECUTION",
    },
    "N_MEDIUM": {
        "container": "DF_Medium",
        "vm_dirname": "BOTTLE_ROCKET_5.0.0_VM_110K_RC",
        "vm_name": "BOTTLE ROCKET 5.0.0 qualification candidate (frozen 4.7.0 core, ISA 4.1)",
        "target_id": "bottle-rocket-5.0.0-local",
        "lineage": "BOTTLE_ROCKET",
        "isa": {"name": "ISA 4.1 / ABI 1.0 / Device ABI 1.0", "opcodes": 32, "services": 20,
                "registers": 16, "word_bits": 1048576, "stack_words": 256, "memory_bytes": 4096,
                "arith_modes": ["WRAP", "CHECKED", "SATURATE", "TRAPPING"], "core_abi": "0x00040700",
                "image_version": 9},
        "guest_dialect": "LCTLC/1.1 (Columned LCTL, native C compiler brlctlc)",
        "guest_ext": ".lctlc",
        "image_format": "BRIM/1 + BRPV/1 (80-byte header, 96-byte provenance, optional Ed25519 signature; native <= 8368/8432 bytes; loader ceiling 51200); BRTM/1 secure bundles",
        "runtime_contract": "LCTLC/1.1 -> BRIR/1.1 -> BRIM/1+BRPV/1 -> BRTM/1; ISA 4.1; ABI 1.0; Device ABI 1.0; BRCR/1; BRGD/1; core ABI 0x00040700",
        "result_register": "R2",
        "domain": "D_BR", "group": "G_ISA41", "worker": "W_MEDIUM", "failure_domain": "FD_MEDIUM",
        "ram_per_vm_bytes": 11000000,
        "cli_step_budget": 4096,
        "requirements": ["C11 compiler (cc/gcc/clang)", "GNU make", "OpenSSL 3 libcrypto headers+lib (-lcrypto)", "Python 3 (qualification tools)", "POSIX sh"],
        "label": "CLASSICAL_NATIVE_VM_BOUNDED_EXECUTION",
    },
    "N_LARGE": {
        "container": "DF_Large",
        "vm_dirname": "BOTTLE_ROCKET_4.7.0_COLUMNED_LCTL_VIRTUAL_DEVICE_IO_SERVICE_ARCHITECTURE_61K",
        "vm_name": "BOTTLE ROCKET 4.7.0 Columned-LCTL Virtual Device/I/O/Service Architecture (BR/1.1, 61K)",
        "target_id": "bottle-rocket-4.7.0-br11-local",
        "lineage": "BOTTLE_ROCKET",
        "isa": {"name": "BR/1.1 (marker 21) / ABI 2 / Device ABI 1.0", "opcodes": 41, "services": 22,
                "registers": 16, "word_bits": 1048576, "stack_words": 256, "memory_bytes": 4096,
                "arith_modes": ["WRAP", "CHECKED", "SATURATE", "TRAPPING"], "image_version": 10},
        "guest_dialect": "LCTLC/1.2 (columned-lctl/4.3, Python compiler tools/lctl430.py)",
        "guest_ext": ".lctlc",
        "image_format": "BRIM/1 (80-byte header + 16-byte instructions, tag BRLCTL43, optional Ed25519 signature)",
        "runtime_contract": "BR/1.1 ISA (marker 21), ABI 2, Device ABI 1.0, LCTLC/1.2 source, BRIM/1 images; freestanding C11 core behind a HAL",
        "result_register": "R2",
        "domain": "D_BR", "group": "G_BR11", "worker": "W_LARGE", "failure_domain": "FD_LARGE",
        "ram_per_vm_bytes": 40000000,
        "cli_step_budget": 4096,
        "requirements": ["C11 compiler (cc/gcc/clang)", "GNU make", "OpenSSL 3 libcrypto headers+lib (-lcrypto)", "Python 3 (compiler tools/lctl430.py, verifier tools/brim_verify.py)", "POSIX sh"],
        "label": "CLASSICAL_NATIVE_VM_BOUNDED_EXECUTION",
    },
    "N_XLARGE": {
        "container": "DF_Xtra_Large",
        "vm_dirname": "QUORUM_LCTL_MSSL_2.1.0",
        "vm_name": "QUORUM Generic VM 5.0.0-candidate (hosted Python reference VM, ISA 1 / ABI 2) inside the QUORUM LCTL/MSSL L4-L9 Open 2.1.0 candidate",
        "target_id": "quorum-vm-5.0.0-candidate-local",
        "lineage": "QUORUM_VM",
        "isa": {"name": "QVM ISA 1 / ABI 2 (semantic world ABI 3)", "opcodes": 24, "services": "0-3 legacy + 16-47 world",
                "registers": 16, "word_bits": 1048576, "stack_words": 256, "memory_bytes": 4096,
                "arith_modes": ["modular", "checked", "saturating", "exact"], "image_version": "QBRIM 2"},
        "guest_dialect": "LCTLC/1.0 (COLUMNED LCTL QVM Profile 1; vm-lane REG rows)",
        "guest_ext": ".lctlc",
        "image_format": "QBRIM 2 (canonical JSON payload + Ed25519 signature; key_id trust store; rollback floor)",
        "runtime_contract": "QVM ISA 1 (24 opcodes) / ABI 2; PC + 16 registers up to 1,048,576 bits; 4096-byte memory; 256-word stack; QBRIM 2 signed images; step budget default 1,000,000; program <= 65,535 instructions",
        "result_register": "R0",
        "domain": "D_QVM", "group": "G_QVM1", "worker": "W_XLARGE", "failure_domain": "FD_XLARGE",
        "ram_per_vm_bytes": 60000000,
        "cli_step_budget": 1000000,
        "requirements": ["Python 3.10+", "Java 21 runtime (bundled LCTL 1.6.1-RC1 column verifier; without it compile runs with --skip-lctl-verify and the result says so)", "cryptography (optional; pure-Python Ed25519 fallback ships)", "POSIX sh"],
        "label": "CLASSICAL_HOSTED_VM_BOUNDED_EXECUTION",
    },
}

#: Feature classes common to every node (adapter spec s3 vocabulary).
_COMMON_FEATURES = {
    "row_sequence_witness": "SUPPORTED_WITH_LIMITS",
    "segmented_row_sequence_witness": "SUPPORTED",
    "bounded_termination": "SUPPORTED",
    "deterministic_replay": "SUPPORTED",
    "integer_arithmetic": "SUPPORTED",
    "declared_step_budget": "SUPPORTED",
    "wide_word_arithmetic_1048576": "SUPPORTED",
    "image_signing": "SUPPORTED",
    "classical_face_execution": "UNSUPPORTED",
    "quantum_face_execution": "UNSUPPORTED",
}


class NodeAdapter:
    """Base of the four adapters. Subclasses implement `_execute_source`."""

    node_id: str = ""
    trust_domain = "LOCAL_TRUSTED"
    authority = "local-operator"
    physical_qpu = False
    physical_parallel = False
    physical_distributed = False
    ADAPTER_VERSION = ADAPTER_VERSION

    #: node-specific feature-class overrides
    FEATURES: Dict[str, str] = {}

    def __init__(self, root: Optional[str] = None, build_dir: Optional[str] = None):
        self.spec = NODE_SPECS[self.node_id]
        self.EXECUTION_LABEL = self.spec["label"]
        self.target_id = self.spec["target_id"]
        assert_not_physical(self)
        env_key = f"DF_{self.node_id[2:]}_ROOT"
        candidate = root or os.environ.get(env_key) or self.default_root()
        self.root = os.path.abspath(candidate) if candidate else None
        if not self.root or not os.path.isdir(self.root):
            raise AdapterRefusal(
                f"no {self.spec['vm_name']} package root found. Pass root=..., set "
                f"{env_key}, or place the DF container beside its vm/ payload. The "
                f"adapter will not fabricate a target.",
                {"env": env_key, "root": self.root, "node_id": self.node_id})
        self.build_dir = build_dir
        self._last: Optional[Dict[str, Any]] = None
        self._check_toolchain()

    # -- location ------------------------------------------------------------
    @classmethod
    def default_root(cls) -> str:
        spec = NODE_SPECS[cls.node_id]
        return os.path.join(container_root_of(__file__), "vm", spec["vm_dirname"])

    def _check_toolchain(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _run(self, args: Sequence[str], cwd: Optional[str] = None,
             timeout: int = SUBPROCESS_TIMEOUT_S) -> subprocess.CompletedProcess:
        return subprocess.run(list(args), cwd=cwd or self.root, capture_output=True,
                              text=True, timeout=timeout)

    # -- s2.1 identity and authentication ------------------------------------
    def toolchain_digests(self) -> Dict[str, str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def target_self_report(self) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def attest(self) -> Dict[str, Any]:
        """s2.1 -- the full evidence bundle, or the reason it cannot be given."""
        return {
            "schema": "DF/ADAPTER_ATTESTATION/1",
            "df_release": DF_RELEASE,
            "adapter_abi": ADAPTER_ABI,
            "adapter_version": self.ADAPTER_VERSION,
            "node_id": self.node_id,
            "target_id": self.target_id,
            "target_class": "CLASSICAL_LOCAL_VM",
            "vm_name": self.spec["vm_name"],
            "authority": self.authority,
            "trust_domain": self.trust_domain,
            "execution_label": self.EXECUTION_LABEL,
            "runtime_contract": self.spec["runtime_contract"],
            "guest_dialect": self.spec["guest_dialect"],
            "image_format": self.spec["image_format"],
            "root": self.root,
            "toolchain": self.toolchain_digests(),
            # Restated from the target's own package, never asserted by us.
            "target_self_reported": self.target_self_report(),
            "physical_release_outputs": {n: BLOCKED_EXTERNAL_AUTHORITY for n in PHYSICAL_RELEASE_OUTPUTS},
            "physical_qpu": False, "physical_parallel": False, "physical_distributed": False,
            "note": ("This attestation covers a classical virtual machine bound as a "
                     "fabric node. It is not, and cannot be upgraded into, attestation "
                     "of a physical quantum target."),
        }

    # -- s2.2 capability description -----------------------------------------
    def native_gate_set(self) -> frozenset:
        """Empty. None of the four machines realizes a quantum gate."""
        return frozenset()

    def feature_class(self, feature: str) -> str:
        table = dict(_COMMON_FEATURES)
        table.update(self.FEATURES)
        return table.get(feature, "UNSUPPORTED")

    def feature_table(self) -> Dict[str, str]:
        table = dict(_COMMON_FEATURES)
        table.update(self.FEATURES)
        for q in ("CX", "H", "MEASURE", "TELEPORT", "PREP0", "ENTANGLE_LINK"):
            table[q] = "UNSUPPORTED"
        return dict(sorted(table.items()))

    def limits(self) -> Dict[str, Any]:
        n = self.node_id
        return {
            "max_rows_per_lowering": W.MAX_ROWS[n],
            "instruction_ceiling": (W.QVM_MAX_INSTRUCTIONS if n == "N_XLARGE" else W.BR_MAX_INSTRUCTIONS),
            "instructions_per_row": W.INSTRUCTIONS_PER_ROW,
            "prologue_epilogue": W.PROLOGUE_EPILOGUE,
            "segmentation": ("a bundle longer than max_rows_per_lowering is executed as a chain "
                             "of segments; each segment starts from the previous accumulator"),
            "qubits": 0,
            "shots": 1,
            "cli_step_budget": self.spec["cli_step_budget"],
            "ram_per_live_vm_bytes_measured": self.spec["ram_per_vm_bytes"],
        }

    def topology(self) -> Dict[str, Any]:
        """One classical node, no quantum capacity. Deliberately not a
        `planner.Topology` (a quantum device graph for a machine with no
        qubits would be the misrepresentation the firewall exists to prevent)."""
        isa = self.spec["isa"]
        return {
            "schema": "DF/ADAPTER_TOPOLOGY/1",
            "nodes": [{
                "node_id": self.node_id,
                "kind": "classical_vm",
                "lineage": self.spec["lineage"],
                "supported_ops": [],
                "quantum_capacity": 0,
                "registers": isa["registers"],
                "word_bits": isa["word_bits"],
                "opcodes": isa["opcodes"],
                "memory_bytes": isa["memory_bytes"],
                "domain": self.spec["domain"], "group": self.spec["group"],
                "worker": self.spec["worker"], "failure_domain": self.spec["failure_domain"],
            }],
            "crosstalk_pairs": [],
            "quantum_links": [],
        }

    def calibration(self) -> Dict[str, Any]:
        return {
            "schema": "DF/ADAPTER_CALIBRATION/1",
            "applicable": False,
            "reason": ("the target is a deterministic classical VM; it has no measured "
                       "physical parameters and therefore no calibration epoch or validity window"),
            "frozen_runtime_contract": self.spec["runtime_contract"],
        }

    # -- execution -----------------------------------------------------------
    def _execute_source(self, src_text: str, unit_id: str, workdir: str,
                        max_steps: Optional[int] = None) -> Dict[str, Any]:  # pragma: no cover
        """Compile/verify/sign/run one guest source. Returns a dict with at
        least: result_low64 (int), status (str), steps (list of step records),
        image_sha256, signed_image_sha256, source_sha256, halted (bool)."""
        raise NotImplementedError

    def submit_words(self, words: Sequence[int], acc_in: int = W.FNV_OFFSET,
                     unit_id: str = "pa.lctl.witness", workdir: Optional[str] = None,
                     keep_workdir: bool = False) -> Dict[str, Any]:
        """Execute one witness segment natively. Fails closed on any bound."""
        if (not isinstance(unit_id, str) or len(unit_id) > 128
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", unit_id) is None
                or unit_id.endswith('.')
                or unit_id.split('.')[0].upper() in
                {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                 *(f'LPT{i}' for i in range(1, 10))}):
            raise AdapterRefusal('unit_id must be a safe portable filename component')
        try:
            rec = W.lowering_record(self.node_id, words, acc_in, unit_id)
        except W.LoweringRefused as exc:
            raise AdapterRefusal(exc.reason, exc.detail) from None
        work = workdir or tempfile.mkdtemp(prefix=f"df-{self.node_id.lower()}-")
        t0 = time.perf_counter()
        try:
            ex = self._execute_source(rec["source"], unit_id, work,
                                      max_steps=rec["instructions"] + 8)
        finally:
            if not keep_workdir and workdir is None:
                shutil.rmtree(work, ignore_errors=True)
        native = ex["result_low64"] & MASK64
        agree = (native == rec["reference_witness"])
        out = {
            "schema": "DF/NODE_SEGMENT_RESULT/1",
            "node_id": self.node_id,
            "label": self.EXECUTION_LABEL,
            "target_id": self.target_id,
            "trust_domain": self.trust_domain,
            "rows": len(words),
            "acc_in": acc_in & MASK64,
            "instructions": rec["instructions"],
            "lowering": {k: v for k, v in rec.items() if k != "source"},
            "native_witness": native,
            "reference_witness": rec["reference_witness"],
            "differential_agreement": agree,
            "halted": ex["halted"],
            "status": ex["status"],
            "image_sha256": ex.get("image_sha256"),
            "signed_image_sha256": ex.get("signed_image_sha256"),
            "steps": ex["steps"],
            "extra": ex.get("extra", {}),
            "wall_s": round(time.perf_counter() - t0, 4),
        }
        if not agree:
            raise AdapterRefusal(
                "native VM witness disagrees with the CPython reference witness; the "
                "lowering or one of the two implementations is wrong and no result is reported",
                {"node_id": self.node_id, "native": native, "reference": rec["reference_witness"],
                 "steps": ex["steps"]})
        self._last = out
        return out

    def submit(self, program, qcir_p2: Optional[Dict[str, Any]] = None,
               shots: int = 1, seed: int = 0) -> Dict[str, Any]:
        """Whole-bundle witness (adapter spec s2.3), one segment. Refuses a
        bundle longer than the node's bound -- use the fabric for chaining."""
        if qcir_p2 is not None and qcir_p2.get("schema") != "PA-LCTL/QCIR-P2/1":
            raise AdapterRefusal(f"QCIR-P2 schema {qcir_p2.get('schema')!r} is not 'PA-LCTL/QCIR-P2/1'",
                                 {"schema": qcir_p2.get("schema")})
        if shots != 1:
            raise AdapterRefusal(
                "the row-sequence witness is deterministic; shots > 1 would report repeated "
                "identical results as if they were samples", {"shots": shots, "supported_shots": 1})
        words = W.words_of(program)
        res = self.submit_words(words, W.FNV_OFFSET, "pa.lctl.witness")
        res = dict(res)
        res.update({
            "schema": "DF/ADAPTER_RESULT/1",
            "program_seal": program.seal(),
            "shots": 1, "seed": seed,
            "feature_class": self.feature_class("row_sequence_witness"),
            "limits": self.limits(),
            "qcir_p2_hash": (hashlib.sha256(json.dumps(qcir_p2, sort_keys=True).encode()).hexdigest()
                             if qcir_p2 is not None else None),
        })
        self._last = res
        return res

    def result(self) -> Dict[str, Any]:
        if self._last is None:
            raise AdapterRefusal("no execution has been submitted", {})
        return self._last

    def provenance(self, program=None) -> Dict[str, Any]:
        """`ledgers.PROVENANCE_FIELDS`-shaped. Every physical flag is False."""
        last = self._last or {}
        return {
            "language": "PA-LCTL",
            "profile": getattr(program, "profile", None) if program else None,
            "execution_class": self.EXECUTION_LABEL,
            "parallel_state": "NOT_CLAIMED",
            "distributed_state": "NOT_CLAIMED",
            "quantum_boundary": "NOT_CROSSED",
            "target": self.target_id,
            "target_verified": bool(last.get("differential_agreement")),
            "target_class": "CLASSICAL_LOCAL_VM",
            "topology_hash": None,
            "schedule_hash": None,
            "source_hash": last.get("program_seal"),
            "qcir_hash": last.get("qcir_p2_hash"),
            "ses_hash": None,
            "proof_ledger_hash": last.get("signed_image_sha256"),
            "physical_qpu": False,
            "physical_parallel": False,
            "physical_distributed": False,
        }

    # -- native guest programs (RUN) -----------------------------------------
    def run_native(self, path: str, max_steps: Optional[int] = None,
                   keep_workdir: bool = False) -> Dict[str, Any]:
        """Execute a program written in this node's own guest dialect."""
        src = open(path, encoding="utf-8").read()
        work = tempfile.mkdtemp(prefix=f"df-{self.node_id.lower()}-native-")
        t0 = time.perf_counter()
        try:
            ex = self._execute_source(src, os.path.splitext(os.path.basename(path))[0], work,
                                      max_steps=max_steps)
        finally:
            if not keep_workdir:
                shutil.rmtree(work, ignore_errors=True)
        return {
            "schema": "DF/NODE_NATIVE_RESULT/1",
            "node_id": self.node_id, "label": self.EXECUTION_LABEL,
            "target_id": self.target_id, "trust_domain": self.trust_domain,
            "source": os.path.abspath(path), "source_sha256": sha256_text(src),
            "guest_dialect": self.spec["guest_dialect"],
            "result_register": self.spec["result_register"],
            "result_low64": ex["result_low64"] & MASK64,
            "halted": ex["halted"], "status": ex["status"],
            "image_sha256": ex.get("image_sha256"), "signed_image_sha256": ex.get("signed_image_sha256"),
            "steps": ex["steps"], "extra": ex.get("extra", {}),
            "wall_s": round(time.perf_counter() - t0, 4),
        }

    # -- helpers ---------------------------------------------------------------
    def _step(self, steps: List[Dict[str, Any]], name: str, args: Sequence[str],
              cwd: Optional[str] = None, ok_codes: Sequence[int] = (0,),
              timeout: int = SUBPROCESS_TIMEOUT_S) -> subprocess.CompletedProcess:
        t0 = time.perf_counter()
        p = self._run(args, cwd=cwd, timeout=timeout)
        steps.append({"step": name,
                      "argv": [os.path.basename(str(args[0]))] + [str(a) for a in args[1:]],
                      "returncode": p.returncode,
                      "stdout": p.stdout.strip()[:2000],
                      "stderr": p.stderr.strip()[:2000],
                      "wall_s": round(time.perf_counter() - t0, 4)})
        if p.returncode not in ok_codes:
            raise AdapterRefusal(f"{self.node_id} {name} failed (exit {p.returncode})",
                                 {"node_id": self.node_id, "steps": steps})
        return p


# --------------------------------------------------------------------------
# BOTTLE ROCKET status-line parser (Small and Medium CLIs share it)
# --------------------------------------------------------------------------

def parse_br_status(stdout: str) -> Dict[str, Any]:
    """'status=<u> trap=<u> ip=<u> image_version=<u> result=PASS|FAIL' + 'R2.low64=<u64>'."""
    out: Dict[str, Any] = {"status": None, "trap": None, "ip": None,
                           "image_version": None, "result": None, "r2_low64": None}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("status="):
            for tok in line.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    if k in ("status", "trap", "ip", "image_version"):
                        try:
                            out[k] = int(v)
                        except ValueError:
                            out[k] = v
                    elif k == "result":
                        out["result"] = v
        elif line.startswith("R2.low64="):
            out["r2_low64"] = int(line.split("=", 1)[1])
    return out


# --------------------------------------------------------------------------
# N_SMALL
# --------------------------------------------------------------------------

class SmallNodeAdapter(NodeAdapter):
    node_id = "N_SMALL"
    FEATURES = {
        "image_signing": "SUPPORTED",
        "independent_image_verification": "UNSUPPORTED",
        "signature_checked_execution": "SUPPORTED",
        "compile_from_source": "SUPPORTED",
        "apdu_serve": "TARGET_SPECIFIC",
    }

    def _check_toolchain(self) -> None:
        self.brctl = os.path.join(self.root, ".build", "brctl")
        if not (os.path.isfile(self.brctl) and os.access(self.brctl, os.X_OK)):
            raise AdapterRefusal(
                f"BOTTLE ROCKET 3.0.0 brctl is not built at {self.brctl}. Run ./BUILD (make) "
                f"before binding the node; the adapter will not substitute an interpreter.",
                {"missing": self.brctl, "node_id": self.node_id})

    def toolchain_digests(self) -> Dict[str, str]:
        return {"brctl_sha256": sha256_file(self.brctl)}

    def target_self_report(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for rel in ("MANIFEST.json", "evidence/OPERATIONAL.json", "evidence/SIZE.json"):
            p = os.path.join(self.root, rel)
            if os.path.isfile(p):
                try:
                    out[rel] = json.load(open(p, encoding="utf-8"))
                except Exception as exc:  # pragma: no cover
                    out[rel] = {"unreadable": str(exc)}
        return out

    def _execute_source(self, src_text: str, unit_id: str, workdir: str,
                        max_steps: Optional[int] = None) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        src = os.path.join(workdir, f"{unit_id}.mssl")
        img = os.path.join(workdir, f"{unit_id}.brimg")
        signed = os.path.join(workdir, f"{unit_id}.signed.brimg")
        priv = os.path.join(workdir, "k.private")
        pub = os.path.join(workdir, "k.public")
        with open(src, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src_text)
        p = self._step(steps, "assemble", [self.brctl, "assemble", src, img])
        if "PASS" not in p.stdout:
            raise AdapterRefusal("MSSL assembly did not report PASS", {"steps": steps})
        self._step(steps, "inspect-image", [self.brctl, "inspect-image", img])
        self._step(steps, "keygen", [self.brctl, "keygen", priv, pub])
        self._step(steps, "sign-image", [self.brctl, "sign-image", img, signed, priv])
        self._step(steps, "verify-image", [self.brctl, "verify-image", signed, pub])
        run = self._step(steps, "run-signed", [self.brctl, "run-signed", signed, pub])
        st = parse_br_status(run.stdout)
        halted = (st["status"] == 2 and st["trap"] == 0 and st["result"] == "PASS")
        if not halted or st["r2_low64"] is None:
            raise AdapterRefusal("BOTTLE ROCKET 3.0.0 did not halt cleanly (or produced no result register)",
                                 {"parsed": st, "steps": steps})
        return {"result_low64": st["r2_low64"], "status": st, "halted": halted, "steps": steps,
                "image_sha256": sha256_file(img), "signed_image_sha256": sha256_file(signed),
                "source_sha256": sha256_text(src_text)}


# --------------------------------------------------------------------------
# N_MEDIUM
# --------------------------------------------------------------------------

class MediumNodeAdapter(NodeAdapter):
    node_id = "N_MEDIUM"
    FEATURES = {
        "image_signing": "SUPPORTED",
        "independent_image_verification": "SUPPORTED",
        "signature_checked_execution": "SUPPORTED",
        "compile_from_source": "SUPPORTED",
        "native_semantic_verifier": "SUPPORTED",
        "pacore_bottlerocket_adapter_compatible": "SUPPORTED",
    }

    def _check_toolchain(self) -> None:
        self.bradmin = os.path.join(self.root, ".build", "bradmin")
        self.brverify = os.path.join(self.root, ".build", "brverify")
        for name, path in (("bradmin", self.bradmin), ("brverify", self.brverify)):
            if not (os.path.isfile(path) and os.access(path, os.X_OK)):
                raise AdapterRefusal(
                    f"BOTTLE ROCKET 5.0.0 {name} is not built at {path}. Run ./BUILD (make) before "
                    f"binding the node; the adapter will not substitute an interpreter.",
                    {"missing": path, "node_id": self.node_id})

    def toolchain_digests(self) -> Dict[str, str]:
        return {"bradmin_sha256": sha256_file(self.bradmin),
                "brverify_sha256": sha256_file(self.brverify)}

    def target_self_report(self) -> Dict[str, Any]:
        p = os.path.join(self.root, "PACKAGE_INFO.json")
        if not os.path.isfile(p):
            raise AdapterRefusal("toolchain root has no PACKAGE_INFO.json; the target cannot be identified",
                                 {"root": self.root})
        info = json.load(open(p, encoding="utf-8"))
        return {"PACKAGE_INFO.json": info,
                "strict_gate": info.get("strict_gate"),
                "operational_requirements": info.get("operational_requirements"),
                "blocked_requirements": info.get("blocked_requirements"),
                "blockers": info.get("blockers", [])}

    def _execute_source(self, src_text: str, unit_id: str, workdir: str,
                        max_steps: Optional[int] = None) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        src = os.path.join(workdir, f"{unit_id}.lctlc")
        brir = os.path.join(workdir, f"{unit_id}.brir")
        img = os.path.join(workdir, f"{unit_id}.brimg")
        signed = os.path.join(workdir, f"{unit_id}.signed.brimg")
        priv = os.path.join(workdir, "k.private")
        pub = os.path.join(workdir, "k.public")
        with open(src, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src_text)
        self._step(steps, "verify-lctlc", [self.bradmin, "verify-lctlc", src])
        self._step(steps, "lctl-to-brir", [self.bradmin, "lctl-to-brir", src, brir])
        self._step(steps, "compile-lctlc", [self.bradmin, "compile-lctlc", src, img])
        # The independent verifier is a separate C program that re-derives the
        # image from source + BRIR without the compiler's data structures.
        self._step(steps, "independent-brim-verify", [self.brverify, img, src, brir])
        self._step(steps, "keygen", [self.bradmin, "keygen", priv, pub])
        self._step(steps, "sign-image", [self.bradmin, "sign-image", img, signed, priv])
        self._step(steps, "verify-image", [self.bradmin, "verify-image", signed, pub])
        run = self._step(steps, "run-signed", [self.bradmin, "run-signed", signed, pub])
        st = parse_br_status(run.stdout)
        halted = (st["status"] == 2 and st["trap"] == 0 and st["result"] == "PASS")
        if not halted or st["r2_low64"] is None:
            raise AdapterRefusal("BOTTLE ROCKET 5.0.0 did not halt cleanly (or produced no result register)",
                                 {"parsed": st, "steps": steps})
        return {"result_low64": st["r2_low64"], "status": st, "halted": halted, "steps": steps,
                "image_sha256": sha256_file(img), "signed_image_sha256": sha256_file(signed),
                "source_sha256": sha256_text(src_text)}


# --------------------------------------------------------------------------
# N_LARGE
# --------------------------------------------------------------------------

class LargeNodeAdapter(NodeAdapter):
    node_id = "N_LARGE"
    FEATURES = {
        "image_signing": "SUPPORTED_WITH_LIMITS",
        "independent_image_verification": "SUPPORTED",
        "signature_checked_execution": "SUPPORTED_WITH_LIMITS",
        "compile_from_source": "SUPPORTED",
        "production_trust_chain_execution": "UNSUPPORTED",
    }

    def _check_toolchain(self) -> None:
        self.brctl_dev = os.path.join(self.root, ".build", "brctl-dev")
        self.lctl430 = os.path.join(self.root, "tools", "lctl430.py")
        self.brim_verify = os.path.join(self.root, "tools", "brim_verify.py")
        if not (os.path.isfile(self.brctl_dev) and os.access(self.brctl_dev, os.X_OK)):
            raise AdapterRefusal(
                f"BOTTLE ROCKET 4.7.0 brctl-dev is not built at {self.brctl_dev}. Run ./BUILD "
                f"(make brctl-dev) before binding the node; the production brctl refuses "
                f"unprovisioned images by design and the adapter will not substitute an interpreter.",
                {"missing": self.brctl_dev, "node_id": self.node_id})
        for p in (self.lctl430, self.brim_verify):
            if not os.path.isfile(p):
                raise AdapterRefusal(f"missing tool {p}", {"missing": p, "node_id": self.node_id})

    def toolchain_digests(self) -> Dict[str, str]:
        return {"brctl_dev_sha256": sha256_file(self.brctl_dev),
                "lctl430_py_sha256": sha256_file(self.lctl430),
                "brim_verify_py_sha256": sha256_file(self.brim_verify)}

    def target_self_report(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for rel in ("evidence/QUALIFICATION_470.json", "evidence/qualification_470.json",
                    "evidence/RELEASE_470.json"):
            p = os.path.join(self.root, rel)
            if os.path.isfile(p):
                try:
                    out[rel] = json.load(open(p, encoding="utf-8"))
                except Exception as exc:  # pragma: no cover
                    out[rel] = {"unreadable": str(exc)}
        out["production_cli_refuses_unprovisioned_images"] = True
        out["fabric_execution_binary"] = ".build/brctl-dev (-DBR_DEVELOPMENT=1)"
        return out

    def _execute_source(self, src_text: str, unit_id: str, workdir: str,
                        max_steps: Optional[int] = None) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        src = os.path.join(workdir, f"{unit_id}.lctlc")
        img = os.path.join(workdir, f"{unit_id}.brimg")
        brir = os.path.join(workdir, f"{unit_id}.brir.json")
        manifest = os.path.join(workdir, f"{unit_id}.manifest.json")
        signed = os.path.join(workdir, f"{unit_id}.signed.brimg")
        priv = os.path.join(workdir, "k.private")
        pub = os.path.join(workdir, "k.public")
        with open(src, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src_text)
        py = sys.executable or "python3"
        self._step(steps, "lctl430-check", [py, self.lctl430, "check", src, "--executable"])
        self._step(steps, "lctl430-compile", [py, self.lctl430, "compile", src, img, "--factory",
                                              "--brir-out", brir, "--manifest", manifest])
        # Independent verifier: "intentionally does not import compiler code".
        self._step(steps, "independent-brim-verify", [py, self.brim_verify, img, "--manifest", manifest])
        self._step(steps, "keygen", [self.brctl_dev, "keygen", priv, pub])
        self._step(steps, "sign-image", [self.brctl_dev, "sign-image", img, signed, priv])
        self._step(steps, "verify-image", [self.brctl_dev, "verify-image", signed, pub])
        # The 4.7.0 dev CLI's run-signed reports status/trap but not R2; the
        # unsigned twin (byte-identical payload) is run for the register
        # read-back and both are recorded. Verified: signed == image + 64 bytes.
        rs = self._step(steps, "run-signed", [self.brctl_dev, "run-signed", signed, pub])
        run = self._step(steps, "run", [self.brctl_dev, "run", img])
        with open(img, "rb") as fh:
            ib = fh.read()
        with open(signed, "rb") as fh:
            sb = fh.read()
        # BRIM/1 header byte 7 is the flags byte; sign-image sets bit 1
        # (SIGNED) and appends the 64-byte Ed25519 signature. Everything else,
        # including the sealed payload, must be identical.
        same_payload = (len(sb) == len(ib) + 64 and sb[:7] == ib[:7]
                        and sb[7] == (ib[7] | 2) and sb[8:len(ib)] == ib[8:])
        if not same_payload:
            raise AdapterRefusal("signed image is not the unsigned image (flags|=SIGNED) plus a "
                                 "64-byte signature; the register read-back would not describe "
                                 "the signature-checked run", {"steps": steps})
        try:
            js = json.loads(rs.stdout.strip().splitlines()[-1])
            j = json.loads(run.stdout.strip().splitlines()[-1])
        except Exception:
            raise AdapterRefusal("BOTTLE ROCKET 4.7.0 produced no JSON status", {"steps": steps})
        halted = (j.get("status") == 2 and j.get("trap") == 0 and js.get("status") == 2 and js.get("trap") == 0)
        if not halted or "R2" not in j:
            raise AdapterRefusal("BOTTLE ROCKET 4.7.0 did not halt cleanly (or produced no result register)",
                                 {"run": j, "run_signed": js, "steps": steps})
        return {"result_low64": int(j["R2"]), "status": {"run": j, "run_signed": js}, "halted": halted,
                "steps": steps, "image_sha256": sha256_file(img), "signed_image_sha256": sha256_file(signed),
                "source_sha256": sha256_text(src_text)}


# --------------------------------------------------------------------------
# N_XLARGE
# --------------------------------------------------------------------------

class XLargeNodeAdapter(NodeAdapter):
    node_id = "N_XLARGE"
    FEATURES = {
        "image_signing": "SUPPORTED",
        "independent_image_verification": "UNSUPPORTED",
        "signature_checked_execution": "SUPPORTED",
        "compile_from_source": "SUPPORTED",
        "external_column_verifier_jvm": "SUPPORTED_WITH_LIMITS",
        "trust_store_rollback_floor": "SUPPORTED",
        "state_hash_trace": "SUPPORTED",
    }

    def _check_toolchain(self) -> None:
        self.vm_root = os.path.join(self.root, "vm")
        self.qvm = os.path.join(self.vm_root, "toolchain", "quorum_vm.py")
        self.dev_seed = os.path.join(self.vm_root, "keys", "DEV_ONLY_private_seed.hex")
        self.trust_store = os.path.join(self.vm_root, "keys", "TRUST_STORE.json")
        self.lctl_tool = os.path.join(self.root, "toolchain", "lctl_1_6_1_rc1", "START_LCTL_1_6_1.sh")
        for p in (self.qvm, self.dev_seed, self.trust_store):
            if not os.path.isfile(p):
                raise AdapterRefusal(f"QUORUM VM toolchain file missing: {p}",
                                     {"missing": p, "node_id": self.node_id})
        self.java = _which("java")

    def toolchain_digests(self) -> Dict[str, str]:
        d = {"quorum_vm_py_sha256": sha256_file(self.qvm),
             "trust_store_sha256": sha256_file(self.trust_store)}
        jar = os.path.join(self.root, "toolchain", "lctl_1_6_1_rc1", "runtime", "bin", "lctl-hyperfederated.jar")
        if os.path.isfile(jar):
            d["lctl_verifier_jar_sha256"] = sha256_file(jar)
        d["java_available"] = bool(self.java)
        return d

    def target_self_report(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for rel in ("MANIFEST.json",):
            p = os.path.join(self.root, rel)
            if os.path.isfile(p):
                try:
                    m = json.load(open(p, encoding="utf-8"))
                    out[rel] = {k: m[k] for k in list(m)[:40] if not isinstance(m[k], (list, dict)) or k in ("gates", "vm_extension")}
                except Exception as exc:  # pragma: no cover
                    out[rel] = {"unreadable": str(exc)}
        for rel in ("vm/evidence/qualification_summary.json",):
            p = os.path.join(self.root, rel)
            if os.path.isfile(p):
                try:
                    out[rel] = json.load(open(p, encoding="utf-8"))
                except Exception as exc:  # pragma: no cover
                    out[rel] = {"unreadable": str(exc)}
        return out

    def _execute_source(self, src_text: str, unit_id: str, workdir: str,
                        max_steps: Optional[int] = None) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        src = os.path.join(workdir, f"{unit_id}.lctlc")
        payload = os.path.join(workdir, f"{unit_id}.payload.json")
        brir = os.path.join(workdir, f"{unit_id}.brir.json")
        signed = os.path.join(workdir, f"{unit_id}.signed.brimg")
        snap = os.path.join(workdir, f"{unit_id}.snapshot.json")
        with open(src, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src_text)
        py = sys.executable or "python3"
        compile_args = [py, self.qvm, "compile", src, payload, "--brir", brir]
        column_verify = "JVM"
        if not self.java or not os.path.isfile(self.lctl_tool):
            compile_args.append("--skip-lctl-verify")
            column_verify = "SKIPPED_NO_JDK" if not self.java else "SKIPPED_NO_VERIFIER"
        self._step(steps, "compile", compile_args)
        self._step(steps, "sign", [py, self.qvm, "sign", payload, "--key", self.dev_seed,
                                   "--key-id", "dev-root", "--out", signed])
        self._step(steps, "verify", [py, self.qvm, "verify", signed, "--trust", self.trust_store])
        budget = int(max_steps or self.spec["cli_step_budget"])
        self._step(steps, "run", [py, self.qvm, "run", signed, "--trust", self.trust_store,
                                  "--max-steps", str(budget), "--snapshot", snap])
        s = json.load(open(snap, encoding="utf-8"))
        halted = (s.get("status") == "HALTED")
        if not halted:
            raise AdapterRefusal("QUORUM VM did not halt cleanly", {"snapshot_status": s.get("status"), "steps": steps})
        r0 = int(s["registers_hex"][0], 16)
        r2 = int(s["registers_hex"][2], 16)
        return {"result_low64": r0 & MASK64,
                "status": {"status": s.get("status"), "pc": s.get("pc"), "steps": s.get("steps"),
                           "state_sha256": s.get("state_sha256"), "r0_low64": r0 & MASK64,
                           "r2_low64": r2 & MASK64, "max_steps": budget},
                "halted": halted, "steps": steps,
                "image_sha256": sha256_file(payload), "signed_image_sha256": sha256_file(signed),
                "source_sha256": sha256_text(src_text),
                "extra": {"lctl_column_verify": column_verify,
                          "output_b64": s.get("output_b64", "")}}


ADAPTERS = {
    "N_SMALL": SmallNodeAdapter,
    "N_MEDIUM": MediumNodeAdapter,
    "N_LARGE": LargeNodeAdapter,
    "N_XLARGE": XLargeNodeAdapter,
}


def make_adapter(node_id: str, root: Optional[str] = None) -> NodeAdapter:
    return ADAPTERS[node_id](root=root)
