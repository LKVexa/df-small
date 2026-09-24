# PA-LCTL Execution Provenance Specification

Document: `PA_LCTL_EXECUTION_PROVENANCE.md`
Authority: `pacore.ledgers.PROVENANCE_FIELDS`,
`pacore.ledgers.LedgerSet._provenance`, `pacore.ledgers._cap`,
`pacore.lang.PARALLEL_STATES`, `DISTRIBUTED_STATES`,
`QUANTUM_BOUNDARY_STATES`, `pacore.simulator.EXECUTION_LABELS`.

RFC 2119 keywords apply.

---

## 1. The rule

> **No ladder value may be claimed above the evidence actually held.**

A provenance record is the one place where a PA-LCTL run states *what kind of
thing just happened*. Because that statement is what a reader will quote, the
implementation makes the ceilings **structural** rather than conventional:
`ledgers._cap` clamps the two ladders, and no branch can exceed the clamp.

---

## 2. The seventeen provenance fields

`ledgers.PROVENANCE_FIELDS` — LCTL 1.2.x §33. *"Exactly these fields, no more,
no less."* `LedgerSet._provenance` raises `LedgerError` when the emitted record's
key set differs:

```python
if set(record) != set(PROVENANCE_FIELDS):
    raise LedgerError("PROVENANCE_LEDGER field set does not match LCTL 1.2.x s33: "
                      f"missing={...} unexpected={...}")
```

and `cli.cmd_selfcheck` re-checks it (`provenance_fields`).

| # | Field | Type | Value in this environment | Derivation |
|---|---|---|---|---|
| 1 | `language` | str | `"PA-LCTL"` | `pacore.LANGUAGE` |
| 2 | `profile` | str | the bundle's `#PROFILE`, default `pa.lctl.core` | `Program.profile` |
| 3 | `execution_class` | str | a `simulator.EXECUTION_LABELS` value, or `"CLASSICAL_STATIC_ANALYSIS_ONLY"` | the simulation label if a run happened, else the static-analysis label |
| 4 | `parallel_state` | str | ≤ `PARALLEL_EMULATION` | §3, clamped by `_cap` |
| 5 | `distributed_state` | str | ≤ `DISTRIBUTED_CLASSICAL_EMULATION` | §4, clamped by `_cap` |
| 6 | `quantum_boundary` | str | **always** `QUANTUM_BOUNDARY_NOT_CROSSED` | §5 |
| 7 | `target` | str | `f"declared_topology:{topology.name}"` | never a device identity |
| 8 | `target_verified` | bool | **always `False`** | no authenticated target exists |
| 9 | `topology_hash` | str | SHA-256 | `Topology.hash()` |
| 10 | `schedule_hash` | str | SHA-256 | `Schedule.hash()` |
| 11 | `source_hash` | str | SHA-256 of the raw source text | `Program.source_hash()` |
| 12 | `qcir_hash` | str | SHA-256 | `QCIRP2.hash()` |
| 13 | `ses_hash` | str | SHA-256 | `SES.hash()` |
| 14 | `proof_ledger_hash` | str | SHA-256 over the commutation rules | `_sha(COMMUTATION_LEDGER["rules"])` |
| 15 | `physical_qpu` | bool | **always `False`** | |
| 16 | `physical_parallel` | bool | **always `False`** | |
| 17 | `physical_distributed` | bool | **always `False`** | |

Fields 9–14 are six independent hashes. Together they identify the source, the
graph, the topology, the plan, the schedule and the proof set — so a
provenance record is a complete, checkable citation of a run.

Note that `source_hash` is of the **raw** text while the bundle *seal*
(`Program.seal()`) is of the canonical text. The two differ: the seal is
formatting-insensitive, the source hash is not. Both are useful; the provenance
record carries the source hash, and the SES carries the seal
(`SES.program_seal`).

---

## 3. The parallel state ladder

`lang.PARALLEL_STATES`, weakest first:

| # | Value | Means |
|---|---|---|
| 1 | `SERIAL` | no concurrency is exposed |
| 2 | `LOGICAL_PARALLEL` | the partial order exposes width > 1; nothing was scheduled or run concurrently |
| 3 | `SCHEDULED_PARALLEL` | concurrency was admitted and placed on more than one target |
| 4 | `PARALLEL_EMULATION` | classical software actually executed the work with the admitted concurrency **← ceiling here** |
| 5 | `PHYSICAL_PARALLEL_CANDIDATE` | a physical target could plausibly run it in parallel |
| 6 | `PHYSICAL_PARALLEL_EXECUTION` | a physical target did |

### 3.1 Derivation

```python
if sim is not None and admitted > 0 and len(targets) > 1:
    parallel_state = "PARALLEL_EMULATION"
elif admitted > 0 or len(targets) > 1:
    parallel_state = "SCHEDULED_PARALLEL"
elif po.width > 1:
    parallel_state = "LOGICAL_PARALLEL"
else:
    parallel_state = "SERIAL"
parallel_state = _cap(parallel_state, lang.PARALLEL_STATES, "PARALLEL_EMULATION")
```

where `admitted = opportunities["admitted_parallel_pairs"]` and `targets` is
the set of placement targets.

Reading the rule as evidence requirements:

| Claim | Evidence required |
|---|---|
| `LOGICAL_PARALLEL` | an exposed maximum antichain width > 1 |
| `SCHEDULED_PARALLEL` | at least one admitted parallel pair **or** more than one placement target |
| `PARALLEL_EMULATION` | all of: a numerical run actually happened, at least one admitted parallel pair, and more than one target |

### 3.2 The ceiling

```python
def _cap(state, ladder, ceiling):
    order = list(ladder)
    if state not in order: raise LedgerError(...)
    return state if order.index(state) <= order.index(ceiling) else ceiling
```

`_cap` is applied unconditionally, so even a future derivation bug cannot emit
`PHYSICAL_PARALLEL_CANDIDATE` or `PHYSICAL_PARALLEL_EXECUTION`. An unknown
state raises rather than passing through.

---

## 4. The distributed state ladder

`lang.DISTRIBUTED_STATES`, weakest first:

| # | Value | Means |
|---|---|---|
| 1 | `LOCAL_ONLY` | one owner, one domain |
| 2 | `LOGICAL_DISTRIBUTED` | more than one owner or domain is named; nothing was placed apart |
| 3 | `DISTRIBUTED_SCHEDULED` | the plan places work on more than one site |
| 4 | `DISTRIBUTED_CLASSICAL_EMULATION` | classical software actually executed the distributed plan **← ceiling here** |
| 5 | `DISTRIBUTED_COMPILED` | compiled for a real distributed target |
| 6 | `PHYSICAL_DISTRIBUTED_CANDIDATE` | a physical distributed target could run it |
| 7 | `PHYSICAL_DISTRIBUTED_EXECUTION` | it did |

### 4.1 Derivation

```python
multi_site = len(targets) > 1 or len(domains) > 1 or len(owners) > 1
if multi_site and sim is not None:  distributed_state = "DISTRIBUTED_CLASSICAL_EMULATION"
elif multi_site:                    distributed_state = "DISTRIBUTED_SCHEDULED"
elif len(owners | domains) > 1:     distributed_state = "LOGICAL_DISTRIBUTED"
else:                               distributed_state = "LOCAL_ONLY"
distributed_state = _cap(distributed_state, lang.DISTRIBUTED_STATES,
                         "DISTRIBUTED_CLASSICAL_EMULATION")
```

`domains` is the set of `SESNode.memory_domain` values, `owners` the set of
`SESNode.owner` values.

Note `DISTRIBUTED_COMPILED` is **above** the ceiling: compiling for a real
distributed target requires that target to exist, so this runtime cannot claim
it either.

---

## 5. The quantum-boundary ladder

`lang.QUANTUM_BOUNDARY_STATES`:

| Value | Means |
|---|---|
| `QUANTUM_BOUNDARY_NOT_CROSSED` | no physical quantum system was involved |
| `QUANTUM_BOUNDARY_CANDIDATE` | a plan exists that would cross it |
| `QUANTUM_BOUNDARY_CROSSED` | a physical quantum system executed part of the work |

`LedgerSet._provenance` writes the literal `"QUANTUM_BOUNDARY_NOT_CROSSED"`.
There is **no derivation and no branch** — the value is not computed, because
nothing in this environment could make it anything else.

`cli.cmd_selfcheck` asserts it (`no_physical_claim`), together with the three
`physical_*` flags and `target_verified`.

---

## 6. Execution class labels

`execution_class` carries the simulation label when a numerical run happened,
and `"CLASSICAL_STATIC_ANALYSIS_ONLY"` otherwise.

`simulator.EXECUTION_LABELS` — the complete set of labels the numerical layer
may emit:

| Label | Emitted for |
|---|---|
| `CLASSICAL_LOCAL_STATEVECTOR_SIMULATION` | `local_statevector` and anything unclassified |
| `CLASSICAL_LOCAL_DENSITY_MATRIX_SIMULATION` | `local_density` |
| `CLASSICAL_STABILIZER_TABLEAU_SIMULATION` | `stabilizer` |
| `CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION` | `distributed_statevector`, `distributed_density`, `shot_farm`, `circuit_farm`, `trajectory_farm` |

`simulator._assert_label_honest` enforces three conditions and raises
`SimulationError` on any of them:

1. the label is in `EXECUTION_LABELS`;
2. it starts with `CLASSICAL_`;
3. it contains **none** of `simulator._FORBIDDEN_LABEL_TOKENS` = `QPU`,
   `HARDWARE`, `PHYSICAL`, `DEVICE`, `QUANTUM_EXECUTION`.

The distributed label is deliberately the same string for all five distributed
backends: *"Distributed / sharded runs are labelled exactly
`CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION`."* A conforming implementation
**SHALL NOT** invent a per-backend distributed label that could read as
hardware-specific.

`cli._sim_report` re-checks the prefix and raises `CommandRejected` for a
dishonest label — a second, independent guard at the command surface.

---

## 7. Provenance in the protocol and fabric layers

| Layer | Provenance carried |
|---|---|
| `protocols.EbitRecord.provenance` | every lifecycle transition: `{from, to, epoch, note}` |
| `protocols.OwnershipTransfer` | `object_key`, `from_node`, `to_node`, `lineage`, `epoch`, `source_invalidated`, `reason` |
| `protocols` results | `label = simulator.LOCAL_STATEVECTOR_LABEL` on every executed protocol |
| `fabric.QuantumPayload.provenance` | a tuple of `"<from>-><to>@<logical time>"` transfer strings |
| `fabric.Provenance` | `created_by`, `created_at_logical`, `source_ref`, `seal` on every federation object |
| `fabric.Event.proof_ref` | per-event proof reference, part of the replay hash |
| `commutation.ProofRow` | `source_hash`, `result_hash`, `proof_hash` per rule |
| `resilience.Checkpoint.provenance` | caller-supplied, plus per-block checksums |
| `erroralgebra.ErrorTerm.provenance` | the `PROOF` cell, or `law:<id>` for a composed term |

The `PROTOCOL_LEDGER.json` claim is normative and applies to all of these:

> *"reference protocol semantics verified numerically; not evidence of physical
> entanglement or physical execution"*

---

## 8. Reading a provenance record

A conforming consumer:

1. **SHALL** read `execution_class` before quoting any result. A label starting
   `CLASSICAL_` means classical software produced it.
2. **SHALL** read `target_verified` before attributing a result to any device.
   `False` means the `target` field is a **declared model name**, not a device.
3. **SHALL** treat `parallel_state` and `distributed_state` as *claims about
   what happened*, not capabilities. `PARALLEL_EMULATION` means classical
   software ran the plan concurrently, on this machine.
4. **SHALL** treat `quantum_boundary = QUANTUM_BOUNDARY_NOT_CROSSED` as
   dispositive: nothing quantum was physically executed.
5. **SHOULD** verify the six hashes against independently rebuilt artifacts when
   the source is available.
6. **SHALL NOT** infer a physical claim from any combination of fields. There is
   no combination that supports one.

---

## 9. Worked record

From `cli ledgers --example`:

```json
{
  "language": "PA-LCTL",
  "profile": "pa.lctl.quantum.parallel.distributed",
  "execution_class": "CLASSICAL_LOCAL_STATEVECTOR_SIMULATION",
  "parallel_state": "PARALLEL_EMULATION",
  "distributed_state": "DISTRIBUTED_CLASSICAL_EMULATION",
  "quantum_boundary": "QUANTUM_BOUNDARY_NOT_CROSSED",
  "target": "declared_topology:reference-4n-2d",
  "target_verified": false,
  "topology_hash": "…", "schedule_hash": "…", "source_hash": "…",
  "qcir_hash": "…", "ses_hash": "…", "proof_ledger_hash": "…",
  "physical_qpu": false,
  "physical_parallel": false,
  "physical_distributed": false
}
```

Read as prose: *this is PA-LCTL; a local classical statevector simulation ran;
the admitted concurrency was actually emulated across more than one modelled
target in more than one modelled domain; no physical quantum system was
involved; the "target" is a declared topology named `reference-4n-2d` and was
not verified; here are six hashes identifying exactly what was analysed.*

---

## 10. Implementation status

| Element | Status | Note |
|---|---|---|
| Seventeen-field record, exact set | `OPERATIONAL` | key-set mismatch raises; re-checked by `selfcheck` |
| Six independent hashes | `OPERATIONAL` | source, SES, topology, schedule, QCIR, proofs |
| Parallel ladder with structural ceiling | `OPERATIONAL` | `_cap` at `PARALLEL_EMULATION` |
| Distributed ladder with structural ceiling | `OPERATIONAL` | `_cap` at `DISTRIBUTED_CLASSICAL_EMULATION` |
| Quantum boundary | `OPERATIONAL` | literal `NOT_CROSSED`; no branch |
| Execution-label honesty | `VERIFIED` | three conditions, two independent guards |
| Per-layer provenance (protocol, fabric, proof, error, checkpoint) | `OPERATIONAL` | |
| `PHYSICAL_PARALLEL_CANDIDATE` / `_EXECUTION` | `BLOCKED` | above the ceiling |
| `DISTRIBUTED_COMPILED` and above | `BLOCKED` | above the ceiling |
| `QUANTUM_BOUNDARY_CANDIDATE` / `_CROSSED` | `BLOCKED` | never emitted |
| Provenance signing / external attestation | `SPECIFIED` | hashes are unsigned; an external authority would be required |
