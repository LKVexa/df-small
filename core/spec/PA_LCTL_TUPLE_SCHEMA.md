# PA-LCTL Tuple Schema

Document: `PA_LCTL_TUPLE_SCHEMA.md`
Authority: `pacore.lang.CORE_COLUMNS`, `pacore.lang.DISTRIBUTED_COLUMNS`,
`pacore.lang.COLUMN_MEANING`, `pacore.lang.Row`, `pacore.lang.verify`.

RFC 2119 keywords apply. The machine-generated companion table is
`COLUMN_DICTIONARY.md`; this document carries the normative prose, the domains
and the examples.

---

## 1. Shape

A PA-LCTL tuple is **22 cells** separated by BROKEN BAR `U+00A6`:

* **18 core columns** — `lang.CORE_COLUMNS`, from LCTL 1.1.x §4.3.
* **4 distributed-profile columns** — `lang.DISTRIBUTED_COLUMNS`, the 1.2+
  extension.

`lang.FULL_COLUMNS = CORE_COLUMNS + DISTRIBUTED_COLUMNS`, in that order. A
conforming writer **SHALL** emit the columns in this order; a conforming reader
**SHALL** accept any order declared by `#COLUMNS` and **SHALL** fill every
absent column with the null cell `-`.

```
ROW¦FACE¦LANE¦QSPACE¦OP¦OUT¦CTRL¦A¦B¦PARAM¦TYPE¦BASIS¦REGIME¦ASSUME¦ERROR¦RESOURCE¦CONF¦PROOF¦DOMAIN¦NODE¦LINK¦FAMILY
```

---

## 2. Design hole H1 — the 1.2+ extension and the FACE enumeration

This section is normative and is the reason this document exists.

### 2.1 The hole

The QUORUM LCTL documents state the canonical columned tuple schema once, in
1.1.x §4.3. Versions 1.2.x through 1.6.x **never restate it and never extend
it**, yet:

* 1.2.x §5 introduces distributed kernel objects (`node`, `link`,
  `classical_channel`, `quantum_link`, …) that a tuple must be able to
  *reference*, not merely *type*;
* 1.5.x §4–5 introduces execution domains and a 17-value parallel family model
  that a tuple must be able to *declare*;
* **1.3.x §6 requires a `semantic_face` field on every SES node** — but no
  document ever enumerates the admissible values of `FACE`.

So there are two holes: the tuple has no way to name a node, a domain, a link
or a parallel family; and the one enumeration the SES depends on is undefined.

### 2.2 How PA-LCTL fills it

Recorded in the `pacore.lang` module docstring as **H1**, and in
`PA_LCTL_NORMATIVE_SPEC.md` §9.

**H1(a) — the FACE enumeration.** `lang.FACES` is a closed, ordered,
17-value tuple. It is closed because `lang.FACE_SET` is the membership test in
`lang.verify` (`E-FACE-001`), and because `ses.build_ses` routes rows into
scopes by face. A face is *executable* iff it is in
`lang.EXECUTABLE_FACES` (7 of the 17); executability decides (i) which rows
`simulator.circuit_from_program` extracts, (ii) which nodes
`ses.admit_concurrency` considers as concurrency candidates, and (iii) which
nodes `planner.HypergraphPartitioner.partition` places.

**H1(b) — the distributed column extension.** Exactly four columns are added:
`DOMAIN`, `NODE`, `LINK`, `FAMILY`. The extension is **additive and optional**:
`lang.parse` fills any column the header omits with `NULL_CELL`, so a 1.1-core
bundle parses unchanged. No core column changes meaning.

### 2.3 Why deterministically

Both halves of H1 are fixed rather than negotiable so that two artifacts are
stable:

* the **SES hash** — `SESNode.semantic_face` is part of `SES.canonical()`, so
  an implementation that invented its own faces would produce a different hash
  for the same program;
* the **bundle seal** — `Program.canonical_text()` renders `self.columns`, so
  a different column set is a different bundle identity.

A conforming implementation **SHALL NOT** add, remove, rename or reorder either
enumeration. An implementation that needs another face **SHALL** treat that as
a language revision, not a local extension.

---

## 3. The 17 FACE values

`lang.FACES`. Column: is the face executable (`lang.EXECUTABLE_FACES`)? SES
scope: which of the 11 `ses.SCOPES` the row joins in addition to `program`.

| # | FACE | Executable | SES scope joined | Meaning |
|---|---|---|---|---|
| 1 | `EXEC` | yes | `quantum_dependency` | executable quantum or classical operation |
| 2 | `PREPARE` | yes | `quantum_dependency` | state preparation / reset |
| 3 | `MEASURE` | yes | `measurement_control` | measurement, sampling, expectation |
| 4 | `NOISE` | yes | `quantum_dependency` | channel / noise / decoherence model row |
| 5 | `CONTROL` | yes | `measurement_control` | classical control flow, feedback, binding |
| 6 | `COMM` | yes | `communication` | classical communication |
| 7 | `PROTOCOL` | yes | `communication` | distributed quantum protocol step |
| 8 | `REGION` | no | (lane → `task`) | parallel / distributed semantic region marker |
| 9 | `TOPOLOGY` | no | `topology` | topology or hardware-model declaration |
| 10 | `FEDERATION` | no | `topology` | federation / execution-domain declaration |
| 11 | `MODEL` | no | — | non-executable analytic or model-only statement |
| 12 | `ASSERT` | no | — | verifiable claim checked by the verifier |
| 13 | `EVIDENCE` | no | — | proof / provenance record reference |
| 14 | `LEARN` | no | — | learned rule awaiting Q1–Q6 admission |
| 15 | `RESOURCE` | no | `resource_conflict` | resource claim / budget declaration |
| 16 | `RECOVERY` | no | `failure_domain` | failure / recovery policy row |
| 17 | `LEDGER` | no | — | ledger emission directive |

A row whose `LANE` is not null additionally joins the `task` scope, regardless
of face (`ses.build_ses`).

**Normative consequences of non-executability.** A non-executable row
**SHALL NOT** contribute an operation to the extracted circuit
(`simulator.circuit_from_program` skips it), **SHALL NOT** be a concurrency
candidate, and **SHALL NOT** be scheduled as work. It still contributes SES
nodes, edges, resource claims and provenance.

---

## 4. The 18 core columns

For each column: meaning (from `lang.COLUMN_MEANING`), domain, nullability,
the code that reads it, and an example.

### 4.1 `ROW` — stable row identity

* **Domain:** any non-empty token; conventionally `R<digits>`.
* **Nullable:** **no**. The parser accepts `-` but the identity then collides
  on the second such row (`E-GRAM-003`).
* **Read by:** `Row.row_id`, `SESNode.id`, `Diagnostic.row`,
  `ProtocolStep.row`, every ledger cross-reference.
* **Rule:** unique within the bundle. Duplicates are `E-GRAM-003` (severity
  `REJECT`); the parser still appends the row, so the bundle is malformed but
  fully reported.
* **Example:** `R013`

### 4.2 `FACE` — semantic face

* **Domain:** exactly one of `lang.FACES` (§3).
* **Nullable:** **no**. `E-FACE-001` otherwise, and the row is skipped for all
  further verification (`continue`).
* **Read by:** `lang.verify`, `ses.build_ses` (scope routing),
  `simulator.circuit_from_program`, `ses.admit_concurrency`,
  `planner.HypergraphPartitioner.partition`, `protocols.ProtocolCompiler`.
* **Example:** `EXEC`

### 4.3 `LANE` — scheduling or subsystem lane

* **Domain:** identifier.
* **Nullable:** yes.
* **Read by:** `SESNode.lane`; `ses.build_ses` `task` scope and `lane_last`
  bookkeeping.
* **Rule:** lane order is a **declared user order, not a dependency**. An
  ordering edge with reason `USER_ORDER` is added between consecutive rows of a
  lane **only** when the later row's `OP` is `REGION_BEGIN` or `REGION_END`.
* **Example:** `L0`

### 4.4 `QSPACE` — Hilbert / subsystem / register domain

* **Domain:** an indexed reference, conventionally a range.
* **Nullable:** yes.
* **Read by:** `Row.qspace` only; the reference implementation records it and
  does not derive operands from it. Operands come from `OUT`, `CTRL`, `A`, `B`.
* **Status:** `IMPLEMENTED` as a declaration; **not** used as a
  constraint by the verifier. An implementation **SHOULD** declare the enclosing
  register here for readability and **SHALL NOT** rely on it being checked.
* **Example:** `q[0:2]`

### 4.5 `OP` — operation

* **Domain:** exactly one of `lang.ALL_OPS` (13 families, `lang.OP_CATALOG`).
* **Nullable:** **no**. `E-OP-001` otherwise; the row is skipped.
* **Read by:** everything downstream. `lang.OP_FAMILY` maps it to a family,
  which fixes the default `duration_model` via `ses.DURATION_UNITS`.
* **Example:** `CX`

### 4.6 `OUT` — destination quantum/classical object

* **Domain:** an indexed reference, or an opaque identifier (a classical bit
  name, a declared node/domain/link name).
* **Nullable:** yes.
* **Read by:** `Row.writes()`; measurement classical-bit registration;
  `DECLARE_*` target name; `TELEPORT` destination.
* **Example:** `c0`, `q[1]`, `N0`

### 4.7 `CTRL` — control qubit / register / operator

* **Domain:** indexed reference or identifier.
* **Nullable:** yes.
* **Read by:** `Row.reads()`; arity counting; `E-CTRL-001` disjointness check;
  classical-control condition (`CLASSICAL_IF` reads `CTRL` first, then `A`).
* **Example:** `q[0]`

### 4.8 `A` — primary operand

* **Domain:** indexed reference or identifier.
* **Nullable:** yes.
* **Read by:** `Row.reads()`; also `Row.writes()` for in-place gates;
  measurement source; `TELEPORT` source; `DECLARE_LINK` endpoint A.
* **Example:** `q[1]`

### 4.9 `B` — secondary operand

* **Domain:** indexed reference or identifier.
* **Nullable:** yes.
* **Read by:** as `A`; third operand of `CCX`/`CSWAP`; `DECLARE_LINK` endpoint
  B; second operand of `TENSOR`/`MERGE` for the clone check.
* **Example:** `q[2]`

### 4.10 `PARAM` — angle, time, coefficient, probability, index, target parameter

* **Domain:** a `;`-separated list of numeric literals (§9 of the grammar), or
  a `k=v` list whose values are numeric literals.
* **Nullable:** yes, except for `lang.PARAMETRIC_GATES`
  (`RX`, `RY`, `RZ`, `PHASE`, `U`), which require at least one evaluable value
  (`E-PARAM-001`).
* **Read by:** `Row.params` (list of floats, unparsable items dropped),
  `Row.param_map`.
* **Domain constraint:** for `lang.OPS_NOISE`, every parsed value **SHALL** lie
  in `[0,1]` (`E-PROB-001`).
* **Examples:** `pi/2`, `3*pi/4`, `0.01`, `0.1;0.05;0.02`, `theta=pi/4`

### 4.11 `TYPE` — quantum/classical semantic type

* **Domain:** a member of `lang.ALL_TYPES`, optionally suffixed with a bracket
  form; the verifier tests only the base name before the first `[`.
* **Nullable:** yes.
* **Read by:** `E-TYPE-001`; `lang._looks_quantum` (a base name in
  `lang.QUANTUM_OWNED_TYPES` makes every operand of the row ownership-governed);
  `SESNode.type`; `fabric.is_quantum`, `crdt.is_quantum_value`,
  `resilience._is_quantum_payload`.
* **Example:** `qubit`, `unitary`, `measurement_result`, `quantum_link`

### 4.12 `BASIS` — computational / X / Y / Z / custom / eigenbasis

* **Domain:** `Z`, `X`, `Y`, or a custom identifier.
* **Nullable:** yes; the simulator defaults an absent basis to `Z`.
* **Read by:** `simulator.circuit_from_program` (measurement basis and
  `PREP_BASIS`), `simulator._BASIS_ROTATION`.
* **Constraint:** the statevector and density engines admit `Z`, `X`, `Y` only;
  anything else raises `SimulationError`. The stabilizer engine admits `Z` only.
* **Example:** `X`

### 4.13 `REGIME` — exactness regime

* **Domain:** one of `lang.REGIMES` (15 values). `lang.EXACT_REGIMES` =
  {`EXACT`, `EXACT_LINEAR`, `PIECEWISE_EXACT`}.
* **Nullable:** yes (`E-REG-001` only for an unknown non-null value).
* **Rules:** an exact regime on an `lang.OPS_NOISE` operation is `E-REG-002`;
  an exact regime with `approx=true` in `ERROR` is `E-REG-003`. An approximate
  regime **SHALL NOT** be promoted to an exact one anywhere in the pipeline.
* **Example:** `EXACT`, `NOISY`, `APPROXIMATE`, `TARGET_SPECIFIC`

### 4.14 `ASSUME` — validity assumptions

* **Domain:** `;`-separated free text items (`Row.assumptions`).
* **Nullable:** yes.
* **Read by:** `Row.assumptions`. **Status: `IMPLEMENTED` as a declaration;
  not machine-checked.** The verifier does not evaluate assumption strings.
  Where assumptions *are* checked, they are checked structurally by the module
  that owns them (`protocols.BBPSSW_ASSUMPTIONS`,
  `commutation.CommutationVerdict.target_assumptions`,
  `erroralgebra.ErrorTerm.independence`).
* **Example:** `unitarity`, `born_rule`, `markovian;memoryless`

### 4.15 `ERROR` — declared error envelope

* **Domain:** `k=v` list (`Row.error_map`).
* **Nullable:** yes.
* **Recognized keys:** `p` or `value` (magnitude), `unit`, `kind`, `domain`,
  `independence` (all validated against `erroralgebra.UNITS`,
  `ERROR_KINDS`, `DOMAINS`, `ASSUMPTIONS` when the term is lifted), `approx`,
  `angle`.
* **Read by:** `SESNode.error_model`; `ledgers.error_terms_from_program`;
  the scheduler's error-budget stage; `planner` cost dimension
  `estimated_error`; `E-REG-003`.
* **Rule:** a cell that does not name a valid unit **and** kind **and** domain
  **and** independence is **not guessed at** — it is skipped and reported in
  `ERROR_LEDGER.json` under `declared_but_untyped_rows`.
* **Example:** `p=0.001;unit=probability;kind=failure_probability;domain=gate;independence=independent`

### 4.16 `RESOURCE` — resource / cost annotation

* **Domain:** `k=v` list (`Row.resource_map`).
* **Nullable:** yes.
* **Recognized keys:** `device` (→ `SESNode.device_class`, default `cpu`),
  `duration` (→ `SESNode.duration_model`, default from `ses.DURATION_UNITS`),
  `failure_domain` (→ `SESNode.failure_domain`, default: the node id, else
  `F_LOCAL`), `memory` (partition and placement accounting), `fidelity`
  (on `DECLARE_LINK`), `angle` (parameter fallback for the matrix oracle),
  and on `DECLARE_TOPOLOGY`: `nodes`, `domains`, `qcapacity` and friends read
  by `conformance.topology_from_program`.
* **Example:** `device=cpu;duration=2.0;memory=1048576`

### 4.17 `CONF` — confidence

* **Domain:** a real number in `[0,1]`, or `-`.
* **Nullable:** yes (`Row.confidence` returns `None` for `-` and for an
  unparsable value).
* **Rule:** a parsable value outside `[0,1]` is `E-CONF-001`. An unparsable
  non-null value is silently `None` — it is **not** an error, because `CONF` is
  advisory.
* **Example:** `1.0`, `0.9`

### 4.18 `PROOF` — evidence / provenance identifier

* **Domain:** identifier.
* **Nullable:** yes.
* **Read by:** `SESNode.proof_ref`; `QCIRP2.proof_ledger_refs`;
  `erroralgebra.ErrorTerm.provenance`; the admission check `E-PROV-001`
  (a claimed result with no provenance reference is refused).
* **Example:** `CP-6f2a1c9d0e4b7a83`

---

## 5. The 4 distributed-profile columns (1.2+)

### 5.1 `DOMAIN` — execution domain identity (1.5 FEDERATION model)

* **Domain:** an identifier previously declared by a `DECLARE_DOMAIN` row.
* **Nullable:** yes; `SESNode.memory_domain` defaults to `D_LOCAL`.
* **Rule:** referencing an undeclared domain **while the row names no node** is
  `E-DOM-001`. (When a node is named, the node's declaration carries the
  domain, so the check defers to `E-NODE-001`.)
* **Used by:** SES `memory_domain`; partition tree
  (`HypergraphPartitioner._partition_tree`); placement hierarchy;
  `ledgers` distributed-state ladder.
* **Example:** `D0`

### 5.2 `NODE` — logical node identity (1.2 distributed kernel)

* **Domain:** an identifier previously declared by a `DECLARE_NODE` row.
* **Nullable:** yes; `SESNode.owner` defaults to `N_LOCAL`.
* **Rule:** referencing an undeclared node is `E-NODE-001`. A local operation
  on an object whose authoritative owner is a different node is `E-OWN-003`
  unless the operation is a member of `lang.OPS_DISTRIBUTED_Q`.
* **Used by:** ownership locality; `Topology.crosstalk_status`; placement;
  protocol endpoint resolution (`ProtocolCompiler._peer_node`).
* **Example:** `N0`

### 5.3 `LINK` — classical channel or quantum link identity

* **Domain:** an identifier previously declared by a `DECLARE_LINK` row.
* **Nullable:** yes.
* **Rules:** referencing an undeclared link is `E-LINK-001`. `TELEPORT`
  requires a link (`E-PROTO-002`); `REMOTE_CNOT` / `REMOTE_CONTROL` require one
  (`E-PROTO-004`); `ENTANGLE_LINK` / `EPR_RESERVE` require one (`E-EPR-001`).
* **Used by:** SES `COMMUNICATION` + `RESOURCE_CONFLICT` edges between
  consecutive users of the same link; the `HE-LINK-*` hyperedge class;
  concurrency `SERIALIZE_RESOURCE`; `ProtocolCompiler` step chaining.
* **Example:** `Q0`

### 5.4 `FAMILY` — declared parallel family

* **Domain:** one of `lang.PARALLEL_FAMILIES` (17 values).
* **Nullable:** yes.
* **Rule:** an unknown non-null value is `E-FAM-001`.
* **Used by:** `SESNode.family`; `ses.discover_opportunities`
  (`declared_families`); `planner.select_parallel_family` (which prefers the
  lowest-communication valid layer and states that this is a **policy
  heuristic, not a semantic law**).
* **Example:** `INSTRUCTION_PARALLEL`, `SHOT_PARALLEL`

---

## 6. Row-level derived views

`lang.Row` exposes deterministic derived views that the rest of the stack uses
instead of re-parsing cells.

| View | Definition |
|---|---|
| `assumptions` | `_split_list(ASSUME)` |
| `error_map` | `_parse_kv(ERROR)` |
| `resource_map` | `_parse_kv(RESOURCE)` |
| `param_map` | `_parse_kv(PARAM)` |
| `params` | for each `;`-item of `PARAM`: take the text after the first `=` if present, then `_eval_number`; **unparsable items are dropped** |
| `confidence` | `float(CONF)` or `None` |
| `operand_refs()` | `parse_ref` over `OUT`, `CTRL`, `A`, `B`, dropping non-references |
| `reads()` | keys of `CTRL`, `A`, `B` |
| `writes()` | keys of `OUT`; **plus**, for `OP` in `GATE_ARITY` or `OPS_NOISE`: the keys of `A`, `B` and `CTRL` for non-controlled gates, and the keys of `A` for controlled gates. Sorted, de-duplicated. |
| `cells(columns)` | the cell values in the requested column order |
| `render(columns)` | `cells` joined with `CANONICAL_SEP` |

The `writes()` rule is what makes an in-place unitary create a
write-after-write edge on its target while leaving a control qubit as a pure
read — the basis of both `QUANTUM_OWNERSHIP` edges and the
`SERIALIZE_OWNERSHIP` concurrency decision.

> **Known asymmetry, stated for implementers.** In `Row.writes()` the branch
> that distinguishes the `A` cell from `CTRL`/`B` for controlled gates compares
> the cell *object* (`cell is self.a`). For a controlled gate whose `A` and
> `CTRL` cells hold equal strings, Python string interning can make the two
> cells the same object. Such a row is already rejected by `E-CTRL-001`
> (control and target must be disjoint), so no accepted program is affected.

---

## 7. Nullability summary

| Column | Nullable | Diagnostic if effectively missing |
|---|---|---|
| `ROW` | no | `E-GRAM-003` on collision |
| `FACE` | no | `E-FACE-001` |
| `OP` | no | `E-OP-001` |
| `PARAM` | conditionally | `E-PARAM-001` for parametric gates |
| `LINK` | conditionally | `E-PROTO-002`, `E-PROTO-004`, `E-EPR-001` |
| `CTRL`/`A`/`B` | conditionally | `E-ARITY-001` when the operand count is wrong |
| `OUT` | conditionally | `E-PROTO-001` for `TELEPORT` |
| all others | yes | — |

---

## 8. Examples by row kind

```
;; declaration
R000¦FEDERATION¦-¦-¦DECLARE_DOMAIN¦D0¦-¦-¦-¦-¦execution_domain¦-¦EXACT¦-¦-¦-¦1.0¦-¦-¦-¦-¦-
R001¦TOPOLOGY¦-¦-¦DECLARE_NODE¦N0¦-¦-¦-¦-¦node¦-¦EXACT¦-¦-¦device=cpu¦1.0¦-¦D0¦-¦-¦-
R003¦TOPOLOGY¦-¦-¦DECLARE_LINK¦Q0¦-¦N0¦N1¦-¦quantum_link¦-¦EXACT¦-¦-¦fidelity=0.98¦1.0¦-¦D0¦-¦-¦-

;; preparation, unitary, measurement, classical control
R010¦PREPARE¦L0¦q[0:2]¦PREP0¦q[0]¦-¦-¦-¦-¦qubit¦Z¦EXACT¦normalization¦-¦-¦1.0¦-¦D0¦N0¦-¦INSTRUCTION_PARALLEL
R012¦EXEC¦L0¦q[0:2]¦H¦-¦-¦q[0]¦-¦-¦unitary¦Z¦EXACT¦unitarity¦-¦-¦1.0¦-¦D0¦N0¦-¦INSTRUCTION_PARALLEL
R013¦EXEC¦L0¦q[0:2]¦CX¦-¦q[0]¦q[1]¦-¦-¦unitary¦Z¦EXACT¦unitarity¦-¦-¦1.0¦-¦D0¦N0¦-¦INSTRUCTION_PARALLEL
R014¦MEASURE¦L0¦q[0:2]¦MEASURE¦c0¦-¦q[0]¦-¦-¦measurement_result¦Z¦EXACT¦born_rule¦-¦-¦1.0¦-¦D0¦N0¦-¦-
R015¦CONTROL¦L0¦-¦CLASSICAL_IF¦-¦c0¦-¦-¦-¦bit¦-¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦-¦-

;; parametric gate and a channel
R020¦EXEC¦L1¦r[0:1]¦RZ¦-¦-¦r[0]¦-¦3*pi/4¦unitary¦Z¦EXACT¦unitarity¦-¦-¦1.0¦-¦D0¦N1¦-¦CIRCUIT_PARALLEL
R021¦NOISE¦L1¦r[0:1]¦DEPOLARIZE¦-¦-¦r[0]¦-¦0.01¦channel¦-¦NOISY¦markovian¦p=0.01;unit=probability;kind=failure_probability¦-¦0.9¦-¦D0¦N1¦-¦-

;; distributed protocol
R300¦PROTOCOL¦-¦-¦EPR_RESERVE¦e[0]¦-¦-¦-¦-¦remote_handle¦-¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦Q0¦-
R301¦PROTOCOL¦-¦-¦TELEPORT¦z[0]¦-¦q[0]¦-¦-¦remote_handle¦-¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N0¦Q0¦-
```

---

## 9. Implementation status

| Element | Status | Note |
|---|---|---|
| 18 core columns | `OPERATIONAL` | parsed, verified, rendered, sealed |
| 4 distributed columns | `OPERATIONAL` | additive; core bundles unaffected |
| 17-value FACE enumeration (H1a) | `OPERATIONAL` | closed set, checked by `E-FACE-001` |
| `QSPACE` as a constraint | `SPECIFIED` | declared and recorded; not checked |
| `ASSUME` as a machine-checked claim | `SPECIFIED` | free text; module-specific assumption sets are checked instead |
| `BASIS` custom / eigenbasis values | `SPECIFIED` | only `Z`, `X`, `Y` have numerical realizations |
| `CONF` propagation into ledgers | `SCAFFOLDED` | parsed and range-checked; not aggregated |
| `ERROR` typed lifting | `OPERATIONAL` | fully typed cells only; untyped ones are reported, never guessed |
