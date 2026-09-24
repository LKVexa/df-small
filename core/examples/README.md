# examples/

Six runnable PA-LCTL programs. Every one parses and
verifies; each note says which engine executes it.

* `01_bell_pair.pal` — Bell pair |00>+|11> on one node; verify + execute-local (stabilizer engine).
* `02_ghz3.pal` — GHZ(3) state; verify + execute-local (stabilizer engine).
* `03_two_lane_parallel.pal` — Two independent circuit lanes across N0/N1; exercises INSTRUCTION_PARALLEL and CIRCUIT_PARALLEL family declarations.
* `04_distributed_teleport.pal` — Distributed program: DECLARE_NODE / DECLARE_LINK / ENTANGLE_LINK / EPR_RESERVE / TELEPORT / REMOTE_CNOT. Runs under `protocol-compile` and `protocol-execute`; `execute-local` correctly refuses it because protocol rows must be compiled by pacore.protocols first.
* `05_measurement_feedback.pal` — Measurement then CLASSICAL_IF-gated RZ correction; verify + execute-local (statevector engine).
* `06_noise_density.pal` — Depolarizing and amplitude-damping channels; verify + execute-local (density-matrix engine).

```
cd ../reference
python3 -m pacore.cli verify ../examples/01_bell_pair.pal
python3 -m pacore.cli execute-local ../examples/01_bell_pair.pal
```
