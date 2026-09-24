# PA-LCTL Semantic Execution Supergraph (SES) Specification

Document: `PA_LCTL_SES_SPEC.md`
Authority: `pacore.ses` — `SES_NODE_FIELDS`, `SESNode`, `SESEdge`, `SCOPES`,
`build_ses`, `SES.canonical`, `SES.hash`, `PartialOrder`, `analyze`,
`_exact_max_antichain`, `ConcurrencyRecord`, `admit_concurrency`,
`discover_opportunities`.

RFC 2119 keywords apply.

---

## 1. What the SES is

The SES (LCTL 1.3.x §6) supersedes the flat causal DAG of LCTL 1.2.x §7 while
preserving its typed edge-reason vocabulary. It is:

* **one node per row**, in source order (`SES.order`), with `node.id ==
  row.row_id`;
* **typed edges only** — every edge carries at least one reason from
  `lang.EDGE_REASONS`, and `build_ses` asserts membership before accumulating;
* **scoped** — eleven nested scopes group nodes by the kind of reasoning that
  applies to them;
* **deterministically serializable and hashable**, so an SES is an artifact
  that can be compared, transported and cited.

> **No edge is ever added without a reason.** — `ses.build_ses`

---

## 2. The SES node record — all 17 fields

`ses.SES_NODE_FIELDS` fixes the field set and its order. `SESNode` carries
three additional convenience fields (`lane`, `family`, `link`) that are part of
`as_dict()` and therefore of the hash, but are not part of the normative
17-field record.

| # | Field | Type | Derivation |
|---|---|---|---|
| 1 | `id` | str | `row.row_id` |
| 2 | `source_ref` | str | `f"{program.path}:{row.line_no}"` |
| 3 | `semantic_face` | str | `row.face` (design hole H1) |
| 4 | `operation` | str | `row.op` |
| 5 | `type` | str | `row.type_` |
| 6 | `owner` | str | `row.node`, or `"N_LOCAL"` when null |
| 7 | `reads` | tuple[str] | `sorted(set(row.reads()))` |
| 8 | `writes` | tuple[str] | `sorted(set(row.writes()))` |
| 9 | `quantum_lineage` | tuple[str] | sorted lineages of every read/written key that has an `OwnershipRecord` |
| 10 | `entanglement_set` | tuple[str] | sorted union of `entangled_with` over every read/written key |
| 11 | `memory_domain` | str | `row.domain`, or `"D_LOCAL"` when null |
| 12 | `device_class` | str | `RESOURCE device=`, default `"cpu"` |
| 13 | `duration_model` | float | `RESOURCE duration=`, default `ses.DURATION_UNITS[family]` |
| 14 | `error_model` | dict | `row.error_map` |
| 15 | `resource_claim` | dict | `row.resource_map` |
| 16 | `failure_domain` | str | `RESOURCE failure_domain=`, else `row.node`, else `"F_LOCAL"` |
| 17 | `proof_ref` | str | `row.proof` |

Additional serialized fields: `lane` (`row.lane`), `family` (`row.family`),
`link` (`row.link`).

Notes:

* `duration_model` is in **abstract time units**. *"The runtime never presents
  these as physical hardware timings."*
* `quantum_lineage` is what turns state identity into a graph property: two
  nodes touching the same lineage are joined by a weight-8.0 hyperedge in the
  partitioner.
* `source_ref` ties a node back to a text location, so a diagnostic on a node
  is always locatable.

---

## 3. The eleven nested scopes

`ses.SCOPES`:

```
program, frame, task, quantum_dependency, classical_dependency,
measurement_control, coupling, communication, topology,
resource_conflict, failure_domain
```

Every node joins `program`. Additional membership is decided by face and lane
(`build_ses`):

| Scope | Members |
|---|---|
| `program` | every node |
| `frame` | *(reserved; no node is routed here by `build_ses`)* |
| `task` | every node whose `LANE` is non-null |
| `quantum_dependency` | faces `EXEC`, `PREPARE`, `NOISE` |
| `classical_dependency` | *(reserved; no node is routed here by `build_ses`)* |
| `measurement_control` | faces `MEASURE`, `CONTROL` |
| `coupling` | *(reserved; coupling is computed as a weighted edge view, `planner.coupling_graph`)* |
| `communication` | faces `COMM` **and** `PROTOCOL` |
| `topology` | faces `TOPOLOGY`, `FEDERATION` |
| `resource_conflict` | face `RESOURCE` |
| `failure_domain` | face `RECOVERY` |

Scopes are serialized as sorted lists inside `SES.canonical()`, so they are
part of the hash. An implementation **SHALL** emit all eleven keys even when a
scope is empty — `build_ses` initializes `{s: [] for s in SCOPES}` and never
deletes a key.

> **Stated gap.** Three scopes (`frame`, `classical_dependency`, `coupling`)
> are declared, serialized and always empty in the reference implementation.
> They are `SCAFFOLDED`: the vocabulary is fixed so that the hash shape is
> stable, and no node is routed into them. `classical_dependency` reasoning is
> carried instead by `DATA_DEPENDENCY` and `CLASSICAL_CONTROL` edges;
> `coupling` reasoning by `planner.coupling_graph`; `frame` by the `LANE`
> column and the `task` scope.

---

## 4. Typed edge reasons

`lang.EDGE_REASONS` — fourteen values. An edge is a `(src, dst)` pair carrying
a **frozenset** of reasons, so one edge may be justified several ways.

| Reason | Emitted by `build_ses` when |
|---|---|
| `DATA_DEPENDENCY` | a read follows a write, or a write follows a read/write, on a key **without** a lineage (classical) |
| `QUANTUM_OWNERSHIP` | the same, on a key **with** a lineage (quantum) |
| `ENTANGLEMENT_DEPENDENCY` | the node's `entanglement_set` names a key whose last writer exists |
| `MEASUREMENT_DEPENDENCY` | a classical-control row consumes a value produced by a destructive measurement |
| `CLASSICAL_CONTROL` | the same edge, added alongside `MEASUREMENT_DEPENDENCY` |
| `RESOURCE_CONFLICT` | two consecutive rows name the same `LINK` |
| `COMMUNICATION` | the same edge, added alongside `RESOURCE_CONFLICT` |
| `BARRIER` | the row's `OP` is `BARRIER`, `FENCE` or `EPOCH`; an edge is added from **every** prior node |
| `USER_ORDER` | consecutive rows in one lane where the later row is `REGION_BEGIN`/`REGION_END` |
| `TOPOLOGY_CONSTRAINT` | *(reserved — not emitted by `build_ses`)* |
| `COUPLING` | *(reserved — weight 3.0 in `planner.coupling_graph` if present)* |
| `MEMORY_ORDER` | *(reserved)* |
| `TARGET_RESTRICTION` | *(reserved)* |
| `ERROR_BUDGET` | *(reserved)* |

Five reasons are declared in the vocabulary and never produced by the reference
builder. They are `SPECIFIED`: an implementation that derives, for example, a
topology constraint **SHALL** use `TOPOLOGY_CONSTRAINT` rather than invent a
new reason, and `build_ses`'s `assert reason in EDGE_REASON_SET` makes an
invented reason a hard failure.

### 4.1 Edge accumulation

Edges accumulate into `edge_acc: dict[(src, dst), set[str]]` and are emitted as
`SESEdge(src, dst, frozenset(reasons))` in **sorted (src, dst)** order. Self
edges and edges from unknown sources are silently dropped by `add_edge`
(`if src == dst or src not in nodes: return`) — an edge to a node that does not
exist yet is not an error, because rows are processed in source order and a
forward reference cannot be a dependency.

### 4.2 Last-writer / last-readers bookkeeping

For each row, after the edges are added:

```python
for k in writes:  last_writer[k] = node.id;  last_readers[k] = []
for k in reads:   last_readers.setdefault(k, []).append(node.id)
```

A write therefore clears the reader set, which is what makes the
write-after-read edges finite rather than cumulative.

---

## 5. Deterministic serialization and hashing

```python
def canonical(self) -> str:
    payload = {
        "schema": "PA-LCTL/SES/1",
        "program_seal": self.program_seal,
        "scopes": {k: sorted(v) for k, v in sorted(self.scopes.items())},
        "nodes": [self.nodes[n].as_dict() for n in self.order],
        "edges": sorted((e.as_dict() for e in self.edges),
                        key=lambda d: (d["src"], d["dst"])),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)

def hash(self) -> str:
    return sha256(self.canonical().encode("utf-8")).hexdigest()
```

Normative properties:

* **Nodes in program order**, edges sorted by `(src, dst)`, scopes sorted by
  name and by member.
* **JSON with sorted keys and `(",", ":")` separators**, UTF-8, non-ASCII
  preserved.
* The `program_seal` is embedded, so an SES hash identifies *both* the graph and
  the bundle it came from.
* Edge reasons are serialized as **sorted lists** (`SESEdge.as_dict`), so the
  frozenset's iteration order cannot leak into the hash.

**Stability requirement.** For a fixed `(Program, VerifyResult)` pair,
`build_ses` **SHALL** produce an identical hash on every invocation. This is
asserted by `smoke.py` (`g.hash() == build_ses(prog, vr).hash()`) and by
`cli.cmd_selfcheck` (`ses_hash_stable`).

---

## 6. Partial-order analysis

`ses.analyze(ses, exact_width_limit=22) -> PartialOrder`

### 6.1 Topological order

Kahn's algorithm with **deterministic tie-breaking by source order**:

```python
ready.sort(key=order.index)
n = ready.pop(0)
for m in sorted(succ[n], key=order.index): ...
```

If a cycle leaves nodes unprocessed, the remaining nodes are **appended in
source order** rather than looping — *"Cycle: report deterministically rather
than looping."* A conforming implementation **SHALL NOT** hang on a cyclic
graph; it **SHALL** produce a deterministic order and let the downstream
deadlock detector report the cycle.

### 6.2 Reachability and transitive reduction

Reachability is computed in reverse topological order:
`reach[n] = union over successors m of ({m} | reach[m])`.

The transitive reduction keeps edge `(u, v)` iff no other successor `m` of `u`
(with `m != v`) reaches `v`:

```python
reduction = [(e.src, e.dst) for e in ses.edges
             if not any(e.dst in reach[m] for m in succ[e.src] if m != e.dst)]
```

### 6.3 Work, span, critical path

| Quantity | Definition |
|---|---|
| **Work `W`** | `sum(node.duration_model)` over every node |
| **Finish times** | `finish[n] = max(finish[p] for p in pred[n], default 0) + dur[n]`, in topological order |
| **Span `D`** | `max(finish.values())` |
| **Critical path** | walk back from the node with maximum finish (ties broken by *earliest* source index, via `key=(finish[k], -order.index(k))`) through the recorded `parent` pointers, then reverse |
| **`Pmax`** | `W / D` when `D > 0`, else `0.0` |
| **Amdahl serial fraction** | `D / W` when `W > 0`, else `1.0` |

**`Pmax` is a logical upper bound on exposed concurrency, NOT a physically
realized speedup.** The note is emitted verbatim in
`PartialOrder.as_dict()["Pmax_note"]` and a conforming implementation
**SHALL** carry it wherever `Pmax` is reported.

### 6.4 ASAP levels and antichain layers

```python
level[n] = max(level[p] for p in pred[n], default -1) + 1
antichains = [[n for n in order if level[n] == L] for L in 0..max_level]
```

The layers are ASAP levels: every member of a layer is mutually incomparable,
so each layer *is* an antichain — but the widest layer is not necessarily the
widest antichain in the poset.

### 6.5 Exact maximum antichain via Dilworth

For graphs with `|V| <= exact_width_limit` (default **22**),
`_exact_max_antichain` computes the exact maximum antichain and sets
`width_exact = True`. Otherwise `width = max(len(a) for a in antichains)` and
`width_exact = False`.

The exact method is Dilworth's theorem applied to the **reachability**
(comparability) DAG:

> maximum antichain = `|V|` − (maximum matching in the bipartite comparability
> graph) = the minimum chain cover.

Implemented with Kuhn's augmenting-path algorithm, deterministic because the
adjacency lists are sorted by source index:

```python
adj = [[idx[m] for m in sorted(reach[u], key=lambda x: idx[x])] for u in order]
...
return n - matching
```

*"Hopcroft-Karp is unnecessary at this size; Kuhn's algorithm is deterministic
and sufficient."*

**Normative honesty rule.** `width_exact` **SHALL** be reported alongside
`width` everywhere the width appears (`PartialOrder.as_dict`,
`discover_opportunities`). A width computed by the layer heuristic **SHALL NOT**
be presented as the maximum antichain.

### 6.6 `PartialOrder.as_dict()`

```json
{"topological_order": [...],
 "work_W": 14.0, "span_D": 6.0, "Pmax_W_over_D": 2.333333,
 "Pmax_note": "logical upper bound on exposed concurrency; NOT a physically realized speedup",
 "critical_path": [...],
 "transitive_reduction_edges": [[src, dst], ...],
 "antichain_levels": [[...], ...],
 "max_antichain_width": 7, "width_is_exact": true,
 "amdahl_serial_fraction": 0.428571}
```

---

## 7. Concurrency admission

`ses.admit_concurrency(ses, po, commutation, topology=None)`

> **No pair is ever parallelized or serialized silently.**

### 7.1 Candidate selection

For every ordered pair `(a, b)` with `a` before `b` in source order:

1. **skip** if `b ∈ reach[a]` or `a ∈ reach[b]` — already causally ordered;
2. **skip** unless **both** faces are in `lang.EXECUTABLE_FACES`.

Every surviving pair produces exactly one `ConcurrencyRecord`.

### 7.2 The record — 14 fields

| Field | Derivation |
|---|---|
| `candidate_pair` | `(a, b)` |
| `ownership_disjoint` | `not (Wa∩Wb or Wa∩Rb or Wb∩Ra)` — note: reads-with-reads is **not** a conflict |
| `data_independent` | equals `ownership_disjoint` |
| `measurement_independent` | `not (both destructive and Ra∩Rb non-empty)` |
| `commutation_status` | `commutation.classify_pair(na, nb).status` |
| `coupling_status` | `DECOUPLED` if ownership-disjoint, else `STRONGLY_COUPLED` |
| `topology_status` | `OK` or `TOPOLOGY_CONFLICT` from `topology.can_coexist(owner_a, owner_b)` |
| `resource_status` | `LINK_CONFLICT` when both nodes name the same non-null `LINK`, else `OK` |
| `timing_status` | always `OK` (see §7.5) |
| `crosstalk_status` | `topology.crosstalk_status(na, nb)` — `OK` or `CROSSTALK_PAIR(x,y)` |
| `error_budget_status` | always `OK` (see §7.5) |
| `decision` | one of `lang.CONCURRENCY_DECISIONS` |
| `reason` | a human-readable justification, usually the commutation verdict's reason |
| `proof_ref` | the commutation `proof_id` |

### 7.3 The decision cascade

Evaluated in this exact order; the first match wins.

| Order | Condition | Decision | Reason |
|---|---|---|---|
| 1 | not `ownership_disjoint` | `SERIALIZE_OWNERSHIP` | the shared object keys |
| 2 | not `measurement_independent` | `SERIALIZE_MEASUREMENT` | `"shared measured subsystem"` |
| 3 | `commutation_status == NON_COMMUTING` | `SERIALIZE_COUPLING` | the commutation reason |
| 4 | `resource_status != OK` | `SERIALIZE_RESOURCE` | `"shared link <id>"` |
| 5 | `topology_status != OK` | `SERIALIZE_TOPOLOGY` | `"topology forbids co-execution"` |
| 6 | `crosstalk_status != OK` | `SERIALIZE_CROSSTALK` | the crosstalk pair |
| 7 | `commutation_status == COMMUTING_TARGET_CONDITIONAL` | `PARALLEL_TARGET_CONDITIONAL` | the commutation reason |
| 8 | otherwise | `PARALLEL_EXACT` | the commutation reason |

`assert decision in CONCURRENCY_DECISIONS` guards the cascade.

### 7.4 The twelve concurrency decisions

`lang.CONCURRENCY_DECISIONS` (LCTL 1.3.x §7, superseding the 1.2.x
`PARALLEL_*` tokens):

`PARALLEL_EXACT`, `PARALLEL_TARGET_CONDITIONAL`, `SERIALIZE_DATA`,
`SERIALIZE_OWNERSHIP`, `SERIALIZE_MEASUREMENT`, `SERIALIZE_COUPLING`,
`SERIALIZE_RESOURCE`, `SERIALIZE_TOPOLOGY`, `SERIALIZE_TIMING`,
`SERIALIZE_CROSSTALK`, `SERIALIZE_ERROR_BUDGET`, `REJECT_INVALID`.

Three of the twelve are `SPECIFIED` but never emitted by the reference
cascade: `SERIALIZE_DATA` (subsumed by `SERIALIZE_OWNERSHIP`, since
`data_independent` is defined equal to `ownership_disjoint`),
`SERIALIZE_TIMING` (`timing_status` is hard-coded `OK`) and
`SERIALIZE_ERROR_BUDGET` (`error_budget_status` is hard-coded `OK`).
`REJECT_INVALID` is likewise not emitted here — an invalid pair is rejected
earlier, by `lang.verify`.

### 7.5 Stated gaps in the cascade

| Field | Current value | Consequence |
|---|---|---|
| `timing_status` | literal `"OK"` | timing-driven serialization is not decided at admission; the scheduler's `timing` stage computes start/end times but does not feed back a `SERIALIZE_TIMING` verdict |
| `error_budget_status` | literal `"OK"` | the scheduler computes `summed_independent_p` but does not feed back a `SERIALIZE_ERROR_BUDGET` verdict |
| `UNRESOLVED` commutation | falls through to `PARALLEL_EXACT` | the *decision* is permissive; `commutation_status` records `UNRESOLVED` and `reason` records the rule's explanation, so the pair is auditable. A reviewer **SHALL** read `commutation_status`, not only `decision`. |

These are honest `IMPLEMENTED_PARTIAL` areas, not silent behaviours: every
field is emitted in `PARALLEL_OPPORTUNITY_LEDGER.json` →
`concurrency_records`.

---

## 8. Opportunity discovery

`ses.discover_opportunities(ses, po, records)` (LCTL 1.4.x §6):

```json
{"candidate_pairs": 32,
 "admitted_parallel_pairs": 31,
 "serialized_pairs": 1,
 "serialization_reasons": {"SERIALIZE_COUPLING": 1},
 "declared_families": {"INSTRUCTION_PARALLEL": ["R010", ...]},
 "max_antichain_width": 7, "width_is_exact": true,
 "critical_path_length": 4,
 "work_W": 14.0, "span_D": 6.0, "Pmax": 2.333333}
```

`admitted_parallel_pairs` counts decisions whose name starts with `PARALLEL`;
`serialized_pairs` counts those starting with `SERIALIZE`. `declared_families`
groups node ids by the `FAMILY` column, sorted.

---

## 9. Complexity and bounds

| Step | Cost | Bound |
|---|---|---|
| `build_ses` | O(rows × operands), plus O(rows²) when barriers are present | none |
| Reachability | O(V·E) worst case | none |
| Transitive reduction | O(E · out-degree) | none |
| Work/span/critical path | O(V + E) | none |
| Exact max antichain | O(V · E_comparability) via Kuhn | **`exact_width_limit = 22` nodes** |
| Concurrency admission | O(V²) pairs, each with one commutation classification | none; the matrix oracle inside is bounded to 6 qubits |

The exact-width limit and the oracle bound are the two places where the SES
layer trades completeness for termination, and both are reported alongside
their results (`width_is_exact`, `MATRIX_ORACLE_MAX_QUBITS`).

---

## 10. Worked example (from `smoke.py`)

A 14-row, two-lane Bell-pair program over two nodes yields:

```
SES nodes: 14  edges: 10
W = 14.0  D = 6.0  Pmax = 2.33  width = 7  width_exact = True
candidate_pairs = 32, admitted_parallel = 31, serialized = 1
serialization_reasons = {"SERIALIZE_COUPLING": 1}
```

The single serialized pair is `("R013", "R014")` — the `CX` on `q[0], q[1]` and
the `MEASURE` of `q[0]`. It is not causally ordered (the measure reads `q[0]`
and the `CX` writes `q[1]` and reads `q[0]`, so no write-after-write edge
exists), it is ownership-disjoint and measurement-independent, and it is
decided by commutation rule `R-MEAS-BARRIER`:

```
commutation_status = NON_COMMUTING
reason             = "measurement / classical-control boundary is not reorderable"
decision           = SERIALIZE_COUPLING          (cascade position 3)
```

The `R014` → `R015` measure/classical-control pair never becomes a candidate,
because `build_ses` already joined them with `MEASUREMENT_DEPENDENCY` and
`CLASSICAL_CONTROL` edges and the pair is therefore causally ordered.

---

## 11. Implementation status

| Element | Status | Note |
|---|---|---|
| 17-field node record | `OPERATIONAL` | |
| Typed edges, 9 of 14 reasons emitted | `OPERATIONAL` | 5 reasons `SPECIFIED` but unused |
| 11 scopes, 8 populated | `OPERATIONAL` | `frame`, `classical_dependency`, `coupling` are `SCAFFOLDED` |
| Deterministic canonical form and hash | `OPERATIONAL` | asserted by `smoke.py` and `cli selfcheck` |
| Kahn order with deterministic tie-break | `OPERATIONAL` | cycles reported, never looped |
| Transitive reduction | `OPERATIONAL` | |
| Work / span / critical path / `Pmax` | `OPERATIONAL` | always carries the "not a speedup" note |
| Exact maximum antichain (Dilworth/Kuhn) | `OPERATIONAL` | bounded to 22 nodes; `width_is_exact` always reported |
| Concurrency admission with recorded reasons | `OPERATIONAL` | 9 of 12 decisions reachable |
| `SERIALIZE_DATA` | `SPECIFIED` | subsumed by `SERIALIZE_OWNERSHIP` |
| `SERIALIZE_TIMING` | `SCAFFOLDED` | `timing_status` hard-coded `OK` |
| `SERIALIZE_ERROR_BUDGET` | `SCAFFOLDED` | `error_budget_status` hard-coded `OK` |
| Opportunity discovery | `OPERATIONAL` | |
