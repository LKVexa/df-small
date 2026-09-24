"""Executable evidence for the BOTTLE ROCKET classical backend (PA21.3).

Every check is a NEGATIVE or a POSITIVE in the same discipline the PA-MATH
guardrail suite uses:

  NEGATIVE - the firewall or a stated bound is attacked and MUST refuse.
  POSITIVE - a legitimate use MUST succeed, proving the refusals discriminate
             rather than blanket-deny.

Run:  cd <package> && python3 -B pamath/tests/test_bottlerocket_backend.py

Requires the BOTTLE ROCKET toolchain, located via PA_LCTL_BOTTLE_ROCKET_ROOT.
If it is absent the suite SKIPS loudly and exits non-zero rather than
reporting a pass it did not earn.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from reference.pacore import lang                                # noqa: E402
from reference.pacore import adapters                            # noqa: E402
from reference.pacore.adapters import bottlerocket as br         # noqa: E402
from reference.pacore.adapters import brlower                    # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name:<66} {detail}")


def load(path: str):
    prog, _ = lang.parse(open(path, encoding="utf-8").read())
    assert prog is not None and lang.verify(prog).ok, path
    return prog


def main() -> int:
    root = os.environ.get("PA_LCTL_BOTTLE_ROCKET_ROOT")
    if not root or not os.path.isdir(root):
        print("SKIP: PA_LCTL_BOTTLE_ROCKET_ROOT is not set to a built "
              "BOTTLE ROCKET toolchain. No result is claimed.")
        return 2

    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    ex = os.path.join(here, "examples")
    if not os.path.isdir(ex):
        print("SKIP: this package carries no examples/ directory, so there is "
              "no bundle to execute. No result is claimed.")
        return 2

    print("\n-- the firewall: a classical adapter may not claim anything "
          "physical " + "-" * 30)

    class Rogue:
        trust_domain = "PHYSICAL_TARGET_AUTHENTICATED"
        EXECUTION_LABEL = "CLASSICAL_NATIVE_VM_BOUNDED_EXECUTION"

    try:
        adapters.assert_not_physical(Rogue())
        check("NEGATIVE: an adapter claiming PHYSICAL_TARGET_AUTHENTICATED "
              "is refused", False)
    except adapters.AdapterRefusal as exc:
        check("NEGATIVE: an adapter claiming PHYSICAL_TARGET_AUTHENTICATED "
              "is refused", True, exc.reason[:60])

    class RogueLabel:
        trust_domain = "LOCAL_TRUSTED"
        EXECUTION_LABEL = "PHYSICAL_QPU_EXECUTION"

    try:
        adapters.assert_not_physical(RogueLabel())
        check("NEGATIVE: a non-CLASSICAL_ execution label is refused", False)
    except adapters.AdapterRefusal:
        check("NEGATIVE: a non-CLASSICAL_ execution label is refused", True)

    class RogueToken:
        trust_domain = "LOCAL_TRUSTED"
        EXECUTION_LABEL = "CLASSICAL_QPU_ASSISTED_RUN"

    try:
        adapters.assert_not_physical(RogueToken())
        check("NEGATIVE: a label containing a forbidden token is refused",
              False)
    except adapters.AdapterRefusal:
        check("NEGATIVE: a label containing a forbidden token is refused",
              True)

    a = br.BottleRocketAdapter(root=root)
    check("positive: the real adapter constructs", True, a.target_id)
    check("the adapter's trust domain is LOCAL_TRUSTED, not physical",
          a.trust_domain == "LOCAL_TRUSTED", a.trust_domain)
    check("the native gate set is empty - the VM realizes no quantum gate",
          a.native_gate_set() == frozenset())
    check("every quantum feature classifies UNSUPPORTED",
          all(a.feature_class(f) == "UNSUPPORTED"
              for f in ("CX", "H", "MEASURE", "TELEPORT", "PREP0", "RZ")))
    check("an unstated feature defaults to UNSUPPORTED",
          a.feature_class("time_travel") == "UNSUPPORTED")

    print("\n-- refusal on a missing or unbuilt toolchain " + "-" * 52)
    try:
        br.BottleRocketAdapter(root="/nonexistent/toolchain")
        check("NEGATIVE: a missing toolchain root is refused, not faked",
              False)
    except adapters.AdapterRefusal:
        check("NEGATIVE: a missing toolchain root is refused, not faked", True)

    print("\n-- native execution and the differential check " + "-" * 51)
    prog = load(os.path.join(ex, "01_bell_pair.pal"))
    res = a.submit(prog)
    check("positive: a verified bundle executes natively on the VM",
          res["label"] == br.EXECUTION_LABEL, res["label"])
    check("the native VM witness equals the CPython reference witness",
          res["differential_agreement"],
          f"{res['native_witness']}")
    check("the pipeline ran all eight native stages",
          [s["step"] for s in res["steps"]] == [
              "verify-lctlc", "lctl-to-brir", "compile-lctlc",
              "independent-brim-verify", "keygen", "sign-image",
              "verify-image", "run-signed"])
    check("the executed image was independently BRIM-verified",
          any(s["step"] == "independent-brim-verify" and s["returncode"] == 0
              for s in res["steps"]))
    check("the executed image was Ed25519-signed and signature-verified",
          any(s["step"] == "verify-image" and s["returncode"] == 0
              for s in res["steps"]))

    print("\n-- the witness is sensitive to the sealed row sequence " + "-" * 43)
    rows = brlower.rendered_rows(prog)
    base = brlower.reference_witness(rows)
    swapped = list(rows)
    swapped[3], swapped[4] = swapped[4], swapped[3]
    check("NEGATIVE: swapping two rows changes the witness",
          brlower.reference_witness(swapped) != base)
    mutated = list(rows)
    mutated[2] = mutated[2].replace("EXACT", "NOISY", 1)
    check("NEGATIVE: mutating one cell of one row changes the witness",
          brlower.reference_witness(mutated) != base)
    check("NEGATIVE: dropping a row changes the witness",
          brlower.reference_witness(rows[:-1]) != base)
    check("positive: the same rows give the same witness (deterministic)",
          brlower.reference_witness(rows) == base)

    prog2 = load(os.path.join(ex, "02_ghz3.pal"))
    res2 = a.submit(prog2)
    check("a different bundle yields a different native witness",
          res2["native_witness"] != res["native_witness"])
    check("the second bundle also agrees with its reference witness",
          res2["differential_agreement"])

    print("\n-- stated bounds are refusals, not degradations " + "-" * 50)
    check("MAX_ROWS is derived from the measured LCTLC instruction ceiling",
          brlower.MAX_ROWS == (brlower.BR_MAX_INSTRUCTIONS
                               - brlower.PROLOGUE_EPILOGUE) // 3,
          f"MAX_ROWS={brlower.MAX_ROWS}")

    class Oversized:
        columns = prog.columns
        rows = list(prog.rows) * (brlower.MAX_ROWS // len(prog.rows) + 2)

        def seal(self):
            return "0" * 64

    try:
        brlower.lower(Oversized())
        check("NEGATIVE: a bundle over MAX_ROWS is refused, not truncated",
              False)
    except brlower.LoweringRefused as exc:
        check("NEGATIVE: a bundle over MAX_ROWS is refused, not truncated",
              True, f"{exc.detail.get('rows')} > {brlower.MAX_ROWS}")

    try:
        a.submit(prog, shots=256)
        check("NEGATIVE: shots>1 on a deterministic witness is refused",
              False)
    except adapters.AdapterRefusal:
        check("NEGATIVE: shots>1 on a deterministic witness is refused", True)

    try:
        a.submit(prog, qcir_p2={"schema": "SOMETHING/ELSE"})
        check("NEGATIVE: a QCIR-P2 document with the wrong schema is refused",
              False)
    except adapters.AdapterRefusal:
        check("NEGATIVE: a QCIR-P2 document with the wrong schema is refused",
              True)

    print("\n-- provenance carries no physical claim " + "-" * 58)
    pv = a.provenance(prog)
    check("provenance.physical_qpu is False", pv["physical_qpu"] is False)
    check("provenance.physical_parallel is False",
          pv["physical_parallel"] is False)
    check("provenance.physical_distributed is False",
          pv["physical_distributed"] is False)
    check("provenance.quantum_boundary is NOT_CROSSED",
          pv["quantum_boundary"] == "NOT_CROSSED")
    check("provenance.execution_class begins CLASSICAL_",
          pv["execution_class"].startswith("CLASSICAL_"))
    check("provenance.target_class names a classical VM",
          pv["target_class"] == "CLASSICAL_LOCAL_VM")

    print("\n" + "=" * 100)
    print(f"  TOTAL {len(PASS) + len(FAIL)}   PASS {len(PASS)}   "
          f"FAIL {len(FAIL)}")
    print("=" * 100)
    print("\nALL CHECKS PASS" if not FAIL else "\nFAILURES PRESENT")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
