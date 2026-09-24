# conformance/ (DF_Small)

`DF_GATE_RESULTS.json` (schema `DF/GATE_RESULTS/1`) is what the assembly host measured from the delivered bytes with `adapter/dfabric/gates.py::run_node_battery` -- the same battery `./VERIFY` runs on your machine. `logs/` holds the VM's own build/gate output and the core selfcheck output. Gates `G0.1`/`G0.2` (container hashes, manifest) can only be SKIPPED in this file because it predates the seal; they PASS in the post-seal verification recorded in the delivery's `_assembly/` folder and in every `./VERIFY` run.
