# PA-LCTL Resource Model

Document: `PA_LCTL_RESOURCE_MODEL.md`
Authority: `pacore.ledgers.CLASSICAL_RESOURCE_FIELDS`,
`pacore.ledgers.QUANTUM_RESOURCE_FIELDS`, `pacore.ledgers.VALUE_PROVENANCE`,
`pacore.ledgers.tagged`, `pacore.ledgers.LedgerSet._resources`,
`pacore.simulator.Circuit.resource`, `pacore.fabric.ResourceLimits`.

The generated companion table is `RESOURCE_CATALOG.md`. RFC 2119 keywords
apply.

---

## 1. The separation rule

> **Classical and quantum resources are reported in separate sections and are
> never summed into a single figure of merit.**
> — `RESOURCE_LEDGER.json` → `separation_rule`

`RESOURCE_LEDGER.json` has exactly three top-level accounting sections:
`classical`, `quantum` and `hardware_measured`. A conforming implementation
**SHALL NOT** produce a combined "total cost" number across the first two, and
**SHALL** emit the third as `tagged(None, "unknown", ...)` whenever no
authenticated physical target is bound — which, in this environment, is always.

---

## 2. Provenance tagging

Every reported number **SHALL** be wrapped by `ledgers.tagged(value, provenance,
note="")`, producing `{"value": ..., "provenance": ..., "note": ...}`. A
provenance outside `ledgers.VALUE_PROVENANCE` raises `LedgerError`.

### 2.1 The provenance vocabulary

| Tag | Definition | May be emitted when |
|---|---|---|
| `exact` | **Counted**, not estimated. No model intervened between the program text (or the extracted circuit) and the number. | The value is a census: gate counts, measurement counts, declared-row counts, qubit count, circuit depth. |
| `compiler_derived` | Computed by this compiler from declared inputs by a stated rule. Deterministic, but the rule is a modelling choice. | Sums of declared durations or memory claims; message counts; ebit generation/consumption counts derived from row kinds. |
| `analytical_estimate` | Produced by a declared analytic model over declared parameters. | `Tmessage = alpha + beta*n` byte movement; makespan; energy; planned ebit demand; worst planned route fidelity; coherence exposure. |
| `simulator_estimate` | Produced by a numerical engine in this package. | Reserved for engine-derived figures; **not currently emitted** by `_resources` (see §7). |
| `hardware_estimate` | Would require a physical target. | **Never emitted by this runtime.** |
| `unknown` | No admissible source exists. | `hardware_measured`; `shots` when no simulation ran. |

### 2.2 Normative tagging rules

1. A number **SHALL NOT** be tagged `exact` unless it was **counted**.
   `_resources` tags `collectives`, `barriers`, `logical_qubits`,
   `gate_counts_by_class`, `two_qubit_gate_count`, `t_count`,
   `clifford_count`, `depth`, `measurement_count`, `swap_count`,
   `remote_gate_count`, `entanglement_swaps` as `exact`, and each of those is a
   census over rows or over the extracted circuit.
2. A number derived through a *time model* **SHALL** be tagged
   `analytical_estimate`, and the note **SHOULD** say what the model is.
   `cpu_work` and `critical_path` carry the note *"abstract time units, not
   seconds"*; `energy_estimate` carries *"abstract proportional model; no
   hardware power measurement exists"*.
3. `shots` is `exact` when a simulation ran and `unknown` otherwise —
   `tagged(self.shots if sim else 0, "exact" if sim else "unknown")`. A shot
   count is never claimed for work that did not happen.
4. `hardware_measured` is **always** `tagged(None, "unknown", "no authenticated
   physical target is bound; nothing here is measured")`.

---

## 3. Classical resource ledger

`ledgers.CLASSICAL_RESOURCE_FIELDS` — nine dimensions.

| Field | Derivation in `_resources` | Tag | Units |
|---|---|---|---|
| `cpu_work` | `round(partial_order.work, 6)` — the sum of every SES node's `duration_model` | `compiler_derived` | abstract time units, **not seconds** |
| `memory` | sum of `RESOURCE memory=` over every SES node | `compiler_derived` | declared units (bytes by convention) |
| `bytes_moved` | sum of `payload_bytes` over the scheduler's communication plan (256 B per planned cross-partition message, `conformance.MESSAGE_PAYLOAD_BYTES`) | `analytical_estimate` | bytes |
| `messages` | count of entries in the communication plan | `compiler_derived` | count |
| `collectives` | count of rows whose `OP` is in `lang.OPS_COLLECTIVE` | `exact` | count |
| `barriers` | count of `BARRIER` / `FENCE` / `EPOCH` rows | `exact` | count |
| `latency_estimate` | `round(schedule.makespan, 6)` | `analytical_estimate` | abstract time units |
| `critical_path` | `round(partial_order.span, 6)` | `compiler_derived` | abstract time units |
| `energy_estimate` | `round(work * 1.0, 6)` | `analytical_estimate` | abstract; proportional model only |

`cpu_work` is the **work** `W` and `critical_path` is the **span** `D` of the
partial order; their ratio is `Pmax = W/D`, which `PARTIAL_ORDER_LEDGER.json`
reports with the note *"logical upper bound on exposed concurrency; NOT a
physically realized speedup"*.

### 3.1 Runtime classical resources

The execution fabric measures a second, independent set of classical figures,
reported in their own ledgers rather than merged into the above:

| Source | Figures |
|---|---|
| `fabric.EventLog.reconstruct()` | per-key `resource_delta` accumulation, `events_per_worker`, `event_count` |
| `fabric.ChannelMetrics` | `sends`, `receives`, `blocked_sends`, `blocked_receives`, `max_queue_depth`, `mean_queue_depth`, `bytes_sent`, `timeouts`, `total_send_wait_s`, `total_recv_wait_s` |
| `fabric.SuperstepRecord` | `messages`, `bytes`, `local_compute_time_s`, `communication_time_s`, `barrier_time_s`, `idle_time_s`, `per_worker_compute_s` |
| `fabric.CollectiveResult` | `messages`, `bytes`, `sync_count`, `steps`, and the full `transfers` list |
| `fabric.LoadBalancer.ledger()` | `tasks_per_worker`, `imbalance_ratio`, per-decision score decomposition |

These are **measured wall-clock or byte counts** and therefore machine
dependent; they live in the `metrics` channel and, per design hole H7, are
**excluded from `EventLog.hash()`** so that deterministic replay still
reproduces exactly.

### 3.2 `payload_bytes` — the byte accounting rule

`fabric.payload_bytes(obj)`:

| Type | Bytes |
|---|---|
| `None` | 0 |
| `QuantumPayload` | **16** — the handle size; the state never moves |
| `bytes`/`bytearray`/`memoryview` | `len` |
| `np.ndarray` | `nbytes` |
| `bool` | 1 |
| `int`, `float` | 8 |
| `complex` | 16 |
| `str` | UTF-8 length |
| list/tuple/set/frozenset | sum over elements |
| dict | sum over keys and values |
| anything else | UTF-8 length of its canonical JSON |

The `QuantumPayload` entry is normative: **a quantum payload is charged as a
handle, never as state**, because the state never travels in a message.

---

## 4. Quantum resource ledger

`ledgers.QUANTUM_RESOURCE_FIELDS` — seventeen dimensions.

| Field | Derivation | Tag |
|---|---|---|
| `logical_qubits` | `Circuit.n_qubits` — distinct operand keys in the extracted circuit | `exact` |
| `gate_counts_by_class` | `ledgers.gate_counts_by_class(prog)` — a census over the seven `_GATE_CLASSES` buckets plus `other` | `exact` |
| `two_qubit_gate_count` | `Circuit.resource()["two_qubit_gate_count"]` | `exact` |
| `t_count` | count of `T`/`TDG` | `exact` |
| `clifford_count` | count of gates in the stabilizer Clifford catalog at the matching arity | `exact` |
| `depth` | frontier depth over the extracted circuit | `exact` |
| `measurement_count` | count of `kind == "measure"` operations | `exact` |
| `shots` | the shot count if a simulation ran, else `0` | `exact` / `unknown` |
| `swap_count` | rows whose `OP` is `SWAP` or `ISWAP` | `exact` |
| `remote_gate_count` | rows whose `OP` is in `lang.OPS_DISTRIBUTED_Q` | `exact` |
| `ebits_generated` | rows whose `OP` is `ENTANGLE_LINK` or `EPR_RESERVE` | `compiler_derived` |
| `ebits_consumed` | rows whose `OP` is `TELEPORT`, `REMOTE_CNOT`, `REMOTE_CONTROL` or `ENTANGLEMENT_SWAP` | `compiler_derived` |
| `entanglement_swaps` | rows whose `OP` is `ENTANGLEMENT_SWAP` | `exact` |
| `purification_rounds` | planned route purification rounds + count of `PURIFY` rows | `compiler_derived` |
| `quantum_link_attempts` | sum of `ebits` over planned quantum routes | `analytical_estimate` |
| `expected_fidelity` | **minimum** fidelity over planned quantum routes (1.0 if none) | `analytical_estimate` |
| `coherence_exposure` | `schedule.coherence["total_coherence_exposure"]` | `analytical_estimate` |

`expected_fidelity` is deliberately the **worst** planned route, with the note
*"worst planned route fidelity from the declared link model"*. A conforming
implementation **SHALL NOT** report a mean or best-case fidelity under this
field name.

### 4.1 `Circuit.resource()` — the circuit census

```python
{"gate_count", "two_qubit_gate_count", "t_count", "clifford_count",
 "measurement_count", "depth"}
```

`depth` is computed by a per-qubit frontier: each operation with resolvable
targets advances every one of its targets to `max(frontier[t]) + 1`. It is
therefore the **critical-path depth in operations**, not a time.

### 4.2 The ebit inventory

`protocols.EbitLedger` is the authoritative entanglement inventory
(`ENTANGLEMENT_INVENTORY_LEDGER.json`). Per record: `ebit_id`, `endpoint_a`,
`endpoint_b`, `creation_epoch`, `fidelity`, `expiry`, `owner_protocol`,
`state`, and a full `provenance` list of every transition. The ledger also
emits `state_counts` over all eight `lang.EPR_STATES` and the complete
transition table. See `PA_LCTL_PROTOCOL_SPEC.md`.

---

## 5. Resource claims and limits

### 5.1 Declared claims

A row declares a claim in its `RESOURCE` cell. `SESNode.resource_claim` is the
parsed map; `device`, `duration`, `failure_domain` and `memory` are the keys
the pipeline reads.

### 5.2 Federation limits — `fabric.ResourceLimits`

| Dimension | Default | Meaning |
|---|---|---|
| `cpu_slots` | 1 | concurrent classical work units |
| `memory_bytes` | 2^30 | memory ceiling |
| `qpu_slots` | 0 | quantum execution slots — **zero by default, because no QPU exists** |
| `ebit_budget` | 0 | ebits the worker may hold |
| `bandwidth_bytes_per_tick` | 1.0e6 | outbound bandwidth |

`can_satisfy(claim)` returns `(False, "unknown resource dimension <k>")` for an
unrecognized key and `(False, "<k>: claim <n> exceeds limit <m>")` for an
over-claim. `TaskRuntime.spawn` raises `ResourceClaimError` when the owning
worker cannot satisfy the claim; `DataflowRuntime.evaluate` records
`resources_ok = False` with the same reason and holds the node.

`merged(other)` sums `cpu_slots`, `memory_bytes`, `qpu_slots` and
`ebit_budget` and takes the **minimum** bandwidth — the aggregate of a group is
as fast as its slowest member, not as fast as their sum.

### 5.3 Topology capacities — `planner.Node`

| Field | Default | Used by |
|---|---|---|
| `capacity` | 1 | `Topology.can_coexist` (co-execution on one node requires `capacity > 1`); `Placer` capacity gate |
| `memory_bytes` | 512 MiB | `Placer` memory gate |
| `supported_ops` | empty frozenset (= "no restriction") | `Placer` operation-support gate; `conformance._check_targets` → `E-TARGET-001` |
| `trust` | `LOCAL_TRUSTED` | `Placer` trust gate |
| `calibration_epoch` / `calibration_valid_until` | 0 / 2^30 | `Topology.calibration_valid` → `E-CAL-001` |
| `crosstalk_pairs` | empty | `Topology.crosstalk_status` → `SERIALIZE_CROSSTALK`, `CROSSTALK_CONFLICT` |

### 5.4 Link capacities — `planner.Link`

| Field | Default | Meaning |
|---|---|---|
| `latency` | 1.0 | α in `Tmessage = α + β·n` |
| `inv_bandwidth` | 0.001 | β, time per byte |
| `capacity` | 1 | simultaneous-use capacity; congestion divisor |
| `fidelity` | 1.0 | quantum links only |
| `gen_rate` | 1.0 | ebit generation attempts per epoch |
| `success_prob` | 1.0 | per-attempt success |
| `herald_latency` | 0.0 | heralding delay |
| `purification` | False | whether purification rounds are available |
| `decoherence_window` | `inf` | time-expanded feasibility bound |
| `failure_domain` | `F0` | route avoidance |

### 5.5 Resource reservations in QCIR-P2

`LedgerSet._reservations` produces the `resource_reservations` section:

```json
{"occupancy_by_target": {"N0": 6.0, "N1": 4.0},
 "targets": ["N0", "N1"],
 "link_reservations": ["Q0"]}
```

Occupancy is the summed `end - start` of every scheduled op on a target, in
abstract time units.

---

## 6. The ledger file set

34 files, `ledgers.LEDGER_FILES`. `LedgerSet._emit` raises `LedgerError` when
any declared file is missing **or** when an undeclared file is produced — the
set is closed in both directions.

The resource-bearing members are `RESOURCE_LEDGER.json`,
`ENTANGLEMENT_INVENTORY_LEDGER.json`, `SCHEDULE_LEDGER.json` (occupancy,
coherence), `COLLECTIVE_LEDGER.json` (bytes, messages, sync counts),
`LOAD_BALANCE_LEDGER.json` (tasks per worker, imbalance ratio),
`TASK_RUNTIME_LEDGER.json`, `NUMERICAL_BACKEND_LEDGER.json` (memory estimates)
and `FARM_EXECUTION_LEDGER.json`.

`LedgerSet.render()` produces deterministic pretty JSON (indent 2, sorted keys,
`ensure_ascii=False`) and `write(dir)` emits every file plus `QCIR_P2.json`.
`digest()` is one SHA-256 over all 34 ledgers, for evidence chaining.

---

## 7. Known accounting gaps

Stated rather than papered over.

| Gap | Consequence |
|---|---|
| `simulator_estimate` is a declared provenance tag that `_resources` never emits | Numerical-engine figures (final-state hash, probabilities, purity, entropy) are reported in `NUMERICAL_BACKEND_LEDGER.json` untagged rather than as tagged resource values. |
| `bytes_moved` charges a fixed 256 B per planned message | It is an `analytical_estimate` and is tagged as one; it is not a measurement of actual payload sizes. The fabric's `ChannelMetrics.bytes_sent` **is** measured, and lives separately. |
| `memory` sums declared claims only | A row with no `RESOURCE memory=` contributes 0. The figure is a declaration census, not a measurement. |
| `energy_estimate` is `work * 1.0` | An abstract proportional model. The note says so. No hardware power measurement exists. |
| Scheduler coherence populates 3 of 6 components | `coherence_exposure` is a lower bound; see `PA_LCTL_ERROR_MODEL.md` §6.3. |
| `qpu_slots` defaults to 0 | Correct: there is no QPU. A claim on `qpu_slots` therefore fails unless the caller explicitly raises the limit, which does not create a QPU. |

---

## 8. Implementation status

| Element | Status | Note |
|---|---|---|
| Provenance tagging with a closed vocabulary | `OPERATIONAL` | `tagged` raises on an unknown tag |
| Classical resource ledger (9 fields) | `OPERATIONAL` | |
| Quantum resource ledger (17 fields) | `OPERATIONAL` | |
| Classical/quantum separation | `OPERATIONAL` | no combined figure of merit exists |
| Circuit census (`exact` figures) | `OPERATIONAL` | counted, not modelled |
| Ebit inventory | `OPERATIONAL` | full lifecycle provenance |
| Federation limits and claim checking | `OPERATIONAL` | fail-closed |
| Topology/link capacity model | `OPERATIONAL` | |
| Runtime measured metrics | `OPERATIONAL` | excluded from replay hashes by design (H7) |
| `simulator_estimate` tag | `SCAFFOLDED` | vocabulary member; not emitted |
| `hardware_estimate` tag | `BLOCKED` | would require an authenticated target |
| `hardware_measured` section | `BLOCKED` | always `unknown` |
| Energy model | `IMPLEMENTED_PARTIAL` | abstract proportional; disclosed in the note |
