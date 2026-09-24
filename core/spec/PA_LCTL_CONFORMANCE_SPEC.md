# PA-LCTL Conformance Specification

Document: `PA_LCTL_CONFORMANCE_SPEC.md`
Authority: `pacore.conformance` in full; `pacore.lang.STATUS_VOCABULARY`;
`pacore.ledgers.LedgerSet._release`; `pacore.cli`.

RFC 2119 keywords apply.

---

## 1. The governing rule

> **Every case in this module is *executed*. Nothing is counted from a table: a
> case contributes to the totals only after `run_suite` has actually run it and
> checked its outcome.**

and, for negatives specifically:

> **A negative case fails when the program is accepted, and also fails when it
> is rejected for a reason other than the one it declares.**

A conforming implementation **SHALL NOT** report a conformance number that was
not produced by running the cases.

---

## 2. How conformance is organized

### 2.1 Case model

`conformance.Case` — `case_id`, `group`, `title`, `source`, `expect`. A case is
**negative** iff `expect["reject"]` is truthy; otherwise it is **positive**.

| Registry | Symbol | Measured size |
|---|---|---|
| Positive cases | `conformance.POSITIVE_CASES` | **712** |
| Negative cases | `conformance.NEGATIVE_CASES` | **560** |
| All cases | `conformance.ALL_CASES` | **1272** |
| Index by id | `conformance.CASE_INDEX` | — |

`cli.cmd_selfcheck` asserts `len(POSITIVE_CASES) >= 512` and
`len(NEGATIVE_CASES) >= 512`, so a registry that shrinks below the floor fails
the self-check.

### 2.2 Program construction DSL

Cases are built, not hand-written, by a small DSL that guarantees a
well-formed 22-cell row every time:

| Helper | Produces |
|---|---|
| `row(row_id, face, op, **cells)` | one canonical `¦`-joined row; unset columns become `-`; an unknown column name raises `PALCTLError` |
| `program(rows, network="deny", backend="none", profile=...)` | a complete source file with magic, directives and `#COLUMNS` |
| `declarations(nodes, topology_resource, quantum_link)` | the standard domain / node / link / topology preamble |
| `prep`, `gate`, `measure`, `claim` | typed row shorthands |
| `_bell_rows`, `_independent_lanes` | reusable circuit fragments |

Because the DSL always emits all 22 columns, a negative case is negative for
the reason it declares and not because of an accidental grammar error.

### 2.3 Parameter sweeps

Positive generators sweep real axes, *"real variation, not padding"*:

```
QUBIT_COUNTS   = (2, 3, 4)
WORKER_COUNTS  = (1, 2, 4, 8)
PARTITION_KS   = (2, 3, 4)
SEEDS          = (0, 1, 7, 13)
BACKEND_SHAPES = ("clifford", "non_clifford", "noisy")
```

---

## 3. The admission pipeline (design holes H22, H23)

### 3.1 The hole

The documents require negative programs to be rejected but **do not say by
which component**, and they name negative categories without giving diagnostic
codes for the ones the 1.1 verifier does not already emit.

### 3.2 How PA-LCTL fills it

* **H23** — *"rejection is the verdict of `admit()`, the full admission
  pipeline"*.
* **H22** — the missing codes are defined in `conformance.ADMISSION_CODES`
  (the `E-CONC-*`, `E-*ROUTE-*`, `E-SEAL-*`, `E-CLAIM-*`, `E-DENS-*` families
  and others), **each with the executable check that produces it**.

### 3.3 The seventeen stages

`conformance.admit(source, path) -> AdmissionResult` fails closed at the first
stage that produces a diagnostic; later stages never run.

| # | Stage | Checks |
|---|---|---|
| 1 | `parse` | `lang.parse` errors |
| 2 | `verify` | `lang.verify` errors |
| 3 | `target_binding` | the bound target supports each operation (`E-TARGET-001`) |
| 4 | `memory` | declared memory claims vs target capacity (`E-MEM-001`) |
| 5 | `calibration` | calibration validity at the claimed epoch (`E-CAL-001`) |
| 6 | `seals` | declared topology / schedule seals (`E-SEAL-001`, `E-SEAL-002`) |
| 7 | `routing` | classical and quantum route existence (`E-ROUTE-001`, `E-QROUTE-001`, `E-CPATH-001`) |
| 8 | `entanglement` | ebit capacity and expiry (`E-EBIT-001`, `E-EBIT-002`) |
| 9 | `numerical_claims` | declared matrices (`E-UNIT-001`, `E-HERM-001`, `E-DENS-001/002`, `E-PROJ-001`, `E-POVM-001`, `E-KRAUS-001`, `E-DIM-001`) at `MATRIX_TOL = 1e-9` |
| 10 | `state_claims` | normalization (`E-NORM-001`), probability domain |
| 11 | `provenance` | results claimed without provenance (`E-PROV-001`), physical claims (`E-CLAIM-001`, `E-CLAIM-002`) |
| 12 | `concurrency` | asserted concurrency vs the real verdict (`E-CONC-WW`, `E-CONC-RW`, `E-CONC-MEASDEP`, `E-CONC-TARGET`, `E-CONC-RESOURCE`, `E-CONC-NONCOMMUTE`, `E-COUPLE-001`, `E-XTALK-001`) |
| 13 | `budgets` | coherence and communication budgets (`E-COH-001`, `E-COMM-001`) |
| 14 | `protocol_compile` | the protocol compiler's verdict (`E-PROTO-COMPILE`) |
| 15 | `error_algebra` | incompatible error composition (`E-ERRC-001`) |
| 16 | `runtime_contracts` | consistency, stealing, replay, CRDT, checkpoint, recovery claims (`E-CONS-001`, `E-STEAL-001`, `E-REPLAY-001`, `E-CRDT-001`, `E-CKPT-001`, `E-RECOV-001`) |
| 17 | `deadlock` | declared wait graph cycles (`E-DEADLOCK-001`) |

`AdmissionResult` carries `accepted`, `codes` (sorted, de-duplicated),
`stage`, `messages` and, on success, a `detail` block with the SES hash,
topology hash, work, span and width.

---

## 4. The twelve positive groups

`conformance.POSITIVE_GROUPS`, with the measured case counts:

| Group | Cases | What it executes |
|---|---|---|
| `numerical_backends` | 108 | backend planning + statevector / density / stabilizer runs across `BACKEND_SHAPES`, qubit counts and seeds; determinism of `final_state_hash`; label honesty |
| `protocols` | 88 | ebit lifecycle, teleport, remote CNOT reference equivalence, entanglement swap, purification domain, protocol compilation and validation |
| `resilience` | 88 | injection, classification, supervision, checkpoints, transactions across scenarios and seeds |
| `scheduling` | 77 | schedule modes, makespan, blocked lists, hashes, bounded exact validation |
| `collectives` | 64 | all eight collectives × five algorithms × worker counts, checking the **values**, not just the counts |
| `commutation` | 54 | rule classes, proof rows, verdict correctness |
| `task_runtime` | 48 | spawn/await, barriers, reduction, scan, ownership transfer, clone refusal |
| `consistency_crdt` | 45 | CRDT merge laws, anti-entropy convergence, contract verdicts |
| `partitioning` | 36 | hyperedges, cost vector, refinement, exact oracle, optimality claim |
| `placement_routing` | 36 | placement proofs, routes, hysteresis, quantum routing |
| `provenance_replay` | 36 | provenance field set, ladder ceilings, event-log replay |
| `federation` | 32 | federation structure, migration/replication rules, work stealing, load balancing |

A positive case is dispatched by `expect["check"]` to one of thirteen checker
functions and **fails** on any `CaseFailure` or any unexpected exception.

---

## 5. The fifty-six negative categories

`conformance.NEGATIVE_CATEGORIES` — **56** categories × `NEGATIVE_VARIATIONS =
10` = 560 executed negative cases. Each category names the diagnostic code it
**must** be rejected with; being rejected for a different reason is a failure.

| # | Category | Expected code |
|---|---|---|
| 1 | `duplicate_unknown_qstate_across_nodes` | `E-OWN-003` |
| 2 | `implicit_copy` | `E-CLONE-001` |
| 3 | `write_write_conflict` | `E-CONC-WW` |
| 4 | `read_write_conflict` | `E-CONC-RW` |
| 5 | `use_after_move` | `E-OWN-001` |
| 6 | `use_after_measure` | `E-OWN-002` |
| 7 | `missing_measurement_dependency` | `E-CTL-003` |
| 8 | `remote_gate_with_no_route` | `E-QROUTE-001` |
| 9 | `remote_gate_unsupported_target` | `E-TARGET-001` |
| 10 | `teleport_without_entanglement` | `E-PROTO-002` |
| 11 | `teleport_without_classical_path` | `E-CPATH-001` |
| 12 | `insufficient_ebits` | `E-EBIT-001` |
| 13 | `expired_ebit` | `E-EBIT-002` |
| 14 | `double_ebit_consume` | `E-EPR-003` |
| 15 | `impossible_target_concurrency` | `E-CONC-TARGET` |
| 16 | `crosstalk_budget_exceeded` | `E-XTALK-001` |
| 17 | `coherence_window_exceeded` | `E-COH-001` |
| 18 | `communication_budget_exceeded` | `E-COMM-001` |
| 19 | `memory_capacity_exceeded` | `E-MEM-001` |
| 20 | `unresolved_coupling` | `E-COUPLE-001` |
| 21 | `corrupted_topology_seal` | `E-SEAL-001` |
| 22 | `corrupted_schedule_seal` | `E-SEAL-002` |
| 23 | `stale_calibration` | `E-CAL-001` |
| 24 | `result_without_provenance` | `E-PROV-001` |
| 25 | `physical_execution_claim_from_simulator` | `E-CLAIM-001` |
| 26 | `physical_distributed_claim_without_two_endpoints` | `E-CLAIM-002` |
| 27 | `hidden_approximation` | `E-REG-003` |
| 28 | `invalid_checkpoint_of_unknown_qstate` | `E-CKPT-001` |
| 29 | `negative_probability` | `E-PROB-001` |
| 30 | `non_normalized_state` | `E-NORM-001` |
| 31 | `invalid_unitary` | `E-UNIT-001` |
| 32 | `non_hermitian_observable_declared_hermitian` | `E-HERM-001` |
| 33 | `density_with_negative_eigenvalue` | `E-DENS-001` |
| 34 | `density_trace_not_one` | `E-DENS-002` |
| 35 | `invalid_projector` | `E-PROJ-001` |
| 36 | `invalid_povm` | `E-POVM-001` |
| 37 | `invalid_kraus_completeness` | `E-KRAUS-001` |
| 38 | `control_equals_target` | `E-CTRL-001` |
| 39 | `incompatible_tensor_dimensions` | `E-DIM-001` |
| 40 | `invalid_arity` | `E-ARITY-001` |
| 41 | `unknown_operation` | `E-OP-001` |
| 42 | `unknown_face` | `E-FACE-001` |
| 43 | `unknown_type` | `E-TYPE-001` |
| 44 | `unknown_regime` | `E-REG-001` |
| 45 | `duplicate_row_id` | `E-GRAM-003` |
| 46 | `conf_out_of_range` | `E-CONF-001` |
| 47 | `network_not_deny` | `E-POL-001` |
| 48 | `backend_not_none` | `E-POL-002` |
| 49 | `exact_regime_on_noise_op` | `E-REG-002` |
| 50 | `consistency_downgrade` | `E-CONS-001` |
| 51 | `illegal_work_steal_of_quantum_task` | `E-STEAL-001` |
| 52 | `replay_corruption` | `E-REPLAY-001` |
| 53 | `crdt_invalid_merge` | `E-CRDT-001` |
| 54 | `error_composition_incompatible_units` | `E-ERRC-001` |
| 55 | `deadlock_cycle` | `E-DEADLOCK-001` |
| 56 | `invalid_recovery_requiring_cloning` | `E-RECOV-001` |

(The authoritative code for each category is
`conformance.NEGATIVE_BUILDERS[category][0]`; the table above records it for
reading convenience.)

### 5.1 The negative-case rule

```python
def _run_negative(case):
    result = admit(case.source, case.case_id)
    if result.accepted:
        raise CaseFailure(f"program was ACCEPTED but must reject with {expected}")
    if expected not in result.codes:
        raise CaseFailure(f"rejected at stage {result.stage} with {codes}, "
                          f"expected {expected}")
```

Both failure modes are real failures. A negative that is rejected "by accident"
— by a grammar error, or by an unrelated check — **does not pass**.

---

## 6. The suite driver

`conformance.run_suite(subset=None, workers=1)`.

`subset` filters by group, category, case id, or the pseudo-keys `positive` and
`negative`; an empty selection raises `PALCTLError`.

> *"the reported result is identical for any worker count because every case is
> independent and the report is ordered by case id"*

The report:

```json
{"schema": "PA-LCTL/CONFORMANCE_REPORT/1",
 "total": 1272, "passed": 1272, "failed": 0,
 "positive_executed": 712, "negative_executed": 560,
 "by_group": {"<group or category>": {"total": n, "passed": p, "failed": f}},
 "failures": [{"case_id": "...", "reason": "..."}],
 "duration_s": 23.95,
 "evidence_hash": "8aa4836e69570330...",
 "workers": 4}
```

`evidence_hash` is the SHA-256 over the sorted `[case_id, passed]` pairs, so
two runs that pass the same cases produce the same hash regardless of timing or
worker count.

**Measured result of the reference implementation at the time of writing:**
1272 cases executed, 1272 passed, 0 failed.

---

## 7. The campaigns

Four campaigns plus a soak. All are deterministic given their seed.

### 7.1 Property campaign (LCTL 1.5.x §70.1)

`property_campaign(seed=0, count=200)`.

* Even iterations generate a program that is **valid by construction**
  (`_random_valid_program`: type correctness, ownership, topology binding and
  the admitted gate set are all preserved — *"the generator only emits rows it
  can justify"*) and require `admit()` to **accept**.
* Odd iterations take such a program, apply one of the `_INJECTORS`
  violations, and require `admit()` to **reject with that injector's code**.

Reports `valid_generated`, `valid_admitted`, `invalid_generated`,
`invalid_caught`, every failure with its reason, `ok`, and an
`evidence_hash`.

### 7.2 Metamorphic campaign (LCTL 1.5.x §70.2)

`metamorphic_campaign(seed=0, count=200)` cycles deterministically through six
relations:

| Relation | Invariant asserted |
|---|---|
| `independent_task_addition` | adding independent work does not change existing results |
| `commuting_gate_reordering` | reordering a proven-commuting pair does not change the outcome |
| `worker_count_invariance` | a collective's values are independent of the worker count |
| `equivalent_partition` | an equivalent partition yields an equivalent plan |
| `failed_then_recovered_task` | a failed-then-recovered task yields the original result |
| `deterministic_repeatability` | the same inputs and seed reproduce the same artifacts |

Reports per-relation `run`/`passed` counts and every failure.

### 7.3 Differential campaign (LCTL 1.5.x §70.3)

`differential_campaign(seed=0, count=100)` compares backends **against the
tolerances declared in `simulator`**, never against an invented one:

| Comparison | Metric | Threshold |
|---|---|---|
| `statevector_vs_density` | `total_variation` of outcome distributions | `DISTRIBUTION_TV_TOL` (1e-9) |
| `statevector_vs_stabilizer` | same, for all-Clifford programs | `DISTRIBUTION_TV_TOL` |
| `statevector_vs_planner_selected` | same, against whatever backend the planner chose | `DISTRIBUTION_TV_TOL` |

Records `run` counts and `max_error` per comparison.

### 7.4 Recovery campaign (LCTL 1.6.x §81)

`recovery_campaign(seed=0, count=120)` cycles through the 22 scenarios,
occasionally perturbing `route_alternatives`, and requires:

* the classification is a member of `lang.RECOVERY_CLASSES`;
* the supervision action is a member of `resilience.SUPERVISION_ACTIONS`;
* **for every non-impossible classification, checkpointing a `QuantumPayload`
  under kind `simulator_state` is refused** — a live assertion that the
  no-checkpoint rule holds.

Reports per-scenario `run`/`recovered`/`impossible`, the total
`recovery_impossible` count, the reference campaign summary, and every failure.

### 7.5 Soak (LCTL 1.6.x §82)

`soak(hours=0.0, seed=0)` runs a **real, time-bounded loop** and reports the
**actual** elapsed time. Thresholds `conformance.SOAK_THRESHOLDS`:
`SOAK_1H` 3600 s, `SOAK_8H` 28800 s, `SOAK_24H` 86400 s.

| Elapsed vs threshold | Verdict |
|---|---|
| below | `SOAK_<N>H_NOT_RUN` |
| at or above, with failures | `SOAK_<N>H_FAILED` |
| at or above, no failures | `SOAK_<N>H_PASS` |

> **A run shorter than a qualification threshold is reported as
> `SOAK_<N>H_NOT_RUN`. It is never reported as a pass.**

`ledgers.LedgerSet._soak(None)` — the default when no soak evidence is supplied
— emits `status: SOAK_NOT_RUN` with the reason *"a soak result is never
inferred from a short analysis run"*.

### 7.6 Aggregate

`full_evidence(seed, ...)` runs the suite plus all four campaigns and returns
one `PA-LCTL/CONFORMANCE_EVIDENCE/1` bundle.

---

## 8. The status vocabulary

`lang.STATUS_VOCABULARY` — seven values, used throughout this specification set
in every "Implementation status" subsection.

| Value | Definition |
|---|---|
| `BLOCKED` | The capability cannot exist in this environment; an external authority is required. |
| `SPECIFIED` | Normatively described; no code implements it. |
| `SCAFFOLDED` | Symbols, vocabulary or structure exist; no behaviour behind them. |
| `IMPLEMENTED` | Behaviour exists and runs. |
| `VERIFIED` | Behaviour is checked by an executable test in this package. |
| `OPERATIONAL` | Verified, **and** exercised end to end (§9). |
| `QUALIFIED` | Operational, **and** covered by a zero-failure conformance report (§10). |

`lang.GAP_TERMINAL_STATES` adds three bookkeeping values for the gap ledger:
`IMPLEMENTED_PARTIAL`, `BLOCKED_EXTERNAL_AUTHORITY`,
`REJECTED_NO_FAITHFUL_FORM`.

The CLI uses a parallel three-value outcome vocabulary for *commands*:

> *"Every command is one of: **OPERATIONAL** — it does the work locally and
> prints machine-readable JSON; **REJECTED** — the program was analyzed and
> refused, with diagnostics; **BLOCKED** — the capability does not exist in this
> environment. There is no fourth outcome."*

with exit codes `0` / `2` / `3` respectively (`4` for an unexpected failure,
`1` for usage).

---

## 9. The `OPERATIONAL` definition — seven conditions

A capability is `OPERATIONAL` **iff all seven** hold. Each is checkable, and
the code site that checks it is named.

| # | Condition | Checked by |
|---|---|---|
| **O1** | **Locally executable.** It runs to completion in this environment with `NETWORK=deny` and `BACKEND=none`, contacting no socket, name service or backend. | `cli.cmd_selfcheck` → `policy_constants`; the absence of any network import in `pacore` |
| **O2** | **Total.** Every input produces either a result or a *named* refusal. No input produces a partial result, a silent default or a hang. | the parser is total (`lang.parse` never raises); every engine raises a named exception; `TemporalScheduler` records refusals in `blocked` instead of raising; `ses.analyze` reports cycles instead of looping |
| **O3** | **Deterministic.** The same inputs and seed produce the same artifact hash. | `smoke.py` (SES hash, `final_state_hash`); `cli.cmd_selfcheck` → `ses_hash_stable`, `simulation_deterministic`; `run_suite`'s `evidence_hash` |
| **O4** | **Evidenced.** It emits a machine-readable artifact carrying an explicit `schema` key of the form `PA-LCTL/<NAME>/1`. | `ledgers` (34 files, design hole H20); `LedgerSet._emit` raises for a missing or unknown file |
| **O5** | **Honestly labelled.** No output claims more than the evidence held: labels start `CLASSICAL_`, ladders are clamped, optimality is claimed only when an oracle ran, and provenance flags are false. | `simulator._assert_label_honest`; `ledgers._cap`; `PartitionResult`/`Schedule` `optimality_claim`; `LedgerSet._provenance` |
| **O6** | **Exercised end to end.** It is reachable from the CLI command surface or from the ledger pipeline, not only from a unit test. | `cli.COMMANDS` (50 commands, every one with a handler — a missing handler is a build error); `LedgerSet.build` |
| **O7** | **Covered.** At least one executed conformance case exercises it, positive or negative. | `conformance.POSITIVE_GROUPS`, `NEGATIVE_CATEGORIES`, and the `by_group` breakdown of `run_suite` |

A capability failing **any** of O1–O7 is at most `VERIFIED`. Where this
specification set marks something `IMPLEMENTED_PARTIAL`, it is because one of
O1–O7 holds only for part of the stated behaviour, and the "Implementation
status" table says which part.

---

## 10. The `QUALIFIED` definition

A **release output** is `QUALIFIED` iff all of the following hold. This is
exactly `ledgers.LedgerSet._release` → `group_ok`.

| # | Condition | Code |
|---|---|---|
| **Q-A** | Conformance evidence was **supplied** to the build. | `if not conf: return False, "no conformance evidence supplied; a release output is never qualified without executed conformance results"` |
| **Q-B** | The evidence reports **zero total failures**. | `if failed != 0: return False, f"conformance reported {failed} failure(s) over {total} executed case(s)"` |
| **Q-C** | Every required evidence **group is present**. | `missing = [g for g in groups if g not in by_group]` |
| **Q-D** | Every required group **executed at least one case**. | `empty = [g for g in groups if by_group[g]["total"] <= 0]` |
| **Q-E** | Every required group reported **zero failures**. | `bad = [g for g in groups if by_group[g]["failed"] != 0]` |
| **Q-F** | The output is **not** a member of `ledgers.PHYSICAL_RELEASE_OUTPUTS`. | the physical loop sets `BLOCKED_EXTERNAL_AUTHORITY` unconditionally |

Q-D exists because an empty group is not evidence: a group with zero cases
trivially has zero failures, and without Q-D that would qualify an output.

Required groups per output:

| Output | Groups |
|---|---|
| `NATIVE_PARALLEL_EXECUTION` | `scheduling`, `task_runtime`, `commutation` |
| `NATIVE_DISTRIBUTED_EXECUTION` | `placement_routing`, `collectives`, `federation` |
| `DISTRIBUTED_NUMERICAL_SIMULATION` | `numerical_backends`, `partitioning` |
| `DISTRIBUTED_PROTOCOL_EMULATION` | `protocols`, `resilience`, `provenance_replay` |
| `PHYSICAL_PARALLEL_QPU_EXECUTION` | (none — always blocked) |
| `PHYSICAL_DISTRIBUTED_QPU_EXECUTION` | (none — always blocked) |

A qualified output's `reason` states the count: *"N executed conformance cases,
0 failures; groups [...] all passed"*, and carries the evidence hash.

**Extension to a *capability* (not a release output).** A capability is
`QUALIFIED` when it is `OPERATIONAL` (§9) **and** it is covered by a group that
satisfies Q-B through Q-E in a supplied report. This specification set marks
almost everything `OPERATIONAL` rather than `QUALIFIED`, because qualification
is a property of a *particular evidence bundle*, not of the code.

---

## 11. Running conformance

```
python3 -m pacore.cli conformance --subset commutation,protocols --workers 4
python3 -m pacore.cli property-test    --seed 0 --count 200
python3 -m pacore.cli metamorphic-test --seed 0 --count 200
python3 -m pacore.cli differential-test --seed 0 --count 100
python3 -m pacore.cli soak --hours 1
python3 -m pacore.cli selfcheck
python3 -m pacore.cli ledgers --example --out ./out
```

`selfcheck` runs fourteen internal checks — parse, verify, SES hash stability,
work/span, simulation determinism, execution-label honesty, ledger file set,
QCIR round trip, provenance field set, no physical claim, physical outputs
blocked, redesignation self-check, policy constants, case-registry floors —
and **raises `CommandRejected` if any fails**, exiting non-zero.

---

## 12. Implementation status

| Element | Status | Note |
|---|---|---|
| Executed case registries (1272 cases) | `OPERATIONAL` | 712 positive, 560 negative; last measured run: 0 failures |
| Twelve positive groups | `OPERATIONAL` | every group non-empty |
| Fifty-six negative categories × 10 variations | `OPERATIONAL` | each must reject with its declared code |
| Seventeen-stage admission pipeline (H23) | `OPERATIONAL` | fails closed at the first failing stage |
| Admission code family (H22) | `OPERATIONAL` | 41 codes with executable checks (`conformance.ADMISSION_CODES`) |
| Property campaign | `OPERATIONAL` | valid-by-construction generator + injectors |
| Metamorphic campaign (6 relations) | `OPERATIONAL` | |
| Differential campaign (3 comparisons) | `OPERATIONAL` | against declared tolerances |
| Recovery campaign | `OPERATIONAL` | live no-checkpoint assertion |
| Soak | `OPERATIONAL` | real elapsed time; never reports a pass it did not earn |
| Worker-count invariance of the report | `OPERATIONAL` | ordered by case id; identical `evidence_hash` |
| `QUALIFIED` for the four non-physical outputs | achievable | requires a supplied zero-failure report |
| `QUALIFIED` for the two physical outputs | `BLOCKED` | `BLOCKED_EXTERNAL_AUTHORITY`, permanently |
| 24-hour soak qualification | not run here | would require 24 h of real elapsed time |
