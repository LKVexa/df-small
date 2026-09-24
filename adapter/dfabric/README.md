# adapter/dfabric

The DF adapter package (DF-PA21.2-1.0.0); byte-identical in every DF container.

* `__init__.py` -- constants, `AdapterRefusal`, `assert_not_physical` (the firewall as code)
* `witness.py` -- the row-sequence witness and its four lowerings (`DF/ROW_WITNESS_LOWERING/1`)
* `nodes.py` -- the four node adapters (`PA-LCTL/TARGET_ADAPTER/1`)
* `fabric_runtime.py` -- the federation, the three fabric programs, event log + replay
* `gates.py` -- the node and fabric gate batteries (what VERIFY runs)
* `manifest.py`, `schemas.py` -- integrity and schema checks
* `cli.py` -- the command surface behind BUILD / VERIFY / RUN
