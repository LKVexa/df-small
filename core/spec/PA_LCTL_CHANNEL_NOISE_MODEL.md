# PA-LCTL Channel and Noise Model

Document: `PA_LCTL_CHANNEL_NOISE_MODEL.md`
Authority: `pacore.simulator.kraus_operators`,
`pacore.simulator.DensityEngine`, `pacore.simulator._apply_operator_density`,
`pacore.lang.OPS_NOISE`, `pacore.commutation` (channel commutation rules).

RFC 2119 keywords apply.

---

## 1. Density-operator semantics

### 1.1 Representation

`DensityEngine` holds a dense `2^n x 2^n` `complex128` matrix `rho`,
initialized to `|0...0><0...0|` (`rho[0,0] = 1.0`). Qubit ordering follows the
global convention: index 0 of the qubit list is the most significant wire
(`PA_LCTL_OPERATOR_SEMANTICS.md` §1.1).

### 1.2 Unitary action

`_apply_operator_density(rho, U, positions, n)` implements
`rho -> U rho U†` as two `tensordot` contractions:

1. contract `U` against the **ket** indices `0..n-1` at `positions`, then
   restore axis order;
2. contract `conj(U)` against the **bra** indices `n..2n-1` at
   `n + positions`, then restore axis order.

No explicit adjoint is formed; the conjugation plus the index placement is the
adjoint. A shape mismatch raises `SimulationError`.

### 1.3 Channel action

A channel is applied as the Kraus sum

```
rho' = sum_i  K_i rho K_i†
```

implemented in `DensityEngine.apply_channel` by accumulating
`_apply_operator_density(rho, K_i, [w], n)` over the operator set. The
accumulation is exact; nothing is normalized afterwards.

### 1.4 Validity

`DensityEngine.validate()` reports, and `assert_valid()` enforces at
`TRACE_TOL = 1e-9`:

| Quantity | Check |
|---|---|
| `trace` | `abs(tr - 1.0) <= TRACE_TOL` |
| `hermiticity_error` | `max abs(rho - rho†) <= TRACE_TOL` |
| `min_eigenvalue` | `>= -TRACE_TOL`, from `eigvalsh((rho + rho†)/2)` |

`run_density` calls `assert_valid()` **before** reporting any result, so an
invalid density matrix is an error, never a reported number.

### 1.5 Derived quantities

| Method | Definition |
|---|---|
| `purity()` | `Re Tr(rho^2)` |
| `von_neumann_entropy()` | `-sum_i lam_i log2 lam_i` over eigenvalues above `PROBABILITY_FLOOR`, **in bits** |
| `probabilities()` | `Re diag(rho)`, entries above `PROBABILITY_FLOOR` |
| `partial_trace(keep)` | permute ket and bra axes, reshape to `(2^k, 2^m, 2^k, 2^m)`, `einsum("aibi->ab")` |
| `state_hash()` | `_hash_array(rho)` — 12-decimal rounding, −0.0 normalized |

Cross-engine metrics live at module scope: `state_fidelity` (pure–pure,
pure–mixed and the Uhlmann formula for mixed–mixed), `trace_distance`
(eigenvalue path for Hermitian differences, singular values otherwise),
`frobenius_error`, `max_amplitude_error`, `total_variation`.

---

## 2. Completeness checking

`kraus_operators(name, params)` is the single admission point for every
channel. It performs, in order:

1. **Name resolution.** An unrecognized name raises
   `ValueError(f"{name} is not an admitted Kraus channel")`.
2. **Parameter-domain checks.** Every probability/rate is checked against
   `[0,1]` and, for `PAULI_CHANNEL`, `px + py + pz <= 1 + KRAUS_COMPLETENESS_TOL`.
   A missing parameter raises `ValueError(f"{name} is missing a required
   parameter")`.
3. **Shape consistency.** All operators must be square and of equal dimension,
   else `ValueError(f"{name} Kraus operators have inconsistent shapes")`.
4. **Completeness.**

```python
total = sum(K.conj().T @ K for K in ops)
err   = max(abs(total - I))
if err > KRAUS_COMPLETENESS_TOL:      # 1e-10
    raise ValueError(f"{name} Kraus set is not complete: "
                     f"max|sum K^dag K - I| = {err:.3e} > 1e-10")
```

**Normative rules.**

* Completeness is a **structural property of a declared channel, not a
  convergence tolerance**, and is therefore checked far tighter (1e-10) than
  the pass thresholds (1e-9).
* An incomplete set **SHALL** raise, naming the channel and the measured
  deviation. **Nothing is renormalized to hide an incomplete set.**
* Only **single-qubit** placement is implemented: `apply_channel` computes
  `k_wires = round(log2(K.shape[0]))` and raises `SimulationError` when it is
  not 1.
* Applying a channel on the **statevector** engine raises `SimulationError`
  ("a statevector cannot represent the resulting mixture"); the density engine
  is required.

---

## 3. Implemented channels and their Kraus operators

`I`, `X`, `Y`, `Z` below are the exact matrices in
`PA_LCTL_OPERATOR_SEMANTICS.md` §4.1.

### 3.1 `DEPOLARIZE(p)` — `_kraus_depolarize`

```
K0 = sqrt(1 - 3p/4) I
K1 = sqrt(p/4) X
K2 = sqrt(p/4) Y
K3 = sqrt(p/4) Z
```

Action: `rho -> (1 - p) rho + p I/2`. Domain `p in [0,1]`.
Kraus rank 4. Note the `3p/4` convention: `p = 1` is the fully depolarizing
channel, not `p = 3/4`.

### 3.2 `BIT_FLIP(p)` — `_kraus_pauli_pair(p, X)`

```
K0 = sqrt(1 - p) I
K1 = sqrt(p) X
```

Action: `rho -> (1-p) rho + p X rho X`. Rank 2.

### 3.3 `PHASE_FLIP(p)` and `DEPHASE(p)` — `_kraus_pauli_pair(p, Z)`

```
K0 = sqrt(1 - p) I
K1 = sqrt(p) Z
```

Action: `rho -> (1-p) rho + p Z rho Z`. Rank 2.
`DEPHASE` and `PHASE_FLIP` are **exact aliases** in this implementation; they
dispatch to the same branch. An implementation **SHALL NOT** give them
different meanings.

### 3.4 `AMPLITUDE_DAMP(γ)` — `_kraus_amplitude_damp`

```
K0 = [[1, 0        ],      K1 = [[0, sqrt(γ)],
      [0, sqrt(1-γ)]]            [0, 0      ]]
```

Domain `γ in [0,1]`. Rank 2. Non-unital: it drives the state toward `|0>`.

### 3.5 `PHASE_DAMP(λ)` — `_kraus_phase_damp`

```
K0 = [[1, 0        ],      K1 = [[0, 0       ],
      [0, sqrt(1-λ)]]            [0, sqrt(λ)]]
```

Domain `λ in [0,1]`. Rank 2. Unital; damps off-diagonal coherence without
changing populations.

### 3.6 `PAULI_CHANNEL(px, py, pz)` — `_kraus_pauli_channel`

```
K0 = sqrt(max(0, 1 - px - py - pz)) I
K1 = sqrt(px) X
K2 = sqrt(py) Y
K3 = sqrt(pz) Z
```

Domain: each of `px, py, pz` in `[0,1]` and
`px + py + pz <= 1 + 1e-10`. Fewer than three parameters raises
`ValueError("PAULI_CHANNEL requires px;py;pz")`. Rank 4.

In a PA-LCTL row the three parameters are written as a semicolon list:
`PARAM = 0.01;0.005;0.02`.

### 3.7 `KRAUS_CHANNEL(ops)` — caller-supplied

The parameter is an explicit list of operator matrices:

```python
raw = ps[0] if isinstance(ps[0], (list, tuple)) else ps
ops = [np.asarray(k, dtype=complex) for k in raw]
```

Then shape consistency and completeness are enforced as in §2. An empty list
raises.

**Stated limitation.** `Row.params` yields a list of *floats*, so a
`KRAUS_CHANNEL` cannot be written in a `PARAM` cell of a PA-LCTL bundle. It is
reachable only from Python callers of `kraus_operators` /
`DensityEngine.apply_channel`. A bundle row naming `KRAUS_CHANNEL` on an
executable face will reach `apply_channel` with float parameters and raise.
This is an honest gap, not a silently-degraded path.

---

## 4. What is NOT implemented

| Channel | Catalogued in | Status | Behaviour |
|---|---|---|---|
| `READOUT_ERROR` | `lang.OPS_NOISE` | `SCAFFOLDED` | `kraus_operators` raises `ValueError("READOUT_ERROR is not an admitted Kraus channel")` |
| `LEAKAGE_MODEL` | `lang.OPS_NOISE` | `SCAFFOLDED` | same |
| `CROSSTALK_MODEL` | `lang.OPS_NOISE` | `SCAFFOLDED` | same |

These three are **language-level vocabulary with no numerical realization**.
They are legal `OP` values; they participate in ownership, SES construction,
partitioning and scheduling; they contribute to `gate_counts_by_class` under
`noise`; and they raise the moment a numerical engine is asked to apply them.

Why they cannot simply be added as Kraus sets:

* **Readout error** is not a CPTP map on the pre-measurement state; it is a
  classical confusion matrix applied to the *outcome*. Modelling it as a
  channel would misplace it in the pipeline and would corrupt the
  post-measurement state. A faithful model requires a measurement-outcome
  transformation stage that this implementation does not have.
* **Leakage** takes the system out of the qubit subspace. It is not
  representable in a `2^n` density matrix at all; it requires a qudit or
  three-level representation.
* **Crosstalk** is inherently a **multi-qubit, calibration-dependent**
  correlated process. `apply_channel` implements single-qubit placement only,
  and the calibration data that would parameterize a crosstalk channel does not
  exist in this environment (`NETWORK=deny`, `BACKEND=none`).

Crosstalk *is* represented elsewhere, at the level it can be honestly
represented: `planner.Node.crosstalk_pairs` declares calibrated conflicting
qubit pairs, `Topology.crosstalk_status` returns
`CROSSTALK_PAIR(x,y)` for a conflicting pair on the same owner, the
concurrency admission turns that into the decision `SERIALIZE_CROSSTALK`, and
the scheduler's `crosstalk_validation` stage adds
`CROSSTALK_CONFLICT:<pair>` to `Schedule.blocked` for temporally overlapping
operations on the same target. That is a **scheduling constraint**, not a noise
channel, and the specification says so rather than pretending otherwise.

Other unimplemented model classes, for completeness:

| Missing model | Consequence |
|---|---|
| Multi-qubit (2+ wire) channels | `apply_channel` raises; only 2x2 Kraus operators are placed |
| Non-Markovian / time-correlated noise | no representation; `commutation` rule `R-CHANNEL-SAME` explicitly assumes *"no time-dependent calibration drift"* |
| Coherent (systematic) over-rotation as a declared channel | expressible only as an explicit parametric gate |
| Thermal (finite-temperature) amplitude damping | only the zero-temperature `AMPLITUDE_DAMP` exists |
| Trajectory (quantum-jump) unravelling | `trajectory_farm` is a *planner target*; `simulator.run` maps it to `run_density`, i.e. the full density matrix, labelled distributed. No stochastic unravelling engine exists. |

---

## 5. Channels in the rest of the stack

### 5.1 Verification

* An `lang.OPS_NOISE` row **SHALL NOT** declare an exact regime
  (`E-REG-002`).
* Every parsed `PARAM` value on a noise row **SHALL** lie in `[0,1]`
  (`E-PROB-001`).
* Channel rows write their operands: `Row.writes()` includes `A`, `B` and
  `CTRL` keys for `OPS_NOISE` operations, so a channel creates the same
  ownership edges an in-place gate would.

### 5.2 Commutation

`commutation.CommutationAuthority.classify_pair`, rules `R-CHANNEL-SAME` and
`R-CHANNEL-MIXED`:

| Situation | Verdict | Exactness | Assumptions recorded |
|---|---|---|---|
| Two **identical** channel ops on shared support | `COMMUTING_TARGET_CONDITIONAL` | `APPROXIMATE` | *"channels are Pauli-diagonal"*, *"no time-dependent calibration drift"* |
| A channel composed with a **non-identical** operation | `UNRESOLVED` | `NO_FAITHFUL_FORM` | — |

The `UNRESOLVED` verdict is not a failure; it is the honest answer. The
`commutation` module states the intended response — *"the scheduler must
serialize conservatively"* — and in practice the conservative outcome is
produced by an **earlier** gate in the decision cascade rather than by the
commutation verdict itself:

`ses.admit_concurrency` evaluates, in order, ownership disjointness →
measurement independence → `NON_COMMUTING` → link contention → topology →
crosstalk → `COMMUTING_TARGET_CONDITIONAL` → otherwise `PARALLEL_EXACT`.
Because an `lang.OPS_NOISE` row **writes** its operands (`Row.writes()`), two
channel rows on shared support are never ownership-disjoint, so they are
decided `SERIALIZE_OWNERSHIP` before the commutation verdict is consulted.

> **Implementation note, stated rather than papered over.** `UNRESOLVED` has no
> dedicated branch in that cascade. A pair that is ownership-disjoint,
> measurement-independent, contention-free and nevertheless `UNRESOLVED` falls
> through to `PARALLEL_EXACT`, with `ConcurrencyRecord.commutation_status`
> recording `UNRESOLVED` and `reason` carrying the rule's explanation. The
> decision is therefore always auditable, but a reviewer reading only
> `decision` would see `PARALLEL_EXACT`; the correct field to read for
> unresolved commutation is `commutation_status`.

### 5.3 Backend planning

`NumericalPlanner` rule cascade:

* **Rule 1** rejects `stabilizer` when channels are declared: *"the stabilizer
  tableau represents no mixed state"*.
* **Rule 2a**: `trajectories >= 64` selects `trajectory_farm`.
* **Rule 2b**: otherwise the density matrix is required; if
  `2^(2n) * 16` bytes exceeds the memory budget, `distributed_density`, else
  `local_density`. Statevector backends are rejected with *"a statevector
  cannot represent the mixed state produced by the declared channels"*.

Every rejected backend carries a stated reason; the planner never returns an
unexplained choice.

### 5.4 Error accounting

A channel row's `ERROR` cell is lifted into an `erroralgebra.ErrorTerm` when it
declares a valid `unit`, `kind`, `domain` and `independence`. The `domain`
value `gate`, `readout`, `idle`, `route`, `link`, `entanglement`, `classical`,
`compiler`, `sampling` or `model` is what lets composition keep incompatible
classes apart. An untyped `ERROR` cell is **not guessed at**; it is listed in
`ERROR_LEDGER.json` under `declared_but_untyped_rows`.

---

### 5.5 Worked example

```
;; a depolarizing channel with a typed error envelope
R021¦NOISE¦L1¦r[0:1]¦DEPOLARIZE¦-¦-¦r[0]¦-¦0.01¦channel¦-¦NOISY¦markovian;memoryless¦p=0.01;unit=probability;kind=failure_probability;domain=gate;independence=independent¦-¦0.9¦-¦D0¦N1¦-¦-

;; a general Pauli channel
R022¦NOISE¦L1¦r[0:1]¦PAULI_CHANNEL¦-¦-¦r[0]¦-¦0.01;0.005;0.02¦channel¦-¦NOISY¦markovian¦p=0.035;unit=probability;kind=failure_probability;domain=gate;independence=independent¦-¦0.9¦-¦D0¦N1¦-¦-

;; REJECTED: exact regime on a noise operation
R023¦NOISE¦L1¦r[0:1]¦DEPOLARIZE¦-¦-¦r[0]¦-¦0.01¦channel¦-¦EXACT¦-¦-¦-¦1.0¦-¦D0¦N1¦-¦-
   -> E-REG-002 @R023

;; REJECTED: parameter outside the probability domain
R024¦NOISE¦L1¦r[0:1]¦DEPOLARIZE¦-¦-¦r[0]¦-¦1.4¦channel¦-¦NOISY¦-¦-¦-¦1.0¦-¦D0¦N1¦-¦-
   -> E-PROB-001 @R024
```

---

## 6. Thresholds

| Constant | Value | Applies to |
|---|---|---|
| `KRAUS_COMPLETENESS_TOL` | `1e-10` | `max abs(sum K†K - I)` |
| `TRACE_TOL` | `1e-9` | trace, hermiticity, minimum eigenvalue of `rho` |
| `DENSITY_FROBENIUS_TOL` | `1e-9` | `||A - B||_F` between density matrices |
| `DISTRIBUTION_TV_TOL` | `1e-9` | total variation between outcome distributions |
| `PROBABILITY_FLOOR` | `1e-15` | omission threshold for reported probabilities; entropy eigenvalue floor |
| `STATE_HASH_DECIMALS` | `12` | rounding for the reproducible state hash |

All are module constants in `pacore.simulator`, stated because LCTL 1.3.x §56
requires pass thresholds to be explicit rather than implicit.

---

## 7. Implementation status

| Element | Status | Note |
|---|---|---|
| Dense density-operator engine | `OPERATIONAL` | exact `complex128` |
| Kraus application and completeness checking | `OPERATIONAL` | 1e-10, fail-closed |
| `DEPOLARIZE`, `BIT_FLIP`, `PHASE_FLIP`/`DEPHASE`, `AMPLITUDE_DAMP`, `PHASE_DAMP`, `PAULI_CHANNEL` | `OPERATIONAL` | single-qubit placement |
| `KRAUS_CHANNEL` | `IMPLEMENTED_PARTIAL` | reachable from Python callers only; not expressible in a `PARAM` cell |
| Density validity enforcement | `OPERATIONAL` | trace / hermiticity / PSD before every reported result |
| Purity, von Neumann entropy, partial trace | `OPERATIONAL` | entropy in bits |
| Multi-qubit channel placement | `SPECIFIED` | raises `SimulationError` |
| `READOUT_ERROR` | `SCAFFOLDED` | needs an outcome-transformation stage, not a channel |
| `LEAKAGE_MODEL` | `SCAFFOLDED` | needs a representation outside the qubit subspace |
| `CROSSTALK_MODEL` | `SCAFFOLDED` | represented instead as a scheduling constraint (`SERIALIZE_CROSSTALK`, `CROSSTALK_CONFLICT`) |
| Trajectory unravelling | `SCAFFOLDED` | `trajectory_farm` plans to the density engine; no jump engine exists |
| Non-Markovian noise | not represented | `R-CHANNEL-SAME` states the assumption explicitly |
| Hardware-calibrated noise parameters | `BLOCKED` | no authenticated target exists |
