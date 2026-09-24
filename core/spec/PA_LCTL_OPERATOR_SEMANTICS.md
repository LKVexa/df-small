# PA-LCTL Operator Semantics

Document: `PA_LCTL_OPERATOR_SEMANTICS.md`
Authority: `pacore.lang.OP_CATALOG`, `pacore.lang.GATE_ARITY`,
`pacore.commutation` (reference matrices), `pacore.simulator`
(three-qubit matrices, Kraus sets, circuit extraction),
`pacore.protocols` (distributed primitives).

The machine-generated companion table is `OPERATOR_CATALOG.md`. This document
carries the normative semantics, the operand conventions, the parameter
domains and the exact reference matrices.

---

## 1. Conventions fixed by this specification

The LCTL documents do not fix these; `pacore.simulator` does, and they are
normative for PA-LCTL.

### 1.1 Qubit ordering

**Index 0 of the engine's qubit list is the MOST significant bit** of the
basis-state index, and the leftmost character of a reported bitstring
(`simulator` module docstring; `simulator._apply_operator` reshapes to
`[2]*n` with axis 0 most significant; `simulator._bitstring` uses
`format(index, f"0{n}b")`).

Consequence: for a two-qubit operator applied to wires `(a, b)`, `a` indexes
the high bit of the 4x4 matrix and `b` the low bit.

### 1.2 Operand order

`simulator._row_targets` collects operand keys in the fixed order
**`CTRL`, then `A`, then `B`**, de-duplicated, preserving first appearance.
This is the wire order handed to the matrix.

| Arity | Wire 0 (most significant) | Wire 1 | Wire 2 |
|---|---|---|---|
| 1 | the single operand (usually `A`) | — | — |
| 2 | `CTRL` if present, else `A` | the next operand | — |
| 3 | `CTRL` | `A` | `B` |

For `CCX`/`TOFFOLI` this means **`CTRL` and `A` are the two controls and `B` is
the target**. For `CSWAP` it means **`CTRL` is the control and `A`,`B` are the
swapped pair**.

### 1.3 Channel conventions

* `DEPOLARIZE(p)`: `rho -> (1-p) rho + p * I/2` on the target qubit.
* `BIT_FLIP(p)` / `PHASE_FLIP(p)` / `DEPHASE(p)`:
  `rho -> (1-p) rho + p A rho A` with `A = X / Z / Z`.
* von Neumann entropy is reported in **bits** (log base 2).

### 1.4 Reference durations

`ses.DURATION_UNITS` gives the default `duration_model` per family, in
**abstract time units**. The runtime never presents these as physical hardware
timings.

| Family | Units | Family | Units |
|---|---|---|---|
| `prepare` | 1.0 | `evolve` | 4.0 |
| `single_qubit` | 1.0 | `qec` | 4.0 |
| `multi_qubit` | 2.0 | `hybrid` | 1.0 |
| `measure` | 3.0 | `distributed_quantum` | 8.0 |
| `noise` | 1.0 | `collective` | 5.0 |
| `tensor` | 1.0 | `structural` | 0.0 |
| `operator` | 1.0 | | |

A row **MAY** override its duration with `RESOURCE duration=<float>`.

---

## 2. Family overview

`lang.OP_CATALOG` — 13 families, 131 distinct operation names
(`len(lang.ALL_OPS) == 131`).

| Family | Symbol | Ops | Numerically realized? |
|---|---|---|---|
| `prepare` | `lang.OPS_PREPARE` | 7 | 4 of 7 (§3) |
| `single_qubit` | `lang.OPS_1Q` | 14 | all 14 |
| `multi_qubit` | `lang.OPS_MQ` | 12 | 8 of 12 |
| `measure` | `lang.OPS_MEASURE` | 9 | 5 destructive + 3 sampling forms |
| `tensor` | `lang.OPS_TENSOR` | 7 | none (analysis only) |
| `operator` | `lang.OPS_OPERATOR` | 10 | none (analysis only) |
| `evolve` | `lang.OPS_EVOLVE` | 7 | none (analysis only) |
| `noise` | `lang.OPS_NOISE` | 11 | 7 of 11 (§6) |
| `qec` | `lang.OPS_QEC` | 7 | none (analysis only) |
| `hybrid` | `lang.OPS_HYBRID` | 8 | control-flow recording only |
| `distributed_quantum` | `lang.OPS_DISTRIBUTED_Q` | 14 | via `pacore.protocols` |
| `collective` | `lang.OPS_COLLECTIVE` | 8 | via `fabric.Collectives` |
| `structural` | `lang.OPS_STRUCTURAL` | 18 | declarations and barriers |

"Analysis only" means: the operation is a legal `OP`, it participates in
ownership, SES construction, partitioning and scheduling, and
`simulator.circuit_from_program` **raises `SimulationError`** if it appears on
an executable face — it never silently drops an executable row.

---

## 3. Preparation family (`lang.OPS_PREPARE`)

`PREP0`, `PREP1`, `PREP_PLUS`, `PREP_MINUS`, `PREP_BASIS`, `PREP_STATE`,
`RESET`

All seven are members of `lang.REINIT_OPS`: each establishes `LIVE` ownership
with a fresh lineage and clears the entanglement set.

### 3.1 Operands and parameters

| Op | Destination | Parameters | Basis |
|---|---|---|---|
| `PREP0`, `PREP1`, `PREP_PLUS`, `PREP_MINUS` | `OUT` (falls back to `CTRL`/`A`/`B` order if `OUT` is null) | none | ignored |
| `PREP_BASIS` | `OUT` | none | `BASIS` is read |
| `PREP_STATE` | `OUT` | amplitudes | — |
| `RESET` | `OUT` | none | — |

`simulator.circuit_from_program` raises `SimulationError` when a preparation
row has no resolvable destination.

### 3.2 Lowering (`simulator.PREPARE_SEQUENCE`)

Each prepared qubit is first `reset` to |0>, then the sequence is applied:

| Op | Sequence | Resulting state |
|---|---|---|
| `PREP0` | (none) | \|0> |
| `PREP1` | `X` | \|1> |
| `PREP_PLUS` | `H` | (\|0> + \|1>)/√2 |
| `PREP_MINUS` | `X`, `H` | (\|0> − \|1>)/√2 |

`PREP_BASIS` and `PREP_STATE` have **no** entry and therefore raise
`SimulationError` — *"preparation X has no admitted deterministic realization
in this module"*. This is a stated gap, not a silent fallback.

### 3.3 `RESET` semantics per engine

| Engine | Implementation | Rationale |
|---|---|---|
| `StatevectorEngine.reset` | Born-rule `measure(target, "Z")`, then classically conditioned `X` on the \|1> branch | a bare projection onto \|0> is not trace preserving and would silently discard amplitude; **this module never silently discards amplitude** |
| `DensityEngine.reset` | `P0 rho P0 + F rho F†` with `F = \|0><1\|` | trace preserving, needs no random draw because the mixture is representable |
| `StabilizerEngine.reset` | `measure`, then `X` on outcome 1 | as statevector |

Consequence: in the statevector engine, `RESET` **consumes randomness from the
seeded generator**. Two programs differing only in a reset therefore produce
different subsequent draws. This is documented behaviour, not a defect.

---

## 4. Single-qubit family (`lang.OPS_1Q`)

`I`, `X`, `Y`, `Z`, `H`, `S`, `SDG`, `T`, `TDG`, `RX`, `RY`, `RZ`, `PHASE`, `U`

Arity 1 (`lang.GATE_ARITY`). `lang.PARAMETRIC_GATES` = {`RX`, `RY`, `RZ`,
`PHASE`, `U`} require a `PARAM` (`E-PARAM-001`).

### 4.1 Static reference matrices (`commutation.ONE_QUBIT_STATIC`)

```
I   = [[1, 0],            X   = [[0, 1],            Y   = [[0, -i],
       [0, 1]]                   [1, 0]]                   [i,  0]]

Z   = [[1,  0],           H   = (1/√2) [[1,  1],     S   = [[1, 0],
       [0, -1]]                         [1, -1]]           [0, i]]

SDG = [[1,  0],           T   = [[1, 0        ],    TDG = [[1, 0         ],
       [0, -i]]                  [0, e^{iπ/4}]]            [0, e^{-iπ/4}]]
```

Exactly as `commutation.I2`, `PAULI_X`, `PAULI_Y`, `PAULI_Z`, `H_GATE`,
`S_GATE`, `SDG_GATE`, `T_GATE`, `TDG_GATE`, all `numpy` `complex128`.

### 4.2 Parametric reference matrices (`commutation.ONE_QUBIT_PARAM`)

With `c = cos(θ/2)`, `s = sin(θ/2)`:

```
RX(θ) = [[  c, -i s],      RY(θ) = [[c, -s],      RZ(θ) = [[e^{-iθ/2}, 0        ],
         [-i s,   c]]               [s,  c]]               [0,         e^{+iθ/2}]]

PHASE(λ) = [[1, 0     ],
            [0, e^{iλ}]]
```

`U(θ, φ, λ)` = `commutation.u3`:

```
U(θ,φ,λ) = [[      cos(θ/2),      -e^{iλ} sin(θ/2)],
            [e^{iφ} sin(θ/2),  e^{i(φ+λ)} cos(θ/2)]]
```

`commutation.one_qubit_matrix(op, params)` returns `None` when a parametric
gate has no parameter; the engines convert that to `SimulationError` naming
the missing angle. For `U`, missing `φ` and `λ` default to `0.0`
(`p = list(params) + [0.0, 0.0]`).

### 4.3 Parameter domains

| Op | Parameter | Domain | Note |
|---|---|---|---|
| `RX`, `RY`, `RZ` | θ (radian) | unrestricted real | 4π-periodic (spinor phase is tracked) |
| `PHASE` | λ (radian) | unrestricted real | 2π-periodic |
| `U` | θ, φ, λ | unrestricted real | φ, λ default to 0 |

The verifier does **not** range-check angles; only noise parameters are range
checked (`E-PROB-001`).

---

## 5. Multi-qubit family (`lang.OPS_MQ`)

`CX`, `CNOT`, `CY`, `CZ`, `CH`, `SWAP`, `ISWAP`, `CSWAP`, `CCX`, `TOFFOLI`,
`CU`, `MCU`

Arity: 2 for `CX`, `CNOT`, `CY`, `CZ`, `CH`, `SWAP`, `ISWAP`; 3 for `CSWAP`,
`CCX`, `TOFFOLI`. `CU` and `MCU` are in `lang.CONTROLLED_GATES` but **not** in
`lang.GATE_ARITY` — they are catalogued, ownership-checked and schedulable, and
have **no reference matrix**; a row using them on an executable face raises
`SimulationError` at circuit extraction.

### 5.1 Two-qubit reference matrices (`commutation.two_qubit_matrix`)

Basis order `|q0 q1>` with `q0` the most significant wire (§1.1).

```
CX = CNOT = [[1,0,0,0],      CZ = diag(1, 1, 1, -1)
             [0,1,0,0],
             [0,0,0,1],      CY = [[1,0,0, 0],
             [0,0,1,0]]            [0,1,0, 0],
                                   [0,0,0,-i],
                                   [0,0,i, 0]]

CH = [[1,0,   0,    0   ],   SWAP = [[1,0,0,0],   ISWAP = [[1,0,0,0],
      [0,1,   0,    0   ],           [0,0,1,0],            [0,0,i,0],
      [0,0, 1/√2, 1/√2 ],            [0,1,0,0],            [0,i,0,0],
      [0,0, 1/√2,-1/√2 ]]            [0,0,0,1]]            [0,0,0,1]]
```

`CH`'s lower-right 2x2 block is exactly `commutation.H_GATE`.

### 5.2 Three-qubit reference matrices (`simulator.three_qubit_matrix`)

8x8 identity with two entries exchanged. Wire `a` is the most significant of
the triple.

```
CCX = TOFFOLI :  identity, except  m[6,6]=m[7,7]=0 and m[6,7]=m[7,6]=1
                 i.e. |110> <-> |111>   (controls = wires 0 and 1,
                                          target  = wire 2)

CSWAP        :  identity, except  m[5,5]=m[6,6]=0 and m[5,6]=m[6,5]=1
                 i.e. |101> <-> |110>   (control = wire 0,
                                          swapped pair = wires 1 and 2)
```

### 5.3 Operand rules

* Control and target **SHALL** be disjoint for every member of
  `lang.CONTROLLED_GATES` (`E-CTRL-001`).
* Total operand key count **SHALL** equal `lang.GATE_ARITY[op]`
  (`E-ARITY-001`).
* `StabilizerEngine.cx` additionally raises `SimulationError` if control and
  target resolve to the same wire.
* `simulator._apply_operator` raises `SimulationError` on a repeated wire in
  the target list and on a matrix whose shape does not match the wire count.

---

## 6. Noise family (`lang.OPS_NOISE`)

`DEPOLARIZE`, `DEPHASE`, `BIT_FLIP`, `PHASE_FLIP`, `AMPLITUDE_DAMP`,
`PHASE_DAMP`, `PAULI_CHANNEL`, `KRAUS_CHANNEL`, `READOUT_ERROR`,
`LEAKAGE_MODEL`, `CROSSTALK_MODEL`

Full Kraus operators and the completeness rule are in
`PA_LCTL_CHANNEL_NOISE_MODEL.md`. Operator-level facts:

| Op | Parameters | Domain | Kraus rank | Status |
|---|---|---|---|---|
| `DEPOLARIZE` | p | [0,1] | 4 | implemented |
| `DEPHASE` | p | [0,1] | 2 | implemented (alias of `PHASE_FLIP`) |
| `PHASE_FLIP` | p | [0,1] | 2 | implemented |
| `BIT_FLIP` | p | [0,1] | 2 | implemented |
| `AMPLITUDE_DAMP` | γ | [0,1] | 2 | implemented |
| `PHASE_DAMP` | λ | [0,1] | 2 | implemented |
| `PAULI_CHANNEL` | px;py;pz | each [0,1], sum ≤ 1 | 4 | implemented |
| `KRAUS_CHANNEL` | explicit operator list | completeness verified | caller-defined | implemented (requires a programmatic operator list; not expressible in a `PARAM` cell) |
| `READOUT_ERROR` | — | — | — | **not implemented**: `ValueError` |
| `LEAKAGE_MODEL` | — | — | — | **not implemented**: `ValueError` |
| `CROSSTALK_MODEL` | — | — | — | **not implemented**: `ValueError` |

A noise row **SHALL NOT** declare an exact regime (`E-REG-002`), and every
parsed parameter **SHALL** lie in `[0,1]` (`E-PROB-001`).

Only **single-qubit** channel placement is implemented:
`DensityEngine.apply_channel` raises `SimulationError` for a Kraus set whose
operators are not 2x2.

---

## 7. Measurement family (`lang.OPS_MEASURE`)

`MEASURE`, `MEASURE_Z`, `MEASURE_X`, `MEASURE_Y`, `MEASURE_BASIS`, `POVM`,
`SAMPLE`, `EXPECT`, `VARIANCE`

`lang.DESTRUCTIVE_OPS` = {`MEASURE`, `MEASURE_Z`, `MEASURE_X`, `MEASURE_Y`,
`MEASURE_BASIS`, `POVM`, `REMOTE_MEASURE`} — note that `REMOTE_MEASURE` is
destructive although it lives in the distributed family, and that `SAMPLE`,
`EXPECT`, `VARIANCE` are **not** destructive.

Basis resolution in `simulator.circuit_from_program`:

| Op | Basis used |
|---|---|
| `MEASURE_X` | forced `X` |
| `MEASURE_Y` | forced `Y` |
| `MEASURE_Z` | forced `Z` |
| `MEASURE`, `MEASURE_BASIS`, `POVM` | the row's `BASIS`, default `Z` |

`SAMPLE`, `EXPECT` and `VARIANCE` are extracted with `kind = "protocol"`, which
means the numerical engines **refuse** them (`SimulationError`: must be
compiled by `pacore.protocols` first). They are analysis-level operations.

Full measurement semantics are in `PA_LCTL_MEASUREMENT_SEMANTICS.md`.

---

## 8. Tensor, operator, evolve and QEC families

These four families are **catalogued and analysed, never numerically executed**
by `pacore.simulator`.

| Family | Ops |
|---|---|
| `tensor` | `TENSOR`, `PARTIAL_TRACE`, `REDUCE`, `PERMUTE_QUBITS`, `PARTITION`, `MERGE`, `ENTANGLE` |
| `operator` | `KRON`, `COMPOSE`, `ADJOINT`, `EXP_OPERATOR`, `COMMUTATOR`, `ANTICOMMUTATOR`, `PROJECT`, `SPECTRAL`, `DIAGONALIZE`, `PAULI_DECOMPOSE` |
| `evolve` | `EVOLVE`, `HAMILTONIAN`, `UNITARY_EVOLVE`, `TROTTER`, `SUZUKI`, `ADIABATIC`, `PULSE` |
| `qec` | `ENCODE`, `SYNDROME`, `DETECT`, `CORRECT`, `DECODE`, `STABILIZER`, `LOGICAL` |

What they *do* contribute:

* ownership effects — notably `TENSOR` and `MERGE` trigger the clone check
  `E-CLONE-001`;
* SES nodes with the family's default duration (`tensor`/`operator` 1.0,
  `evolve`/`qec` 4.0);
* partitioning, placement and scheduling weight;
* resource accounting by class (`ledgers.gate_counts_by_class` buckets them
  under `other`).

A row from these families on an **executable face** with no numerical
realization raises `SimulationError` at extraction:
*"operation 'TROTTER' on FACE EXEC has no admitted numerical realization in
this module."* Placing such a row on a non-executable face (`MODEL`, `ASSERT`)
is the admitted way to declare it without claiming execution.

The engines do provide the *underlying primitives* as engine methods, outside
the `OP` catalog: `StatevectorEngine.partial_trace`,
`DensityEngine.partial_trace`, `StatevectorEngine.expectation`,
`DensityEngine.purity`, `von_neumann_entropy`, and the metrics
`state_fidelity`, `trace_distance`, `total_variation`, `frobenius_error`,
`max_amplitude_error`.

---

## 9. Hybrid / control-flow family (`lang.OPS_HYBRID`)

`CLASSICAL_IF`, `CLASSICAL_SWITCH`, `REPEAT`, `UNTIL`, `WHILE`, `SHOT_LOOP`,
`PARAM_BIND`, `FEEDBACK`

Extraction produces `kind = "control"` operations carrying the condition
(`CTRL` if present, else `A`). The engines **record** them and do not simulate
them as quantum actions:

> Classical control is recorded, not simulated as a quantum action. Its causal
> effect is already carried by the SES measurement and classical-control edges;
> executing it here would double-count. — `simulator._dispatch`

The engine still checks the dependency: a control whose condition was not
produced by a prior measurement raises `SimulationError`, mirroring
`E-CTL-003` at run time.

`FEEDBACK` is in `OPS_HYBRID` but is **not** in the extraction's control-flow
list; it is handled through the protocol path together with
`CLASSICAL_FEEDBACK`.

---

## 10. Distributed quantum family (`lang.OPS_DISTRIBUTED_Q`)

`ENTANGLE_LINK`, `EPR_RESERVE`, `EPR_RELEASE`, `TELEPORT`, `REMOTE_CONTROL`,
`REMOTE_CNOT`, `REMOTE_MEASURE`, `CLASSICAL_FEEDBACK`, `ENTANGLEMENT_SWAP`,
`PURIFY`, `HERALD`, `SYNC_QCLOCK`, `QCHANNEL_SEND`, `QCHANNEL_RECEIVE`

All 14 have a compilation template in
`protocols.ProtocolCompiler.SUPPORTED_OPS`; the compiler raises
`ProtocolError` for anything else rather than emitting an empty schedule.
Full semantics: `PA_LCTL_PROTOCOL_SPEC.md`.

Operator-level requirements enforced by `lang.verify`:

| Op | Requires | Diagnostic |
|---|---|---|
| `TELEPORT` | `A` and `OUT`; a `LINK`; a prepared source | `E-PROTO-001/002/003` |
| `REMOTE_CNOT`, `REMOTE_CONTROL` | a `LINK` | `E-PROTO-004` |
| `ENTANGLE_LINK`, `EPR_RESERVE` | a `LINK` | `E-EPR-001` |
| `EPR_RELEASE` | a known, unreleased ebit | `E-EPR-002/003` |

The reference duration of the whole family is 8.0 abstract units — the highest
of any family, which is what makes the scheduler and the partitioner prefer
local work.

---

## 11. Collective family (`lang.OPS_COLLECTIVE`)

`BROADCAST`, `SCATTER`, `GATHER`, `ALLGATHER`, `REDUCE`, `ALLREDUCE`, `SCAN`,
`ALLTOALL`

Executed by `fabric.Collectives` over the local worker fabric with an explicit
transfer schedule; the returned values are produced by those transfers, not by
a shortcut. Five algorithms are admitted (`fabric.COLLECTIVE_ALGORITHMS`:
`tree`, `ring`, `recursive_doubling`, `pairwise`, `linear`) and
`fabric.select_algorithm` chooses one by an explainable rule cascade `R0`–`R7`.
See `PA_LCTL_FABRIC_SPEC.md` §7.

> `REDUCE` appears in **both** `lang.OPS_TENSOR` and `lang.OPS_COLLECTIVE`.
> `lang.OP_FAMILY` is built by iterating `OP_CATALOG` in insertion order, so
> `REDUCE` resolves to the **later** family, `collective`, and therefore gets
> the collective default duration of 5.0. Implementers relying on the family of
> `REDUCE` should read `lang.OP_FAMILY["REDUCE"]` rather than assume.

---

## 12. Structural family (`lang.OPS_STRUCTURAL`)

`REGION_BEGIN`, `REGION_END`, `BARRIER`, `FENCE`, `EPOCH`, `SPAWN`, `AWAIT`,
`CHANNEL_SEND`, `CHANNEL_RECV`, `DECLARE_NODE`, `DECLARE_DOMAIN`,
`DECLARE_LINK`, `DECLARE_TOPOLOGY`, `DECLARE_FEDERATION`, `CLAIM`,
`ASSERT_INVARIANT`, `EMIT_LEDGER`, `NOTE`

Default duration 0.0 — structural rows are free.

### 12.1 Declarations

`DECLARE_NODE`, `DECLARE_DOMAIN` and `DECLARE_LINK` populate
`VerifyResult.declared_nodes / declared_domains / declared_links` and then
`continue` — no further checks apply to them.

| Op | Reads | Records |
|---|---|---|
| `DECLARE_NODE` | `OUT` (id), `DOMAIN`, `RESOURCE` | `{node, domain, resource, row}` |
| `DECLARE_DOMAIN` | `OUT` (id), `RESOURCE` | `{domain, resource, row}` |
| `DECLARE_LINK` | `OUT` (id), `A`, `B`, `TYPE`, `RESOURCE` | `{link, a, b, kind, resource, row}` |

`DECLARE_TOPOLOGY`, `DECLARE_FEDERATION`, `NOTE`, `EMIT_LEDGER`, `CLAIM` and
`ASSERT_INVARIANT` are skipped by the verifier entirely (`continue`) — they
carry no ownership effect. `conformance.topology_from_program` reads
`DECLARE_TOPOLOGY`'s `RESOURCE` map to build the bound `planner.Topology`, and
`CLAIM` rows are the subject of the admission pipeline's claim checks
(`E-CLAIM-001`, `E-CLAIM-002`, `E-PROV-001`, `E-SEAL-001`, `E-SEAL-002`,
`E-CAL-001`, and the matrix/state checks).

### 12.2 Barriers

`BARRIER`, `FENCE` and `EPOCH` produce an SES edge with reason `BARRIER` from
**every** prior node in source order. They are also absolute reorder barriers
in the commutation authority (rule `R-MEAS-BARRIER`).

### 12.3 Regions

`REGION_BEGIN` / `REGION_END` are the only operations for which lane adjacency
produces an edge, and its reason is `USER_ORDER` — declared order, not inferred
dependency.

---

## 13. Arity and parameter summary (normative)

```
GATE_ARITY = { I, X, Y, Z, H, S, SDG, T, TDG, RX, RY, RZ, PHASE, U : 1
               CX, CNOT, CY, CZ, CH, SWAP, ISWAP                   : 2
               CSWAP, CCX, TOFFOLI                                 : 3 }

PARAMETRIC_GATES = { RX, RY, RZ, PHASE, U }

CONTROLLED_GATES = { CX, CNOT, CY, CZ, CH, CSWAP, CCX, TOFFOLI, CU, MCU }

DESTRUCTIVE_OPS  = { MEASURE, MEASURE_Z, MEASURE_X, MEASURE_Y,
                     MEASURE_BASIS, POVM, REMOTE_MEASURE }

REINIT_OPS       = OPS_PREPARE
```

Arity is checked by counting the **keys** of `CTRL`, `A` and `B` — so
`CX ctrl=q[0] a=q[1:3]` is an arity error (3 keys for a 2-qubit gate), not a
broadcast.

---

## 14. Implementation status

| Element | Status | Note |
|---|---|---|
| Operation catalog membership (`E-OP-001`) | `OPERATIONAL` | |
| Arity checking (`E-ARITY-001`) | `OPERATIONAL` | |
| 1-qubit static + parametric matrices | `OPERATIONAL` | exact `complex128` |
| 2-qubit matrices (7 of 12 MQ ops) | `OPERATIONAL` | |
| 3-qubit matrices (`CCX`/`TOFFOLI`, `CSWAP`) | `OPERATIONAL` | |
| `CU`, `MCU` | `SCAFFOLDED` | catalogued; no matrix; extraction raises |
| `PREP0/1/PLUS/MINUS` | `OPERATIONAL` | |
| `PREP_BASIS`, `PREP_STATE` | `SCAFFOLDED` | no admitted deterministic realization; raises |
| `RESET` | `OPERATIONAL` | trace-preserving in all three engines |
| 7 of 11 noise channels | `OPERATIONAL` | single-qubit placement only |
| `READOUT_ERROR`, `LEAKAGE_MODEL`, `CROSSTALK_MODEL` | `SCAFFOLDED` | catalogued; no Kraus set |
| `tensor` / `operator` / `evolve` / `qec` families | `SPECIFIED` | analysed and scheduled; never numerically executed; raises on an executable face |
| Hybrid control flow | `IMPLEMENTED` | recorded, dependency-checked, not executed as quantum action |
| Distributed quantum family | `OPERATIONAL` | via `pacore.protocols` |
| Collectives | `OPERATIONAL` | real transfer schedules |
| Structural declarations and barriers | `OPERATIONAL` | |
