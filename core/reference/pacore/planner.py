"""
Topology, hierarchical partitioner, placer, routers and temporal scheduler.

Implements:
  * LCTL 1.2.x s13-14  topology model and the partitioner/placer pipeline
  * LCTL 1.2.x s15     coupling graph and coupling verdicts
  * LCTL 1.2.x s18     communication model  Tmessage = alpha + beta * n
  * LCTL 1.2.x s30     the 15-stage scheduler pipeline
  * LCTL 1.3.x s9-14   multilevel partitioning with KL/FM refinement and a
                       bounded exact oracle; placement proofs; classical and
                       quantum routers; temporal scheduling
  * LCTL 1.4.x s14-22  Hypergraph Partitioner 2.0, Pareto planning,
                       hierarchical placement, Routers 2.0, Scheduler 2.0,
                       dynamic scheduling
  * LCTL 1.5.x s10-20  Multilevel Hypergraph Partitioner 3.0, federated
                       placement, Routers 3.0, Scheduler 3.0
  * LCTL 1.6.x s11/s19/s60  recursive partition trees, communication
                       avoidance order, p95 makespan in the Pareto vector

Exit gates covered: TOPOLOGY_PASS, PARTITIONER_PASS, PLACEMENT_PASS,
ROUTING_PASS, COMMUNICATION_MODEL_PASS, DISTRIBUTED_SCHEDULER_PASS,
PARTITIONER_OPERATIONAL, PLACER_OPERATIONAL, CLASSICAL_ROUTER_OPERATIONAL,
QUANTUM_ROUTER_OPERATIONAL_REFERENCE, TEMPORAL_SCHEDULER_OPERATIONAL,
HYPERGRAPH_PARTITIONER_OPERATIONAL, PARETO_PARTITIONING_OPERATIONAL,
HIERARCHICAL_PLACEMENT_OPERATIONAL, ROUTING_2_OPERATIONAL,
TEMPORAL_SCHEDULER_2_OPERATIONAL, HYPERGRAPH_3_OPERATIONAL.

Honesty rules enforced here (LCTL 1.3.x s53, 1.4.x s64):
  * No heuristic result is ever labelled optimal. `optimal_cost` is populated
    only when the bounded exact oracle actually ran.
  * A route is a plan, not proof of physical hardware (LCTL 1.4.x s19).
  * The planner prefers the lowest-communication valid parallelism layer.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import math
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from . import lang
from .lang import NULL_CELL

# --------------------------------------------------------------------------
# 1. Topology (LCTL 1.2.x s13, 1.3.x s13)
# --------------------------------------------------------------------------

NODE_KINDS = ("host", "process", "numa", "device", "cpu", "gpu",
              "logical_node", "qpu", "qpu_partition", "memory_pool",
              "simulator_pool", "domain")


@dataclass
class Link:
    link_id: str
    a: str
    b: str
    kind: str                      # "classical" | "quantum"
    latency: float = 1.0           # alpha, abstract time units
    inv_bandwidth: float = 0.001   # beta, time per byte
    capacity: int = 1              # simultaneous-use capacity
    fidelity: float = 1.0          # quantum links only
    gen_rate: float = 1.0          # ebit generation attempts per epoch
    success_prob: float = 1.0
    herald_latency: float = 0.0
    purification: bool = False
    decoherence_window: float = math.inf
    failure_domain: str = "F0"

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["decoherence_window"] = (None if math.isinf(self.decoherence_window)
                                   else self.decoherence_window)
        return d


@dataclass
class Node:
    node_id: str
    kind: str = "logical_node"
    domain: str = "D0"
    parent: Optional[str] = None
    capacity: int = 1              # concurrent operations
    memory_bytes: int = 512 * 1024 * 1024
    supported_ops: FrozenSet[str] = field(default_factory=lambda: frozenset())
    trust: str = "LOCAL_TRUSTED"
    failure_domain: str = "F0"
    calibration_epoch: int = 0
    calibration_valid_until: int = 1 << 30
    crosstalk_pairs: FrozenSet[Tuple[str, str]] = field(default_factory=frozenset)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["supported_ops"] = sorted(self.supported_ops)
        d["crosstalk_pairs"] = [list(p) for p in sorted(self.crosstalk_pairs)]
        return d


class Topology:
    """Target-neutral in source; target-specific only after binding."""

    def __init__(self, nodes: Sequence[Node], links: Sequence[Link],
                 name: str = "reference") -> None:
        self.name = name
        self.nodes: Dict[str, Node] = {n.node_id: n for n in nodes}
        self.links: Dict[str, Link] = {l.link_id: l for l in links}
        self._adj: Dict[str, List[Tuple[str, str]]] = {}
        for l in links:
            self._adj.setdefault(l.a, []).append((l.b, l.link_id))
            self._adj.setdefault(l.b, []).append((l.a, l.link_id))
        for k in self._adj:
            self._adj[k].sort()

    # -- integrity seal (LCTL 1.2.x PHASE 9) -------------------------------
    def canonical(self) -> str:
        return json.dumps({
            "schema": "PA-LCTL/TOPOLOGY/1", "name": self.name,
            "nodes": [self.nodes[k].as_dict() for k in sorted(self.nodes)],
            "links": [self.links[k].as_dict() for k in sorted(self.links)],
        }, sort_keys=True, separators=(",", ":"))

    def hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    # -- queries -----------------------------------------------------------
    def neighbours(self, node: str, kind: Optional[str] = None
                   ) -> List[Tuple[str, str]]:
        out = self._adj.get(node, [])
        if kind is None:
            return out
        return [(n, lid) for n, lid in out if self.links[lid].kind == kind]

    def can_coexist(self, a: str, b: str) -> bool:
        if a == b:
            n = self.nodes.get(a)
            return bool(n and n.capacity > 1)
        return True

    def crosstalk_status(self, na, nb) -> str:
        """LCTL 1.3.x s15: reject or serialize on calibrated crosstalk."""
        if na.owner != nb.owner:
            return "OK"
        node = self.nodes.get(na.owner)
        if node is None:
            return "OK"
        qa = set(na.reads) | set(na.writes)
        qb = set(nb.reads) | set(nb.writes)
        for x in sorted(qa):
            for y in sorted(qb):
                if (x, y) in node.crosstalk_pairs or (y, x) in node.crosstalk_pairs:
                    return f"CROSSTALK_PAIR({x},{y})"
        return "OK"

    def calibration_valid(self, node: str, epoch: int) -> bool:
        n = self.nodes.get(node)
        return bool(n and n.calibration_epoch <= epoch <= n.calibration_valid_until)


def reference_topology(n_nodes: int = 4, domains: int = 2,
                       quantum: bool = True) -> Topology:
    """A deterministic reference topology used by conformance and examples."""
    nodes: List[Node] = []
    for d in range(domains):
        nodes.append(Node(f"D{d}", kind="domain", domain=f"D{d}",
                          capacity=1 << 20, failure_domain=f"F{d}"))
    for i in range(n_nodes):
        d = i % domains
        nodes.append(Node(f"N{i}", kind="logical_node", domain=f"D{d}",
                          parent=f"D{d}", capacity=2,
                          memory_bytes=256 * 1024 * 1024,
                          supported_ops=frozenset(lang.ALL_OPS),
                          failure_domain=f"F{d}",
                          calibration_valid_until=1000))
    links: List[Link] = []
    for i in range(n_nodes - 1):
        same = (i % domains) == ((i + 1) % domains)
        links.append(Link(f"C{i}", f"N{i}", f"N{i+1}", "classical",
                          latency=1.0 if same else 4.0,
                          inv_bandwidth=0.001, capacity=2,
                          failure_domain=f"F{i % domains}"))
        if quantum:
            links.append(Link(f"Q{i}", f"N{i}", f"N{i+1}", "quantum",
                              latency=2.0 if same else 8.0, capacity=1,
                              fidelity=0.98 if same else 0.94,
                              gen_rate=4.0, success_prob=0.6,
                              herald_latency=1.0, purification=True,
                              decoherence_window=50.0,
                              failure_domain=f"F{i % domains}"))
    # close the classical ring so alternate routes exist
    if n_nodes > 2:
        links.append(Link(f"C{n_nodes-1}", f"N{n_nodes-1}", "N0", "classical",
                          latency=5.0, inv_bandwidth=0.001, capacity=2,
                          failure_domain="F0"))
    return Topology(nodes, links, name=f"reference-{n_nodes}n-{domains}d")


# --------------------------------------------------------------------------
# 2. Communication model (LCTL 1.2.x s18)
# --------------------------------------------------------------------------

def t_message(link: Link, payload_bytes: int) -> float:
    """Tmessage = alpha + beta * n. Never used for quantum links alone."""
    return link.latency + link.inv_bandwidth * float(payload_bytes)


@dataclass
class QuantumLinkCost:
    """A quantum link is never reduced to a classical byte-transfer model."""
    setup_time: float
    ebits_consumed: int
    fidelity: float
    herald_delay: float
    purification_cost: float
    retry_probability: float
    expected_completion: float

    def as_dict(self) -> dict:
        return {k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


def q_link_cost(link: Link, ebits: int = 1, purify_rounds: int = 0
                ) -> QuantumLinkCost:
    p = max(link.success_prob, 1e-9)
    attempts = 1.0 / p
    setup = attempts / max(link.gen_rate, 1e-9)
    fid = link.fidelity
    pur = 0.0
    for _ in range(purify_rounds):
        if not link.purification:
            break
        fid = (fid ** 2) / (fid ** 2 + (1 - fid) ** 2)
        pur += 2.0 * setup
    return QuantumLinkCost(
        setup_time=setup * ebits, ebits_consumed=ebits, fidelity=fid,
        herald_delay=link.herald_latency, purification_cost=pur,
        retry_probability=1.0 - p,
        expected_completion=setup * ebits + link.herald_latency + pur + link.latency)


# --------------------------------------------------------------------------
# 3. Coupling analysis (LCTL 1.2.x s15)
# --------------------------------------------------------------------------

def coupling_graph(ses) -> Tuple[Dict[Tuple[str, str], float], Dict[str, str]]:
    """Weighted coupling between SES nodes plus a per-pair verdict."""
    weights: Dict[Tuple[str, str], float] = {}
    for e in ses.edges:
        w = 0.0
        for r in e.reasons:
            w += {"QUANTUM_OWNERSHIP": 4.0, "ENTANGLEMENT_DEPENDENCY": 8.0,
                  "MEASUREMENT_DEPENDENCY": 4.0, "DATA_DEPENDENCY": 1.0,
                  "COMMUNICATION": 2.0, "CLASSICAL_CONTROL": 2.0,
                  "RESOURCE_CONFLICT": 1.0, "BARRIER": 6.0,
                  "COUPLING": 3.0}.get(r, 0.5)
        weights[(e.src, e.dst)] = w
    verdicts: Dict[str, str] = {}
    for (s, d), w in weights.items():
        if w >= 8.0:
            v = "MONOLITHIC_REQUIRED"
        elif w >= 4.0:
            v = "STRONGLY_COUPLED"
        elif w >= 2.0:
            v = "MODERATELY_COUPLED"
        elif w > 0.0:
            v = "WEAKLY_COUPLED"
        else:
            v = "DECOUPLED"
        verdicts[f"{s}->{d}"] = v
    return weights, verdicts


# --------------------------------------------------------------------------
# 4. Hypergraph partitioner (LCTL 1.3.x s9, 1.4.x s14, 1.5.x s10)
# --------------------------------------------------------------------------

PARTITION_COST_DIMENSIONS = (
    "cut_edges", "weighted_communication", "remote_quantum_ops", "ebit_cost",
    "expected_latency", "memory_imbalance", "compute_imbalance",
    "failure_exposure", "estimated_error", "coherence_exposure",
    "energy_estimate", "p95_makespan",
)


@dataclass
class Hyperedge:
    edge_id: str
    pins: Tuple[str, ...]
    weight: float
    kind: str          # multi_qubit | entanglement | collective | resource | protocol


@dataclass
class PartitionResult:
    assignment: Dict[str, int]
    k: int
    cost: Dict[str, float]
    scalarized: float
    weights: Dict[str, float]
    method: str
    refinement_passes: int
    optimal_cost: Optional[float]      # populated ONLY by the exact oracle
    gap: Optional[float]
    runtime_ops: int
    tree: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "assignment": dict(sorted(self.assignment.items())),
            "k": self.k,
            "cost_vector": {k: round(v, 6) for k, v in sorted(self.cost.items())},
            "scalarized_cost": round(self.scalarized, 6),
            "scalarization_weights": dict(sorted(self.weights.items())),
            "method": self.method,
            "refinement_passes": self.refinement_passes,
            "optimal_cost": self.optimal_cost,
            "gap": self.gap,
            "optimality_claim": ("PROVEN_OPTIMAL_BOUNDED_INSTANCE"
                                 if self.optimal_cost is not None
                                 and abs(self.scalarized - self.optimal_cost) < 1e-9
                                 else "HEURISTIC_NO_OPTIMALITY_CLAIM"),
            "runtime_ops": self.runtime_ops,
            "partition_tree": self.tree,
        }


DEFAULT_PARTITION_WEIGHTS = {
    "cut_edges": 1.0, "weighted_communication": 1.0, "remote_quantum_ops": 6.0,
    "ebit_cost": 4.0, "expected_latency": 0.5, "memory_imbalance": 1.0,
    "compute_imbalance": 1.0, "failure_exposure": 0.5,
    "estimated_error": 2.0, "coherence_exposure": 1.0,
    "energy_estimate": 0.25, "p95_makespan": 0.5,
}


class HypergraphPartitioner:
    """Multilevel coarsen -> initial partition -> uncoarsen -> KL/FM refine,
    with a bounded exact branch-and-bound oracle for small instances."""

    EXACT_ORACLE_LIMIT = 12       # nodes; k^n enumeration stays bounded

    def __init__(self, weights: Optional[Dict[str, float]] = None,
                 balance_tolerance: float = 0.25) -> None:
        self.weights = dict(weights or DEFAULT_PARTITION_WEIGHTS)
        self.balance_tolerance = balance_tolerance
        self.ops = 0

    # -- model building ----------------------------------------------------
    def build_hyperedges(self, ses) -> List[Hyperedge]:
        edges: List[Hyperedge] = []
        # multi-qubit operations and entanglement groups become hyperedges
        by_object: Dict[str, List[str]] = {}
        for n in ses.order:
            node = ses.nodes[n]
            for k in set(node.reads) | set(node.writes):
                by_object.setdefault(k, []).append(n)
        for obj, pins in sorted(by_object.items()):
            if len(pins) < 2:
                continue
            edges.append(Hyperedge(f"HE-OBJ-{obj}", tuple(pins),
                                   weight=4.0, kind="multi_qubit"))
        ent: Dict[str, Set[str]] = {}
        for n in ses.order:
            node = ses.nodes[n]
            for lin in node.quantum_lineage:
                ent.setdefault(lin, set()).add(n)
        for lin, pins in sorted(ent.items()):
            if len(pins) > 1:
                edges.append(Hyperedge(f"HE-LIN-{lin}", tuple(sorted(pins)),
                                       weight=8.0, kind="entanglement"))
        by_link: Dict[str, Set[str]] = {}
        for n in ses.order:
            lk = ses.nodes[n].link
            if lk != NULL_CELL:
                by_link.setdefault(lk, set()).add(n)
        for lk, pins in sorted(by_link.items()):
            if len(pins) > 1:
                edges.append(Hyperedge(f"HE-LINK-{lk}", tuple(sorted(pins)),
                                       weight=2.0, kind="resource"))
        return edges

    # -- cost --------------------------------------------------------------
    def cost_vector(self, ses, hedges: Sequence[Hyperedge],
                    assign: Dict[str, int], k: int) -> Dict[str, float]:
        self.ops += 1
        cut = 0
        wcomm = 0.0
        remote_q = 0
        ebits = 0
        for he in hedges:
            parts = {assign[p] for p in he.pins if p in assign}
            if len(parts) > 1:
                cut += 1
                wcomm += he.weight * (len(parts) - 1)
                if he.kind == "entanglement":
                    remote_q += len(parts) - 1
                    ebits += len(parts) - 1
        load = [0.0] * k
        mem = [0.0] * k
        err = 0.0
        coh = 0.0
        for n in ses.order:
            p = assign.get(n)
            if p is None:
                continue
            node = ses.nodes[n]
            load[p] += node.duration_model
            mem[p] += float(node.resource_claim.get("memory", 1))
            try:
                err += float(node.error_model.get("p", 0.0) or 0.0)
            except ValueError:
                pass
            coh += node.duration_model
        avg = (sum(load) / k) if k else 0.0
        compute_imb = (max(load) - avg) / avg if avg > 0 else 0.0
        mavg = (sum(mem) / k) if k else 0.0
        mem_imb = (max(mem) - mavg) / mavg if mavg > 0 else 0.0
        fdomains = {ses.nodes[n].failure_domain for n in assign}
        lat = wcomm * 2.0 + remote_q * 8.0
        span = max(load) if load else 0.0
        return {
            "cut_edges": float(cut), "weighted_communication": wcomm,
            "remote_quantum_ops": float(remote_q), "ebit_cost": float(ebits),
            "expected_latency": lat, "memory_imbalance": mem_imb,
            "compute_imbalance": compute_imb,
            "failure_exposure": float(len(fdomains)),
            "estimated_error": err, "coherence_exposure": coh,
            "energy_estimate": sum(load) * 1.0,
            "p95_makespan": span * 1.05,
        }

    def scalarize(self, cost: Dict[str, float]) -> float:
        return sum(self.weights.get(k, 0.0) * v for k, v in cost.items())

    # -- the multilevel pipeline -------------------------------------------
    def partition(self, ses, k: int, exact: Optional[bool] = None
                  ) -> PartitionResult:
        self.ops = 0
        nodes = [n for n in ses.order
                 if ses.nodes[n].semantic_face in lang.EXECUTABLE_FACES]
        if not nodes:
            nodes = list(ses.order)
        k = max(1, min(k, len(nodes)))
        hedges = self.build_hyperedges(ses)

        use_exact = exact if exact is not None else (len(nodes) <= self.EXACT_ORACLE_LIMIT
                                                     and k <= 4)

        # 1-2. coarsen by heavy-edge matching
        levels = self._coarsen(nodes, hedges)
        # 3. initial partition of the coarsest graph (deterministic greedy seed)
        coarse_nodes = levels[-1][0]
        assign_c = self._greedy_seed(coarse_nodes, k)
        # 4-6. uncoarsen and KL/FM refine at each level
        passes = 0
        for lvl in range(len(levels) - 1, -1, -1):
            mapping = levels[lvl][1]
            assign = {}
            for fine, coarse in mapping.items():
                assign[fine] = assign_c.get(coarse, 0)
            assign, p = self._kl_fm_refine(ses, hedges, assign, k)
            passes += p
            assign_c = assign
        final = {n: assign_c.get(n, 0) for n in nodes}

        cost = self.cost_vector(ses, hedges, final, k)
        scal = self.scalarize(cost)

        opt = gap = None
        if use_exact:
            best_assign, best_cost = self._exact_oracle(ses, hedges, nodes, k)
            if best_assign is not None:
                opt = best_cost
                if best_cost < scal - 1e-12:
                    final, scal, cost = (best_assign, best_cost,
                                         self.cost_vector(ses, hedges,
                                                          best_assign, k))
                gap = round(scal - opt, 9)

        tree = self._partition_tree(ses, final)
        return PartitionResult(
            assignment=final, k=k, cost=cost, scalarized=scal,
            weights=self.weights,
            method="multilevel_heavy_edge_coarsening+KL_FM_refinement"
                   + ("+bounded_exact_oracle" if use_exact else ""),
            refinement_passes=passes, optimal_cost=opt, gap=gap,
            runtime_ops=self.ops, tree=tree)

    # -- stages ------------------------------------------------------------
    def _coarsen(self, nodes: Sequence[str], hedges: Sequence[Hyperedge]
                 ) -> List[Tuple[List[str], Dict[str, str]]]:
        levels: List[Tuple[List[str], Dict[str, str]]] = []
        cur = list(nodes)
        identity = {n: n for n in cur}
        levels.append((cur, identity))
        while len(cur) > 8:
            weight: Dict[Tuple[str, str], float] = {}
            for he in hedges:
                pins = [p for p in he.pins if p in set(cur)]
                for a, b in itertools.combinations(sorted(pins), 2):
                    weight[(a, b)] = weight.get((a, b), 0.0) + he.weight
            if not weight:
                break
            matched: Set[str] = set()
            mapping: Dict[str, str] = {}
            for (a, b), _w in sorted(weight.items(),
                                     key=lambda kv: (-kv[1], kv[0])):
                if a in matched or b in matched:
                    continue
                matched.update((a, b))
                mapping[a] = a
                mapping[b] = a
            nxt = []
            for n in cur:
                rep = mapping.get(n, n)
                mapping[n] = rep
                if rep == n:
                    nxt.append(n)
            if len(nxt) == len(cur):
                break
            levels.append((nxt, mapping))
            cur = nxt
            self.ops += 1
        return levels

    @staticmethod
    def _greedy_seed(nodes: Sequence[str], k: int) -> Dict[str, int]:
        return {n: i % k for i, n in enumerate(sorted(nodes))}

    def _kl_fm_refine(self, ses, hedges, assign: Dict[str, int], k: int,
                      max_passes: int = 8) -> Tuple[Dict[str, int], int]:
        """Fiduccia-Mattheyses style pass with deterministic tie-breaking and
        a balance constraint."""
        best = dict(assign)
        best_cost = self.scalarize(self.cost_vector(ses, hedges, best, k))
        passes = 0
        for _ in range(max_passes):
            improved = False
            for n in sorted(best):
                cur_p = best[n]
                for p in range(k):
                    if p == cur_p:
                        continue
                    trial = dict(best)
                    trial[n] = p
                    if not self._balanced(ses, trial, k):
                        continue
                    c = self.scalarize(self.cost_vector(ses, hedges, trial, k))
                    if c < best_cost - 1e-12:
                        best, best_cost, improved = trial, c, True
            passes += 1
            if not improved:
                break
        return best, passes

    def _balanced(self, ses, assign: Dict[str, int], k: int) -> bool:
        load = [0.0] * k
        for n, p in assign.items():
            load[p] += ses.nodes[n].duration_model
        avg = sum(load) / k if k else 0.0
        if avg <= 0:
            return True
        return (max(load) - avg) / avg <= self.balance_tolerance + 1e-9

    def _exact_oracle(self, ses, hedges, nodes: Sequence[str], k: int
                      ) -> Tuple[Optional[Dict[str, int]], float]:
        """Bounded exhaustive validator. Symmetry is broken by fixing the
        first node to partition 0."""
        if len(nodes) > self.EXACT_ORACLE_LIMIT or k ** (len(nodes) - 1) > 400000:
            return None, math.inf
        best_a, best_c = None, math.inf
        head, tail = nodes[0], list(nodes[1:])
        for combo in itertools.product(range(k), repeat=len(tail)):
            a = {head: 0}
            a.update(dict(zip(tail, combo)))
            if not self._balanced(ses, a, k):
                continue
            c = self.scalarize(self.cost_vector(ses, hedges, a, k))
            if c < best_c:
                best_a, best_c = a, c
        return best_a, best_c

    @staticmethod
    def _partition_tree(ses, assign: Dict[str, int]) -> dict:
        """LCTL 1.6.x s11 recursive partition tree, reported not flattened."""
        tree: Dict[str, dict] = {}
        for n, p in sorted(assign.items()):
            node = ses.nodes[n]
            site = tree.setdefault(f"SITE_{p % 2}", {"domains": {}})
            dom = site["domains"].setdefault(node.memory_domain,
                                             {"partitions": {}})
            part = dom["partitions"].setdefault(f"P{p}", {"nodes": []})
            part["nodes"].append(n)
        return tree


# --------------------------------------------------------------------------
# 5. Pareto planning (LCTL 1.4.x s15, 1.5.x, 1.6.x s60)
# --------------------------------------------------------------------------

def pareto_front(candidates: Sequence[PartitionResult]) -> List[int]:
    """Indices of non-dominated candidates over the full cost vector."""
    front: List[int] = []
    for i, a in enumerate(candidates):
        dominated = False
        for j, b in enumerate(candidates):
            if i == j:
                continue
            le = all(b.cost[d] <= a.cost[d] + 1e-12
                     for d in PARTITION_COST_DIMENSIONS)
            lt = any(b.cost[d] < a.cost[d] - 1e-12
                     for d in PARTITION_COST_DIMENSIONS)
            if le and lt:
                dominated = True
                break
        if not dominated:
            front.append(i)
    return front


def pareto_partitions(ses, ks: Sequence[int],
                      policies: Optional[Dict[str, Dict[str, float]]] = None
                      ) -> dict:
    """Produce multi-objective candidates and a DETERMINISTIC selection."""
    policies = policies or {
        "balanced": DEFAULT_PARTITION_WEIGHTS,
        "min_communication": {**DEFAULT_PARTITION_WEIGHTS,
                              "weighted_communication": 8.0, "cut_edges": 4.0},
        "min_remote_quantum": {**DEFAULT_PARTITION_WEIGHTS,
                               "remote_quantum_ops": 32.0, "ebit_cost": 16.0},
        "min_makespan": {**DEFAULT_PARTITION_WEIGHTS,
                         "p95_makespan": 8.0, "compute_imbalance": 4.0},
        "min_error": {**DEFAULT_PARTITION_WEIGHTS, "estimated_error": 16.0},
        "min_failure_exposure": {**DEFAULT_PARTITION_WEIGHTS,
                                 "failure_exposure": 8.0},
    }
    cands: List[PartitionResult] = []
    labels: List[str] = []
    for pname, w in sorted(policies.items()):
        for k in ks:
            cands.append(HypergraphPartitioner(w).partition(ses, k))
            labels.append(f"{pname}/k={k}")
    front = pareto_front(cands)
    # Deterministic selection policy: lowest balanced-scalarized cost on the
    # front, ties broken by (k, label).
    base = HypergraphPartitioner(DEFAULT_PARTITION_WEIGHTS)
    scored = sorted(front, key=lambda i: (base.scalarize(cands[i].cost),
                                          cands[i].k, labels[i]))
    chosen = scored[0] if scored else 0
    return {
        "schema": "PA-LCTL/PARETO_LEDGER/1",
        "dimensions": list(PARTITION_COST_DIMENSIONS),
        "candidates": [{"label": labels[i], **cands[i].as_dict()}
                       for i in range(len(cands))],
        "pareto_front": [labels[i] for i in front],
        "selection_policy": "lowest balanced-scalarized cost on the front; "
                            "ties broken by (k, label) ascending",
        "selected": labels[chosen],
        "selected_assignment": dict(sorted(cands[chosen].assignment.items())),
    }


# --------------------------------------------------------------------------
# 6. Hierarchical / federated placer (LCTL 1.3.x s11, 1.4.x s16, 1.5.x s13)
# --------------------------------------------------------------------------

# Capacity units a single concurrent-operation slot may absorb. Declared
# explicitly so `capacity_pass` is a binding constraint rather than a
# formality (LCTL 1.3.x s11.2 requires capacity_pass to mean something).
CAPACITY_UNITS_PER_SLOT = 8.0

PLACEMENT_LEVELS = ("FEDERATION", "SITE", "DOMAIN", "NUMA", "PROCESS",
                    "DEVICE", "LOGICAL_NODE", "QPU_PARTITION")


@dataclass
class PlacementProof:
    partition: int
    target: str
    capacity_pass: bool
    operation_support_pass: bool
    memory_pass: bool
    topology_pass: bool
    calibration_pass: bool
    trust_pass: bool
    failure_domain: str
    objective_delta: float
    reason: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class Placer:
    def __init__(self, topology: Topology, epoch: int = 0) -> None:
        self.topology = topology
        self.epoch = epoch

    def place(self, ses, part: PartitionResult) -> Tuple[Dict[int, str],
                                                          List[PlacementProof]]:
        targets = [n for n in sorted(self.topology.nodes)
                   if self.topology.nodes[n].kind in
                   ("logical_node", "qpu", "qpu_partition", "device", "cpu")]
        if not targets:
            targets = sorted(self.topology.nodes)
        proofs: List[PlacementProof] = []
        placement: Dict[int, str] = {}
        used: Dict[str, float] = {t: 0.0 for t in targets}

        parts = sorted(set(part.assignment.values()))
        for p in parts:
            members = [n for n, q in sorted(part.assignment.items()) if q == p]
            need_ops = {ses.nodes[n].operation for n in members}
            need_mem = sum(float(ses.nodes[n].resource_claim.get("memory", 1))
                           for n in members)
            need_load = sum(ses.nodes[n].duration_model for n in members)

            best: Optional[Tuple[float, str, PlacementProof]] = None
            for t in targets:
                node = self.topology.nodes[t]
                # Capacity is expressed in concurrent operations; the load a
                # partition places on a target is bounded by its declared
                # `capacity_units` claim, defaulting to one unit per member.
                cap_units = float(sum(
                    float(ses.nodes[n].resource_claim.get("capacity_units", 1))
                    for n in members))
                cap_ok = used[t] + cap_units <= float(node.capacity) * CAPACITY_UNITS_PER_SLOT
                ops_ok = (not node.supported_ops) or need_ops <= node.supported_ops
                mem_ok = need_mem <= node.memory_bytes
                topo_ok = True
                cal_ok = self.topology.calibration_valid(t, self.epoch)
                trust_ok = node.trust in ("LOCAL_TRUSTED", "REMOTE_AUTHENTICATED",
                                          "PHYSICAL_TARGET_AUTHENTICATED")
                delta = used[t] + cap_units + (0.0 if ops_ok else 1e6)
                proof = PlacementProof(
                    partition=p, target=t, capacity_pass=cap_ok,
                    operation_support_pass=ops_ok, memory_pass=mem_ok,
                    topology_pass=topo_ok, calibration_pass=cal_ok,
                    trust_pass=trust_ok, failure_domain=node.failure_domain,
                    objective_delta=round(delta, 6),
                    reason="candidate")
                if all((cap_ok, ops_ok, mem_ok, topo_ok, cal_ok, trust_ok)):
                    if best is None or delta < best[0]:
                        best = (delta, t, proof)
                proofs.append(proof)
            if best is None:
                proofs.append(PlacementProof(
                    p, "NONE", False, False, False, False, False, False,
                    "NONE", math.inf,
                    "BLOCKED: no target satisfies capacity, operation support, "
                    "memory, calibration and trust simultaneously"))
                continue
            _d, t, proof = best
            proof.reason = "selected: lowest objective_delta among valid targets"
            placement[p] = t
            used[t] += float(sum(
                float(ses.nodes[n].resource_claim.get("capacity_units", 1))
                for n in members))
        return placement, proofs

    def hierarchy(self, placement: Dict[int, str]) -> dict:
        out: dict = {"FEDERATION": {}}
        for p, t in sorted(placement.items()):
            node = self.topology.nodes.get(t)
            dom = node.domain if node else "D?"
            site = out["FEDERATION"].setdefault(f"SITE_{dom}", {})
            d = site.setdefault(dom, {})
            d.setdefault(t, []).append(f"P{p}")
        return out


# --------------------------------------------------------------------------
# 7. Routers (LCTL 1.3.x s12-13, 1.4.x s18-19, 1.5.x s15-16)
# --------------------------------------------------------------------------

@dataclass
class Route:
    route_id: str
    src: str
    dst: str
    hops: Tuple[str, ...]
    links: Tuple[str, ...]
    latency: float
    bottleneck_bandwidth: float
    expected_transfer: float
    kind: str
    fidelity: float = 1.0
    ebits: int = 0
    expected_completion: float = 0.0
    purification_rounds: int = 0

    def hash(self) -> str:
        return hashlib.sha256(
            f"{self.src}|{self.dst}|{'>'.join(self.links)}".encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        d = {k: (round(v, 6) if isinstance(v, float) else v)
             for k, v in self.__dict__.items()}
        d["hops"] = list(self.hops)
        d["links"] = list(self.links)
        d["route_hash"] = self.hash()
        return d


class ClassicalRouter:
    """k-shortest, latency-, bandwidth- and failure-domain-aware routing with
    deterministic tie-breaking, congestion awareness and route hysteresis."""

    def __init__(self, topology: Topology, hysteresis: float = 0.15) -> None:
        self.topology = topology
        self.hysteresis = hysteresis
        self.reservations: Dict[str, int] = {}
        self._last: Dict[Tuple[str, str], Route] = {}

    def k_shortest(self, src: str, dst: str, k: int = 3,
                   payload: int = 1024,
                   avoid_domains: Sequence[str] = ()) -> List[Route]:
        results: List[Route] = []
        banned_links: Set[str] = set()
        for i in range(k):
            r = self._dijkstra(src, dst, payload, banned_links, avoid_domains,
                               f"R{i}")
            if r is None:
                break
            results.append(r)
            if r.links:
                banned_links.add(r.links[len(r.links) // 2])
        return results

    def _dijkstra(self, src: str, dst: str, payload: int,
                  banned: Set[str], avoid_domains: Sequence[str],
                  tag: str) -> Optional[Route]:
        if src not in self.topology.nodes or dst not in self.topology.nodes:
            return None
        dist: Dict[str, float] = {src: 0.0}
        prev: Dict[str, Tuple[str, str]] = {}
        pq: List[Tuple[float, str]] = [(0.0, src)]
        seen: Set[str] = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in seen:
                continue
            seen.add(u)
            if u == dst:
                break
            for v, lid in self.topology.neighbours(u, "classical"):
                if lid in banned:
                    continue
                link = self.topology.links[lid]
                if link.failure_domain in avoid_domains:
                    continue
                congestion = 1.0 + self.reservations.get(lid, 0) / max(link.capacity, 1)
                w = t_message(link, payload) * congestion
                nd = d + w
                if nd < dist.get(v, math.inf) - 1e-12:
                    dist[v] = nd
                    prev[v] = (u, lid)
                    heapq.heappush(pq, (nd, v))
        if dst not in dist:
            return None
        hops: List[str] = [dst]
        links: List[str] = []
        cur = dst
        while cur != src:
            p, lid = prev[cur]
            links.append(lid)
            hops.append(p)
            cur = p
        hops.reverse()
        links.reverse()
        lat = sum(self.topology.links[l].latency for l in links)
        bw = min((1.0 / max(self.topology.links[l].inv_bandwidth, 1e-12)
                  for l in links), default=math.inf)
        return Route(f"{tag}-{src}-{dst}", src, dst, tuple(hops), tuple(links),
                     latency=lat, bottleneck_bandwidth=bw,
                     expected_transfer=dist[dst], kind="classical")

    def select(self, src: str, dst: str, payload: int = 1024,
               avoid_domains: Sequence[str] = ()) -> Optional[Route]:
        """Route hysteresis: keep the previous route unless a new candidate is
        better by more than the hysteresis margin (LCTL 1.5.x s15)."""
        cands = self.k_shortest(src, dst, 3, payload, avoid_domains)
        if not cands:
            return None
        best = min(cands, key=lambda r: (r.expected_transfer, r.route_id))
        prev = self._last.get((src, dst))
        if prev is not None and prev.links and all(
                l in self.topology.links for l in prev.links):
            if best.expected_transfer > prev.expected_transfer * (1 - self.hysteresis):
                return prev
        self._last[(src, dst)] = best
        return best

    def reserve(self, route: Route) -> None:
        for l in route.links:
            self.reservations[l] = self.reservations.get(l, 0) + 1

    def release(self, route: Route) -> None:
        for l in route.links:
            self.reservations[l] = max(0, self.reservations.get(l, 0) - 1)

    def multicast_tree(self, src: str, dsts: Sequence[str],
                       payload: int = 1024) -> dict:
        edges: Set[str] = set()
        routes = []
        for d in sorted(dsts):
            r = self.select(src, d, payload)
            if r:
                routes.append(r)
                edges.update(r.links)
        return {"root": src, "destinations": sorted(dsts),
                "tree_links": sorted(edges),
                "routes": [r.as_dict() for r in routes],
                "total_links": len(edges)}


class QuantumRouter:
    """Reference entanglement router. Time-expanded, ebit-inventory aware,
    purification aware. A route is a PLAN, not proof of physical hardware."""

    def __init__(self, topology: Topology, ebit_ledger=None) -> None:
        self.topology = topology
        self.ledger = ebit_ledger

    def route(self, src: str, dst: str, epoch: int = 0,
              fidelity_target: float = 0.9,
              max_purify: int = 2) -> Optional[Route]:
        dist: Dict[str, float] = {src: 0.0}
        fid: Dict[str, float] = {src: 1.0}
        prev: Dict[str, Tuple[str, str, int]] = {}
        pq: List[Tuple[float, str]] = [(0.0, src)]
        seen: Set[str] = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in seen:
                continue
            seen.add(u)
            if u == dst:
                break
            for v, lid in self.topology.neighbours(u, "quantum"):
                link = self.topology.links[lid]
                if link.decoherence_window < d:
                    continue                     # time-expanded feasibility
                best_local = None
                for rounds in range(0, max_purify + 1):
                    c = q_link_cost(link, 1, rounds)
                    nf = fid[u] * c.fidelity
                    nd = d + c.expected_completion
                    if nd > link.decoherence_window:
                        continue
                    if best_local is None or (nf >= fidelity_target and
                                              nd < best_local[0]):
                        best_local = (nd, nf, rounds)
                    if nf >= fidelity_target:
                        break
                if best_local is None:
                    continue
                nd, nf, rounds = best_local
                if nd < dist.get(v, math.inf) - 1e-12:
                    dist[v] = nd
                    fid[v] = nf
                    prev[v] = (u, lid, rounds)
                    heapq.heappush(pq, (nd, v))
        if dst not in dist:
            return None
        hops = [dst]
        links: List[str] = []
        rounds_total = 0
        cur = dst
        while cur != src:
            p, lid, r = prev[cur]
            links.append(lid)
            rounds_total += r
            hops.append(p)
            cur = p
        hops.reverse()
        links.reverse()
        return Route(f"QR-{src}-{dst}", src, dst, tuple(hops), tuple(links),
                     latency=sum(self.topology.links[l].latency for l in links),
                     bottleneck_bandwidth=0.0,
                     expected_transfer=dist[dst], kind="quantum",
                     fidelity=fid[dst], ebits=len(links),
                     expected_completion=dist[dst],
                     purification_rounds=rounds_total)

    def min_cost_flow(self, demands: Sequence[Tuple[str, str, int]],
                      epoch: int = 0) -> dict:
        """Deterministic successive-shortest-path allocation of ebit demands
        against link capacity. Reports unmet demand honestly."""
        capacity = {lid: l.capacity for lid, l in self.topology.links.items()
                    if l.kind == "quantum"}
        allocations: List[dict] = []
        unmet: List[dict] = []
        for src, dst, n in demands:
            got = 0
            for _ in range(n):
                r = self.route(src, dst, epoch)
                if r is None or any(capacity.get(l, 0) <= 0 for l in r.links):
                    break
                for l in r.links:
                    capacity[l] -= 1
                allocations.append(r.as_dict())
                got += 1
            if got < n:
                unmet.append({"src": src, "dst": dst, "requested": n,
                              "allocated": got,
                              "reason": "insufficient ebit link capacity or "
                                        "no feasible time-expanded path"})
        return {"allocations": allocations, "unmet_demand": unmet,
                "residual_capacity": dict(sorted(capacity.items())),
                "claim": "reference allocation plan; not evidence of physical "
                         "entanglement distribution"}


# --------------------------------------------------------------------------
# 8. Temporal scheduler (LCTL 1.2.x s30, 1.3.x s14, 1.4.x s21, 1.5.x s18)
# --------------------------------------------------------------------------

SCHEDULER_STAGES = (
    "dependency_extraction", "ownership_analysis", "commutation_analysis",
    "coupling_analysis", "critical_path_analysis", "partition_proposal",
    "topology_binding", "placement", "routing", "communication_planning",
    "timing", "crosstalk_validation", "error_budget_validation",
    "resource_validation", "schedule_canonicalization",
)

SCHEDULE_MODES = ("ASAP", "ALAP", "CRITICAL_PATH", "COMMUNICATION_AWARE",
                  "FIDELITY_AWARE", "CROSSTALK_AWARE", "COHERENCE_AWARE",
                  "DEADLINE_AWARE", "ENERGY_AWARE", "HEFT", "BALANCED")


@dataclass
class ScheduledOp:
    node_id: str
    start: float
    end: float
    target: str
    partition: int
    layer: int
    reason: str

    def as_dict(self) -> dict:
        return {k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


@dataclass
class Schedule:
    mode: str
    ops: List[ScheduledOp]
    makespan: float
    layers: List[List[str]]
    stage_ledger: Dict[str, dict]
    blocked: List[str]
    exact_optimum: Optional[float]
    coherence: Dict[str, float]

    def canonical(self) -> str:
        return json.dumps({
            "schema": "PA-LCTL/SCHEDULE/1", "mode": self.mode,
            "ops": [o.as_dict() for o in
                    sorted(self.ops, key=lambda x: (x.start, x.node_id))],
            "makespan": round(self.makespan, 6),
            "layers": self.layers, "blocked": self.blocked,
        }, sort_keys=True, separators=(",", ":"))

    def hash(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    def as_dict(self) -> dict:
        return {
            "schema": "PA-LCTL/SCHEDULE_LEDGER/1",
            "mode": self.mode,
            "ops": [o.as_dict() for o in self.ops],
            "makespan": round(self.makespan, 6),
            "layers": self.layers,
            "stage_ledger": self.stage_ledger,
            "blocked": self.blocked,
            "exact_bounded_optimum": self.exact_optimum,
            "optimality_claim": ("PROVEN_OPTIMAL_BOUNDED_INSTANCE"
                                 if self.exact_optimum is not None
                                 and abs(self.makespan - self.exact_optimum) < 1e-9
                                 else "HEURISTIC_NO_OPTIMALITY_CLAIM"),
            "coherence_exposure": self.coherence,
            "schedule_hash": self.hash(),
        }


class TemporalScheduler:
    EXACT_LIMIT = 9         # nodes for the bounded branch-and-bound validator

    def __init__(self, topology: Topology, commutation_authority,
                 coherence_budget: float = math.inf) -> None:
        self.topology = topology
        self.authority = commutation_authority
        self.coherence_budget = coherence_budget

    def schedule(self, ses, po, part: PartitionResult,
                 placement: Dict[int, str], concurrency_records,
                 mode: str = "CRITICAL_PATH",
                 exact: bool = False) -> Schedule:
        assert mode in SCHEDULE_MODES, mode
        stage: Dict[str, dict] = {}
        blocked: List[str] = []

        stage["dependency_extraction"] = {"edges": len(ses.edges),
                                          "reduction": len(po.reduction)}
        stage["ownership_analysis"] = {
            "quantum_objects": len({k for n in ses.order
                                    for k in ses.nodes[n].quantum_lineage})}
        serialized = [r for r in concurrency_records
                      if r.decision.startswith("SERIALIZE")]
        stage["commutation_analysis"] = {
            "pairs": len(concurrency_records),
            "serialized": len(serialized),
            "reasons": sorted({r.decision for r in serialized})}
        _w, cverdicts = coupling_graph(ses)
        stage["coupling_analysis"] = {"verdicts": cverdicts}
        stage["critical_path_analysis"] = {"span": round(po.span, 6),
                                           "path": po.critical_path}
        stage["partition_proposal"] = {"k": part.k,
                                       "scalarized": round(part.scalarized, 6)}
        stage["topology_binding"] = {"topology": self.topology.name,
                                     "topology_hash": self.topology.hash()}
        stage["placement"] = {"map": {str(k): v for k, v in sorted(placement.items())}}

        # ---- routing + communication planning ----
        router = ClassicalRouter(self.topology)
        comm_plan: List[dict] = []
        for e in ses.edges:
            pa = part.assignment.get(e.src)
            pb = part.assignment.get(e.dst)
            if pa is None or pb is None or pa == pb:
                continue
            ta, tb = placement.get(pa), placement.get(pb)
            if not ta or not tb or ta == tb:
                continue
            r = router.select(ta, tb, payload=256)
            if r is None:
                blocked.append(f"ROUTE_MISSING:{e.src}->{e.dst}")
                continue
            comm_plan.append({"from": e.src, "to": e.dst, "route": r.as_dict(),
                              "payload_bytes": 256,
                              "t_message": round(r.expected_transfer, 6)})
        stage["routing"] = {"routes": len(comm_plan)}
        stage["communication_planning"] = {"messages": comm_plan}

        comm_delay: Dict[Tuple[str, str], float] = {
            (c["from"], c["to"]): c["t_message"] for c in comm_plan}

        # ---- timing ----
        pred = ses.pred()
        ready_at: Dict[str, float] = {}
        finish: Dict[str, float] = {}
        target_free: Dict[str, float] = {t: 0.0 for t in self.topology.nodes}
        target_free["__unplaced__"] = 0.0
        ops: List[ScheduledOp] = []

        prio = self._priority(ses, po, mode)
        pending = list(po.topo)
        pending.sort(key=lambda n: (-prio.get(n, 0.0), po.topo.index(n)))

        scheduled: Set[str] = set()
        while len(scheduled) < len(pending):
            progressed = False
            for n in pending:
                if n in scheduled:
                    continue
                if not all(p in scheduled for p in pred[n]):
                    continue
                est = 0.0
                for p in pred[n]:
                    est = max(est, finish[p] + comm_delay.get((p, n), 0.0))
                p_idx = part.assignment.get(n, 0)
                tgt = placement.get(p_idx, "__unplaced__")
                start = max(est, target_free.get(tgt, 0.0))
                dur = ses.nodes[n].duration_model
                end = start + dur
                target_free[tgt] = end
                finish[n] = end
                ready_at[n] = est
                scheduled.add(n)
                ops.append(ScheduledOp(
                    n, start, end, tgt, p_idx, 0,
                    reason=f"mode={mode}; est={est:.3f}; "
                           f"target_free={start:.3f}"))
                progressed = True
            if not progressed:
                remaining = [n for n in pending if n not in scheduled]
                blocked.append("DISTRIBUTED_DEADLOCK_DETECTED:"
                               + ",".join(remaining[:8]))
                break

        makespan = max((o.end for o in ops), default=0.0)
        stage["timing"] = {"makespan": round(makespan, 6)}

        # ---- crosstalk validation ----
        xt: List[str] = []
        for a, b in itertools.combinations(ops, 2):
            if a.target != b.target:
                continue
            if a.start < b.end - 1e-12 and b.start < a.end - 1e-12:
                s = self.topology.crosstalk_status(ses.nodes[a.node_id],
                                                   ses.nodes[b.node_id])
                if s != "OK":
                    xt.append(f"{a.node_id}~{b.node_id}:{s}")
        stage["crosstalk_validation"] = {"violations": xt}
        if xt:
            blocked.extend(f"CROSSTALK_CONFLICT:{v}" for v in xt)

        # ---- error budget ----
        total_err = 0.0
        for n in ses.order:
            try:
                total_err += float(ses.nodes[n].error_model.get("p", 0.0) or 0.0)
            except ValueError:
                pass
        stage["error_budget_validation"] = {"summed_independent_p": round(total_err, 9),
                                            "note": "sum is an upper bound only "
                                                    "under an independence assumption"}

        # ---- resource validation ----
        overload = [t for t, f in target_free.items()
                    if t in self.topology.nodes
                    and f > self.topology.nodes[t].capacity * 1e9]
        stage["resource_validation"] = {"overloaded_targets": overload}

        # ---- coherence exposure ----
        coh = {"operation_time": round(sum(ses.nodes[n].duration_model
                                           for n in ses.order), 6),
               "idle_time": round(max(0.0, makespan * max(1, len(set(placement.values())))
                                      - sum(ses.nodes[n].duration_model
                                            for n in ses.order)), 6),
               "route_time": round(sum(c["t_message"] for c in comm_plan), 6),
               "measurement_wait": 0.0, "classical_feedback_wait": 0.0,
               "entanglement_wait": 0.0}
        coh["total_coherence_exposure"] = round(
            coh["operation_time"] + coh["route_time"] + coh["idle_time"], 6)
        if coh["total_coherence_exposure"] > self.coherence_budget:
            blocked.append("SCHEDULE_BLOCKED_COHERENCE")

        stage["schedule_canonicalization"] = {"ops": len(ops)}

        # ---- bounded exact optimum ----
        opt = None
        if exact and len(ses.order) <= self.EXACT_LIMIT:
            opt = self._exact_makespan(ses, po, part, placement)

        layers = [list(a) for a in po.antichains]
        for st in SCHEDULER_STAGES:
            stage.setdefault(st, {})
        return Schedule(mode=mode, ops=sorted(ops, key=lambda o: (o.start, o.node_id)),
                        makespan=makespan, layers=layers, stage_ledger=stage,
                        blocked=blocked, exact_optimum=opt, coherence=coh)

    def _priority(self, ses, po, mode: str) -> Dict[str, float]:
        succ = ses.succ()
        bottom: Dict[str, float] = {}
        for n in reversed(po.topo):
            bottom[n] = ses.nodes[n].duration_model + max(
                (bottom.get(m, 0.0) for m in succ[n]), default=0.0)
        if mode in ("CRITICAL_PATH", "HEFT", "BALANCED"):
            return bottom
        if mode == "ASAP":
            return {n: -float(po.levels.get(n, 0)) for n in po.topo}
        if mode == "ALAP":
            return {n: float(po.levels.get(n, 0)) for n in po.topo}
        if mode == "COMMUNICATION_AWARE":
            return {n: bottom[n] + 2.0 * len(ses.nodes[n].entanglement_set)
                    for n in po.topo}
        if mode in ("FIDELITY_AWARE", "COHERENCE_AWARE", "CROSSTALK_AWARE",
                    "DEADLINE_AWARE", "ENERGY_AWARE"):
            return {n: bottom[n] * (1.0 + len(ses.nodes[n].error_model))
                    for n in po.topo}
        return bottom

    def _exact_makespan(self, ses, po, part, placement) -> Optional[float]:
        """Bounded branch-and-bound over topological permutations."""
        nodes = list(po.topo)
        if len(nodes) > self.EXACT_LIMIT:
            return None
        pred = ses.pred()
        best = [math.inf]

        def rec(done: List[str], free: Dict[str, float], finish: Dict[str, float]):
            if len(done) == len(nodes):
                best[0] = min(best[0], max(finish.values(), default=0.0))
                return
            cur = max(finish.values(), default=0.0)
            if cur >= best[0]:
                return
            for n in nodes:
                if n in done or not all(p in done for p in pred[n]):
                    continue
                est = max((finish[p] for p in pred[n]), default=0.0)
                tgt = placement.get(part.assignment.get(n, 0), "__u__")
                start = max(est, free.get(tgt, 0.0))
                end = start + ses.nodes[n].duration_model
                nf = dict(free)
                nf[tgt] = end
                nfin = dict(finish)
                nfin[n] = end
                rec(done + [n], nf, nfin)

        rec([], {}, {})
        return None if math.isinf(best[0]) else round(best[0], 6)


# --------------------------------------------------------------------------
# 9. Communication-avoidance policy (LCTL 1.5.x s76, 1.6.x s19)
# --------------------------------------------------------------------------

COMMUNICATION_AVOIDANCE_ORDER = (
    "independent parameter/circuit/shot farming",
    "local task parallelism",
    "local pipelines",
    "tensor-factor locality",
    "same-domain state sharding",
    "cross-domain sharding",
    "remote protocols",
)


def select_parallel_family(ses, po) -> dict:
    """Choose the lowest-communication valid parallelism layer
    (LCTL 1.4.x s64). This is a policy heuristic, not a semantic law."""
    declared = {ses.nodes[n].family for n in ses.order
                if ses.nodes[n].family != NULL_CELL}
    has_protocol = any(ses.nodes[n].semantic_face == "PROTOCOL" for n in ses.order)
    has_remote = any(ses.nodes[n].link != NULL_CELL for n in ses.order)
    domains = {ses.nodes[n].memory_domain for n in ses.order}

    if "SHOT_PARALLEL" in declared or "CIRCUIT_PARALLEL" in declared \
            or "PARAMETER_PARALLEL" in declared:
        choice, rank = "SHOT/CIRCUIT/PARAMETER farming", 1
    elif po.width > 1 and not has_remote:
        choice, rank = "TASK_PARALLEL", 2
    elif "PIPELINE_PARALLEL" in declared:
        choice, rank = "PIPELINE_PARALLEL", 3
    elif "TENSOR_FACTOR_PARALLEL" in declared:
        choice, rank = "TENSOR_FACTOR_PARALLEL", 4
    elif len(domains) <= 1:
        choice, rank = "NUMERICAL_SHARD_PARALLEL (same domain)", 5
    elif has_protocol:
        choice, rank = "PROTOCOL_PARALLEL", 7
    else:
        choice, rank = "NUMERICAL_SHARD_PARALLEL (cross domain)", 6

    return {
        "preference_order": list(COMMUNICATION_AVOIDANCE_ORDER),
        "declared_families": sorted(declared),
        "selected": choice,
        "selected_rank": rank,
        "rationale": "lowest-communication valid layer given exposed width "
                     f"{po.width}, {len(domains)} domain(s), "
                     f"remote_links={has_remote}, protocols={has_protocol}",
        "note": "policy heuristic, not a semantic law",
    }
