# BOTTLE ROCKET 3.0.0-MODEL: DF Small 1.0.1 derivation

Offline teaching/model VM; physical SIM/UICC and production trust are unqualified.
Runtime formats remain MSSL/ASM-1 and BRIM/1-v3. The current distribution adds
bounded host reads, exclusive key creation, bounded APDU input, overflow-safe
assembler offsets and reliable stream/descriptor cleanup.

Requires POSIX, C11, GNU make and OpenSSL 3. Run `make clean operational` and
`make sanitize`. Operational validation includes conformance, malformed inputs,
restart/update, deterministic image reproduction and the 110,000-byte package
ceiling. Sanitizers exercise VM regressions. The enclosing DF release adds host
and assembler security regressions.

Build `.build/brctl`, then use `assemble SOURCE IMAGE`, `inspect-image IMAGE`,
`run IMAGE`, `keygen PRIVATE PUBLIC`, `sign-image INPUT OUTPUT PRIVATE`,
`verify-image IMAGE PUBLIC`, `run-signed IMAGE PUBLIC` or `serve --state PREFIX`.
Use new paths for keys; failures preserve existing paths and can leave a new
private file if creating the public file fails. Never deploy fixture trust.

Use trusted local work/state directories. No production authority, physical card
qualification, cross-host federation or physical quantum execution is supplied.
See ../../README.md, ../../AUDIT.md and ../../provenance/ for the full setup,
original guide, original inventory and current per-file changes. SHA256SUMS.txt
covers current payload bytes; original hashes remain historical evidence.
