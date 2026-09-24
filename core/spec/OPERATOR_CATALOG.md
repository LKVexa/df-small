<!-- GENERATED FILE - do not edit by hand.
     Produced by tools/gen_catalogs.py from the reference implementation
     in pacore/. Re-run the generator after any change to pacore. -->

# PA-LCTL Operator Catalog

Language: **PA-LCTL** &nbsp;&nbsp; Bundle magic: `#PA-LCTL/1.6` &nbsp;&nbsp; Core version: `1.6.0-rc1`

Generated from: `pacore.lang.OP_CATALOG`, `pacore.lang.GATE_ARITY`, `pacore.commutation`, `pacore.simulator`

Total distinct operations: **131** (`lang.ALL_OPS`) across **13** families (`lang.OP_CATALOG`). An `OP` cell outside this set is rejected with `E-OP-001`.

## Families

| Family | Count | Reference duration (SES units) | Members |
|---|---|---|---|
| `prepare` | 7 | 1.0 | `PREP0`, `PREP1`, `PREP_PLUS`, `PREP_MINUS`, `PREP_BASIS`, `PREP_STATE`, `RESET` |
| `single_qubit` | 14 | 1.0 | `I`, `X`, `Y`, `Z`, `H`, `S`, `SDG`, `T`, `TDG`, `RX`, `RY`, `RZ`, `PHASE`, `U` |
| `multi_qubit` | 12 | 2.0 | `CX`, `CNOT`, `CY`, `CZ`, `CH`, `SWAP`, `ISWAP`, `CSWAP`, `CCX`, `TOFFOLI`, `CU`, `MCU` |
| `measure` | 9 | 3.0 | `MEASURE`, `MEASURE_Z`, `MEASURE_X`, `MEASURE_Y`, `MEASURE_BASIS`, `POVM`, `SAMPLE`, `EXPECT`, `VARIANCE` |
| `tensor` | 7 | 1.0 | `TENSOR`, `PARTIAL_TRACE`, `REDUCE`, `PERMUTE_QUBITS`, `PARTITION`, `MERGE`, `ENTANGLE` |
| `operator` | 10 | 1.0 | `KRON`, `COMPOSE`, `ADJOINT`, `EXP_OPERATOR`, `COMMUTATOR`, `ANTICOMMUTATOR`, `PROJECT`, `SPECTRAL`, `DIAGONALIZE`, `PAULI_DECOMPOSE` |
| `evolve` | 7 | 4.0 | `EVOLVE`, `HAMILTONIAN`, `UNITARY_EVOLVE`, `TROTTER`, `SUZUKI`, `ADIABATIC`, `PULSE` |
| `noise` | 11 | 1.0 | `DEPOLARIZE`, `DEPHASE`, `BIT_FLIP`, `PHASE_FLIP`, `AMPLITUDE_DAMP`, `PHASE_DAMP`, `PAULI_CHANNEL`, `KRAUS_CHANNEL`, `READOUT_ERROR`, `LEAKAGE_MODEL`, `CROSSTALK_MODEL` |
| `qec` | 7 | 4.0 | `ENCODE`, `SYNDROME`, `DETECT`, `CORRECT`, `DECODE`, `STABILIZER`, `LOGICAL` |
| `hybrid` | 8 | 1.0 | `CLASSICAL_IF`, `CLASSICAL_SWITCH`, `REPEAT`, `UNTIL`, `WHILE`, `SHOT_LOOP`, `PARAM_BIND`, `FEEDBACK` |
| `distributed_quantum` | 14 | 8.0 | `ENTANGLE_LINK`, `EPR_RESERVE`, `EPR_RELEASE`, `TELEPORT`, `REMOTE_CONTROL`, `REMOTE_CNOT`, `REMOTE_MEASURE`, `CLASSICAL_FEEDBACK`, `ENTANGLEMENT_SWAP`, `PURIFY`, `HERALD`, `SYNC_QCLOCK`, `QCHANNEL_SEND`, `QCHANNEL_RECEIVE` |
| `collective` | 8 | 5.0 | `BROADCAST`, `SCATTER`, `GATHER`, `ALLGATHER`, `REDUCE`, `ALLREDUCE`, `SCAN`, `ALLTOALL` |
| `structural` | 18 | 0.0 | `REGION_BEGIN`, `REGION_END`, `BARRIER`, `FENCE`, `EPOCH`, `SPAWN`, `AWAIT`, `CHANNEL_SEND`, `CHANNEL_RECV`, `DECLARE_NODE`, `DECLARE_DOMAIN`, `DECLARE_LINK`, `DECLARE_TOPOLOGY`, `DECLARE_FEDERATION`, `CLAIM`, `ASSERT_INVARIANT`, `EMIT_LEDGER`, `NOTE` |

## Every operation

| Operation | Family | Qubit arity | Flags | Numerical realization | Protocol template |
|---|---|---|---|---|---|
| `PREP0` | prepare | - | reinit | - | - |
| `PREP1` | prepare | - | reinit | - | - |
| `PREP_PLUS` | prepare | - | reinit | - | - |
| `PREP_MINUS` | prepare | - | reinit | - | - |
| `PREP_BASIS` | prepare | - | reinit | - | - |
| `PREP_STATE` | prepare | - | reinit | - | - |
| `RESET` | prepare | - | reinit | - | - |
| `I` | single_qubit | 1 | diagonal, clifford | 1-qubit reference matrix | - |
| `X` | single_qubit | 1 | clifford | 1-qubit reference matrix | - |
| `Y` | single_qubit | 1 | clifford | 1-qubit reference matrix | - |
| `Z` | single_qubit | 1 | diagonal, clifford | 1-qubit reference matrix | - |
| `H` | single_qubit | 1 | clifford | 1-qubit reference matrix | - |
| `S` | single_qubit | 1 | diagonal, clifford | 1-qubit reference matrix | - |
| `SDG` | single_qubit | 1 | diagonal, clifford | 1-qubit reference matrix | - |
| `T` | single_qubit | 1 | diagonal | 1-qubit reference matrix | - |
| `TDG` | single_qubit | 1 | diagonal | 1-qubit reference matrix | - |
| `RX` | single_qubit | 1 | parametric | 1-qubit reference matrix | - |
| `RY` | single_qubit | 1 | parametric | 1-qubit reference matrix | - |
| `RZ` | single_qubit | 1 | parametric, diagonal | 1-qubit reference matrix | - |
| `PHASE` | single_qubit | 1 | parametric, diagonal | 1-qubit reference matrix | - |
| `U` | single_qubit | 1 | parametric | 1-qubit reference matrix | - |
| `CX` | multi_qubit | 2 | controlled, clifford | 2-qubit reference matrix | - |
| `CNOT` | multi_qubit | 2 | controlled, clifford | 2-qubit reference matrix | - |
| `CY` | multi_qubit | 2 | controlled | 2-qubit reference matrix | - |
| `CZ` | multi_qubit | 2 | controlled, diagonal, clifford | 2-qubit reference matrix | - |
| `CH` | multi_qubit | 2 | controlled | 2-qubit reference matrix | - |
| `SWAP` | multi_qubit | 2 | clifford | 2-qubit reference matrix | - |
| `ISWAP` | multi_qubit | 2 | - | 2-qubit reference matrix | - |
| `CSWAP` | multi_qubit | 3 | controlled | 3-qubit reference matrix | - |
| `CCX` | multi_qubit | 3 | controlled | 3-qubit reference matrix | - |
| `TOFFOLI` | multi_qubit | 3 | controlled | 3-qubit reference matrix | - |
| `CU` | multi_qubit | - | controlled | - | - |
| `MCU` | multi_qubit | - | controlled | - | - |
| `MEASURE` | measure | - | destructive | - | - |
| `MEASURE_Z` | measure | - | destructive | - | - |
| `MEASURE_X` | measure | - | destructive | - | - |
| `MEASURE_Y` | measure | - | destructive | - | - |
| `MEASURE_BASIS` | measure | - | destructive | - | - |
| `POVM` | measure | - | destructive | - | - |
| `SAMPLE` | measure | - | - | - | - |
| `EXPECT` | measure | - | - | - | - |
| `VARIANCE` | measure | - | - | - | - |
| `TENSOR` | tensor | - | - | - | - |
| `PARTIAL_TRACE` | tensor | - | - | - | - |
| `REDUCE` | tensor | - | - | - | - |
| `PERMUTE_QUBITS` | tensor | - | - | - | - |
| `PARTITION` | tensor | - | - | - | - |
| `MERGE` | tensor | - | - | - | - |
| `ENTANGLE` | tensor | - | - | - | - |
| `KRON` | operator | - | - | - | - |
| `COMPOSE` | operator | - | - | - | - |
| `ADJOINT` | operator | - | - | - | - |
| `EXP_OPERATOR` | operator | - | - | - | - |
| `COMMUTATOR` | operator | - | - | - | - |
| `ANTICOMMUTATOR` | operator | - | - | - | - |
| `PROJECT` | operator | - | - | - | - |
| `SPECTRAL` | operator | - | - | - | - |
| `DIAGONALIZE` | operator | - | - | - | - |
| `PAULI_DECOMPOSE` | operator | - | - | - | - |
| `EVOLVE` | evolve | - | - | - | - |
| `HAMILTONIAN` | evolve | - | - | - | - |
| `UNITARY_EVOLVE` | evolve | - | - | - | - |
| `TROTTER` | evolve | - | - | - | - |
| `SUZUKI` | evolve | - | - | - | - |
| `ADIABATIC` | evolve | - | - | - | - |
| `PULSE` | evolve | - | - | - | - |
| `DEPOLARIZE` | noise | - | - | Kraus set, rank 4 | - |
| `DEPHASE` | noise | - | - | Kraus set, rank 2 | - |
| `BIT_FLIP` | noise | - | - | Kraus set, rank 2 | - |
| `PHASE_FLIP` | noise | - | - | Kraus set, rank 2 | - |
| `AMPLITUDE_DAMP` | noise | - | - | Kraus set, rank 2 | - |
| `PHASE_DAMP` | noise | - | - | Kraus set, rank 2 | - |
| `PAULI_CHANNEL` | noise | - | - | Kraus set, rank 4 | - |
| `KRAUS_CHANNEL` | noise | - | - | no admitted Kraus set | - |
| `READOUT_ERROR` | noise | - | - | no admitted Kraus set | - |
| `LEAKAGE_MODEL` | noise | - | - | no admitted Kraus set | - |
| `CROSSTALK_MODEL` | noise | - | - | no admitted Kraus set | - |
| `ENCODE` | qec | - | - | - | - |
| `SYNDROME` | qec | - | - | - | - |
| `DETECT` | qec | - | - | - | - |
| `CORRECT` | qec | - | - | - | - |
| `DECODE` | qec | - | - | - | - |
| `STABILIZER` | qec | - | - | - | - |
| `LOGICAL` | qec | - | - | - | - |
| `CLASSICAL_IF` | hybrid | - | - | - | - |
| `CLASSICAL_SWITCH` | hybrid | - | - | - | - |
| `REPEAT` | hybrid | - | - | - | - |
| `UNTIL` | hybrid | - | - | - | - |
| `WHILE` | hybrid | - | - | - | - |
| `SHOT_LOOP` | hybrid | - | - | - | - |
| `PARAM_BIND` | hybrid | - | - | - | - |
| `FEEDBACK` | hybrid | - | - | - | - |
| `ENTANGLE_LINK` | distributed_quantum | - | - | - | yes |
| `EPR_RESERVE` | distributed_quantum | - | - | - | yes |
| `EPR_RELEASE` | distributed_quantum | - | - | - | yes |
| `TELEPORT` | distributed_quantum | - | - | - | yes |
| `REMOTE_CONTROL` | distributed_quantum | - | - | - | yes |
| `REMOTE_CNOT` | distributed_quantum | - | - | - | yes |
| `REMOTE_MEASURE` | distributed_quantum | - | destructive | - | yes |
| `CLASSICAL_FEEDBACK` | distributed_quantum | - | - | - | yes |
| `ENTANGLEMENT_SWAP` | distributed_quantum | - | - | - | yes |
| `PURIFY` | distributed_quantum | - | - | - | yes |
| `HERALD` | distributed_quantum | - | - | - | yes |
| `SYNC_QCLOCK` | distributed_quantum | - | - | - | yes |
| `QCHANNEL_SEND` | distributed_quantum | - | - | - | yes |
| `QCHANNEL_RECEIVE` | distributed_quantum | - | - | - | yes |
| `BROADCAST` | collective | - | - | - | - |
| `SCATTER` | collective | - | - | - | - |
| `GATHER` | collective | - | - | - | - |
| `ALLGATHER` | collective | - | - | - | - |
| `REDUCE` | collective | - | - | - | - |
| `ALLREDUCE` | collective | - | - | - | - |
| `SCAN` | collective | - | - | - | - |
| `ALLTOALL` | collective | - | - | - | - |
| `REGION_BEGIN` | structural | - | - | - | - |
| `REGION_END` | structural | - | - | - | - |
| `BARRIER` | structural | - | - | - | - |
| `FENCE` | structural | - | - | - | - |
| `EPOCH` | structural | - | - | - | - |
| `SPAWN` | structural | - | - | - | - |
| `AWAIT` | structural | - | - | - | - |
| `CHANNEL_SEND` | structural | - | - | - | - |
| `CHANNEL_RECV` | structural | - | - | - | - |
| `DECLARE_NODE` | structural | - | - | - | - |
| `DECLARE_DOMAIN` | structural | - | - | - | - |
| `DECLARE_LINK` | structural | - | - | - | - |
| `DECLARE_TOPOLOGY` | structural | - | - | - | - |
| `DECLARE_FEDERATION` | structural | - | - | - | - |
| `CLAIM` | structural | - | - | - | - |
| `ASSERT_INVARIANT` | structural | - | - | - | - |
| `EMIT_LEDGER` | structural | - | - | - | - |
| `NOTE` | structural | - | - | - | - |

## Stabilizer-engine Clifford catalog

1-qubit: `H`, `S`, `SDG`, `X`, `Y`, `Z`, `I`

2-qubit: `CX`, `CNOT`, `CZ`, `SWAP`


## Preparation lowering (`simulator.PREPARE_SEQUENCE`)

| Preparation | Gate sequence applied after reset to |0> |
|---|---|
| `PREP0` | (none) |
| `PREP1` | `X` |
| `PREP_PLUS` | `H` |
| `PREP_MINUS` | `X`, `H` |

Preparations in `lang.OPS_PREPARE` without an entry above (`PREP_BASIS`, `PREP_STATE`) have no admitted deterministic realization in `pacore.simulator` and raise `SimulationError`.

## Commutation rules (`pacore.commutation.CommutationAuthority`)

| Rule id | Precondition | Verdict(s) | Exactness |
|---|---|---|---|
| R-DISJOINT | supports are disjoint | DISJOINT | EXACT |
| R-MEAS-BARRIER | measurement / classical-control / barrier boundary | NON_COMMUTING | EXACT |
| R-RESET | RESET on shared support | NON_COMMUTING | EXACT |
| R-DIAG | both operations in DIAGONAL_GATES | COMMUTING_EXACT | EXACT |
| R-ROT-SAME-AXIS | same ROTATION_AXIS on identical support | COMMUTING_EXACT | EXACT |
| R-PAULI-SYMPLECTIC | both operations are Pauli letters | COMMUTING_EXACT / NON_COMMUTING | EXACT |
| R-CLIFFORD-CONJ | tabulated in CLIFFORD_CONJUGATION | COMMUTING_EXACT / NON_COMMUTING | EXACT |
| R-CHANNEL-SAME | identical noise channels on shared support | COMMUTING_TARGET_CONDITIONAL | APPROXIMATE |
| R-CHANNEL-MIXED | channel composed with a non-identical operation | UNRESOLVED | NO_FAITHFUL_FORM |
| R-MATRIX-ORACLE | support <= 6 qubits and both operators embeddable | COMMUTING_NUMERICALLY_VERIFIED / NON_COMMUTING | EXACT_NUMERICAL / EXACT |
| R-UNRESOLVED | no admitted rule applies | UNRESOLVED | NO_FAITHFUL_FORM |

Matrix-oracle tolerance: `1e-12` (`commutation.MATRIX_COMMUTATOR_TOL`); bound `6` qubits (`commutation.MATRIX_ORACLE_MAX_QUBITS`).

Commutation verdict vocabulary (`commutation.COMMUTATION_STATUS`): `DISJOINT`, `COMMUTING_EXACT`, `COMMUTING_NUMERICALLY_VERIFIED`, `COMMUTING_TARGET_CONDITIONAL`, `NON_COMMUTING`, `UNRESOLVED`
