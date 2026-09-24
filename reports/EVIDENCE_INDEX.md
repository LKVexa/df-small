# evidence index (DF_Small)

| question | file |
|---|---|
| what was measured, and did it pass? | `conformance/DF_GATE_RESULTS.json` (+ `conformance/logs/`) |
| what is claimed, item by item, with the gate it cites? | `reports/DF_CAPABILITY_LEDGER.json` / `.md` |
| what does NOT work, and why? | `reports/DF_BLOCKED_REGISTER.md` |
| what is the VM, as measured before translation? | `reports/VM_PROFILE_MEASURED.md` / `.json` |
| how is the VM expressed in the fabric? | `spec/DF_NODE_SPEC.md`, `spec/DF_LANGUAGE_MAP.md`, `node/NODE.pal` |
| what did the translation read, by digest? | `authority/DF_SOURCE_AUTHORITY.json` |
| every translated claim as a record | `corpus/DF_Small_Translation_Corpus.jsonl.gz` |
| the fabric-level evidence (four nodes together) | `DF_Fabric/conformance/DF_GATE_RESULTS.json` |
