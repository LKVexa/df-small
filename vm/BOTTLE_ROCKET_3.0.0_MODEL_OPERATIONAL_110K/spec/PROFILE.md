# Normative profile

`src/brvm.h` freezes ABI constants; `BR_SPEC.json` freezes v3 policy. Checks precede operand access. Opcode classes require CONTROL, ARITH, MEMORY, STACK, or SERVICE; SVC 4–5 additionally require STATE, 6 DIAG, and 8 UPDATE. Delegation is subset-only and volatile.

Arithmetic and stack entries are exactly 1,048,576 bits. LOAD/STORE intentionally move bounded low 64-bit values. Persistent generation overflow fails closed. Recovery selects the highest CRC-valid state, verifies the active image/hash/signature, restores only admitted root authority, and clears all volatile state. A signed lower version is denied; equal version is the governed deterministic-reinstall policy.

This is a vendor-neutral host model, not physical-card qualification. External gates in README remain blocking.
