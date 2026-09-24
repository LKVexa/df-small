# PA-LCTL Error Model

Document: `PA_LCTL_ERROR_MODEL.md`
Authority: `pacore.erroralgebra` (terms, laws, `compose`, Monte Carlo,
coherence exposure), `pacore.ledgers.error_terms_from_program`,
`pacore.planner.TemporalScheduler` (error-budget and coherence stages).

The generated companion table is `ERROR_CATALOG.md`. RFC 2119 keywords apply.

---

## 1. The three structural rules

These are not policy; they are enforced by construction in
`erroralgebra.compose`.

1. **Incompatible units are never merged into a false scalar.** `compose`
   returns `ERROR_COMPOSITION_UNRESOLVED` with the separate dimensions
   preserved, so the caller sees a vector of incomparable quantities rather
   than a fabricated single number.
2. **Additive small-error composition is always labelled `APPROXIMATE`** and
   carries the neglected second-order magnitude.
3. **Variances add only under a justified independence assumption.**
   Correlated terms require an explicit covariance or correlation model;
   without one the composition is unresolved.

---

## 2. Error terms

### 2.1 The term record — `erroralgebra.ErrorTerm`

| Field | Domain | Meaning |
|---|---|---|
| `name` | str | identity; a lifted term uses the source `ROW` id |
| `value` | float | magnitude |
| `unit` | `erroralgebra.UNITS` | dimension |
| `domain` | `erroralgebra.DOMAINS` | where the error arises |
| `kind` | `erroralgebra.ERROR_KINDS` | what the number *is* |
| `independence` | `erroralgebra.ASSUMPTIONS` | what the **declarer** claims |
| `correlation` | `dict[str, float]` | pairwise ρ by partner name |
| `calibration_epoch` | `int \| None` | device-state epoch |
| `bounds` | `(lo, hi)` | optional interval |
| `confidence_level` | `float \| None` in [0,1] | |
| `variance` | `float \| None` | |
| `provenance` | str | usually the `PROOF` cell |

Construction-time validation (`__post_init__`) rejects: an unknown unit,
domain, kind or assumption; a `probability`-unit value outside `[0,1]`; a
`confidence_level` outside `[0,1]`; inverted bounds. All raise
`ErrorAlgebraError`.

The `independence` field is deliberately the **declarer's** claim, not the
composer's assumption. `compose` checks the two against each other and refuses
to proceed when they disagree (§4, rule 2).

### 2.2 Error classes

**Units** (`erroralgebra.UNITS`, 7): `probability`, `log_probability`,
`dimensionless`, `second`, `hertz`, `count`, `radian`.

**Kinds** (`erroralgebra.ERROR_KINDS`, 10): `success_probability`,
`failure_probability`, `infidelity`, `log_success`, `additive_error`,
`variance`, `std_dev`, `duration`, `rate`, `count`.

**Domains** (`erroralgebra.DOMAINS`, 10): `gate`, `readout`, `idle`, `route`,
`link`, `entanglement`, `classical`, `compiler`, `sampling`, `model`.

**Assumptions** (`erroralgebra.ASSUMPTIONS`, 6): `independent`, `correlated`,
`unknown_correlation`, `small_error`, `exact_multiplicative`,
`exact_logarithmic`.

The **error class** of a term, for the purpose of merging, is the pair
`unit/kind` (`_group_dimensions`). Two terms are in the same class iff both
components are equal. Domain is *not* part of the class — it is reported and,
when a composition spans several domains, the combined term's domain becomes
`model` rather than a misleading specific one.

### 2.3 Success probability

`ErrorTerm.success_probability` is defined for four kinds only:

| Kind | `success_probability` |
|---|---|
| `success_probability` | `value` |
| `failure_probability`, `infidelity`, `additive_error` | `1 - value` |
| `log_success` | `exp(value)` |
| anything else | raises `ErrorAlgebraError` |

### 2.4 Lifting `ERROR` cells from a program

`ledgers.error_terms_from_program`:

* reads `p`, falling back to `value`; a row with neither is skipped;
* a non-numeric magnitude is skipped;
* `unit`, `kind`, `domain`, `independence` default to `probability`,
  `failure_probability`, `gate`, `unknown_correlation`;
* **a cell that does not name a valid unit AND kind AND domain AND
  independence is skipped, not guessed at**;
* a construction failure (`ErrorAlgebraError`) skips the term.

Every skipped row with a non-empty `ERROR` map is reported in
`ERROR_LEDGER.json` under `declared_but_untyped_rows`. A conforming
implementation **SHALL NOT** coerce an untyped error cell into a typed term.

---

## 3. The composition-law registry (design hole H13)

The QUORUM documents require *"an explicit composition law registry"* but never
fix the identifiers. PA-LCTL fixes `L1`–`L6` and **every composed term names
the law that produced it** (`CompositionResult.law_id`,
`ErrorTerm.provenance = "law:L<n>"`).

| Law | Name | Applies to kinds | Requires | Exactness | Statement |
|---|---|---|---|---|---|
| `L1` | `independent_success_multiplication` | `success_probability`, `failure_probability`, `infidelity` | `independent` | `EXACT` | `P = prod_i p_i` — exact, not an approximation |
| `L2` | `log_success_addition` | `log_success` | `independent` | `EXACT` | `log P = sum_i log p_i` |
| `L3` | `additive_small_error` | `additive_error`, `infidelity`, `failure_probability` | `small_error` | `APPROXIMATE` | `1 - prod(1-e_i) ≈ sum e_i`; the neglected second-order magnitude is reported |
| `L4` | `independent_variance_addition` | `variance`, `std_dev` | `independent` | `EXACT` | `Var = sum_i Var_i` |
| `L5` | `correlated_variance_with_covariance` | `variance`, `std_dev` | `correlated` | `EXACT` | `Var = sum_i Var_i + 2 sum_{i<j} rho_ij s_i s_j` |
| `L6` | `duration_addition` | `duration` | `independent` | `EXACT` | `T = sum_i t_i` |

`erroralgebra.law_for(kind, assumption)` selects the first law whose
`applies_to_kinds` contains the kind and whose `required_assumption` equals the
assumption; `None` when no law applies.

Note that `L3` and `L1` overlap on `infidelity` and `failure_probability`; they
are distinguished by the **assumption**: `independent` selects the exact `L1`,
`small_error` selects the approximate `L3`. An implementation **SHALL NOT**
select `L3` when `L1` applies.

---

## 4. `compose()` — the decision procedure

`erroralgebra.compose(terms, assumption="independent") -> CompositionResult`

Evaluated in this order; the first failing gate returns
`ERROR_COMPOSITION_UNRESOLVED` with the dimensions preserved and a reason.

**Gate 0 — non-empty.** An empty term list raises `ErrorAlgebraError`; an
unknown assumption raises.

**Gate 1 — dimensional compatibility.** If the terms span more than one
`unit/kind` group:

> *"terms span N incompatible unit/kind dimensions [...]; merging them would
> fabricate a scalar with no physical meaning, so the dimensions are returned
> separately"*

`resolved = False`, `token = ERROR_COMPOSITION_UNRESOLVED`,
`combined = None`, `dimensions` = the full split.

**Gate 2 — declared vs requested independence.** With
`assumption = "independent"`, if any term declares something outside
`{independent, exact_multiplicative, exact_logarithmic, small_error}`:

> *"independent composition requested but the terms declare [...]; an
> unjustified independence claim would understate the combined uncertainty"*

**Gate 3 — unknown correlation.** `assumption = "unknown_correlation"` is
always unresolved:

> *"no composition law is admissible without either an independence
> justification or a covariance model"*

**Gate 4 — a law must exist.** `law_for(kind, assumption)` returning `None` is
unresolved, naming the kind and assumption.

**Gate 5 — one calibration epoch.** Terms from different
`calibration_epoch` values are unresolved:

> *"they do not describe the same device state"*

**Application.** Once all gates pass, the selected law is applied:

| Law | Computation |
|---|---|
| `L1` | `p = prod(success_probability)`; result is `p` for `success_probability`, else `1 - p` |
| `L2` | `sum(value)` |
| `L3` | `sum(e_i)` where `e_i = value` (or `1 - value` for a success kind); also computes the exact `1 - prod(1-e_i)` and reports `neglected_second_order = abs(sum - exact)`. **If the sum exceeds 1 the result is unresolved**: *"the additive sum exceeds 1 and is therefore not a probability; the approximation is outside its domain"* |
| `L4` | `sum(v)` with `v = value` for `variance`, `value**2` for `std_dev` |
| `L5` | `sum(s_i^2) + 2 sum_{i<j} rho_ij s_i s_j`; **a missing pairwise ρ is unresolved**, naming the missing pairs |
| `L6` | `sum(value)` |

**Output typing.** For `L4`/`L5` the combined kind becomes `variance` and a
`probability` unit is rewritten to `dimensionless` (a variance of a probability
is not a probability). The combined `domain` is the shared domain if all terms
agree, otherwise `model`. The combined `confidence_level` is the **minimum** of
the declared levels. The combined `independence` is the requested assumption,
and `provenance` is `law:<id>`.

The result's `token` is the law's exactness (`EXACT` or `APPROXIMATE`), so a
caller reading only `token` still learns whether the number is exact.

### 4.1 `ERROR_COMPOSITION_UNRESOLVED`

`erroralgebra.TOKEN_UNRESOLVED = "ERROR_COMPOSITION_UNRESOLVED"`.

A conforming implementation **SHALL**:

* return this token rather than any scalar when any gate fails;
* preserve `dimensions` — the full per-group listing of the input terms;
* preserve `reasons` — an ordered list of human-readable justifications;
* preserve `inputs` — every input term's dict form.

and **SHALL NOT** offer a "best effort" scalar, a clamped value, or a
downgraded assumption.

`ERROR_COMPOSITION_LEDGER.json` records an attempt under **every** assumption
in `erroralgebra.ASSUMPTIONS`, so the ledger shows not only what resolved but
what did not and why.

---

## 5. Monte-Carlo uncertainty propagation

`erroralgebra.MonteCarloUncertainty` (LCTL 1.4.x §33).

### 5.1 Determinism

The generator is `numpy.random.default_rng(seed)` and inputs are sampled **in
declaration order**, so two runs with the same seed and the same inputs produce
bit-identical reports. Defaults: `seed = 20260811`, `samples = 20000`;
fewer than two samples raises.

### 5.2 Inputs

`erroralgebra.UncertainInput(name, distribution, params, unit)` with
`distribution` in `erroralgebra.DISTRIBUTIONS`:

| Distribution | `params` | Sampler |
|---|---|---|
| `normal` | (μ, σ) | `rng.normal` |
| `uniform` | (lo, hi) | `rng.uniform` |
| `lognormal` | (μ, σ) | `rng.lognormal` |
| `beta` | (a, b) | `rng.beta` |
| `point` | (v,) | `np.full(n, v)` |

### 5.3 Report

`MonteCarloReport` carries `samples`, `seed`, `mean`, `variance` (ddof=1),
`std_dev` (ddof=1), quantiles `q05`/`q50`/`q95`, `min`, `max`, a
`distribution_summary` (skewness, excess kurtosis, a 10-bin histogram with
edges), the declared `assumptions`, the input descriptors, a `report_hash`, and
the token `UNCERTAINTY_PROPAGATION_PASS`.

The `assumptions` list is **caller-supplied and always reported**. There is no
way to obtain a Monte-Carlo number from this module without an accompanying
statement of what was assumed.

### 5.4 `propagate_terms`

A convenience path for a product of independent success probabilities: each
term becomes a `normal(success_probability, |success_probability| *
relative_sigma)` input, the combiner multiplies with `clip(0, 1)`, and the
assumptions recorded are exactly *"terms independent"*, *"relative sigma
&lt;r&gt;"*, *"probabilities clipped to [0,1]"*. The clipping is disclosed, not
hidden.

---

## 6. Coherence exposure

`erroralgebra.CoherenceExposure` (LCTL 1.4.x §48, 1.5.x §55).

### 6.1 Components

`erroralgebra.EXPOSURE_COMPONENTS` — six, and the last three are the ones
usually omitted by other systems:

| Component | Meaning |
|---|---|
| `operation_time` | time spent applying operations |
| `idle_time` | time held while doing nothing |
| `route_time` | time in transit / routing |
| `measurement_wait` | latency of a measurement result |
| `classical_feedback_wait` | latency of the classical bit that gates a correction |
| `entanglement_wait` | time waiting for an ebit to be established |

`add(component, seconds)` rejects an unknown component and a negative
duration. `total_coherence_exposure` is the plain sum;
`breakdown()` returns the six components.

### 6.2 The check

`CoherenceExposure.check(budget)` returns a `CoherenceCheck`:

```
ok        = total <= budget
token     = SCHEDULE_ADMITTED_COHERENCE   if ok
            SCHEDULE_BLOCKED_COHERENCE    otherwise
headroom  = budget - total
utilization = total / budget
dominant_component = argmax over the breakdown (ties broken by sorted name)
reason    = names the dominant contribution and its magnitude
```

A non-positive budget raises. `require(budget)` raises
`CoherenceBudgetExceeded` carrying the token when the ceiling is breached.

### 6.3 `SCHEDULE_BLOCKED_COHERENCE`

The token is produced in two places and means the same thing in both:

1. `erroralgebra.CoherenceExposure.check` / `require`, as above.
2. `planner.TemporalScheduler.schedule`, which computes its own exposure
   dictionary and appends `"SCHEDULE_BLOCKED_COHERENCE"` to
   `Schedule.blocked` when
   `total_coherence_exposure > self.coherence_budget` (default `inf`).

The scheduler's exposure uses the same component names:

```
operation_time = sum of every SES node's duration_model
idle_time      = max(0, makespan * distinct_targets - operation_time)
route_time     = sum of planned t_message over the communication plan
measurement_wait = classical_feedback_wait = entanglement_wait = 0.0
total          = operation_time + route_time + idle_time
```

> **Stated limitation.** The scheduler populates only three of the six
> components; `measurement_wait`, `classical_feedback_wait` and
> `entanglement_wait` are structurally present and set to `0.0`. The exposure
> the scheduler reports is therefore a **lower bound**, and an implementation
> **SHALL NOT** present it as a complete accounting. The three missing waits are
> observable elsewhere — `protocols.ClassicalFeedbackFabric` measures message
> latency and delivery, and `planner.q_link_cost` computes ebit setup and
> herald delay — but they are not yet folded into the schedule's coherence
> total.

A blocked schedule is still emitted, with its `blocked` list populated, so the
reason is inspectable; `optimality_claim` and the schedule hash are unaffected.

---

## 7. Error budgets in the scheduler

`TemporalScheduler.schedule` stage `error_budget_validation` sums the declared
`p` values over every SES node and reports:

```json
{"summed_independent_p": <float>,
 "note": "sum is an upper bound only under an independence assumption"}
```

The note is normative. This sum is deliberately **not** run through
`erroralgebra.compose`: it is a fast scheduling-stage figure with its
assumption stated inline. The authoritative composition is the one in
`ERROR_LEDGER.json` / `ERROR_COMPOSITION_LEDGER.json`, which applies the law
registry and can return `UNRESOLVED`.

The same sum feeds the partitioner's cost dimension `estimated_error`
(`HypergraphPartitioner.cost_vector`), weighted `2.0` by default and `16.0`
under the `min_error` Pareto policy.

---

## 8. Exceptions

| Exception | Raised for |
|---|---|
| `ErrorAlgebraError` | base; unknown unit/kind/domain/assumption/distribution/component, out-of-domain value, empty compose, negative exposure, non-positive budget, too few Monte-Carlo samples |
| `UnitMismatchError` | terms with incompatible units forced into a scalar composition |
| `IndependenceUnjustifiedError` | variance addition without a justified independence claim |
| `CorrelationModelMissingError` | correlated terms composed with no covariance model |
| `CoherenceBudgetExceeded` | `require(budget)` when the ceiling is breached |

> **Note for implementers.** `UnitMismatchError`, `IndependenceUnjustifiedError`
> and `CorrelationModelMissingError` are declared and exported but the
> corresponding conditions are currently reported through the
> `CompositionResult` path (token `ERROR_COMPOSITION_UNRESOLVED`) rather than
> raised. The refusal is therefore always visible, but a caller **SHALL** check
> `result.resolved` and **SHALL NOT** rely on an exception to detect it.

---

## 9. Worked examples

```python
from pacore import erroralgebra as ea

# L1 - exact multiplication of independent success probabilities
t1 = ea.ErrorTerm("R012", 0.001, kind="failure_probability",
                  independence="independent")
t2 = ea.ErrorTerm("R013", 0.002, kind="failure_probability",
                  independence="independent")
r = ea.compose([t1, t2], "independent")
# r.resolved is True, r.law_id == "L1", r.token == "EXACT"
# r.combined.value == 1 - (0.999 * 0.998)

# Gate 1 - incompatible dimensions
t3 = ea.ErrorTerm("R020", 1.5e-6, unit="second", kind="duration",
                  independence="independent")
r = ea.compose([t1, t3], "independent")
# r.resolved is False, r.token == "ERROR_COMPOSITION_UNRESOLVED"
# r.dimensions has two keys: "probability/failure_probability", "second/duration"

# Gate 3 - unknown correlation
r = ea.compose([t1, t2], "unknown_correlation")     # UNRESOLVED

# L5 - correlated variances without a rho
a = ea.ErrorTerm("A", 0.04, unit="dimensionless", kind="variance",
                 independence="correlated")
b = ea.ErrorTerm("B", 0.09, unit="dimensionless", kind="variance",
                 independence="correlated")
r = ea.compose([a, b], "correlated")
# UNRESOLVED: "missing [('A', 'B')]"
a.correlation["B"] = 0.5
r = ea.compose([a, b], "correlated")                # resolved, law L5

# Coherence
c = ea.CoherenceExposure("LIN0001")
c.add("operation_time", 8e-6).add("entanglement_wait", 5e-5)
chk = c.check(2e-5)
# chk.ok is False, chk.token == "SCHEDULE_BLOCKED_COHERENCE",
# chk.dominant_component == "entanglement_wait"
```

---

## 10. Implementation status

| Element | Status | Note |
|---|---|---|
| Typed error terms with validation | `OPERATIONAL` | |
| Unit/kind class separation | `OPERATIONAL` | never merged into a false scalar |
| Composition laws `L1`–`L6` | `OPERATIONAL` | every result names its law |
| `ERROR_COMPOSITION_UNRESOLVED` | `OPERATIONAL` | dimensions and reasons preserved |
| Calibration-epoch gate | `OPERATIONAL` | |
| Monte-Carlo propagation | `OPERATIONAL` | seeded, order-stable, assumptions always reported |
| Coherence exposure model (6 components) | `OPERATIONAL` | as a data structure |
| Scheduler coherence accounting | `IMPLEMENTED_PARTIAL` | 3 of 6 components populated; reported total is a lower bound |
| `SCHEDULE_BLOCKED_COHERENCE` | `OPERATIONAL` | emitted by both the algebra and the scheduler |
| Scheduler error-budget stage | `IMPLEMENTED` | naive independent sum, assumption stated inline |
| Dedicated `UnitMismatchError` / `IndependenceUnjustifiedError` / `CorrelationModelMissingError` raising paths | `SCAFFOLDED` | declared and exported; conditions currently reported via the unresolved token |
| Federated (cross-domain) error budgets | `SPECIFIED` | terms carry a domain; no cross-domain aggregation stage exists |
| Hardware-measured error parameters | `BLOCKED` | no authenticated target |
