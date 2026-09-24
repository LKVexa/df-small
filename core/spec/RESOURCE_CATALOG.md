<!-- GENERATED FILE - do not edit by hand.
     Produced by tools/gen_catalogs.py from the reference implementation
     in pacore/. Re-run the generator after any change to pacore. -->

# PA-LCTL Resource Catalog

Language: **PA-LCTL** &nbsp;&nbsp; Bundle magic: `#PA-LCTL/1.6` &nbsp;&nbsp; Core version: `1.6.0-rc1`

Generated from: `pacore.ledgers` field tuples and `LedgerSet._resources`

## Value provenance vocabulary (`ledgers.VALUE_PROVENANCE`)

| Tag | Meaning |
|---|---|
| `exact` | counted from the program or the extracted circuit; no model intervened |
| `compiler_derived` | computed by this compiler from declared inputs |
| `analytical_estimate` | produced by a declared analytic model over declared parameters |
| `simulator_estimate` | produced by a numerical engine in this package |
| `hardware_estimate` | would require a physical target; never emitted by this runtime |
| `unknown` | no admissible source exists |

## Classical resource fields (`ledgers.CLASSICAL_RESOURCE_FIELDS`)

| Field | Derivation | Provenance tag emitted |
|---|---|---|
| `cpu_work` | sum of SES `duration_model` values, abstract time units | `compiler_derived` |
| `memory` | sum of declared `RESOURCE` memory claims | `compiler_derived` |
| `bytes_moved` | planned payloads over planned routes | `analytical_estimate` |
| `messages` | count of planned cross-partition messages | `compiler_derived` |
| `collectives` | count of rows whose OP is in lang.OPS_COLLECTIVE | `exact` |
| `barriers` | count of BARRIER / FENCE / EPOCH rows | `exact` |
| `latency_estimate` | schedule makespan in abstract time units | `analytical_estimate` |
| `critical_path` | partial-order span D | `compiler_derived` |
| `energy_estimate` | abstract proportional model; no hardware power measurement exists | `analytical_estimate` |

## Quantum resource fields (`ledgers.QUANTUM_RESOURCE_FIELDS`)

| Field | Derivation | Provenance tag emitted |
|---|---|---|
| `logical_qubits` | distinct qubit keys in the extracted circuit | `exact` |
| `gate_counts_by_class` | census over lang operation families | `exact` |
| `two_qubit_gate_count` | counted from the extracted circuit | `exact` |
| `t_count` | counted T / TDG gates | `exact` |
| `clifford_count` | counted Clifford gates | `exact` |
| `depth` | frontier depth over the extracted circuit | `exact` |
| `measurement_count` | counted measurement operations | `exact` |
| `shots` | shots actually executed, else unknown | `exact \| unknown` |
| `swap_count` | counted SWAP / ISWAP rows | `exact` |
| `remote_gate_count` | counted lang.OPS_DISTRIBUTED_Q rows | `exact` |
| `ebits_generated` | ENTANGLE_LINK / EPR_RESERVE rows | `compiler_derived` |
| `ebits_consumed` | TELEPORT / REMOTE_CNOT / REMOTE_CONTROL / ENTANGLEMENT_SWAP rows | `compiler_derived` |
| `entanglement_swaps` | counted ENTANGLEMENT_SWAP rows | `exact` |
| `purification_rounds` | planned route rounds plus PURIFY rows | `compiler_derived` |
| `quantum_link_attempts` | planned ebit demand over the declared topology | `analytical_estimate` |
| `expected_fidelity` | worst planned route fidelity from the declared link model | `analytical_estimate` |
| `coherence_exposure` | schedule coherence total, abstract time units | `analytical_estimate` |

## Provenance record fields (`ledgers.PROVENANCE_FIELDS`)

Exactly 17 fields; `LedgerSet._provenance` raises `LedgerError` if the emitted record's key set differs.

`language`, `profile`, `execution_class`, `parallel_state`, `distributed_state`, `quantum_boundary`, `target`, `target_verified`, `topology_hash`, `schedule_hash`, `source_hash`, `qcir_hash`, `ses_hash`, `proof_ledger_hash`, `physical_qpu`, `physical_parallel`, `physical_distributed`

## QCIR-P2 sections (`ledgers.QCIRP2_SECTIONS`)

15 sections, schema `PA-LCTL/QCIR-P2/1`, replay modes `DETERMINISTIC_REPLAY`, `SEEDED_REPLAY`, `NO_REPLAY`.

| # | Section |
|---|---|
| 1 | `ses_graph` |
| 2 | `partition_graph` |
| 3 | `placement_table` |
| 4 | `classical_route_table` |
| 5 | `quantum_route_table` |
| 6 | `temporal_schedule` |
| 7 | `resource_reservations` |
| 8 | `ebit_lifecycle` |
| 9 | `failure_domains` |
| 10 | `recovery_policy` |
| 11 | `calibration_epoch` |
| 12 | `error_ledger_refs` |
| 13 | `proof_ledger_refs` |
| 14 | `replay_mode` |
| 15 | `deterministic_schedule_seed` |

## Ledger file set (`ledgers.LEDGER_FILES`)

34 files. `LedgerSet._emit` raises `LedgerError` if any file is missing or unknown.

| # | File |
|---|---|
| 1 | `SES.json` |
| 2 | `COMMUTATION_LEDGER.json` |
| 3 | `PARTITION_LEDGER.json` |
| 4 | `PARETO_LEDGER.json` |
| 5 | `PLACEMENT_LEDGER.json` |
| 6 | `ROUTING_LEDGER.json` |
| 7 | `QUANTUM_ROUTING_LEDGER.json` |
| 8 | `ENTANGLEMENT_INVENTORY_LEDGER.json` |
| 9 | `SCHEDULE_LEDGER.json` |
| 10 | `DYNAMIC_SCHEDULE_LEDGER.json` |
| 11 | `PARALLEL_OPPORTUNITY_LEDGER.json` |
| 12 | `PARTIAL_ORDER_LEDGER.json` |
| 13 | `HYPERGRAPH_PARTITION_LEDGER.json` |
| 14 | `HIERARCHICAL_PLACEMENT_LEDGER.json` |
| 15 | `TASK_RUNTIME_LEDGER.json` |
| 16 | `CONSISTENCY_LEDGER.json` |
| 17 | `COLLECTIVE_LEDGER.json` |
| 18 | `FARM_EXECUTION_LEDGER.json` |
| 19 | `LOAD_BALANCE_LEDGER.json` |
| 20 | `STRAGGLER_LEDGER.json` |
| 21 | `NUMERICAL_BACKEND_LEDGER.json` |
| 22 | `PROTOCOL_COMPILER_LEDGER.json` |
| 23 | `PROTOCOL_LEDGER.json` |
| 24 | `RESOURCE_LEDGER.json` |
| 25 | `ERROR_LEDGER.json` |
| 26 | `ERROR_COMPOSITION_LEDGER.json` |
| 27 | `FAILURE_LEDGER.json` |
| 28 | `RECOVERY_LEDGER.json` |
| 29 | `LIVE_RECOVERY_LEDGER.json` |
| 30 | `EVENT_RECONSTRUCTION_LEDGER.json` |
| 31 | `PROVENANCE_LEDGER.json` |
| 32 | `ADMISSION_LEDGER.json` |
| 33 | `SOAK_LEDGER.json` |
| 34 | `RELEASE_QUALIFICATION.json` |

## Release outputs (`ledgers.RELEASE_OUTPUTS`)

| Output | Evidence groups | Status in this environment |
|---|---|---|
| `NATIVE_PARALLEL_EXECUTION` | scheduling, task_runtime, commutation | QUALIFIED only with a zero-failure conformance report |
| `NATIVE_DISTRIBUTED_EXECUTION` | placement_routing, collectives, federation | QUALIFIED only with a zero-failure conformance report |
| `DISTRIBUTED_NUMERICAL_SIMULATION` | numerical_backends, partitioning | QUALIFIED only with a zero-failure conformance report |
| `DISTRIBUTED_PROTOCOL_EMULATION` | protocols, resilience, provenance_replay | QUALIFIED only with a zero-failure conformance report |
| `PHYSICAL_PARALLEL_QPU_EXECUTION` | (none) | `BLOCKED_EXTERNAL_AUTHORITY` always |
| `PHYSICAL_DISTRIBUTED_QPU_EXECUTION` | (none) | `BLOCKED_EXTERNAL_AUTHORITY` always |

## Federation resource limits (`fabric.ResourceLimits`)

| Dimension | Default |
|---|---|
| `cpu_slots` | 1 |
| `memory_bytes` | 1073741824 |
| `qpu_slots` | 0 |
| `ebit_budget` | 0 |
| `bandwidth_bytes_per_tick` | 1000000.0 |

## Checkpoint kinds (`resilience.CHECKPOINT_KINDS`)

`classical_compiler`, `source`, `ir`, `scheduler`, `classical_measurement`, `classically_known_state_prep`, `simulator_state`

## Numerical backends (`simulator.BACKENDS`)

| Backend | Engine behind it | Execution label |
|---|---|---|
| `local_statevector` | StatevectorEngine | `CLASSICAL_LOCAL_STATEVECTOR_SIMULATION` |
| `distributed_statevector` | StatevectorEngine (labelled distributed) | `CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION` |
| `local_density` | DensityEngine | `CLASSICAL_LOCAL_DENSITY_MATRIX_SIMULATION` |
| `distributed_density` | DensityEngine (labelled distributed) | `CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION` |
| `stabilizer` | StabilizerEngine | `CLASSICAL_STABILIZER_TABLEAU_SIMULATION` |
| `tensor_network` | NONE - planning target only | `CLASSICAL_LOCAL_STATEVECTOR_SIMULATION` |
| `shot_farm` | StatevectorEngine (labelled distributed) | `CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION` |
| `circuit_farm` | StatevectorEngine (labelled distributed) | `CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION` |
| `trajectory_farm` | DensityEngine (labelled distributed) | `CLASSICAL_DISTRIBUTED_STATEVECTOR_SIMULATION` |

## Farm thresholds

| Constant | Value |
|---|---|
| `simulator.CIRCUIT_FARM_MIN` | 8 |
| `simulator.SHOT_FARM_MIN` | 1024 |
| `simulator.TRAJECTORY_FARM_MIN` | 64 |
| `simulator.LOCAL_STATEVECTOR_MAX_QUBITS` | 20 |
| `simulator.DEFAULT_MEMORY_BUDGET` | 536870912 |
