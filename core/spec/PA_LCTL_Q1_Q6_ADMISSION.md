# PA-LCTL Q1–Q6 Admission Gate

Document: `PA_LCTL_Q1_Q6_ADMISSION.md`
Authority: `pacore.lang.Q6_ADMISSION`,
`pacore.commutation.CommutationAuthority.submit_learned_rule`,
`pacore.commutation.CommutationAuthority.classify_pair`,
`pacore.commutation.plan_rewrites`,
`pacore.ledgers.LedgerSet._admission` (`ADMISSION_LEDGER.json`).

RFC 2119 keywords apply.

---

## 1. What the gate is for

The Q1–Q6 gate answers one question about any **proposed semantic rule** — a
commutation rule, a rewrite, a scheduling equivalence, a learned heuristic:

> *May this rule be used to change what the compiler does?*

The gate exists because PA-LCTL admits rules from more than one source: some
are algebraic theorems, some are numerically verified on bounded instances,
some hold only under target-specific assumptions, and some are *learned* from
observation. Without a gate, all four would be indistinguishable at the point
of use. With it, each rule carries a verdict, a proof reference and an
exactness label, and only some verdicts license action.

**The one rule an implementation must not violate:**

> **No learned rule may reach the optimizer before admission.**

`commutation.CommutationAuthority.submit_learned_rule` is the only entry point
for a learned rewrite and it is unconditional:

```python
def submit_learned_rule(self, rule: dict) -> str:
    rec = dict(rule)
    rec["admission"] = "QUARANTINE"
    rec["usable_by_optimizer"] = False
    self.quarantined.append(rec)
    return "QUARANTINE"
```

It returns `QUARANTINE`, sets `usable_by_optimizer = False`, appends the record
to `self.quarantined`, and **never** returns the rule to the caller in a usable
form. The quarantine list is emitted verbatim in
`COMMUTATION_LEDGER.json` → `quarantined_learned_rules` and in
`ADMISSION_LEDGER.json` → `quarantined_learned_rules`, alongside the stated
policy:

> *"a learned rewrite is QUARANTINE until Q1–Q6 admission and is never usable
> by the optimizer before that"*

---

## 2. The six questions

Q1–Q6 are the ordered questions the gate asks. Q1–Q5 are decided by the
authority's rule cascade (`classify_pair`), and Q6 is the verdict that names
the outcome.

| Gate | Question | Decided by | Evidence produced |
|---|---|---|---|
| **Q1** | Is the rule *well formed* — does it name operands, a precondition and a claimed effect? | `classify_pair` builds the operator signature `sig = "<opA>([supports]) ~ <opB>([supports])"`; a malformed pair cannot produce a signature | the signature string |
| **Q2** | Does an **admitted rule class** cover this case? | the ordered cascade `R-DISJOINT` → `R-MEAS-BARRIER` → `R-RESET` → `R-DIAG` → `R-ROT-SAME-AXIS` → `R-PAULI-SYMPLECTIC` → `R-CLIFFORD-CONJ` → `R-CHANNEL-*` → `R-MATRIX-ORACLE` → `R-UNRESOLVED` | `rule_id` |
| **Q3** | Is the decision **exact**, numerically verified, or conditional? | each rule declares its own `exactness`: `EXACT`, `EXACT_NUMERICAL`, `APPROXIMATE`, `NO_FAITHFUL_FORM` | `exactness` |
| **Q4** | What **assumptions** does it depend on? | `target_assumptions` on the verdict; non-empty only for `R-CHANNEL-SAME` | `target_assumptions` tuple |
| **Q5** | Is the decision **recorded** with a reproducible proof? | `_record()` writes a `ProofRow` and mints `proof_id = "CP-" + sha256(...)[:16]`; duplicate proofs are de-duplicated by id | `ProofRow`, `proof_id` |
| **Q6** | What is the **admission verdict**? | the mapping in §4 | one of `lang.Q6_ADMISSION` |

A verdict **SHALL NOT** be produced without a `ProofRow` entering the ledger
(Q5 is not optional).

### 2.1 The proof row

`commutation.ProofRow` — nine fields, all emitted:

| Field | Content |
|---|---|
| `rule_id` | which rule class fired |
| `precondition` | the rule's stated precondition |
| `operator_signature` | `"<opA>([supports]) ~ <opB>([supports])"` |
| `commutator_status` | the `COMMUTATION_STATUS` verdict |
| `equivalence_status` | `SEMANTICS_PRESERVING` unless the verdict is `NON_COMMUTING`, in which case `ORDER_SIGNIFICANT` |
| `exactness` | `EXACT` / `EXACT_NUMERICAL` / `APPROXIMATE` / `NO_FAITHFUL_FORM` |
| `target_assumptions` | tuple, possibly empty |
| `source_hash` | sha256 of the signature, 32 hex |
| `result_hash` | sha256 of the verdict, 32 hex |
| `proof_hash` | sha256 over `rule_id\|precondition\|signature\|verdict\|exactness\|assumptions` |

---

## 3. The nine Q6 verdicts

`lang.Q6_ADMISSION` — LCTL 1.3.x §58 fixes nine values (the 1.2.x set plus
`ADMIT_NUMERICALLY_VERIFIED`).

| # | Verdict | Meaning | May the optimizer act on it? |
|---|---|---|---|
| 1 | `ADMIT_EXACT` | Proven algebraically or symplectically; holds unconditionally. | **Yes**, unconditionally. |
| 2 | `ADMIT_NUMERICALLY_VERIFIED` | Verified by the bounded matrix oracle to a **stated** tolerance, on a bounded instance. | **Yes**, for instances within the stated bound, with the tolerance recorded. |
| 3 | `ADMIT_APPROXIMATE` | Holds under a stated approximation; the neglected magnitude is reported. | **Yes**, but the result **SHALL** be labelled approximate and **SHALL NOT** be promoted to exact. |
| 4 | `ADMIT_TARGET_SPECIFIC` | Holds only under target assumptions that are recorded with the verdict. | **Yes**, only against a target that satisfies the recorded assumptions. This environment binds no physical target, so in practice such rules are recorded and not acted on. |
| 5 | `OBSERVE` | The rule is plausible and reproducible but not admitted. It is recorded so it can be studied. | **No.** |
| 6 | `QUARANTINE` | The rule's provenance is not sufficient for admission — the default for every learned rule. | **No.** |
| 7 | `REJECT_INVALID` | The rule is wrong: it contradicts an admitted rule or fails its own check. | **No**, and it **SHALL NOT** be resubmitted unchanged. |
| 8 | `REJECT_NO_FAITHFUL_FORM` | No faithful form of the rule exists in this language — it cannot be stated without changing its meaning. | **No.** |
| 9 | `REJECT_IMPOSSIBLE` | The rule asserts something impossible (e.g. it would require cloning). | **No.** |

Verdicts 1–4 are *admitting*; 5–6 are *holding*; 7–9 are *terminal refusals*.

### 3.1 Verdict monotonicity

A verdict **SHALL NOT** be upgraded without new evidence, and **SHALL NOT** be
upgraded at all across the exact/approximate boundary. Concretely:

* `ADMIT_APPROXIMATE` **SHALL NOT** become `ADMIT_EXACT` because a particular
  instance happened to come out exact.
* `ADMIT_NUMERICALLY_VERIFIED` **SHALL NOT** become `ADMIT_EXACT`; it is bounded
  by `MATRIX_ORACLE_MAX_QUBITS` and by `MATRIX_COMMUTATOR_TOL`, and both bounds
  are part of the claim.
* `QUARANTINE` may become any verdict, including a refusal, **only** through an
  admission decision recorded in `ADMISSION_LEDGER.json`.

---

## 4. Verdict derivation

`ledgers.LedgerSet._admission` maps every `ProofRow` in the authority's ledger
to a Q6 verdict:

```python
exact_map = {
    "EXACT":            "ADMIT_EXACT",
    "EXACT_NUMERICAL":  "ADMIT_NUMERICALLY_VERIFIED",
    "APPROXIMATE":      "ADMIT_APPROXIMATE",
    "NO_FAITHFUL_FORM": "QUARANTINE",
}
verdict = exact_map.get(row.exactness, "OBSERVE")
if row.commutator_status == "COMMUTING_TARGET_CONDITIONAL":
    verdict = "ADMIT_TARGET_SPECIFIC"
```

so:

| Rule class | `exactness` | `commutator_status` | Q6 verdict |
|---|---|---|---|
| `R-DISJOINT` | `EXACT` | `DISJOINT` | `ADMIT_EXACT` |
| `R-MEAS-BARRIER`, `R-RESET` | `EXACT` | `NON_COMMUTING` | `ADMIT_EXACT` (the *decision* is exact; the pair is simply not reorderable) |
| `R-DIAG`, `R-ROT-SAME-AXIS`, `R-PAULI-SYMPLECTIC`, `R-CLIFFORD-CONJ` | `EXACT` | `COMMUTING_EXACT` / `NON_COMMUTING` | `ADMIT_EXACT` |
| `R-MATRIX-ORACLE` (commuting) | `EXACT_NUMERICAL` | `COMMUTING_NUMERICALLY_VERIFIED` | `ADMIT_NUMERICALLY_VERIFIED` |
| `R-MATRIX-ORACLE` (not commuting) | `EXACT` | `NON_COMMUTING` | `ADMIT_EXACT` |
| `R-CHANNEL-SAME` | `APPROXIMATE` | `COMMUTING_TARGET_CONDITIONAL` | `ADMIT_TARGET_SPECIFIC` (the target-conditional override wins over `ADMIT_APPROXIMATE`) |
| `R-CHANNEL-MIXED`, `R-UNRESOLVED` | `NO_FAITHFUL_FORM` | `UNRESOLVED` | `QUARANTINE` |

`REJECT_INVALID`, `REJECT_NO_FAITHFUL_FORM` and `REJECT_IMPOSSIBLE` are
**vocabulary reserved for rule submission**; the automatic mapping above never
produces them, because a rule the cascade cannot decide is held in
`QUARANTINE` rather than declared wrong. `REJECT_INVALID` *is* produced
elsewhere, as a concurrency decision in `lang.CONCURRENCY_DECISIONS`.

---

## 5. What quarantine means

A quarantined item is:

1. **Recorded.** It appears in `CommutationAuthority.quarantined` and is
   serialized into both `COMMUTATION_LEDGER.json` and
   `ADMISSION_LEDGER.json`. Quarantine is never a silent drop.
2. **Flagged.** Every quarantined record carries `admission: "QUARANTINE"` and
   `usable_by_optimizer: false` as explicit fields, so a consumer cannot miss
   the status by reading only the payload.
3. **Inert.** No code path in `pacore` reads `quarantined` for any purpose
   other than reporting. In particular, `commutation.plan_rewrites` — the only
   producer of rewrite proposals — never consults it.
4. **Reviewable.** The record retains whatever the submitter supplied, so a
   human or a later admission process has the full proposal.
5. **Not a rejection.** Quarantine says "not admitted", not "wrong". A
   quarantined rule may later be admitted with evidence, or refused with one of
   the three terminal verdicts.

### 5.1 The rewrite policy

`commutation.plan_rewrites` proposes only rewrite classes it can prove:

| Rewrite | Condition | Admission | Effect recorded |
|---|---|---|---|
| `ROTATION_FUSION` | adjacent nodes, identical support, same rotation op | `ADMIT_EXACT` | `resource_effect {gate_count: -1}`, `error_effect {delta: 0.0}` |
| `INVERSE_CANCELLATION` | adjacent nodes, identical support, `inverse[a] == b` | `ADMIT_EXACT` | `resource_effect {gate_count: -2}`, `error_effect {delta: 0.0}` |
| `REORDER_CANDIDATE` | verdict is `COMMUTING_EXACT` or `COMMUTING_NUMERICALLY_VERIFIED` | **`OBSERVE`** | *"commuting pair; reordering exposes a wider antichain but is not applied automatically"* |

Every proposal carries a `proof_ref` — the `proof_id` of the verdict that
justified it. The ledger states the policy:

> *"only ADMIT_EXACT rewrites may be applied; OBSERVE candidates are reported,
> never used"*

The inverse table is explicit: `S↔SDG`, `T↔TDG`, and the self-inverse
`X`, `Y`, `Z`, `H`, `CX`, `CNOT`, `CZ`, `SWAP`.

---

## 6. Interaction with concurrency admission

The Q6 gate governs **rules**. `ses.admit_concurrency` governs **pairs**, using
those rules. The two vocabularies are distinct and **SHALL NOT** be conflated:

| | Governs | Vocabulary | Symbol |
|---|---|---|---|
| Q6 admission | a rule's usability | 9 values | `lang.Q6_ADMISSION` |
| Concurrency admission | a node pair's schedulability | 12 values | `lang.CONCURRENCY_DECISIONS` |

`REJECT_INVALID` appears in both, with different meanings: in Q6 it means "this
rule is wrong"; in concurrency it means "this pair is not admissible at all".

`ADMISSION_LEDGER.json` reports both side by side:

```json
{
  "schema": "PA-LCTL/ADMISSION_LEDGER/1",
  "q6_vocabulary": [...9 values...],
  "concurrency_decisions": {"PARALLEL_EXACT": 31, "SERIALIZE_COUPLING": 1},
  "commutation_rule_admission": [
    {"rule_id": "R-DIAG", "proof_hash": "...", 
     "commutator_status": "COMMUTING_EXACT",
     "exactness": "EXACT", "q6_admission": "ADMIT_EXACT"}
  ],
  "quarantined_learned_rules": [],
  "learned_rule_policy": "a learned rewrite is QUARANTINE until Q1-Q6 admission and is never usable by the optimizer before that"
}
```

Rules are sorted by `(rule_id, proof_hash)` so the ledger is deterministic.

---

## 7. Normative obligations

A conforming implementation:

1. **SHALL** attach a Q6 verdict to every rule before it can influence a
   compiler decision.
2. **SHALL** record a proof row for every verdict, with a reproducible
   `proof_hash`.
3. **SHALL** default every learned or observed rule to `QUARANTINE` with
   `usable_by_optimizer = false`.
4. **SHALL NOT** consult the quarantine list from any optimizing code path.
5. **SHALL NOT** upgrade a verdict across the exact/approximate boundary.
6. **SHALL** state the bound and the tolerance whenever it emits
   `ADMIT_NUMERICALLY_VERIFIED` (here: 6 qubits, `1e-12`).
7. **SHALL** record `target_assumptions` whenever it emits
   `ADMIT_TARGET_SPECIFIC`, and **SHALL NOT** act on such a rule without a
   target that satisfies them.
8. **SHALL** apply only `ADMIT_EXACT` rewrites automatically; anything else is
   reported.

---

## 8. Implementation status

| Element | Status | Note |
|---|---|---|
| Nine-value Q6 vocabulary | `OPERATIONAL` | `lang.Q6_ADMISSION` |
| Proof ledger with reproducible hashes | `OPERATIONAL` | de-duplicated by `proof_id` |
| Automatic verdict derivation from exactness | `OPERATIONAL` | `ledgers.LedgerSet._admission` |
| Learned-rule quarantine | `OPERATIONAL` | unconditional; inert; reported |
| `ADMIT_EXACT` rewrite application policy | `OPERATIONAL` | fusion and inverse cancellation |
| `OBSERVE` reorder candidates | `OPERATIONAL` | reported, never applied |
| `REJECT_INVALID` / `REJECT_NO_FAITHFUL_FORM` / `REJECT_IMPOSSIBLE` as *rule* verdicts | `SPECIFIED` | vocabulary present; no code path emits them for rules — undecidable cases are held in `QUARANTINE` instead of being declared wrong |
| A promotion path out of `QUARANTINE` | `SPECIFIED` | no automatic admission process exists; promotion is an out-of-band decision that must be recorded |
| `ADMIT_TARGET_SPECIFIC` acted upon | `BLOCKED` | no authenticated target exists to satisfy the assumptions |
