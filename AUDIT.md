# DF Small 1.0.1 audit

Work was performed in a separate copy of DF_Small; the original is unchanged.

## Findings and repairs

- Original shared adapters matched the previously audited DF implementation.
  Applied strict inventory records, canonical contained paths, link/duplicate
  rejection and safe adapter output identifiers.
- The assembler used `offset + length` before indexing data, allowing unsigned
  wraparound. It now checks the offset and remaining capacity by subtraction.
- Sources exceeding the assembler's line count previously bypassed fclose.
  Rejected input now closes its stream; regression checks monitor descriptors.
- Host reads now cap allocation at BR_MAX_IMAGE, reject links, FIFOs and other
  nonregular input, initialize failure outputs and cleanse failed read buffers.
- Key generation previously truncated existing keys and deleted the private path
  after failures. Exclusive creation preserves existing files and uses mode 0600.
- Fixed double fclose in image writing and missing close after fsync failure.
  State writes refuse symbolic-link targets. Bounded APDU line input and signing
  image growth; retained the existing trust and runtime contract.
- Original payload guide and inventories are preserved under provenance/. The
  current payload guide is concise and the 110,000-byte VM ceiling is retained.
  Root metadata and regression suites are outside that embedded-payload boundary.
- Added README, Apache-2.0 LICENSE/NOTICE naming RUSSELL PHILIP SMITHSON,
  complete current inventories and a per-file original/current hash map.

## Validation scope

Portable tests cover malformed/duplicate/tampered manifests, filesystem links,
path escapes and reserved output names. Local Windows link creation may be
skipped for missing privilege. CI covers Python 3.10/3.14 on Windows and Linux.

Linux CI runs native acceptance, image replay, signing/update, model conformance,
source distribution size, adapter gates and VM sanitizers. Additional ASan/UBSan
regressions with leak detection exercise existing-key preservation, FIFOs/links,
bounded reads, /dev/full flushing, UINT64_MAX assembler offsets and rejected
source-file descriptor cleanup. Review actual Actions results for native outcomes;
this local Windows host has no C compiler.

The original PA-LCTL core and runtime ABI labels remain unchanged. The VM source
has explicit hardening changes recorded in provenance. Historical evidence is
not newly measured qualification. Physical SIM/UICC, production key provisioning,
AArch64/native Windows execution and cross-host federation remain unqualified.
