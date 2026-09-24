# core/

The PA-LCTL reference core carried from the corpora, unchanged (DF-PA21.2-1.0.0).

* `reference/pacore/` -- PA-LCTL 1.6.x reference implementation (lang, fabric, adapters, ledgers, ...); the authority every spec document defers to. Pinned by `PACORE_DIGEST.json`.
* `spec/` -- the 30 PA-LCTL specification documents.
* `examples/` -- the corpora's six `.pal` programs.
* `pamath/tests/test_bottlerocket_backend.py` -- the corpora's 30-check backend suite (needs PA_LCTL_BOTTLE_ROCKET_ROOT).

Run it yourself: `python3 -B -m reference.pacore.cli selfcheck` from this directory.
