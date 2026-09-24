# DF_Small -- the embedded VM as measured on the assembly host

> Produced while profiling `Small.zip` before translation (DF-PA21.2-1.0.0). Paths like `/home/claude/work/...` are the assembly host's scratch copies of the package; every claim below was observed there, and the reproducible parts are re-run by `./VERIFY` (gates N1, A1-A9) from the delivered bytes.

---

# Profile: Small — BOTTLE ROCKET 3.0.0-MODEL

Profiled in cloud sandbox on 2026-08-16. Source (read-only):
`/home/claude/work/vms/Small/BOTTLE_ROCKET_3.0.0_MODEL_OPERATIONAL_110K`.
Built + measured in: `/home/claude/work/build/Small`. Logs: `/home/claude/work/profiles/Small_gates/`.

## Identity
- **Package**: `BOTTLE_ROCKET_3.0.0_MODEL_OPERATIONAL_110K` (record `BOTTLE_ROCKET.ReleaseManifest`).
- **VM name / version**: **BOTTLE ROCKET**, version **3.0.0-MODEL**.
- **What it is**: an offline, self-contained **C11 "SIM-core" virtual machine** — a smart-card / UICC-style
  secure element modeled in software. It has a wide-word ISA, a capability model, signed loadable images
  (BRIM), an APDU command protocol, and a persistent host with durable state, atomic update, and recovery.
- **Qualification**: `MODEL_OPERATIONAL` (vendor-neutral host model). The physical SIM/UICC is
  explicitly **EXTERNAL_NOT_QUALIFIED**. This is a reference/host model, NOT a card-qualified product.

## Runtime contract (ISA / word / registers / image / guest language)
- **ABI**: `BRIM/1-v3 + APDU/1 + MSSL/ASM-1` (frozen in `src/brvm.h` + `spec/BR_SPEC.json`).
- **Word width**: **1,048,576 bits** = 16,384 × uint64 limbs = **128 KiB per word** (`BR_WORD_BITS`, `BR_LIMBS`).
- **Registers**: **16** (R0–R15), each a full 1,048,576-bit word.
- **Stack**: **256** entries (`BR_STACK_WORDS`), each a full word; PUSH/POP move whole words.
- **Memory**: **4096 bytes** (`BR_MEMORY_BYTES`), byte-addressable; LOAD/STORE deliberately move only the
  low 64 bits; `.data` and the SVC crypto services operate on this byte memory.
- **Capabilities**: 16 slots (`BR_CAPS`), 8 defined bits: CONTROL(1) ARITH(2) MEMORY(4) STACK(8)
  SERVICE(16) STATE(32) UPDATE(64) DIAG(128). Grants are volatile (never survive reset).
- **Opcodes (24)**: NOP MOVI MOV JMP JZ JNZ HALT | ADD SUB MUL DIVU MODU AND OR XOR NOT SHL SHR CMP |
  LOAD STORE | PUSH POP | SVC. Opcode class → required cap (CONTROL/ARITH/MEMORY/STACK/SERVICE).
- **Arithmetic modes (4)**: WRAP, CHECKED, SATURATE, TRAPPING (per-instruction; CHECKED/TRAPPING trap on
  overflow, SATURATE clamps, WRAP truncates at width).
- **SVC services (9)**: YIELD, STATUS, REVOKE, DELEGATE(subset-only), CONFIGURE, COMMIT(generation++),
  DIAG_EVENT, **SHA256**, **ED25519_VERIFY**. SVC 4–5 also need STATE, 6 needs DIAG, 8 needs UPDATE.
  No guest *signing* exists (verify only).
- **Instruction encoding**: fixed **16 bytes** — `op, mode, rd, ra, rb, cap, flags(u16), imm(u64)` (LE).
- **Traps (16)**: NONE BAD_IMAGE BAD_OPCODE CAPABILITY BOUNDS OVERFLOW DIV_ZERO STACK BUDGET CANCEL OOM
  STATE REPLAY INTEGRITY TRUST PROTOCOL. **Status**: READY(0) RUNNING(1) HALTED(2) TRAPPED(3) CANCELLED(4).
- **Image format — BRIM/1-v3** (max **51,200 bytes**, 80-byte header). Verified by hexdump of the factory image:
  - `[0:4]="BRIM"`, `[4:7]`=major/minor/patch=3/0/0, `[7]`=flags (0x01 FACTORY, 0x02 SIGNED).
  - `[8:14]`=1,20,16,16,24,4 (self-describing regs/caps/opcodes/modes); `[14:16]`=header size 80.
  - `[16:20]`=version (nonzero u32); `[20:24]`=requested caps; `[24:28]`=authenticated padding length;
    `[28:30]`=code count; `[30:32]`=data length.
  - `[32:64]`=**SHA-256 seal** over payload (code+data+padding); `[64:79]`=ASCII tag `Q17-MODEL-OPS-3`.
  - Optional trailing **64-byte Ed25519 signature** over header+payload (signed images).
  - Admission: `granted = requested & platform_allowlist`; version must be nonzero; **lower version denied**,
    **equal version = governed deterministic reinstall**; generation wrap fails closed.
- **Guest language — MSSL / ASM-1** (`compiler:"MSSL/ASM-1"`). Text assembly, two-pass, bounded.
  Required directives: `.profile SIM_CORE`, `.image_version N`, `.request_caps A|B`; optional `.data OFFSET HEX`.
  Syntax: `OPCODE.MODE operands`, registers `Rn`, capability regs `Cn`, decimal/0x integers, labels `name:`,
  `#`/`;` comments. Assembler covers all 24 opcodes / 4 modes and rejects malformed input.

## Capabilities matrix
| Capability | Present | Evidence (measured here) |
|---|---|---|
| Compile source | **YES** | `brctl assemble prog.mssl img.brimg` → "MSSL assembly: PASS"; 24-opcode assembler selftest passes. |
| Sign images | **YES** | Ed25519 via OpenSSL EVP: `keygen` + `sign-image` PASS; `verify-image`/`run-signed` PASS. Keys are host-evaluation only. |
| Trust chain | **YES (root external)** | Issuer-key admission, signature verify, version monotonicity, `requested&allowlist` grant — all exercised. But "No production authority included"; root/AID/production trust are external + blocking. |
| Device ABI | **YES** | APDU/1 protocol (12 commands) + `serve` loop (one hex APDU frame per line) + `br_platform` read/write adapter. Smart-card device ABI; **no network/telemetry** (offline). |
| Deterministic replay | **YES** | `repro` gate: two assemblies byte-identical; rebuilt factory image bit-identical to shipped. Equal-version deterministic reinstall; txid-monotonic replay protection (REPLAY trap / SW 0x6985); dual-CRC state slots; recover/rollback proven in selftest. |
| Step bound | **YES** | Instruction budget (default **4096**); CLI `run` uses 4096; APDU EXEC takes a u32 budget; `br_vm_run(vm,budget)`. Exhaustion → trap 8 (BUDGET) — reproduced with an infinite loop. |

## Toolchain + dependencies
- **Requires**: POSIX, C11, GNU Make, **OpenSSL 3** (`-lcrypto`). Measured against: gcc 13.3.0, OpenSSL 3.0.13, GNU Make 4.3.
- **Stock flags** (no overrides used): `-std=c11 -O2 -Wall -Wextra -Werror -fno-common -fstack-protector-strong`,
  `-Isrc`, `-lcrypto`. Sanitize target adds `-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer`.
- **OpenSSL surface**: one-shot `SHA256()` (from `<openssl/sha.h>`; NOT deprecated in OpenSSL 3, so `-Werror`
  build is clean), `EVP_PKEY_ED25519` sign/verify/raw-key, `RAND_bytes`, `OPENSSL_cleanse`.
- **Compiler builtins**: `unsigned __int128`, `__builtin_clzll` (GCC/Clang). No other third-party libs.

## Entry points and exact commands
- **Build**: `make` → `.build/brctl`, `.build/br_tests`, `.build/br_sizes`.
- **Canonical gate (per MANIFEST `verify`)**: `make clean operational && make sanitize`.
  - `operational` = `verify` + `check-dist-size`; `verify` = `test` + `check-size` + `repro` + `signed-smoke`
    + `inspect-image` + `run` + `br_sizes`.
- **brctl CLI surface** (from usage string):
  ```
  brctl selftest
  brctl assemble SRC IMAGE [--image-version N]
  brctl inspect-image IMAGE
  brctl run IMAGE
  brctl keygen PRIVATE PUBLIC
  brctl sign-image INPUT OUTPUT PRIVATE
  brctl verify-image FILE PUBLIC
  brctl run-signed FILE PUBLIC
  brctl update SIGNED PUBLIC --state STATE
  brctl save-state STATE | recover-state STATE [PUBLIC] | status --state STATE
  brctl serve --state STATE [--issuer-public-key PUBLIC]
  ```

## Build & gate results measured here
All commands run in `/home/claude/work/build/Small` with stock Makefile flags. Timings are wall-clock (2 vCPU).

| Gate | Command | Exit | Time | Result |
|---|---|---|---|---|
| Build | `make` (all) | 0 | 3.64 s | 3 binaries built; **warning-clean under `-Werror`**. |
| Unit/self-test | `make test` | 0 | 14.12 s | `{"suite":"BOTTLE_ROCKET_3.0.0_MODEL","failures":0,"result":"PASS"}`. |
| Full gate | `make clean operational` | 0 | 27.20 s | All sub-gates PASS (below). |
| Sanitizers | `make sanitize` (ASan+UBSan) | 0 | 19.42 s | `failures:0` — no ASan/UBSan diagnostics. |
| Integrity | `sha256sum -c SHA256SUMS.txt` (source) | 0 | — | **19 OK / 0 bad** (binds all files except itself). |

Sub-gates inside `operational` (all observed PASS):
- `test` → 0 failures.
- `check-size` → BRIM 176 B ≤ 51,200.
- `repro` → two assemblies `cmp` identical (deterministic).
- `signed-smoke` → keygen/sign-image/verify-image/run-signed(R2=42)/update/recover-state/status/serve all PASS;
  `serve` HELLO returned `9000…` (SW_OK) with word_bits=1048576, opcodes=24, caps=16.
- `check-dist-size` → **distribution_uncompressed_bytes=109905, ceiling=110000** (95 B headroom).

**Reproduced package claims**: SIZE.json (109905/110000 dist, 176/51200 BRIM) reproduced exactly; MANIFEST
deploy hash `5b5cfceb…` reproduced bit-for-bit by rebuild; `repro` byte-identical; OPERATIONAL.json's
"verified" list (24 opcode positive+denial paths, 9 services, 4 arith modes, wide-word boundaries, MSSL
parse/reproduce/reject, capability intersection, guest SHA256+Ed25519, 51200-byte fragmented signed update,
recover/replay/rollback, interrupted-stage rejection, all 12 APDUs, 10,000 fuzz cases, ASan+UBSan,
warning-clean build, size gates) is exactly what the selftest + gates exercise, and they pass.

## How to drive it programmatically as a fabric target
Contract: **compile → run under a step bound → read result register from stdout.**

1. **(a) Compile MSSL text → image**
   ```sh
   .build/brctl assemble prog.mssl prog.brimg [--image-version N]
   # stdout "MSSL assembly: PASS"; exit 0 on success, 1 on reject, 2 on bad args.
   ```
2. **(b) Run under a step bound**
   ```sh
   .build/brctl run prog.brimg          # fixed budget = 4096 instructions
   ```
   For a *custom* budget, drive the C API `br_vm_run(vm, budget)` or the APDU **EXEC** (cmd 4) with a 4-byte
   u32 budget via `serve`. Exhausting the budget yields trap 8 (BUDGET), status 3 (TRAPPED).
3. **(c) Read result / witness**
   Parse the two stdout lines:
   ```
   status=<u> trap=<u> ip=<u> image_version=<u> result=PASS|FAIL
   R2.low64=<u64>
   ```
   Convention: the guest leaves its result in **R2**; the runner prints R2's low 64 bits. `status=2`
   (HALTED) + `trap=0` + `result=PASS` = clean success; exit code 0. For arbitrary readback (any register,
   memory, or stack slot, ≤512 B) use `serve` + APDU **STATE** `{kind,index,offset:u32,length:u16}`.

   Verified example (`examples/boot.mssl`, computes 40+2): `status=2 trap=0 ip=6 image_version=3 result=PASS`,
   `R2.low64=42`. Custom check (123456×1000): `R2.low64=123456000`.

**Bounds (hard limits):**
- Program size: **≤256 instructions** (`BR_MAX_CODE`; 257 rejected, 256 accepted → 4176-byte image).
- Image size: **≤51,200 bytes**; header fixed 80 B; each instruction 16 B.
- Guest memory: **4096 bytes**; `.data`/INPUT bounded to it; LOAD/STORE offset ≤ 4096−8.
- Default step budget **4096**; service budget **64** SVCs/run.
- Update: ordered fragments ≤4096 B each, ≤51,200 B staged total; monotonic txid (replay-protected).
- Wide state: 16 registers + 256 stack entries, each 1,048,576-bit.
- **RAM per VM instance ≈ 34.3 MB** (`br_sizes`: allocated_wide_bytes 35,913,728 + vm_inline 12,816 =
  35,926,544). Relevant if a fabric node hosts many concurrent VMs.

## Self-reported status / blockers (from its own docs)
- Classification **MODEL_OPERATIONAL** (host model); **physical SIM/UICC EXTERNAL_NOT_QUALIFIED**.
- Blocking **external gates** (README/AUDIT_DIGEST): card target/AID, production trust authority, NVM/watchdog,
  hardware/security qualification, deployment authorization. "No production authority is included."
- Absent by design: network/telemetry/desktop/cloud facilities; no guest signing.
- Evidence files self-declare `gate: PASS` for the model and `EXTERNAL_NOT_QUALIFIED` for the physical layer.

## Files of note
- `src/brvm.c` (1571 lines) — core VM: wide-word ALU, ISA step/run, BRIM image load/write/inspect, Ed25519/SHA,
  persistence (dual-CRC slots), APDU dispatch, and the entire selftest battery.
- `src/brasm.c` — MSSL/ASM-1 two-pass assembler. `src/brvm.h` — frozen ABI constants/enums. `src/brasm.h`.
- `host/brctl.c` — CLI (assemble/inspect/run/keygen/sign/verify/update/recover/status/serve).
- `host/br_sizes.c` — resource measurement. `tests/test_main.c` — thin selftest wrapper.
- `examples/boot.mssl` — canonical 6-instruction example. `deploy/…MODEL.brimg` — factory image (176 B).
- `spec/BR_SPEC.json`, `spec/PROFILE.md`, `src/CORE.mssl` — frozen policy/profile.
- `MANIFEST.json`, `SHA256SUMS.txt`, `evidence/{AUDIT_DIGEST,OPERATIONAL,SIZE}.json`, `evidence/q17/SIDECAR.json`.

## Anything surprising or inconsistent
- **Enormous words for a "SIM core"**: 1,048,576-bit (128 KiB) registers/stack; full-width bignum ADD/MUL/
  DIVMOD/shift, yet LOAD/STORE intentionally move only the low 64 bits. The "Small" package nonetheless costs
  **~34 MB RAM per live VM** despite a 176-byte image.
- **Size gate is razor-thin**: 109,905 / 110,000 bytes — only 95 B of headroom; almost any source edit blows it.
- **Result readback is hardwired to R2.low64** in the CLI; no flag to pick another register — arbitrary readback
  requires the `serve`/APDU STATE path.
- **`--image-version` changes the header but not the payload SHA-256 seal** (version lives outside the sealed
  payload), so two images differing only in version share the same inspect `digest` yet differ on disk.
- **CLI `run` is unauthenticated**: it loads factory or signed images without a key unless you use `run-signed`;
  trust/signature enforcement lives only on the signed/update/serve paths.
- **Internal codename "Q17"**: every image embeds the ASCII tag `Q17-MODEL-OPS-3` at offset 64, and there is an
  `evidence/q17/` sidecar.
- Exit-code convention: `assemble`/`run` return 0 (PASS) / 1 (FAIL) and print PASS/FAIL to stdout; unknown
  command → exit 2 with a usage string.
