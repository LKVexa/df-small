"""Importers for bundles written in languages ancestral to PA-LCTL.

PA-LCTL 1.6 descends from LCTL. `PA_LCTL_TUPLE_SCHEMA.md` records the lineage
directly: PA-LCTL freezes the eighteen columns of LCTL 1.1.x s4.3 and adds
exactly four additive, optional distributed columns. The eighteen names are
byte-identical in both languages.

That makes external LCTL bundles the one thing PA-LCTL's conformance story has
never had: **test vectors written by someone else.** `README_SPEC_INDEX.md`
states the precedence rule plainly -- the reference implementation is the
authority and a disagreeing specification is a defect -- which means, until
now, "conforming" could only mean "agrees with this Python". An imported
external corpus is the first independent check on that.

The importer is deliberately **fail-closed**. It maps only vocabulary whose
equivalence is provable from the two languages' own catalogues, and refuses
everything else with a typed reason rather than guessing. A guessed mapping
would manufacture agreement, which is precisely the failure mode this release
exists to avoid.
"""

from __future__ import annotations

IMPORTER_ABI = "PA-LCTL/IMPORTER/1"


class ImportRefused(Exception):
    """Raised when a bundle cannot be imported without inventing meaning."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}
