<!-- GENERATED FILE - do not edit by hand.
     Produced by tools/gen_catalogs.py from the reference implementation
     in pacore/. Re-run the generator after any change to pacore. -->

# PA-LCTL Diagnostic Catalog

Language: **PA-LCTL** &nbsp;&nbsp; Bundle magic: `#PA-LCTL/1.6` &nbsp;&nbsp; Core version: `1.6.0-rc1`

Generated from: a source scan of `pacore/lang.py` plus `pacore.conformance.ADMISSION_CODES`

`pacore.lang` emits **38** distinct diagnostic codes. Severity vocabulary: `ERROR`, `REJECT`, `WARN`, `INFO` (`lang.Diagnostic.severity`). A program is invalid when any diagnostic has severity `ERROR` or `REJECT` (`lang.VerifyResult.ok`).

Codes are scanned from the module source, so this table cannot silently fall out of sync: an undocumented code aborts the generator.

## Codes emitted by `pacore.lang`

| Code | Severity | Emitted by | Meaning |
|---|---|---|---|
| `E-ARITY-001` | REJECT | `lang.verify` | the operand count does not match `lang.GATE_ARITY` |
| `E-CLONE-001` | REJECT | `lang.verify` | `TENSOR` / `MERGE` would duplicate a quantum object appearing in both operands |
| `E-CONF-001` | REJECT | `lang.verify` | the `CONF` cell parses to a number outside [0,1] |
| `E-CTL-002` | REJECT | `lang.verify` | a classical control row states no condition |
| `E-CTL-003` | REJECT | `lang.verify` | a classical control row depends on a value that no prior measurement produced |
| `E-CTRL-001` | REJECT | `lang.verify` | a controlled gate names overlapping control and target qubits |
| `E-DOM-001` | REJECT | `lang.verify` | a `DOMAIN` is referenced before its `DECLARE_DOMAIN` row and the row names no node |
| `E-EPR-001` | REJECT | `lang.verify` | `ENTANGLE_LINK` / `EPR_RESERVE` names no `LINK` |
| `E-EPR-002` | REJECT | `lang.verify` | `EPR_RELEASE` names an ebit that was never established |
| `E-EPR-003` | REJECT | `lang.verify` | double release or double consume of one ebit |
| `E-FACE-001` | REJECT | `lang.verify` | the `FACE` cell is not a member of `lang.FACES` |
| `E-FAM-001` | REJECT | `lang.verify` | the `FAMILY` cell is not in `lang.PARALLEL_FAMILIES` |
| `E-GRAM-001` | ERROR | `lang.parse` | a data line contains no column separator |
| `E-GRAM-002` | ERROR | `lang.parse` | a data line has a cell count different from the declared `#COLUMNS` width |
| `E-GRAM-003` | REJECT | `lang.parse` | two rows share a `ROW` identity |
| `E-GRAM-004` | ERROR | `lang.parse` | the bundle magic line is missing; parsing returns no program |
| `E-GRAM-005` | ERROR | `lang.parse` | the bundle contains no data rows |
| `E-LINK-001` | REJECT | `lang.verify` | a `LINK` is referenced before its `DECLARE_LINK` row |
| `E-NODE-001` | REJECT | `lang.verify` | a `NODE` is referenced before its `DECLARE_NODE` row |
| `E-OP-001` | REJECT | `lang.verify` | the `OP` cell is not in the normative operation catalog |
| `E-OWN-001` | REJECT | `lang.verify` | use-after-move: the object's ownership was transferred by a prior protocol row |
| `E-OWN-002` | REJECT | `lang.verify` | use-after-destructive-measure: `RESET` or a preparation is required before reuse |
| `E-OWN-003` | REJECT | `lang.verify` | a local operation acts on an object whose authoritative owner is another node; a distributed primitive is required |
| `E-OWN-005` | REJECT | `lang.verify` | a quantum object is used before any preparation established ownership |
| `E-OWN-006` | REJECT | `lang.verify` | use of a resource already released by `EPR_RELEASE` |
| `E-PARAM-001` | REJECT | `lang.verify` | a parametric gate carries no usable `PARAM` angle |
| `E-POL-001` | REJECT | `lang.verify` | the `#NETWORK` directive is not `deny` |
| `E-POL-002` | REJECT | `lang.verify` | the `#BACKEND` directive is not `none` |
| `E-PROB-001` | REJECT | `lang.verify` | a noise parameter lies outside the probability domain [0,1] |
| `E-PROTO-001` | REJECT | `lang.verify` | `TELEPORT` is missing `A` (source) or `OUT` (destination) |
| `E-PROTO-002` | REJECT | `lang.verify` | `TELEPORT` names no `LINK` carrying an ebit |
| `E-PROTO-003` | REJECT | `lang.verify` | the `TELEPORT` source object was never prepared |
| `E-PROTO-004` | REJECT | `lang.verify` | `REMOTE_CNOT` / `REMOTE_CONTROL` names no `LINK` |
| `E-REG-001` | REJECT | `lang.verify` | the `REGIME` cell is not a member of `lang.REGIMES` |
| `E-REG-002` | REJECT | `lang.verify` | a noise operation declares an exact regime |
| `E-REG-003` | REJECT | `lang.verify` | a row declares an exact regime while carrying `approx=true` in `ERROR` |
| `E-TYPE-001` | REJECT | `lang.verify` | the `TYPE` base name is not in `lang.ALL_TYPES` |
| `W-PROTO-005` | WARN | `lang.verify` | a remote gate is applied to co-located qubits; a local gate is cheaper and semantically identical |

> Note: `E-OWN-004` is not assigned. The ownership family is `E-OWN-001`, `-002`, `-003`, `-005`, `-006`; the gap is real and is documented rather than renumbered, because diagnostic codes are a stable external interface.

## Codes emitted by the admission pipeline (`conformance.ADMISSION_CODES`)

41 additional codes are produced by `conformance.admit`, the full admission pipeline that runs after `lang.verify` succeeds.

| Code | Meaning |
|---|---|
| `E-CAL-001` | the bound target calibration is stale at the claimed epoch |
| `E-CKPT-001` | a checkpoint of unknown quantum state was requested |
| `E-CLAIM-001` | physical execution is claimed by a classical simulator |
| `E-CLAIM-002` | physical distributed execution is claimed without two authenticated endpoints |
| `E-COH-001` | planned coherence exposure exceeds the declared budget |
| `E-COMM-001` | planned communication exceeds the declared budget |
| `E-CONC-MEASDEP` | rows asserted concurrent share a measured subsystem |
| `E-CONC-NONCOMMUTE` | rows asserted concurrent provably do not commute |
| `E-CONC-RESOURCE` | rows asserted concurrent contend for one link |
| `E-CONC-RW` | rows asserted concurrent have a read/write conflict |
| `E-CONC-TARGET` | the bound target cannot host the asserted concurrency |
| `E-CONC-WW` | rows asserted concurrent write the same object |
| `E-CONS-001` | the requested consistency guarantee is not achievable |
| `E-COUPLE-001` | no admitted rule resolves the coupling of an asserted pair |
| `E-CPATH-001` | a protocol needs a classical path that does not exist |
| `E-CRDT-001` | an invalid CRDT merge was attempted |
| `E-DEADLOCK-001` | the declared wait graph contains a cycle |
| `E-DENS-001` | a declared density matrix has a negative eigenvalue |
| `E-DENS-002` | a declared density matrix does not have unit trace |
| `E-DIM-001` | declared tensor dimensions are incompatible |
| `E-EBIT-001` | the requested ebits exceed the admitted link capacity |
| `E-EBIT-002` | the referenced ebit is expired at the claimed epoch |
| `E-ERRC-001` | error terms with incompatible units cannot be composed |
| `E-HERM-001` | a matrix declared Hermitian is not Hermitian |
| `E-KRAUS-001` | a declared Kraus set is not trace preserving |
| `E-MEM-001` | declared memory claims exceed the bound target capacity |
| `E-NORM-001` | a declared state preparation is not normalized |
| `E-POVM-001` | a declared POVM does not resolve the identity |
| `E-PROJ-001` | a declared projector is not idempotent and Hermitian |
| `E-PROTO-COMPILE` | the protocol compiler refused this program |
| `E-PROV-001` | a result is claimed without a provenance reference |
| `E-QROUTE-001` | no quantum route exists between the required endpoints |
| `E-RECOV-001` | the only admissible recovery would require cloning |
| `E-REPLAY-001` | deterministic replay does not reproduce the recorded state |
| `E-ROUTE-001` | no classical route exists between the required endpoints |
| `E-SEAL-001` | the declared topology seal does not match the topology |
| `E-SEAL-002` | the declared schedule seal does not match the schedule |
| `E-STEAL-001` | a task holding unknown quantum state was stolen |
| `E-TARGET-001` | the bound target does not support the requested operation |
| `E-UNIT-001` | a matrix declared unitary is not unitary |
| `E-XTALK-001` | asserted concurrency violates a calibrated crosstalk pair |

## Admission pipeline stages

`conformance.admit` fails closed at the first stage that produces a diagnostic; later stages never run.

| # | Stage |
|---|---|
| 1 | `parse` |
| 2 | `verify` |
| 3 | `target_binding` |
| 4 | `memory` |
| 5 | `calibration` |
| 6 | `seals` |
| 7 | `routing` |
| 8 | `entanglement` |
| 9 | `numerical_claims` |
| 10 | `state_claims` |
| 11 | `provenance` |
| 12 | `concurrency` |
| 13 | `budgets` |
| 14 | `protocol_compile` |
| 15 | `error_algebra` |
| 16 | `runtime_contracts` |
| 17 | `deadlock` |

## Fail-closed exception types

| Exception | Module | Raised for |
|---|---|---|
| `PALCTLError` | `pacore.lang` | malformed invocation, never program rejection |
| `SimulationError` | `pacore.simulator` | any fail-closed condition in a numerical engine |
| `BackendPlanError` | `pacore.simulator` | no admitted backend can serve a circuit summary |
| `ProtocolError` | `pacore.protocols` | every fail-closed condition in the protocol fabric |
| `FabricError` (+ 16 subclasses) | `pacore.fabric` | every fail-closed fabric condition |
| `CRDTError` (+ 3 subclasses) | `pacore.crdt` | every fail-closed CRDT/consistency condition |
| `ResilienceError` (+ 5 subclasses) | `pacore.resilience` | every fail-closed resilience condition |
| `ErrorAlgebraError` (+ 4 subclasses) | `pacore.erroralgebra` | every fail-closed error-algebra condition |
| `LedgerError` | `pacore.ledgers` | a ledger set that cannot be built or is incomplete |
| `CommandBlocked` / `CommandRejected` | `pacore.cli` | capability absent / program refused |

## Fabric, CRDT and resilience tokens

| Symbol | Token |
|---|---|
| `fabric.TOKEN_DEADLOCK` | `DISTRIBUTED_DEADLOCK_DETECTED` |
| `fabric.TOKEN_THRASH` | `ADAPTATION_THRASH_DETECTED` |
| `fabric.TOKEN_REPLAY_MISMATCH` | `REPLAY_MISMATCH` |
| `fabric.TOKEN_REPLAY_PASS` | `DETERMINISTIC_REPLAY_PASS` |
| `fabric.TOKEN_LOAD_BALANCE_LEDGER` | `LOAD_BALANCE_LEDGER` |
| `fabric.TOKEN_STEAL_REJECTED` | `QUANTUM_TASK_STEAL_REJECTED` |
| `fabric.TOKEN_CLONE_REJECTED` | `QUANTUM_CLONE_REJECTED` |
| `fabric.TOKEN_LIVELOCK` | `RETRY_STORM_DETECTED` |
| `fabric.TOKEN_STARVATION` | `STARVATION_DETECTED` |
| `fabric.TOKEN_UNSATISFIED_FUTURE` | `UNSATISFIED_FUTURE_DETECTED` |
| `crdt.TOKEN_BLOCKED` | `CONSISTENCY_GUARANTEE_BLOCKED` |
| `crdt.TOKEN_ADMITTED` | `CONSISTENCY_GUARANTEE_ADMITTED` |
| `crdt.TOKEN_QUANTUM_REJECTED` | `QUANTUM_REPLICATION_REJECTED` |
| `crdt.TOKEN_LAWS_VERIFIED` | `CRDT_LAWS_VERIFIED` |
| `crdt.TOKEN_LAWS_FAILED` | `CRDT_LAWS_FAILED` |
| `crdt.TOKEN_CONVERGED` | `ANTI_ENTROPY_CONVERGED` |
| `crdt.TOKEN_NOT_CONVERGED` | `ANTI_ENTROPY_NOT_CONVERGED` |
| `resilience.TOKEN_RECOVERY_IMPOSSIBLE` | `RECOVERY_IMPOSSIBLE` |
| `resilience.TOKEN_CHECKPOINT_REFUSED` | `QUANTUM_CHECKPOINT_REFUSED` |
| `resilience.TOKEN_ROLLBACK_REFUSED` | `QUANTUM_ROLLBACK_REFUSED` |
| `resilience.TOKEN_CAMPAIGN_COMPLETE` | `RESILIENCE_CAMPAIGN_COMPLETE` |
| `resilience.TOKEN_CHECKSUM_MISMATCH` | `CHECKPOINT_CHECKSUM_MISMATCH` |
| `erroralgebra.TOKEN_UNRESOLVED` | `ERROR_COMPOSITION_UNRESOLVED` |
| `erroralgebra.TOKEN_SCHEDULE_BLOCKED` | `SCHEDULE_BLOCKED_COHERENCE` |
| `protocols.TELEPORT_PASS_TOKEN` | `TELEPORT_REFERENCE_EQUIVALENCE_PASS` |
| `protocols.REMOTE_CNOT_PASS_TOKEN` | `REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS` |
| `protocols.SWAP_PASS_TOKEN` | `ENTANGLEMENT_SWAP_REFERENCE_EQUIVALENCE_PASS` |
| `protocols.FAIL_TOKEN` | `REFERENCE_EQUIVALENCE_FAIL` |
| `ledgers.BLOCKED_EXTERNAL_AUTHORITY` | `BLOCKED_EXTERNAL_AUTHORITY` |
| `ledgers.SOAK_NOT_RUN_TOKEN` | `SOAK_NOT_RUN` |
| `cli.BLOCKED_CAPABILITY_ABSENT` | `BLOCKED_CAPABILITY_ABSENT` |
| `cli.PROGRAM_REJECTED` | `PROGRAM_REJECTED` |
