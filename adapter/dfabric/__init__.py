"""dfabric -- the Distributed Fabric (DF) node/fabric adapters for PA-LCTL.

This package shares an adapter ABI across the DF containers (the four node
containers DF_Small / DF_Medium / DF_Large / DF_Xtra_Large and DF_Fabric). It
implements, for each embedded virtual machine, the PA-LCTL target-adapter ABI
(`PA_LCTL_TARGET_ADAPTER_SPEC.md` s2, `PA-LCTL/TARGET_ADAPTER/1`) and, in
`fabric_runtime.py`, the federation of those adapters under the reference
execution fabric (`pacore.fabric`, `PA_LCTL_FABRIC_SPEC.md`).

Governing rule, inherited from the corpora that define the language:

    No item is operational because its source file exists. Operational status
    requires native executable evidence satisfying that item's promotion gate.

Every adapter here binds a CLASSICAL machine. Nothing in this package claims,
simulates or relabels physical quantum execution; `PHYSICAL_QPU_EXECUTION`,
`PHYSICAL_PARALLEL_QPU_EXECUTION` and `PHYSICAL_DISTRIBUTED_QPU_EXECUTION`
remain `BLOCKED_EXTERNAL_AUTHORITY`. `NETWORK=deny` and `BACKEND=none` are
inherited unchanged: the fabric is a *model of* a federation executed on one
host with local processes; cross-machine federation is `BLOCKED`.
"""

from __future__ import annotations

DF_RELEASE = "DF-PA21.2-1.0.1"
DF_SCHEMA_PREFIX = "DF"
LANGUAGE = "PA-LCTL"
LANGUAGE_MAGIC = "#PA-LCTL/1.6"
CORE_VERSION_EXPECTED = "1.6.0-rc1"
ADAPTER_ABI = "PA-LCTL/TARGET_ADAPTER/1"
LOWERING_SCHEMA = "DF/ROW_WITNESS_LOWERING/1"

#: Trust domains a DF adapter may claim (mirrors pacore.adapters).
PERMITTED_TRUST_DOMAINS = ("LOCAL_TRUSTED", "LOCAL_UNTRUSTED")
#: Tokens that may never appear in an execution label emitted here.
FORBIDDEN_LABEL_TOKENS = ("QPU", "HARDWARE", "PHYSICAL", "DEVICE",
                          "QUANTUM_EXECUTION")
#: The five-value feature-class vocabulary (adapter spec s3).
FEATURE_CLASSES = ("SUPPORTED", "SUPPORTED_WITH_LIMITS", "APPROXIMATE",
                   "TARGET_SPECIFIC", "UNSUPPORTED")
#: Status vocabulary (lang.STATUS_VOCABULARY + gap-ledger values).
STATUS_VOCABULARY = ("BLOCKED", "SPECIFIED", "SCAFFOLDED", "IMPLEMENTED",
                     "VERIFIED", "OPERATIONAL", "QUALIFIED",
                     "IMPLEMENTED_PARTIAL", "BLOCKED_EXTERNAL_AUTHORITY",
                     "BLOCKED_CAPABILITY_ABSENT", "SKIPPED")

PHYSICAL_RELEASE_OUTPUTS = ("PHYSICAL_PARALLEL_QPU_EXECUTION",
                            "PHYSICAL_DISTRIBUTED_QPU_EXECUTION")
BLOCKED_EXTERNAL_AUTHORITY = "BLOCKED_EXTERNAL_AUTHORITY"

#: Node ids used throughout the fabric.
NODE_IDS = ("N_SMALL", "N_MEDIUM", "N_LARGE", "N_XLARGE")


class AdapterRefusal(Exception):
    """An adapter refuses. Adapters fail closed (adapter spec s2.4)."""

    def __init__(self, reason: str, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = dict(detail or {})

    def as_dict(self):
        return {"refused": True, "reason": self.reason, "detail": self.detail}


def assert_not_physical(adapter) -> None:
    """The physical-evidence firewall, as code (mirrors pacore.adapters)."""
    td = getattr(adapter, "trust_domain", None)
    if td not in PERMITTED_TRUST_DOMAINS:
        raise AdapterRefusal(
            f"a DF adapter may not claim trust domain {td!r}; permitted: "
            f"{PERMITTED_TRUST_DOMAINS}", {"trust_domain": td})
    label = getattr(adapter, "EXECUTION_LABEL", "")
    if not label.startswith("CLASSICAL_"):
        raise AdapterRefusal(f"execution label {label!r} must begin 'CLASSICAL_'",
                             {"label": label})
    for tok in FORBIDDEN_LABEL_TOKENS:
        if tok in label:
            raise AdapterRefusal(
                f"execution label {label!r} contains forbidden token {tok!r}",
                {"label": label, "token": tok})
    for flag in ("physical_qpu", "physical_parallel", "physical_distributed"):
        if getattr(adapter, flag, False):
            raise AdapterRefusal(f"a DF adapter may not set {flag}=True",
                                 {flag: True})
