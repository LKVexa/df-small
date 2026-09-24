# PA-LCTL Execution Fabric Specification

Document: `PA_LCTL_FABRIC_SPEC.md`
Authority: `pacore.fabric` in full.

RFC 2119 keywords apply.

---

## 1. Normative policy

> **`NETWORK=deny`, `BACKEND=none`. Nothing in this module opens a socket,
> resolves a name, or contacts a backend. Parallelism is realized with local
> threads and local processes only.**

The federation object model is therefore a **model of** a federation, executed
locally. Every "domain", "node" and "link" is a logical name inside one
process tree. A conforming implementation **SHALL NOT** report fabric results
as distributed execution across machines.

---

## 2. Execution profiles

`fabric.ExecutionProfile` mirrors `lang.EXECUTION_PROFILES` exactly; the module
asserts this at import:

```python
assert tuple(p.value for p in ExecutionProfile) == tuple(EXECUTION_PROFILES)
```

| Profile | `deterministic` | `uses_threads` | `uses_processes` | `may_reorder` | Runtime realization |
|---|---|---|---|---|---|
| `single_process_deterministic` | ✓ | | | | tasks run in submission order in the calling thread |
| `multi_thread_deterministic` | ✓ | ✓ | | | a real `ThreadPoolExecutor` with a **submission-order turnstile** (a `Condition` + turn counter) |
| `multi_process_deterministic` | ✓ | | ✓ | | a real `multiprocessing.Pool` with `pool.map` (ordered) |
| `multi_process_throughput` | | | ✓ | ✓ | `pool.imap_unordered`; completions harvested in whatever order the pool produces |

`DEFAULT_PROFILE = SINGLE_PROCESS_DETERMINISTIC`.
`ExecutionProfile.from_token` raises `ProfileError` for an unknown token.
`as_dict()` always reports `replay_trace_required: True`.

### 2.1 Design hole H8 — deterministic timeouts

> Deterministic timeouts cannot be wall-clock timeouts.

| Field | Honoured in | Semantics |
|---|---|---|
| `Task.timeout_ticks` | **every** profile | compared against the declared **logical cost model**: `cost_estimate > timeout_ticks` ⇒ `TIMED_OUT` **before the body runs** |
| `Task.timeout_s` | **only** `multi_process_throughput` | compared against `measured_duration` after a successful run |

Both are recorded on the task and in `as_dict()`, so the distinction is
auditable rather than implicit.

---

## 3. The federation object model

`fabric.Worker` → `WorkerGroup` → `ExecutionDomain` → `Federation`
(LCTL 1.5.x §21).

### 3.1 Common policy objects

Every level carries the same four rule objects, so a policy can be stated at
whichever scope owns it:

| Object | Fields | Fail-closed behaviour |
|---|---|---|
| `ResourceLimits` | `cpu_slots`, `memory_bytes`, `qpu_slots` (**default 0**), `ebit_budget` (default 0), `bandwidth_bytes_per_tick` | `can_satisfy` names the failing dimension; `merged` sums capacities and takes the **minimum** bandwidth |
| `MigrationRules` | `allow_classical_migration` (T), `allow_quantum_migration` (**F**), `require_explicit_movable` (T), `max_hops` (2), `forbid_cross_trust` (T) | `permits()` returns `(False, reason)` naming the rule |
| `ReplicationRules` | `allow_classical_replication` (T), `allow_quantum_replication` (**structurally F**), `replication_factor` ≥ 1, `consistency_profile` | `__post_init__` **raises** if quantum replication is enabled: *"a QSTATE is ownership-constrained (LCTL 1.1.x s6)"* |
| `ConsistencyRules` | `profile`, `ordering`, `quantum_scope` (`SINGLE_OWNER_ONLY`) | unknown profile raises |
| `Provenance` | `created_by`, `created_at_logical`, `source_ref`, `seal` | — |

`fabric._check_trust` validates every `trust` against `lang.TRUST_DOMAINS` at
construction of all four levels.

### 3.2 Structure

`WorkerGroup.add` rewrites a worker's `group_id`/`domain_id`/`federation_id`;
`ExecutionDomain.add` and `Federation.add` propagate downward. Duplicate ids
raise `FabricError`. `Federation.locate(worker_id)` returns the
`(federation, domain, group)` triple; `group_of` returns the containing group.

`build_flat_federation(n, groups=1, domains=1, ...)` builds a deterministic
federation with ids `W00`, `W01`, …, groups `D<d>G<g>`, domains `D<d>`, and
failure domains `FD<d>`.

### 3.3 Worker signals

A `Worker` carries measured signals used by the balancer and the straggler
engine: `alive`, `throughput_ema`, `memory_pressure`, `queue_delay`,
`heartbeat_latency`, `completed_tasks`.

---

## 4. The task runtime

`fabric.TaskRuntime(federation, profile, log, clock, max_workers=4)`.

### 4.1 Task states

`fabric.TASK_STATES`: `PENDING`, `READY`, `RUNNING`, `DONE`, `FAILED`,
`CANCELLED`, `TIMED_OUT`. `Task.done` is any of the last four.

### 4.2 Spawn

`spawn(fn, *args, task_id, priority, deadline, movable, owner, cost_estimate,
timeout_ticks, timeout_s, stage, resource_claim, provenance, **kwargs)`:

* duplicate `task_id` raises;
* absent `owner` round-robins over `federation.worker_ids()` by sequence
  number;
* a `resource_claim` is checked against the owner's limits and raises
  `ResourceClaimError` on failure;
* the task is set `READY`, stamped with `clock.tick()`, and a `SPAWN` event is
  appended with the claim as `resource_delta`.

### 4.3 Execution

`run()` selects every `READY` task, sorts by `(-priority, seq)` — **submission
order within a priority class is the deterministic schedule** — and dispatches
per profile (§2).

`_execute` records `TASK_START`, runs the body, catches **every** exception into
`Task.error` (`f"{type}: {msg}"`) with state `FAILED`, measures
`measured_duration` with `perf_counter`, applies the throughput-profile
wall-clock timeout, increments the owner's `completed_tasks` on success, appends
`TASK_DONE`/`TASK_FAIL`, and writes the duration into `log.metrics` —
**not** into the event, so the replay hash stays stable (hole H7).

`_run_processes` refuses **before** dispatch:

```python
if t.holds_quantum:
    raise OwnershipTransferError("task holds quantum payload(s) and can never "
                                 "cross a process boundary")
```

and `QuantumPayload.__reduce__` would raise anyway — two independent guards.

### 4.4 Await, cancel, barriers, events

* `await_task` runs pending work if needed, then raises `TaskCancelledError`,
  `TaskTimeoutError` or `TaskFailedError` for the corresponding states, else
  logs `AWAIT` and returns the result.
* `cancel(t, reason)` is a no-op on a finished task; otherwise sets
  `CANCELLED` and logs `failure_delta {"cancelled": 1}`.
* `barrier(level, participants)` accepts `worker`, `worker_group`, `domain`,
  `federation`; **runs pending work first** (*"a barrier is a fence over
  pending work"*), then records a `BarrierRecord`.
* `event(name)` / `set_event` / `wait_event`: in a deterministic profile,
  `wait_event` drains pending work before waiting, because *"draining pending
  work is the only way an event can become set without another OS thread"*.

### 4.5 Combinators

`reduction(fn, items, initial)` folds **in index order** — deterministic by
construction. `scan(fn, items, inclusive)` produces the prefix list;
`inclusive=False` shifts and prepends `None`. `pipeline_stage(name, fn, items)`
spawns one task per item tagged with the stage; `pipeline(stages, items)`
chains them.

---

## 5. Channels and the dataflow runtime

### 5.1 `fabric.Channel`

A bounded, closable channel with a `Condition`. `capacity < 1` raises.

`send` blocks with `wait_for(len < capacity or closed)` and raises
`ChannelTimeoutError` on expiry, `ChannelClosedError` on a closed or cancelled
channel. `receive` symmetrically. `try_send`/`try_receive` are non-blocking
probes; `peek` returns `(ready, value)` without consuming.

`ChannelMetrics` records `sends`, `receives`, `blocked_sends`,
`blocked_receives`, `max_depth`, `mean_depth`, `bytes_sent`, `timeouts`,
`total_send_wait`, `total_recv_wait`.

`fabric.select(channels, timeout)` is **deterministic**: *"the lowest-index
ready channel always wins"*. It raises `ChannelClosedError` when every channel
is closed and empty, and `ChannelTimeoutError` on deadline.

### 5.2 The dataflow firing rule (LCTL 1.3.x §26)

`DataflowRuntime.evaluate(node)` produces a `FiringDecision` with **six**
recorded conditions; a node fires only when all six hold:

| # | Condition | Recorded field |
|---|---|---|
| 1 | every input channel has a token | `inputs_available` |
| 2 | ownership condition passes | `ownership_ok` |
| 3 | the resource claim is satisfiable on the owning worker | `resources_ok` |
| 4 | the failure policy admits the owner (alive, in scope) | `failure_policy_ok` |
| 5 | every output channel has backpressure headroom | `backpressure_ok` |
| 6 | the logical deadline has not passed | `deadline_ok` |

Condition 2 has three sub-rules:

* a `QuantumPayload` whose state is not `LIVE` refuses;
* **a quantum token cannot fan out to multiple outputs** — the no-cloning rule
  applied to dataflow;
* an optional `ownership_guard(tokens)` may refuse.

`decision` is `FIRE` or `HOLD`, and `reason` names the **first** failing
condition in the order above. Every decision is appended to
`self.decisions`, so a held node is never silently stalled.

`step()` orders nodes by `(-priority, deadline, node_id)` and fires; `run()`
iterates until no node fires or `max_steps` (default 64) is reached.

---

## 6. The BSP engine

`fabric.BSPEngine` (LCTL 1.4.x §9, 1.6.x §15).

### 6.1 The three normative phases

Each superstep records exactly `("local_compute", "communication", "barrier")`
and measures every phase:

| Phase | What happens | Measured |
|---|---|---|
| 1. local compute | each participant's function is called on its inbox, **in sorted worker order**; the inbox is then cleared | `per_worker_compute`, `local_compute_time` |
| 2. communication | each `(src, dst, payload)` is appended to the destination's inbox | `messages`, `bytes_moved`, `communication_time` |
| 3. barrier | hierarchical barrier at the requested level | `barrier_time`, `barriers` |

`idle_time` is the slack: `sum(slowest - d for d in durations)`.

**A `QSTATE` never enters a superstep message buffer**:

```python
if is_quantum(payload):
    raise OwnershipTransferError("BSP message payloads are classical; a QSTATE "
                                 "never enters a superstep message buffer")
```

An unknown destination worker raises.

### 6.2 Hierarchical barriers

`BARRIER_LEVELS = ("worker_group", "domain", "federation")`. A barrier at level
L executes barriers at **every level up to and including L**, in order, and for
each scope in sorted order, ticking the logical clock and logging
`BSP_BARRIER`. An unknown level raises `BarrierError`.

`totals()` aggregates supersteps, messages, bytes and all four times.

---

## 7. The asynchronous engine

`fabric.AsyncEngine` (LCTL 1.4.x §13).

* **`AsyncFuture`** — states `PENDING`/`DONE`/`FAILED`/`CANCELLED`; double
  resolution raises; `result()` raises `TaskFailedError` /
  `TaskCancelledError` / `FabricError` for the non-`DONE` states;
  `add_done_callback` fires immediately on an already-resolved future.
* **`drain()`** resolves pending futures in **deterministic dependency order**:
  repeated passes, skipping any future whose happens-before predecessors are
  unresolved, until no progress. Exceptions are captured into the future and
  emitted as a `FAILURE` event.
* **`TimerWheel`** — *"A logical-tick timer wheel. Deterministic: no wall clock
  is consulted."* Slots default to 64; a delay ≥ span raises; `advance(ticks)`
  fires due entries **sorted by `timer_id`**.
* **Control events** — `ASYNC_EVENT_KINDS = ("RETRY", "ROUTE_CHANGE",
  "FAILURE", "MEASUREMENT_CONTROL", "TIMER", "COMPLETION")`; an unknown kind
  raises. `measurement_control` logs only the **hash** of the outcome, not the
  outcome, so the event log carries no measurement values.
* **Happens-before graph** — `add_happens_before`, `happens_before` (DFS with
  sorted successors), `happens_before_graph()`.

---

## 8. Collectives

`fabric.Collectives(n_workers)` (LCTL 1.4.x §15-16, 1.6.x §13).

> **Every operation is realized by an explicit transfer schedule; the returned
> values are produced by those transfers, not by a shortcut. Bytes, messages
> and synchronization counts are measured from the same schedule.**

### 8.1 Algorithms

`fabric.COLLECTIVE_ALGORITHMS = ("tree", "ring", "recursive_doubling",
"pairwise", "linear")`. An unknown algorithm raises `CollectiveError`.

### 8.2 Operations and their schedules

| Op | `linear` | `pairwise` | `ring` | `tree` / `recursive_doubling` |
|---|---|---|---|---|
| `BROADCAST` | root sends to all in one step; sync 1 | one step per peer; sync n−1 | pass along the ring; sync n−1 | binomial broadcast (`_binomial_bcast`), doubling distance; sync = steps |
| `SCATTER` | root sends each chunk; sync 1 | as linear with per-peer steps; sync n−1 | carry the remaining block round the ring; sync n−1 | recursive halving (`_scatter_halving`); sync ⌈log₂n⌉ |
| `GATHER` | all send to root; sync 1 | per-peer steps; sync n−1 | accumulate round the ring; sync n−1 | recursive doubling (`_gather_doubling`); sync ⌈log₂n⌉ |
| `ALLGATHER` | gather + broadcast | full pairwise exchange; sync n−1 | n−1 rotation steps; sync n−1 | recursive doubling with a **deterministic linear remainder fill** for non-power-of-two sizes |
| `REDUCE` | all send to root, fold in rank order; sync 1 | per-peer steps; sync n−1 | fold along the ring; sync n−1 | recursive halving fold; sync ⌈log₂n⌉ |
| `ALLREDUCE` | reduce + broadcast, transfer steps offset by `r0.steps` | as linear | n−1 rotation steps folding pointwise | recursive doubling with a **deterministic remainder repair** when n is not a power of two |
| `SCAN` (inclusive prefix) | sequential prefix; sync n−1 | full pairwise then direct prefix; sync n−1 | as linear | recursive doubling by distance; tree: recursive prefix with a binomial broadcast of the left total |
| `ALLTOALL` | every pair in one step; sync 1 | n−1 rounds, one message per worker per round | n−1 rotation steps | recursive doubling by XOR distance plus a deterministic fill |

`REDUCE_OPS` supplies `sum`, `prod`, `max`, `min`, `concat`; a callable is
accepted directly; anything else raises.

Every operation returns a `CollectiveResult` with `op`, `algorithm`, `workers`,
`values`, `messages`, `bytes_moved`, `sync_count`, `steps` and the full
`transfers` list of `(step, src, dst, nbytes)`.

`run(op, data, ...)` dispatches by `lang.OPS_COLLECTIVE` name and raises for an
unknown collective.

### 8.3 Explainable algorithm selection

`fabric.select_algorithm(op, workers, message_size, topology)` — the first
matching rule wins and is reported by id:

| Rule | Condition | Choice | Stated reason |
|---|---|---|---|
| `R0` | `workers <= 1` | `linear` | a single worker performs no transfers |
| `R1` | `workers == 2` | `pairwise` | every schedule degenerates to one exchange |
| `R2` | topology in `{ring, line, chain}` | `ring` | only ring-order transfers are physically realizable |
| `R3` | `op == ALLTOALL` | `pairwise` | bounds concurrent link use at one message per worker per round |
| `R4` | `op in {ALLREDUCE, ALLGATHER}` and `message_size >= 65536` | `ring` | bandwidth-bound; each byte moves at most once per hop |
| `R5` | `op in {ALLREDUCE, ALLGATHER, SCAN}` and n is a power of two | `recursive_doubling` | log₂n rounds with no remainder repair |
| `R6` | `op in {BROADCAST, SCATTER, GATHER, REDUCE}` and `workers >= 4` | `tree` | reduces the root's serial fan-out from n−1 to log₂n |
| `R7` | otherwise | `linear` | the explicit default, always correct |

---

## 9. Four-level work stealing

`fabric.STEAL_LEVELS = ("worker_local", "worker_group", "domain",
"federation")` (LCTL 1.6.x §27).

### 9.1 Deterministic victim selection

> In a deterministic profile the victim order is a total order derived from
> **(queue depth desc, level order, worker id asc)** — never a random draw.

```python
def victims(self, thief, level):
    cands = [v for v in self._scope(thief, level) if v != thief]
    return sorted(cands, key=lambda v: (-self.depth(v), v))
```

### 9.2 The steal walk

`attempt_steal(thief, strict=False)`:

1. `pop_local` — **LIFO** on the owner's own deque: *"best locality, no
   contention"*.
2. Otherwise walk `worker_group` → `domain` → `federation`, and within each
   level walk victims in the deterministic order, scanning each deque **from
   the FIFO end**: *"oldest, coldest"*.

### 9.3 Refusals

| Condition | Token | Behaviour |
|---|---|---|
| `task.holds_unknown_quantum` | `QUANTUM_TASK_STEAL_REJECTED` | appended to `refusals`; raises `StealRejectedError` when `strict`, else skipped |
| `not task.movable` or wrong state | `TASK_NOT_MOVABLE` | appended to `refusals`, skipped |
| `MigrationRules.permits` fails | — | `StealRejectedError` naming the rule |
| task not queued on the named victim | — | `StealRejectedError` |

`steal_specific` applies the same rules and always raises rather than skipping.
Every successful steal appends a `StealRecord` (`steal_id`, `thief`, `victim`,
`task`, `level`, `reason`, `logical_time`) and logs an ownership `move` delta.

---

## 10. Load balancing

`fabric.LoadBalancer` — token `LOAD_BALANCE_LEDGER`.

**Static** (`static_assign`): weighted round-robin over declared `cpu_slots`,
tasks in `seq` order; each decision records the capacity share and every
alternative.

**Dynamic** (`dynamic_assign`): argmax of a **fully decomposed weighted score**:

```
+ w_throughput        * throughput
+ w_affinity          * affinity
- w_predicted_cost    * cost_estimate / max(1e-9, throughput)
- w_memory_pressure   * memory_pressure
- w_communication_cost* communication_cost
- w_queue_delay       * queue_delay
- w_failure_exposure  * failure_exposure
```

Default weights: throughput 1.0, predicted_cost 0.5, memory_pressure 0.8,
communication_cost 0.6, queue_delay 0.9, affinity 0.7, failure_exposure 1.2.

Ties are broken by worker id. After each assignment the chosen worker's
`queue_delay` is increased by `cost_estimate / throughput`, so the balancer is
self-damping. Dead workers are excluded; no live worker raises `FabricError`.

Every `PlacementDecision` records `score`, the full `components` breakdown, the
`rule` string and every alternative with its score. `ledger()` adds
`tasks_per_worker`, `imbalance_ratio = (max - min) / max(1, max)` and a
`ledger_hash`.

---

## 11. Straggler detection and mitigation

`fabric.StragglerEngine` (LCTL 1.4.x §25, 1.5.x §31).

### 11.1 Signals and thresholds

| Signal | Threshold | Default |
|---|---|---|
| throughput EMA vs peer mean | `ema < throughput_drop * peer_mean` | 0.5 |
| queue delay | `> queue_delay_limit` | 2.0 |
| task duration deviation | `> duration_sigma` σ from the global mean | 2.0 |
| heartbeat latency | `> heartbeat_limit` | 1.0 |

EMA smoothing `alpha` defaults to 0.3. Every verdict records **all five**
signal values and the list of reasons that fired.

### 11.2 The mitigation rule

| Case | Action |
|---|---|
| not detected | `NONE` |
| detected, classical | `SPECULATE` |
| detected, quantum | `REROUTE` |

> **Classical tasks may be speculatively duplicated (first valid completion
> wins, the loser is cancelled). Quantum work may only be rerouted or restarted
> from an admissible boundary: speculation would require cloning unknown
> quantum state and is refused.**

`speculate(task)` raises `SpeculationRejectedError` for any task holding a
quantum payload, naming the payloads. Otherwise it creates a replica with
`priority + 1` and `replica_of` set.

`first_valid_completion(original, replica)` accepts the first `DONE` and
cancels the loser with the reason *"speculative replica lost the race"*.

`quantum_remedy(task, route_alternatives, admissible_boundary)` returns exactly
one of `REROUTE`, `RESTART_FROM_BOUNDARY:<b>`, `ESCALATE` — **the only two
admissible quantum straggler remedies, plus escalation**.

---

## 12. Elasticity and `ADAPTATION_THRASH_DETECTED`

`fabric.ElasticWorkerGroup(group, min_size=1, max_size=16, window=10,
max_rate=0.4, strict=False)` (LCTL 1.6.x §19/§26).

```python
def adaptation_rate(self):
    recent = [a for a in self.adaptations if a.logical_time > clock.time - window]
    return len(recent) / float(window)
```

After every `grow`/`shrink`, `_guard` compares the rate to `max_rate`:

* over threshold ⇒ `thrash_detected = True`, the record's `token` becomes
  `ADAPTATION_THRASH_DETECTED`, a `reason` is written naming the measured rate,
  the threshold and the window, and an `ADAPTATION_THRASH` event is logged with
  `failure_delta`;
* **the guard reports; it raises `AdaptationThrashError` only when constructed
  with `strict=True`**.

`grow` stops at `max_size`, `shrink` at `min_size`, and `shrink` removes the
**lexicographically last** worker id, so shrinking is deterministic. Every
adaptation is recorded with `index`, `kind`, `delta`, `size_after`,
`logical_time`, `token`, `reason`.

---

## 13. Grain-size adaptation

`fabric.GrainSizeEngine(target_overhead_fraction=0.05, min_grain=1,
max_grain=4096)` (LCTL 1.6.x §7).

```
grain = ceil( overhead * (1 - target) / (target * item_cost) ),
        clamped to [min_grain, max_grain]
achieved = overhead / (overhead + grain * item_cost)
```

* `choose_grain` is **pure and deterministic**; `per_item_cost_s <= 0` raises.
* `measure_overhead(runtime, reps)` performs a **real local microbenchmark**
  (empty-body task dispatch), so its output is machine dependent.
* *"the value it produced is always reported alongside the chosen grain so the
  decision is auditable rather than asserted"* — `GrainDecision.measurement`
  carries `reps`, `elapsed_s`, `per_task_overhead_s`, `profile` and `method`.

---

## 14. Deadlock, livelock and starvation detection

`fabric.DeadlockDetector` (LCTL 1.4.x §27-28).

### 14.1 The merged graph

Three graphs in one directed graph whose nodes carry a kind:

| Edge builder | Meaning |
|---|---|
| `add_wait(task, blocker)` | task waits for another task |
| `add_resource_wait(task, resource)` | task waits for a resource |
| `add_resource_hold(holder, resource)` | resource is held by a task (edge resource → holder) |
| `add_future_dep(task, future_id)` | task waits for a future |

Node kinds: `task`, `resource`, `future`. A reported cycle therefore **names
both the blocked tasks and the resources they are blocked on**.

### 14.2 Detection

`_minimal_cycle()` runs a BFS from every node back to itself, in sorted node
order, and keeps the **shortest** cycle — deterministic tie-break by node
order.

| Finding | Token |
|---|---|
| a cycle | `DISTRIBUTED_DEADLOCK_DETECTED` |
| retries above `retry_storm_threshold` (default 8) | `RETRY_STORM_DETECTED` |
| unresolved futures | `UNSATISFIED_FUTURE_DETECTED` |
| waiting longer than `starvation_threshold` ticks (default 25) | `STARVATION_DETECTED` |

Priority order for the single reported `token`: deadlock → livelock →
unsatisfied future → starvation. `ok` is true only when **all four** are clear.
`assert_live()` raises `DeadlockDetectedError` naming the minimal cycle and the
resources involved.

---

## 15. Logical clocks

| Clock | Properties |
|---|---|
| `LamportClock` | scalar, **thread-safe** (`threading.Lock`), monotone; `tick`, `send`, `receive(remote) = max(t, remote) + 1` |
| `VectorClock` | per-node counters; `tick`, `merge` (pointwise max), `receive` (merge then tick), `happens_before`, `concurrent`, `copy` |

No wall clock participates in any logical ordering.

---

## 16. The append-only event log and replay

`fabric.EventLog` (LCTL 1.6.x §32).

### 16.1 The event record

`fabric.Event` — `event_id` (`E%06d`), `logical_time`, `worker`, `operation`,
`input_hash`, `output_hash`, `ownership_delta`, `resource_delta`,
`failure_delta`, `proof_ref`, `seq`.

Inputs and outputs are stored as **hashes**, not values
(`stable_hash` = first 32 hex of SHA-256 over canonical JSON), so the log is
bounded and carries no payload.

### 16.2 Append-only and sealing

`append` raises when the log is sealed: *"append-only logs are never
rewritten"*. `seal()` returns the hash and freezes the log.

### 16.3 Design hole H7 — what is hashed

> **Only the semantic content of an event is hashed. Wall-clock measurements
> are recorded separately (`EventLog.metrics`) and never influence the replay
> hash, so a deterministic profile reproduces `hash()` exactly.**

`canonical()` is `{"schema": "PA-LCTL/EVENTLOG/1", "name", "events": [...]}`
under sorted-key, `(",", ":")` JSON; `hash()` is its SHA-256.

### 16.4 Reconstruction and replay verification

`reconstruct()` rebuilds the final state **from the log alone**:

| Delta key | Effect |
|---|---|
| `ownership_delta["acquire"]` | `ownership[key] = event.worker` |
| `ownership_delta["move"]` | `ownership[key] = dst` for each `[key, src, dst]` |
| `ownership_delta["release"]` / `["consume"]` | remove the key |
| `resource_delta` | accumulate per key, rounded to 9 decimals |
| `failure_delta` | accumulate counts |

plus `operations`, `events_per_worker`, `event_count`.

`verify_replay(expected)` diffs the reconstruction against an expected state
with a **structural diff** (`_diff`) reporting `MISSING_KEY`, `UNEXPECTED_KEY`,
`LENGTH_MISMATCH` and `VALUE_MISMATCH` with full paths.

| Outcome | Token | Severity |
|---|---|---|
| identical | `DETERMINISTIC_REPLAY_PASS` | `PASS` |
| any mismatch | `REPLAY_MISMATCH` | **`CONFORMANCE_FAILURE`** |

> **A mismatch is a conformance failure, never a warning.**

`assert_replay` raises `ReplayMismatchError` with the first mismatch.

---

## 17. Fail-closed exception hierarchy

`FabricError` with sixteen subclasses:
`ProfileError`, `CloneAttemptError`, `OwnershipTransferError`,
`StealRejectedError`, `SpeculationRejectedError`, `ChannelClosedError`,
`ChannelTimeoutError`, `ResourceClaimError`, `DeadlockDetectedError`,
`ReplayMismatchError`, `AdaptationThrashError`, `TaskCancelledError`,
`TaskTimeoutError`, `TaskFailedError`, `BarrierError`, `CollectiveError`.

---

## 18. Implementation status

| Element | Status | Note |
|---|---|---|
| Four execution profiles | `OPERATIONAL` | real threads and real processes |
| Deterministic thread turnstile | `OPERATIONAL` | submission-order execution |
| Logical vs wall-clock timeouts (H8) | `OPERATIONAL` | both recorded |
| Federation object model | `OPERATIONAL` | four levels, four policy objects each |
| Task runtime, combinators, barriers | `OPERATIONAL` | |
| Quantum payload discipline | `OPERATIONAL` | copy/pickle/steal/speculate/BSP/fan-out all fail closed |
| Bounded channels with metrics | `OPERATIONAL` | |
| Dataflow six-condition firing rule | `OPERATIONAL` | every decision recorded with a reason |
| BSP supersteps and hierarchical barriers | `OPERATIONAL` | three phases always measured |
| Async engine, timer wheel, happens-before | `OPERATIONAL` | logical ticks only |
| Eight collectives × five algorithms | `OPERATIONAL` | explicit transfer schedules |
| Explainable algorithm selection (R0–R7) | `OPERATIONAL` | |
| Four-level work stealing | `OPERATIONAL` | deterministic victim order |
| Load balancing with score decomposition | `OPERATIONAL` | |
| Straggler detection and mitigation | `OPERATIONAL` | quantum speculation refused |
| Elasticity + `ADAPTATION_THRASH_DETECTED` | `OPERATIONAL` | reports by default, raises only in `strict` |
| Grain adaptation | `OPERATIONAL` | microbenchmark result always reported |
| Deadlock / livelock / starvation detection | `OPERATIONAL` | merged graph, minimal cycle |
| Event log and replay verification | `OPERATIONAL` | wall clock excluded from the hash (H7) |
| `multi_process_throughput` reproducibility | not claimed | `may_reorder = True`; the log is complete but not reproducible |
| Cross-machine federation | `BLOCKED` | `NETWORK=deny` |
| Real elastic provisioning | `SCAFFOLDED` | `ElasticWorkerGroup` adds and removes local `Worker` records only |
