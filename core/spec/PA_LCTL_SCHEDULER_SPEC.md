# PA-LCTL Temporal Scheduler Specification

Document: `PA_LCTL_SCHEDULER_SPEC.md`
Authority: `pacore.planner` — `SCHEDULER_STAGES`, `SCHEDULE_MODES`,
`ScheduledOp`, `Schedule`, `TemporalScheduler`, and
`pacore.ledgers.LedgerSet._dynamic_schedule`.

RFC 2119 keywords apply.

---

## 1. Contract

`TemporalScheduler(topology, commutation_authority, coherence_budget=inf)`

```python
schedule(ses, po, part, placement, concurrency_records,
         mode="CRITICAL_PATH", exact=False) -> Schedule
```

`assert mode in SCHEDULE_MODES` guards entry. The scheduler is **total**: it
always returns a `Schedule`, and every refusal is recorded in
`Schedule.blocked` rather than raised. This is deliberate — a blocked schedule
is inspectable evidence; an exception is not.

---

## 2. The fifteen scheduler stages

`planner.SCHEDULER_STAGES` (LCTL 1.2.x §30, 1.3.x §14, 1.4.x §21, 1.5.x §18).
Every stage writes an entry into `Schedule.stage_ledger`; stages that produced
nothing are backfilled with `{}` by `stage.setdefault(st, {})`, so **all
fifteen keys are always present**.

| # | Stage | Input | Recorded |
|---|---|---|---|
| 1 | `dependency_extraction` | SES, partial order | `{"edges": n, "reduction": m}` |
| 2 | `ownership_analysis` | SES lineages | `{"quantum_objects": count of distinct lineages}` |
| 3 | `commutation_analysis` | concurrency records | `{"pairs", "serialized", "reasons": sorted distinct SERIALIZE_* decisions}` |
| 4 | `coupling_analysis` | SES edges | `{"verdicts": {"<src>-><dst>": COUPLING_VERDICT}}` from `planner.coupling_graph` |
| 5 | `critical_path_analysis` | partial order | `{"span": D, "path": [...]}` |
| 6 | `partition_proposal` | partition | `{"k", "scalarized"}` |
| 7 | `topology_binding` | topology | `{"topology": name, "topology_hash": sha256}` |
| 8 | `placement` | placement map | `{"map": {"<partition>": target}}` |
| 9 | `routing` | classical router | `{"routes": count}` |
| 10 | `communication_planning` | routes | `{"messages": [{from, to, route, payload_bytes, t_message}]}` |
| 11 | `timing` | the list scheduler | `{"makespan": float}` |
| 12 | `crosstalk_validation` | topology crosstalk pairs | `{"violations": ["A~B:CROSSTALK_PAIR(x,y)"]}` |
| 13 | `error_budget_validation` | SES error models | `{"summed_independent_p", "note"}` |
| 14 | `resource_validation` | target occupancy | `{"overloaded_targets": [...]}` |
| 15 | `schedule_canonicalization` | the op list | `{"ops": count}` |

A conforming implementation **SHALL** emit all fifteen keys and **SHALL NOT**
omit a stage that found nothing — an absent stage is indistinguishable from an
unrun one, and that ambiguity is what the backfill removes.

---

## 3. The eleven schedule modes

`planner.SCHEDULE_MODES`. The mode selects a **priority function**; the list
scheduler and every validation stage are identical across modes.

| Mode | Priority (`_priority`) |
|---|---|
| `CRITICAL_PATH` | bottom level: `dur(n) + max(bottom(succ))` |
| `HEFT` | identical to `CRITICAL_PATH` |
| `BALANCED` | identical to `CRITICAL_PATH` |
| `ASAP` | `-level(n)` — earliest ASAP level first |
| `ALAP` | `+level(n)` — latest level first |
| `COMMUNICATION_AWARE` | `bottom(n) + 2.0 * len(entanglement_set(n))` |
| `FIDELITY_AWARE` | `bottom(n) * (1 + len(error_model(n)))` |
| `COHERENCE_AWARE` | identical to `FIDELITY_AWARE` |
| `CROSSTALK_AWARE` | identical to `FIDELITY_AWARE` |
| `DEADLINE_AWARE` | identical to `FIDELITY_AWARE` |
| `ENERGY_AWARE` | identical to `FIDELITY_AWARE` |

> **Stated honestly.** Eleven mode *names* map to **four** distinct priority
> functions. `HEFT` and `BALANCED` are aliases of `CRITICAL_PATH`;
> `FIDELITY_AWARE`, `COHERENCE_AWARE`, `CROSSTALK_AWARE`, `DEADLINE_AWARE` and
> `ENERGY_AWARE` share one error-model-weighted function. The names are
> `SPECIFIED` vocabulary; the behaviours are `IMPLEMENTED_PARTIAL`. An
> implementation **SHALL NOT** claim that, for example, `ENERGY_AWARE` optimizes
> energy — it currently biases toward nodes carrying more declared error keys.

The bottom level is computed once, in reverse topological order:

```python
bottom[n] = duration_model(n) + max(bottom[m] for m in succ[n], default 0.0)
```

---

## 4. Timing

### 4.1 The list scheduler

Nodes are ordered by `(-priority, topological index)` and then scheduled by
repeated passes:

```
while not all scheduled:
    progressed = False
    for n in pending:
        if already scheduled or any predecessor unscheduled: continue
        est = max over predecessors p of (finish[p] + comm_delay[(p, n)])
        tgt = placement[partition[n]]  or "__unplaced__"
        start = max(est, target_free[tgt])
        end   = start + duration_model(n)
        target_free[tgt] = end;  finish[n] = end
        record ScheduledOp(n, start, end, tgt, partition, layer=0, reason=...)
        progressed = True
    if not progressed:
        blocked.append("DISTRIBUTED_DEADLOCK_DETECTED:" + first 8 remaining)
        break
```

Properties:

* **One operation at a time per target.** `target_free[tgt]` is a single
  timestamp, so a target is modelled as a unit-capacity resource regardless of
  its declared `capacity`.
* **Communication delay is charged on the edge**, from
  `comm_delay[(pred, node)] = t_message` of the planned route.
* **Unplaced partitions** land on the pseudo-target `"__unplaced__"`, which has
  its own free-time counter. They are scheduled, not dropped, so the makespan
  reflects them.
* **No progress ⇒ `DISTRIBUTED_DEADLOCK_DETECTED`** with the first eight
  remaining node ids, appended to `blocked`. The loop then breaks rather than
  spinning.

Each `ScheduledOp` records `node_id`, `start`, `end`, `target`, `partition`,
`layer` and a `reason` string of the form
`"mode=<M>; est=<e>; target_free=<s>"`, so every placement in time is
attributable.

> **Note.** `ScheduledOp.layer` is written as `0` for every op. The layer
> information lives in `Schedule.layers`, which is the partial order's ASAP
> antichain list. The field is `SCAFFOLDED`.

### 4.2 Makespan

`makespan = max(op.end)`, `0.0` for an empty schedule.

---

## 5. Crosstalk validation

For every pair of scheduled ops **on the same target** whose intervals overlap
strictly (`a.start < b.end - 1e-12 and b.start < a.end - 1e-12`):

```python
s = topology.crosstalk_status(ses.nodes[a.node_id], ses.nodes[b.node_id])
if s != "OK":
    violations.append(f"{a.node_id}~{b.node_id}:{s}")
    blocked.append(f"CROSSTALK_CONFLICT:{s}")
```

`Topology.crosstalk_status(na, nb)` returns `OK` when the two nodes have
different owners or the owner is unknown; otherwise it scans the owner's
declared `crosstalk_pairs` against the union of each node's reads and writes,
in sorted order, and returns `CROSSTALK_PAIR(x,y)` for the first conflict.

Because §4.1 serializes per target, an overlap can only arise between an op on
a real target and one on `"__unplaced__"`, or through a scheduler variant that
permits parallel target use. The validation stage is therefore always run and
always reported, and in the reference list scheduler it normally finds nothing.
This is a real `IMPLEMENTED_PARTIAL`: crosstalk is enforced earlier and more
effectively at **concurrency admission** (`SERIALIZE_CROSSTALK`), which
prevents the pair from ever being co-scheduled.

---

## 6. Error-budget validation

```python
total_err = sum(float(node.error_model.get("p", 0.0) or 0.0) for node in SES)
stage["error_budget_validation"] = {
    "summed_independent_p": round(total_err, 9),
    "note": "sum is an upper bound only under an independence assumption"}
```

The note is normative and **SHALL** accompany the number. This is a fast
scheduling-stage figure; the authoritative composition is
`erroralgebra.compose`, which applies the law registry and may return
`ERROR_COMPOSITION_UNRESOLVED`. A conforming implementation **SHALL NOT**
present `summed_independent_p` as a composed error.

A `ValueError` while parsing a `p` value is caught and the term skipped — an
untyped error cell never aborts a schedule.

---

## 7. Resource validation

```python
overload = [t for t, f in target_free.items()
            if t in topology.nodes and f > topology.nodes[t].capacity * 1e9]
```

Reported as `resource_validation.overloaded_targets`. The `* 1e9` factor makes
this effectively non-binding for realistic loads; it is the same threshold used
by `Placer`'s `capacity_pass`, so the two agree. This is an
`IMPLEMENTED_PARTIAL` gate, recorded as such rather than presented as capacity
enforcement.

---

## 8. Coherence exposure

```python
coh = {
  "operation_time":  sum(duration_model over every SES node),
  "idle_time":       max(0, makespan * max(1, distinct placed targets)
                            - operation_time),
  "route_time":      sum(t_message over the communication plan),
  "measurement_wait": 0.0,
  "classical_feedback_wait": 0.0,
  "entanglement_wait": 0.0,
}
coh["total_coherence_exposure"] = operation_time + route_time + idle_time
if coh["total_coherence_exposure"] > self.coherence_budget:
    blocked.append("SCHEDULE_BLOCKED_COHERENCE")
```

The component names are exactly `erroralgebra.EXPOSURE_COMPONENTS`.

> **Stated limitation.** Three of the six components are structurally present
> and set to `0.0`. The scheduler's total is therefore a **lower bound** on
> coherence exposure. `measurement_wait`, `classical_feedback_wait` and
> `entanglement_wait` are measurable elsewhere
> (`protocols.ClassicalFeedbackFabric`, `planner.q_link_cost`) but are not yet
> folded in. See `PA_LCTL_ERROR_MODEL.md` §6.3.

The default `coherence_budget` is `math.inf`, so the token is emitted only when
a caller supplies a finite budget.

---

## 9. Canonicalization and hashing

```python
def canonical(self) -> str:
    return json.dumps({
        "schema": "PA-LCTL/SCHEDULE/1", "mode": self.mode,
        "ops": [o.as_dict() for o in sorted(self.ops,
                                            key=lambda x: (x.start, x.node_id))],
        "makespan": round(self.makespan, 6),
        "layers": self.layers, "blocked": self.blocked,
    }, sort_keys=True, separators=(",", ":"))

def hash(self) -> str:  return sha256(canonical()).hexdigest()
```

Normative properties:

* Ops sorted by `(start, node_id)` — a total order even when two ops start
  simultaneously.
* Every float in `ScheduledOp.as_dict()` rounded to **6 decimals**, and the
  makespan likewise, so tiny floating-point differences cannot change the hash.
* The `stage_ledger` and `coherence` are **not** part of the hash. The hash
  identifies the *schedule*, not the reasoning that produced it.
* `blocked` **is** part of the hash: a blocked schedule and an unblocked one
  with the same ops are different artifacts.

The schedule hash appears in `PROVENANCE_LEDGER.json` (`schedule_hash`), in
QCIR-P2 `temporal_schedule`, and is the subject of the admission diagnostic
`E-SEAL-002` (a declared schedule seal that does not match).

---

## 10. The bounded exact makespan validator

```python
EXACT_LIMIT = 9

if exact and len(ses.order) <= EXACT_LIMIT:
    opt = self._exact_makespan(ses, po, part, placement)
```

`_exact_makespan` is a branch-and-bound over topological permutations:

```
rec(done, free, finish):
    if all done:  best = min(best, max(finish));  return
    if max(finish) >= best:  return                       # bound
    for every node whose predecessors are all done:
        est   = max(finish[p] for p in pred)
        tgt   = placement[partition[n]]
        start = max(est, free[tgt])
        end   = start + duration_model(n)
        recurse with updated copies of free and finish
```

Returns `None` when nothing finished (`inf`), else the rounded optimum. Note
that it does **not** charge communication delay, so its optimum is a
communication-free lower bound; the heuristic makespan may legitimately exceed
it.

### 10.1 The optimality claim

```python
"optimality_claim": ("PROVEN_OPTIMAL_BOUNDED_INSTANCE"
                     if self.exact_optimum is not None
                     and abs(self.makespan - self.exact_optimum) < 1e-9
                     else "HEURISTIC_NO_OPTIMALITY_CLAIM")
```

`exact_bounded_optimum` is `None` unless the validator ran. A conforming
implementation **SHALL NOT** claim optimality on any other basis, and
**SHALL** scope the claim to the bounded instance.

---

## 11. `Schedule.as_dict()` — the emitted ledger

```json
{"schema": "PA-LCTL/SCHEDULE_LEDGER/1",
 "mode": "CRITICAL_PATH",
 "ops": [{"node_id": "R010", "start": 0.0, "end": 1.0,
          "target": "N0", "partition": 0, "layer": 0,
          "reason": "mode=CRITICAL_PATH; est=0.000; target_free=0.000"}],
 "makespan": 10.26,
 "layers": [["R000", "R001"], ["R010"]],
 "stage_ledger": { ...all fifteen stages... },
 "blocked": [],
 "exact_bounded_optimum": null,
 "optimality_claim": "HEURISTIC_NO_OPTIMALITY_CLAIM",
 "coherence_exposure": {"operation_time": 14.0, "idle_time": 6.5,
                        "route_time": 0.0, "measurement_wait": 0.0,
                        "classical_feedback_wait": 0.0,
                        "entanglement_wait": 0.0,
                        "total_coherence_exposure": 20.5},
 "schedule_hash": "6172..."}
```

`SCHEDULE_LEDGER.json` additionally prefixes the full `stages` and `modes`
vocabularies, so a consumer can validate the mode without importing the code.

---

## 12. The `blocked` vocabulary

`Schedule.blocked` is a list of strings; each entry is a refusal that did not
abort the schedule.

| Entry | Emitted when |
|---|---|
| `ROUTE_MISSING:<src>-><dst>` | no classical route between two placed targets |
| `DISTRIBUTED_DEADLOCK_DETECTED:<n1>,<n2>,...` | the list scheduler made no progress |
| `CROSSTALK_CONFLICT:<status>` | overlapping ops on one target violate a calibrated crosstalk pair |
| `SCHEDULE_BLOCKED_COHERENCE` | total exposure exceeds the declared budget |

An empty `blocked` list is the L4 conformance condition
(`PA_LCTL_NORMATIVE_SPEC.md` §6.1).

---

## 13. Dynamic re-scheduling

`ledgers.LedgerSet._dynamic_schedule` re-plans the same SES under **every**
admitted mode and reports the spread:

```json
{"schema": "PA-LCTL/DYNAMIC_SCHEDULE_LEDGER/1",
 "candidates": [{"mode": "ASAP", "makespan": 11.02, "blocked": [],
                 "schedule_hash": "..."}, ...],
 "selected_mode": "CRITICAL_PATH",
 "lowest_makespan_mode": "ALAP",
 "optimality_claim": "HEURISTIC_NO_OPTIMALITY_CLAIM",
 "note": "re-planning only; this runtime never re-schedules against measurements taken from a physical target"}
```

The note is normative. **This is the honest form of "dynamic scheduling"
available without a physical target**: the same graph is re-planned and the
observed makespan of each mode is reported. No mode is claimed optimal, and
the selection is not changed by the comparison.

---

## 14. Determinism

| Choice | Tie-break |
|---|---|
| Ready-node order | `(-priority, topological index)` |
| Op emission order | `sorted(ops, key=(start, node_id))` |
| Crosstalk pair scan | `sorted(qa) × sorted(qb)` |
| Coupling verdict keys | `"<src>-><dst>"` |
| Stage ledger | dict with all fifteen keys, serialized sorted |
| Hash | 6-decimal rounding, sorted JSON keys |

Two runs of `TemporalScheduler.schedule` on the same inputs produce identical
`hash()`.

---

## 15. Implementation status

| Element | Status | Note |
|---|---|---|
| Fifteen stages, all always emitted | `OPERATIONAL` | backfilled with `{}` |
| Eleven mode names | `OPERATIONAL` | vocabulary and validation |
| Distinct mode behaviours | `IMPLEMENTED_PARTIAL` | 11 names → 4 priority functions |
| List scheduling with communication delay | `OPERATIONAL` | one op at a time per target |
| Deadlock detection in the scheduling loop | `OPERATIONAL` | token appended, loop breaks |
| Crosstalk validation stage | `IMPLEMENTED_PARTIAL` | enforced effectively at concurrency admission; the stage rarely fires because targets are serialized |
| Error-budget stage | `IMPLEMENTED` | naive independent sum; assumption stated inline |
| Resource validation stage | `IMPLEMENTED_PARTIAL` | `capacity * 1e9` threshold |
| Coherence exposure | `IMPLEMENTED_PARTIAL` | 3 of 6 components; lower bound |
| `SCHEDULE_BLOCKED_COHERENCE` | `OPERATIONAL` | |
| Canonicalization and hashing | `OPERATIONAL` | 6-decimal rounding, total ordering |
| Bounded exact makespan validator | `OPERATIONAL` | ≤9 nodes; communication-free lower bound |
| Optimality claim discipline | `OPERATIONAL` | claim only when the validator ran |
| `ScheduledOp.layer` | `SCAFFOLDED` | always 0; layers live in `Schedule.layers` |
| Dynamic re-planning ledger | `OPERATIONAL` | re-planning only; never against measurements |
| Measurement-driven adaptive rescheduling | `BLOCKED` | requires a physical target |
