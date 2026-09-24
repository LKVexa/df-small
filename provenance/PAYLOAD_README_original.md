# BOTTLE ROCKET 3.0.0-MODEL

Offline C11 SIM-core VM: `MODEL_OPERATIONAL`; physical SIM/UICC is external and unqualified.

Requires POSIX, Make, C11, and OpenSSL 3:

```sh
make clean operational
make sanitize
```

The gates run conformance, 10,000 malformed cases, restart/update proofs, ASan/UBSan, reproduction, and size checks.

## MSSL/ASM-1

```sh
.build/brctl assemble examples/boot.mssl app.brimg --image-version 41
.build/brctl inspect-image app.brimg
.build/brctl run app.brimg
```

Source requires `.profile SIM_CORE`, `.image_version N`, `.request_caps A|B`; `.data OFFSET HEX` is optional. Canonical syntax uses uppercase names, registers, unsigned integers, comments, and labels. The bounded two-pass assembler covers all 24 opcodes/four modes and rejects malformed input. Boot source reproduces the factory BRIM.

BRIM/1-v3 contains caller version, requested mask, code/data, authenticated padding, SHA-256, and optional Ed25519. Admission grants `requested & platform_allowlist`; lower version is denied, equal version permits deterministic reinstall, and generation wrap fails closed.

## Persistent host

```sh
.build/brctl keygen issuer.private issuer.public
.build/brctl sign-image app.brimg app.signed.brimg issuer.private
.build/brctl update app.signed.brimg issuer.public --state state/node
.build/brctl recover-state state/node
.build/brctl status --state state/node
.build/brctl serve --state state/node [--issuer-public-key issuer.public]
```

`serve` exchanges one hex APDU/1 frame per line; EOF stops it. UPDATE stages ordered fragments (≤4096 each, ≤51200 total), verifies them, atomically writes the inactive image, then commits generation, txid, version, hash, slot, config, and diagnostics in dual CRC slots. File and parent are fsynced. Restart loads only the verified committed image; orphan staging never activates. RECOVER returns generation/version/slot/status.

STATE keeps its legacy empty-payload reply. `{kind,index,offset:u32,length:u16}` returns ≤512 bytes from a register, memory, or active stack entry.

The VM has 16×1,048,576-bit registers, 256 equally wide stack entries, 4096 memory bytes, 16 capabilities, 24 opcodes, budgets/traps, and diagnostics. SVC includes SHA-256/Ed25519 verify; STATE, UPDATE, and DIAG are independently enforced. No guest signing exists.

No production authority is included. Card target/AID, trust, NVM/watchdog, hardware/security qualification, and deployment authorization remain external. Network/telemetry/desktop/cloud facilities are absent.
