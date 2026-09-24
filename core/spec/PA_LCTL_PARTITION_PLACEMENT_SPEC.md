# PA-LCTL Partitioning and Placement Specification

Document: `PA_LCTL_PARTITION_PLACEMENT_SPEC.md`
Authority: `pacore.planner` — `Hyperedge`, `HypergraphPartitioner`,
`PARTITION_COST_DIMENSIONS`, `DEFAULT_PARTITION_WEIGHTS`, `PartitionResult`,
`pareto_front`, `pareto_partitions`, `PLACEMENT_LEVELS`, `PlacementProof`,
`Placer`, `coupling_graph`, `select_parallel_family`,
`COMMUNICATION_AVOIDANCE_ORDER`.

RFC 2119 keywords apply.

---

## 1. The honesty rules enforced here

Stated in the `planner` module header and enforced structurally:

1. **No heuristic result is ever labelled optimal.** `optimal_cost` is
   populated *only* when the bounded exact oracle actually ran.
2. **A route is a plan, not proof of physical hardware.**
3. **The planner prefers the lowest-communication valid parallelism layer.**

---

## 2. Hyperedge construction

`HypergraphPartitioner.build_hyperedges(ses)` derives the hypergraph from the
SES. Nothing is declared by the program; every hyperedge is derived, so a
program cannot opt out of a coupling it created.

| Class | Edge id | Pins | Weight | Condition |
|---|---|---|---|---|
| `multi_qubit` | `HE-OBJ-<key>` | every node reading or writing the object key | **4.0** | at least 2 pins |
| `entanglement` | `HE-LIN-<lineage>` | every node whose `quantum_lineage` contains the lineage | **8.0** | more than 1 pin |
| `resource` | `HE-LINK-<link>` | every node naming that `LINK` | **2.0** | more than 1 pin |

Objects are iterated in sorted key order, lineages in sorted lineage order,
links in sorted link order, so the hyperedge list is deterministic.

The weight ordering is normative: **entanglement (8.0) > object sharing (4.0) >
link sharing (2.0)**. Splitting a quantum lineage across partitions is the most
expensive structural decision the partitioner can make, which is exactly the
intent — a cut lineage implies a remote quantum operation.

`protocol` and `collective` are named in the `Hyperedge.kind` domain comment
but are not produced by the reference builder; they are `SPECIFIED`.

---

## 3. The twelve-dimension cost vector

`planner.PARTITION_COST_DIMENSIONS` — the cost is a **vector**, not a scalar.
Scalarization exists only to break ties and to drive local search.

| # | Dimension | Computation (`cost_vector`) | Default weight |
|---|---|---|---|
| 1 | `cut_edges` | count of hyperedges spanning >1 partition | 1.0 |
| 2 | `weighted_communication` | `sum(he.weight * (parts - 1))` over cut hyperedges | 1.0 |
| 3 | `remote_quantum_ops` | `sum(parts - 1)` over cut **entanglement** hyperedges | **6.0** |
| 4 | `ebit_cost` | identical accumulation to `remote_quantum_ops` | **4.0** |
| 5 | `expected_latency` | `weighted_communication * 2.0 + remote_quantum_ops * 8.0` | 0.5 |
| 6 | `memory_imbalance` | `(max(mem) - mean(mem)) / mean(mem)`, 0 if mean ≤ 0 | 1.0 |
| 7 | `compute_imbalance` | `(max(load) - mean(load)) / mean(load)` over `duration_model` | 1.0 |
| 8 | `failure_exposure` | count of distinct `failure_domain` values over assigned nodes | 0.5 |
| 9 | `estimated_error` | `sum(float(error_model["p"]))` over assigned nodes | **2.0** |
| 10 | `coherence_exposure` | `sum(duration_model)` over assigned nodes | 1.0 |
| 11 | `energy_estimate` | `sum(load)` | 0.25 |
| 12 | `p95_makespan` | `max(load) * 1.05` (LCTL 1.6.x §60) | 0.5 |

`scalarize(cost) = sum(weights[k] * cost[k])`, with unknown dimensions
contributing 0.0.

**Normative reading.** Dimensions 3 and 4 dominate the default weights
(6.0 and 4.0) because a cut entanglement hyperedge means a remote quantum
operation, which is the most expensive thing in the model (family duration 8.0,
ebit consumption, fidelity loss). Dimension 9 is next (2.0). An implementation
**MAY** supply different weights, and **SHALL** report the weight vector it
used — `PartitionResult.as_dict()["scalarization_weights"]`.

> **Note.** `remote_quantum_ops` and `ebit_cost` are computed identically in
> `cost_vector` (both accumulate `len(parts) - 1` over cut entanglement
> hyperedges) and differ only in weight. They are kept as separate dimensions
> because the Pareto front is computed over the *vector*, and a policy that
> reweights one without the other is expressible.

---

## 4. The multilevel pipeline

`HypergraphPartitioner.partition(ses, k, exact=None)`.

### 4.0 Node selection and `k` clamping

```python
nodes = [n for n in ses.order if ses.nodes[n].semantic_face in EXECUTABLE_FACES]
if not nodes: nodes = list(ses.order)
k = max(1, min(k, len(nodes)))
```

Only executable-face nodes are partitioned; if a program has none, every node
is used, so the partitioner is total.

### 4.1 Coarsening — heavy-edge matching

```
while len(cur) > 8:
    weight[(a,b)] += he.weight   for every pin pair of every hyperedge
    for (a,b) in sorted(weight.items(), key=(-w, pair)):   # deterministic
        if a or b already matched: skip
        map both to a
    if no reduction: break
```

* Pair order is `(-weight, pair)` — heaviest first, lexicographic tie-break.
* The coarsening floor is **8 nodes**; below that, coarsening stops.
* Each level is recorded as `(nodes, mapping)`, so uncoarsening is exact.

### 4.2 Initial partition — deterministic greedy seed

```python
{n: i % k for i, n in enumerate(sorted(nodes))}
```

Round-robin over sorted names. Deliberately simple: refinement does the work,
and a deterministic seed is what makes the whole pipeline reproducible.

### 4.3 Uncoarsening and KL/FM refinement

For each level from coarsest to finest, the coarse assignment is projected onto
the fine nodes, then refined.

`_kl_fm_refine` is a Fiduccia-Mattheyses-style pass:

```
for pass in range(max_passes = 8):
    for n in sorted(best):                 # deterministic node order
        for p in range(k):                 # deterministic target order
            trial = best with n -> p
            if not balanced(trial): continue
            if scalarize(cost(trial)) < best_cost - 1e-12:
                accept
    if nothing improved: break
```

* **Deterministic tie-breaking**: nodes in sorted id order, partitions in index
  order, strict improvement threshold `1e-12`.
* **Balance constraint** (`_balanced`): `(max(load) - mean(load)) / mean(load)
  <= balance_tolerance + 1e-9`, with `balance_tolerance` defaulting to **0.25**.
  A zero or negative mean load is trivially balanced.
* The number of passes actually run is reported as `refinement_passes`.

### 4.4 The bounded exact oracle

```python
use_exact = exact if exact is not None else (len(nodes) <= EXACT_ORACLE_LIMIT
                                             and k <= 4)
EXACT_ORACLE_LIMIT = 12
```

`_exact_oracle` refuses when `len(nodes) > 12` or `k ** (len(nodes) - 1) >
400000`, returning `(None, inf)`. Otherwise it enumerates every assignment with
the **first node fixed to partition 0** (symmetry breaking), skips unbalanced
assignments, and keeps the minimum scalarized cost.

If the oracle ran and found a strictly better assignment, that assignment
replaces the heuristic one and the cost vector is recomputed. `gap` is
`round(scal - opt, 9)`.

### 4.5 The optimality claim

```python
"optimality_claim": ("PROVEN_OPTIMAL_BOUNDED_INSTANCE"
                     if self.optimal_cost is not None
                     and abs(self.scalarized - self.optimal_cost) < 1e-9
                     else "HEURISTIC_NO_OPTIMALITY_CLAIM")
```

**Normative rule.** `optimal_cost` **SHALL** be `None` unless the exact oracle
actually ran to completion on this instance. `PROVEN_OPTIMAL_BOUNDED_INSTANCE`
**SHALL NOT** be claimed on any other basis, and the claim is scoped to the
*bounded instance* — it says nothing about instances above the bound.

An implementation **SHALL NOT** report a heuristic result as optimal even when
it happens to be optimal, unless the oracle proved it.

### 4.6 The recursive partition tree (LCTL 1.6.x §11)

`_partition_tree` produces a nested structure rather than a flat map:

```json
{"SITE_0": {"domains": {"D0": {"partitions": {"P0": {"nodes": ["R010", "R012"]}}}}}}
```

Site is `f"SITE_{p % 2}"`, domain is the node's `memory_domain`, partition is
`f"P{p}"`. It is **reported, not flattened**: `HIERARCHICAL_PLACEMENT_LEDGER.json`
carries it whole.

> **Stated limitation.** The site level is derived as `partition % 2`, which is
> a placeholder grouping rather than a physical site topology. A real
> hyperfederated deployment would derive it from declared sites; no such
> declaration exists in the reference topology model. This is
> `IMPLEMENTED_PARTIAL`.

### 4.7 `PartitionResult`

| Field | Content |
|---|---|
| `assignment` | `{node_id: partition_index}` |
| `k` | the clamped partition count |
| `cost` | the 12-dimension vector |
| `scalarized` | the weighted sum |
| `weights` | the weight vector used |
| `method` | `"multilevel_heavy_edge_coarsening+KL_FM_refinement"` plus `"+bounded_exact_oracle"` when the oracle ran |
| `refinement_passes` | count |
| `optimal_cost` | `None` unless the oracle ran |
| `gap` | `scalarized - optimal_cost`, or `None` |
| `runtime_ops` | number of `cost_vector` evaluations — the honest work counter |
| `tree` | the recursive partition tree |

---

## 5. Pareto planning

### 5.1 Dominance

`pareto_front(candidates)` returns the indices of non-dominated candidates over
**the full twelve-dimension vector**:

```
b dominates a  iff  (all d: b[d] <= a[d] + 1e-12)  and  (some d: b[d] < a[d] - 1e-12)
```

The `1e-12` slack on both sides makes dominance robust to floating-point noise
without letting a genuinely worse candidate dominate.

### 5.2 Candidate generation

`pareto_partitions(ses, ks, policies=None)` runs one partition per
`(policy, k)` pair, with the default six policies:

| Policy | Reweighting relative to `DEFAULT_PARTITION_WEIGHTS` |
|---|---|
| `balanced` | (the defaults) |
| `min_communication` | `weighted_communication` 8.0, `cut_edges` 4.0 |
| `min_remote_quantum` | `remote_quantum_ops` 32.0, `ebit_cost` 16.0 |
| `min_makespan` | `p95_makespan` 8.0, `compute_imbalance` 4.0 |
| `min_error` | `estimated_error` 16.0 |
| `min_failure_exposure` | `failure_exposure` 8.0 |

Policies are iterated in **sorted name order**, and `ks` in the caller's order,
so the candidate list is deterministic.

### 5.3 The deterministic selection policy

> *"lowest balanced-scalarized cost on the front; ties broken by (k, label)
> ascending"*

```python
base   = HypergraphPartitioner(DEFAULT_PARTITION_WEIGHTS)
scored = sorted(front, key=lambda i: (base.scalarize(cands[i].cost),
                                      cands[i].k, labels[i]))
chosen = scored[0] if scored else 0
```

The rule is stated verbatim in the emitted ledger under `selection_policy`, so
a consumer never has to infer it. A conforming implementation **SHALL** state
its selection policy in the same field, and **SHALL** make it a total order.

`PARETO_LEDGER.json` carries: the dimension list, every candidate with its full
`PartitionResult.as_dict()`, the front (by label), the selection policy, the
selected label, and the selected assignment.

---

## 6. Placement

### 6.1 The eight placement levels

`planner.PLACEMENT_LEVELS`:

```
FEDERATION, SITE, DOMAIN, NUMA, PROCESS, DEVICE, LOGICAL_NODE, QPU_PARTITION
```

These are the levels a hierarchical placement *may* address.
`Placer.hierarchy()` currently materializes three of them —
`FEDERATION → SITE_<domain> → <domain> → <target> → [P<k>]`. The remaining
levels (`NUMA`, `PROCESS`, `DEVICE`, `QPU_PARTITION`) are `SPECIFIED`
vocabulary with no derivation in the reference topology model.

### 6.2 Target selection

`Placer.place(ses, part)` considers targets whose `kind` is one of
`logical_node`, `qpu`, `qpu_partition`, `device`, `cpu`; if none exist, every
topology node is a candidate.

For each partition, in sorted partition order:

```
members   = nodes assigned to this partition, in sorted id order
need_ops  = {operation of each member}
need_mem  = sum of RESOURCE memory= over members
need_load = sum of duration_model over members
```

### 6.3 The six placement gates

Every candidate target produces a `PlacementProof` with six boolean gates:

| Gate | Test |
|---|---|
| `capacity_pass` | `used[t] + need_load <= node.capacity * 1e9` |
| `operation_support_pass` | `not node.supported_ops` (empty = unrestricted) **or** `need_ops ⊆ node.supported_ops` |
| `memory_pass` | `need_mem <= node.memory_bytes` |
| `topology_pass` | literal `True` — see §6.6 |
| `calibration_pass` | `node.calibration_epoch <= epoch <= node.calibration_valid_until` |
| `trust_pass` | `node.trust in {LOCAL_TRUSTED, REMOTE_AUTHENTICATED, PHYSICAL_TARGET_AUTHENTICATED}` |

`objective_delta = used[t] + need_load + (0.0 if ops_ok else 1e6)` — the
1e6 penalty makes an operation-unsupported target unattractive even before the
gate rejects it.

### 6.4 Selection

A target is eligible only when **all six** gates pass. Among eligible targets
the one with the **lowest `objective_delta`** wins, first-found on ties (the
target list is sorted, so this is deterministic). The winning proof's `reason`
is rewritten to `"selected: lowest objective_delta among valid targets"`; every
other candidate keeps `reason = "candidate"`.

### 6.5 Placement proofs and blocking

Every candidate evaluation appends a `PlacementProof`, so the ledger records
*why each target was or was not chosen*, not merely the winner.

If no target satisfies all six gates, a synthetic blocking proof is appended:

```
PlacementProof(p, "NONE", False, False, False, False, False, False,
               "NONE", inf,
               "BLOCKED: no target satisfies capacity, operation support, "
               "memory, calibration and trust simultaneously")
```

and the partition is left **unplaced**. `PLACEMENT_LEDGER.json` reports
`unplaced_partitions` explicitly. `cli.Analysis.placement` raises
`CommandRejected` when *no* partition could be placed, attaching every proof.

### 6.6 Stated gaps

| Gate | Status | Note |
|---|---|---|
| `topology_pass` | `SCAFFOLDED` | hard-coded `True`; no adjacency or reachability constraint is applied at placement time. Topology *is* enforced elsewhere — `Topology.can_coexist` at concurrency admission, and routing failure at schedule time (`ROUTE_MISSING`). |
| `capacity_pass` | `IMPLEMENTED_PARTIAL` | the `* 1e9` scaling makes the capacity gate effectively non-binding for realistic loads; capacity is enforced in practice by `resource_validation` in the scheduler, which reports `overloaded_targets` against the same threshold. |
| `NUMA` / `PROCESS` / `DEVICE` / `QPU_PARTITION` levels | `SPECIFIED` | no derivation exists in the reference topology model |

---

## 7. Coupling analysis

`planner.coupling_graph(ses)` assigns each SES edge a weight by summing its
reasons:

| Reason | Weight | Reason | Weight |
|---|---|---|---|
| `ENTANGLEMENT_DEPENDENCY` | 8.0 | `COMMUNICATION` | 2.0 |
| `BARRIER` | 6.0 | `CLASSICAL_CONTROL` | 2.0 |
| `QUANTUM_OWNERSHIP` | 4.0 | `DATA_DEPENDENCY` | 1.0 |
| `MEASUREMENT_DEPENDENCY` | 4.0 | `RESOURCE_CONFLICT` | 1.0 |
| `COUPLING` | 3.0 | anything else | 0.5 |

and maps the total to a verdict from `lang.COUPLING_VERDICTS`:

| Weight | Verdict |
|---|---|
| ≥ 8.0 | `MONOLITHIC_REQUIRED` |
| ≥ 4.0 | `STRONGLY_COUPLED` |
| ≥ 2.0 | `MODERATELY_COUPLED` |
| > 0.0 | `WEAKLY_COUPLED` |
| = 0.0 | `DECOUPLED` |

The sixth verdict, `COUPLING_UNRESOLVED`, is `SPECIFIED` and not produced by
this function; the corresponding admission-pipeline diagnostic is
`E-COUPLE-001`.

Verdicts are emitted per edge (`"<src>-><dst>": verdict`) into the scheduler's
`coupling_analysis` stage ledger.

---

## 8. Parallel-family selection and communication avoidance

### 8.1 The communication-avoidance order (LCTL 1.5.x §76, 1.6.x §19)

`planner.COMMUNICATION_AVOIDANCE_ORDER`, cheapest first:

1. independent parameter/circuit/shot farming
2. local task parallelism
3. local pipelines
4. tensor-factor locality
5. same-domain state sharding
6. cross-domain sharding
7. remote protocols

### 8.2 `select_parallel_family(ses, po)`

The first matching rule wins:

| Rank | Condition | Selected |
|---|---|---|
| 1 | any of `SHOT_PARALLEL`, `CIRCUIT_PARALLEL`, `PARAMETER_PARALLEL` declared | `SHOT/CIRCUIT/PARAMETER farming` |
| 2 | `po.width > 1` and no row names a `LINK` | `TASK_PARALLEL` |
| 3 | `PIPELINE_PARALLEL` declared | `PIPELINE_PARALLEL` |
| 4 | `TENSOR_FACTOR_PARALLEL` declared | `TENSOR_FACTOR_PARALLEL` |
| 5 | one memory domain | `NUMERICAL_SHARD_PARALLEL (same domain)` |
| 7 | any `PROTOCOL`-face row | `PROTOCOL_PARALLEL` |
| 6 | otherwise | `NUMERICAL_SHARD_PARALLEL (cross domain)` |

The result carries `preference_order`, `declared_families`, `selected`,
`selected_rank`, a `rationale` naming the exposed width, the domain count, the
remote-link flag and the protocol flag, and — normatively — the note:

> **"policy heuristic, not a semantic law"**

A conforming implementation **SHALL** carry that note wherever the selection is
reported. The selection changes *which* parallelism is preferred; it never
changes what a program means.

Note the rank ordering: rule 7 (`PROTOCOL_PARALLEL`) is tested *before* the
fallback that yields rank 6, so the emitted `selected_rank` values are not
monotone in evaluation order. `selected_rank` is the position in the
**preference order**, not the position in the rule cascade.

---

## 9. Emitted ledgers

| File | Content |
|---|---|
| `PARTITION_LEDGER.json` | cost dimensions + full `PartitionResult.as_dict()` |
| `HYPERGRAPH_PARTITION_LEDGER.json` | every hyperedge with pins, weight and kind; hyperedge count; method; refinement passes; `runtime_ops`; optimality claim |
| `PARETO_LEDGER.json` | all candidates, the front, the selection policy, the selected assignment |
| `PLACEMENT_LEDGER.json` | placement map, **every** proof, unplaced partitions, topology hash |
| `HIERARCHICAL_PLACEMENT_LEDGER.json` | the placement hierarchy and the recursive partition tree |

---

## 10. Implementation status

| Element | Status | Note |
|---|---|---|
| Hyperedge derivation (3 classes) | `OPERATIONAL` | `protocol`/`collective` kinds `SPECIFIED` |
| Twelve-dimension cost vector | `OPERATIONAL` | `remote_quantum_ops` and `ebit_cost` are computed identically |
| Multilevel coarsening (heavy-edge) | `OPERATIONAL` | floor 8 nodes |
| KL/FM refinement with balance constraint | `OPERATIONAL` | deterministic; tolerance 0.25 |
| Bounded exact oracle | `OPERATIONAL` | ≤12 nodes and `k^(n-1) ≤ 400 000` |
| Optimality claim discipline | `OPERATIONAL` | claim only when the oracle ran |
| Pareto front over the full vector | `OPERATIONAL` | |
| Deterministic Pareto selection policy | `OPERATIONAL` | stated verbatim in the ledger |
| Recursive partition tree | `IMPLEMENTED_PARTIAL` | site level is `partition % 2`, a placeholder |
| Placement with six gates and proofs | `OPERATIONAL` | every candidate produces a proof |
| `topology_pass` gate | `SCAFFOLDED` | hard-coded `True` |
| `capacity_pass` gate | `IMPLEMENTED_PARTIAL` | `capacity * 1e9` is effectively non-binding |
| Hierarchical placement (3 of 8 levels) | `IMPLEMENTED_PARTIAL` | `NUMA`/`PROCESS`/`DEVICE`/`QPU_PARTITION` `SPECIFIED` |
| Coupling verdicts (5 of 6) | `OPERATIONAL` | `COUPLING_UNRESOLVED` `SPECIFIED` |
| Communication-avoidance family selection | `OPERATIONAL` | labelled a policy heuristic, not a law |
| Federated placement across real sites | `BLOCKED` | `NETWORK=deny`; sites are logical |
