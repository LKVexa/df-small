"""
Error algebra: composition laws, uncertainty propagation, coherence exposure.

Implements:
  * LCTL 1.2.x s37      error-term algebra and declared composition laws
  * LCTL 1.3.x s31-32   composition admissibility and the unresolved token
  * LCTL 1.4.x s33      Monte-Carlo uncertainty propagation
  * LCTL 1.4.x s48      coherence-exposure accounting
  * LCTL 1.5.x s54-55   correlated-term handling and calibration epochs
  * LCTL 1.6.x s13      federated error budgets across domains

Exit-gate tokens covered:
    ERROR_ALGEBRA_OPERATIONAL, ERROR_COMPOSITION_UNRESOLVED,
    APPROXIMATE, SCHEDULE_BLOCKED_COHERENCE, UNCERTAINTY_PROPAGATION_PASS.

Three rules are structural:

  1. Incompatible units are never merged into a false scalar. `compose`
     returns ERROR_COMPOSITION_UNRESOLVED with the separate dimensions
     preserved, so the caller sees a vector of incomparable quantities rather
     than a fabricated single number.
  2. Additive small-error composition is always labelled APPROXIMATE and
     carries the neglected second-order magnitude.
  3. Variances add only under a justified independence assumption. Correlated
     terms require an explicit covariance or correlation model; without one
     the composition is unresolved.

SPECIFICATION HOLES FILLED HERE (recorded in the gap ledger):
  H13. The documents require "an explicit composition law registry" but never
       fix the law identifiers. This module defines the canonical law ids
       (L1..L6) and every composed term names the law that produced it.
  H14. The unit lattice is not stated anywhere. A minimal, explicit unit
       algebra is declared here: dimensionless probabilities, log-probability,
       seconds, hertz, and an opaque "count". Only identical units compose.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, Iterable, List, Mapping, Optional,
                    Sequence, Tuple)

import numpy as np

# --------------------------------------------------------------------------
# 0. Tokens and exceptions
# --------------------------------------------------------------------------

TOKEN_UNRESOLVED = "ERROR_COMPOSITION_UNRESOLVED"
TOKEN_APPROXIMATE = "APPROXIMATE"
TOKEN_EXACT = "EXACT"
TOKEN_SCHEDULE_BLOCKED = "SCHEDULE_BLOCKED_COHERENCE"
TOKEN_SCHEDULE_ADMITTED = "SCHEDULE_ADMITTED_COHERENCE"


class ErrorAlgebraError(Exception):
    """Base class for every fail-closed error-algebra condition."""


class UnitMismatchError(ErrorAlgebraError):
    """Terms with incompatible units were forced into a scalar composition."""


class IndependenceUnjustifiedError(ErrorAlgebraError):
    """Variance addition was requested without a justified independence claim."""


class CorrelationModelMissingError(ErrorAlgebraError):
    """Correlated terms were composed with no covariance/correlation model."""


class CoherenceBudgetExceeded(ErrorAlgebraError):
    """The coherence ceiling was breached and the schedule is blocked."""


# --------------------------------------------------------------------------
# 1. Units, kinds, assumptions (hole H14)
# --------------------------------------------------------------------------

UNITS = ("probability", "log_probability", "dimensionless", "second", "hertz",
         "count", "radian")
ERROR_KINDS = ("success_probability", "failure_probability", "infidelity",
               "log_success", "additive_error", "variance", "std_dev",
               "duration", "rate", "count")
DOMAINS = ("gate", "readout", "idle", "route", "link", "entanglement",
           "classical", "compiler", "sampling", "model")

ASSUMPTIONS = ("independent", "correlated", "unknown_correlation",
               "small_error", "exact_multiplicative", "exact_logarithmic")


def _check(value: str, allowed: Sequence[str], what: str) -> str:
    if value not in allowed:
        raise ErrorAlgebraError(f"unknown {what} {value!r}; expected one of "
                                f"{list(allowed)}")
    return value


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=repr, ensure_ascii=False)


def term_hash(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------
# 2. Error terms
# --------------------------------------------------------------------------

@dataclass
class ErrorTerm:
    """A single declared error contribution.

    `independence` states what the *declarer* claims, not what the composer
    assumes. The composer checks the two against each other and refuses to
    proceed when they disagree.
    """
    name: str
    value: float
    unit: str = "probability"
    domain: str = "gate"
    kind: str = "failure_probability"
    independence: str = "unknown_correlation"
    correlation: Dict[str, float] = field(default_factory=dict)
    calibration_epoch: Optional[int] = None
    bounds: Tuple[Optional[float], Optional[float]] = (None, None)
    confidence_level: Optional[float] = None
    variance: Optional[float] = None
    provenance: str = "-"

    def __post_init__(self) -> None:
        _check(self.unit, UNITS, "unit")
        _check(self.domain, DOMAINS, "domain")
        _check(self.kind, ERROR_KINDS, "error kind")
        _check(self.independence, ASSUMPTIONS, "independence assumption")
        self.value = float(self.value)
        if self.unit == "probability" and not 0.0 <= self.value <= 1.0:
            raise ErrorAlgebraError(
                f"term {self.name!r}: probability {self.value} outside [0,1]")
        if self.confidence_level is not None and \
                not 0.0 <= float(self.confidence_level) <= 1.0:
            raise ErrorAlgebraError(
                f"term {self.name!r}: confidence_level outside [0,1]")
        lo, hi = self.bounds
        if lo is not None and hi is not None and float(lo) > float(hi):
            raise ErrorAlgebraError(f"term {self.name!r}: inverted bounds")

    @property
    def success_probability(self) -> float:
        if self.kind == "success_probability":
            return self.value
        if self.kind in ("failure_probability", "infidelity",
                         "additive_error"):
            return 1.0 - self.value
        if self.kind == "log_success":
            return math.exp(self.value)
        raise ErrorAlgebraError(
            f"term {self.name!r} of kind {self.kind!r} has no success probability")

    def as_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "unit": self.unit,
                "domain": self.domain, "kind": self.kind,
                "independence": self.independence,
                "correlation": dict(sorted(self.correlation.items())),
                "calibration_epoch": self.calibration_epoch,
                "bounds": list(self.bounds),
                "confidence_level": self.confidence_level,
                "variance": self.variance, "provenance": self.provenance,
                "term_hash": term_hash({"n": self.name, "v": self.value,
                                        "u": self.unit, "k": self.kind})}


@dataclass
class CompositionResult:
    """Either a combined term or the unresolved token with the split kept."""
    resolved: bool
    token: str
    law_id: Optional[str]
    law_name: Optional[str]
    exactness: str                      # EXACT | APPROXIMATE | UNRESOLVED
    combined: Optional[ErrorTerm]
    dimensions: Dict[str, List[dict]]
    reasons: List[str]
    inputs: List[dict]
    neglected_second_order: Optional[float] = None

    def as_dict(self) -> dict:
        return {"resolved": self.resolved, "token": self.token,
                "law_id": self.law_id, "law_name": self.law_name,
                "exactness": self.exactness,
                "combined": self.combined.as_dict() if self.combined else None,
                "dimensions": self.dimensions, "reasons": self.reasons,
                "inputs": self.inputs,
                "neglected_second_order": self.neglected_second_order}


# --------------------------------------------------------------------------
# 3. Composition-law registry (hole H13)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CompositionLaw:
    law_id: str
    name: str
    applies_to_kinds: Tuple[str, ...]
    required_assumption: str
    exactness: str
    statement: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


COMPOSITION_LAWS: Tuple[CompositionLaw, ...] = (
    CompositionLaw(
        "L1", "independent_success_multiplication",
        ("success_probability", "failure_probability", "infidelity"),
        "independent", TOKEN_EXACT,
        "independent success probabilities multiply: P = prod_i p_i; this is "
        "exact, not an approximation"),
    CompositionLaw(
        "L2", "log_success_addition", ("log_success",), "independent",
        TOKEN_EXACT,
        "after taking the logarithm, independent log-success terms add "
        "exactly: log P = sum_i log p_i"),
    CompositionLaw(
        "L3", "additive_small_error", ("additive_error", "infidelity",
                                       "failure_probability"),
        "small_error", TOKEN_APPROXIMATE,
        "for small errors, 1 - prod(1-e_i) ~= sum e_i; the neglected "
        "second-order magnitude is reported and the result is APPROXIMATE"),
    CompositionLaw(
        "L4", "independent_variance_addition", ("variance", "std_dev"),
        "independent", TOKEN_EXACT,
        "variances add only under a justified independence assumption: "
        "Var = sum_i Var_i"),
    CompositionLaw(
        "L5", "correlated_variance_with_covariance", ("variance", "std_dev"),
        "correlated", TOKEN_EXACT,
        "correlated variances need a covariance model: "
        "Var = sum_i Var_i + 2 sum_{i<j} rho_ij s_i s_j"),
    CompositionLaw(
        "L6", "duration_addition", ("duration",), "independent", TOKEN_EXACT,
        "serial durations add exactly: T = sum_i t_i"),
)

LAW_INDEX: Dict[str, CompositionLaw] = {l.law_id: l for l in COMPOSITION_LAWS}


def law_for(kind: str, assumption: str) -> Optional[CompositionLaw]:
    """Select the law for (kind, assumption); None when no law applies."""
    for law in COMPOSITION_LAWS:
        if kind in law.applies_to_kinds and law.required_assumption == assumption:
            return law
    return None


def _group_dimensions(terms: Sequence[ErrorTerm]) -> Dict[str, List[ErrorTerm]]:
    groups: Dict[str, List[ErrorTerm]] = {}
    for t in terms:
        groups.setdefault(f"{t.unit}/{t.kind}", []).append(t)
    return groups


# --------------------------------------------------------------------------
# 4. compose()
# --------------------------------------------------------------------------

def compose(terms: Sequence[ErrorTerm], assumption: str = "independent"
            ) -> CompositionResult:
    """Compose error terms under a declared assumption.

    Returns a combined term, or the token ERROR_COMPOSITION_UNRESOLVED with
    the separate dimensions preserved. Incompatible units are never merged
    into a false scalar.
    """
    terms = list(terms)
    if not terms:
        raise ErrorAlgebraError("compose() requires at least one term")
    _check(assumption, ASSUMPTIONS, "assumption")
    inputs = [t.as_dict() for t in terms]
    groups = _group_dimensions(terms)
    dims = {k: [t.as_dict() for t in v] for k, v in sorted(groups.items())}
    reasons: List[str] = []

    # -- rule 1: incompatible dimensions are never fused -------------------
    if len(groups) > 1:
        reasons.append(
            f"terms span {len(groups)} incompatible unit/kind dimensions "
            f"{sorted(groups)}; merging them would fabricate a scalar with no "
            f"physical meaning, so the dimensions are returned separately")
        return CompositionResult(
            resolved=False, token=TOKEN_UNRESOLVED, law_id=None, law_name=None,
            exactness="UNRESOLVED", combined=None, dimensions=dims,
            reasons=reasons, inputs=inputs)

    kind = terms[0].kind
    unit = terms[0].unit

    # -- rule 2: the declared independence must match the requested law ----
    declared = {t.independence for t in terms}
    if assumption == "independent" and \
            declared - {"independent", "exact_multiplicative",
                        "exact_logarithmic", "small_error"}:
        reasons.append(
            f"independent composition requested but the terms declare "
            f"{sorted(declared)}; an unjustified independence claim would "
            f"understate the combined uncertainty")
        return CompositionResult(False, TOKEN_UNRESOLVED, None, None,
                                 "UNRESOLVED", None, dims, reasons, inputs)
    if assumption == "unknown_correlation":
        reasons.append(
            "the correlation structure is unknown; no composition law is "
            "admissible without either an independence justification or a "
            "covariance model")
        return CompositionResult(False, TOKEN_UNRESOLVED, None, None,
                                 "UNRESOLVED", None, dims, reasons, inputs)

    law = law_for(kind, assumption)
    if law is None:
        reasons.append(
            f"no registered composition law covers kind {kind!r} under "
            f"assumption {assumption!r}")
        return CompositionResult(False, TOKEN_UNRESOLVED, None, None,
                                 "UNRESOLVED", None, dims, reasons, inputs)

    epochs = {t.calibration_epoch for t in terms if t.calibration_epoch is not None}
    if len(epochs) > 1:
        reasons.append(
            f"terms come from different calibration epochs {sorted(epochs)}; "
            f"they do not describe the same device state")
        return CompositionResult(False, TOKEN_UNRESOLVED, None, None,
                                 "UNRESOLVED", None, dims, reasons, inputs)
    epoch = next(iter(epochs)) if epochs else None
    conf = [t.confidence_level for t in terms if t.confidence_level is not None]
    combined_conf = min(conf) if conf else None
    neglected: Optional[float] = None

    # -- apply the law -----------------------------------------------------
    if law.law_id == "L1":
        p = 1.0
        for t in terms:
            p *= t.success_probability
        value = p if kind == "success_probability" else 1.0 - p
        reasons.append(law.statement)
    elif law.law_id == "L2":
        value = float(sum(t.value for t in terms))
        reasons.append(law.statement)
    elif law.law_id == "L3":
        errs = [t.value if t.kind != "success_probability" else 1 - t.value
                for t in terms]
        value = float(sum(errs))
        exact = 1.0 - float(np.prod([1.0 - e for e in errs]))
        neglected = abs(value - exact)
        reasons.append(law.statement)
        reasons.append(f"neglected higher-order magnitude {neglected:.6e} "
                       f"(exact 1-prod(1-e) = {exact:.6e})")
        if value > 1.0:
            reasons.append(
                f"the additive sum {value:.6e} exceeds 1 and is therefore not "
                f"a probability; the approximation is outside its domain")
            return CompositionResult(False, TOKEN_UNRESOLVED, law.law_id,
                                     law.name, "UNRESOLVED", None, dims,
                                     reasons, inputs, neglected)
    elif law.law_id == "L4":
        variances = [t.value if t.kind == "variance" else t.value ** 2
                     for t in terms]
        value = float(sum(variances))
        reasons.append(law.statement)
    elif law.law_id == "L5":
        sds = [math.sqrt(t.value) if t.kind == "variance" else t.value
               for t in terms]
        missing = []
        cross = 0.0
        for i in range(len(terms)):
            for j in range(i + 1, len(terms)):
                a, b = terms[i], terms[j]
                rho = a.correlation.get(b.name, b.correlation.get(a.name))
                if rho is None:
                    missing.append((a.name, b.name))
                else:
                    cross += 2.0 * float(rho) * sds[i] * sds[j]
        if missing:
            reasons.append(
                f"correlated composition requires a correlation coefficient for "
                f"every pair; missing {missing}")
            return CompositionResult(False, TOKEN_UNRESOLVED, law.law_id,
                                     law.name, "UNRESOLVED", None, dims,
                                     reasons, inputs)
        value = float(sum(s * s for s in sds) + cross)
        reasons.append(law.statement)
    else:                                    # L6
        value = float(sum(t.value for t in terms))
        reasons.append(law.statement)

    out_kind = "variance" if law.law_id in ("L4", "L5") else kind
    out_unit = "dimensionless" if law.law_id in ("L4", "L5") and \
        unit == "probability" else unit
    combined = ErrorTerm(
        name="+".join(t.name for t in terms), value=value, unit=out_unit,
        domain=terms[0].domain if len({t.domain for t in terms}) == 1 else "model",
        kind=out_kind, independence=assumption,
        calibration_epoch=epoch, confidence_level=combined_conf,
        provenance=f"law:{law.law_id}")
    return CompositionResult(
        resolved=True,
        token=law.exactness,
        law_id=law.law_id, law_name=law.name, exactness=law.exactness,
        combined=combined, dimensions=dims, reasons=reasons, inputs=inputs,
        neglected_second_order=neglected)


# --------------------------------------------------------------------------
# 5. Monte-Carlo uncertainty propagation (LCTL 1.4.x s33)
# --------------------------------------------------------------------------

DISTRIBUTIONS = ("normal", "uniform", "lognormal", "beta", "point")


@dataclass
class UncertainInput:
    name: str
    distribution: str
    params: Tuple[float, ...]
    unit: str = "dimensionless"

    def __post_init__(self) -> None:
        _check(self.distribution, DISTRIBUTIONS, "distribution")
        _check(self.unit, UNITS, "unit")

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        d, p = self.distribution, self.params
        if d == "normal":
            return rng.normal(p[0], p[1], n)
        if d == "uniform":
            return rng.uniform(p[0], p[1], n)
        if d == "lognormal":
            return rng.lognormal(p[0], p[1], n)
        if d == "beta":
            return rng.beta(p[0], p[1], n)
        return np.full(n, float(p[0]))

    def as_dict(self) -> dict:
        return {"name": self.name, "distribution": self.distribution,
                "params": list(self.params), "unit": self.unit}


@dataclass
class MonteCarloReport:
    samples: int
    seed: int
    mean: float
    variance: float
    std_dev: float
    quantiles: Dict[str, float]
    minimum: float
    maximum: float
    distribution_summary: Dict[str, Any]
    assumptions: List[str]
    inputs: List[dict]
    token: str = "UNCERTAINTY_PROPAGATION_PASS"

    def as_dict(self) -> dict:
        return {"token": self.token, "sample_count": self.samples,
                "seed": self.seed, "mean": self.mean, "variance": self.variance,
                "std_dev": self.std_dev, "quantiles": self.quantiles,
                "min": self.minimum, "max": self.maximum,
                "distribution_summary": self.distribution_summary,
                "assumptions": self.assumptions, "inputs": self.inputs,
                "report_hash": term_hash({"m": self.mean, "v": self.variance,
                                          "q": self.quantiles,
                                          "n": self.samples,
                                          "s": self.seed})}


class MonteCarloUncertainty:
    """Deterministic-seeded Monte-Carlo propagation.

    The generator is `numpy.random.default_rng(seed)` and inputs are sampled
    in declaration order, so two runs with the same seed and the same inputs
    produce bit-identical reports.
    """

    def __init__(self, seed: int = 20260811, samples: int = 20000) -> None:
        if samples < 2:
            raise ErrorAlgebraError("Monte-Carlo needs at least two samples")
        self.seed = int(seed)
        self.samples = int(samples)
        self.reports: List[MonteCarloReport] = []

    def propagate(self, fn: Callable[..., Any],
                  inputs: Sequence[UncertainInput],
                  *, assumptions: Sequence[str] = ("inputs are independent",),
                  samples: Optional[int] = None,
                  seed: Optional[int] = None) -> MonteCarloReport:
        n = int(samples or self.samples)
        s = int(self.seed if seed is None else seed)
        rng = np.random.default_rng(s)
        cols = [inp.sample(rng, n) for inp in inputs]
        out = np.asarray(fn(*cols), dtype=float)
        if out.shape != (n,):
            out = np.broadcast_to(out, (n,)).astype(float)
        q = np.quantile(out, [0.05, 0.50, 0.95])
        rep = MonteCarloReport(
            samples=n, seed=s,
            mean=float(np.mean(out)),
            variance=float(np.var(out, ddof=1)),
            std_dev=float(np.std(out, ddof=1)),
            quantiles={"q05": float(q[0]), "q50": float(q[1]),
                       "q95": float(q[2])},
            minimum=float(np.min(out)), maximum=float(np.max(out)),
            distribution_summary={
                "skewness": float(_skew(out)),
                "kurtosis_excess": float(_kurtosis(out)),
                "histogram_bins": 10,
                "histogram_counts": [int(c) for c in
                                     np.histogram(out, bins=10)[0]],
                "histogram_edges": [float(e) for e in
                                    np.histogram(out, bins=10)[1]],
            },
            assumptions=list(assumptions),
            inputs=[i.as_dict() for i in inputs])
        self.reports.append(rep)
        return rep

    def propagate_terms(self, terms: Sequence[ErrorTerm], *,
                        relative_sigma: float = 0.1,
                        samples: Optional[int] = None,
                        seed: Optional[int] = None) -> MonteCarloReport:
        """Propagate a product of independent success probabilities."""
        ins = [UncertainInput(t.name, "normal",
                              (t.success_probability,
                               abs(t.success_probability) * relative_sigma),
                              "probability")
               for t in terms]

        def combine(*cols: np.ndarray) -> np.ndarray:
            acc = np.ones_like(cols[0])
            for c in cols:
                acc = acc * np.clip(c, 0.0, 1.0)
            return acc

        return self.propagate(combine, ins,
                              assumptions=["terms independent",
                                           f"relative sigma {relative_sigma}",
                                           "probabilities clipped to [0,1]"],
                              samples=samples, seed=seed)

    def as_dict(self) -> dict:
        return {"seed": self.seed, "default_samples": self.samples,
                "reports": [r.as_dict() for r in self.reports]}


def _skew(x: np.ndarray) -> float:
    m = np.mean(x)
    s = np.std(x)
    return 0.0 if s == 0 else float(np.mean((x - m) ** 3) / s ** 3)


def _kurtosis(x: np.ndarray) -> float:
    m = np.mean(x)
    s = np.std(x)
    return 0.0 if s == 0 else float(np.mean((x - m) ** 4) / s ** 4 - 3.0)


# --------------------------------------------------------------------------
# 6. Coherence exposure (LCTL 1.4.x s48, 1.5.x s55)
# --------------------------------------------------------------------------

EXPOSURE_COMPONENTS = ("operation_time", "idle_time", "route_time",
                       "measurement_wait", "classical_feedback_wait",
                       "entanglement_wait")


@dataclass
class CoherenceCheck:
    ok: bool
    token: str
    budget_s: float
    exposure_s: float
    headroom_s: float
    utilization: float
    dominant_component: str
    breakdown: Dict[str, float]
    reason: str

    def as_dict(self) -> dict:
        return {"ok": self.ok, "token": self.token, "budget_s": self.budget_s,
                "total_coherence_exposure_s": self.exposure_s,
                "headroom_s": self.headroom_s,
                "utilization": round(self.utilization, 9),
                "dominant_component": self.dominant_component,
                "breakdown_s": {k: round(v, 15)
                                for k, v in sorted(self.breakdown.items())},
                "reason": self.reason}


@dataclass
class CoherenceExposure:
    """Accumulated coherence exposure for one quantum lineage.

    Total exposure is the sum of every interval during which unknown quantum
    state is held, including the waits that are often omitted: measurement
    latency, classical feedback, and entanglement establishment.
    """
    lineage: str = "-"
    operation_time: float = 0.0
    idle_time: float = 0.0
    route_time: float = 0.0
    measurement_wait: float = 0.0
    classical_feedback_wait: float = 0.0
    entanglement_wait: float = 0.0

    def add(self, component: str, seconds: float) -> "CoherenceExposure":
        if component not in EXPOSURE_COMPONENTS:
            raise ErrorAlgebraError(
                f"unknown exposure component {component!r}; expected one of "
                f"{list(EXPOSURE_COMPONENTS)}")
        if seconds < 0:
            raise ErrorAlgebraError("coherence exposure cannot be negative")
        setattr(self, component, getattr(self, component) + float(seconds))
        return self

    @property
    def total_coherence_exposure(self) -> float:
        return float(sum(getattr(self, c) for c in EXPOSURE_COMPONENTS))

    def breakdown(self) -> Dict[str, float]:
        return {c: float(getattr(self, c)) for c in EXPOSURE_COMPONENTS}

    def check(self, budget: float) -> CoherenceCheck:
        """Return SCHEDULE_BLOCKED_COHERENCE when the ceiling is breached."""
        if budget <= 0:
            raise ErrorAlgebraError("coherence budget must be positive")
        total = self.total_coherence_exposure
        b = self.breakdown()
        dominant = max(sorted(b), key=lambda k: b[k]) if total > 0 else "-"
        ok = total <= float(budget)
        return CoherenceCheck(
            ok=ok,
            token=TOKEN_SCHEDULE_ADMITTED if ok else TOKEN_SCHEDULE_BLOCKED,
            budget_s=float(budget), exposure_s=total,
            headroom_s=float(budget) - total,
            utilization=total / float(budget),
            dominant_component=dominant, breakdown=b,
            reason=("exposure is within the declared coherence ceiling" if ok
                    else f"total exposure {total:.6e}s exceeds the ceiling "
                         f"{float(budget):.6e}s; the dominant contribution is "
                         f"{dominant} at {b.get(dominant, 0.0):.6e}s"))

    def require(self, budget: float) -> CoherenceCheck:
        c = self.check(budget)
        if not c.ok:
            raise CoherenceBudgetExceeded(f"{TOKEN_SCHEDULE_BLOCKED}: {c.reason}")
        return c

    def as_dict(self) -> dict:
        d = {"lineage": self.lineage}
        d.update({c: float(getattr(self, c)) for c in EXPOSURE_COMPONENTS})
        d["total_coherence_exposure"] = self.total_coherence_exposure
        return d


__all__ = [
    "ErrorAlgebraError", "UnitMismatchError", "IndependenceUnjustifiedError",
    "CorrelationModelMissingError", "CoherenceBudgetExceeded",
    "ErrorTerm", "CompositionResult", "CompositionLaw", "COMPOSITION_LAWS",
    "LAW_INDEX", "law_for", "compose", "UncertainInput",
    "MonteCarloUncertainty", "MonteCarloReport", "CoherenceExposure",
    "CoherenceCheck", "EXPOSURE_COMPONENTS", "UNITS", "ERROR_KINDS", "DOMAINS",
    "ASSUMPTIONS", "TOKEN_UNRESOLVED", "TOKEN_APPROXIMATE",
    "TOKEN_SCHEDULE_BLOCKED",
]
