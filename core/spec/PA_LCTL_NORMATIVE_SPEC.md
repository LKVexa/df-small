# PA-LCTL Normative Specification (master)

Document: `PA_LCTL_NORMATIVE_SPEC.md`
Language: **PA-LCTL** (`pacore.LANGUAGE`)
Profile: `pa.lctl.quantum.parallel.distributed` (`pacore.PROFILE`)
Bundle magic: `#PA-LCTL/1.6` (`pacore.BUNDLE_MAGIC`)
Core version of the reference implementation: `1.6.0-rc1` (`pacore.CORE_VERSION`)

The key words **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT** and **MAY**
are to be interpreted as described in RFC 2119.

This specification is *descriptive of an existing implementation*. Every
normative statement below is traceable to a symbol in `pacore/`. Where the
implementation is partial, the "Implementation status" subsection says so using
the vocabulary in §11 rather than describing an intention.

---

## 1. Language thesis

### 1.1 What PA-LCTL is

PA-LCTL is a **columned tuple language for describing quantum, parallel and
distributed computation together, with the honesty of each claim carried in the
program text itself.**

A PA-LCTL bundle is not a circuit description and not a task graph. It is a
sequence of *semantic tuples*, each of which declares:

* what operation is intended (`OP`),
* on which objects (`OUT`, `CTRL`, `A`, `B`, `QSPACE`),
* under which semantic type (`TYPE`),
* under which **exactness regime** (`REGIME`),
* under which **stated assumptions** (`ASSUME`),
* with which **declared error envelope** (`ERROR`),
* at which **cost** (`RESOURCE`),
* with which **confidence** (`CONF`),
* and against which **evidence** (`PROOF`).

Three of those columns — `REGIME`, `ASSUME`, `PROOF` — are what separate
PA-LCTL from an instruction list. A row that says `H` on `q[0]` is a gate. A row
that says `H` on `q[0]` **`EXACT`** with assumption `unitarity` and no proof
reference is a *claim about a gate*, and the verifier is entitled to check it.

### 1.2 The four load-bearing commitments

1. **No-cloning is a type rule, not a runtime check.** Ownership of unknown
   quantum state is tracked statically through the program
   (`lang.OwnershipRecord`, `lang.verify`), and duplication is rejected at
   analysis time (`E-CLONE-001`, `fabric.CloneAttemptError`).
2. **Approximation is never silent.** A row **SHALL** declare its regime; an
   approximate regime **SHALL NOT** be promoted to an exact one
   (`lang.EXACT_REGIMES`, diagnostics `E-REG-002`, `E-REG-003`); an error
   composition with incompatible dimensions **SHALL** return
   `ERROR_COMPOSITION_UNRESOLVED` rather than a fabricated scalar
   (`erroralgebra.compose`).
3. **Parallelism is admitted, not assumed.** Every candidate concurrent pair
   receives a recorded decision with a reason and a proof reference
   (`ses.ConcurrencyRecord`, `ses.admit_concurrency`). Neither
   parallelization nor serialization ever happens silently.
4. **Nothing claims physical execution.** This runtime holds no authenticated
   physical quantum target. Every execution label starts with `CLASSICAL_`
   (`simulator.EXECUTION_LABELS`, `simulator._assert_label_honest`), the
   quantum boundary is always `QUANTUM_BOUNDARY_NOT_CROSSED`
   (`ledgers.LedgerSet._provenance`), and the two physical release outputs are
   permanently `BLOCKED_EXTERNAL_AUTHORITY` (`ledgers.PHYSICAL_RELEASE_OUTPUTS`,
   `cli.cmd_adapter_check`).

### 1.3 Normative environment constants

| Constant | Value | Symbol | Enforced by |
|---|---|---|---|
| `BACKEND` | `none` | `pacore.BACKEND` | `lang.verify` → `E-POL-002` |
| `NETWORK` | `deny` | `pacore.NETWORK` | `lang.verify` → `E-POL-001` |
| Bundle magic | `#PA-LCTL/1.6` | `pacore.BUNDLE_MAGIC` | `lang.MAGIC_RE`, `E-GRAM-004` |
| Column separator | BROKEN BAR `U+00A6` | `lang.CANONICAL_SEP` | `lang.parse`, `Row.render` |
| Null cell | `-` | `lang.NULL_CELL` | `lang.normalize_cell` |

A conforming implementation **SHALL** reject a program whose `#NETWORK`
directive is not `deny` and whose `#BACKEND` directive is not `none`. No module
in `pacore` opens a socket, resolves a name, or contacts a backend; the
"nodes", "links" and "domains" are logical names inside one deterministic
process (`fabric` module docstring, `protocols` module docstring).

---

## 2. Architecture: bundle, module, frame, tuple

PA-LCTL has exactly four structural levels. Only the tuple level is syntactic;
the other three are the units of identity, analysis and evidence.

### 2.1 Tuple

The **tuple** is one row: 22 `¦`-separated cells, parsed into `lang.Row`. It is
the atomic unit of semantics, of diagnostics (every `Diagnostic` carries a
`row`), and of SES node identity (`ses.SESNode.id == Row.row_id`).

Normative properties:

* A tuple **SHALL** have a `ROW` identity unique within the bundle
  (`E-GRAM-003`).
* A tuple **SHALL** name a `FACE` from `lang.FACES` (`E-FACE-001`) and an `OP`
  from `lang.ALL_OPS` (`E-OP-001`).
* Every other column **MAY** be the null cell `-`.

### 2.2 Frame

A **frame** is the set of tuples sharing a `LANE`. A frame is the declared
scheduling or subsystem grouping: lane membership places a node into the SES
`task` scope (`ses.build_ses`, `scopes["task"]`), and lane order is treated as
**declared user order, not a hidden dependency** — an ordering edge is added
only for `REGION_BEGIN` / `REGION_END` rows, with reason `USER_ORDER`
(`ses.build_ses`).

An implementation **SHALL NOT** infer a data dependency from lane adjacency.

### 2.3 Module

A **module** is the set of tuples sharing a `DOMAIN` (execution domain) and, at
finer grain, a `NODE`. Modules are the unit of:

* ownership locality (`E-OWN-003`: a local operation on an object owned by
  another node is rejected),
* memory-domain assignment in the SES (`SESNode.memory_domain`),
* placement hierarchy (`planner.PLACEMENT_LEVELS`),
* federation structure (`fabric.ExecutionDomain`).

### 2.4 Bundle

A **bundle** is one source file: magic line, directives, `#COLUMNS`, comments
and rows, parsed into `lang.Program`. The bundle is the unit of **sealing**:
`Program.canonical_text()` produces a deterministic canonical serialization
(magic, directives sorted by key, canonical `#COLUMNS`, rows rendered with
`CANONICAL_SEP`) and `Program.seal()` is its SHA-256.

A conforming implementation **SHALL** produce a byte-identical
`canonical_text()` for two bundles that differ only in separator alias,
whitespace, comment lines and directive order. This is what makes the seal an
identity rather than a checksum of formatting.

---

## 3. The layered stack

Each layer consumes the layer above and adds exactly one kind of judgement.
Layer *n* **SHALL NOT** be entered when layer *n−1* has produced a `REJECT` or
`ERROR` diagnostic (`conformance.admit` fails closed at the first failing
stage; `ledgers.LedgerSet.build` raises `LedgerError`).

| # | Layer | Module | Input | Adds | Output artifact |
|---|---|---|---|---|---|
| 0 | Lexical / grammar | `lang.parse` | source text | tuple structure | `lang.Program` + seal |
| 1 | Static semantics | `lang.verify` | `Program` | types, arity, ownership, policy | `lang.VerifyResult` |
| 2 | Semantic Execution Supergraph | `ses.build_ses` | `Program`, `VerifyResult` | typed causal edges, 11 scopes | `ses.SES` + hash |
| 3 | Partial order | `ses.analyze` | `SES` | W, D, Pmax, exact width | `ses.PartialOrder` |
| 4 | Commutation authority | `commutation.CommutationAuthority` | SES node pairs | proof-gated reorder verdicts | `ProofRow` ledger |
| 5 | Concurrency admission | `ses.admit_concurrency` | SES, PO, authority, topology | recorded PARALLEL/SERIALIZE decisions | `ConcurrencyRecord[]` |
| 6 | Partitioning | `planner.HypergraphPartitioner` | SES | 12-dimension cost vector, Pareto front | `PartitionResult` |
| 7 | Placement | `planner.Placer` | partition, topology | per-partition placement proofs | `PlacementProof[]` |
| 8 | Routing | `planner.ClassicalRouter`, `planner.QuantumRouter` | topology, placement | classical and entanglement route plans | `Route[]` |
| 9 | Temporal scheduling | `planner.TemporalScheduler` | all of the above | 15-stage ledger, makespan, coherence | `Schedule` + hash |
| 10 | Numerics | `simulator` | `Program` | exact classical simulation | labelled result dict |
| 11 | Protocol fabric | `protocols` | `Program` | ebit lifecycle, executed protocols | `ProtocolPlan`, results |
| 12 | Execution fabric | `fabric` | tasks / dataflow / BSP / async | real local parallel execution | `EventLog` + replay |
| 13 | Consistency | `crdt` | classical replicas | CRDT laws, consistency contracts | `ContractVerdict` |
| 14 | Resilience | `resilience` | scenarios | injection, classification, supervision | campaign entries |
| 15 | Error algebra | `erroralgebra` | declared error terms | composition laws, Monte Carlo | `CompositionResult` |
| 16 | Ledgers / IR | `ledgers` | everything above | QCIR-P2 + 34 ledger files | `LedgerSet` + digest |
| 17 | Conformance | `conformance` | the whole stack | executed positive/negative cases | conformance report |
| 18 | Command surface | `cli` | user invocation | OPERATIONAL / REJECTED / BLOCKED | JSON envelope + exit code |

### 3.1 Ordering invariant

For any bundle *B*, the following **SHALL** hold in a conforming
implementation:

```
parse(B) = (P, D)          and  no d in D has severity in {ERROR, REJECT}
verify(P) = V              and  V.ok
build_ses(P, V) = G        and  G.hash() is a function of P.seal() alone
analyze(G) = O             and  O.work >= O.span > 0 for any non-empty G
```

The SES hash stability requirement is checked executably by
`cli.cmd_selfcheck` (`ses_hash_stable`) and by `smoke.py`.

---

## 4. The six-version chain

`pacore.VERSION_CHAIN` fixes both the order and the names. A conforming
implementation **SHALL** apply the chain in this order, because each version's
objects are defined in terms of the previous version's.

| Version | Name (`VERSION_CHAIN`) | What it contributed to PA-LCTL | Principal symbols |
|---|---|---|---|
| 1.1.x | `QUANTUM_COMPUTING_TECHNICAL_LANGUAGE_CREATION` | The columned tuple schema (18 core columns), the semantic type system, quantum ownership / no-cloning as a type rule, the normative operation catalog, the exactness-regime vocabulary. Originally designated QCTL. | `lang.CORE_COLUMNS`, `lang.ALL_TYPES`, `lang.OP_CATALOG`, `lang.REGIMES`, `lang.verify` |
| 1.2.x | `PARALLEL_DISTRIBUTED_SYSTEM_EXPANSION` | Parallel and distributed *semantic kernel objects* as first-class types; the causal DAG with typed edge reasons; work/span/critical path; the topology model; the communication model `Tmessage = alpha + beta*n`; distributed quantum primitives; the 17-field provenance record; the failure/recovery vocabulary. | `lang.PARALLEL_TYPES`, `lang.DISTRIBUTED_TYPES`, `lang.EDGE_REASONS`, `planner.t_message`, `lang.OPS_DISTRIBUTED_Q`, `ledgers.PROVENANCE_FIELDS`, `lang.RECOVERY_CLASSES` |
| 1.3.x | `NATIVE_PARALLEL_DISTRIBUTED_EXECUTION_FABRIC` | The Semantic Execution Supergraph replacing the flat DAG; concurrency admission records; the commutation proof ledger; QCIR-P2; the numerical engines (statevector, density, stabilizer) and the explainable backend planner; execution profiles and deterministic replay; the ebit lifecycle and classical feedback fabric; the Q1–Q6 admission gate; explicit numerical thresholds; the status vocabulary. | `ses.SES`, `ses.ConcurrencyRecord`, `commutation.ProofRow`, `ledgers.QCIRP2`, `simulator.*Engine`, `fabric.ExecutionProfile`, `protocols.EbitLedger`, `lang.Q6_ADMISSION`, `lang.STATUS_VOCABULARY` |
| 1.4.x | `ADAPTIVE_PARALLEL_DISTRIBUTED_MESH` | Hypergraph Partitioner 2.0 and Pareto planning; hierarchical placement; Routers 2.0; Scheduler 2.0 and dynamic re-planning; Commutation Authority 2.0; the partial-order model with exact bounded antichain width; parallelism opportunity discovery; BSP and asynchronous runtimes; collectives and algorithm selection; work stealing, load balancing, straggler mitigation; deadlock/livelock/starvation detection; consistency profiles and CRDTs; Monte-Carlo uncertainty propagation; coherence exposure; supervision trees and recovery transactions; the teleport and purification validity checklists. | `planner.pareto_partitions`, `planner.PLACEMENT_LEVELS`, `ses.analyze`, `ses.discover_opportunities`, `fabric.BSPEngine`, `fabric.AsyncEngine`, `fabric.Collectives`, `fabric.WorkStealing`, `fabric.DeadlockDetector`, `crdt.*`, `erroralgebra.MonteCarloUncertainty`, `erroralgebra.CoherenceExposure`, `resilience.SupervisionTree`, `protocols.purify` |
| 1.5.x | `FEDERATED_PARALLEL_DISTRIBUTED_RUNTIME_MESH` | The federation object model (worker → group → domain → federation); the 17-value parallel family model; Multilevel Hypergraph Partitioner 3.0 and federated placement; Routers 3.0 with hysteresis; Scheduler 3.0; Commutation Authority 3.0 (exact Clifford conjugation); federated anti-entropy and version vectors; migration/replication/consistency/provenance rules; the remote-CNOT reference-equivalence criterion; federated checkpoint scopes; property, metamorphic and differential campaigns; the release-qualification outputs. | `fabric.Federation`, `lang.PARALLEL_FAMILIES`, `planner.ClassicalRouter.select` (hysteresis), `commutation.CLIFFORD_CONJUGATION`, `crdt.AntiEntropy`, `fabric.MigrationRules`, `protocols.REMOTE_CNOT_PASS_TOKEN`, `conformance.property_campaign` etc., `ledgers.RELEASE_OUTPUTS` |
| 1.6.x | `HYPERFEDERATED_MASSIVELY_PARALLEL_DISTRIBUTED_EXECUTION_FABRIC` | Recursive partition trees and the communication-avoidance order; p95 makespan in the Pareto cost vector; grain-size adaptation; elastic worker groups with the adaptation-thrash guard; four-level work stealing; federated collectives and hierarchical reduction; the append-only event log and replay verification; empirical verification of the CRDT merge laws; reproducible resilience campaigns; soak qualification thresholds; the status vocabulary applied to the release outputs. | `planner.HypergraphPartitioner._partition_tree`, `planner.COMMUNICATION_AVOIDANCE_ORDER`, `fabric.GrainSizeEngine`, `fabric.ElasticWorkerGroup`, `fabric.STEAL_LEVELS`, `fabric.EventLog`, `crdt.verify_all_crdt_laws`, `resilience.run_campaign`, `conformance.SOAK_THRESHOLDS` |

### 4.1 Chain compatibility rule

The 1.2+ column extension is **additive**. A 1.1-core bundle declaring only the
18 core columns parses unchanged and receives `NULL_CELL` for `DOMAIN`, `NODE`,
`LINK` and `FAMILY` (`lang.parse` → `cmap.setdefault(missing, NULL_CELL)`).
An implementation **SHALL NOT** require the distributed columns of a core
bundle.

---

## 5. The honesty rules

These are the rules that make a PA-LCTL claim checkable. They are stated here
once and enforced in the code at the sites named.

### 5.1 The four claim classes

| Class | Meaning | Where it may be claimed | Enforcement |
|---|---|---|---|
| **exact** | The result is the mathematically exact one for the declared model, to the stated numerical tolerance. | `REGIME` in `lang.EXACT_REGIMES`; commutation `exactness = EXACT`; resource provenance `exact`. | `E-REG-002`, `E-REG-003`; `ledgers.tagged` |
| **approximate** | A stated approximation was applied and its neglected magnitude is reported. | `REGIME` in the non-exact part of `lang.REGIMES`; composition law `L3`; `commutation` verdict `COMMUTING_TARGET_CONDITIONAL`. | `erroralgebra.compose` reports `neglected_second_order`; `protocols.purify` returns `regime = APPROXIMATE` |
| **emulated** | Classical software reproduced the *semantics* of a distributed or quantum activity, on this machine, with no physical carrier. | `parallel_state ≤ PARALLEL_EMULATION`; `distributed_state ≤ DISTRIBUTED_CLASSICAL_EMULATION`; execution labels. | `ledgers._cap`, `simulator._assert_label_honest` |
| **physical** | An authenticated physical target executed the work. | **Nowhere in this implementation.** | `cli.cmd_adapter_check` raises `CommandBlocked(BLOCKED_EXTERNAL_AUTHORITY)`; `PHYSICAL_RELEASE_OUTPUTS` are always blocked |

### 5.2 Normative honesty statements

1. A result label **SHALL** begin with `CLASSICAL_` and **SHALL NOT** contain
   any of `QPU`, `HARDWARE`, `PHYSICAL`, `DEVICE`, `QUANTUM_EXECUTION`
   (`simulator._FORBIDDEN_LABEL_TOKENS`). Violation raises `SimulationError`.
2. A heuristic result **SHALL NOT** be labelled optimal. `optimal_cost` is
   populated only when the bounded exact oracle actually ran, and
   `optimality_claim` is `PROVEN_OPTIMAL_BOUNDED_INSTANCE` only when the
   heuristic value equals the oracle value to `1e-9`
   (`planner.PartitionResult.as_dict`, `planner.Schedule.as_dict`).
3. A route **SHALL** be reported as a plan over the declared topology, never as
   evidence of physical hardware (`ledgers` `ROUTING_LEDGER.json` and
   `QUANTUM_ROUTING_LEDGER.json` `claim` fields;
   `planner.QuantumRouter.min_cost_flow` `claim` field).
4. Incompatible error dimensions **SHALL NOT** be merged into a scalar; the
   composer returns `ERROR_COMPOSITION_UNRESOLVED` with the dimensions
   preserved (`erroralgebra.compose`, rule 1).
5. An impossible consistency guarantee **SHALL NOT** be silently downgraded;
   `crdt.ConsistencyContract.evaluate` returns
   `CONSISTENCY_GUARANTEE_BLOCKED` and sets `downgrade_offered: false`.
6. Unknown quantum state **SHALL NOT** be checkpointed, rolled back, replicated,
   stolen, speculated or pickled
   (`resilience.QuantumCheckpointRefused`, `resilience.QuantumRollbackRefused`,
   `crdt.QuantumReplicationRejected`, `fabric.StealRejectedError`,
   `fabric.SpeculationRejectedError`, `fabric.QuantumPayload.__reduce__`).
7. A soak shorter than a qualification threshold **SHALL** be reported as
   `SOAK_<N>H_NOT_RUN`, never as a pass (`conformance.soak`).
8. A release output **SHALL NOT** be `QUALIFIED` without executed conformance
   evidence with zero failures (`ledgers.LedgerSet._release`, `group_ok`).
9. A learned rewrite rule **SHALL** be `QUARANTINE` and
   `usable_by_optimizer: false` until Q1–Q6 admission
   (`commutation.CommutationAuthority.submit_learned_rule`).
10. Every reported resource number **SHALL** carry a provenance tag from
    `ledgers.VALUE_PROVENANCE`; `ledgers.tagged` raises `LedgerError` for any
    other tag.

### 5.3 Fail-closed principle

The verifier never repairs a program. `lang.verify` accumulates diagnostics and
`VerifyResult.ok` is false if any has severity `ERROR` or `REJECT`. Every
numerical, protocol, fabric, CRDT and resilience failure path raises a named
exception rather than returning a degraded value. The one place a substitution
is permitted — `ledgers.LedgerSet._numerics`, when the planned backend refuses a
circuit — records the refusal verbatim in `substitution_reason` and sets the
status to `EXECUTED_WITH_RECORDED_SUBSTITUTION`.

---

## 6. Conformance levels

Conformance is defined against *executed* evidence. `conformance.run_suite`
counts a case only after running it.

### 6.1 Level definitions

| Level | Requirement | Checkable by |
|---|---|---|
| **L0 — Grammar** | `lang.parse` accepts the bundle with no `ERROR`/`REJECT` diagnostic. | `cli verify` |
| **L1 — Static** | L0 plus `lang.verify(prog).ok`. | `cli verify` |
| **L2 — Analyzable** | L1 plus a constructible SES whose hash is stable across two builds, and a partial order with `work >= span > 0`. | `cli ses`, `cli partial-order` |
| **L3 — Admissible** | L2 plus `conformance.admit(source).accepted` — the full 17-stage admission pipeline. | `conformance.admit` |
| **L4 — Planable** | L3 plus a partition, a non-empty placement, and a schedule with an empty `blocked` list. | `cli schedule` |
| **L5 — Executable (classical)** | L4 plus a numerical run producing a `CLASSICAL_*` label and a reproducible `final_state_hash` for a fixed seed. | `cli execute-local`, `cli simulate-*` |
| **L6 — Evidenced** | L5 plus a complete `LedgerSet` (all 34 files), a round-trip-stable QCIR-P2, and a provenance record with the exact 17 fields. | `cli ledgers`, `cli selfcheck` |
| **L7 — Qualified** | L6 plus a zero-failure conformance report covering the required evidence groups for the release output being claimed. | `cli conformance` + `ledgers.LedgerSet._release` |

A conforming **implementation** (as opposed to a conforming program) **SHALL**
support at minimum L0–L3 and **SHALL** be able to state, for any capability it
does not implement, one of the status values in §11.

### 6.2 Program-level conformance obligations

* A conforming program **SHALL** declare its magic line, `#NETWORK deny` and
  `#BACKEND none`.
* A conforming program **SHOULD** declare `#COLUMNS` explicitly; when absent,
  `lang.parse` assumes `lang.FULL_COLUMNS`.
* A conforming program **SHALL** declare every node, domain and link before
  referencing it.
* A conforming program **SHOULD** carry a `PROOF` reference on any row whose
  `TYPE` is `measurement_result` or `proof_record` when it also carries a
  `CLAIM`; the admission pipeline emits `E-PROV-001` when a result is claimed
  without provenance.

---

## 7. The six mandatory release outputs

`ledgers.RELEASE_OUTPUTS` fixes the set. `ledgers.LedgerSet._release` computes
each status from supplied conformance evidence; nothing is asserted.

| # | Output | Required evidence groups | Status rule |
|---|---|---|---|
| 1 | `NATIVE_PARALLEL_EXECUTION` | `scheduling`, `task_runtime`, `commutation` | `QUALIFIED` iff conformance was supplied, total failures = 0, each group present, non-empty and failure-free; else `BLOCKED` with the reason |
| 2 | `NATIVE_DISTRIBUTED_EXECUTION` | `placement_routing`, `collectives`, `federation` | as above |
| 3 | `DISTRIBUTED_NUMERICAL_SIMULATION` | `numerical_backends`, `partitioning` | as above |
| 4 | `DISTRIBUTED_PROTOCOL_EMULATION` | `protocols`, `resilience`, `provenance_replay` | as above |
| 5 | `PHYSICAL_PARALLEL_QPU_EXECUTION` | (none admissible) | **always** `BLOCKED_EXTERNAL_AUTHORITY` |
| 6 | `PHYSICAL_DISTRIBUTED_QPU_EXECUTION` | (none admissible) | **always** `BLOCKED_EXTERNAL_AUTHORITY` |

Outputs 5 and 6 are members of `ledgers.PHYSICAL_RELEASE_OUTPUTS`. An
implementation **SHALL NOT** qualify them without an external authority holding
authenticated hardware, and this package **SHALL NOT** simulate that authority
(`cli.cmd_qualify_distributed_target`).

The absence of conformance evidence is itself a blocking reason, with the
message *"no conformance evidence supplied; a release output is never qualified
without executed conformance results"*.

---

## 8. Determinism requirements

A conforming implementation **SHALL** make the following reproducible for fixed
inputs and seeds:

| Artifact | Determinism mechanism | Symbol |
|---|---|---|
| Bundle seal | canonical text, SHA-256 | `Program.seal` |
| SES hash | sorted-key JSON with `(",", ":")` separators over ordered nodes and sorted edges | `SES.canonical`, `SES.hash` |
| Topology hash | sorted-key JSON over sorted nodes/links | `Topology.hash` |
| Schedule hash | sorted-key JSON over ops sorted by `(start, node_id)` | `Schedule.canonical`, `Schedule.hash` |
| QCIR-P2 hash | sorted-key JSON over the 15 sections | `QCIRP2.canonical`, `QCIRP2.hash` |
| Final state hash | amplitudes rounded to 12 decimals, negative zero normalized | `simulator._hash_array` |
| Event-log hash | semantic event content only; wall-clock metrics excluded | `fabric.EventLog.hash` (hole H7) |
| Commutation proof id | `CP-` + first 16 hex of the proof hash | `CommutationAuthority._record` |
| Conformance evidence hash | SHA-256 over `[case_id, passed]` pairs | `conformance.run_suite` |
| Failure injection | per-scenario seed derived by hashing `scenario_id|seed` | `resilience._derive_seed` (hole H12) |

Tie-breaking **SHALL** be total and stated: Kahn topological sort breaks ties by
source order (`ses.analyze`); Pareto selection breaks ties by `(k, label)`
ascending (`planner.pareto_partitions`); work-stealing victim order is
`(-depth, worker_id)` (`fabric.WorkStealing.victims`); dynamic placement breaks
ties by worker id (`fabric.LoadBalancer.dynamic_assign`); `fabric.select`
always returns the lowest-index ready channel.

---

## 9. Specification holes filled deterministically

The QUORUM LCTL documents leave a number of points open. PA-LCTL fills each one
deterministically and records it. The holes are numbered as in the module
docstrings.

| Hole | Left open by | Filled by | Where |
|---|---|---|---|
| H1 | 1.2.x–1.6.x never restate the tuple schema and never enumerate `FACE`, while 1.3.x §6 requires a `semantic_face` on every SES node | canonical 17-value `FACE` enumeration and the 4-column 1.2+ extension | `lang.FACES`, `lang.DISTRIBUTED_COLUMNS` |
| H7 | "identical event traces across runs" without saying how wall-clock is excluded | only semantic event content is hashed; measured durations live in a separate `metrics` channel | `fabric.EventLog` |
| H8 | deterministic timeouts vs wall-clock timeouts | `timeout_ticks` against the declared logical cost model in deterministic profiles; `timeout_s` honoured only in the throughput profile; both recorded | `fabric.Task`, `TaskRuntime._execute` |
| H9 | consistency profiles named without impossibility conditions | explicit rule table `C1`–`C9`, verdict names the rule that fired | `crdt.CONSISTENCY_RULES` |
| H10 | LWW register clock policy | three explicit policies; policy is part of replica identity so mismatched policies never merge | `crdt.CLOCK_POLICIES` |
| H11 | failure kinds in prose without stable ids | canonical 22-entry scenario catalog with stable ids | `resilience.SCENARIOS` |
| H12 | reproducibility of injection | `(scenario_id, seed)` determines every draw; per-scenario seed derived by hashing | `resilience._derive_seed` |
| H13 | "explicit composition law registry" without law ids | canonical law ids `L1`–`L6`; every composed term names its law | `erroralgebra.COMPOSITION_LAWS` |
| H14 | no unit lattice stated | minimal explicit unit algebra; only identical unit/kind pairs compose | `erroralgebra.UNITS` |
| H20 | ledger files named without JSON schemas | every ledger carries `schema: PA-LCTL/<LEDGER>/1` | `ledgers` |
| H21 | QCIR-P2 required deterministic without a canonicalization | UTF-8 JSON, sorted keys, `(",", ":")` separators | `ledgers.canonical_json` |
| H22 | negative categories without diagnostic codes | the `E-CONC-*`, `E-*ROUTE-*`, `E-SEAL-*`, `E-CLAIM-*`, `E-DENS-*` families with executable checks | `conformance.ADMISSION_CODES` |
| H23 | "must be rejected" without saying by which component | rejection is the verdict of `conformance.admit`, the full 17-stage pipeline | `conformance.admit` |

H1 is the load-bearing one and is specified in full in
`PA_LCTL_TUPLE_SCHEMA.md`.

---

## 10. Designation

The designation prefix is **PA** (formerly **JA**). `pacore.redesignate`
provides a checked mapping surface and a `self_check()` that
`cli.cmd_redesignate` and `cli.cmd_selfcheck` both run; a failing self-check is
a `CommandRejected`, not a warning. The designation appears in
`pacore.LANGUAGE`, `pacore.BUNDLE_MAGIC`, every `schema` key
(`PA-LCTL/<NAME>/1`) and the accepted magic alternatives
(`lang.MAGIC_RE` accepts `PA-LCTL`, `LCTL` and the historical `QCTL`, but a
conforming bundle **SHALL** write `#PA-LCTL/1.6`).

---

## 11. Status vocabulary

`lang.STATUS_VOCABULARY` = `BLOCKED`, `SPECIFIED`, `SCAFFOLDED`,
`IMPLEMENTED`, `VERIFIED`, `OPERATIONAL`, `QUALIFIED`.

| Value | Means |
|---|---|
| `BLOCKED` | The capability cannot exist here; an external authority is required. |
| `SPECIFIED` | Normatively described in this specification set; no code. |
| `SCAFFOLDED` | Symbols and vocabulary exist; no behaviour behind them. |
| `IMPLEMENTED` | Behaviour exists and runs. |
| `VERIFIED` | Behaviour is checked by an executable test in this package. |
| `OPERATIONAL` | Verified, and exercised end-to-end by the CLI or ledger pipeline. |
| `QUALIFIED` | Operational, plus a zero-failure conformance report covering it. |

`lang.GAP_TERMINAL_STATES` adds `IMPLEMENTED_PARTIAL`,
`BLOCKED_EXTERNAL_AUTHORITY` and `REJECTED_NO_FAITHFUL_FORM` for gap-ledger
bookkeeping.

### Implementation status (this document's subject matter)

| Area | Status | Note |
|---|---|---|
| Grammar, verifier, ownership | `OPERATIONAL` | `smoke.py`, `cli selfcheck`, conformance negatives |
| SES, partial order, concurrency admission | `OPERATIONAL` | exact width only up to 22 nodes |
| Commutation authority | `OPERATIONAL` | matrix oracle bounded to 6 qubits |
| Partitioning, placement, routing, scheduling | `OPERATIONAL` | optimality claimed only when the bounded oracle ran |
| Statevector / density / stabilizer engines | `OPERATIONAL` | dense; no tensor-network or out-of-core engine |
| Distributed protocols | `OPERATIONAL` | numerically executed, single process |
| Execution fabric | `OPERATIONAL` | real threads and processes, no network |
| CRDT / consistency | `OPERATIONAL` | classical state only |
| Resilience | `OPERATIONAL` | injection is descriptive, not destructive |
| Error algebra | `OPERATIONAL` | |
| Ledgers, QCIR-P2 | `OPERATIONAL` | |
| Conformance suite and campaigns | `OPERATIONAL` | soak requires real elapsed time |
| Tensor-network backend | `SCAFFOLDED` | appears in `simulator.BACKENDS`; `cli simulate-tensor` is `BLOCKED_CAPABILITY_ABSENT` |
| Out-of-core execution | `BLOCKED` | `cli simulate-ooc` is `BLOCKED_CAPABILITY_ABSENT` |
| Readout / leakage / crosstalk noise models | `SCAFFOLDED` | catalogued in `lang.OPS_NOISE`; no Kraus set exists |
| Physical target adapters | `BLOCKED` | `BLOCKED_EXTERNAL_AUTHORITY`, permanently |
| Multi-machine federation | `BLOCKED` | `NETWORK=deny` is normative, not a limitation to be lifted here |

---

## 12. Document set

See `README_SPEC_INDEX.md`. The normative order of precedence, highest first,
is:

1. The reference implementation in `pacore/` — it is the authority.
2. This master specification.
3. The generated catalogs (`*_CATALOG.md`, `COLUMN_DICTIONARY.md`), which are
   emitted from the implementation by `tools/gen_catalogs.py`.
4. The individual subsystem specifications.

Where a subsystem specification and the implementation disagree, the
implementation is correct and the specification is a defect.
