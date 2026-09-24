# PA-LCTL Routing Specification

Document: `PA_LCTL_ROUTING_SPEC.md`
Authority: `pacore.planner` — `Link`, `Node`, `Topology`, `t_message`,
`QuantumLinkCost`, `q_link_cost`, `Route`, `ClassicalRouter`, `QuantumRouter`,
`reference_topology`.

RFC 2119 keywords apply.

---

## 1. The governing rule

> **A route is a PLAN, not proof of physical hardware.**
> — `planner.QuantumRouter` docstring, LCTL 1.4.x §19

Both emitted routing ledgers carry the claim in their payload:

* `ROUTING_LEDGER.json` → *"a route is a plan over the declared topology; it is
  not evidence of a physical network"*
* `QUANTUM_ROUTING_LEDGER.json` → *"reference entanglement routing plan; not
  evidence of physical entanglement distribution"*
* `QuantumRouter.min_cost_flow` → *"reference allocation plan; not evidence of
  physical entanglement distribution"*

A conforming implementation **SHALL** attach an equivalent claim to every
routing artifact it emits, and **SHALL NOT** present a route as a measurement.

---

## 2. The topology model

### 2.1 `planner.Node`

| Field | Default | Role in routing |
|---|---|---|
| `node_id` | — | vertex identity |
| `kind` | `logical_node` | one of `planner.NODE_KINDS` |
| `domain`, `parent` | `D0`, `None` | hierarchy |
| `capacity` | 1 | `can_coexist` requires `> 1` for two ops on one node |
| `memory_bytes` | 512 MiB | placement gate |
| `supported_ops` | empty (unrestricted) | placement gate, `E-TARGET-001` |
| `trust` | `LOCAL_TRUSTED` | placement gate |
| `failure_domain` | `F0` | route avoidance |
| `calibration_epoch`, `calibration_valid_until` | 0, 2^30 | `calibration_valid`, `E-CAL-001` |
| `crosstalk_pairs` | empty | `crosstalk_status` |

### 2.2 `planner.Link`

| Field | Default | Meaning |
|---|---|---|
| `kind` | — | `"classical"` or `"quantum"` |
| `latency` | 1.0 | **α** in `Tmessage` |
| `inv_bandwidth` | 0.001 | **β**, time per byte |
| `capacity` | 1 | simultaneous-use capacity; congestion divisor and ebit-flow capacity |
| `fidelity` | 1.0 | quantum links only |
| `gen_rate` | 1.0 | ebit generation attempts per epoch |
| `success_prob` | 1.0 | per-attempt heralding success |
| `herald_latency` | 0.0 | heralding delay |
| `purification` | False | whether purification rounds are available |
| `decoherence_window` | `inf` | time-expanded feasibility bound |
| `failure_domain` | `F0` | route avoidance |

`Topology` builds a sorted adjacency map and exposes `neighbours(node, kind)`,
so classical and quantum routing traverse **disjoint subgraphs of the same
topology**. `Topology.canonical()`/`hash()` seal the topology; the hash is
recorded in the scheduler's `topology_binding` stage and in
`PLACEMENT_LEDGER.json`.

### 2.3 The reference topology

`reference_topology(n_nodes=4, domains=2, quantum=True)` builds a deterministic
topology used by conformance and examples:

* one `domain`-kind node per domain, capacity `1<<20`;
* `n_nodes` logical nodes, capacity 2, 256 MiB, `supported_ops = ALL_OPS`,
  `calibration_valid_until = 1000`;
* a classical link between consecutive nodes: latency **1.0** same-domain,
  **4.0** cross-domain, `inv_bandwidth` 0.001, capacity 2;
* a quantum link between consecutive nodes: latency 2.0/8.0, capacity 1,
  fidelity **0.98** same-domain / **0.94** cross-domain, `gen_rate` 4.0,
  `success_prob` 0.6, `herald_latency` 1.0, purification enabled,
  `decoherence_window` 50.0;
* a closing classical ring link (latency 5.0) when `n_nodes > 2`, **so that
  alternate routes exist** — without it `k_shortest` would degenerate.

---

## 3. The classical communication model

```
Tmessage = alpha + beta * n
```

`planner.t_message(link, payload_bytes) = link.latency + link.inv_bandwidth *
payload_bytes` (LCTL 1.2.x §18).

The docstring states the boundary: *"Never used for quantum links alone."*
An implementation **SHALL NOT** cost a quantum link with `t_message`.

The scheduler charges `conformance.MESSAGE_PAYLOAD_BYTES = 256` bytes per
planned cross-partition message, and the resource ledger tags the resulting
`bytes_moved` as an `analytical_estimate`.

---

## 4. Classical routing

`planner.ClassicalRouter(topology, hysteresis=0.15)`.

### 4.1 Shortest path

`_dijkstra(src, dst, payload, banned, avoid_domains, tag)` is a standard
priority-queue Dijkstra over the **classical** subgraph with edge weight

```
w = t_message(link, payload) * congestion
congestion = 1.0 + reservations[link] / max(link.capacity, 1)
```

Links in `banned` and links whose `failure_domain` is in `avoid_domains` are
skipped. Relaxation uses a strict `nd < dist[v] - 1e-12`, so equal-cost paths
never oscillate.

The returned `Route` carries `hops`, `links`, `latency` (the **sum of raw link
latencies**, not the congested weight), `bottleneck_bandwidth`
(`min(1/inv_bandwidth)` over the path), `expected_transfer` (the congested
Dijkstra distance) and `kind = "classical"`.

`Route.hash()` is `sha256(f"{src}|{dst}|{'>'.join(links)}")[:16]`, so a route
identity is its link sequence, not its cost.

### 4.2 k-shortest

`k_shortest(src, dst, k=3, payload=1024, avoid_domains=())` runs Dijkstra `k`
times; after each success it **bans the middle link of the found path**
(`r.links[len(r.links)//2]`) and re-runs. This is a deterministic
diversification heuristic, not Yen's algorithm: it guarantees distinct paths
when they exist but does **not** guarantee the true k shortest. Stated
explicitly here because the method name invites the stronger reading.

### 4.3 Congestion and reservations

`reserve(route)` increments and `release(route)` decrements a per-link counter,
floored at 0. The counter enters the weight as the congestion multiplier above.
Reservations are **advisory routing pressure**, not admission control: nothing
refuses a route because a link is reserved.

### 4.4 Route hysteresis (LCTL 1.5.x §15)

```python
best = min(cands, key=lambda r: (r.expected_transfer, r.route_id))
prev = self._last.get((src, dst))
if prev is not None and prev.links are all still in the topology:
    if best.expected_transfer > prev.expected_transfer * (1 - self.hysteresis):
        return prev            # keep the incumbent
self._last[(src, dst)] = best
return best
```

The incumbent route is retained unless a candidate is better by more than the
hysteresis margin (default **15 %**). This prevents route flapping under small
congestion changes. Tie-breaking within `min` is `(expected_transfer,
route_id)` — total and deterministic.

### 4.5 Multicast

`multicast_tree(src, dsts, payload)` unions the links of the per-destination
selected routes and reports `root`, sorted `destinations`, sorted `tree_links`,
every route, and `total_links`. It is a **union of unicast routes**, not a
Steiner tree; the field name `tree_links` describes what it contains, and no
minimality is claimed.

---

## 5. Why a quantum link is never a byte-transfer model

A classical link is fully described by two numbers, α and β, because a
classical message is a *copyable payload of known size*. None of that holds for
entanglement:

| Property | Classical channel | Quantum link |
|---|---|---|
| Payload | copyable bytes | an ebit — consumed on use, never copied |
| Success | assumed (retransmit on loss) | probabilistic per attempt (`success_prob`) |
| Confirmation | implicit | explicit heralding, with its own latency |
| Quality | binary (arrives or not) | continuous fidelity, degraded by every hop |
| Improvement | resend | purification, which **consumes additional pairs** |
| Lifetime | unbounded in the model | bounded by `decoherence_window` |
| Aggregation | bandwidth adds | fidelity **multiplies** along a path |

Modelling entanglement distribution as `α + β·n` would therefore erase the
consumption, the heralding, the fidelity and the deadline all at once. PA-LCTL
gives it a **separate cost object**.

### 5.1 `QuantumLinkCost` and `q_link_cost`

`q_link_cost(link, ebits=1, purify_rounds=0)`:

```python
p        = max(link.success_prob, 1e-9)
attempts = 1.0 / p                              # expected attempts
setup    = attempts / max(link.gen_rate, 1e-9)  # expected setup time
fid      = link.fidelity
pur      = 0.0
for _ in range(purify_rounds):
    if not link.purification: break             # refuse if unsupported
    fid = fid**2 / (fid**2 + (1 - fid)**2)      # recurrence
    pur += 2.0 * setup                          # two pairs consumed per round
```

Returned fields: `setup_time = setup * ebits`, `ebits_consumed`,
`fidelity`, `herald_delay = link.herald_latency`, `purification_cost = pur`,
`retry_probability = 1 - p`, and

```
expected_completion = setup*ebits + herald_latency + purification_cost + latency
```

Every one of the seven fields is a quantity a byte model cannot express. Note
that `purify_rounds` **breaks out** when the link does not support
purification — the fidelity is not improved on a link that cannot purify.

---

## 6. Quantum (entanglement) routing

`planner.QuantumRouter(topology, ebit_ledger=None)`.

### 6.1 `route(src, dst, epoch=0, fidelity_target=0.9, max_purify=2)`

A Dijkstra over the **quantum** subgraph where the relaxation is
**time-expanded and fidelity-aware**:

```
for each quantum neighbour link:
    if link.decoherence_window < d:        continue      # already too late
    for rounds in 0..max_purify:
        c  = q_link_cost(link, 1, rounds)
        nf = fid[u] * c.fidelity                          # fidelity MULTIPLIES
        nd = d + c.expected_completion
        if nd > link.decoherence_window:   continue       # would miss the window
        keep the first feasible, or a cheaper one that meets the target
        if nf >= fidelity_target:          break          # enough purification
    relax if nd < dist[v] - 1e-12
```

Three properties distinguish it from the classical router:

1. **Time expansion.** A link is unusable once the accumulated time exceeds its
   `decoherence_window`. Feasibility depends on *when* you arrive, not only on
   *where*.
2. **Fidelity accumulation.** `fid[v] = fid[u] * c.fidelity` — end-to-end
   fidelity is the product along the path, so a long path is penalized
   multiplicatively even when its time cost is linear.
3. **Purification as a per-hop decision.** The number of rounds is chosen per
   hop to reach `fidelity_target`, up to `max_purify`, and the total is
   accumulated into the route.

The returned `Route` carries `kind = "quantum"`, `fidelity = fid[dst]`,
`ebits = len(links)` (one ebit per hop), `expected_completion = dist[dst]`,
`purification_rounds` (the path total), and `bottleneck_bandwidth = 0.0` —
**bandwidth is meaningless for an entanglement route and is reported as zero
rather than as a plausible number**.

### 6.2 Min-cost-flow ebit allocation

`min_cost_flow(demands, epoch=0)` is a deterministic
successive-shortest-path allocation:

```
capacity = {link_id: link.capacity for quantum links}
for (src, dst, n) in demands:
    for _ in range(n):
        r = route(src, dst, epoch)
        if r is None or any hop has no residual capacity: break
        decrement every hop's capacity
        record the allocation
    if fewer than n were allocated: record unmet demand
```

The report contains `allocations`, `unmet_demand` (with `requested`,
`allocated` and the reason *"insufficient ebit link capacity or no feasible
time-expanded path"*), `residual_capacity` sorted by link, and the plan claim.

**Normative rule: unmet demand is reported honestly.** A conforming
implementation **SHALL NOT** silently truncate a demand, **SHALL NOT** report
partial allocation as success, and **SHALL** state the residual capacity so the
shortfall is attributable.

> **Stated simplification.** This is successive-shortest-path over residual
> *capacity*, not a true min-cost-flow with cost-scaling or negative-cycle
> cancellation. It is greedy in demand order and therefore order dependent —
> deterministic, but not guaranteed globally optimal. The name follows the
> LCTL vocabulary; the docstring says "deterministic successive-shortest-path
> allocation", and this specification says so too.

---

## 7. Routing inside the scheduler

`TemporalScheduler.schedule` stages `routing` and `communication_planning`:

```
for every SES edge whose endpoints are in different partitions
    placed on different targets:
        r = ClassicalRouter(topology).select(ta, tb, payload=256)
        if r is None:  blocked.append(f"ROUTE_MISSING:{src}->{dst}")
        else:          comm_plan.append({from, to, route, payload_bytes,
                                         t_message: r.expected_transfer})
```

The resulting `comm_delay[(p, n)]` is added to the predecessor's finish time
when computing a node's earliest start, so **communication cost enters the
makespan directly**. A missing route does not abort the schedule; it appends a
`ROUTE_MISSING` entry to `Schedule.blocked`, and the schedule is still emitted
so the failure is inspectable.

`ledgers.LedgerSet._route_tables` builds both tables over every pair of
**placed targets**, recording status `PLANNED`, `NO_CLASSICAL_ROUTE` or
`NO_QUANTUM_ROUTE`.

---

## 8. Routing-related diagnostics

| Code | Condition (admission pipeline) |
|---|---|
| `E-ROUTE-001` | no classical route exists between the required endpoints |
| `E-QROUTE-001` | no quantum route exists between the required endpoints |
| `E-CPATH-001` | a protocol needs a classical path that does not exist |
| `E-EBIT-001` | the requested ebits exceed the admitted link capacity |
| `E-EBIT-002` | the referenced ebit is expired at the claimed epoch |
| `E-LINK-001` | (verifier) a `LINK` is used before its `DECLARE_LINK` |
| `ROUTE_MISSING:<src>-><dst>` | (scheduler `blocked` entry) no route between two placed targets |

---

## 9. Determinism

| Choice | Tie-break |
|---|---|
| Dijkstra relaxation | strict `< dist - 1e-12`; adjacency pre-sorted by `Topology.__init__` |
| `k_shortest` diversification | ban the middle link, deterministic index |
| `select` | `min` by `(expected_transfer, route_id)` |
| Hysteresis | compare against `prev.expected_transfer * (1 - h)` |
| Quantum purification rounds | first feasible, or a strictly cheaper one meeting the target; `break` at the target |
| `min_cost_flow` | demands in caller order; hops in path order |
| `multicast_tree` | destinations sorted, links sorted |

---

## 10. Implementation status

| Element | Status | Note |
|---|---|---|
| Topology model with sealing | `OPERATIONAL` | canonical JSON + SHA-256 |
| `Tmessage = α + β·n` | `OPERATIONAL` | classical links only |
| Dijkstra with congestion and failure-domain avoidance | `OPERATIONAL` | |
| k-shortest paths | `IMPLEMENTED_PARTIAL` | middle-link banning; distinct paths, not provably the k shortest |
| Route hysteresis | `OPERATIONAL` | 15 % default margin |
| Reservations | `IMPLEMENTED` | advisory pressure, not admission control |
| Multicast | `IMPLEMENTED_PARTIAL` | union of unicast routes; no Steiner minimality claimed |
| `QuantumLinkCost` (7 fields) | `OPERATIONAL` | setup, herald, purification, retry, fidelity |
| Time-expanded, fidelity-aware quantum routing | `OPERATIONAL` | decoherence window enforced |
| Per-hop purification decision | `OPERATIONAL` | refuses on links without `purification` |
| Ebit allocation | `IMPLEMENTED_PARTIAL` | greedy successive-shortest-path; order dependent; unmet demand reported |
| Route as plan, not evidence | `OPERATIONAL` | claim carried in every artifact |
| Physical network routing | `BLOCKED` | `NETWORK=deny`; every link is a logical name |
| Measured link fidelity / latency | `BLOCKED` | no authenticated target; all values are declared |
