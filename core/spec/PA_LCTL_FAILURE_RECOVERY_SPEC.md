# PA-LCTL Failure and Recovery Specification

Document: `PA_LCTL_FAILURE_RECOVERY_SPEC.md`
Authority: `pacore.resilience` in full; `pacore.lang.RECOVERY_CLASSES`;
`pacore.fabric.StragglerEngine.quantum_remedy`.

RFC 2119 keywords apply.

---

## 1. The structural rule

> **Unknown physical quantum state can never be reconstructed from a classical
> checkpoint, rolled back, or duplicated. Every path that would require it
> returns `RECOVERY_IMPOSSIBLE` or raises, and no path anywhere silently
> substitutes an approximation.**

This single rule generates the impossibility rules `I1`–`I5` (§4), the
checkpoint refusal (§7), the rollback refusal (§8), and the quantum straggler
policy in `fabric` (`REROUTE` or restart, never speculate).

---

## 2. The scenario catalog (design hole H11)

The documents enumerate failure kinds in prose without stable ids. PA-LCTL
fixes a canonical **22-entry** catalog, `resilience.SCENARIOS`, so campaign
results are comparable across runs and across versions.

`resilience.Scenario` fields: `scenario_id`, `kind`, `subject`, `detectable`,
`quantum_state_at_risk`, `description`.

| # | `scenario_id` | Kind | Subject | Detectable | Quantum state at risk |
|---|---|---|---|---|---|
| 1 | `worker_crash` | CLASSICAL | worker | ✓ | |
| 2 | `process_crash` | CLASSICAL | process | ✓ | |
| 3 | `domain_loss` | CLASSICAL | domain | ✓ | |
| 4 | `node_unavailable` | CLASSICAL | node | ✓ | |
| 5 | `memory_exhaustion` | CLASSICAL | worker | ✓ | |
| 6 | `classical_link_timeout` | CLASSICAL | link | ✓ | |
| 7 | `classical_packet_loss` | CLASSICAL | link | ✓ | |
| 8 | `dropped_message` | CLASSICAL | message | ✓ | |
| 9 | `duplicate_message` | CLASSICAL | message | ✓ | |
| 10 | `corrupt_message` | CLASSICAL | message | ✓ | |
| 11 | `quantum_link_failure` | QUANTUM | quantum_link | ✓ | **✓** |
| 12 | `ebit_generation_failure` | QUANTUM | ebit | ✓ | |
| 13 | `ebit_expiration` | QUANTUM | ebit | ✓ | |
| 14 | `stale_calibration` | QUANTUM | device | **✗** | **✓** |
| 15 | `qpu_unavailable` | QUANTUM | qpu | ✓ | **✓** |
| 16 | `route_invalidation` | TOPOLOGY | route | ✓ | **✓** |
| 17 | `route_degradation` | TOPOLOGY | route | **✗** | **✓** |
| 18 | `coherence_deadline_exceeded` | QUANTUM | qstate | ✓ | **✓** |
| 19 | `checkpoint_corruption` | STORAGE | checkpoint | ✓ | |
| 20 | `shard_corruption` | STORAGE | shard | ✓ | |
| 21 | `stale_topology` | TOPOLOGY | topology | **✗** | |
| 22 | `stale_reservation` | TOPOLOGY | reservation | ✓ | |

Four failure **classes**: `CLASSICAL`, `QUANTUM`, `STORAGE`, `TOPOLOGY`.
Three scenarios are **not locally detectable** (14, 17, 21) — a fact the
campaign reports rather than hides, because an undetectable failure cannot be
recovered from by the local detector alone. A caller may set
`detector_extended: True` in the context to model an external detector.

---

## 3. Deterministic failure injection (design hole H12)

> **Reproducibility is defined here as: the pair `(scenario_id, seed)`
> determines every injected value. The seed is derived by hashing the scenario
> id with the campaign seed, so adding a scenario never perturbs the draws of
> the others.**

```python
def _derive_seed(scenario_id, seed):
    h = sha256(f"{scenario_id}|{int(seed)}".encode()).digest()
    return int.from_bytes(h[:8], "big")
```

`FailureInjector.inject(scenario_id, target, context)`:

* an unknown id raises `UnknownScenarioError` listing the catalog;
* a per-scenario `random.Random(_derive_seed(...))` supplies every draw;
* the detail payload is scenario-specific and always includes the description.

| Scenario(s) | Injected detail |
|---|---|
| `classical_packet_loss` | `loss_fraction` ∈ [0.01, 0.4], `messages_lost` ∈ [1,12] |
| `classical_link_timeout` | `deadline_s` ∈ [0.05,1.0], `observed_s` ∈ [1.1,4.0] |
| `memory_exhaustion` | `requested_bytes` ∈ [2³⁰, 2³⁴], `limit_bytes` = 2³⁰ |
| `ebit_generation_failure`, `ebit_expiration` | `attempts` ∈ [1,8], `epr_state` = `FAILED` / `EXPIRED` |
| `stale_calibration` | `plan_epoch`, `device_epoch` = plan + [1,5] |
| `coherence_deadline_exceeded` | `budget_s`, `exposure_s` = budget × [1.05, 3.0] |
| `checkpoint_corruption`, `shard_corruption` | `block_index`, `expected_checksum`, `actual_checksum` |
| `route_degradation` | `claimed_fidelity` ∈ [0.9,0.99], `observed_fidelity` ∈ [0.3,0.85] |
| `duplicate_message` | `duplicates` ∈ [1,3] |
| `corrupt_message` | `corrupt_bytes` ∈ [1,64] |
| everything else | `nonce` |

**Injection is descriptive, not destructive.** *"Nothing here touches a network
or a real device: an injection produces a `FailureEvent` describing what a
runtime would observe, and the campaign drives the recovery machinery from that
description."*

`FailureEvent` records `scenario_id`, `kind`, `subject`, `target`, `seed`,
`derived_seed`, `detected`, `quantum_state_at_risk`, `detail` and
`logical_time`.

---

## 4. The eight recovery classifications

`lang.RECOVERY_CLASSES` — eight values. `RecoveryPlanner.classify` maps a
failure plus its context to **exactly one**, and

> **Rule order matters: the impossibility rules are evaluated first, so no
> later rule can ever promote an impossible recovery into an optimistic one.**

### 4.1 Impossibility rules (evaluated first, never overridden)

| Rule | Condition | Class |
|---|---|---|
| `I1` | unknown quantum, not classically known, **and** recovery would restore from a classical checkpoint | `RECOVERY_IMPOSSIBLE` — *"no classical record can contain it"* |
| `I2` | unknown quantum, not classically known, **and** recovery requires duplication | `RECOVERY_IMPOSSIBLE` — *"the no-cloning rule forbids it"* |
| `I3` | `coherence_deadline_exceeded` with unknown quantum, not classically known, and **no admissible boundary** | `RECOVERY_IMPOSSIBLE` |
| `I4` | `quantum_link_failure` with unknown quantum, **zero routes**, no boundary, not classically known | `RECOVERY_IMPOSSIBLE` |
| `I5` | (fallthrough) unknown quantum held, no route, no boundary, no classical description | `RECOVERY_IMPOSSIBLE` |

### 4.2 Ordinary rules

Evaluated in this order; the first match wins.

| Rule | Condition | Class | Reason |
|---|---|---|---|
| `R1` | scenario is `ebit_generation_failure` or `ebit_expiration` | `REGENERATE_ENTANGLEMENT` | entanglement is a regenerable resource; no unknown state is lost |
| `R2` | `state_classically_known` and scenario ∈ {worker_crash, process_crash, qpu_unavailable, stale_calibration, coherence_deadline_exceeded, quantum_link_failure} | `REPREPARE_KNOWN_STATE` | the preparation is classically known, so it can be re-prepared exactly |
| `R3` | scenario ∈ {route_invalidation, route_degradation, quantum_link_failure, node_unavailable, domain_loss} and `route_alternatives > 0` | `REROUTE_SAFE` | alternatives exist and no unknown state has to move |
| `R4` | `target_specific` | `RECOVERY_TARGET_SPECIFIC` | depends on a device-specific capability; cannot be stated portably |
| `R5` | `classical_only` and an `admissible_boundary` exists | `RESTART_FROM_CLASSICAL_BOUNDARY` | resume without reconstructing quantum state |
| `R6` | `classical_only` and scenario ∈ {checkpoint_corruption, shard_corruption, memory_exhaustion, worker_crash, process_crash} | `ROLLBACK_CLASSICAL_ONLY` | the damaged state is classical |
| `R7` | `idempotent` or scenario ∈ {dropped_message, duplicate_message, corrupt_message, classical_packet_loss, classical_link_timeout, stale_reservation, stale_topology} | `RETRY_SAFE` | classical and idempotent; a retry cannot change the observable result |
| `R8` | unknown quantum **with** an admissible boundary | `RESTART_FROM_CLASSICAL_BOUNDARY` | unknown quantum state is **discarded** and execution restarts from the boundary |
| `R9` | otherwise | `RESTART_FROM_CLASSICAL_BOUNDARY` | default classical remedy |

`assert cls in RECOVERY_CLASSES` guards the result. Every classification
appends a `ClassificationRecord` with the scenario, the class, the `rule_id`,
the reason and the sorted context keys — so the decision is reconstructible
from the record alone.

### 4.3 The context vocabulary

| Key | Default | Effect |
|---|---|---|
| `quantum_state_unknown` | the event's `quantum_state_at_risk` | drives the impossibility rules |
| `state_classically_known` | `False` | enables `R2` |
| `restore_from_classical_checkpoint` | `False` | triggers `I1` |
| `requires_duplication` | `False` | triggers `I2` |
| `route_alternatives` | `0` | enables `R3`, disarms `I4` |
| `admissible_boundary` | `None` | enables `R5`/`R8`, disarms `I3`/`I4` |
| `idempotent` | `False` | enables `R7` |
| `classical_only` | `not unknown_q` | enables `R5`/`R6` |
| `target_specific` | `False` | enables `R4` |
| `detector_extended` | `False` | marks an undetectable scenario as detected |

---

## 5. The supervision tree

`resilience.SUPERVISION_LEVELS = ("TASK", "WORKER", "DOMAIN", "FEDERATION")`
`resilience.SUPERVISION_ACTIONS = ("retry", "restart", "reroute", "escalate",
"abort")`

### 5.1 Per-level decision

`Supervisor.decide(subject, recovery_class, failure)`:

1. increment the per-subject attempt counter;
2. `RECOVERY_IMPOSSIBLE` ⇒ **`abort`**, with the reason *"recovery is
   impossible; aborting is the only honest action"*;
3. attempts above the level budget ⇒ `escalate`;
4. otherwise map the class to an action:

| Recovery class | Action |
|---|---|
| `REROUTE_SAFE` | `reroute` |
| `RETRY_SAFE`, `REGENERATE_ENTANGLEMENT` | `retry` |
| `RESTART_FROM_CLASSICAL_BOUNDARY`, `REPREPARE_KNOWN_STATE`, `ROLLBACK_CLASSICAL_ONLY` | `restart` |
| `RECOVERY_TARGET_SPECIFIC` | `escalate` |

### 5.2 Escalation

Default budgets: `TASK` 2, `WORKER` 2, `DOMAIN` 1, `FEDERATION` 1.

`SupervisionTree.handle(subject, class, failure, start_level="TASK")` walks
**strictly upward** until a level returns a non-`escalate` action. Escalating
past `FEDERATION` raises `SupervisionEscalation`:

> *"escalation past the FEDERATION supervisor for `<subject>` under `<class>`;
> no supervisor can absorb this failure"*

**Escalating past the top is a fail-closed condition, not a silent drop.** Each
decision records `escalated_from`, so the escalation path is reconstructible.

---

## 6. Recovery transactions

`resilience.RecoveryTransaction(transaction_id)` (LCTL 1.4.x §45).
States: `OPEN`, `COMMITTED`, `ROLLED_BACK`, `REFUSED`. Any operation on a
non-`OPEN` transaction raises `TransactionStateError`.

| Method | Behaviour |
|---|---|
| `begin(snapshot)` | records each snapshot entry as a classical prior value |
| `record_classical(key, prior)` | **raises `QuantumRollbackRefused`** if the value is quantum: *"use register_quantum, which is journal-only"* |
| `write(key, value)` | raises for a quantum value; journals the previous value once (`setdefault`) |
| `register_quantum(key, payload, classically_known=False)` | records **that** the transaction touched quantum state, with the class name and the known flag — **journal only, never a value** |
| `commit()` | `COMMITTED` |
| `rollback()` | if any registered quantum entry is **not** classically known: state becomes `REFUSED`, and `QuantumRollbackRefused` is raised — *"unknown physical quantum state can never be rolled back; only a classical-only rollback is permitted"*. Otherwise the journal is restored and the state becomes `ROLLED_BACK`. |

`as_dict()` reports the record plus a `live_state_hash` and the quantum
registrations.

---

## 7. Checkpoints

### 7.1 The seven kinds

`resilience.CHECKPOINT_KINDS`:

```
classical_compiler, source, ir, scheduler, classical_measurement,
classically_known_state_prep, simulator_state
```

An unknown kind raises `ResilienceError` listing the admitted set.

### 7.2 The prohibition on checkpointing unknown quantum state

```python
if _is_quantum_payload(payload) and not (
        kind == "classically_known_state_prep" and classically_known):
    raise QuantumCheckpointRefused(
        "QUANTUM_CHECKPOINT_REFUSED: checkpoint <id> of kind <k> carries "
        "unknown quantum state; a classical record can never represent it "
        "(LCTL 1.5.x s49)")
```

and, symmetrically:

```python
if kind == "classically_known_state_prep" and not classically_known:
    raise QuantumCheckpointRefused(
        "kind 'classically_known_state_prep' requires an explicit "
        "classically_known=True assertion")
```

**There is exactly one admitted way to checkpoint anything quantum**: declare
the kind `classically_known_state_prep` **and** assert `classically_known=True`
— i.e. checkpoint the *recipe*, never the state. A conforming implementation
**SHALL NOT** widen this.

Note that `simulator_state` is an admitted **kind**, but a payload that
`_is_quantum_payload` recognizes is still refused under it — the negative
conformance case `invalid_checkpoint_of_unknown_qstate` and the recovery
campaign both exercise exactly that.

### 7.3 Block structure and corruption

Every checkpoint is split into `BLOCK_BYTES = 256`-byte blocks of its canonical
JSON, each with its own SHA-256 (first 32 hex), *"so corruption is localized
rather than invalidating the whole record"*.

| Method | Behaviour |
|---|---|
| `verify(id)` | recomputes the blocks and reports `corrupt_blocks`; a block-count mismatch adds `-1`; token `CHECKPOINT_CHECKSUM_MISMATCH` on failure |
| `corrupt(id, index)` | deterministically corrupts one block (used by the campaign); an out-of-range index raises |
| `load(id)` | **verifies first**; raises on any corrupt block rather than returning damaged data |
| `digest()` | evidence hash over the block list |

---

## 8. Recovery campaigns

`resilience.run_campaign(scenarios=None, seed=20260811, contexts=None)`
(LCTL 1.6.x §81).

For each scenario: inject → classify (with the merged default and caller
context) → supervise → record.

```python
recovered = ev.detected and action in ("retry", "restart", "reroute")
```

An **undetected** failure is therefore never counted as recovered, regardless
of the action — which is why the three undetectable scenarios matter.

`CampaignEntry` records `scenario_id`, `injected`, `detected`,
`classification`, `action`, `recovered`, `evidence_hash`, `rule_id`, `reason`.
A `SupervisionEscalation` during the campaign is caught, the action becomes
`abort`, and the exception text is appended to the reason — the campaign
completes and reports rather than aborting.

`campaign_summary(entries)` reports the token
`RESILIENCE_CAMPAIGN_COMPLETE`, the scenario count, detected count, recovered
count, the `RECOVERY_IMPOSSIBLE` count, a per-class histogram and a
`campaign_hash`.

`DEFAULT_CONTEXTS` supplies a per-scenario context for all 22 scenarios, so a
campaign with no caller context is still meaningful and reproducible.

`conformance.recovery_campaign(seed, count)` additionally asserts, for every
non-impossible classification, that **checkpointing a `QuantumPayload` under
kind `simulator_state` is refused** — a live check that the prohibition holds.

---

## 9. Emitted ledgers

| File | Content |
|---|---|
| `FAILURE_LEDGER.json` | the full 22-scenario catalog, what was injected, what was detected, the count |
| `RECOVERY_LEDGER.json` | the eight recovery classes, every campaign entry, the summary |
| `LIVE_RECOVERY_LEDGER.json` | a real end-to-end inject-and-recover: a saved checkpoint, its verification, a real transaction rollback, the restored state, the injected failure, the classification record, the supervision decision, and the statement `"QUANTUM_ROLLBACK_REFUSED for unknown quantum state; only classical state is rolled back"` |

QCIR-P2's `recovery_policy` section carries the class vocabulary,
`"quantum_checkpoint": "REFUSED_FOR_UNKNOWN_QUANTUM_STATE"`,
`"classical_rollback": "ROLLBACK_CLASSICAL_ONLY"` and the supervision levels.

---

## 10. Tokens and exceptions

| Token | Symbol |
|---|---|
| `RECOVERY_IMPOSSIBLE` | `resilience.TOKEN_RECOVERY_IMPOSSIBLE` |
| `QUANTUM_CHECKPOINT_REFUSED` | `resilience.TOKEN_CHECKPOINT_REFUSED` |
| `QUANTUM_ROLLBACK_REFUSED` | `resilience.TOKEN_ROLLBACK_REFUSED` |
| `RESILIENCE_CAMPAIGN_COMPLETE` | `resilience.TOKEN_CAMPAIGN_COMPLETE` |
| `CHECKPOINT_CHECKSUM_MISMATCH` | `resilience.TOKEN_CHECKSUM_MISMATCH` |

Exceptions: `ResilienceError` (base), `UnknownScenarioError`,
`QuantumCheckpointRefused`, `QuantumRollbackRefused`, `TransactionStateError`,
`SupervisionEscalation`.

Related admission diagnostics: `E-CKPT-001` (a checkpoint of unknown quantum
state was requested), `E-RECOV-001` (the only admissible recovery would require
cloning).

---

## 11. Implementation status

| Element | Status | Note |
|---|---|---|
| 22-scenario catalog with stable ids (H11) | `OPERATIONAL` | four kinds; three undetectable, reported as such |
| Deterministic injection by `(scenario_id, seed)` (H12) | `OPERATIONAL` | per-scenario derived seed |
| Eight recovery classifications | `OPERATIONAL` | impossibility rules first, never overridden |
| Supervision tree with upward escalation | `OPERATIONAL` | escalating past `FEDERATION` raises |
| Recovery transactions, classical-only rollback | `OPERATIONAL` | quantum entries are journal-only |
| Seven checkpoint kinds | `OPERATIONAL` | |
| Prohibition on checkpointing unknown quantum state | `OPERATIONAL` | one narrow, explicitly-asserted exception |
| Block-level checksums and localized corruption | `OPERATIONAL` | |
| Reproducible campaigns | `OPERATIONAL` | `campaign_hash` stable for a seed |
| Injection into a live runtime | `SPECIFIED` | injection is descriptive; nothing is actually killed |
| Recovery against a physical target | `BLOCKED` | `BACKEND=none`; `qpu_unavailable` is modelled, not observed |
| Automatic re-execution after a recovery decision | `SPECIFIED` | the tree decides an action; executing it is the caller's job |
