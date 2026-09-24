# examples/ (DF_Small)

* `01_bell_pair.pal` .. `06_noise_density.pal` -- the corpora's six PA-LCTL programs (byte-identical to `PA_Language_PA21.2/examples`). `./RUN examples/NN_*.pal` executes each one's row-sequence witness natively on this VM and checks it against the CPython reference; gate `A3` does the same for all of them plus `node/NODE.pal`.
* `add42.mssl` -- a native MSSL/ASM-1 program: `./RUN examples/add42.mssl` -> `R2` = 42 (gate `A9`).
* `loop_forever.mssl` -- an unbounded loop: the VM traps on its step budget instead of running (gate `A6`).
