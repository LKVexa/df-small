"""The row-sequence witness and its lowering to each fabric node's dialect.

Schema: `DF/ROW_WITNESS_LOWERING/1`. This generalises `pacore.adapters.brlower`
(`PA-LCTL/BR-LOWERING/1`) in exactly one way: the accumulator may start from a
caller-supplied value `acc_in` instead of the FNV offset basis. With
`acc_in == FNV_OFFSET` and the full row list, `lower_lctlc11` reproduces
`brlower.lower()` byte for byte (asserted by the DF gates), so the fabric's
Medium node computes precisely the witness the corpora's own adapter defines.

The witness, for rendered rows r_0 .. r_{n-1} in sealed order:

    w(r)      = low 64 bits of sha256(r)          (little-endian, as brlower)
    acc_0     = acc_in                             (FNV-1a 64 offset basis by default)
    acc_{i+1} = ((acc_i * FNV_PRIME) mod 2**64) XOR w(r_i)
    witness   = acc_n

Why one program computes it on four different machines
------------------------------------------------------
Every embedded VM has 1,048,576-bit registers and MOVI / MUL / XOR / MOV /
HALT. Multiplication in a modulus 2**N with N >= 64 preserves the low 64 bits
of the product, and XOR against a 64-bit word only touches the low 64 bits, so
the low 64 bits of the wide accumulator equal the 64-bit witness exactly on
every node, whatever the node's wrap width. The nodes report:

    Small   (MSSL/ASM-1, BOTTLE ROCKET 3.0.0-MODEL)  R2.low64=<u64>
    Medium  (LCTLC/1.1,  BOTTLE ROCKET 5.0.0/ISA 4.1) R2.low64=<u64>
    Large   (LCTLC/1.2,  BOTTLE ROCKET 4.7.0/BR 1.1)  {"R2": <u64>}
    XLarge  (LCTLC/1.0,  QUORUM VM 5.0.0-candidate)   registers_hex[0] -> low 64 bits

Segmentation
------------
The three BOTTLE ROCKET nodes admit 256 instructions per image; three
instructions per row plus four of prologue/epilogue bound a single lowering to
84 rows (`brlower.MAX_ROWS`). A longer bundle is split into consecutive
segments of at most 84 rows; segment k+1 starts from segment k's output
accumulator. Segments are therefore a dependency chain -- a pipeline across
nodes, never a parallel reduction -- and the fabric records that ordering as
happens-before edges. The QVM node admits 65,535 instructions (21,843 rows).

Bounds are refusals, not truncations (adapter spec s3, SUPPORTED_WITH_LIMITS).
"""

from __future__ import annotations

import hashlib
from typing import List, Sequence

LOWERING_SCHEMA = "DF/ROW_WITNESS_LOWERING/1"

FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MASK64 = (1 << 64) - 1

INSTRUCTIONS_PER_ROW = 3
PROLOGUE_EPILOGUE = 4

#: Per-node instruction ceilings, measured from each toolchain (not assumed).
BR_MAX_INSTRUCTIONS = 256          # Small (brasm.c), Medium (brlctlc.c N05), Large (lctl430.py MAX_CODE)
BR_MAX_SOURCE_LINES = 512          # Medium (ALINES)
QVM_MAX_INSTRUCTIONS = 65535       # Xtra_Large (quorum_vm.py MAX_PROGRAM_INSNS)

BR_MAX_ROWS = (BR_MAX_INSTRUCTIONS - PROLOGUE_EPILOGUE) // INSTRUCTIONS_PER_ROW   # 84
QVM_MAX_ROWS = (QVM_MAX_INSTRUCTIONS - PROLOGUE_EPILOGUE) // INSTRUCTIONS_PER_ROW  # 21843

MAX_ROWS = {"N_SMALL": BR_MAX_ROWS, "N_MEDIUM": BR_MAX_ROWS,
            "N_LARGE": BR_MAX_ROWS, "N_XLARGE": QVM_MAX_ROWS}

#: LCTLC row separator (U+2502) and operand separator (U+203A) -- as brlower.
LCTLC_SEP = "│"
LCTLC_IN_SEP = "›"

#: Frozen BOTTLE ROCKET 5.0.0 unit contract this lowering targets (as brlower).
BR11_UNIT_VERSION = "4.7.0"
BR11_IMAGE_VERSION = 9
#: BOTTLE ROCKET 4.7.0 (BR/1.1) columned-lctl/4.3 contract (lctl430.py).
BR12_UNIT_VERSION = "4.3.0"
BR12_LANGUAGE = "columned-lctl/4.3"
BR12_ISA = "BR/1.1"
BR12_IMAGE_VERSION = 10
#: BOTTLE ROCKET 3.0.0-MODEL MSSL/ASM-1 image version (nonzero u32).
MSSL_IMAGE_VERSION = 3


class LoweringRefused(Exception):
    """A bundle cannot be lowered inside the target's bounds."""

    def __init__(self, reason: str, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = dict(detail or {})


# --------------------------------------------------------------------------
# reference computation (CPython)
# --------------------------------------------------------------------------

def row_word(rendered_row: str) -> int:
    """The 64-bit word a single canonical row contributes (as brlower)."""
    return int.from_bytes(
        hashlib.sha256(rendered_row.encode("utf-8")).digest()[:8], "little")


def reference_witness(rendered_rows: Sequence[str], acc_in: int = FNV_OFFSET) -> int:
    acc = acc_in & MASK64
    for r in rendered_rows:
        acc = (acc * FNV_PRIME) & MASK64
        acc ^= row_word(r)
    return acc & MASK64


def reference_witness_words(words: Sequence[int], acc_in: int = FNV_OFFSET) -> int:
    acc = acc_in & MASK64
    for w in words:
        acc = (acc * FNV_PRIME) & MASK64
        acc ^= (w & MASK64)
    return acc & MASK64


def rendered_rows(program) -> List[str]:
    """The canonical text of each row, in sealed order (as brlower)."""
    return [r.render(program.columns) for r in program.rows]


def words_of(program) -> List[int]:
    return [row_word(r) for r in rendered_rows(program)]


def segments(words: Sequence[int], max_rows: int) -> List[List[int]]:
    """Consecutive segments of at most `max_rows` words, in order."""
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    return [list(words[i:i + max_rows]) for i in range(0, len(words), max_rows)]


def instruction_count(n_rows: int) -> int:
    return n_rows * INSTRUCTIONS_PER_ROW + PROLOGUE_EPILOGUE


def _check_bound(words: Sequence[int], max_rows: int, ceiling: int, node: str) -> None:
    if not words:
        raise LoweringRefused("no rows to witness", {"rows": 0, "node": node})
    if len(words) > max_rows:
        raise LoweringRefused(
            f"{len(words)} rows exceed the {node} lowering bound of {max_rows} rows "
            f"({ceiling}-instruction image ceiling, {INSTRUCTIONS_PER_ROW} instructions "
            f"per row + {PROLOGUE_EPILOGUE} prologue/epilogue). Exceeding a stated "
            f"bound is an error, not a degradation.",
            {"rows": len(words), "max_rows": max_rows, "instruction_ceiling": ceiling,
             "node": node, "feature_class": "SUPPORTED_WITH_LIMITS"})


# --------------------------------------------------------------------------
# N_MEDIUM -- LCTLC/1.1 (BOTTLE ROCKET 5.0.0, ISA 4.1) -- brlower-compatible
# --------------------------------------------------------------------------

def lower_lctlc11(words: Sequence[int], acc_in: int = FNV_OFFSET,
                  unit_id: str = "pa.lctl.witness") -> str:
    """LCTLC/1.1 unit for the 5.0.0 toolchain (bradmin). Byte-identical to
    `pacore.adapters.brlower.lower(program)` when acc_in == FNV_OFFSET."""
    _check_bound(words, BR_MAX_ROWS, BR_MAX_INSTRUCTIONS, "N_MEDIUM")
    n_insn = instruction_count(len(words))
    out: List[str] = ["LCTLC/1.1"]
    out.append(
        f"@unit id={unit_id} version={BR11_UNIT_VERSION} profile=brvm-native "
        f"entry=W00001 target=local-reference backend=brir network=deny "
        f"replay=deterministic image_version={BR11_IMAGE_VERSION} "
        f"requested_caps=CONTROL|ARITH max_steps={n_insn} "
        f"termination=bounded")
    out.append("@defaults mode=WRAP width=WIDE")
    out.append(f"@frame id=F0000 parent=ROOT module={unit_id}")
    out.append(LCTLC_SEP.join(("ID", "LANE", "OP", "OUT", "CTRL", "IN", "ARG", "META")))
    counter = [0]

    def rid() -> str:
        counter[0] += 1
        return f"W{counter[0]:05d}"

    def emit(op: str, out_reg: str, in_regs: Sequence[str] = (),
             arg: str = "_", meta: str = "width=WIDE") -> None:
        out.append(LCTLC_SEP.join((
            rid(), "w", op, out_reg, "C0",
            LCTLC_IN_SEP.join(in_regs) if in_regs else "_", arg, meta)))

    emit("MOVI", "R0", (), f"u64:{acc_in & MASK64}")
    emit("MOVI", "R1", (), f"u64:{FNV_PRIME}")
    for w in words:
        emit("MUL", "R0", ("R0", "R1"), "_", "mode=WRAP;width=WIDE")
        emit("MOVI", "R3", (), f"u64:{w & MASK64}")
        emit("XOR", "R0", ("R0", "R3"))
    emit("MOV", "R2", ("R0",))
    out.append(LCTLC_SEP.join((rid(), "w", "HALT", "_", "C0", "_", "_", "_")))
    out.append("@end")
    text = "\n".join(out) + "\n"
    if len(out) > BR_MAX_SOURCE_LINES:
        raise LoweringRefused(
            f"lowered unit is {len(out)} source lines; LCTLC/1.1 accepts {BR_MAX_SOURCE_LINES}",
            {"lines": len(out), "max_lines": BR_MAX_SOURCE_LINES, "node": "N_MEDIUM"})
    return text


# --------------------------------------------------------------------------
# N_SMALL -- MSSL/ASM-1 (BOTTLE ROCKET 3.0.0-MODEL)
# --------------------------------------------------------------------------

def lower_mssl(words: Sequence[int], acc_in: int = FNV_OFFSET,
               unit_id: str = "pa.lctl.witness") -> str:
    """MSSL/ASM-1 text for `brctl assemble`. Result convention: R2.low64."""
    _check_bound(words, BR_MAX_ROWS, BR_MAX_INSTRUCTIONS, "N_SMALL")
    out: List[str] = [
        f"# {LOWERING_SCHEMA} unit={unit_id} rows={len(words)}",
        ".profile SIM_CORE",
        f".image_version {MSSL_IMAGE_VERSION}",
        ".request_caps CONTROL|ARITH",
        f"MOVI.WRAP R0, {acc_in & MASK64}, C0",
        f"MOVI.WRAP R1, {FNV_PRIME}, C0",
    ]
    for w in words:
        out.append("MUL.WRAP R0, R0, R1, C0")
        out.append(f"MOVI.WRAP R3, {w & MASK64}, C0")
        out.append("XOR.WRAP R0, R0, R3, C0")
    out.append("MOV.WRAP R2, R0, C0")
    out.append("HALT.WRAP C0")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# N_LARGE -- LCTLC/1.2 columned-lctl/4.3, BR/1.1 (BOTTLE ROCKET 4.7.0)
# --------------------------------------------------------------------------

def lower_lctlc12(words: Sequence[int], acc_in: int = FNV_OFFSET,
                  unit_id: str = "pa.lctl.witness") -> str:
    """LCTLC/1.2 unit for `tools/lctl430.py compile --factory`. Result: R2."""
    _check_bound(words, BR_MAX_ROWS, BR_MAX_INSTRUCTIONS, "N_LARGE")
    uid = unit_id.replace("_", "-")
    out: List[str] = ["LCTLC/1.2"]
    out.append(
        f"@unit id={uid} version={BR12_UNIT_VERSION} language={BR12_LANGUAGE} "
        f"isa={BR12_ISA} br_image_version={BR12_IMAGE_VERSION} "
        f"br_request_caps=CONTROL|ARITH")
    out.append("@defaults mode=WRAP width=WIDE")
    out.append(LCTLC_SEP.join(("ID", "LANE", "OP", "OUT", "CTRL", "IN", "ARG", "META")))
    counter = [0]

    def rid() -> str:
        counter[0] += 1
        return f"W{counter[0]:05d}"

    def row(op: str, out_reg: str, cap: str, ins: Sequence[str] = (), arg: str = "_") -> None:
        out.append(LCTLC_SEP.join((
            rid(), "exec", op, out_reg, f"C0:{cap}",
            LCTLC_IN_SEP.join(ins) if ins else "_", arg, "_")))

    row("MOVI", "R0", "CONTROL", (), f"imm={acc_in & MASK64}")
    row("MOVI", "R1", "CONTROL", (), f"imm={FNV_PRIME}")
    for w in words:
        row("MUL", "R0", "ARITH", ("R0", "R1"))
        row("MOVI", "R3", "CONTROL", (), f"imm={w & MASK64}")
        row("XOR", "R0", "ARITH", ("R0", "R3"))
    row("MOV", "R2", "CONTROL", ("R0",))
    row("HALT", "_", "CONTROL")
    out.append("@end")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# N_XLARGE -- LCTLC/1.0 COLUMNED LCTL QVM Profile 1 (QUORUM VM 5.0.0-candidate)
# --------------------------------------------------------------------------

def lower_qvm(words: Sequence[int], acc_in: int = FNV_OFFSET,
              unit_id: str = "pa.lctl.witness") -> str:
    """LCTLC/1.0 unit for `quorum_vm.py compile`. Result register: R0."""
    _check_bound(words, QVM_MAX_ROWS, QVM_MAX_INSTRUCTIONS, "N_XLARGE")
    uid = unit_id
    out: List[str] = ["LCTLC/1.0"]
    out.append(f"@unit id={uid} version=1.0.0 profile=native program_version=1")
    out.append("@defaults qspace=H basis=computational regime=exact "
               "assume=finite_dimension error=exact conf=1.0")
    out.append(f"@frame id=F0000 parent=ROOT module={uid}")
    out.append(LCTLC_SEP.join(("ID", "LANE", "OP", "OUT", "CTRL", "IN", "ARG", "META")))
    # The bundled LCTL 1.6.1-RC1 column verifier requires a declared quantum
    # register and classical register (the two alloc rows). They allocate
    # nothing on the QVM; the vm lane carries the program.
    out.append(LCTLC_SEP.join(("D0001", "alloc", "Q", "q", "_", "_", "1",
                               'res="logical_qubits=1";p=lctl:1.2')))
    out.append(LCTLC_SEP.join(("D0002", "alloc", "C", "c", "_", "_", "1",
                               'res="classical_bits=1";p=lctl:1.2')))
    counter = [0]

    def vrow(arg: str) -> None:
        counter[0] += 1
        out.append(LCTLC_SEP.join((f"V{counter[0]:05d}", "vm", "REG", "_", "_", "_",
                                   arg, "p=quorum:vm5")))

    vrow(f"vm.op=MOVI;dst=R0;imm={acc_in & MASK64};mode=modular")
    vrow(f"vm.op=MOVI;dst=R1;imm={FNV_PRIME};mode=modular")
    for w in words:
        vrow("vm.op=MUL;dst=R0;a=R0;b=R1;mode=modular")
        vrow(f"vm.op=MOVI;dst=R3;imm={w & MASK64};mode=modular")
        vrow("vm.op=XOR;dst=R0;a=R0;b=R3;mode=modular")
    vrow("vm.op=MOV;dst=R2;a=R0;mode=modular")
    vrow("vm.op=HALT")
    out.append(LCTLC_SEP.join(("C9999", "terminal", ".", "_", "_", "_",
                               "terminal=sealed_graph_end;justification=canonical_entry_sentinel",
                               "p=lctl:nop")))
    out.append("@end")
    return "\n".join(out) + "\n"


LOWERINGS = {
    "N_SMALL": lower_mssl,
    "N_MEDIUM": lower_lctlc11,
    "N_LARGE": lower_lctlc12,
    "N_XLARGE": lower_qvm,
}


def lowering_record(node_id: str, words: Sequence[int], acc_in: int = FNV_OFFSET,
                    unit_id: str = "pa.lctl.witness") -> dict:
    """Everything a caller needs to reproduce and check one lowering."""
    src = LOWERINGS[node_id](words, acc_in, unit_id)
    return {
        "schema": LOWERING_SCHEMA,
        "node_id": node_id,
        "unit_id": unit_id,
        "rows_lowered": len(words),
        "max_rows": MAX_ROWS[node_id],
        "instructions": instruction_count(len(words)),
        "acc_in": acc_in & MASK64,
        "source_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest(),
        "reference_witness": reference_witness_words(words, acc_in),
        "source": src,
    }
