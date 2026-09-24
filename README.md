# DF Small

**1.0.1** (`DF-PA21.2-1.0.1`) by **RUSSELL PHILIP SMITHSON**.

DF Small is the `N_SMALL` classical node for the PA-LCTL fabric. It embeds
BOTTLE ROCKET 3.0.0-MODEL, a C11 teaching VM with an MSSL/ASM-1 assembler,
Ed25519 image signing, bounded execution and an offline APDU interface.
This repository is the separate delivery of the supplied `DF_Small` folder.

## Setup and checks

Use Python 3.10+ in a virtual environment:

```sh
python -m pip install --only-binary=:all: -r REQUIREMENTS.txt
python -B tools/validate_release.py
python -B adapter/dfabric/cli.py node-verify
```

The portable validator checks the root and payload inventories, malformed
manifest evidence and adapter output paths. CI covers Windows/Linux with
Python 3.10 and 3.14. Native checks require Linux, GNU make, a C11 compiler
and OpenSSL/libcrypto development files. The node verifier builds a disposable
copy and runs native acceptance, replay, signing/update and adapter gates.
Unavailable native prerequisites are explicitly SKIPPED.

Run `python -B tools/validate_native.py` for VM ASan/UBSan and the new host and
assembler regressions with leak detection. CI exercises these on Linux with
Python 3.12. Use `python -B adapter/dfabric/cli.py node-build` to build locally.
Original machine-specific `.build` binaries and generated state are excluded.
The embedded VM retains its 110,000-byte payload ceiling; the enclosing fabric
core, regression tests and provenance records are additional distribution content.

## Security and limits

The assembler rejects overflowing data offsets and closes overlong source files.
Host reads are bounded and reject symbolic links and nonregular files. Key
creation refuses existing paths, with private keys created in mode 0600.
Choose new paths for both key outputs: failure creating the public file can
leave a newly created private file. Existing paths are not overwritten or deleted.
Buffered-write and fsync failures close handles once. APDU lines and adapter
output identifiers are bounded. Use trusted local source, work and state folders.

This is a host model. Physical SIM/UICC qualification, production trust,
AArch64/native Windows execution and cross-host federation require external
work. No physical quantum execution or production authority is supplied.
Update external DF node registry pins deliberately for this derived payload.

## Provenance and license

The distribution version is 1.0.1; BRIM/runtime version labels remain 3.0.0.
The PA-LCTL core pin is unchanged. Original inventories, the original payload
guide and a per-file change map are retained in `provenance/`. Payload checksums
now cover the hardened bytes. Historical conformance records are not new
certifications, and in-package hashes do not establish independent authenticity.

See [AUDIT.md](AUDIT.md), [SECURITY.md](SECURITY.md), [LICENSE](LICENSE) and
[NOTICE](NOTICE). Copyright 2026 **RUSSELL PHILIP SMITHSON**, Apache License 2.0.
