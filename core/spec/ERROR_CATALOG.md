<!-- GENERATED FILE - do not edit by hand.
     Produced by tools/gen_catalogs.py from the reference implementation
     in pacore/. Re-run the generator after any change to pacore. -->

# PA-LCTL Error Catalog

Language: **PA-LCTL** &nbsp;&nbsp; Bundle magic: `#PA-LCTL/1.6` &nbsp;&nbsp; Core version: `1.6.0-rc1`

Generated from: `pacore.erroralgebra`, `pacore.simulator`, `pacore.lang`

## Units (`erroralgebra.UNITS`)

`probability`, `log_probability`, `dimensionless`, `second`, `hertz`, `count`, `radian`

## Error kinds (`erroralgebra.ERROR_KINDS`)

`success_probability`, `failure_probability`, `infidelity`, `log_success`, `additive_error`, `variance`, `std_dev`, `duration`, `rate`, `count`

## Error domains (`erroralgebra.DOMAINS`)

`gate`, `readout`, `idle`, `route`, `link`, `entanglement`, `classical`, `compiler`, `sampling`, `model`

## Independence assumptions (`erroralgebra.ASSUMPTIONS`)

`independent`, `correlated`, `unknown_correlation`, `small_error`, `exact_multiplicative`, `exact_logarithmic`

## Composition-law registry (`erroralgebra.COMPOSITION_LAWS`)

| Law | Name | Applies to kinds | Required assumption | Exactness | Statement |
|---|---|---|---|---|---|
| `L1` | `independent_success_multiplication` | `success_probability`, `failure_probability`, `infidelity` | `independent` | `EXACT` | independent success probabilities multiply: P = prod_i p_i; this is exact, not an approximation |
| `L2` | `log_success_addition` | `log_success` | `independent` | `EXACT` | after taking the logarithm, independent log-success terms add exactly: log P = sum_i log p_i |
| `L3` | `additive_small_error` | `additive_error`, `infidelity`, `failure_probability` | `small_error` | `APPROXIMATE` | for small errors, 1 - prod(1-e_i) ~= sum e_i; the neglected second-order magnitude is reported and the result is APPROXIMATE |
| `L4` | `independent_variance_addition` | `variance`, `std_dev` | `independent` | `EXACT` | variances add only under a justified independence assumption: Var = sum_i Var_i |
| `L5` | `correlated_variance_with_covariance` | `variance`, `std_dev` | `correlated` | `EXACT` | correlated variances need a covariance model: Var = sum_i Var_i + 2 sum_{i<j} rho_ij s_i s_j |
| `L6` | `duration_addition` | `duration` | `independent` | `EXACT` | serial durations add exactly: T = sum_i t_i |

## Composition tokens

| Token | Symbol | Meaning |
|---|---|---|
| `EXACT` | erroralgebra.TOKEN_EXACT | the applied law is exact |
| `APPROXIMATE` | erroralgebra.TOKEN_APPROXIMATE | the applied law is an approximation; the neglected second-order magnitude is reported |
| `ERROR_COMPOSITION_UNRESOLVED` | erroralgebra.TOKEN_UNRESOLVED | no admitted law applies; the separate dimensions are preserved and no scalar is produced |
| `SCHEDULE_BLOCKED_COHERENCE` | erroralgebra.TOKEN_SCHEDULE_BLOCKED | coherence exposure exceeds the declared budget |
| `SCHEDULE_ADMITTED_COHERENCE` | erroralgebra.TOKEN_SCHEDULE_ADMITTED | coherence exposure is within the declared budget |

## Coherence exposure components (`erroralgebra.EXPOSURE_COMPONENTS`)

`operation_time`, `idle_time`, `route_time`, `measurement_wait`, `classical_feedback_wait`, `entanglement_wait`

## Monte-Carlo input distributions (`erroralgebra.DISTRIBUTIONS`)

`normal`, `uniform`, `lognormal`, `beta`, `point`

## Implemented noise channels (`simulator.kraus_operators`)

| Channel | Status | Kraus rank | Notes |
|---|---|---|---|
| `DEPOLARIZE` | implemented | 4 | single-qubit (2x2) |
| `DEPHASE` | implemented | 2 | single-qubit (2x2) |
| `BIT_FLIP` | implemented | 2 | single-qubit (2x2) |
| `PHASE_FLIP` | implemented | 2 | single-qubit (2x2) |
| `AMPLITUDE_DAMP` | implemented | 2 | single-qubit (2x2) |
| `PHASE_DAMP` | implemented | 2 | single-qubit (2x2) |
| `PAULI_CHANNEL` | implemented | 4 | single-qubit (2x2) |
| `KRAUS_CHANNEL` | implemented | 1 | caller-supplied operator list; completeness is still verified |
| `READOUT_ERROR` | NOT implemented | - | `ValueError`: READOUT_ERROR is not an admitted Kraus channel |
| `LEAKAGE_MODEL` | NOT implemented | - | `ValueError`: LEAKAGE_MODEL is not an admitted Kraus channel |
| `CROSSTALK_MODEL` | NOT implemented | - | `ValueError`: CROSSTALK_MODEL is not an admitted Kraus channel |

Kraus completeness tolerance: `1e-10` (`simulator.KRAUS_COMPLETENESS_TOL`). A set violating `max|sum K^dag K - I| <= tol` raises `ValueError` naming the channel; nothing is renormalized.

## Numerical pass thresholds (`pacore.simulator`)

| Constant | Value | Applies to |
|---|---|---|
| `MAX_AMPLITUDE_ERROR_TOL` | 1e-09 | max_i \|a_i - b_i\| between amplitude vectors |
| `DENSITY_FROBENIUS_TOL` | 1e-09 | \|\|A - B\|\|_F between density matrices |
| `TRACE_TOL` | 1e-09 | trace, hermiticity and PSD validation of rho |
| `STATE_FIDELITY_TOL` | 1e-09 | state fidelity comparisons |
| `DISTRIBUTION_TV_TOL` | 1e-09 | total variation between outcome distributions |
| `KRAUS_COMPLETENESS_TOL` | 1e-10 | Kraus completeness (structural, not convergence) |
| `PROBABILITY_FLOOR` | 1e-15 | probabilities below this are omitted from reports |
| `STATE_HASH_DECIMALS` | 12 | amplitude rounding used for the reproducible state hash |
| `protocols.PROTOCOL_FIDELITY_TOL` | 1e-09 | protocol reference-equivalence pass threshold |
| `commutation.MATRIX_COMMUTATOR_TOL` | 1e-12 | bounded matrix commutator oracle |
| `conformance.MATRIX_TOL` | 1e-09 | declared-matrix admission checks |

## Exactness regimes (`lang.REGIMES`)

| Regime | Exact? |
|---|---|
| `EXACT` | yes |
| `EXACT_LINEAR` | yes |
| `PIECEWISE_EXACT` | yes |
| `PERTURBATIVE` | no |
| `LINEARIZED` | no |
| `VARIATIONAL` | no |
| `STOCHASTIC` | no |
| `ASYMPTOTIC` | no |
| `APPROXIMATE` | no |
| `NOISY` | no |
| `HARDWARE_CALIBRATED` | no |
| `TARGET_SPECIFIC` | no |
| `NO_FAITHFUL_FORM` | no |
| `UNSUPPORTED` | no |
| `IMPOSSIBLE` | no |

A row whose `REGIME` is exact and whose `OP` is in `lang.OPS_NOISE` is rejected with `E-REG-002`; an exact row carrying `approx=true` in `ERROR` is rejected with `E-REG-003`.
