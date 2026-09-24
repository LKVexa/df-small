"""Target adapters for PA-LCTL.

`PA_LCTL_TARGET_ADAPTER_SPEC.md` specifies an adapter ABI and states, as of
PA21.2, that *no* adapter implements it:

    "There is no implemented target adapter in `pacore`, and there cannot be
     one in this environment."

That statement was true for a *physical quantum* target and remains true.
PA21.3 adds the first adapter of any kind: `bottlerocket`, which binds a
local, offline, deterministic **classical** virtual machine.

The distinction is load-bearing and is enforced, not merely documented:

* An adapter here MAY declare `trust_domain` `LOCAL_TRUSTED`.
* An adapter here SHALL NOT declare `PHYSICAL_TARGET_AUTHENTICATED`.
* `physical_qpu`, `physical_parallel` and `physical_distributed` SHALL be
  False for every adapter in this package.

`PHYSICAL_QPU_EXECUTION`, `PHYSICAL_PARALLEL_QPU_EXECUTION` and
`PHYSICAL_DISTRIBUTED_QPU_EXECUTION` remain `BLOCKED_EXTERNAL_AUTHORITY`.
Nothing in this subpackage lifts, weakens or routes around that blocker, and
`assert_not_physical()` below is called by every adapter on construction so
that a future edit cannot quietly do so.
"""

from __future__ import annotations

from typing import Any, Dict

ADAPTER_ABI = "PA-LCTL/TARGET_ADAPTER/1"

#: Trust domains an adapter in this package may claim. Deliberately excludes
#: PHYSICAL_TARGET_AUTHENTICATED.
PERMITTED_TRUST_DOMAINS = ("LOCAL_TRUSTED", "LOCAL_UNTRUSTED")

#: Tokens that may not appear in an execution label emitted from this package.
#: Mirrors `simulator._FORBIDDEN_LABEL_TOKENS`.
FORBIDDEN_LABEL_TOKENS = ("QPU", "HARDWARE", "PHYSICAL", "DEVICE",
                          "QUANTUM_EXECUTION")


class AdapterRefusal(Exception):
    """Raised when an adapter refuses. Adapters fail closed.

    Per adapter spec s2.4 every method either returns a complete, evidenced
    answer or raises with a stated reason. A refusal is never a degraded
    result.
    """

    def __init__(self, reason: str, detail: Dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}

    def as_dict(self) -> Dict[str, Any]:
        return {"refused": True, "reason": self.reason, "detail": self.detail}


def assert_not_physical(adapter: Any) -> None:
    """Fail loudly if an adapter claims anything physical.

    Called from every adapter constructor. This is the firewall the adapter
    spec s1 describes, expressed as code rather than prose.
    """
    td = getattr(adapter, "trust_domain", None)
    if td not in PERMITTED_TRUST_DOMAINS:
        raise AdapterRefusal(
            "an adapter in pacore.adapters may not claim trust domain "
            f"{td!r}; permitted: {PERMITTED_TRUST_DOMAINS}",
            {"trust_domain": td})
    label = getattr(adapter, "EXECUTION_LABEL", "")
    if not label.startswith("CLASSICAL_"):
        raise AdapterRefusal(
            f"execution label {label!r} must begin 'CLASSICAL_'",
            {"label": label})
    for tok in FORBIDDEN_LABEL_TOKENS:
        if tok in label:
            raise AdapterRefusal(
                f"execution label {label!r} contains forbidden token {tok!r}",
                {"label": label, "token": tok})
