# PA-LCTL Ownership Model (no-cloning as a type rule)

Document: `PA_LCTL_OWNERSHIP_MODEL.md`
Authority: `pacore.lang.OwnershipRecord`, `pacore.lang.verify`,
`pacore.lang.QUANTUM_OWNED_TYPES`, `pacore.lang._looks_quantum`,
`pacore.fabric.QuantumPayload`, `pacore.protocols.OwnershipTransfer`.

RFC 2119 keywords apply.

---

## 1. The rule

> **No-cloning is enforced as a static type rule over object keys, not as a
> runtime check over values.**

An unknown quantum state has, at any point in a bundle, **exactly one live
owner**. Ownership is tracked by `lang.verify` as it walks the rows in source
order, in a map from *object key* to `OwnershipRecord`. Any row that would
produce two live references to one lineage, or that would read a lineage whose
owner has moved, been measured or been released, is rejected before anything
executes.

This is stronger than a runtime guard in three ways:

1. It rejects the program, so no partial execution occurs.
2. It is decidable from the text: no simulation is required.
3. It composes with the distributed model — the record carries the *owning
   node*, so a local operation on a remotely owned object is a static error
   (`E-OWN-003`).

### 1.1 Object keys

The unit of ownership is a **key**, produced by `lang.QRef.keys()`:

| Cell | Keys |
|---|---|
| `q` | `["q"]` |
| `q[0]` | `["q[0]"]` |
| `q[0:3]` | `["q[0]", "q[1]", "q[2]"]` |

Ownership is therefore per element, not per register. `q[0:3]` moving by
teleport moves three keys.

### 1.2 Which keys are governed

A key is ownership-governed for a given row when `lang._looks_quantum(row, key)`
holds: either the row's `TYPE` base name is in `lang.QUANTUM_OWNED_TYPES`, or
the row's `OP` is a gate / preparation / channel **and** the key matches
`^(q|qr|anc|ebit|psi|rho|log)`. See `PA_LCTL_TYPE_SYSTEM.md` §4.1. In addition,
any key that already has an `OwnershipRecord` is governed from then on,
regardless of the current row's type.

---

## 2. The ownership record

`lang.OwnershipRecord` — seven fields, all normative:

| Field | Type | Meaning |
|---|---|---|
| `key` | `str` | the object key (§1.1) |
| `lineage` | `str` | `LIN0001`-style identity of the *state*, not of the container |
| `state` | `str` | one of `LIVE`, `MOVED`, `MEASURED`, `RELEASED` |
| `owner_node` | `str` | the `NODE` cell of the row that established it |
| `owner_domain` | `str` | the `DOMAIN` cell of the row that established it |
| `last_row` | `str` | the `ROW` id of the last row to touch it |
| `entangled_with` | `set[str]` | keys this key is entangled with |

The record is exported verbatim by `VerifyResult.as_dict()["ownership"]` with
`entangled_with` sorted, so ownership is part of the reportable evidence of a
verification, not an internal detail.

---

## 3. Lineage

A **lineage** identifies the *state*, and survives a change of container. It is
minted by the counter in `lang.verify` (`new_lineage()` → `LIN%04d`).

Normative lineage rules:

1. A reinitializing operation (`lang.REINIT_OPS` = `lang.OPS_PREPARE`)
   **SHALL** mint a **fresh** lineage, because the prior state is destroyed and
   a known state replaces it. It also clears `entangled_with`.
2. `TELEPORT` **SHALL** carry the source's lineage to the destination key
   unchanged: `own[dst] = OwnershipRecord(dst, srec.lineage, "LIVE", ...)`.
   The *state* did not change; only its location did.
3. Any other operation that first creates a quantum output **SHALL** mint a
   fresh lineage.
4. Lineage is the key by which the SES groups nodes into entanglement
   hyperedges: `HypergraphPartitioner.build_hyperedges` creates one
   `HE-LIN-<lineage>` hyperedge of weight 8.0 per lineage touched by more than
   one node, which is why splitting a lineage across partitions is the most
   expensive thing the partitioner can do.
5. `SESNode.quantum_lineage` is the sorted tuple of lineages of all keys the
   node reads or writes, and it is part of `SES.canonical()` and therefore of
   the SES hash.

At runtime, `fabric.QuantumPayload.lineage` and
`protocols.OwnershipTransfer.lineage` carry the same concept;
`protocols._lineage_of` derives a content-addressed `LIN-<16 hex>` from the
amplitudes so that a protocol result can be tied to the state it moved.

---

## 4. The four ownership states

```
                 PREP*/RESET
      (none) ─────────────────▶ LIVE ◀────────────┐
                                 │                │ PREP*/RESET
             MEASURE/POVM/...    │                │ (fresh lineage)
                    ┌────────────┼────────────┐   │
                    ▼            ▼            ▼   │
                MEASURED      MOVED       RELEASED┘
                                (TELEPORT)   (EPR_RELEASE)
```

| State | Entered by | Legal successors | Use is |
|---|---|---|---|
| `LIVE` | any `lang.OPS_PREPARE` op; `ENTANGLE_LINK`/`EPR_RESERVE` on `OUT`; the destination of `TELEPORT`; first quantum write of any other op | `MEASURED`, `MOVED`, `RELEASED`, `LIVE` (re-prep) | legal |
| `MOVED` | `TELEPORT` on the source key | `LIVE` only via re-preparation | `E-OWN-001` |
| `MEASURED` | any `lang.DESTRUCTIVE_OPS` on `A` (or `CTRL` when `A` is null) | `LIVE` only via `RESET`/`PREP*` | `E-OWN-002` |
| `RELEASED` | `EPR_RELEASE` on `A` | `LIVE` only via re-establishment | `E-OWN-006` |

Notes that matter to an implementer:

* `MEASURED` is *destructive of the pre-measurement frame*, not of the qubit.
  The qubit still exists; its state is now known and the language requires an
  explicit `RESET` or preparation before further use. This mirrors the
  simulator, which applies the basis rotation and does **not** invert it
  (`simulator._BASIS_ROTATION`, `StatevectorEngine.measure`).
* The transition into `MEASURED` uses `parse_ref(row.a) or parse_ref(row.ctrl)`
  — the source is `A` if present, otherwise `CTRL`.
* Re-preparation is the **only** way back to `LIVE`, and it always changes the
  lineage. There is no "unmove" and no "unmeasure".
* `RELEASED` and the ebit-ledger state `CONSUMED` are both refused by
  `E-EPR-003` on a second release.

---

## 5. The eight global ownership rules

These are the rules `lang.verify` enforces on every row; together they are the
LCTL 1.2.x §6 "global ownership" gate. Each is stated normatively with its
diagnostic.

### Rule 1 — Single live owner

An ownership-governed key **SHALL** have at most one `OwnershipRecord` in state
`LIVE` at any point in the row order. The record map is keyed by the object
key, so a second live owner is not representable; the ways a program could try
to create one are Rules 2 and 3.

### Rule 2 — No duplication by composition

An operation **SHALL NOT** name the same quantum key in both of two operand
positions that denote independent subsystems. Concretely, `TENSOR` and `MERGE`
with overlapping `A` and `B` keys are rejected:

```
E-CLONE-001  operation TENSOR would duplicate quantum object(s) ['q[0]']
```

### Rule 3 — No use after move

Reading or writing a key whose record is `MOVED` is rejected:

```
E-OWN-001  use-after-move of quantum object 'q[0]' (moved at row R301)
```

### Rule 4 — No use after destructive measurement

Reading or writing a key whose record is `MEASURED`, by any operation not in
`lang.REINIT_OPS`, is rejected:

```
E-OWN-002  use-after-destructive-measure of 'q[0]' (measured at row R014);
           RESET or PREP required
```

### Rule 5 — No use before establishment

Using a key that `_looks_quantum` judges quantum and that has no record is
rejected:

```
E-OWN-005  quantum object 'q[0]' used before preparation
```

This is what makes preparation mandatory rather than conventional.

### Rule 6 — No use after release

Using a key whose record is `RELEASED` is rejected:

```
E-OWN-006  use of released resource 'e[0]'
```

### Rule 7 — Ownership locality (cross-node)

A **local** operation on a key whose `owner_node` is a different, non-null node
is rejected unless the operation is a distributed primitive
(`lang.OPS_DISTRIBUTED_Q`):

```
E-OWN-003  local operation on 'q[0]' whose authoritative owner is node 'N0',
           not 'N1'; a distributed protocol primitive is required
```

This is the rule that makes the distributed model honest: state does not
teleport itself because a row happens to name a different node.

### Rule 8 — Classical dependency on measurement

A classical control operation (`CLASSICAL_IF`, `CLASSICAL_SWITCH`, `FEEDBACK`,
`CLASSICAL_FEEDBACK`) **SHALL** name a condition, and that condition
**SHALL** be a classical value that a prior `lang.DESTRUCTIVE_OPS` row
registered under its `OUT` cell:

```
E-CTL-002  CLASSICAL_IF requires a classical condition
E-CTL-003  CLASSICAL_IF depends on classical value 'c9' that no prior
           measurement produced
```

Rule 8 belongs to the ownership model because it is the rule that keeps the
classical shadow of a quantum state honest: a bit that no measurement produced
is not a bit derived from a state.

---

## 6. Entanglement lineage

Entanglement is tracked as a symmetric relation on keys, in
`OwnershipRecord.entangled_with`.

**Established by:** `REMOTE_CNOT` and `REMOTE_CONTROL`. When both the `CTRL`
and `A` references resolve to keys with existing records:

```python
cn.entangled_with.add(an.key)
an.entangled_with.add(cn.key)
```

**Cleared by:** re-preparation (`rec.entangled_with.clear()` in the
`REINIT_OPS` branch). Nothing else clears it — a measured or moved key retains
its recorded entanglement partners, which is correct: the *history* of the
correlation is what downstream analysis needs.

**Consumed by:**

* `ses.build_ses` — every key in a node's accumulated entanglement set
  contributes an `ENTANGLEMENT_DEPENDENCY` edge from that key's last writer;
* `SESNode.entanglement_set`, which is part of the SES hash;
* `planner.coupling_graph`, where `ENTANGLEMENT_DEPENDENCY` carries weight
  8.0 — the highest of any reason, and enough on its own to produce the
  `MONOLITHIC_REQUIRED` coupling verdict;
* `planner.TemporalScheduler._priority` in `COMMUNICATION_AWARE` mode, which
  adds `2.0 * len(entanglement_set)` to a node's bottom level.

**Normative statement.** An implementation **SHALL NOT** partition, place or
schedule as if two entangled keys were independent. The mechanism is the
weight-8.0 `HE-LIN-*` hyperedge plus the `ENTANGLEMENT_DEPENDENCY` edge; both
are derived, not declared, so a program cannot opt out.

**Limitation, stated.** The reference verifier records entanglement only for
the two remote-gate operations. A local `CX` between two qubits also entangles
them physically, but produces a `QUANTUM_OWNERSHIP` data edge rather than an
entry in `entangled_with`. The consequence is that the *ordering* constraint is
always correct (the data edge is there), while the *coupling weight* understates
purely local entanglement. This is an `IMPLEMENTED_PARTIAL` area; see §10.

---

## 7. Cross-node ownership

`owner_node` and `owner_domain` are captured from the row that established the
record. Their effects:

| Site | Effect |
|---|---|
| `lang.verify` Rule 7 | `E-OWN-003` for a local op on a remotely owned key |
| `ses.build_ses` | `SESNode.owner` (default `N_LOCAL`), `SESNode.memory_domain` (default `D_LOCAL`) |
| `ses.admit_concurrency` | `topology.can_coexist(na.owner, nb.owner)` → `SERIALIZE_TOPOLOGY`; `topology.crosstalk_status` uses `na.owner == nb.owner` |
| `planner.Placer` | placement is per partition, and partitions are formed over nodes carrying owners |
| `conformance._check_targets` | `E-TARGET-001` when the owner's declared target does not support the operation |
| `protocols.ProtocolCompiler._peer_node` | resolves the remote endpoint from the declared `LINK`; never guesses a topology, falling back to the placeholder `N_REMOTE` |

A conforming implementation **SHALL** treat `owner_node` as authoritative and
**SHALL NOT** infer relocation from a row's `NODE` cell.

---

## 8. Admitted state relocation

There are exactly **four** admitted ways for unknown quantum state to change
custody, and each has a distinct signature.

| # | Mechanism | Op(s) | Source after | Destination | Lineage | Requires |
|---|---|---|---|---|---|---|
| 1 | **Teleportation** | `TELEPORT` | `MOVED` | new `LIVE` record on `OUT` at the row's node/domain | **preserved** | a `LINK` (`E-PROTO-002`); a prepared source (`E-PROTO-003`); `A` and `OUT` (`E-PROTO-001`) |
| 2 | **Entanglement establishment** | `ENTANGLE_LINK`, `EPR_RESERVE` | n/a | new `LIVE` record on `OUT` | fresh | a `LINK` (`E-EPR-001`) |
| 3 | **Entanglement release** | `EPR_RELEASE` | `RELEASED` | none | ends | an existing record (`E-EPR-002`), not already released/consumed (`E-EPR-003`) |
| 4 | **Nonlocal unitary** | `REMOTE_CNOT`, `REMOTE_CONTROL` | unchanged (`LIVE`) | unchanged | unchanged | a `LINK` (`E-PROTO-004`); records entanglement |

Mechanism 4 is *not* relocation: both qubits stay with their owners and only
the ebit is consumed. `protocols.remote_cnot` records exactly that in its
`OwnershipTransfer` with `from_node == to_node` and
`source_invalidated = False`, and its `reason` says so.

Mechanism 1 *is* relocation, and `protocols.teleport` records
`source_invalidated = True` with the reason *"TELEPORT moves the state; the
source qubit was destructively measured and holds no copy (no-cloning)"*. The
executed protocol reads the destination amplitudes out of the collapsed
register rather than asserting the textbook result, and reports the measured
fidelity against the input state.

**Prohibited relocations.** A quantum payload **SHALL NOT** move by:
task migration (`fabric.MigrationRules.allow_quantum_migration` is `False` and
`ReplicationRules.__post_init__` raises if quantum replication is enabled),
work stealing (`StealRejectedError`, token `QUANTUM_TASK_STEAL_REJECTED`),
process boundary (`QuantumPayload.__reduce__` raises), BSP message
(`BSPEngine.superstep` raises `OwnershipTransferError`), dataflow fan-out to
multiple outputs (`DataflowRuntime.evaluate` refuses), replication
(`crdt.reject_quantum`), checkpoint (`resilience.CheckpointStore.save`), or
rollback (`RecoveryTransaction.rollback`).

---

## 9. Runtime ownership: `fabric.QuantumPayload`

The static model has a runtime counterpart for the execution fabric.
`QuantumPayload` is a **handle**; the state never moves with it.

* `__copy__`, `__deepcopy__`, `__reduce__` and `clone()` all raise
  `CloneAttemptError` carrying the token `QUANTUM_CLONE_REJECTED`. Pickling is
  therefore impossible, which is what makes the process profiles fail closed
  instead of duplicating state.
* `__post_init__` rejects a `type_` outside `lang.QUANTUM_OWNED_TYPES`, so the
  static and runtime vocabularies cannot diverge.
* States are `LIVE` / `MOVED` / `CONSUMED`; `unknown` is
  `not known_classically`.
* `TaskRuntime.transfer_ownership` is the only admitted move: it refuses a
  non-`LIVE` payload, refuses a task that already holds the payload
  (`CloneAttemptError`), strips the payload from its previous holder, appends a
  provenance entry `"<from>-><to>@<logical time>"`, and emits an
  `OWNERSHIP_TRANSFER` event with `ownership_delta = {"move": [[key, src, dst]]}`.
* `TaskRuntime.duplicate_ownership` exists **only** to fail: it always raises.
  It is present so that the negative conformance case can call something.
* `payload_bytes(QuantumPayload) == 16` — the handle size. The state is never
  charged to a message, because it never travels in one.

---

## 10. Diagnostic reference (ownership family)

| Code | Severity | Emitted when |
|---|---|---|
| `E-OWN-001` | `REJECT` | use-after-move |
| `E-OWN-002` | `REJECT` | use-after-destructive-measure |
| `E-OWN-003` | `REJECT` | local operation on a remotely owned object |
| `E-OWN-005` | `REJECT` | quantum object used before preparation |
| `E-OWN-006` | `REJECT` | use of a released resource |
| `E-CLONE-001` | `REJECT` | `TENSOR`/`MERGE` would duplicate a quantum object |
| `E-PROTO-001` | `REJECT` | `TELEPORT` missing `A` or `OUT` |
| `E-PROTO-002` | `REJECT` | `TELEPORT` without a `LINK` |
| `E-PROTO-003` | `REJECT` | `TELEPORT` source unprepared |
| `E-PROTO-004` | `REJECT` | `REMOTE_CNOT`/`REMOTE_CONTROL` without a `LINK` |
| `W-PROTO-005` | `WARN` | remote gate on co-located qubits (cheaper local gate exists; semantically identical) |
| `E-EPR-001` | `REJECT` | `ENTANGLE_LINK`/`EPR_RESERVE` without a `LINK` |
| `E-EPR-002` | `REJECT` | release of an unknown ebit |
| `E-EPR-003` | `REJECT` | double release/consume of an ebit |
| `E-CTL-002` | `REJECT` | classical control with no condition |
| `E-CTL-003` | `REJECT` | classical control on a value no measurement produced |

> `E-OWN-004` is **not assigned**. The gap is real and is preserved rather than
> renumbered, because diagnostic codes are a stable external interface.

Related runtime tokens: `QUANTUM_CLONE_REJECTED`,
`QUANTUM_TASK_STEAL_REJECTED` (`fabric`), `QUANTUM_REPLICATION_REJECTED`
(`crdt`), `QUANTUM_CHECKPOINT_REFUSED`, `QUANTUM_ROLLBACK_REFUSED`
(`resilience`).

---

## 11. Worked rejections

```
;; E-OWN-002: reuse after measurement
R014¦MEASURE¦L0¦-¦MEASURE¦c0¦-¦q[0]¦-¦-¦measurement_result¦Z¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦-¦-
R015¦EXEC¦L0¦-¦H¦-¦-¦q[0]¦-¦-¦unitary¦Z¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦-¦-
   -> E-OWN-002 @R015

;; E-OWN-001: use after teleport
R301¦PROTOCOL¦-¦-¦TELEPORT¦z[0]¦-¦q[0]¦-¦-¦remote_handle¦-¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦Q0¦-
R302¦EXEC¦-¦-¦X¦-¦-¦q[0]¦-¦-¦unitary¦Z¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦-¦-
   -> E-OWN-001 @R302

;; E-OWN-003: local gate on a remotely owned qubit
R010¦PREPARE¦-¦-¦PREP0¦q[0]¦-¦-¦-¦-¦qubit¦Z¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦-¦-
R011¦EXEC¦-¦-¦H¦-¦-¦q[0]¦-¦-¦unitary¦Z¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N1¦-¦-
   -> E-OWN-003 @R011

;; E-CLONE-001: composition that would duplicate
R020¦EXEC¦-¦-¦TENSOR¦s[0]¦-¦q[0]¦q[0]¦-¦tensor_product¦-¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦-¦-
   -> E-CLONE-001 @R020
```

---

## 12. Implementation status

| Element | Status | Note |
|---|---|---|
| Static ownership over keys | `OPERATIONAL` | `lang.verify`, checked by conformance negatives |
| Four-state lifecycle | `OPERATIONAL` | |
| Lineage minting and preservation | `OPERATIONAL` | |
| The eight global rules | `OPERATIONAL` | |
| Cross-node ownership (`E-OWN-003`) | `OPERATIONAL` | |
| Admitted relocations (4 mechanisms) | `OPERATIONAL` | teleport and remote-CNOT are numerically executed, not asserted |
| Runtime handle discipline | `OPERATIONAL` | copy/deepcopy/pickle/clone all fail closed |
| Entanglement lineage for **remote** gates | `OPERATIONAL` | |
| Entanglement lineage for **local** entangling gates | `IMPLEMENTED_PARTIAL` | ordering is correct via `QUANTUM_OWNERSHIP` edges; `entangled_with` is not populated, so coupling weight understates local entanglement |
| Quantum-hint naming convention as a fallback | `IMPLEMENTED` | deliberate false negatives for untyped, unconventionally named registers; declare `TYPE` to avoid |
| Ownership across a real network | `BLOCKED` | `NETWORK=deny`; nodes are logical names in one process |
