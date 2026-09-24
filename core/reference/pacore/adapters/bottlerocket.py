"""BOTTLE ROCKET 5.0.0 classical execution adapter for PA-LCTL.

This is the first implementation of the `PA_LCTL_TARGET_ADAPTER_SPEC.md` s2
ABI in this package. It binds a **local, offline, deterministic classical
virtual machine** -- not a quantum target.

What it changes about the release's claims
------------------------------------------

Before PA21.3 every executed check in this release ran inside the same CPython
process as the code being checked. This adapter adds an independent native
execution path: PA-LCTL source -> LCTLC/1.1 -> a semantically verified,
compiled, Ed25519-signed BRIM image -> execution on a C virtual machine whose
own conformance suites (ISA/ABI 32 opcodes + 20 services, 59 wide-state
requirements, 66 device-I/O requirements) pass independently.

What it does NOT change
-----------------------

`PHYSICAL_QPU_EXECUTION`, `PHYSICAL_PARALLEL_QPU_EXECUTION` and
`PHYSICAL_DISTRIBUTED_QPU_EXECUTION` remain `BLOCKED_EXTERNAL_AUTHORITY`.
BOTTLE ROCKET is classical; binding it cannot and does not lift a physical
quantum blocker. Concretely, and enforced in code:

* `trust_domain` is `LOCAL_TRUSTED`, never `PHYSICAL_TARGET_AUTHENTICATED`;
* `native_gate_set()` is **empty** -- the VM realizes no quantum gate;
* every quantum feature classifies `UNSUPPORTED`;
* `provenance()` reports `physical_qpu`, `physical_parallel` and
  `physical_distributed` all False;
* `adapters.assert_not_physical()` runs in the constructor.

The adapter fails closed (adapter spec s2.4). Every method either returns a
complete, evidenced answer or raises `AdapterRefusal` with a stated reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence

from . import AdapterRefusal, ADAPTER_ABI, assert_not_physical
from . import brlower

TARGET_ID = "bottle-rocket-5.0.0-local"
ADAPTER_VERSION = "1.0.0"

#: Truthful label for what this adapter does. Begins CLASSICAL_ and contains
#: none of `simulator._FORBIDDEN_LABEL_TOKENS`; it is NOT a member of
#: `simulator.EXECUTION_LABELS` because this is not a simulation of anything.
EXECUTION_LABEL = "CLASSICAL_NATIVE_VM_BOUNDED_EXECUTION"

#: The frozen runtime contract this adapter is qualified against.
RUNTIME_CONTRACT = ("LCTLC/1.1 -> BRIR/1.1 -> BRIM/1+BRPV/1 -> BRTM/1; "
                    "ISA 4.1; ABI 1.0; Device ABI 1.0; BRCR/1; BRGD/1; "
                    "core ABI 0x00040700")

_ENV_ROOT = "PA_LCTL_BOTTLE_ROCKET_ROOT"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BottleRocketAdapter:
    """Adapter ABI s2 over a local BOTTLE ROCKET toolchain."""

    trust_domain = "LOCAL_TRUSTED"
    target_id = TARGET_ID
    authority = "local-operator"
    EXECUTION_LABEL = EXECUTION_LABEL

    # -- s2.1 identity and authentication ---------------------------------
    def __init__(self, root: Optional[str] = None):
        assert_not_physical(self)
        self.root = os.path.abspath(
            root or os.environ.get(_ENV_ROOT, "")) if (
                root or os.environ.get(_ENV_ROOT)) else None
        if not self.root or not os.path.isdir(self.root):
            raise AdapterRefusal(
                "no BOTTLE ROCKET toolchain root supplied. Pass root=... or "
                f"set {_ENV_ROOT}. The adapter will not fabricate a target.",
                {"env": _ENV_ROOT, "root": self.root})
        self.bradmin = os.path.join(self.root, ".build", "bradmin")
        self.brverify = os.path.join(self.root, ".build", "brverify")
        for name, path in (("bradmin", self.bradmin),
                           ("brverify", self.brverify)):
            if not os.path.isfile(path) or not os.access(path, os.X_OK):
                raise AdapterRefusal(
                    f"BOTTLE ROCKET {name} is not built at {path}. Build the "
                    f"toolchain before binding the target; the adapter will "
                    f"not substitute an interpreter.",
                    {"missing": path})
        self._last: Optional[Dict[str, Any]] = None

    def _run(self, args: Sequence[str], cwd: Optional[str] = None
             ) -> subprocess.CompletedProcess:
        return subprocess.run(list(args), cwd=cwd or self.root,
                              capture_output=True, text=True, timeout=300)

    def attest(self) -> Dict[str, Any]:
        """s2.1 -- the full evidence bundle, or the reason it cannot be given."""
        pkg = os.path.join(self.root, "PACKAGE_INFO.json")
        if not os.path.isfile(pkg):
            raise AdapterRefusal(
                "toolchain root has no PACKAGE_INFO.json; the target cannot "
                "be identified", {"root": self.root})
        with open(pkg, encoding="utf-8") as fh:
            info = json.load(fh)
        return {
            "schema": "PA-LCTL/ADAPTER_ATTESTATION/1",
            "adapter_abi": ADAPTER_ABI,
            "adapter_version": ADAPTER_VERSION,
            "target_id": self.target_id,
            "target_class": "CLASSICAL_LOCAL_VM",
            "authority": self.authority,
            "trust_domain": self.trust_domain,
            "runtime_contract": RUNTIME_CONTRACT,
            "toolchain": {
                "bradmin_sha256": _sha256_file(self.bradmin),
                "brverify_sha256": _sha256_file(self.brverify),
                "package": info.get("package"),
                "release": info.get("release"),
                "distribution": info.get("distribution"),
            },
            # Restated from the target's own package info, not asserted by us.
            "target_self_reported_gate": {
                "strict_gate": info.get("strict_gate"),
                "operational_requirements": info.get(
                    "operational_requirements"),
                "blocked_requirements": info.get("blocked_requirements"),
                "blockers": info.get("blockers", []),
            },
            "physical_quantum_execution": "BLOCKED_EXTERNAL_AUTHORITY",
            "note": (
                "This attestation covers a classical virtual machine. It is "
                "not, and cannot be upgraded into, attestation of a physical "
                "quantum target."),
        }

    # -- s2.2 capability description --------------------------------------
    def native_gate_set(self) -> frozenset:
        """Empty. The VM realizes no quantum gate. This is not a limitation
        to be worked around; it is what the target is."""
        return frozenset()

    def feature_class(self, feature: str) -> str:
        """s3 -- every feature classified; unstated features are UNSUPPORTED."""
        classical = {
            "row_sequence_witness": "SUPPORTED_WITH_LIMITS",
            "bounded_termination": "SUPPORTED",
            "deterministic_replay": "SUPPORTED",
            "integer_arithmetic": "SUPPORTED",
            "declared_step_budget": "SUPPORTED",
            "image_signing": "SUPPORTED",
            "independent_image_verification": "SUPPORTED",
        }
        if feature in classical:
            return classical[feature]
        return "UNSUPPORTED"

    def limits(self) -> Dict[str, Any]:
        """Bounds a caller SHALL record with any SUPPORTED_WITH_LIMITS result."""
        return {
            "max_rows_per_lowering": brlower.MAX_ROWS,
            "instruction_ceiling": brlower.BR_MAX_INSTRUCTIONS,
            "source_line_ceiling": brlower.BR_MAX_SOURCE_LINES,
            "qubits": 0,
            "shots": 1,
            "rationale": ("LCTLC/1.1 admits 256 instructions per unit; the "
                          "witness lowering costs 3 instructions per PA-LCTL "
                          "row plus 4 of prologue/epilogue."),
        }

    def topology(self) -> Dict[str, Any]:
        """One classical node, no quantum capacity. Deliberately not a
        `planner.Topology`: presenting a quantum device graph for a machine
        with no qubits would be the exact misrepresentation the adapter spec
        firewall exists to prevent."""
        return {
            "schema": "PA-LCTL/ADAPTER_TOPOLOGY/1",
            "nodes": [{
                "node_id": "BR0",
                "kind": "classical_vm",
                "supported_ops": [],
                "quantum_capacity": 0,
                "registers": 16,
                "word_bits": 1048576,
            }],
            "crosstalk_pairs": [],
            "quantum_links": [],
        }

    def calibration(self) -> Dict[str, Any]:
        """A deterministic VM has no calibration drift. Saying so is more
        useful than inventing an epoch."""
        return {
            "schema": "PA-LCTL/ADAPTER_CALIBRATION/1",
            "applicable": False,
            "reason": ("the target is a deterministic classical VM; it has no "
                       "measured physical parameters and therefore no "
                       "calibration epoch or validity window"),
            "frozen_runtime_contract": RUNTIME_CONTRACT,
        }

    # -- s2.3 execution ----------------------------------------------------
    def submit(self, program, qcir_p2: Optional[Dict[str, Any]] = None,
               shots: int = 1, seed: int = 0) -> Dict[str, Any]:
        """Lower, verify, compile, independently verify, sign and execute.

        `qcir_p2`, when supplied, SHALL carry `schema == "PA-LCTL/QCIR-P2/1"`
        (adapter spec s2.3). It is recorded in provenance; it is not executed,
        because its quantum content has no realization on this target.
        """
        if qcir_p2 is not None:
            schema = qcir_p2.get("schema")
            if schema != "PA-LCTL/QCIR-P2/1":
                raise AdapterRefusal(
                    f"QCIR-P2 schema {schema!r} is not 'PA-LCTL/QCIR-P2/1'",
                    {"schema": schema})
        if shots != 1:
            raise AdapterRefusal(
                "the row-sequence witness is deterministic; shots > 1 would "
                "report repeated identical results as if they were samples",
                {"shots": shots, "supported_shots": 1})

        try:
            rec = brlower.lowering_record(program)
        except brlower.LoweringRefused as exc:
            raise AdapterRefusal(exc.reason, exc.detail) from None

        work = tempfile.mkdtemp(prefix="pa-br-")
        try:
            src = os.path.join(work, "witness.lctlc")
            img = os.path.join(work, "witness.brimg")
            signed = os.path.join(work, "witness.signed.brimg")
            priv = os.path.join(work, "k.private")
            pub = os.path.join(work, "k.public")
            steps: List[Dict[str, Any]] = []

            with open(src, "w", encoding="utf-8") as fh:
                fh.write(rec["lctlc_source"])

            def step(name: str, args: Sequence[str]) -> subprocess.CompletedProcess:
                p = self._run(args)
                steps.append({"step": name,
                              "argv": [os.path.basename(args[0])] + list(args[1:]),
                              "returncode": p.returncode,
                              "stdout": p.stdout.strip()[:2000],
                              "stderr": p.stderr.strip()[:2000]})
                if p.returncode != 0:
                    raise AdapterRefusal(
                        f"BOTTLE ROCKET {name} failed", {"steps": steps})
                return p

            brir = os.path.join(work, "witness.brir")
            step("verify-lctlc", [self.bradmin, "verify-lctlc", src])
            step("lctl-to-brir", [self.bradmin, "lctl-to-brir", src, brir])
            step("compile-lctlc", [self.bradmin, "compile-lctlc", src, img])
            # The independent verifier is a separate 6.3 KB C program that
            # re-derives the image from source + BRIR without using the
            # compiler's own data structures.
            step("independent-brim-verify", [self.brverify, img, src, brir])
            step("keygen", [self.bradmin, "keygen", priv, pub])
            step("sign-image", [self.bradmin, "sign-image", img, signed, priv])
            step("verify-image", [self.bradmin, "verify-image", signed, pub])
            run = step("run-signed", [self.bradmin, "run-signed", signed, pub])

            native = None
            for line in run.stdout.splitlines():
                if line.startswith("R2.low64="):
                    native = int(line.split("=", 1)[1])
            if native is None:
                raise AdapterRefusal(
                    "native execution produced no witness register",
                    {"steps": steps})

            agree = (native == rec["reference_witness"] & ((1 << 64) - 1))
            self._last = {
                "schema": "PA-LCTL/ADAPTER_RESULT/1",
                "label": EXECUTION_LABEL,
                "target_id": self.target_id,
                "trust_domain": self.trust_domain,
                "feature_class": self.feature_class("row_sequence_witness"),
                "limits": self.limits(),
                "shots": 1,
                "seed": seed,
                "program_seal": rec["program_seal"],
                "lowering": {k: v for k, v in rec.items()
                             if k != "lctlc_source"},
                "lctlc_source_sha256": rec["source_sha256"],
                "image_sha256": _sha256_file(img),
                "signed_image_sha256": _sha256_file(signed),
                "native_witness": native,
                "reference_witness": rec["reference_witness"],
                "differential_agreement": agree,
                "steps": steps,
                "qcir_p2_hash": (
                    hashlib.sha256(
                        json.dumps(qcir_p2, sort_keys=True).encode()
                    ).hexdigest() if qcir_p2 is not None else None),
            }
            if not agree:
                raise AdapterRefusal(
                    "native VM witness disagrees with the CPython reference "
                    "witness; the lowering or one of the two implementations "
                    "is wrong and no result is reported",
                    {"native": native,
                     "reference": rec["reference_witness"]})
            return self._last
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def result(self) -> Dict[str, Any]:
        if self._last is None:
            raise AdapterRefusal("no execution has been submitted", {})
        return self._last

    # -- s2.3 provenance ---------------------------------------------------
    def provenance(self, program=None) -> Dict[str, Any]:
        """`ledgers.PROVENANCE_FIELDS`-shaped. Every physical flag is False."""
        last = self._last or {}
        return {
            "language": "PA-LCTL",
            "profile": getattr(program, "profile", None) if program else None,
            "execution_class": EXECUTION_LABEL,
            "parallel_state": "NOT_CLAIMED",
            "distributed_state": "NOT_CLAIMED",
            "quantum_boundary": "NOT_CROSSED",
            "target": self.target_id,
            # True in the sense the target ABI defines it for THIS target: the
            # image was independently BRIM-verified and Ed25519-verified before
            # execution. It is not, and must not be read as, verification of a
            # physical quantum device.
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
