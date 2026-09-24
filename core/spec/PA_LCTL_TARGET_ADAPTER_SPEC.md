# PA-LCTL Target Adapter Specification

Document: `PA_LCTL_TARGET_ADAPTER_SPEC.md`
Authority: `pacore.cli.cmd_adapter_check`,
`pacore.cli.cmd_qualify_distributed_target`,
`pacore.ledgers.PHYSICAL_RELEASE_OUTPUTS`,
`pacore.ledgers.BLOCKED_EXTERNAL_AUTHORITY`,
`pacore.ledgers.LedgerSet._provenance`, `pacore.lang.TRUST_DOMAINS`,
`pacore.planner.Node`, `pacore.simulator.EXECUTION_LABELS`.

RFC 2119 keywords apply.

> **Status of this document.** Unlike every other specification in this set,
> the subject matter here is **`BLOCKED`**, not `OPERATIONAL`. There is no
> implemented target adapter in `pacore`, and there cannot be one in this
> environment. What *is* implemented, and what this document specifies
> normatively, is the **firewall**: the set of refusals that prevent a physical
> claim from ever being made without external evidence. Sections 2 and 3
> describe an ABI and a feature vocabulary that a future adapter **SHALL**
> implement; sections 4–7 describe code that exists today.

---

## 1. Why there is no adapter

```
BACKEND = none          (pacore.BACKEND)
NETWORK = deny          (pacore.NETWORK)
```

Both are **normative constants**, not configuration. `lang.verify` rejects a
program declaring anything else (`E-POL-001`, `E-POL-002`). No module in
`pacore` opens a socket, resolves a name, or contacts a backend.

A physical quantum target cannot be bound without (a) a network path to it and
(b) an authority that authenticates it. This package has neither, **and it will
not simulate the answer**.

---

## 2. The adapter ABI (`SPECIFIED`)

A conforming target adapter is a component external to `pacore` that presents
the following surface. Nothing in `pacore` implements it; this is the contract
a future implementation **SHALL** satisfy.

### 2.1 Identity and authentication

| Member | Type | Obligation |
|---|---|---|
| `target_id` | str | stable identity of the physical target |
| `authority` | str | the external authority that authenticates the target |
| `trust_domain` | one of `lang.TRUST_DOMAINS` | **SHALL** be `PHYSICAL_TARGET_AUTHENTICATED` for a physical target; any other value means the target is not authenticated |
| `attest()` | → evidence bundle | **SHALL** return the full evidence list of §4; an adapter that cannot **SHALL** return the reason, not a partial bundle |

### 2.2 Capability description

| Member | Type | Obligation |
|---|---|---|
| `topology()` | → `planner.Topology` | the real device graph, with `Node.supported_ops`, `capacity`, `crosstalk_pairs`, `calibration_epoch`, `calibration_valid_until` populated from measurement |
| `feature_class(feature)` | → one of §3 | **SHALL** be stated for every language feature the adapter is asked about; an unstated feature is `UNSUPPORTED` |
| `native_gate_set()` | → set[str] ⊆ `lang.ALL_OPS` | |
| `calibration()` | → epoch + per-qubit/per-pair parameters | **SHALL** carry a timestamp and a validity window |

### 2.3 Execution

| Member | Obligation |
|---|---|
| `submit(qcir_p2, shots, seed)` | **SHALL** accept a QCIR-P2 document and **SHALL** verify `schema == "PA-LCTL/QCIR-P2/1"` |
| `result()` | **SHALL** return a result dict whose `label` is a *physical* label — and therefore **SHALL NOT** use any member of `simulator.EXECUTION_LABELS`, all of which begin `CLASSICAL_` |
| `provenance()` | **SHALL** return a `ledgers.PROVENANCE_FIELDS`-shaped record with `target_verified: True` and the appropriate `physical_*` flags |

### 2.4 Refusal

An adapter **SHALL** fail closed. Every method **SHALL** either return a
complete, evidenced answer or raise with a stated reason. An adapter
**SHALL NOT** return an estimate where a measurement was requested, and
**SHALL NOT** widen a `SUPPORTED_WITH_LIMITS` answer to `SUPPORTED`.

---

## 3. The feature-class vocabulary (`SPECIFIED`)

Five values. An adapter **SHALL** classify every feature it is asked about into
exactly one.

| Class | Meaning | Obligations on the caller |
|---|---|---|
| `SUPPORTED` | The target realizes the feature with its documented semantics, with no caveat. | May be used freely. Results carry the target's own regime. |
| `SUPPORTED_WITH_LIMITS` | Realized, but only inside stated bounds (qubit count, connectivity, angle granularity, shot count, depth). | The caller **SHALL** record the bounds with the result; exceeding a bound is an error, not a degradation. |
| `APPROXIMATE` | Realized only up to a stated approximation, with a stated magnitude. | The result **SHALL** carry a non-exact `REGIME` and **SHALL NOT** be promoted to an exact one (`lang.EXACT_REGIMES`, `E-REG-002`, `E-REG-003`). |
| `TARGET_SPECIFIC` | Realized in a way that is not portable — the semantics depend on this target's calibration or hardware detail. | The result **SHALL** carry `REGIME = TARGET_SPECIFIC` and **SHALL** name the assumptions; it **SHALL NOT** be reused against another target. Mirrors `commutation`'s `COMMUTING_TARGET_CONDITIONAL` / Q6 `ADMIT_TARGET_SPECIFIC`. |
| `UNSUPPORTED` | Not realized. | The caller **SHALL** refuse the program rather than substituting an alternative. Mirrors `REGIME = UNSUPPORTED` and the terminal Q6 verdicts. |

The vocabulary aligns with three existing enumerations, deliberately:

| Feature class | `lang.REGIMES` counterpart | Q6 counterpart |
|---|---|---|
| `SUPPORTED` | `EXACT` family | `ADMIT_EXACT` |
| `SUPPORTED_WITH_LIMITS` | `PIECEWISE_EXACT`, `HARDWARE_CALIBRATED` | `ADMIT_NUMERICALLY_VERIFIED` |
| `APPROXIMATE` | `APPROXIMATE`, `NOISY`, `PERTURBATIVE`, … | `ADMIT_APPROXIMATE` |
| `TARGET_SPECIFIC` | `TARGET_SPECIFIC` | `ADMIT_TARGET_SPECIFIC` |
| `UNSUPPORTED` | `UNSUPPORTED`, `NO_FAITHFUL_FORM`, `IMPOSSIBLE` | `REJECT_*` |

> **Implementation status.** These five tokens do **not** appear as symbols in
> `pacore`. They are `SPECIFIED` here so that an adapter has a fixed vocabulary
> to report against, and so that the mapping above is normative rather than
> improvised. `lang.REGIMES` and `lang.Q6_ADMISSION`, which *are* implemented,
> already cover the same distinctions inside the language.

---

## 4. The physical-evidence firewall

This is the part that exists in code today.

### 4.1 The rule

> **No output may claim physical execution without an external authority that
> holds authenticated hardware, and this software cannot supply that
> authority.**

### 4.2 The full required evidence list

For a claim of physical execution to be admissible, **all** of the following
**SHALL** be present and verifiable. Absence of any one item blocks the claim.

1. **An authenticated target identity.** A `target_id` bound to a
   `PHYSICAL_TARGET_AUTHENTICATED` trust domain by an authority external to the
   compiler. — currently: `target_verified = False`, always.
2. **A calibration record with a validity window.** `calibration_epoch` and
   `calibration_valid_until` measured, not declared, and covering the execution
   epoch (`Topology.calibration_valid`, `E-CAL-001`). — currently:
   `calibration_epoch = 0` and every value is declared.
3. **A device topology derived from the target**, with real `supported_ops`,
   real connectivity and real `crosstalk_pairs`. — currently:
   `planner.reference_topology`, a declared model.
4. **Measured gate and readout error parameters**, tagged
   `hardware_estimate` or better, with a stated measurement procedure. —
   currently: `ledgers.VALUE_PROVENANCE` includes `hardware_estimate`, and
   **nothing in `pacore` ever emits it**.
5. **A measured link fidelity and entanglement-generation record** for every
   quantum link the plan uses, with heralding evidence. — currently:
   `Link.fidelity`, `gen_rate`, `success_prob` are declared inputs; the ebit
   ledger records a logical lifecycle in one process.
6. **A submitted QCIR-P2 document with its hash**, and a returned result that
   references that hash. — the QCIR side exists (`QCIRP2.hash()`); no submission
   path exists.
7. **A result whose execution label is a physical label.** Every label
   `pacore` can emit is in `simulator.EXECUTION_LABELS` and
   `_assert_label_honest` raises unless it starts with `CLASSICAL_` and
   contains none of `QPU`, `HARDWARE`, `PHYSICAL`, `DEVICE`,
   `QUANTUM_EXECUTION`. — currently: **no physical label is expressible**.
8. **A provenance record with `target_verified: True`** and the corresponding
   `physical_qpu` / `physical_parallel` / `physical_distributed` flags set. —
   currently: `LedgerSet._provenance` writes all four as `False`.
9. **A quantum-boundary state above `QUANTUM_BOUNDARY_NOT_CROSSED`.** —
   currently: hard-coded `QUANTUM_BOUNDARY_NOT_CROSSED`.
10. **A parallel/distributed ladder value above the emulation ceiling.**
    `ledgers._cap` clamps `parallel_state` at `PARALLEL_EMULATION` and
    `distributed_state` at `DISTRIBUTED_CLASSICAL_EMULATION`, structurally.
11. **For a distributed physical claim: two authenticated physical endpoints
    and a measured entanglement link between them** (`cmd_qualify_distributed_target`).
12. **Zero-failure conformance evidence** covering the claim
    (`ledgers.LedgerSet._release`).

Items 1–11 are unobtainable here. Item 12 is obtainable and is exactly what
qualifies the four **non-physical** release outputs.

### 4.3 Where the firewall is enforced

| Site | Enforcement |
|---|---|
| `cli.cmd_adapter_check` | **always** raises `CommandBlocked(BLOCKED_EXTERNAL_AUTHORITY)`: *"Binding a physical target requires an external authority with authenticated hardware; this package cannot supply one and will not simulate the answer."* Detail carries `NETWORK`, `BACKEND`, and all four false flags. |
| `cli.cmd_qualify_distributed_target` | **always** raises `CommandBlocked(BLOCKED_EXTERNAL_AUTHORITY)`: *"requires two authenticated physical endpoints and a measured entanglement link. None exist in this environment."* Detail maps both `PHYSICAL_RELEASE_OUTPUTS` to the token. |
| `simulator._assert_label_honest` | raises `SimulationError` for any label outside `EXECUTION_LABELS`, any label not starting `CLASSICAL_`, and any label containing a forbidden token |
| `ledgers.LedgerSet._provenance` | writes `target_verified`, `physical_qpu`, `physical_parallel`, `physical_distributed` as `False` and `quantum_boundary` as `QUANTUM_BOUNDARY_NOT_CROSSED`; raises `LedgerError` if the field set is not exactly `PROVENANCE_FIELDS` |
| `ledgers._cap` | clamps both ladders to their emulation ceilings |
| `ledgers.LedgerSet._release` | sets both `PHYSICAL_RELEASE_OUTPUTS` to `BLOCKED_EXTERNAL_AUTHORITY` unconditionally, with `evidence_groups: []` |
| `ledgers.LedgerSet._resources` | emits `hardware_measured` as `tagged(None, "unknown", "no authenticated physical target is bound; nothing here is measured")` |
| `cli._sim_report` | raises `CommandRejected` if a result label does not start with `CLASSICAL_` |
| `cli.cmd_selfcheck` | asserts `no_physical_claim` and `physical_outputs_blocked`; a failure exits non-zero |
| `conformance` negatives | `physical_execution_claim_from_simulator` → `E-CLAIM-001`; `physical_distributed_claim_without_two_endpoints` → `E-CLAIM-002` |

### 4.4 Exit codes

`cli` returns `EXIT_BLOCKED = 3` for a `CommandBlocked`. A blocked command
**SHALL** exit non-zero; *"a command never prints a success record for work it
did not do"*.

The two blocking tokens are distinct and both normative:

| Token | Meaning | Used by |
|---|---|---|
| `BLOCKED_EXTERNAL_AUTHORITY` | The capability requires an authority outside this software. **Permanent here.** | `adapter-check`, `qualify-distributed-target`, both physical release outputs |
| `BLOCKED_CAPABILITY_ABSENT` | The capability is simply not implemented in this package. | `simulate-tensor` (no tensor-network engine), `simulate-ooc` (no out-of-core engine) |

The distinction matters: `BLOCKED_CAPABILITY_ABSENT` could be lifted by writing
code; `BLOCKED_EXTERNAL_AUTHORITY` could not.

---

## 5. Why every physical output is `BLOCKED_EXTERNAL_AUTHORITY`

`ledgers.PHYSICAL_RELEASE_OUTPUTS`:

* `PHYSICAL_PARALLEL_QPU_EXECUTION`
* `PHYSICAL_DISTRIBUTED_QPU_EXECUTION`

```python
for name in PHYSICAL_RELEASE_OUTPUTS:
    outputs[name] = {
        "status": BLOCKED_EXTERNAL_AUTHORITY,
        "evidence_groups": [],
        "reason": "no authenticated physical quantum target exists in this "
                  "environment; qualification requires an external authority "
                  "that this software cannot supply",
        "evidence_hash": None}
```

There is **no branch** that can produce any other status for these two outputs.
This is not a policy switch; it is the absence of a code path. A conforming
implementation **SHALL NOT** add one that does not consult an external
authority holding the evidence of §4.2.

Note the asymmetry with the other four outputs, which *are* computed from
supplied conformance evidence and *can* be `QUALIFIED`. The firewall blocks
exactly the claims that require hardware, and nothing else.

---

## 6. What a hypothetical adapter would change, and what it would not

If an external authority supplied a conforming adapter, the following would
change:

| Would change | Would not change |
|---|---|
| `target_verified` could become `True` | the tuple schema, the type system, ownership |
| `physical_*` flags could become `True` | the SES, the partial order, `Pmax`'s meaning |
| the parallel/distributed ladders could exceed the emulation ceilings | the honesty rules of `PA_LCTL_NORMATIVE_SPEC.md` §5 |
| execution labels could become physical | the requirement that a label state what it is |
| `hardware_estimate` and `hardware_measured` could carry values | the requirement that every number carry a provenance tag |
| `PHYSICAL_*_QPU_EXECUTION` could be qualified | the requirement of zero-failure conformance evidence |
| `ADMIT_TARGET_SPECIFIC` rules could be acted on | the requirement that their assumptions be recorded and checked |
| `REGIME = HARDWARE_CALIBRATED` would become meaningful | `E-REG-002`/`E-REG-003` |

The language does not change. That is the point of the layering: the firewall
is a property of the *evidence*, not of the semantics.

---

## 7. Implementation status

| Element | Status | Note |
|---|---|---|
| Adapter ABI | `SPECIFIED` | no code; contract stated in §2 |
| Feature-class vocabulary (5 values) | `SPECIFIED` | no code; mapped to `lang.REGIMES` and `lang.Q6_ADMISSION` in §3 |
| Physical-evidence firewall | `OPERATIONAL` | enforced at nine independent sites |
| `BLOCKED_EXTERNAL_AUTHORITY` for both physical outputs | `OPERATIONAL` | no branch produces any other status |
| `adapter-check` | `BLOCKED` | permanently; exits 3 |
| `qualify-distributed-target` | `BLOCKED` | permanently; exits 3 |
| Execution-label honesty | `VERIFIED` | asserted by `cli selfcheck` and by conformance |
| `hardware_estimate` provenance tag | `BLOCKED` | vocabulary member; never emitted |
| `PHYSICAL_TARGET_AUTHENTICATED` trust domain | `BLOCKED` | vocabulary member; unreachable |
| `HARDWARE_CALIBRATED` regime | `SPECIFIED` | accepted in a program; nothing can satisfy it |
| Tensor-network backend | `BLOCKED_CAPABILITY_ABSENT` | distinct from the authority block; could be implemented |
| Out-of-core execution | `BLOCKED_CAPABILITY_ABSENT` | as above |
