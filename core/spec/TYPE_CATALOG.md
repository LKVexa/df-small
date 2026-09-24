<!-- GENERATED FILE - do not edit by hand.
     Produced by tools/gen_catalogs.py from the reference implementation
     in pacore/. Re-run the generator after any change to pacore. -->

# PA-LCTL Type Catalog

Language: **PA-LCTL** &nbsp;&nbsp; Bundle magic: `#PA-LCTL/1.6` &nbsp;&nbsp; Core version: `1.6.0-rc1`

Generated from: `pacore.lang` type-group tuples and `pacore.lang.QUANTUM_OWNED_TYPES`

Total distinct type names: **128** (`lang.ALL_TYPES`). Ownership-governed types: **13** (`lang.QUANTUM_OWNED_TYPES`).

A `TYPE` cell is accepted when its base name (the text before the first `[`) is a member of `lang.ALL_TYPES`; otherwise the verifier emits `E-TYPE-001`.

## Groups

| Group | Symbol | Count | Classical/quantum |
|---|---|---|---|
| Scalar | `lang.SCALAR_TYPES` | 9 | classical |
| Quantum state | `lang.QUANTUM_STATE_TYPES` | 13 | quantum |
| Operator | `lang.OPERATOR_TYPES` | 14 | quantum |
| Structural | `lang.STRUCTURAL_TYPES` | 12 | classical |
| Classical | `lang.CLASSICAL_TYPES` | 12 | classical |
| Parallel kernel | `lang.PARALLEL_TYPES` | 28 | classical |
| Distributed kernel | `lang.DISTRIBUTED_TYPES` | 24 | classical |
| Federation | `lang.FEDERATION_TYPES` | 16 | classical |

## Every type

| Type | Group | Ownership-governed | Domain |
|---|---|---|---|
| `amplitude` | Scalar | no | classical |
| `probability` | Scalar | no | classical |
| `phase` | Scalar | no | classical |
| `angle` | Scalar | no | classical |
| `complex` | Scalar | no | classical |
| `real` | Scalar | no | classical |
| `time` | Scalar | no | classical |
| `frequency` | Scalar | no | classical |
| `energy` | Scalar | no | classical |
| `qubit` | Quantum state | yes | quantum |
| `qudit` | Quantum state | yes | quantum |
| `qreg` | Quantum state | yes | quantum |
| `ket` | Quantum state | yes | quantum |
| `bra` | Quantum state | no | quantum |
| `pure_state` | Quantum state | yes | quantum |
| `mixed_state` | Quantum state | yes | quantum |
| `density` | Quantum state | yes | quantum |
| `subsystem` | Quantum state | yes | quantum |
| `bipartite_state` | Quantum state | yes | quantum |
| `multipartite_state` | Quantum state | yes | quantum |
| `entangled_state` | Quantum state | yes | quantum |
| `separable_state` | Quantum state | yes | quantum |
| `operator` | Operator | no | quantum |
| `linear_operator` | Operator | no | quantum |
| `hermitian` | Operator | no | quantum |
| `unitary` | Operator | no | quantum |
| `projector` | Operator | no | quantum |
| `povm` | Operator | no | quantum |
| `hamiltonian` | Operator | no | quantum |
| `kraus_set` | Operator | no | quantum |
| `channel` | Operator | no | quantum |
| `cptp` | Operator | no | quantum |
| `isometry` | Operator | no | quantum |
| `permutation` | Operator | no | quantum |
| `controlled_operator` | Operator | no | quantum |
| `oracle` | Operator | no | quantum |
| `tensor` | Structural | no | classical |
| `tensor_product` | Structural | no | classical |
| `graph` | Structural | no | classical |
| `coupling_graph` | Structural | no | classical |
| `basis` | Structural | no | classical |
| `spectrum` | Structural | no | classical |
| `eigensystem` | Structural | no | classical |
| `sparse_operator` | Structural | no | classical |
| `block_operator` | Structural | no | classical |
| `pauli_string` | Structural | no | classical |
| `stabilizer` | Structural | no | classical |
| `syndrome` | Structural | no | classical |
| `bit` | Classical | no | classical |
| `integer` | Classical | no | classical |
| `real_c` | Classical | no | classical |
| `complex_c` | Classical | no | classical |
| `vector` | Classical | no | classical |
| `matrix` | Classical | no | classical |
| `distribution` | Classical | no | classical |
| `measurement_result` | Classical | no | classical |
| `histogram` | Classical | no | classical |
| `confidence_interval` | Classical | no | classical |
| `resource_report` | Classical | no | classical |
| `proof_record` | Classical | no | classical |
| `task` | Parallel kernel | no | classical |
| `qtask` | Parallel kernel | no | classical |
| `ctask` | Parallel kernel | no | classical |
| `lane` | Parallel kernel | no | classical |
| `epoch` | Parallel kernel | no | classical |
| `tick` | Parallel kernel | no | classical |
| `event` | Parallel kernel | no | classical |
| `future` | Parallel kernel | no | classical |
| `promise` | Parallel kernel | no | classical |
| `dependency` | Parallel kernel | no | classical |
| `barrier` | Parallel kernel | no | classical |
| `fence` | Parallel kernel | no | classical |
| `critical_path` | Parallel kernel | no | classical |
| `work` | Parallel kernel | no | classical |
| `span` | Parallel kernel | no | classical |
| `parallel_region` | Parallel kernel | no | classical |
| `pipeline` | Parallel kernel | no | classical |
| `stage` | Parallel kernel | no | classical |
| `partition` | Parallel kernel | no | classical |
| `shard` | Parallel kernel | no | classical |
| `replica` | Parallel kernel | no | classical |
| `reduction` | Parallel kernel | no | classical |
| `scan` | Parallel kernel | no | classical |
| `collective` | Parallel kernel | no | classical |
| `scheduler_hint` | Parallel kernel | no | classical |
| `placement_constraint` | Parallel kernel | no | classical |
| `locality_constraint` | Parallel kernel | no | classical |
| `resource_claim` | Parallel kernel | no | classical |
| `node` | Distributed kernel | no | classical |
| `cluster` | Distributed kernel | no | classical |
| `device` | Distributed kernel | no | classical |
| `cpu` | Distributed kernel | no | classical |
| `gpu` | Distributed kernel | no | classical |
| `qpu` | Distributed kernel | no | classical |
| `memory_domain` | Distributed kernel | no | classical |
| `numa_domain` | Distributed kernel | no | classical |
| `network` | Distributed kernel | no | classical |
| `link` | Distributed kernel | no | classical |
| `classical_channel` | Distributed kernel | no | classical |
| `quantum_link` | Distributed kernel | no | classical |
| `communicator` | Distributed kernel | no | classical |
| `route` | Distributed kernel | no | classical |
| `topology` | Distributed kernel | no | classical |
| `region` | Distributed kernel | no | classical |
| `distributed_partition` | Distributed kernel | no | classical |
| `remote_handle` | Distributed kernel | no | classical |
| `remote_result` | Distributed kernel | no | classical |
| `remote_event` | Distributed kernel | no | classical |
| `consistency_scope` | Distributed kernel | no | classical |
| `failure_domain` | Distributed kernel | no | classical |
| `checkpoint_scope` | Distributed kernel | no | classical |
| `ebit` | Distributed kernel | yes | classical |
| `federation` | Federation | no | classical |
| `execution_domain` | Federation | no | classical |
| `domain_group` | Federation | no | classical |
| `worker_group` | Federation | no | classical |
| `placement_set` | Federation | no | classical |
| `route_set` | Federation | no | classical |
| `trust_domain` | Federation | no | classical |
| `consistency_domain` | Federation | no | classical |
| `calibration_domain` | Federation | no | classical |
| `resource_pool` | Federation | no | classical |
| `ebit_pool` | Federation | no | classical |
| `memory_pool` | Federation | no | classical |
| `task_pool` | Federation | no | classical |
| `shot_pool` | Federation | no | classical |
| `parameter_pool` | Federation | no | classical |
| `circuit_pool` | Federation | no | classical |

## Ownership-governed set

`bipartite_state`, `density`, `ebit`, `entangled_state`, `ket`, `mixed_state`, `multipartite_state`, `pure_state`, `qreg`, `qubit`, `qudit`, `separable_state`, `subsystem`

## Consistency, trust, profile and family vocabularies

**lang.REGIMES** (15): `EXACT`, `EXACT_LINEAR`, `PIECEWISE_EXACT`, `PERTURBATIVE`, `LINEARIZED`, `VARIATIONAL`, `STOCHASTIC`, `ASYMPTOTIC`, `APPROXIMATE`, `NOISY`, `HARDWARE_CALIBRATED`, `TARGET_SPECIFIC`, `NO_FAITHFUL_FORM`, `UNSUPPORTED`, `IMPOSSIBLE`

**lang.EXACT_REGIMES** (3): `EXACT`, `EXACT_LINEAR`, `PIECEWISE_EXACT`

**lang.PARALLEL_FAMILIES** (17): `INSTRUCTION_PARALLEL`, `TENSOR_FACTOR_PARALLEL`, `CIRCUIT_PARALLEL`, `SHOT_PARALLEL`, `PARAMETER_PARALLEL`, `OBSERVABLE_PARALLEL`, `TRAJECTORY_PARALLEL`, `TASK_PARALLEL`, `PIPELINE_PARALLEL`, `DATA_PARALLEL`, `NUMERICAL_SHARD_PARALLEL`, `STABILIZER_BATCH_PARALLEL`, `TENSOR_CONTRACTION_PARALLEL`, `CLASSICAL_REPLICA_PARALLEL`, `PROTOCOL_PARALLEL`, `SUBTREE_PARALLEL`, `EXCHANGE_PIPELINE_PARALLEL`

**lang.PARALLEL_REGIONS** (8): `PARALLEL_REGION`, `DISTRIBUTED_REGION`, `DATAFLOW_REGION`, `ASYNC_REGION`, `BSP_REGION`, `ENSEMBLE_REGION`, `NUMERICAL_SHARD_REGION`, `QNETWORK_REGION`

**lang.CONSISTENCY_PROFILES** (7): `SINGLE_OWNER`, `EVENTUAL`, `CAUSAL`, `SEQUENTIAL`, `LINEARIZABLE`, `IMMUTABLE_REPLICA`, `CRDT_DECLARED`

**lang.TRUST_DOMAINS** (5): `LOCAL_TRUSTED`, `LOCAL_UNTRUSTED`, `REMOTE_AUTHENTICATED`, `REMOTE_UNAUTHENTICATED`, `PHYSICAL_TARGET_AUTHENTICATED`

**lang.EXECUTION_PROFILES** (4): `single_process_deterministic`, `multi_thread_deterministic`, `multi_process_deterministic`, `multi_process_throughput`

**lang.EPR_STATES** (8): `REQUESTED`, `GENERATING`, `HERALDED`, `RESERVED`, `CONSUMED`, `EXPIRED`, `FAILED`, `RELEASED`

**lang.EDGE_REASONS** (14): `DATA_DEPENDENCY`, `QUANTUM_OWNERSHIP`, `ENTANGLEMENT_DEPENDENCY`, `MEASUREMENT_DEPENDENCY`, `CLASSICAL_CONTROL`, `RESOURCE_CONFLICT`, `TOPOLOGY_CONSTRAINT`, `COMMUNICATION`, `BARRIER`, `COUPLING`, `MEMORY_ORDER`, `TARGET_RESTRICTION`, `ERROR_BUDGET`, `USER_ORDER`

**lang.RECOVERY_CLASSES** (8): `RETRY_SAFE`, `RESTART_FROM_CLASSICAL_BOUNDARY`, `REROUTE_SAFE`, `REGENERATE_ENTANGLEMENT`, `REPREPARE_KNOWN_STATE`, `ROLLBACK_CLASSICAL_ONLY`, `RECOVERY_TARGET_SPECIFIC`, `RECOVERY_IMPOSSIBLE`

**lang.Q6_ADMISSION** (9): `ADMIT_EXACT`, `ADMIT_NUMERICALLY_VERIFIED`, `ADMIT_APPROXIMATE`, `ADMIT_TARGET_SPECIFIC`, `OBSERVE`, `QUARANTINE`, `REJECT_INVALID`, `REJECT_NO_FAITHFUL_FORM`, `REJECT_IMPOSSIBLE`

**lang.STATUS_VOCABULARY** (7): `BLOCKED`, `SPECIFIED`, `SCAFFOLDED`, `IMPLEMENTED`, `VERIFIED`, `OPERATIONAL`, `QUALIFIED`

**lang.CONCURRENCY_DECISIONS** (12): `PARALLEL_EXACT`, `PARALLEL_TARGET_CONDITIONAL`, `SERIALIZE_DATA`, `SERIALIZE_OWNERSHIP`, `SERIALIZE_MEASUREMENT`, `SERIALIZE_COUPLING`, `SERIALIZE_RESOURCE`, `SERIALIZE_TOPOLOGY`, `SERIALIZE_TIMING`, `SERIALIZE_CROSSTALK`, `SERIALIZE_ERROR_BUDGET`, `REJECT_INVALID`

**lang.COUPLING_VERDICTS** (6): `DECOUPLED`, `WEAKLY_COUPLED`, `MODERATELY_COUPLED`, `STRONGLY_COUPLED`, `MONOLITHIC_REQUIRED`, `COUPLING_UNRESOLVED`

**lang.PARALLEL_STATES** (6): `SERIAL`, `LOGICAL_PARALLEL`, `SCHEDULED_PARALLEL`, `PARALLEL_EMULATION`, `PHYSICAL_PARALLEL_CANDIDATE`, `PHYSICAL_PARALLEL_EXECUTION`

**lang.DISTRIBUTED_STATES** (7): `LOCAL_ONLY`, `LOGICAL_DISTRIBUTED`, `DISTRIBUTED_SCHEDULED`, `DISTRIBUTED_CLASSICAL_EMULATION`, `DISTRIBUTED_COMPILED`, `PHYSICAL_DISTRIBUTED_CANDIDATE`, `PHYSICAL_DISTRIBUTED_EXECUTION`

**lang.QUANTUM_BOUNDARY_STATES** (3): `QUANTUM_BOUNDARY_NOT_CROSSED`, `QUANTUM_BOUNDARY_CANDIDATE`, `QUANTUM_BOUNDARY_CROSSED`

**lang.GAP_TERMINAL_STATES** (7): `QUALIFIED`, `OPERATIONAL`, `VERIFIED`, `IMPLEMENTED`, `IMPLEMENTED_PARTIAL`, `BLOCKED_EXTERNAL_AUTHORITY`, `REJECTED_NO_FAITHFUL_FORM`

