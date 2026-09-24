# PA-LCTL Consistency and Replication Specification

Document: `PA_LCTL_CONSISTENCY_SPEC.md`
Authority: `pacore.crdt` in full; `pacore.lang.CONSISTENCY_PROFILES`;
`pacore.fabric.ReplicationRules`, `ConsistencyRules`.

RFC 2119 keywords apply.

---

## 1. The two structural invariants

Stated in the module header and enforced by construction:

1. **Every merge in this module is deterministic, commutative, associative and
   idempotent.** `verify_crdt_laws` checks all four empirically over generated
   samples and **returns a report rather than an assertion**.
2. **A QSTATE is never replicated.** Unknown quantum state is
   ownership-constrained (LCTL 1.1.x §6), so every mutator runs a rejection
   guard and raises `QuantumReplicationRejected`. **There is no replication type
   in this module that can hold a quantum payload.**

---

## 2. Quantum state is excluded from all replication

### 2.1 The guard

`crdt.is_quantum_value(value)` is *deliberately independent of `fabric`* — the
consistency layer must not depend on the execution layer. It recognizes:

* the string `"QSTATE"` (case-insensitive, stripped);
* any object with a truthy `is_qstate` attribute;
* any object whose class is named `QuantumPayload`;
* any object whose `type_`/`type` attribute is in `lang.QUANTUM_OWNED_TYPES`;
* **recursively**, any list, tuple, set or frozenset containing such a value;
* **recursively**, any dict with such a key **or** value.

`crdt.reject_quantum(value, where=...)` raises
`QuantumReplicationRejected` with the token `QUANTUM_REPLICATION_REJECTED` and
the message *"unknown quantum state is ownership-constrained and can never be
replicated, merged or reconciled (LCTL 1.1.x s6, 1.4.x s11)"*.

### 2.2 Where the guard runs

| Site | Effect |
|---|---|
| `GCounter.increment`, `PNCounter.increment/decrement` | rejects a quantum increment |
| `GSet.add`, `ORSet.add` | rejects a quantum element |
| `LWWRegister.set` | rejects a quantum value |
| `AntiEntropy.add_replica` | rejects a replica whose `value()` is quantum |
| `ConsistencyContract.admit_value` | rule **C7**: a QSTATE never enters a replicated scope |
| `fabric.ReplicationRules.__post_init__` | **raises** if `allow_quantum_replication` is set at all |

### 2.3 Rule C7

C7 applies to **every** profile except `SINGLE_OWNER`:

```python
if self.profile != "SINGLE_OWNER":
    required.append("no quantum state is replicated")
```

`SINGLE_OWNER` is excluded because it *is* the non-replicated profile; there is
nothing to exclude.

---

## 3. Consistency profiles

`lang.CONSISTENCY_PROFILES` — seven values, **classical state only**
(LCTL 1.4.x §10):

`SINGLE_OWNER`, `EVENTUAL`, `CAUSAL`, `SEQUENTIAL`, `LINEARIZABLE`,
`IMMUTABLE_REPLICA`, `CRDT_DECLARED`.

An unknown profile raises `CRDTError` at contract construction and
`FabricError` at `ReplicationRules`/`ConsistencyRules` construction.

---

## 4. CRDT types and the four merge laws

### 4.1 The base

`crdt.CRDT` requires `merge`, `value`, `state`, `copy` and the class method
`random_sample(rng, replica_id)` used by the law verifier. `_check_mergeable`
raises `MergeTypeError` when the two operands are of different classes.
`state_hash()` is the SHA-256 (first 32 hex) of the canonical JSON of
`state()`, so equality is **semantic**, insensitive to Python container
ordering.

### 4.2 The five types

`crdt.CRDT_TYPES = (GCounter, PNCounter, GSet, ORSet, LWWRegister)`.

| Type | State | Merge | Value |
|---|---|---|---|
| `GCounter` | `{replica: count}` | **per-replica maximum** | sum of counts |
| `PNCounter` | two G-counters (`positive`, `negative`) | per-replica maximum on both | `sum(positive) - sum(negative)` |
| `GSet` | a set | **union** | sorted elements |
| `ORSet` | `{(element, tag)}` plus `removed_tags` | union of both | elements with at least one non-removed tag |
| `LWWRegister` | `value`, `timestamp`, `writer`, `policy`, `version` | **the dominating side** by the declared policy | the value |

`GCounter.increment` rejects a negative delta (*"use PNCounter for
decrements"*).

`ORSet` semantics are exact: an element is present when it has at least one add
tag not observed as removed, and **re-adding after a remove is well defined
because the new add carries a fresh tag** (`_tag()` = `"<replica>:<counter>"`).
Merge takes the max of the two counters so tags never collide after a merge.

### 4.3 Design hole H10 — the LWW clock policy

The documents leave the clock policy open. PA-LCTL declares three explicit
policies in `crdt.CLOCK_POLICIES`:

```
LAMPORT_THEN_REPLICA_ID
DECLARED_WALL_CLOCK_THEN_REPLICA_ID
VERSION_VECTOR_DOMINANCE_THEN_REPLICA_ID
```

and makes the policy **part of the replica identity**:

```python
if self.policy != other.policy:
    raise MergeTypeError("clock policy mismatch: ...; a merge under two "
                         "different 'last' orders is not deterministic")
```

The dominance test is a total order in every policy:

1. under `VERSION_VECTOR_DOMINANCE_THEN_REPLICA_ID`, strict version-vector
   dominance wins; concurrent or dominated vectors fall through to (2);
2. higher `timestamp` wins;
3. higher `writer` (string order) wins;
4. higher canonical JSON of the value wins.

Step 4 guarantees totality: two states are never both non-dominating, so merge
is deterministic even for identical timestamps and writers.

The merged version vector is always the pointwise maximum, regardless of which
side won.

### 4.4 The four laws

`crdt.verify_crdt_laws(cls, samples=40, seed=20260811)` checks, over a pool of
`max(3, samples)` generated replicas:

| Law | Test |
|---|---|
| **deterministic** | `h(a.merge(b)) == h(a.merge(b))` |
| **commutative** | `h(a.merge(b)) == h(b.merge(a))` |
| **associative** | `h(a.merge(b).merge(c)) == h(a.merge(b.merge(c)))` |
| **idempotent** | `h(a.merge(a)) == h(a)` **and** `h(a.merge(b).merge(b)) == h(a.merge(b))` |

`h` is the canonical state hash, so the check is *"insensitive to the
incidental ordering of Python containers and sensitive to every semantic
difference"*.

The report (`LawReport`) records, per law, the number checked and the full
failing samples (including both merge results for a commutativity failure).
Token: `CRDT_LAWS_VERIFIED` or `CRDT_LAWS_FAILED`.

`verify_all_crdt_laws(samples, seed)` runs all five types and reports the
conjunction.

**Normative statement.** These are **empirical** checks over generated samples,
not proofs. A conforming implementation **SHALL** report the sample count and
the seed alongside the verdict, and **SHALL NOT** present
`CRDT_LAWS_VERIFIED` as a proof of the laws for all inputs.

---

## 5. Version vectors

`crdt.VersionVector(replica_id, versions)`:

| Method | Semantics |
|---|---|
| `tick()` | increment own counter |
| `merge(other)` | pointwise maximum |
| `dominates(other)` | `≥` everywhere and `>` somewhere |
| `concurrent(other)` | neither dominates and they are unequal |
| `compare(other)` | `EQUAL` \| `DOMINATES` \| `DOMINATED` \| `CONCURRENT` |

`fabric.VectorClock` is the execution-layer counterpart with the same
semantics plus `receive` (merge then tick) and `happens_before`.

---

## 6. Anti-entropy

`crdt.AntiEntropy(replicas)` (LCTL 1.5.x §26).

> **Pairs are visited in lexicographic order and each exchange is symmetric,
> so `converge()` is reproducible and terminates as soon as one full round
> changes nothing.**

`sync(a, b, round_index)`:

1. record both state hashes before;
2. `merged = replicas[a].merge(replicas[b])`;
3. **both** replicas are replaced by copies of the merge, each keeping its own
   `replica_id` — the exchange is symmetric;
4. a `SyncRecord` is appended with `a_hash_before`, `b_hash_before`,
   `merged_hash` and `changed`.

`converge(max_rounds=16)`:

* fewer than two replicas ⇒ trivially `ANTI_ENTROPY_CONVERGED`;
* otherwise, for each round, sync **every** pair in
  `itertools.combinations(sorted(ids), 2)`;
* stop when all state hashes are equal **and** no exchange changed anything;
* the report carries `converged`, the token (`ANTI_ENTROPY_CONVERGED` or
  `ANTI_ENTROPY_NOT_CONVERGED`), `rounds`, the replica list, the final hash
  (only when converged), every exchange, and the final value.

A non-converged run is reported as such; convergence is never assumed from
termination.

---

## 7. Quorum semantics

`crdt.FailureModel.quorum` is `replicas // 2 + 1` — a simple majority. It is
used by the contract rules, not by a running consensus protocol.

| Model field | Meaning |
|---|---|
| `crash_stop` | processes fail by stopping |
| `message_loss` | messages may be lost |
| `reliable_retransmit` | lost messages are retransmitted |
| `byzantine` | arbitrary faults |
| `max_concurrent_failures` | `f` |
| `replicas` | `n` |
| `failover` | a replacement owner exists |
| `anti_entropy` | an anti-entropy path exists |
| `crdt_declared` | the replicated types are declared CvRDTs |

`crdt.PartitionModel` declares `partitions_possible`, `bounded_duration`,
`max_partition_ticks`, `asymmetric`.

---

## 8. The consistency contract and the consensus boundary (design hole H9)

### 8.1 The hole

LCTL 1.4.x §10 names the consistency profiles but **never states the
partition/failure conditions that make each one impossible**. PA-LCTL declares
an explicit, citable rule table, `crdt.CONSISTENCY_RULES`, and **the verdict
always names the rule that fired**.

| Profile | Rules |
|---|---|
| `LINEARIZABLE` | `C1`, `C2`, `C3`, `C7` |
| `SEQUENTIAL` | `C1`, `C2`, `C3`, `C7` |
| `CAUSAL` | `C4`, `C7` |
| `EVENTUAL` | `C5`, `C7` |
| `CRDT_DECLARED` | `C6`, `C7` |
| `SINGLE_OWNER` | `C8` |
| `IMMUTABLE_REPLICA` | `C9` |

### 8.2 The rules — FLP-like and CAP-like limits made explicit

| Rule | Fires when | Stated reason |
|---|---|---|
| **C1** | `partitions_possible` and `replicas < 3` | a total order requires a majority quorum to remain reachable; **a minority side cannot serve a total order (CAP)** |
| **C2** | `partitions_possible` and `max_concurrent_failures >= quorum` | **no quorum survives** |
| **C3** | `byzantine` and `replicas < 3f + 1` | byzantine agreement needs `3f+1` replicas |
| **C4** | `message_loss` and not `reliable_retransmit` | **causal delivery cannot be reconstructed when messages are lost and never retransmitted** |
| **C5** | (`partitions_possible` and not `bounded_duration`) **or** (`message_loss` and neither retransmission nor anti-entropy) | *"'eventual' is vacuous under an unbounded partition; no convergence time can be stated"* |
| **C6** | not `crdt_declared`, **or** not `anti_entropy` | the profile asserts CRDT semantics that the failure model does not declare / convergence needs an anti-entropy path |
| **C7** | (implicit, always, except `SINGLE_OWNER`) | **no quantum state is replicated** |
| **C8** | `crash_stop` and not `failover` and `max_concurrent_failures > 0` | the single owner can crash and no failover is declared |
| **C9** | `byzantine` | immutability cannot be assumed against byzantine replicas without per-block attestation |

C1–C3 are the CAP and byzantine-agreement boundaries; C4 and C5 are the
liveness boundaries that make "causal" and "eventual" meaningful; together they
are the FLP-like limits PA-LCTL refuses to paper over. An asynchronous system
with unbounded partitions and no convergence path **cannot** be given a
guarantee, and the contract says so instead of degrading quietly.

### 8.3 `CONSISTENCY_GUARANTEE_BLOCKED`

> **The contract never downgrades.**

`ConsistencyContract.evaluate()` returns a `ContractVerdict`:

```json
{"profile": "LINEARIZABLE",
 "admissible": false,
 "token": "CONSISTENCY_GUARANTEE_BLOCKED",
 "rules_fired": ["C1", "C2"],
 "reasons": ["C1: ...", "C2: ..."],
 "required_conditions": ["a majority quorum reachable during every partition", ...],
 "partition_model": {...}, "failure_model": {...},
 "downgrade_offered": false,
 "note": "a blocked guarantee is never silently downgraded; the caller must restate the requirement or change the model"}
```

`require()` raises `ConsistencyGuaranteeBlocked` listing the rules and reasons.
`admit_value(v)` enforces C7 on any value entering the scope.

The admissible token is `CONSISTENCY_GUARANTEE_ADMITTED`.

**Normative obligations.** A conforming implementation **SHALL**:

1. name every rule that fired;
2. state the required conditions for the requested profile, whether or not it
   was admitted;
3. echo the partition and failure models it judged against;
4. set `downgrade_offered: false` and **SHALL NOT** substitute a weaker
   profile.

---

## 9. Where the contract is exercised

`ledgers.LedgerSet._consistency` evaluates **every** profile in
`lang.CONSISTENCY_PROFILES` against a fixed model
(`partitions_possible=True`, `bounded_duration=True`, `crash_stop=True`,
`anti_entropy=True`, `crdt_declared=True`, `replicas=2`) and emits every
verdict, alongside the CRDT law report, an anti-entropy convergence run over
three `GCounter` replicas, and the statement:

> `"quantum_replication": "QUANTUM_REPLICATION_REJECTED: an unknown quantum
> state is never replicated"`

Under that model, `LINEARIZABLE` and `SEQUENTIAL` fire `C1` (partitions
possible with only 2 replicas), so `CONSISTENCY_LEDGER.json` shows them
blocked — an example of the contract refusing rather than degrading.

`cli.cmd_consistency_check --profile <P>` evaluates one profile on demand.

---

## 10. Related diagnostics and tokens

| Code / token | Source | Meaning |
|---|---|---|
| `E-CONS-001` | `conformance.ADMISSION_CODES` | the requested consistency guarantee is not achievable |
| `E-CRDT-001` | `conformance.ADMISSION_CODES` | an invalid CRDT merge was attempted |
| `CONSISTENCY_GUARANTEE_BLOCKED` | `crdt.TOKEN_BLOCKED` | a requested guarantee is impossible |
| `CONSISTENCY_GUARANTEE_ADMITTED` | `crdt.TOKEN_ADMITTED` | admissible |
| `QUANTUM_REPLICATION_REJECTED` | `crdt.TOKEN_QUANTUM_REJECTED` | a QSTATE was offered to a replication type |
| `CRDT_LAWS_VERIFIED` / `CRDT_LAWS_FAILED` | `crdt` | empirical law check outcome |
| `ANTI_ENTROPY_CONVERGED` / `ANTI_ENTROPY_NOT_CONVERGED` | `crdt` | reconciliation outcome |

Exceptions: `CRDTError` (base), `QuantumReplicationRejected`,
`MergeTypeError`, `ConsistencyGuaranteeBlocked`.

---

## 11. Implementation status

| Element | Status | Note |
|---|---|---|
| Five CvRDT types | `OPERATIONAL` | `GCounter`, `PNCounter`, `GSet`, `ORSet`, `LWWRegister` |
| Four merge laws, empirically checked | `VERIFIED` | seeded and reproducible; empirical, not a proof |
| Explicit LWW clock policies (H10) | `OPERATIONAL` | policy is part of replica identity |
| Version vectors | `OPERATIONAL` | |
| Deterministic pairwise anti-entropy | `OPERATIONAL` | lexicographic pair order; symmetric exchange |
| Consistency rule table `C1`–`C9` (H9) | `OPERATIONAL` | verdict always names the rule |
| `CONSISTENCY_GUARANTEE_BLOCKED`, no downgrade | `OPERATIONAL` | `downgrade_offered: false` |
| Quantum exclusion from replication | `OPERATIONAL` | recursive guard at every mutator |
| Quorum arithmetic | `IMPLEMENTED` | used by the contract rules |
| A running consensus protocol (Paxos/Raft) | not implemented | the module reasons about consensus feasibility; it does not run consensus |
| Cross-machine replication | `BLOCKED` | `NETWORK=deny`; replicas are in-process objects |
| Delta-state / operation-based CRDTs | `SPECIFIED` | only state-based CvRDTs are implemented |
