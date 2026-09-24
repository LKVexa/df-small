# DF_Small (N_SMALL) -- capability ledger

Release DF-PA21.2-1.0.0. Rule: No item is operational because its source file exists. Operational status requires native executable evidence satisfying that item's promotion gate.

Distribution: BLOCKED 3, BLOCKED_CAPABILITY_ABSENT 1, BLOCKED_EXTERNAL_AUTHORITY 1, OPERATIONAL 16, VERIFIED 1. `operational_without_evidence`: []; `operational_without_passing_gate`: [].

| item | title | status | statement | gates |
|---|---|---|---|---|
| `DFN-01` | container integrity | `VERIFIED` | every delivered byte is hashed (SHA256SUMS.txt) and the MANIFEST inventory matches disk | `G0.1`=SKIPPED, `G0.2`=SKIPPED |
| `DFN-02` | payload byte-identical | `OPERATIONAL` | the embedded VM package equals the source zip content, file by file | `G0.3`=PASS |
| `DFN-03` | one core, pinned | `OPERATIONAL` | core/reference/pacore equals the pinned digest shared by all five containers | `G0.4`=PASS |
| `DFN-04` | schemas and citations | `OPERATIONAL` | every JSON artifact validates against its shipped schema; every ledger citation resolves | `G2`=PASS, `G3`=PASS |
| `DFN-05` | reference core selfcheck | `OPERATIONAL` | python3 -B -m reference.pacore.cli selfcheck -> SELFCHECK_PASS | `G1`=PASS |
| `DFN-06` | toolchain preflight | `OPERATIONAL` | REQUIREMENTS.txt is checked first; missing tools become SKIPPED diagnostics | `N0`=PASS |
| `DFN-07` | the VM's own build and acceptance gate | `OPERATIONAL` | `make clean operational` reproduces from the delivered bytes with stock flags; the VM's own sums verify afterwards (SHA256SUMS.txt (19 entries)) | `N1`=PASS |
| `DFN-08` | adapter attestation | `OPERATIONAL` | PA-LCTL/TARGET_ADAPTER/1 identity: LOCAL_TRUSTED, CLASSICAL_ label, empty gate set, no physical flag, toolchain digests, target self-report restated | `A1`=PASS |
| `DFN-09` | physical firewall | `OPERATIONAL` | three physical claims are refused at adapter construction | `A2`=PASS |
| `DFN-10` | row-sequence witness, native + differential | `OPERATIONAL` | the sealed row sequence of every shipped bundle executes natively and equals the CPython reference | `A3`=PASS |
| `DFN-11` | witness sensitivity | `OPERATIONAL` | cell change / reorder / delete / insert each change the native witness | `A4`=PASS |
| `DFN-12` | bounds are refusals | `OPERATIONAL` | max_rows+1 is refused with SUPPORTED_WITH_LIMITS stated; max_rows executes | `A5`=PASS |
| `DFN-13` | declared step budget | `OPERATIONAL` | an unbounded loop traps instead of running | `A6`=PASS |
| `DFN-14` | deterministic replay | `OPERATIONAL` | same rows -> same source, same image, same witness, twice | `A7`=PASS |
| `DFN-15` | fail-closed refusals | `OPERATIONAL` | shots>1, wrong QCIR-P2 schema, empty rows are refused | `A8`=PASS |
| `DFN-16` | native guest program | `OPERATIONAL` | examples/add42.mssl compiles, signs, verifies, runs and returns 42 | `A9`=PASS |
| `DFN-20` | segmented witness across nodes | `OPERATIONAL` | a bundle longer than the bound is executed as a chain of segments in the fabric | - |
| `DFN-30` | classical-face execution | `BLOCKED_CAPABILITY_ABSENT` | executing the classical rows of a bundle rather than witnessing them | - |
| `DFN-31` | quantum-face execution | `BLOCKED` | the machine has no qubit; every quantum feature is UNSUPPORTED | - |
| `DFN-32` | cross-machine federation | `BLOCKED` | NETWORK=deny; the fabric is executed on one host | - |
| `DFN-33` | physical quantum outputs | `BLOCKED_EXTERNAL_AUTHORITY` | PHYSICAL_PARALLEL_QPU_EXECUTION, PHYSICAL_DISTRIBUTED_QPU_EXECUTION | - |
| `DFN-34` | the target's own blockers | `BLOCKED` | card target/AID; production trust authority; NVM/watchdog; hardware/security qualification; deployment authorization | - |

Statuses follow the corpora's vocabulary. `VERIFIED` is used for exactly one item -- container integrity -- whose hash gates can only run after the seal and therefore cannot be cited from inside the container (they passed in the post-seal `./VERIFY`, recorded in the delivery's `_assembly/` folder, and run first in every `./VERIFY`). `IMPLEMENTED` means the code exists but its gate did not pass or could not run on the assembly host; `SPECIFIED` means stated but not exercised; `BLOCKED*` means what it says, with the reason in `DF_BLOCKED_REGISTER.md`.
