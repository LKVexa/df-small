<!-- GENERATED FILE - do not edit by hand.
     Produced by tools/gen_catalogs.py from the reference implementation
     in pacore/. Re-run the generator after any change to pacore. -->

# PA-LCTL Column Dictionary

Language: **PA-LCTL** &nbsp;&nbsp; Bundle magic: `#PA-LCTL/1.6` &nbsp;&nbsp; Core version: `1.6.0-rc1`

Generated from: `pacore.lang.CORE_COLUMNS`, `pacore.lang.DISTRIBUTED_COLUMNS`, `pacore.lang.COLUMN_MEANING`

Canonical separator: BROKEN BAR `U+00A6` (`lang.CANONICAL_SEP`). Accepted input aliases: `|`. Null cell: `-` (`lang.NULL_CELL`); an *empty* cell is normalized to the null cell by `lang.normalize_cell`.

Column count: **22** = 18 core + 4 distributed.

| # | Column | Introduced | Meaning | Cell syntax | Nullable | Verifier checks |
|---|---|---|---|---|---|---|
| 0 | `ROW` | core (1.1) | stable row identity | identifier, unique within the bundle | no | duplicate detection (E-GRAM-003) |
| 1 | `FACE` | core (1.1) | semantic face; one of FACES | one of lang.FACES | no | membership in FACE_SET (E-FACE-001) |
| 2 | `LANE` | core (1.1) | scheduling or subsystem lane | identifier | yes | none beyond presence |
| 3 | `QSPACE` | core (1.1) | Hilbert/subsystem/register domain | indexed reference, e.g. `q[0:2]` | yes | none beyond presence |
| 4 | `OP` | core (1.1) | operation | one of lang.ALL_OPS | no | membership in ALL_OPS (E-OP-001); arity (E-ARITY-001) |
| 5 | `OUT` | core (1.1) | destination quantum/classical object | indexed reference | yes | none beyond presence |
| 6 | `CTRL` | core (1.1) | control qubit/register/operator | indexed reference | yes | disjointness from A for controlled gates (E-CTRL-001) |
| 7 | `A` | core (1.1) | primary operand | indexed reference | yes | none beyond presence |
| 8 | `B` | core (1.1) | secondary operand | indexed reference | yes | none beyond presence |
| 9 | `PARAM` | core (1.1) | angle, time, coefficient, probability, index, or target parameter | numeric literal list or k=v list | yes | required for PARAMETRIC_GATES (E-PARAM-001); probability domain for OPS_NOISE (E-PROB-001) |
| 10 | `TYPE` | core (1.1) | quantum/classical semantic type | one of lang.ALL_TYPES (optionally `name[...]`) | yes | membership in ALL_TYPES (E-TYPE-001) |
| 11 | `BASIS` | core (1.1) | computational/X/Y/Z/custom/eigenbasis | `Z` \| `X` \| `Y` \| identifier | yes | none beyond presence |
| 12 | `REGIME` | core (1.1) | exactness regime; one of REGIMES | one of lang.REGIMES | yes | membership in REGIME_SET (E-REG-001); exactness honesty (E-REG-002, E-REG-003) |
| 13 | `ASSUME` | core (1.1) | validity assumptions, semicolon separated | `;`-separated free text | yes | none beyond presence |
| 14 | `ERROR` | core (1.1) | declared error envelope, key=value pairs | `k=v;k=v` | yes | none beyond presence |
| 15 | `RESOURCE` | core (1.1) | resource/cost annotation, key=value pairs | `k=v;k=v` | yes | none beyond presence |
| 16 | `CONF` | core (1.1) | confidence in [0,1] or NULL | real in [0,1] | yes | range check (E-CONF-001) |
| 17 | `PROOF` | core (1.1) | evidence/provenance identifier | identifier | yes | none beyond presence |
| 18 | `DOMAIN` | distributed (1.2+) | execution domain identity (1.5 FEDERATION model) | identifier declared by DECLARE_DOMAIN | yes | declared-before-use (E-DOM-001) |
| 19 | `NODE` | distributed (1.2+) | logical node identity (1.2 distributed kernel) | identifier declared by DECLARE_NODE | yes | declared-before-use (E-NODE-001); ownership locality (E-OWN-003) |
| 20 | `LINK` | distributed (1.2+) | classical channel or quantum link identity | identifier declared by DECLARE_LINK | yes | declared-before-use (E-LINK-001); required by protocol ops (E-PROTO-002, E-PROTO-004, E-EPR-001) |
| 21 | `FAMILY` | distributed (1.2+) | declared parallel family; one of PARALLEL_FAMILIES | one of lang.PARALLEL_FAMILIES | yes | membership in PARALLEL_FAMILY_SET (E-FAM-001) |

## Canonical `#COLUMNS` line

```
#COLUMNS ROW¦FACE¦LANE¦QSPACE¦OP¦OUT¦CTRL¦A¦B¦PARAM¦TYPE¦BASIS¦REGIME¦ASSUME¦ERROR¦RESOURCE¦CONF¦PROOF¦DOMAIN¦NODE¦LINK¦FAMILY
```
