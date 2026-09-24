# PA-LCTL BOTTLE ROCKET Backend Specification

Document: `PA_LCTL_BOTTLE_ROCKET_BACKEND.md`
Authority: `pacore.adapters`, `pacore.adapters.bottlerocket`,
`pacore.adapters.brlower`, `pacore.cli.cmd_adapter_check`,
`pacore.cli.cmd_backend_run`, `pamath/tests/test_bottlerocket_backend.py`.

RFC 2119 keywords apply.

> **Status.** `OPERATIONAL`. This is the first implementation of the
> `PA_LCTL_TARGET_ADAPTER_SPEC.md` §2 ABI in this package. It binds a
> **classical** target. `PA_LCTL_TARGET_ADAPTER_SPEC.md` remains correct in
> every statement it makes about *physical quantum* targets, all of which are
> still `BLOCKED_EXTERNAL_AUTHORITY`.

---

## 1. What this backend is, and what it is not

BOTTLE ROCKET 5.0.0 is a **classical integer virtual machine**: 32 opcodes, 20
services, wide integer registers, a signed image format, transactional
persistence, and a capability-gated device ABI. It has **no qubit**, no
amplitude, and no gate.

Therefore, and enforced in code rather than promised in prose:

| | |
|---|---|
| `native_gate_set()` | **empty** |
| `feature_class(<any quantum op>)` | `UNSUPPORTED` |
| `trust_domain` | `LOCAL_TRUSTED` — never `PHYSICAL_TARGET_AUTHENTICATED` |
| `provenance().physical_qpu` | `False` |
| `provenance().physical_parallel` | `False` |
| `provenance().physical_distributed` | `False` |
| `provenance().quantum_boundary` | `NOT_CROSSED` |
| `PHYSICAL_*_QPU_EXECUTION` | `BLOCKED_EXTERNAL_AUTHORITY`, unchanged |

`adapters.assert_not_physical()` runs in every adapter constructor and refuses
any adapter in this package that claims a physical trust domain or an
execution label that does not begin `CLASSICAL_` or that contains any of
`QPU`, `HARDWARE`, `PHYSICAL`, `DEVICE`, `QUANTUM_EXECUTION`. Three negative
checks in the test suite attack that firewall directly and MUST be refused.

**Binding a classical VM is not a step toward a physical quantum claim, and
SHALL NOT be presented as one.** `adapter-check` keeps the two paths
separate: `--target bottle-rocket` returns a classical attestation;
`--target <anything else>` still returns `BLOCKED_EXTERNAL_AUTHORITY`.

---

## 2. What it adds that PA21.2 did not have

Every executed check in PA21.2 ran inside the same CPython process as the code
under test. 606 PA-MATH checks and 1,272 conformance cases, all in one
interpreter, all exercising one implementation.

This backend adds a second, independent implementation path:

```
PA-LCTL source
  -> lang.parse / lang.verify          (CPython reference core)
  -> brlower.lower                     (PA-LCTL/BR-LOWERING/1)
  -> LCTLC/1.1 source
  -> bradmin verify-lctlc              (native semantic verifier, C)
  -> bradmin lctl-to-brir              (native lowering to BRIR/1.1)
  -> bradmin compile-lctlc             (native BRIM/1 image)
  -> brverify IMAGE SOURCE BRIR        (independent verifier, separate program)
  -> bradmin keygen / sign-image       (Ed25519)
  -> bradmin verify-image              (signature check)
  -> bradmin run-signed                (native execution under a step bound)
```

The value is in the last comparison: the witness is computed **twice**, once
by CPython and once by the VM's own ISA, and the adapter refuses to report a
result if they disagree. That is a differential check between two independent
implementations — a class of evidence this release previously had none of.

---

## 3. The row-sequence witness (`PA-LCTL/BR-LOWERING/1`)

### 3.1 Definition

For a verified `Program` with rows `r₀ … rₙ₋₁` rendered in canonical (sealed)
order:

```
w(r)  = low 64 bits of sha256(r.render(program.columns))
acc₀  = 1469598103934665603                       (FNV-1a 64 offset basis)
accᵢ₊₁ = ((accᵢ × 1099511628211) mod 2⁶⁴) XOR w(rᵢ)
witness = accₙ
```

The lowered LCTLC unit performs exactly this: `MOVI` the seed and the prime,
then three instructions per row (`MUL`, `MOVI`, `XOR`), then `MOV R2` and
`HALT`. `R2.low64` as reported by the production host is the witness.

`w` is taken over the same bytes the bundle's seal is computed over, so the
witness is a function of the sealed content of every row, in order.

### 3.2 What it proves

* The row sequence survived lowering, native compilation, image construction,
  independent verification, signing and execution **intact**. Any change to
  any cell of any row, any reordering of two rows, and any insertion or
  deletion of a row changes the witness. Four negative checks assert this.
* The program terminated inside a **declared step budget** (`max_steps` is
  derived from the row count, not padded).
* The image that executed was the image that was verified and signed.

### 3.3 What it does not prove

It says nothing whatever about quantum semantics. It does not simulate a
circuit, does not compute an amplitude, and does not produce a measurement
distribution. A caller SHALL NOT present the witness as an execution result
for the quantum face of a bundle.

---

## 4. Bounds (§ adapter spec 3, `SUPPORTED_WITH_LIMITS`)

Measured from the toolchain, not assumed:

| Bound | Value | Source |
|---|---|---|
| instructions per unit | 256 | `N05`, `src/brlctlc.c` |
| source lines per unit | 512 | `ALINES`, `src/brlctlc.c` |
| instructions per PA-LCTL row | 3 | this lowering |
| prologue + epilogue | 4 | this lowering |
| **rows per lowering** | **84** | `(256 − 4) / 3` |
| shots | 1 | the witness is deterministic |
| qubits | 0 | the target has none |

Exceeding a bound is an **error, not a degradation**. `brlower.lower()` raises
`LoweringRefused`; the adapter converts that to `AdapterRefusal`; the CLI exits
non-zero. Nothing is truncated and no partial witness is reported. A negative
check submits an oversized bundle and asserts the refusal.

`shots > 1` is likewise refused rather than answered: repeating a
deterministic computation and reporting the repetitions as samples would be a
false claim about what was measured.

---

## 5. Command surface

```
export PA_LCTL_BOTTLE_ROCKET_ROOT=/path/to/BOTTLE_ROCKET_5.0.0_VM_110K_RC

python3 -m reference.pacore.cli adapter-check --target bottle-rocket
python3 -m reference.pacore.cli backend-run <program.pal>
python3 -B pamath/tests/test_bottlerocket_backend.py
```

`backend-run` parses, verifies, and only then executes. A program that does
not verify is **not** executed, and the command exits `REJECTED` with the
diagnostics. If the toolchain is absent or unbuilt the adapter refuses rather
than falling back to an interpreter: there is no silent substitution.

The test suite **SKIPS loudly and exits non-zero** when the toolchain is
absent. It never reports a pass it did not earn.

---

## 6. Evidence

`pamath/tests/test_bottlerocket_backend.py` — 30 checks, executed:

| Group | Checks |
|---|---|
| the physical firewall (3 negatives + 5 positives) | 8 |
| refusal on a missing toolchain | 1 |
| native execution and the differential check | 5 |
| witness sensitivity to the sealed row sequence | 6 |
| stated bounds are refusals | 4 |
| provenance carries no physical claim | 6 |

The BOTTLE ROCKET package's own gates were re-run on this host before the
backend was accepted: ISA/ABI conformance (32 opcodes, 20 services, 14
required traps) PASS; wide-state conformance 59 requirements PASS; device-I/O
conformance 66 requirements PASS; Core-61 49,072/61,000 bytes PASS;
BRIM-51 272/51,200 PASS; production source boundary 107,675/110,000 PASS.

**The target's own blockers are inherited, not laundered.** BOTTLE ROCKET
5.0.0 self-reports `strict_gate: BLOCKED` with 88/96 requirements
OPERATIONAL and 8 BLOCKED — independent second-machine replay and rebuild,
AArch64 and Windows execution, and a literal 72-hour soak. `attest()` restates
those verbatim under `target_self_reported_gate`. Binding this target does not
close them and this document does not claim it does.

---

## 7. Roadmap

* **Loop-based lowering.** The 84-row bound comes from emitting three
  instructions per row. A `LOAD`/`JNZ` loop over an image data section would
  make the instruction count independent of row count and lift the bound by
  orders of magnitude.
* **Classical face execution.** The witness is a checksum, not an
  interpretation. Lowering the genuinely classical rows — measurement-feedback
  control flow, classical bit arithmetic, budget accounting — would let the VM
  execute the parts of a PA-LCTL bundle that *are* classical, rather than
  attesting to their presence.
* **Device ABI.** BOTTLE ROCKET's storage, monotonic and diagnostic devices
  could carry PA-LCTL ledger emission natively under its per-run quotas.
