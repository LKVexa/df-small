# PA-LCTL QCIR-P2 Execution-Plan IR Specification

Document: `PA_LCTL_QCIR_P2_SPEC.md`
Authority: `pacore.ledgers.QCIRP2`, `pacore.ledgers.QCIRP2_SECTIONS`,
`pacore.ledgers.REPLAY_MODES`, `pacore.ledgers.canonical_json`,
`pacore.ledgers.LedgerSet._emit` (construction site).

RFC 2119 keywords apply.

---

## 1. What QCIR-P2 is

QCIR-P2 (LCTL 1.3.x §17) is the **deterministic execution-plan intermediate
representation**: one JSON object that carries everything a runtime would need
to execute a verified PA-LCTL bundle, and nothing that would let it claim it
already had.

Two properties define it.

> **QCIR-P2 is a *plan*. It is never evidence that a physical target executed
> anything (LCTL 1.4.x §19).**

> **`as_dict` → `from_dict` is a total round trip: the reconstructed object
> hashes identically to the original.**

Schema identifier: `QCIRP2.SCHEMA = "PA-LCTL/QCIR-P2/1"`.

---

## 2. The fifteen sections

`ledgers.QCIRP2_SECTIONS` fixes the set and the order. Every section is a
constructor parameter of the `QCIRP2` dataclass, and `as_dict()` emits them in
this order after the `schema` key.

| # | Section | Type | Content as built by `LedgerSet._emit` |
|---|---|---|---|
| 1 | `ses_graph` | dict | `{"nodes": [SESNode.as_dict()...] in program order, "edges": [SESEdge.as_dict()...], "ses_hash": <sha256>}` |
| 2 | `partition_graph` | dict | `{"assignment": {node_id: partition}, "k": int, "cost_vector": {dim: rounded}, "tree": <recursive partition tree>}` |
| 3 | `placement_table` | dict | `{str(partition): target_node_id}` |
| 4 | `classical_route_table` | list[dict] | one entry per placed-target pair: `src`, `dst`, `status` (`PLANNED` \| `NO_CLASSICAL_ROUTE`), and the full `Route.as_dict()` when planned |
| 5 | `quantum_route_table` | list[dict] | the same shape from `QuantumRouter.route`, status `PLANNED` \| `NO_QUANTUM_ROUTE` |
| 6 | `temporal_schedule` | dict | `{"mode", "makespan", "ops": [ScheduledOp...], "layers", "blocked", "schedule_hash"}` |
| 7 | `resource_reservations` | dict | `{"occupancy_by_target", "targets", "link_reservations"}` |
| 8 | `ebit_lifecycle` | dict | `EbitLedger.as_dict()` — admitted states, the full transition table, every record with its provenance, state counts, events |
| 9 | `failure_domains` | dict | `{failure_domain: [node ids sorted]}` |
| 10 | `recovery_policy` | dict | `{"classes": lang.RECOVERY_CLASSES, "quantum_checkpoint": "REFUSED_FOR_UNKNOWN_QUANTUM_STATE", "classical_rollback": "ROLLBACK_CLASSICAL_ONLY", "supervision_levels": resilience.SUPERVISION_LEVELS}` |
| 11 | `calibration_epoch` | int | coerced with `int()`; `0` in this environment |
| 12 | `error_ledger_refs` | list[str] | sorted names of the typed error terms in `ERROR_LEDGER.json` |
| 13 | `proof_ledger_refs` | list[str] | sorted `proof_hash` values from `COMMUTATION_LEDGER.json` |
| 14 | `replay_mode` | str | one of `ledgers.REPLAY_MODES` |
| 15 | `deterministic_schedule_seed` | int | coerced with `int()`; the build seed |

### 2.1 Why these fifteen and not fewer

Each section answers a question a runtime would otherwise have to re-derive,
and re-derivation is where drift enters. Sections 1–3 fix *what and where*,
4–7 fix *how it moves and when*, 8–10 fix *what happens when it fails*, and
11–15 fix *the evidence and the reproducibility contract*. A conforming
implementation **SHALL** emit all fifteen; `from_dict` raises
`LedgerError(f"QCIR-P2 payload is missing sections {missing}")` for any absent
one.

---

## 3. Construction-time validation

`QCIRP2.__post_init__`:

```python
if self.replay_mode not in REPLAY_MODES:
    raise LedgerError(...)
self.calibration_epoch = int(self.calibration_epoch)
self.deterministic_schedule_seed = int(self.deterministic_schedule_seed)
self.error_ledger_refs  = sorted(str(r) for r in self.error_ledger_refs)
self.proof_ledger_refs  = sorted(str(r) for r in self.proof_ledger_refs)
```

Three normative effects:

1. An inadmissible `replay_mode` is a **construction failure**, not a runtime
   surprise.
2. The two integer fields are **normalized on construction**, so a caller
   passing `"0"` and a caller passing `0` produce the same hash.
3. The two reference lists are **sorted and stringified on construction**, so
   reference order can never affect the hash.

This is why the round trip is total: `from_dict` re-runs `__post_init__`, and
every normalization is idempotent.

---

## 4. Canonicalization (design hole H21)

The QUORUM documents require QCIR-P2 to be deterministic but do not state a
canonicalization. PA-LCTL fixes it:

```python
def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=_json_default)
```

| Rule | Value |
|---|---|
| Encoding | UTF-8 |
| Key order | sorted, recursively |
| Separators | `(",", ":")` — no whitespace |
| Non-ASCII | preserved (`ensure_ascii=False`) |
| Float formatting | Python `repr` semantics; **no** custom formatting |
| `set` / `frozenset` | serialized as `sorted(...)` |
| `tuple` | serialized as a list |
| objects with `as_dict()` | serialized via `as_dict()` |
| anything else | `str(o)` |

`QCIRP2.canonical()` is `canonical_json(self.as_dict())` and
`QCIRP2.hash()` is its SHA-256 hex digest.

> **Float caveat, stated.** Because floats are emitted with Python's default
> repr, an implementation in another language **SHALL** reproduce
> shortest-round-trip float formatting (IEEE-754 double, shortest decimal that
> round-trips) to obtain identical hashes. PA-LCTL mitigates the risk by
> rounding the values it controls before they enter the IR: schedule times to 6
> decimals (`ScheduledOp.as_dict`), makespan to 6, partition cost dimensions to
> 6, resource occupancy to 6.

---

## 5. Round-trip stability

```python
QCIRP2.from_dict(qcir.as_dict()).hash() == qcir.hash()
```

`from_dict` reconstructs by section name:

```python
missing = [s for s in QCIRP2_SECTIONS if s not in d]
if missing: raise LedgerError(...)
return cls(**{s: d[s] for s in QCIRP2_SECTIONS})
```

Note that the `schema` key present in `as_dict()` output is **ignored** on
input — `from_dict` selects only the fifteen section names. A conforming
reader **SHOULD** check `d["schema"] == "PA-LCTL/QCIR-P2/1"` before calling
`from_dict`, because `from_dict` will happily accept a payload with a foreign
schema key as long as the fifteen sections are present.

The round trip is asserted executably by `cli.cmd_selfcheck`:

```python
record("qcir_round_trip",
       ledgers.QCIRP2.from_dict(ls.qcir.as_dict()).hash() == ls.qcir.hash())
```

A failing round trip makes `selfcheck` exit non-zero.

---

## 6. Replay modes

`ledgers.REPLAY_MODES`:

| Mode | Meaning | Obligation on a consumer |
|---|---|---|
| `DETERMINISTIC_REPLAY` | Every decision in the plan is reproducible from the plan alone. The `deterministic_schedule_seed` fixes any seeded choice. | A replay **SHALL** produce identical artifacts: identical SES hash, schedule hash, and (given the same engine) identical `final_state_hash`. |
| `SEEDED_REPLAY` | Reproducible **given the recorded seed**, but the plan does not by itself determine every choice (e.g. a stochastic mitigation policy). | A replay **SHALL** use the recorded seed and **SHALL** state that reproduction is seed-conditional. |
| `NO_REPLAY` | The plan is not reproducible. | A consumer **SHALL NOT** present a re-execution as a replay of this plan. |

`LedgerSet.build` always constructs with `DETERMINISTIC_REPLAY`, because every
decision in the reference pipeline is deterministic: sorted tie-breaks
everywhere, seeded generators, no wall-clock input to any planning decision.

`fabric.ExecutionProfile` carries the runtime counterpart: three of the four
profiles are `deterministic`, and `multi_process_throughput` is not
(`may_reorder = True`). A plan executed under the throughput profile **SHALL
NOT** be reported as `DETERMINISTIC_REPLAY`, because the completion order is
whatever the process pool produces — the event log still records every task,
but it is not reproducible.

---

## 7. Relationship to the ledgers

QCIR-P2 is built **last** in `LedgerSet._emit`, because it references every
table above it. The dependency direction is normative: a ledger **SHALL NOT**
read the QCIR, and the QCIR **SHALL** be a projection of ledgers that already
exist.

| QCIR section | Source ledger(s) |
|---|---|
| `ses_graph` | `SES.json` |
| `partition_graph` | `PARTITION_LEDGER.json`, `HIERARCHICAL_PLACEMENT_LEDGER.json` |
| `placement_table` | `PLACEMENT_LEDGER.json` |
| `classical_route_table` | `ROUTING_LEDGER.json` |
| `quantum_route_table` | `QUANTUM_ROUTING_LEDGER.json` |
| `temporal_schedule` | `SCHEDULE_LEDGER.json` |
| `resource_reservations` | derived from `SCHEDULE_LEDGER.json` ops |
| `ebit_lifecycle` | `ENTANGLEMENT_INVENTORY_LEDGER.json` |
| `failure_domains` | derived from the SES |
| `recovery_policy` | `RECOVERY_LEDGER.json` vocabulary |
| `error_ledger_refs` | `ERROR_LEDGER.json` |
| `proof_ledger_refs` | `COMMUTATION_LEDGER.json` |

The QCIR hash then feeds **back** into `PROVENANCE_LEDGER.json` as
`qcir_hash`, which is emitted after the QCIR is built. That is the only
backward reference and it is a hash, not content.

`LedgerSet.write(dir)` emits `QCIR_P2.json` alongside the 34 ledger files, as
pretty JSON (indent 2, sorted keys).

---

## 8. What QCIR-P2 deliberately does not contain

| Absent | Why |
|---|---|
| Measurement outcomes | A plan predates execution. Outcomes live in the numerical result dict and the event log. |
| Any `physical_*` flag | Those live in `PROVENANCE_LEDGER.json`, and are always `False`. |
| A target authentication record | No authenticated target exists (`BACKEND=none`). |
| Wall-clock timings | Every time in the IR is in abstract units (`duration_model`, `makespan`, `occupancy`). Measured durations live in `EventLog.metrics` and are excluded from hashes by design hole H7. |
| An "optimal" flag | Optimality claims live with the artifact that can support them: `PartitionResult.optimality_claim` and `Schedule.optimality_claim`, each `PROVEN_OPTIMAL_BOUNDED_INSTANCE` only when the bounded exact oracle actually ran. |

---

## 9. Consumer obligations

A conforming consumer of a QCIR-P2 document:

1. **SHALL** verify `schema == "PA-LCTL/QCIR-P2/1"` before interpreting it.
2. **SHALL** treat every section as a plan and **SHALL NOT** report any of it
   as evidence of execution.
3. **SHALL** honour `replay_mode` per §6.
4. **SHALL** recompute `hash()` after any transformation and **SHALL NOT**
   present a transformed plan under the original hash.
5. **SHALL** refuse a payload missing any of the fifteen sections rather than
   defaulting it.
6. **SHOULD** cross-check `ses_graph.ses_hash` against an independently rebuilt
   SES when the source bundle is available; a mismatch means the plan does not
   describe that bundle.

---

## 10. Worked shape

```json
{
  "schema": "PA-LCTL/QCIR-P2/1",
  "calibration_epoch": 0,
  "classical_route_table": [
    {"src": "N0", "dst": "N1", "status": "PLANNED",
     "route_id": "R0-N0-N1", "hops": ["N0", "N1"], "links": ["C0"],
     "latency": 1.0, "bottleneck_bandwidth": 1000.0,
     "expected_transfer": 1.256, "kind": "classical",
     "route_hash": "a1b2c3d4e5f60718"}
  ],
  "deterministic_schedule_seed": 0,
  "ebit_lifecycle": {"schema": "PA-LCTL/ENTANGLEMENT_INVENTORY_LEDGER/1", "...": "..."},
  "error_ledger_refs": ["R021"],
  "failure_domains": {"N0": ["R010", "R012"], "N1": ["R020"]},
  "partition_graph": {"assignment": {"R010": 0, "R020": 1}, "k": 2,
                      "cost_vector": {"cut_edges": 0.0, "...": 0.0},
                      "tree": {"SITE_0": {"domains": {"D0": {"partitions": {"P0": {"nodes": ["R010"]}}}}}}},
  "placement_table": {"0": "N0", "1": "N1"},
  "proof_ledger_refs": ["3f2a...", "9c1b..."],
  "quantum_route_table": [{"src": "N0", "dst": "N1", "status": "PLANNED", "...": "..."}],
  "recovery_policy": {"classes": ["RETRY_SAFE", "..."],
                      "quantum_checkpoint": "REFUSED_FOR_UNKNOWN_QUANTUM_STATE",
                      "classical_rollback": "ROLLBACK_CLASSICAL_ONLY",
                      "supervision_levels": ["TASK", "WORKER", "DOMAIN", "FEDERATION"]},
  "replay_mode": "DETERMINISTIC_REPLAY",
  "resource_reservations": {"occupancy_by_target": {"N0": 6.0, "N1": 4.0},
                            "targets": ["N0", "N1"], "link_reservations": ["Q0"]},
  "ses_graph": {"nodes": ["..."], "edges": ["..."], "ses_hash": "ff2d..."},
  "temporal_schedule": {"mode": "CRITICAL_PATH", "makespan": 10.26,
                        "ops": ["..."], "layers": [["R000"], ["R010"]],
                        "blocked": [], "schedule_hash": "6172..."}
}
```

(Keys appear sorted because that is the canonical form.)

---

## 11. Implementation status

| Element | Status | Note |
|---|---|---|
| Fifteen-section IR | `OPERATIONAL` | closed set; missing sections raise |
| Construction-time normalization | `OPERATIONAL` | replay mode, ints, sorted refs |
| Canonicalization (H21) | `OPERATIONAL` | sorted keys, `(",", ":")`, UTF-8 |
| Round-trip hash stability | `VERIFIED` | asserted by `cli selfcheck` |
| `DETERMINISTIC_REPLAY` | `OPERATIONAL` | the only mode `LedgerSet` emits |
| `SEEDED_REPLAY`, `NO_REPLAY` | `SPECIFIED` | vocabulary present; no builder emits them |
| Cross-language float canonicalization | `IMPLEMENTED_PARTIAL` | relies on shortest-round-trip repr; mitigated by pre-rounding |
| `calibration_epoch` from a real device | `BLOCKED` | always `0`; no authenticated target |
| Schema check inside `from_dict` | `SPECIFIED` | the reader ignores `schema`; consumers must check it themselves |
