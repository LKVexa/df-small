"""PA-LCTL -> Columned LCTL lowering.  Schema: `PA-LCTL/BR-LOWERING/1`.

What this lowering is, and what it deliberately is not
------------------------------------------------------

BOTTLE ROCKET is a **classical integer virtual machine**. It has 32 opcodes,
64-bit-and-wider integer registers, no floating point in its ISA and no notion
of a qubit. It therefore **cannot** execute the quantum face of a PA-LCTL
bundle, and this module does not pretend otherwise: no gate is lowered to
anything, and `BottleRocketAdapter.native_gate_set()` returns the empty set.

What the VM *can* do, natively and under an enforced step budget, is execute a
bounded classical program derived deterministically from the bundle's sealed
row sequence. This lowering emits exactly that: a **row-sequence witness**.

    R0 <- FNV_OFFSET
    R1 <- FNV_PRIME
    for each row, in canonical (sealed) order:
        R0 <- R0 * R1        (mod 2**64, WRAP)
        R3 <- w(row)         (low 64 bits of sha256 of the row's canonical text)
        R0 <- R0 XOR R3
    R2 <- R0
    HALT

`w(row)` is taken over `Row.render(program.columns)` — the same bytes the
bundle's seal is computed over — so the witness is a function of the sealed
content of every row, in order. Changing any cell of any row, reordering two
rows, or adding or removing a row changes the witness.

Why this is worth executing natively
------------------------------------

The witness is computed twice: once by CPython in `reference_witness()`, and
once by the VM's own compiled BRIM image running its real ISA. The adapter
compares them. That makes the result a **differential check between the Python
reference core and an independent native implementation**, which is a class of
evidence PA21.2 had none of -- every executed check in that release ran in the
same CPython process as the code under test.

It is a witness, not a simulation. It proves the row sequence was carried
intact through compilation, image construction, signing and native execution
under a declared step bound. It proves nothing whatever about quantum
semantics.

Bounds (LCTLC/1.1, measured from the toolchain, not assumed)
------------------------------------------------------------

* `N05` = 256 instructions per unit  -> 3 instructions per row + 4 of prologue
  and epilogue gives **MAX_ROWS = 84**.
* `ALINES` = 512 source lines.
* Both are hard refusals, not truncations: exceeding a bound is an error
  (adapter spec s3, `SUPPORTED_WITH_LIMITS`).
"""

from __future__ import annotations

import hashlib
from typing import List, Sequence

LOWERING_SCHEMA = "PA-LCTL/BR-LOWERING/1"

#: LCTLC row separator: U+2502 BOX DRAWINGS LIGHT VERTICAL.
LCTLC_SEP = "│"
#: LCTLC operand separator: U+203A SINGLE RIGHT-POINTING ANGLE QUOTATION MARK.
LCTLC_IN_SEP = "›"

#: Measured from `src/brlctlc.c` (N05) and `src/brvm.h`, not assumed.
BR_MAX_INSTRUCTIONS = 256
BR_MAX_SOURCE_LINES = 512
INSTRUCTIONS_PER_ROW = 3
PROLOGUE_EPILOGUE = 4
MAX_ROWS = (BR_MAX_INSTRUCTIONS - PROLOGUE_EPILOGUE) // INSTRUCTIONS_PER_ROW

FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MASK64 = (1 << 64) - 1

#: The frozen BOTTLE ROCKET runtime contract this lowering targets.
BR_UNIT_VERSION = "4.7.0"
BR_IMAGE_VERSION = 9


class LoweringRefused(Exception):
    """Raised when a bundle cannot be lowered inside the target's bounds."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def row_word(rendered_row: str) -> int:
    """The 64-bit word a single canonical row contributes to the witness."""
    return int.from_bytes(
        hashlib.sha256(rendered_row.encode("utf-8")).digest()[:8], "little")


def reference_witness(rendered_rows: Sequence[str]) -> int:
    """Compute the witness in CPython. The VM must agree with this."""
    acc = FNV_OFFSET
    for r in rendered_rows:
        acc = (acc * FNV_PRIME) & MASK64
        acc ^= row_word(r)
    return acc & MASK64


def rendered_rows(program) -> List[str]:
    """The canonical text of each row, in sealed order."""
    return [r.render(program.columns) for r in program.rows]


def lower(program, unit_id: str = "pa.lctl.witness") -> str:
    """Lower a verified PA-LCTL `Program` to LCTLC/1.1 source text.

    Raises `LoweringRefused` rather than truncating when a bound is exceeded.
    """
    rows = rendered_rows(program)
    if not rows:
        raise LoweringRefused("bundle has no rows to witness", {"rows": 0})
    if len(rows) > MAX_ROWS:
        raise LoweringRefused(
            f"bundle has {len(rows)} rows; the BOTTLE ROCKET lowering is bound "
            f"to {MAX_ROWS} rows by the LCTLC/1.1 {BR_MAX_INSTRUCTIONS}-"
            f"instruction unit ceiling. Exceeding a stated bound is an error, "
            f"not a degradation.",
            {"rows": len(rows), "max_rows": MAX_ROWS,
             "instruction_ceiling": BR_MAX_INSTRUCTIONS,
             "feature_class": "SUPPORTED_WITH_LIMITS"})

    n_insn = len(rows) * INSTRUCTIONS_PER_ROW + PROLOGUE_EPILOGUE
    out: List[str] = ["LCTLC/1.1"]
    out.append(
        f"@unit id={unit_id} version={BR_UNIT_VERSION} profile=brvm-native "
        f"entry=W00001 target=local-reference backend=brir network=deny "
        f"replay=deterministic image_version={BR_IMAGE_VERSION} "
        f"requested_caps=CONTROL|ARITH max_steps={n_insn} "
        f"termination=bounded")
    out.append("@defaults mode=WRAP width=WIDE")
    out.append(f"@frame id=F0000 parent=ROOT module={unit_id}")
    out.append(LCTLC_SEP.join(
        ("ID", "LANE", "OP", "OUT", "CTRL", "IN", "ARG", "META")))

    counter = [0]

    def rid() -> str:
        counter[0] += 1
        return f"W{counter[0]:05d}"

    def emit(op: str, out_reg: str, in_regs: Sequence[str] = (),
             arg: str = "_", meta: str = "width=WIDE") -> None:
        out.append(LCTLC_SEP.join((
            rid(), "w", op, out_reg, "C0",
            LCTLC_IN_SEP.join(in_regs) if in_regs else "_", arg, meta)))

    emit("MOVI", "R0", (), f"u64:{FNV_OFFSET}")
    emit("MOVI", "R1", (), f"u64:{FNV_PRIME}")
    for r in rows:
        emit("MUL", "R0", ("R0", "R1"), "_", "mode=WRAP;width=WIDE")
        emit("MOVI", "R3", (), f"u64:{row_word(r)}")
        emit("XOR", "R0", ("R0", "R3"))
    # R2 is the register the production host reports.
    emit("MOV", "R2", ("R0",))
    out.append(LCTLC_SEP.join(
        (rid(), "w", "HALT", "_", "C0", "_", "_", "_")))
    out.append("@end")

    text = "\n".join(out) + "\n"
    n_lines = len(out)
    if n_lines > BR_MAX_SOURCE_LINES:
        raise LoweringRefused(
            f"lowered unit is {n_lines} source lines; LCTLC/1.1 accepts "
            f"{BR_MAX_SOURCE_LINES}",
            {"lines": n_lines, "max_lines": BR_MAX_SOURCE_LINES})
    return text


def lowering_record(program, unit_id: str = "pa.lctl.witness") -> dict:
    """Everything the caller needs to reproduce and check the lowering."""
    rows = rendered_rows(program)
    src = lower(program, unit_id)
    return {
        "schema": LOWERING_SCHEMA,
        "unit_id": unit_id,
        "rows_lowered": len(rows),
        "max_rows": MAX_ROWS,
        "instructions": len(rows) * INSTRUCTIONS_PER_ROW + PROLOGUE_EPILOGUE,
        "instruction_ceiling": BR_MAX_INSTRUCTIONS,
        "source_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest(),
        "reference_witness": reference_witness(rows),
        "program_seal": program.seal(),
        "lctlc_source": src,
    }
