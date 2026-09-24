# PA-LCTL Measurement Semantics

Document: `PA_LCTL_MEASUREMENT_SEMANTICS.md`
Authority: `pacore.simulator.StatevectorEngine.measure`,
`pacore.simulator.DensityEngine.measure`,
`pacore.simulator.StabilizerEngine.measure`,
`pacore.simulator._BASIS_ROTATION`, `pacore.lang.DESTRUCTIVE_OPS`,
`pacore.simulator._counts_from_probabilities`.

RFC 2119 keywords apply.

---

## 1. Why measurement is not an ordinary deterministic function

Every other operation in PA-LCTL is a *function of the state*. Measurement is
not, and the language treats it differently in five ways that an implementer
must reproduce.

1. **It is a sampling event.** The outcome is drawn from the Born distribution.
   The engines draw from a **seeded** generator, so a run is reproducible, but
   the *result is still a sample*: the same program with a different seed
   legitimately yields a different outcome. Nothing in the language treats a
   measurement outcome as derivable from the program text.
2. **It mutates the state irreversibly.** The post-measurement state is the
   renormalized projection. There is no inverse operation, and no engine method
   restores the pre-measurement state.
3. **It consumes ownership.** `lang.DESTRUCTIVE_OPS` moves the source key to
   `MEASURED`; reuse without `RESET`/`PREP*` is `E-OWN-002`. Measurement is the
   only operation besides `TELEPORT` and `EPR_RELEASE` that ends a lineage's
   liveness.
4. **It is an absolute reorder barrier.** `commutation.CommutationAuthority`
   rule `R-MEAS-BARRIER` returns `NON_COMMUTING` with exactness `EXACT` for any
   pair in which either side is destructive or is classical control. No
   optimizer may move an operation across a measurement.
5. **It creates a classical value with a name and a producer.** The `OUT` cell
   registers a classical bit; classical control that references an
   unproduced name is `E-CTL-003`, and the SES adds both a
   `MEASUREMENT_DEPENDENCY` and a `CLASSICAL_CONTROL` edge from producer to
   consumer.

A conforming implementation **SHALL NOT** model measurement as a pure function,
**SHALL NOT** constant-fold across it, and **SHALL NOT** reorder around it.

---

## 2. The Born rule as implemented

### 2.1 Statevector engine

`StatevectorEngine.measure(target, basis="Z")`:

```
1.  basis validation:  basis in {Z, X, Y}, else SimulationError
2.  basis rotation:    apply _BASIS_ROTATION[basis] (see §3) -- NOT inverted
3.  marginal:          p1 = <psi|P_1|psi>   computed as
                       vdot(take(psi_tensor, 1, axis=w), same).real
4.  clamp:             p1 = min(max(p1, 0.0), 1.0)
5.  draw:              u = rng.random();  outcome = 1 if u < p1 else 0
6.  prob:              prob = p1 if outcome else 1 - p1
7.  degeneracy guard:  prob <= PROBABILITY_FLOOR (1e-15) -> SimulationError
8.  collapse:          zero the amplitudes of the complementary branch
9.  renormalize:       psi <- psi / sqrt(prob)
10. log:               {qubit, basis, outcome, p1, draw}
```

Steps 4 and 7 matter. The clamp absorbs floating-point excursions of order
1e-17 outside `[0,1]`; the degeneracy guard **refuses** rather than dividing by
a near-zero norm. A measurement of an outcome with probability below `1e-15` is
an error, not a rare event: at that magnitude the state is numerically
degenerate and the result would be meaningless.

The draw and `p1` are both recorded in `measurement_log`, so a run is auditable
without re-executing it.

### 2.2 Density engine

`DensityEngine.measure` follows the same ten steps with two differences:

* `p1 = Tr(P_1 rho P_1)` computed by `_projector_weight(w, 1)`, which selects
  the `(1,1)` block along the ket and bra axes of the target wire and takes the
  real trace;
* collapse is `rho <- P_o rho P_o / prob` with `P_o` the rank-1 projector, via
  `_apply_operator_density`.

### 2.3 Stabilizer engine

`StabilizerEngine.measure(target)` returns `(outcome, deterministic_flag)` and
implements the CHP algorithm (Aaronson–Gottesman, quant-ph/0406196):

* **Random branch** — some stabilizer row `p` in `[n, 2n)` has `x[p][a] == 1`.
  Every other row with `x[i][a] == 1` is `rowsum`-ed into `p`; row `p-n`
  becomes a copy of row `p`; row `p` is replaced by `Z_a` with sign
  `r[p] = outcome`, where `outcome = rng.integers(0, 2)` — a **fair coin**,
  because for a stabilizer state an undetermined `Z` outcome is exactly
  uniform. Flag `False`.
* **Deterministic branch** — no such row exists. The scratch row `2n` is
  cleared and accumulates `rowsum(2n, i+n)` over destabilizers with
  `x[i][a] == 1`; the outcome is `r[2n]`. Flag `True`.

`_rowsum` raises `SimulationError` if the accumulated phase is odd, which is
impossible for a well-formed tableau — the check exists so that a corrupted
tableau fails closed rather than producing a plausible bit.

`peek_z(target)` returns the deterministic outcome **without disturbing the
tableau**, or `None` when the outcome is random; `expectation_z` returns
exactly `+1`, `-1` or `0` from it. These are the exact cross-check surface, not
a sampling shortcut.

---

## 3. Basis measurement

`simulator._BASIS_ROTATION`:

| `BASIS` | Rotation applied before the Z-projection | Meaning |
|---|---|---|
| `Z` | (none) | computational basis |
| `X` | `H` | X-eigenbasis |
| `Y` | `SDG`, `H` | Y-eigenbasis |

**The rotation is applied and NOT inverted.** This is a normative semantic
choice, stated in the module: an LCTL `MEASURE` row is destructive
(`lang.DESTRUCTIVE_OPS`), so the post-measurement frame is the rotated one.

Consequences an implementer must accept:

* After `MEASURE_X` on `q[0]`, subsequent operations on `q[0]` act in the
  rotated frame. This is not a defect; it is why the ownership model requires
  `RESET`/`PREP*` before reuse (`E-OWN-002`).
* The reported `probabilities()` after a mixed-basis program are probabilities
  **in the frame the program left the state in**.
* The stabilizer engine measures `Z` **only** and raises `SimulationError` for
  any other declared basis, with the message *"rotate explicitly with H / SDG
  for other bases"*. Explicit rotation is always available and is exactly what
  the other engines do internally.

A basis outside `{Z, X, Y}` raises `SimulationError` naming the admitted set.
`BASIS` values such as a custom eigenbasis are `SPECIFIED` in the language and
have no numerical realization.

---

## 4. Projective measurement, POVM and expectation

| Op | Extraction kind | Numerically executed | Notes |
|---|---|---|---|
| `MEASURE`, `MEASURE_Z`, `MEASURE_X`, `MEASURE_Y`, `MEASURE_BASIS` | `measure` | yes | rank-1 projective, per qubit, in the resolved basis |
| `POVM` | `measure` | **as a projective measurement** | `POVM` is in `DESTRUCTIVE_OPS` and extracts as `kind="measure"`; the engines have **no** general POVM element machinery. A declared POVM is checked for identity resolution by the admission pipeline (`E-POVM-001`) but is executed as a projective measurement in the resolved basis. |
| `SAMPLE`, `EXPECT`, `VARIANCE` | `protocol` | **no** | the engines raise `SimulationError` requiring protocol compilation first |

Expectation values *are* available as engine methods, outside the `OP`
catalog:

* `StatevectorEngine.expectation(pauli_string, qubits)` computes
  `<psi|P|psi>` for a Pauli string over the named qubits. It raises
  `SimulationError` if the string length does not match the qubit count, if a
  character is not in `{I,X,Y,Z}`, or if the result has an imaginary part above
  `MAX_AMPLITUDE_ERROR_TOL` (1e-9) — that last check is a Hermiticity guard,
  not a tolerance to be relaxed.
* `StabilizerEngine.expectation_z(target)` returns exactly ±1 or 0.

`protocols.entanglement_swap` uses `expectation("ZZ", ...)` and
`expectation("XX", ...)` as an **independent correlation check** on the
executed state before it will herald a swapped pair.

---

## 5. Collapse and the post-measurement state

### 5.1 Statevector

```
psi_after = (I ⊗ ... ⊗ P_o ⊗ ... ⊗ I) psi / sqrt(prob)
```

implemented as: reshape to `[2]*n`, write `0.0` into the slice of the
complementary outcome along the target axis, reshape back, divide by
`sqrt(prob)`. Amplitude is never discarded silently — the removed branch's
weight is exactly `1 - prob`, and the renormalization restores unit norm.

### 5.2 Density

```
rho_after = P_o rho P_o / prob
```

`DensityEngine.assert_valid()` re-checks trace, hermiticity and positive
semi-definiteness at `TRACE_TOL = 1e-9` after every run; `run_density` calls it
before reporting.

### 5.3 Stabilizer

The tableau is updated in place as described in §2.3. The post-measurement
state is again a stabilizer state, exactly.

### 5.4 Reported probabilities after measurement

`probabilities()` reports `|amplitude|^2` (statevector), `Re diag(rho)`
(density), or `|amplitude|^2` of the reconstructed statevector (stabilizer,
`n <= 14` only). Entries below `PROBABILITY_FLOOR = 1e-15` are **omitted**, so
a reported distribution is a sparse map, and a key's absence means "below the
floor", not "impossible".

---

## 6. Classical binding

A destructive measurement with a non-null `OUT` **SHALL** register that name as
a classical value.

| Stage | Mechanism |
|---|---|
| Verifier | `classical_bits[row.out] = row.row_id` |
| SES | `measurement_producer[row.out] = node.id`; a later classical-control row gains edges `MEASUREMENT_DEPENDENCY` **and** `CLASSICAL_CONTROL` from the producer |
| Circuit extraction | `OUT` is appended to `Circuit.classical_bits` |
| Engine | the per-outcome record carries `"out": op["out"]` |
| Runtime check | `simulator._dispatch` for `kind == "control"` raises if the condition is not in the set of bound `out` names |

`CLASSICAL_IF`, `CLASSICAL_SWITCH`, `FEEDBACK` and `CLASSICAL_FEEDBACK` read
their condition from `CTRL` if present, otherwise from `A`. A missing condition
is `E-CTL-002`; an unproduced one is `E-CTL-003`.

In the distributed protocol fabric the same discipline is enforced across a
channel: `protocols.ClassicalFeedbackFabric.assert_ready(msg_id)` raises
`ProtocolError` unless the message state is `DELIVERED`, so a Pauli correction
can never run ahead of the classical bit it depends on. The protocol compiler
enforces the structural counterpart: a `PAULI_CORRECT` step with no
`classical_required` flag, or with no `CLASSICAL_RECV` in its dependency set,
fails `ProtocolPlan.validate()`.

---

## 7. Shot semantics

### 7.1 What a shot is here

`shots` in this implementation is a **resampling of the final reported
distribution**, not a re-execution of the circuit.

```python
def _counts_from_probabilities(probs, shots, rng):
    keys = sorted(probs)
    weights = normalize([probs[k] for k in keys])
    draws = rng.choice(len(keys), size=shots, p=weights)
    -> counts dict, sorted
```

* The generator is `np.random.default_rng(seed + 1)` — a *different* stream
  from the engine's `default_rng(seed)`, so sampling never perturbs the
  mid-circuit measurement draws.
* Keys are sorted before sampling, so the mapping from RNG draws to bitstrings
  is deterministic.
* Counts are returned sorted.

### 7.2 The consequence, stated plainly

For a circuit **without** mid-circuit measurement, resampling the final
distribution is exactly equivalent to independent shots, and this is the common
case.

For a circuit **with** mid-circuit measurement, it is **not** equivalent: the
engine performs the mid-circuit measurement **once**, collapsing to one branch,
and then `shots` samples are drawn from that single collapsed branch's final
distribution. A conforming implementation that wants per-shot branch diversity
**SHALL** re-run the circuit per shot with distinct seeds; that loop is not
provided by `pacore.simulator`.

This is why the numerical planner refuses to farm shots when classical feedback
is present:

> `RULE-4: shots >= SHOT_FARM_MIN with no classical feedback -> shot farm`
> and otherwise `reject("shot_farm", "mid-circuit classical feedback makes
> shots non-independent")`.

`Circuit.summary` sets `classical_feedback = any(op["kind"] == "protocol")`.

### 7.3 Shot thresholds

| Constant | Value | Effect |
|---|---|---|
| `simulator.SHOT_FARM_MIN` | 1024 | at or above, and with no feedback, the planner selects `shot_farm` |
| `simulator.CIRCUIT_FARM_MIN` | 8 | independent circuits at or above select `circuit_farm` |
| `simulator.TRAJECTORY_FARM_MIN` | 64 | noise trajectories at or above select `trajectory_farm` |

Farming is embarrassingly parallel **classical** work; the
`FARM_EXECUTION_LEDGER` states explicitly that it *"never crosses the quantum
boundary"*.

---

## 8. Determinism and reproducibility

| Property | Guarantee | Mechanism |
|---|---|---|
| Same program + same seed → same outcomes | yes | `np.random.default_rng(seed)` per engine instance |
| Same program + same seed → same `final_state_hash` | yes | `_hash_array` rounds to 12 decimals and normalizes −0.0 |
| Same program + same seed → same counts | yes | separate `default_rng(seed + 1)`, sorted keys |
| Different seed → possibly different outcomes | yes, by design | measurement is a sampling event |
| Measurement draw auditable | yes | `measurement_log` records `p1` and the raw `draw` |

`cli.cmd_selfcheck` asserts `simulation_deterministic` by running the same
circuit twice with seed 5 and comparing `final_state_hash`; `smoke.py` does the
same with seed 7 and 256 shots.

---

## 9. Diagnostics and failure modes

| Condition | Result |
|---|---|
| Basis not in `{Z, X, Y}` | `SimulationError` naming the admitted set |
| Stabilizer engine, basis ≠ `Z` | `SimulationError` telling the caller to rotate explicitly |
| Selected outcome probability ≤ 1e-15 | `SimulationError` — the state is numerically degenerate |
| Measurement row with no qubit operand | `SimulationError` at extraction |
| Reuse of a `MEASURED` key | `E-OWN-002` at verification |
| Classical control with no condition | `E-CTL-002` |
| Classical control on an unproduced value | `E-CTL-003` (static) / `SimulationError` (runtime) |
| Pauli expectation with a non-Hermitian embedding | `SimulationError` reporting the imaginary part |
| CHP rowsum with an odd phase | `SimulationError` — the tableau is not well formed |

---

## 10. Implementation status

| Element | Status | Note |
|---|---|---|
| Born-rule projective measurement, statevector | `OPERATIONAL` | with collapse and renormalization |
| Born-rule projective measurement, density | `OPERATIONAL` | with validity re-check |
| CHP measurement, stabilizer | `OPERATIONAL` | random and deterministic branches; `peek_z` non-destructive |
| Z / X / Y basis measurement | `OPERATIONAL` | rotation applied and not inverted |
| Custom / eigenbasis measurement | `SPECIFIED` | no numerical realization |
| Classical binding and control gating | `OPERATIONAL` | static and runtime |
| Pauli expectation values | `OPERATIONAL` | engine method, not an `OP` |
| `POVM` as a general positive-operator measurement | `SCAFFOLDED` | catalogued and admission-checked for identity resolution; executed as a projective measurement |
| `SAMPLE`, `EXPECT`, `VARIANCE` as operations | `SPECIFIED` | extraction marks them `protocol`; engines refuse |
| Shots as resampling of the final distribution | `IMPLEMENTED` | exact for feedback-free circuits; **not** per-shot branch diversity for mid-circuit measurement |
| Per-shot circuit re-execution loop | not provided | a caller-level loop over seeds |
| Readout-error modelling on measurement | `SCAFFOLDED` | `READOUT_ERROR` is catalogued with no Kraus set |
