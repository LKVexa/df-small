# PA-LCTL Distributed Quantum Protocol Specification

Document: `PA_LCTL_PROTOCOL_SPEC.md`
Authority: `pacore.protocols` — `EBIT_TRANSITIONS`, `EbitRecord`, `EbitLedger`,
`OwnershipTransfer`, `teleport`, `remote_cnot`, `entanglement_swap`, `purify`,
`ClassicalFeedbackFabric`, `ProtocolStep`, `ProtocolPlan`, `ProtocolCompiler`.

RFC 2119 keywords apply.

---

## 1. The honesty invariants

Stated in the module header and enforced at every call site:

1. **Every protocol result is produced by running `pacore.simulator` engines on
   the real state, never by asserting a textbook outcome.** The Bell outcome is
   a Born-rule draw; the Pauli correction is applied from the drawn classical
   bits; the destination amplitudes are read back out of the collapsed
   register; the fidelity is measured against the input.
2. **Every fail-closed condition raises `ProtocolError`. No lifecycle
   transition is ever silently coerced.**
3. **No routine in this module contacts a network or a device.** The "nodes"
   and "links" are logical names inside a single deterministic process.

---

## 2. The ebit lifecycle

### 2.1 States

`lang.EPR_STATES` — eight values:

```
REQUESTED, GENERATING, HERALDED, RESERVED, CONSUMED, EXPIRED, FAILED, RELEASED
```

`protocols.USABLE_EBIT_STATES = ("HERALDED", "RESERVED")` — **only these two
carry confirmed entanglement**. `TERMINAL_EBIT_STATES` is derived as every
state with no admitted successor: `CONSUMED`, `EXPIRED`, `FAILED`, `RELEASED`.

### 2.2 The transition table

`protocols.EBIT_TRANSITIONS` — anything not listed fails closed.

| From | Admitted next states |
|---|---|
| `REQUESTED` | `GENERATING`, `FAILED`, `EXPIRED` |
| `GENERATING` | `HERALDED`, `FAILED`, `EXPIRED` |
| `HERALDED` | `RESERVED`, `CONSUMED`, `RELEASED`, `EXPIRED` |
| `RESERVED` | `CONSUMED`, `RELEASED`, `EXPIRED` |
| `CONSUMED` | (terminal) |
| `EXPIRED` | (terminal) |
| `FAILED` | (terminal) |
| `RELEASED` | (terminal) |

```
REQUESTED ──▶ GENERATING ──▶ HERALDED ──▶ RESERVED ──▶ CONSUMED
    │              │             │  │          │
    ├──▶ FAILED    ├──▶ FAILED   │  ├──▶ RELEASED ◀──┤
    └──▶ EXPIRED   └──▶ EXPIRED  │  └──▶ EXPIRED ◀───┘
                                 └──▶ CONSUMED
```

The module asserts coverage at import time:

```python
if set(EBIT_TRANSITIONS) != set(EPR_STATES):
    raise ProtocolError("the ebit transition table does not cover "
                        "lang.EPR_STATES exactly: ...")
```

so the language vocabulary and the runtime table cannot diverge.

### 2.3 The record

`protocols.EbitRecord` — *"Field names are normative"* (LCTL 1.3.x §23):

`ebit_id`, `endpoint_a`, `endpoint_b`, `creation_epoch`, `fidelity`, `expiry`,
`owner_protocol`, `state`, `provenance`.

`endpoints()` returns the sorted pair, so `(N0, N1)` and `(N1, N0)` are the
same link. `provenance` is a list of `{from, to, epoch, note}` records — **every
transition is recorded**, so an ebit's whole history is reconstructible.

### 2.4 Operations and their refusals

| Method | Refuses when | Message |
|---|---|---|
| `request(a, b, ...)` | `a == b` | *"an ebit needs two distinct endpoints"* |
| | `expiry < epoch` | *"expiry precedes creation epoch"* |
| | fidelity outside [0,1] | *"target fidelity outside [0,1]"* |
| `generate(id)` | state ≠ `REQUESTED` | illegal transition, listing admitted next states |
| `herald(id, fidelity)` | fidelity outside [0,1] | *"heralded fidelity outside [0,1]"* |
| | state ≠ `GENERATING` | illegal transition |
| `reserve(id, owner)` | state ≠ `HERALDED` | *"only a HERALDED ebit carries confirmed entanglement"* |
| | `epoch > expiry` | *"expired at epoch E; the request is at epoch F"* |
| `consume(id)` | state = `CONSUMED` | ***"double consume of ebit X: entanglement is not a copyable resource (LCTL 1.1.x s6)"*** |
| | state = `EXPIRED` | *"expired at epoch E and cannot be consumed"* |
| | state not usable | *"cannot consume ebit X in state S"* |
| `release(id)` | state terminal | illegal transition |
| `expire(id=None)` | — | expires one ebit, or **every** non-terminal ebit past its expiry, in sorted id order |

`inventory(a, b, epoch)` returns usable, unexpired ebit ids between two nodes,
in creation order. `counts()` returns a count per state, over all eight states.

`as_dict()` emits `ENTANGLEMENT_INVENTORY_LEDGER` with the admitted states, the
whole transition table, every record, the state counts and the event list.

---

## 3. Teleportation

`protocols.teleport(state_amplitudes, ebit_ledger, ebit_id, src_node,
dst_node, epoch=0, seed=0)`

### 3.1 Preconditions (all fail closed)

* the ebit is `HERALDED` or `RESERVED`;
* the ebit's endpoints are exactly `{src_node, dst_node}`;
* the payload is a **single-qubit** state (2 amplitudes) with non-zero norm.

### 3.2 The executed circuit

Register `["q_src", "q_ea", "q_eb"]`, statevector engine, seeded:

```
set_single_qubit_state("q_src", normalized source)   # refuses if q_src is not |0>
H  q_ea                                              # build |Phi+> on (ea, eb)
CX q_ea, q_eb
CX q_src, q_ea                                       # Bell measurement basis
H  q_src
m_src = measure(q_src, "Z")                          # Born draw
m_ea  = measure(q_ea,  "Z")                          # Born draw
if m_ea: X q_eb ; correction += "X"
if m_src: Z q_eb ; correction += "Z"
```

`set_single_qubit_state` refuses when the target is not in `|0>`, with the
message *"refusing to overwrite unknown quantum state (LCTL 1.1.x s6
no-cloning)"* — the no-cloning rule is enforced even in a helper.

### 3.3 Readback and verification

The destination amplitudes are **read out of the collapsed register**:

```python
base = (m_src << 2) | (m_ea << 1)
dest = [psi[base], psi[base | 1]]      # normalized
fidelity = |<source|dest>|^2
```

A zero-norm branch raises *"the collapse bookkeeping is inconsistent"*.

`verdict = TELEPORT_REFERENCE_EQUIVALENCE_PASS` iff
`fidelity >= 1 - PROTOCOL_FIDELITY_TOL` (`1e-9`), else
`REFERENCE_EQUIVALENCE_FAIL`.

### 3.4 Ownership and resource effects

* The ebit is **consumed**: `ebit_ledger.consume(ebit_id, owner_protocol="TELEPORT")`.
* An `OwnershipTransfer` is recorded with `source_invalidated = True` and the
  reason *"TELEPORT moves the state; the source qubit was destructively
  measured and holds no copy (no-cloning)"*.
* `ebits_consumed = 1`, two classical bits.
* The reported `label` is `simulator.LOCAL_STATEVECTOR_LABEL` — a classical
  simulation label, never a physical one.

---

## 4. Remote CNOT and `REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS`

`protocols.remote_cnot(control_amp, target_amp, ebit_ledger, ebit_id,
control_node, target_node, epoch=0, seed=0)`

### 4.1 Inputs

Either a 2-amplitude control **and** a 2-amplitude target (tensored), or a
4-amplitude joint two-qubit state ordered `|control, target>` with
`target_amp=None`. Anything else raises.

### 4.2 The ideal reference

Computed **before** the protocol runs, from the same input:

```python
ideal[(c << 1) | (t ^ c)] = joint[(c << 1) | t]        # CNOT|c,t> = |c, t xor c>
```

This is the criterion's whole point: the protocol is compared against the
**ideal local CNOT applied to the same input**, not against a textbook claim.

### 4.3 The executed cat-entangler / cat-disentangler circuit

Register `["q_c", "q_eA", "q_eB", "q_t"]`, seeded, initialized with the joint
state tensored against `|Phi+>` on `(q_eA, q_eB)`:

```
CX q_c, q_eA            # cat-entangler: copy the control's Z value onto the remote half
m1 = measure(q_eA, Z)
if m1: X q_eB           # correction from the classical bit

CX q_eB, q_t            # apply the CNOT remotely using the shared cat state

H  q_eB                 # cat-disentangler
m2 = measure(q_eB, Z)
if m2: Z q_c            # correction on the control
```

### 4.4 The pass criterion

```python
final    = amplitudes of the (m1, m2) branch, normalized
fidelity = |<ideal|final>|^2
max_amplitude_error = max_i |ideal_i - e^{-i arg<ideal|final>} final_i|
passed   = fidelity >= 1 - PROTOCOL_FIDELITY_TOL          # 1e-9
verdict  = "REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS" if passed
           else "REFERENCE_EQUIVALENCE_FAIL"
```

Both a fidelity and a **global-phase-corrected max amplitude error** are
reported, so a phase-only discrepancy is distinguishable from a real one.

**Normative statement.** `REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS` **SHALL** be
emitted only when a run of the protocol on the actual state reproduced the
ideal local CNOT to within `1e-9` in fidelity. It **SHALL NOT** be emitted from
a static analysis, from a template match, or from a prior run.

### 4.5 Ownership

`REMOTE_CNOT` is a **nonlocal unitary, not a relocation**. The recorded
`OwnershipTransfer` has `from_node == to_node == control_node` and
`source_invalidated = False`, with the reason *"both qubits stay with their
owners, only the ebit is consumed"*. One ebit consumed, **two classical bits
sent**.

---

## 5. Entanglement swapping

`protocols.entanglement_swap(ledger, ebit_ab, ebit_bc, node_a, node_b, node_c,
epoch=0, seed=0, expiry=None)`

### 5.1 Preconditions

Both input ebits usable; `ebit_ab` spans `{A, B}`; `ebit_bc` spans `{B, C}`;
`node_a != node_c`.

### 5.2 The executed circuit

Register `["q_A", "q_B1", "q_B2", "q_C"]`: two Bell pairs are built, then a
Bell measurement at the middle node:

```
H q_A ; CX q_A, q_B1            # pair A-B
H q_B2 ; CX q_B2, q_C           # pair B-C
CX q_B1, q_B2 ; H q_B1          # Bell basis at B
m1 = measure(q_B1, Z) ; m2 = measure(q_B2, Z)
if m2: X q_C ; if m1: Z q_C     # corrections at the far end
```

### 5.3 The two-part acceptance test

```python
out       = normalized (m1, m2) branch over (A, C)
fidelity  = |<Phi+|out>|^2
# INDEPENDENT correlation check on a fresh engine seeded with `out`:
zz = expectation("ZZ", [q_A, q_C]);  xx = expectation("XX", [q_A, q_C])
correlations_ok = |zz - 1| <= 1e-9 and |xx - 1| <= 1e-9
passed = fidelity >= 1 - 1e-9 and correlations_ok
```

Both inputs are consumed **before** the test. If the test fails, the function
**raises `ProtocolError`**:

> *"refusing to herald an A-C pair that the numerics do not support"*

This is the strongest fail-closed rule in the module: the swapped pair is
registered, generated and heralded in the ledger **only after** the numerical
check passes, and its provenance records
`{"from": "SWAP", "to": "HERALDED", "note": "swapped from <ab> and <bc> at <B>"}`.

The new pair's expiry is `min(expiry_ab, expiry_bc)` unless overridden, and its
fidelity is `min(fidelity_ab, fidelity_bc)` — **the weaker of the two inputs**,
never the better.

Verdict token: `ENTANGLEMENT_SWAP_REFERENCE_EQUIVALENCE_PASS`.

---

## 6. BBPSSW purification and its validity domain

`protocols.purify(pair1_fidelity, pair2_fidelity, model="BBPSSW")`

### 6.1 The validity domain

`BBPSSW_MIN_FIDELITY = 0.5`. Every input is checked:

| Condition | Result |
|---|---|
| model not in `PURIFICATION_MODELS` (`("BBPSSW",)`) | `ProtocolError` listing admitted models |
| not a real number, or a bool | `ProtocolError` |
| NaN | `ProtocolError` |
| outside `[0, 1]` | `ProtocolError` |
| `<= 0.5` | `ProtocolError`: *"outside the BBPSSW validity domain (F > 0.5); the recurrence does not increase fidelity there and this module refuses to extrapolate it (LCTL 1.4.x s42)"* |

**Normative rule.** LCTL 1.4.x §42 forbids applying a purification formula
outside its assumed noise model, so **no clamped or extrapolated value is ever
returned**. A conforming implementation **SHALL** raise rather than return a
plausible number outside the domain.

### 6.2 The recurrence

Werner form: `a = F`, `b = c = d = (1 - F)/3` for each pair.

```
p_success = (a1 + d1)(a2 + d2) + (b1 + c1)(b2 + c2)
F_out     = (a1 a2 + d1 d2) / p_success
```

`p_success <= 0` raises; `F_out` outside `[0, 1 + 1e-12]` raises.

### 6.3 The reported assumptions

`protocols.BBPSSW_ASSUMPTIONS` is returned with **every** result:

1. both input pairs are Bell-diagonal and Werner-twirled with the stated
   fidelity F relative to `|Phi+>`;
2. the two input pairs are statistically independent (no correlated noise);
3. local operations (bilateral CNOT) and measurements are noiseless;
4. classical communication of the two measurement outcomes is reliable;
5. the recurrence increases fidelity only for F > 1/2; outside that domain the
   model is not applied;
6. one output pair is produced per two input pairs, and only on success.

The result also carries `regime = "APPROXIMATE"` and the note *"the recurrence
is exact for Werner states only; any real pair must be twirled first, which
itself loses fidelity information"*, plus `pairs_consumed = 2`,
`pairs_produced_on_success = 1`, and an `improved` flag.

> **Stated scope.** `purify` is an **analytic** recurrence over declared
> fidelities. Unlike `teleport`, `remote_cnot` and `entanglement_swap`, it does
> not execute a circuit. The distinction is visible in the return value: there
> is no `label`, no measured state and no verdict token, because nothing was
> measured. `PURIFY` *does* have a compiler template (§8) so it can be
> scheduled.

---

## 7. The classical feedback fabric

`protocols.ClassicalFeedbackFabric(default_latency=1.0, timeout=8.0,
max_retries=2)` — LCTL 1.3.x §24.

### 7.1 Message states

`protocols.MESSAGE_STATES = ("SENT", "IN_FLIGHT", "DELIVERED", "TIMED_OUT",
"DROPPED")`.

### 7.2 The happens-before rule

> **A remote conditional quantum operation must call `assert_ready(msg_id)`
> before it executes.** `assert_ready` raises `ProtocolError` unless the message
> has been delivered, so a correction can never run ahead of the classical bit
> it depends on.

```python
def assert_ready(self, msg_id):
    if msg.state != "DELIVERED":
        raise ProtocolError("a remote conditional quantum operation must not "
                            "execute before its classical dependency is "
                            "satisfied (LCTL 1.3.x s24)")
```

### 7.3 Transport

| Method | Behaviour |
|---|---|
| `send(src, dst, payload, depends_on, latency, at_time)` | refuses self-send; refuses if any dependency is not `DELIVERED`; refuses negative latency; refuses a send time before the fabric clock; records `happens_before` edges |
| `receive(msg_id)` | idempotent for an already-delivered message; refuses `TIMED_OUT`/`DROPPED`; **marks `TIMED_OUT` and raises when `latency > timeout`**, advancing the clock to `send_time + timeout`; otherwise advances the clock to `arrival_time` and marks `DELIVERED` |
| `retry(msg_id, latency)` | requires `TIMED_OUT`; marks `DROPPED` and raises when attempts exceed `max_retries`; otherwise creates a **new** message id with `attempts + 1`, linked by a `happens_before` edge. The original stays `TIMED_OUT` in the replay log. |
| `drop(msg_id)` | marks `DROPPED` |
| `add_happens_before(a, b)` | refuses a self edge |
| `check_acyclic()` | DFS three-colouring; raises on a cycle |

### 7.4 Deterministic replay

`replay()` returns the event log in order; `replay_hash()` is the SHA-256 of

```
"<event>:<msg_id>:<src>-><dst>:<clock:.6f>:<state>"  joined by ";"
```

*"Two runs of the same program produce byte-identical replays."*

---

## 8. The protocol compiler

`protocols.ProtocolCompiler.compile(program, vr) -> ProtocolPlan`

### 8.1 Scope and refusal

A row is compiled when its `FACE` is `PROTOCOL` **or** its `OP` is in
`lang.OPS_DISTRIBUTED_Q`. A primitive outside
`ProtocolCompiler.SUPPORTED_OPS` (all 14 distributed primitives) raises
`ProtocolError` naming the admitted set:

> *"The compiler is total over the templates it declares and fails closed on
> anything else: an unrecognized distributed primitive raises `ProtocolError`
> rather than emitting an empty or guessed schedule."*

### 8.2 Endpoint resolution

`_peer_node` resolves the remote endpoint by scanning for the `DECLARE_LINK`
row whose `OUT` matches the row's `LINK` and taking the endpoint that is not
the local node. When no link is declared it returns the placeholder
`"N_REMOTE"` — *"Never guesses a physical topology."*

### 8.3 The eight step kinds

`protocols.STEP_KINDS`:

| Kind | Meaning |
|---|---|
| `EPR_GENERATE` | request/begin generation of an ebit on the link |
| `EPR_HERALD` | heralding confirms the pair exists |
| `LOCAL_BELL` | a local entangling/basis-change block (CNOT + H, cat-entangler, bilateral CNOT) |
| `MEASURE` | destructive local measurement |
| `CLASSICAL_SEND` | send classical bits |
| `CLASSICAL_RECV` | receive classical bits (`classical_required = True`) |
| `PAULI_CORRECT` | conditional Pauli gated on a delivered bit (`classical_required = True`) |
| `OWNERSHIP_TRANSFER` | ownership moves, or an ebit returns to the pool |

Each `ProtocolStep` carries `step_id` (`S%04d:<row>:<kind>`), `kind`, `node`,
`link`, `depends_on`, `ebit_required`, `classical_required`, `row`,
`operation`, `detail`.

Consecutive rows sharing a `LINK` are chained: the first step of a row with no
other dependency inherits the last step of the previous row on that link.

### 8.4 Templates

| Op | Emitted steps |
|---|---|
| `ENTANGLE_LINK`, `EPR_RESERVE` | `EPR_GENERATE` → `EPR_HERALD` |
| `HERALD` | `EPR_HERALD` |
| `EPR_RELEASE` | `OWNERSHIP_TRANSFER` (release back to the pool) |
| `TELEPORT` | `EPR_GENERATE` → `EPR_HERALD` → `LOCAL_BELL` → `MEASURE` → `CLASSICAL_SEND` → `CLASSICAL_RECV` → `PAULI_CORRECT` → `OWNERSHIP_TRANSFER` |
| `REMOTE_CNOT`, `REMOTE_CONTROL` | `EPR_GENERATE` → `EPR_HERALD` → `LOCAL_BELL` (cat-entangler) → `MEASURE` → `CLASSICAL_SEND` → `CLASSICAL_RECV` → `PAULI_CORRECT` → `LOCAL_BELL` (remote CNOT + H) → `MEASURE` → `CLASSICAL_SEND` → `CLASSICAL_RECV` → `PAULI_CORRECT` (cat-disentangler) |
| `REMOTE_MEASURE` | `MEASURE` → `CLASSICAL_SEND` → `CLASSICAL_RECV` |
| `ENTANGLEMENT_SWAP` | two generate/herald pairs → `LOCAL_BELL` → `MEASURE` → `CLASSICAL_SEND` → `CLASSICAL_RECV` → `PAULI_CORRECT` → `EPR_HERALD` (the swapped pair) |
| `PURIFY` | two generate/herald pairs → `LOCAL_BELL` (bilateral CNOT) → `MEASURE` → `CLASSICAL_SEND` → `CLASSICAL_RECV` → `EPR_HERALD` *"only on outcome agreement"* |
| `CLASSICAL_FEEDBACK` | `CLASSICAL_SEND` → `CLASSICAL_RECV` → `PAULI_CORRECT` |
| `QCHANNEL_SEND`, `QCHANNEL_RECEIVE` | `OWNERSHIP_TRANSFER` *"ownership is transferred, never duplicated"* |
| `SYNC_QCLOCK` | `CLASSICAL_SEND` → `CLASSICAL_RECV` *"clock synchronization is classical only"* |

### 8.5 Structural validation

`ProtocolPlan.validate()` is run by `compile()` and fails closed on:

| Check | Failure |
|---|---|
| topological order exists | *"protocol step DAG has a cycle among [...]"* |
| every dependency is a known step | *"depends on unknown step X"* |
| `kind in STEP_KINDS` | *"has kind K, not in STEP_KINDS"* |
| a step follows its dependencies | *"does not follow its dependency D"* |
| **a `PAULI_CORRECT` declares `classical_required`** | *"a correction may never precede its classical bit"* |
| **a `PAULI_CORRECT` depends on a `CLASSICAL_RECV`** | *"corrects without depending on a CLASSICAL_RECV step"* |
| **a `CLASSICAL_RECV` depends on a `CLASSICAL_SEND`** | *"receives without a matching CLASSICAL_SEND dependency"* |

The last three are the structural counterpart of the runtime `assert_ready`
rule: a correction cannot be *scheduled* before its bit, just as it cannot be
*executed* before it.

Returns `{"steps", "topological_order", "acyclic": True,
"correction_gating_ok": True}`.

---

## 9. Verdict tokens

| Token | Symbol | Emitted by |
|---|---|---|
| `TELEPORT_REFERENCE_EQUIVALENCE_PASS` | `protocols.TELEPORT_PASS_TOKEN` | `teleport` |
| `REMOTE_CNOT_REFERENCE_EQUIVALENCE_PASS` | `protocols.REMOTE_CNOT_PASS_TOKEN` | `remote_cnot` |
| `ENTANGLEMENT_SWAP_REFERENCE_EQUIVALENCE_PASS` | `protocols.SWAP_PASS_TOKEN` | `entanglement_swap` |
| `REFERENCE_EQUIVALENCE_FAIL` | `protocols.FAIL_TOKEN` | any of the above on failure |

Threshold: `PROTOCOL_FIDELITY_TOL = 1e-9`, stated explicitly as LCTL 1.3.x §56
requires.

---

## 10. Emitted ledgers

| File | Content |
|---|---|
| `ENTANGLEMENT_INVENTORY_LEDGER.json` | states, transition table, every record with provenance, counts, events, requesting rows |
| `PROTOCOL_COMPILER_LEDGER.json` | supported primitives, step kinds, status (`COMPILED` / `BLOCKED`), detail, the plan, the validation result |
| `PROTOCOL_LEDGER.json` | reference-equivalence results for `TELEPORT` and `REMOTE_CNOT` (verdict, fidelity, label), purification model and assumptions, ebit counts, message states, and the claim: *"reference protocol semantics verified numerically; not evidence of physical entanglement or physical execution"* |

---

## 11. Implementation status

| Element | Status | Note |
|---|---|---|
| Ebit lifecycle, 8 states, fail-closed table | `OPERATIONAL` | import-time coverage assertion |
| Full transition provenance | `OPERATIONAL` | |
| Teleportation, numerically executed | `OPERATIONAL` | measured fidelity, real Born draws |
| Remote CNOT with reference equivalence | `OPERATIONAL` | compared against the ideal local CNOT on the same input |
| Entanglement swapping | `OPERATIONAL` | raises rather than heralding an unsupported pair |
| BBPSSW purification | `OPERATIONAL` (analytic) | refuses outside `F > 0.5`; assumptions always reported; no circuit executed |
| Classical feedback fabric | `OPERATIONAL` | happens-before enforced; deterministic replay hash |
| Protocol compiler, 14 templates | `OPERATIONAL` | fails closed on anything else |
| Correction-gating validation | `OPERATIONAL` | structural counterpart of `assert_ready` |
| Multi-model purification (DEJMPS, etc.) | `SPECIFIED` | `PURIFICATION_MODELS` has one member |
| Purification executed as a circuit | `SPECIFIED` | analytic recurrence only |
| Multi-hop / repeater chains | `IMPLEMENTED_PARTIAL` | `entanglement_swap` composes pairwise; no chain orchestrator |
| Physical entanglement distribution | `BLOCKED` | `NETWORK=deny`; nodes are logical names in one process |
