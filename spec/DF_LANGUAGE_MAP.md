# DF language map -- how each VM concept is expressed in the PA-LCTL fabric

Release DF-PA21.2-1.0.0. Rules T1-T12 are stated in `authority/DF_SOURCE_AUTHORITY.json`.

| VM concept | PA-LCTL / fabric construct | rule |
|---|---|---|
| a container VM (`*.zip`) | a DF node container; the package embedded byte-identical under `vm/` | T1 |
| the VM itself | `fabric.Worker` (one worker per running instance) + `DECLARE_NODE` row | T2 |
| the VM's ISA lineage | `fabric.WorkerGroup` (`G_BR3`, `G_ISA41`, `G_BR11`, `G_QVM1`) | T2 |
| the VM's vendor lineage | `fabric.ExecutionDomain` (`D_BR`, `D_QVM`) + `DECLARE_DOMAIN` | T2 |
| the set of four | `fabric.Federation` `DF0` + `DECLARE_FEDERATION`; `DECLARE_TOPOLOGY` nodes=4 domains=2 qcapacity=0 | T2, T12 |
| a VM instance's process | `Worker.failure_domain` (one per node) | T2 |
| RAM per live VM (measured) | `ResourceLimits.memory_bytes`; `qpu_slots=0`, `ebit_budget=0` | T2 |
| the VM's CLI (assemble/compile/sign/verify/run) | the adapter ABI `PA-LCTL/TARGET_ADAPTER/1`: `submit`, `result`, `provenance` | T3 |
| the VM's package info / evidence | `attest()` restates it verbatim under `target_self_reported` | T3, T8 |
| a guest program | a lowered witness unit in the node's own dialect (MSSL/ASM-1, LCTLC/1.1, LCTLC/1.2, LCTLC/1.0) | T4 |
| a signed image | never moved between nodes; produced and consumed on one node (`F7`) | T4 |
| the CLI step budget | `declared_step_budget` (`SUPPORTED`); `Task.timeout_ticks` in the fabric is logical, not wall-clock (hole H8) | T5 |
| 256 instructions per image | `max_rows_per_lowering = 84`; segments chained by accumulator | T5 |
| the result register (R2 / R0) | the witness; compared with `brlower.reference_witness` -- the differential check | T4 |
| `make operational` / `VERIFY_ALL.sh` | gate `N1` (re-run from the delivered bytes, stock flags, scratch copy) | T6 |
| `SHA256SUMS` of the VM | gate `G0.3` (payload pinned) + the VM's own sums checked after its gate | T1, T6 |
| `strict_gate: BLOCKED`, `EXTERNAL_NOT_QUALIFIED`, `CONDITIONAL_EXTERNAL_AUTHORITY` | inherited blockers, verbatim, in `DF_BLOCKED_REGISTER.md` and `attest()` | T8 |
| Ed25519 keygen / sign / verify | feature class `image_signing` (`SUPPORTED` / `SUPPORTED_WITH_LIMITS`) | T7 |
| an independent verifier (brverify, brim_verify.py) | feature class `independent_image_verification` | T7 |
| the audit's BUILD / VERIFY / RUN | exactly those three launchers, plus `.cmd` twins | T10 |
| the audit's REQUIREMENTS.txt + preflight | `REQUIREMENTS.txt`; gate `N0` runs first; missing tools -> SKIPPED with a reason | T10 |
| the audit's shared `core/` with one digest | `core/reference/pacore` + `PACORE_DIGEST.json`, identical in all five containers (fabric gate `G0.4`) | T11 |
| `mssl_to_lctlc.py` | `DF_Fabric/tools/`; fabric gate `F9` runs a Small witness program on Medium after translation | T12 |
| four VMs run at once | `TaskRuntime` under the four `ExecutionProfile`s; `EventLog` sealed + replayed; BSP supersteps; collectives with explicit schedules | T12 |
| a distributed claim | `distributed_state <= DISTRIBUTED_CLASSICAL_EMULATION`; cross-machine `BLOCKED` (`NETWORK=deny`) | T9, T12 |

## The translation rules

* **T1** (container). One VM package becomes one DF node container: the package is embedded byte-identical under vm/<package>/ and pinned by node/PAYLOAD_DIGEST.json; nothing inside it is edited.
* **T2** (identity). The VM becomes a fabric node (DECLARE_NODE) with a stable node_id, worker_id, worker group (its ISA lineage), execution domain (its vendor lineage) and federation DF0, expressed as a PA-LCTL bundle (node/NODE.pal) and as pacore.fabric objects (Worker/WorkerGroup/ExecutionDomain/Federation).
* **T3** (adapter). The VM is bound through the PA-LCTL target-adapter ABI (PA-LCTL/TARGET_ADAPTER/1): attest / topology / feature_class / limits / calibration / submit / result / provenance, in the shape of pacore.adapters.bottlerocket, failing closed.
* **T4** (execution). The unit of execution moved to a node is the sealed PA-LCTL row sequence, lowered to the node's own guest dialect as a row-sequence witness (DF/ROW_WITNESS_LOWERING/1, generalising PA-LCTL/BR-LOWERING/1 by a caller-supplied accumulator); images are never moved between nodes.
* **T5** (bounds). Every measured bound of the VM (instructions per image, step budget, memory) becomes a stated limit; exceeding a limit is a refusal (SUPPORTED_WITH_LIMITS), never a truncation.
* **T6** (gates). The VM's own build and acceptance gate are re-run from the delivered bytes with the VM's stock flags; only observed results are recorded, in conformance/DF_GATE_RESULTS.json.
* **T7** (status). Capabilities are stated in the corpora's status vocabulary (BLOCKED / SPECIFIED / SCAFFOLDED / IMPLEMENTED / VERIFIED / OPERATIONAL / QUALIFIED, plus BLOCKED_EXTERNAL_AUTHORITY and BLOCKED_CAPABILITY_ABSENT); OPERATIONAL requires a passing gate cited by path.
* **T8** (blockers). The VM's self-reported blockers are restated verbatim and inherited, never laundered; binding a VM to the fabric closes none of them.
* **T9** (firewall). No adapter may claim PHYSICAL_TARGET_AUTHENTICATED or an execution label outside CLASSICAL_*; PHYSICAL_PARALLEL_QPU_EXECUTION and PHYSICAL_DISTRIBUTED_QPU_EXECUTION remain BLOCKED_EXTERNAL_AUTHORITY; NETWORK=deny and BACKEND=none are inherited unchanged.
* **T10** (entry points). Every container exposes exactly BUILD / VERIFY / RUN (plus .cmd twins) and a REQUIREMENTS.txt checked first by VERIFY, so a missing dependency is a diagnostic (SKIPPED with a reason), not a failure.
* **T11** (core). The PA-LCTL reference core (reference/pacore, spec/, examples/) is carried byte-identically in every container under core/ and pinned by core/PACORE_DIGEST.json; the fabric container checks that the same digest appears in all five.
* **T12** (fabric). The four nodes are federated by DF_Fabric: a pacore.fabric Federation, TaskRuntime programs in all four execution profiles, one append-only EventLog sealed and replay-checked, BSP supersteps and collectives with explicit transfer schedules; distributed_state is capped at DISTRIBUTED_CLASSICAL_EMULATION (one host, local processes).
