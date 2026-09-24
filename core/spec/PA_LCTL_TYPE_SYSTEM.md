# PA-LCTL Type System

Document: `PA_LCTL_TYPE_SYSTEM.md`
Authority: `pacore.lang` §4 (type-group tuples), `pacore.lang.ALL_TYPES`,
`pacore.lang.QUANTUM_OWNED_TYPES`, `pacore.lang._looks_quantum`,
`pacore.lang.verify`.

The generated companion table (every name, grouped, with its
ownership flag) is `TYPE_CATALOG.md`. This document is the normative prose.

---

## 1. What the type system is, and is not

PA-LCTL's `TYPE` column carries a **semantic type**: a name from a closed
vocabulary that says what kind of thing a row's subject is. The type system is
deliberately **nominal, flat and non-inferring**:

* **Nominal.** Two types are the same iff their names are equal. There are no
  structural rules, no parameters that participate in equality, and no
  unification.
* **Flat.** There is no user-declarable subtyping and no type constructor. The
  only structure is the group membership described below, plus the one
  distinguished subset `QUANTUM_OWNED_TYPES`.
* **Non-inferring.** The verifier never infers a type for a row that omits one.
  An absent `TYPE` cell is legal and simply means "not typed here"; the row is
  still checked for arity, ownership, parameters and regime.

The check `lang.verify` performs is exactly:

```python
if row.type_ != NULL_CELL:
    base = row.type_.split("[")[0]
    if base not in ALL_TYPES:
        err("E-TYPE-001", row, f"unknown TYPE {row.type_!r}")
```

so a type **MAY** carry a bracketed suffix (`qreg[4]`, `matrix[2,2]`), and the
suffix is **not** interpreted. A conforming implementation **SHALL NOT** attach
meaning to the suffix beyond documentation.

---

## 2. The eight type groups

`lang.ALL_TYPES` is the union of eight disjoint tuples. Group membership is not
written in a program; it is a property of the name.

| # | Group | Symbol | Count | Introduced | Domain |
|---|---|---|---|---|---|
| 1 | Scalar | `lang.SCALAR_TYPES` | 9 | 1.1.x §5 | classical |
| 2 | Quantum state | `lang.QUANTUM_STATE_TYPES` | 13 | 1.1.x §5 | quantum |
| 3 | Operator | `lang.OPERATOR_TYPES` | 14 | 1.1.x §5 | quantum |
| 4 | Structural | `lang.STRUCTURAL_TYPES` | 12 | 1.1.x §5 | classical |
| 5 | Classical | `lang.CLASSICAL_TYPES` | 12 | 1.1.x §5 | classical |
| 6 | Parallel kernel | `lang.PARALLEL_TYPES` | 28 | 1.2.x §4 | classical |
| 7 | Distributed kernel | `lang.DISTRIBUTED_TYPES` | 24 | 1.2.x §5 | classical |
| 8 | Federation | `lang.FEDERATION_TYPES` | 16 | 1.5.x §4 | classical |

Total: **128** distinct names (`len(lang.ALL_TYPES) == 128`; the eight groups are disjoint).

### 2.1 Group 1 — scalar (`lang.SCALAR_TYPES`)

`amplitude`, `probability`, `phase`, `angle`, `complex`, `real`, `time`,
`frequency`, `energy`

Numeric quantities with a physical or mathematical reading. Note that these are
*quantities*, not *containers*: a vector of amplitudes is `vector` (group 5) or
`ket` (group 2), depending on whether it is classical data or quantum state.

### 2.2 Group 2 — quantum state (`lang.QUANTUM_STATE_TYPES`)

`qubit`, `qudit`, `qreg`, `ket`, `bra`, `pure_state`, `mixed_state`, `density`,
`subsystem`, `bipartite_state`, `multipartite_state`, `entangled_state`,
`separable_state`

These are the carriers of quantum state. **Twelve of the thirteen are
ownership-governed** (§4); the exception is `bra`, which denotes a functional
rather than a state carrier and therefore is not owned.

### 2.3 Group 3 — operator (`lang.OPERATOR_TYPES`)

`operator`, `linear_operator`, `hermitian`, `unitary`, `projector`, `povm`,
`hamiltonian`, `kraus_set`, `channel`, `cptp`, `isometry`, `permutation`,
`controlled_operator`, `oracle`

Operators are **not** ownership-governed: an operator is classically
describable data, and duplicating a description of a gate is not cloning a
state. The admission pipeline nevertheless checks declared operator properties
numerically when a matrix is supplied: `E-UNIT-001` (not unitary),
`E-HERM-001` (not Hermitian), `E-PROJ-001` (not an idempotent Hermitian
projector), `E-POVM-001` (does not resolve the identity), `E-KRAUS-001` (not
trace preserving), all at tolerance `conformance.MATRIX_TOL = 1e-9`.

### 2.4 Group 4 — structural (`lang.STRUCTURAL_TYPES`)

`tensor`, `tensor_product`, `graph`, `coupling_graph`, `basis`, `spectrum`,
`eigensystem`, `sparse_operator`, `block_operator`, `pauli_string`,
`stabilizer`, `syndrome`

Descriptions of structure. `pauli_string` and `stabilizer` are the types that
the stabilizer engine and the symplectic commutation path consume
(`commutation.pauli_symplectic`, `simulator.StabilizerEngine`).

### 2.5 Group 5 — classical (`lang.CLASSICAL_TYPES`)

`bit`, `integer`, `real_c`, `complex_c`, `vector`, `matrix`, `distribution`,
`measurement_result`, `histogram`, `confidence_interval`, `resource_report`,
`proof_record`

Note the deliberate `real_c` / `complex_c` names: they are the *classical
container* types, distinguished from the scalar quantities `real` and
`complex`. `measurement_result` is the type of the classical value produced by
a destructive measurement and consumed by classical control.

### 2.6 Group 6 — parallel kernel objects (`lang.PARALLEL_TYPES`, 1.2.x §4)

`task`, `qtask`, `ctask`, `lane`, `epoch`, `tick`, `event`, `future`,
`promise`, `dependency`, `barrier`, `fence`, `critical_path`, `work`, `span`,
`parallel_region`, `pipeline`, `stage`, `partition`, `shard`, `replica`,
`reduction`, `scan`, `collective`, `scheduler_hint`, `placement_constraint`,
`locality_constraint`, `resource_claim`

This is the 1.2.x contribution that makes parallelism *nameable in the
language* rather than a property of a backend. Each of these has a runtime
counterpart: `task` → `fabric.Task`, `future`/`promise` → `fabric.AsyncFuture`,
`barrier` → `fabric.BarrierRecord`, `collective` → `fabric.CollectiveResult`,
`work`/`span`/`critical_path` → `ses.PartialOrder`, `partition` →
`planner.PartitionResult`, `resource_claim` → `fabric.ResourceLimits`.

`qtask` vs `ctask` is the language-level distinction between a task that holds
quantum state and one that does not; at runtime the same distinction is
`fabric.Task.holds_unknown_quantum`, and it is what makes a task unstealable.

### 2.7 Group 7 — distributed kernel objects (`lang.DISTRIBUTED_TYPES`, 1.2.x §5)

`node`, `cluster`, `device`, `cpu`, `gpu`, `qpu`, `memory_domain`,
`numa_domain`, `network`, `link`, `classical_channel`, `quantum_link`,
`communicator`, `route`, `topology`, `region`, `distributed_partition`,
`remote_handle`, `remote_result`, `remote_event`, `consistency_scope`,
`failure_domain`, `checkpoint_scope`, `ebit`

`ebit` is the only ownership-governed member of this group: an entangled pair
is a quantum resource with a lifecycle (`lang.EPR_STATES`,
`protocols.EBIT_TRANSITIONS`) and can be consumed exactly once.

`classical_channel` and `quantum_link` are the two `DECLARE_LINK` kinds. The
distinction is load-bearing: a classical channel is costed with
`Tmessage = alpha + beta*n` (`planner.t_message`), a quantum link never is
(`planner.QuantumLinkCost`).

### 2.8 Group 8 — federation objects (`lang.FEDERATION_TYPES`, 1.5.x §4)

`federation`, `execution_domain`, `domain_group`, `worker_group`,
`placement_set`, `route_set`, `trust_domain`, `consistency_domain`,
`calibration_domain`, `resource_pool`, `ebit_pool`, `memory_pool`, `task_pool`,
`shot_pool`, `parameter_pool`, `circuit_pool`

Runtime counterparts: `fabric.Federation`, `fabric.ExecutionDomain`,
`fabric.WorkerGroup`, `lang.TRUST_DOMAINS`, `fabric.ResourceLimits`.

---

## 3. Subtyping

PA-LCTL has **no declared subtyping relation and no coercion**. What it has
instead are three *membership predicates*, each of which is a real subtyping
relation in the sense that it licenses a different set of operations.

### 3.1 The ownership predicate — the only structural subtype

```
QUANTUM_OWNED  <:  ALL_TYPES
```

`lang.QUANTUM_OWNED_TYPES` (13 names) is the subset whose inhabitants carry
*unknown quantum state*. Membership licenses nothing; it **forbids**:
copying, replication, checkpointing, rollback, stealing, speculation and
process-boundary serialization. See `PA_LCTL_OWNERSHIP_MODEL.md`.

```
qubit, qudit, qreg, ket, pure_state, mixed_state, density, subsystem,
bipartite_state, multipartite_state, entangled_state, separable_state, ebit
```

### 3.2 The executability predicate

```
EXECUTABLE_FACES  <:  FACES
```

Seven of the seventeen faces are executable
(`lang.EXECUTABLE_FACES` = `EXEC`, `PREPARE`, `MEASURE`, `NOISE`, `CONTROL`,
`COMM`, `PROTOCOL`). Only rows with an executable face contribute operations,
concurrency candidates and scheduled work.

### 3.3 The exactness predicate

```
EXACT_REGIMES  <:  REGIMES
```

`lang.EXACT_REGIMES` = {`EXACT`, `EXACT_LINEAR`, `PIECEWISE_EXACT`}. This is
the one *subtyping-like* relation with a variance rule, and the rule is
**anti-monotone**: a value **MAY** flow from an exact regime into an
approximate context, and **SHALL NOT** flow the other way. The implementation
enforces the two cases it can decide syntactically (`E-REG-002`, `E-REG-003`);
downstream, `erroralgebra.compose` refuses to label an approximate law exact,
and `commutation` labels the channel rule `APPROXIMATE` regardless of the
declared regime.

### 3.4 What is deliberately *not* a subtype

* `unitary` is **not** a subtype of `operator` in any checked sense. A row
  typed `operator` is not accepted where `unitary` is required, and vice
  versa; nothing checks the relation because nothing needs it.
* `qubit` is **not** a subtype of `qreg`. Both are ownership-governed and that
  is the only property the system uses.
* Classical types do **not** widen (`bit` → `integer` → `real_c` is not a
  chain).

An implementation **SHALL NOT** introduce such relations, because doing so
would change which programs `lang.verify` accepts without changing the bundle
seal semantics.

---

## 4. Quantum versus classical

### 4.1 The decision procedure

Whether an operand is treated as quantum is decided by
`lang._looks_quantum(row, key)`:

```python
base = row.type_.split("[")[0]
if base in QUANTUM_OWNED_TYPES:
    return True
if row.op in GATE_ARITY or row.op in OPS_PREPARE or row.op in OPS_NOISE:
    return bool(_QUANTUM_HINT.match(key))
return False
```

with `_QUANTUM_HINT = ^(q|qr|anc|ebit|psi|rho|log)`.

This is a **two-tier** rule and both tiers are normative:

1. **Declared.** If the row's `TYPE` base name is ownership-governed, every
   operand of that row is quantum. This is the authoritative tier: a program
   that types its rows is never at the mercy of naming.
2. **Conventional.** Otherwise, *only for gate, preparation and channel
   operations*, an operand whose name begins with `q`, `qr`, `anc`, `ebit`,
   `psi`, `rho` or `log` is treated as quantum.

Tier 2 exists so that an untyped gate row still gets ownership checking. It is
a naming convention with normative force: a conforming program **SHOULD** name
qubit registers with one of those prefixes, and **SHALL** declare
`TYPE` when it does not.

Consequence, stated plainly: a row `EXEC ... H ... a=x[0]` with no `TYPE` is
**not** ownership-checked, because `x` matches no hint. This is a deliberate
false-negative — the alternative is to guess — and it is why §4.1 tier 1 is
the recommended discipline.

### 4.2 Duck-typed quantum detection at runtime

Three modules must recognize quantum values at runtime, in objects that never
came from a `TYPE` cell. They implement the same predicate independently and on
purpose (so that `crdt` does not depend on `fabric`):

| Function | Module | Recognizes |
|---|---|---|
| `fabric.is_quantum` | `fabric` | a `QuantumPayload`; any object with `is_qstate` truthy; the string `"QSTATE"`; any object whose `type_`/`type` attribute is in `QUANTUM_OWNED_TYPES` |
| `crdt.is_quantum_value` | `crdt` | the above, plus **recursive** descent into lists, tuples, sets and dicts (keys and values) |
| `resilience._is_quantum_payload` | `resilience` | the above, with recursion into lists, tuples, sets and dict **values** |

A conforming implementation **SHALL** make these predicates conservative: a
false positive costs a refused operation, a false negative costs a violated
no-cloning rule.

### 4.3 The quantum/classical boundary in the type system

| Property | Quantum types | Classical types |
|---|---|---|
| Copyable | **never** | freely |
| Replicable (CRDT) | **never** (`QUANTUM_REPLICATION_REJECTED`) | yes, under a consistency profile |
| Checkpointable | only as `classically_known_state_prep` with an explicit assertion | yes |
| Rollback-able | **never** (`QUANTUM_ROLLBACK_REFUSED`) | yes, from the journal |
| Stealable between workers | **never** while `LIVE` and unknown (`QUANTUM_TASK_STEAL_REJECTED`) | yes, subject to `MigrationRules` |
| Speculatively duplicated | **never** (`SpeculationRejectedError`); only reroute or restart-from-boundary | yes, first valid completion wins |
| Crosses a process boundary | **never** (`QuantumPayload.__reduce__` raises) | yes |
| Enters a BSP message buffer | **never** (`BSPEngine.superstep` raises) | yes |
| Fans out to multiple dataflow outputs | **never** (`DataflowRuntime.evaluate` refuses) | yes |

---

## 5. Effect types

PA-LCTL does not have an effect *annotation*; it has effect-carrying
**operation classes**, which is the same thing decided by `OP` rather than by a
separate column. The effects that change what a program means are:

| Effect class | Membership test | Effect on ownership / semantics |
|---|---|---|
| **Reinitializing** | `lang.REINIT_OPS` (= `lang.OPS_PREPARE`) | (re)establishes `LIVE` ownership with a **fresh lineage**, clears the entanglement set, sets owner node and domain |
| **Destructive** | `lang.DESTRUCTIVE_OPS` (7 measurement ops) | moves the source to `MEASURED`; registers a classical bit under `OUT`; forms a measurement barrier for commutation |
| **Ownership-moving** | `TELEPORT` | source → `MOVED`, destination gains the **source's lineage**, not a new one |
| **Ownership-releasing** | `EPR_RELEASE` | source → `RELEASED`; a second release is `E-EPR-003` |
| **Entangling** | `REMOTE_CNOT`, `REMOTE_CONTROL` | records a symmetric edge in both operands' `entangled_with` sets |
| **Establishing** | `ENTANGLE_LINK`, `EPR_RESERVE` | creates `LIVE` ownership for the `OUT` handle, requires a `LINK` |
| **Channel** | `lang.OPS_NOISE` | forces a non-exact regime; parameters constrained to `[0,1]`; requires the density engine |
| **Control-flow** | `CLASSICAL_IF`, `CLASSICAL_SWITCH`, `FEEDBACK`, `CLASSICAL_FEEDBACK` | requires a classical value produced by a prior measurement; forms a barrier |
| **Structural** | `BARRIER`, `FENCE`, `EPOCH` | edges with reason `BARRIER` from every prior node; never reorderable |
| **Declarative** | `DECLARE_*`, `NOTE`, `EMIT_LEDGER`, `CLAIM`, `ASSERT_INVARIANT` | `lang.verify` short-circuits after recording; no ownership effect |

An operation **SHALL** belong to at most one of *reinitializing*,
*destructive*, *ownership-moving* and *ownership-releasing*; the reference
catalog satisfies this.

---

## 6. Authority types

"Authority" is the PA-LCTL name for *who is entitled to make a claim*. Four
authority vocabularies are typed:

### 6.1 Trust domains (`lang.TRUST_DOMAINS`)

`LOCAL_TRUSTED`, `LOCAL_UNTRUSTED`, `REMOTE_AUTHENTICATED`,
`REMOTE_UNAUTHENTICATED`, `PHYSICAL_TARGET_AUTHENTICATED`

* Validated on construction of every `fabric.Worker`, `WorkerGroup`,
  `ExecutionDomain` and `Federation` (`fabric._check_trust`).
* `planner.Placer` admits a target only if its trust is `LOCAL_TRUSTED`,
  `REMOTE_AUTHENTICATED` or `PHYSICAL_TARGET_AUTHENTICATED`
  (`trust_pass`).
* `fabric.MigrationRules.forbid_cross_trust` defaults to `True`: a task
  **SHALL NOT** migrate across trust domains unless the rule is explicitly
  relaxed.
* `PHYSICAL_TARGET_AUTHENTICATED` is a *vocabulary* value only in this
  environment; no code path can produce an authenticated physical target.

### 6.2 Commutation authority (`commutation.COMMUTATION_STATUS`)

`DISJOINT`, `COMMUTING_EXACT`, `COMMUTING_NUMERICALLY_VERIFIED`,
`COMMUTING_TARGET_CONDITIONAL`, `NON_COMMUTING`, `UNRESOLVED`.

Every verdict carries a `rule_id`, an `exactness`, a `proof_id` and, for the
target-conditional case, an explicit `target_assumptions` tuple. A verdict
**SHALL NOT** be produced without a `ProofRow` entering the ledger.

### 6.3 Q6 admission authority (`lang.Q6_ADMISSION`)

Nine values; see `PA_LCTL_Q1_Q6_ADMISSION.md`. This is the authority type that
governs whether a *learned* rule may influence the optimizer. Until admitted,
its status is `QUARANTINE` and `usable_by_optimizer` is `False`.

### 6.4 Release authority (`ledgers.RELEASE_OUTPUTS`)

Four outputs are qualifiable from executed conformance evidence; two are
permanently `BLOCKED_EXTERNAL_AUTHORITY`. The authority to qualify a physical
output is, by construction, **not held by this software**.

---

## 7. Type-related diagnostics

| Code | Condition |
|---|---|
| `E-TYPE-001` | `TYPE` base name not in `lang.ALL_TYPES` |
| `E-FAM-001` | `FAMILY` not in `lang.PARALLEL_FAMILIES` |
| `E-REG-001` | `REGIME` not in `lang.REGIMES` |
| `E-REG-002` | exact regime declared on a noise operation |
| `E-REG-003` | exact regime declared with `approx=true` |
| `E-OWN-005` | an operand judged quantum by §4.1 used before preparation |
| `E-UNIT-001`, `E-HERM-001`, `E-PROJ-001`, `E-POVM-001`, `E-KRAUS-001`, `E-DENS-001`, `E-DENS-002` | a declared operator/state property that the supplied matrix does not have (admission pipeline) |
| `E-DIM-001` | declared tensor dimensions incompatible |

---

## 8. Implementation status

| Element | Status | Note |
|---|---|---|
| Nominal type checking (`E-TYPE-001`) | `OPERATIONAL` | |
| The 8 groups, 128 names | `OPERATIONAL` | closed vocabulary |
| Ownership subtype (`QUANTUM_OWNED_TYPES`) | `OPERATIONAL` | enforced statically and at runtime |
| Exactness anti-monotonicity | `IMPLEMENTED` | the two syntactically decidable cases are checked; general dataflow of exactness is not tracked |
| Bracketed type parameters (`qreg[4]`) | `SPECIFIED` | parsed and ignored; no dimensional checking |
| Structural subtyping / coercion | not part of the language | |
| Type inference | not part of the language | |
| Effect annotations as a column | not part of the language | effects are carried by `OP` |
| `PHYSICAL_TARGET_AUTHENTICATED` trust | `BLOCKED` | vocabulary only |
