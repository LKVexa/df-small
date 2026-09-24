# PA-LCTL Specification Set — Index

Language: **PA-LCTL** (`pacore.LANGUAGE`) · Bundle magic `#PA-LCTL/1.6` ·
Profile `pa.lctl.quantum.parallel.distributed` · Core `1.6.0-rc1`
Normative constants: `BACKEND = none`, `NETWORK = deny`, canonical column
separator **BROKEN BAR `U+00A6`**.

PA-LCTL realizes the QUORUM LCTL 1.1.x → 1.6.x chain. **PA** is the
designation (formerly **JA**).

---

## How to read this set

These documents are **descriptive of an existing implementation**. Every
normative statement is traceable to a symbol in `pacore/`. Order of precedence,
highest first:

1. the reference implementation in `pacore/` — it is the authority;
2. `PA_LCTL_NORMATIVE_SPEC.md`;
3. the generated catalogs (emitted from the code by `tools/gen_catalogs.py`);
4. the individual subsystem specifications.

Where a subsystem specification and the implementation disagree, the
implementation is correct and the specification is a defect.

Every document ends with an **Implementation status** subsection using the
vocabulary `BLOCKED` / `SPECIFIED` / `SCAFFOLDED` / `IMPLEMENTED` / `VERIFIED`
/ `OPERATIONAL` / `QUALIFIED` (`lang.STATUS_VOCABULARY`), plus the gap-ledger
values `IMPLEMENTED_PARTIAL` and `BLOCKED_EXTERNAL_AUTHORITY`. Being truthful
about limits is treated as more important than sounding complete.

---

## 1. Master specification

| # | Document | One-line summary |
|---|---|---|
| 1 | [`PA_LCTL_NORMATIVE_SPEC.md`](PA_LCTL_NORMATIVE_SPEC.md) | The master spec: language thesis, bundle/module/frame/tuple architecture, the 19-layer stack, the six-version chain and what each version contributed, eight conformance levels, the honesty rules (exact / approximate / emulated / physical), the six mandatory release outputs, and the thirteen filled specification holes. |

## 2. Language definition

| # | Document | One-line summary |
|---|---|---|
| 2 | [`PA_LCTL_GRAMMAR.ebnf`](PA_LCTL_GRAMMAR.ebnf) | Complete EBNF for exactly what `lang.parse` accepts — magic line, directives, `#COLUMNS`, `;;` comments, rows and cells, the null cell `-`, `k=v` metadata, semicolon lists, identifiers, `name[i]` / `name[i:j]`, and the numeric sandbox including `pi`, `tau`, `e` and `3*pi/4` — with a prose preamble and a worked example. |
| 3 | [`PA_LCTL_TUPLE_SCHEMA.md`](PA_LCTL_TUPLE_SCHEMA.md) | The 18 core columns and the 4 distributed-profile columns, each with meaning, domain, nullability and examples; and design hole **H1**: the 1.2+ column extension and the 17-value `FACE` enumeration that the QUORUM documents leave open, filled deterministically. |
| 4 | [`PA_LCTL_TYPE_SYSTEM.md`](PA_LCTL_TYPE_SYSTEM.md) | The eight type groups and all 128 type names from `lang`, why the system is nominal/flat/non-inferring, the three membership predicates that act as real subtyping, the quantum/classical boundary, effect-carrying operation classes, and the four authority vocabularies. |
| 5 | [`PA_LCTL_OWNERSHIP_MODEL.md`](PA_LCTL_OWNERSHIP_MODEL.md) | No-cloning as a static type rule: object keys, lineage, the `LIVE`/`MOVED`/`MEASURED`/`RELEASED` lifecycle, the eight global ownership rules, entanglement lineage, cross-node ownership, the four admitted state relocations, the runtime handle discipline, and every `E-OWN-*` / `E-CLONE-*` / `E-PROTO-*` / `E-EPR-*` code. |
| 6 | [`PA_LCTL_OPERATOR_SEMANTICS.md`](PA_LCTL_OPERATOR_SEMANTICS.md) | The full 131-operation catalog by family with arity, operand order, parameter domains, and the exact reference matrices for every primitive gate — including which families are analysed but never numerically executed. |
| 7 | [`PA_LCTL_MEASUREMENT_SEMANTICS.md`](PA_LCTL_MEASUREMENT_SEMANTICS.md) | Born-rule measurement in all three engines, basis rotation that is applied and not inverted, collapse and post-measurement state, classical binding, shot semantics as resampling (and where that is *not* equivalent to independent shots), and the five reasons measurement is not an ordinary deterministic function. |

## 3. Physical and numerical models

| # | Document | One-line summary |
|---|---|---|
| 8 | [`PA_LCTL_CHANNEL_NOISE_MODEL.md`](PA_LCTL_CHANNEL_NOISE_MODEL.md) | Density-operator semantics, the seven implemented channels with their exact Kraus operators, completeness checking at 1e-10 with no renormalization, and an explicit account of what is **not** implemented — readout, leakage and crosstalk models — and why each cannot simply be added as a channel. |
| 9 | [`PA_LCTL_ERROR_MODEL.md`](PA_LCTL_ERROR_MODEL.md) | Typed error terms, the `L1`–`L6` composition-law registry (hole **H13**), the rule that incompatible dimensions are never merged, `ERROR_COMPOSITION_UNRESOLVED`, seeded Monte-Carlo propagation, the six-component coherence exposure model and `SCHEDULE_BLOCKED_COHERENCE`. |
| 10 | [`PA_LCTL_RESOURCE_MODEL.md`](PA_LCTL_RESOURCE_MODEL.md) | The nine classical and seventeen quantum resource fields with their exact derivations, the separation rule that forbids a combined figure of merit, and the provenance tag vocabulary `exact` / `compiler_derived` / `analytical_estimate` / `simulator_estimate` / `hardware_estimate` / `unknown`. |

## 4. Analysis and planning

| # | Document | One-line summary |
|---|---|---|
| 11 | [`PA_LCTL_Q1_Q6_ADMISSION.md`](PA_LCTL_Q1_Q6_ADMISSION.md) | The Q1–Q6 gate, the nine Q6 verdicts and how each is derived, what quarantine means in practice, the `ADMIT_EXACT`-only rewrite policy, and the rule that no learned rule may reach the optimizer before admission. |
| 12 | [`PA_LCTL_SES_SPEC.md`](PA_LCTL_SES_SPEC.md) | The Semantic Execution Supergraph: the 17-field node record, the eleven nested scopes, the fourteen typed edge reasons, deterministic serialization and hashing, work/span/`Pmax`, the exact maximum antichain via Dilworth, and the twelve-value concurrency admission cascade with recorded reasons. |
| 13 | [`PA_LCTL_QCIR_P2_SPEC.md`](PA_LCTL_QCIR_P2_SPEC.md) | The deterministic execution-plan IR: all fifteen sections, the canonicalization that makes it hashable (hole **H21**), total round-trip stability, the three replay modes, and what the IR deliberately does not contain. |
| 14 | [`PA_LCTL_PARTITION_PLACEMENT_SPEC.md`](PA_LCTL_PARTITION_PLACEMENT_SPEC.md) | Hyperedge construction, multilevel coarsening with KL/FM refinement, the twelve-dimension cost vector, the Pareto front and its deterministic selection policy, the bounded exact oracle and the rule that optimality is claimed only when the oracle actually ran, and hierarchical placement with six-gate placement proofs. |
| 15 | [`PA_LCTL_ROUTING_SPEC.md`](PA_LCTL_ROUTING_SPEC.md) | Classical k-shortest / congestion / hysteresis routing, the communication model `Tmessage = α + β·n`, the time-expanded fidelity-aware entanglement router, seven reasons a quantum link is never reduced to a byte-transfer model, ebit allocation with honest unmet-demand reporting, and the rule that a route is a plan not proof of hardware. |
| 16 | [`PA_LCTL_SCHEDULER_SPEC.md`](PA_LCTL_SCHEDULER_SPEC.md) | The fifteen scheduler stages, the eleven schedule modes (and the four distinct priority functions behind them), timing with communication delay, crosstalk and error-budget validation, coherence exposure, schedule canonicalization and hashing, and the bounded exact makespan validator. |

## 5. Execution

| # | Document | One-line summary |
|---|---|---|
| 17 | [`PA_LCTL_PROTOCOL_SPEC.md`](PA_LCTL_PROTOCOL_SPEC.md) | The ebit lifecycle and its fail-closed transition table, numerically executed teleportation, remote CNOT with the `REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS` criterion, entanglement swapping that refuses to herald an unsupported pair, BBPSSW purification and its validity domain, the classical feedback fabric, and the eight protocol compiler step kinds. |
| 18 | [`PA_LCTL_FABRIC_SPEC.md`](PA_LCTL_FABRIC_SPEC.md) | Four execution profiles, the federation object model, the task / dataflow / BSP / async runtimes, eight collectives × five algorithms with explainable selection, four-level work stealing, load balancing, straggler mitigation, elasticity and `ADAPTATION_THRASH_DETECTED`, grain adaptation, deadlock detection, logical clocks, and the append-only event log with replay verification. |
| 19 | [`PA_LCTL_CONSISTENCY_SPEC.md`](PA_LCTL_CONSISTENCY_SPEC.md) | Seven consistency profiles, five CvRDT types and the four empirically verified merge laws, version vectors, deterministic anti-entropy, quorum semantics, the `C1`–`C9` consensus-boundary rule table (hole **H9**), `CONSISTENCY_GUARANTEE_BLOCKED` with no silent downgrade, and the total exclusion of quantum state from replication. |
| 20 | [`PA_LCTL_FAILURE_RECOVERY_SPEC.md`](PA_LCTL_FAILURE_RECOVERY_SPEC.md) | The 22-scenario catalog (hole **H11**), deterministic injection by `(scenario_id, seed)` (hole **H12**), the eight recovery classifications with impossibility rules evaluated first, the supervision tree, recovery transactions, the seven checkpoint kinds, and the prohibition on checkpointing unknown quantum state. |

## 6. Evidence and boundaries

| # | Document | One-line summary |
|---|---|---|
| 21 | [`PA_LCTL_TARGET_ADAPTER_SPEC.md`](PA_LCTL_TARGET_ADAPTER_SPEC.md) | The adapter ABI and the five-value feature-class vocabulary (`SUPPORTED` / `SUPPORTED_WITH_LIMITS` / `APPROXIMATE` / `TARGET_SPECIFIC` / `UNSUPPORTED`) — both `SPECIFIED`, not implemented — plus the physical-evidence firewall that *is* implemented, its full twelve-item required evidence list, and why every physical output is `BLOCKED_EXTERNAL_AUTHORITY`. |
| 22 | [`PA_LCTL_EXECUTION_PROVENANCE.md`](PA_LCTL_EXECUTION_PROVENANCE.md) | The seventeen provenance fields, the parallel / distributed / quantum-boundary state ladders with their structurally enforced ceilings, the execution-label honesty rules, and the rule that no ladder value may be claimed above the evidence actually held. |
| 23 | [`PA_LCTL_CONFORMANCE_SPEC.md`](PA_LCTL_CONFORMANCE_SPEC.md) | How conformance is organized: the twelve positive groups and the fifty-six negative categories with their required diagnostic codes, the seventeen-stage admission pipeline (holes **H22**/**H23**), the property / metamorphic / differential / recovery campaigns and the soak, the status vocabulary, the `OPERATIONAL` definition's seven conditions and the `QUALIFIED` definition's six. |

## 7. Generated reference catalogs

Emitted by `tools/gen_catalogs.py`, which imports `pacore` and reads its own
symbols, so these cannot drift from the code. **Do not edit by hand — re-run
the generator.**

```
python3 tools/gen_catalogs.py [--out spec]
```

| # | Document | One-line summary |
|---|---|---|
| 24a | [`COLUMN_DICTIONARY.md`](COLUMN_DICTIONARY.md) | All 22 columns with meaning, cell syntax, nullability and the verifier checks that read them, plus the canonical `#COLUMNS` line. |
| 24b | [`TYPE_CATALOG.md`](TYPE_CATALOG.md) | All 128 type names grouped, with the ownership-governed flag, and every enumeration vocabulary in `lang`. |
| 24c | [`OPERATOR_CATALOG.md`](OPERATOR_CATALOG.md) | All 131 operations by family with arity, flags, numerical realization and protocol-template availability; the preparation lowering table; the eleven commutation rules with their verdicts and exactness. |
| 24d | [`ERROR_CATALOG.md`](ERROR_CATALOG.md) | Units, kinds, domains, assumptions, the `L1`–`L6` law registry, the composition and coherence tokens, per-channel Kraus rank and implementation status, every numerical threshold, and the regime table. |
| 24e | [`RESOURCE_CATALOG.md`](RESOURCE_CATALOG.md) | The provenance tag vocabulary, all classical and quantum resource fields with their derivation and emitted tag, the provenance record fields, the fifteen QCIR-P2 sections, the 34 ledger files, the six release outputs and the backend/farm tables. |
| 24f | [`DIAGNOSTIC_CATALOG.md`](DIAGNOSTIC_CATALOG.md) | Every diagnostic code emitted by `lang.py`, **scanned from the module source** so an undocumented code aborts the generator, with severity, emitting site and meaning; plus the 41 admission-pipeline codes, the pipeline stages, the fail-closed exception types and every fabric/CRDT/resilience/protocol token. |

---

## Quick reference

### The six release outputs

| Output | Status in this environment |
|---|---|
| `NATIVE_PARALLEL_EXECUTION` | `QUALIFIED` with a zero-failure conformance report |
| `NATIVE_DISTRIBUTED_EXECUTION` | `QUALIFIED` with a zero-failure conformance report |
| `DISTRIBUTED_NUMERICAL_SIMULATION` | `QUALIFIED` with a zero-failure conformance report |
| `DISTRIBUTED_PROTOCOL_EMULATION` | `QUALIFIED` with a zero-failure conformance report |
| `PHYSICAL_PARALLEL_QPU_EXECUTION` | **`BLOCKED_EXTERNAL_AUTHORITY`**, permanently |
| `PHYSICAL_DISTRIBUTED_QPU_EXECUTION` | **`BLOCKED_EXTERNAL_AUTHORITY`**, permanently |

### The version chain

| Version | Contribution |
|---|---|
| 1.1.x | quantum technical language creation (originally QCTL): tuple schema, type system, ownership, operation catalog |
| 1.2.x | parallel & distributed system expansion: kernel objects, causal DAG, work/span, topology, `Tmessage`, provenance record |
| 1.3.x | native parallel/distributed execution fabric: SES, concurrency admission, QCIR-P2, numerical engines, ebit lifecycle, Q1–Q6 |
| 1.4.x | adaptive parallel/distributed execution mesh: Pareto planning, hierarchical placement, BSP/async, collectives, stealing, CRDTs, coherence exposure |
| 1.5.x | federated parallel/distributed runtime mesh: federation object model, parallel family model, hysteresis routing, anti-entropy, campaigns, release qualification |
| 1.6.x | hyperfederated massively parallel/distributed execution fabric: partition trees, grain adaptation, elasticity, four-level stealing, event log and replay, soak thresholds |

### Filled specification holes

`H1` FACE enumeration + 1.2+ column extension · `H7` what an event log hashes ·
`H8` logical vs wall-clock timeouts · `H9` consistency impossibility rules ·
`H10` LWW clock policy · `H11` failure scenario ids · `H12` injection
reproducibility · `H13` composition law ids · `H14` the unit lattice ·
`H20` ledger JSON schemas · `H21` QCIR-P2 canonicalization · `H22` admission
diagnostic codes · `H23` which component rejects.

### Files in this directory

```
README_SPEC_INDEX.md                 this file
PA_LCTL_NORMATIVE_SPEC.md            master specification
PA_LCTL_GRAMMAR.ebnf                 concrete grammar
PA_LCTL_TUPLE_SCHEMA.md              22-column tuple schema (hole H1)
PA_LCTL_TYPE_SYSTEM.md               type system
PA_LCTL_OWNERSHIP_MODEL.md           no-cloning and ownership
PA_LCTL_OPERATOR_SEMANTICS.md        operation catalog and reference matrices
PA_LCTL_MEASUREMENT_SEMANTICS.md     measurement
PA_LCTL_CHANNEL_NOISE_MODEL.md       channels and noise
PA_LCTL_ERROR_MODEL.md               error algebra
PA_LCTL_RESOURCE_MODEL.md            resource accounting
PA_LCTL_Q1_Q6_ADMISSION.md           rule admission gate
PA_LCTL_SES_SPEC.md                  semantic execution supergraph
PA_LCTL_QCIR_P2_SPEC.md              execution-plan IR
PA_LCTL_PARTITION_PLACEMENT_SPEC.md  partitioning and placement
PA_LCTL_ROUTING_SPEC.md              classical and quantum routing
PA_LCTL_SCHEDULER_SPEC.md            temporal scheduling
PA_LCTL_PROTOCOL_SPEC.md             distributed quantum protocols
PA_LCTL_FABRIC_SPEC.md               execution fabric
PA_LCTL_CONSISTENCY_SPEC.md          consistency and replication
PA_LCTL_FAILURE_RECOVERY_SPEC.md     failure and recovery
PA_LCTL_TARGET_ADAPTER_SPEC.md       adapters and the physical firewall
PA_LCTL_EXECUTION_PROVENANCE.md      provenance and the state ladders
PA_LCTL_CONFORMANCE_SPEC.md          conformance and qualification
COLUMN_DICTIONARY.md                 generated
TYPE_CATALOG.md                      generated
OPERATOR_CATALOG.md                  generated
ERROR_CATALOG.md                     generated
RESOURCE_CATALOG.md                  generated
DIAGNOSTIC_CATALOG.md                generated
```
