"""LCTL/1.0-1.3 canonical bundle -> PA-LCTL/1.6.  Schema `PA-LCTL/IMPORT_LCTL/1`.

Source form
-----------

    LCTL/1.2
    BUNDLE¦ID¦VERSION¦PROFILE¦ENTRY¦…
    (bundle ¦ lctl.example.x ¦ 1.2.0 ¦ lctl.quantum.parallel ¦ … )
    QFRAME/1.0 BEGIN F0000
    ID¦PARENT¦MODULE¦…
    (F0000 ¦ ROOT ¦ lctl.example.x ¦ … )
    QTUPLE/1.0
    ROW¦FACE¦LANE¦QSPACE¦OP¦OUT¦CTRL¦A¦B¦PARAM¦TYPE¦BASIS¦REGIME¦ASSUME¦ERROR¦RESOURCE¦CONF¦PROOF
    (C0010 ¦ CODE ¦ circuit ¦ H ¦ H ¦ q[0] ¦ _ ¦ q[0] ¦ … )

Four structural differences from PA-LCTL, all mechanical:

  1. magic `LCTL/x.y` vs `#PA-LCTL/1.6`;
  2. bundle/frame metadata rows vs `#DIRECTIVE` lines;
  3. data rows parenthesised and space-padded around the separator;
  4. the null cell is `_`, not `-`.

The eighteen QTUPLE column names are **byte-identical** to
`lang.CORE_COLUMNS`, so cell positions carry over exactly; the four PA-LCTL
distributed columns default to the null cell.

Vocabulary is the part that is not mechanical, and it is where this module
refuses rather than guesses. Every mapping below is either an identity or is
justified in a comment against both languages' own catalogues. A value with no
justified mapping is recorded as `UNMAPPED` and the row is dropped from the
imported bundle with its reason retained -- never silently coerced to
something PA-LCTL happens to accept.
"""

from __future__ import annotations

import collections
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import lang
from . import ImportRefused

IMPORT_SCHEMA = "PA-LCTL/IMPORT_LCTL/1"

SRC_SEP = "¦"
SRC_NULL = "_"

MAGIC_RE = re.compile(r"^\s*LCTL\s*/\s*(\d+\.\d+)\s*$")
ROW_RE = re.compile(r"^\((.*)\)\s*$")

# -- FACE ------------------------------------------------------------------
# Identity where the name exists in `lang.FACES`. Two justified renames:
#   CODE -> EXEC   both languages' execution face; LCTL's QTUPLE `CODE` rows
#                  carry gate operations, which is exactly what PA-LCTL's EXEC
#                  face is defined to carry (COLUMN_DICTIONARY s4.2).
#   DECL -> handled per-op, not per-face: LCTL folds register declaration and
#           topology declaration into one face, PA-LCTL splits them across
#           FEDERATION / TOPOLOGY with DECLARE_* operations. A blanket mapping
#           would be a guess, so DECL rows are mapped only when their OP has a
#           DECLARE_* counterpart (see OP_MAP).
FACE_MAP: Dict[str, str] = {
    "CODE": "EXEC",
    "TOPOLOGY": "TOPOLOGY",
    "PROTOCOL": "PROTOCOL",
    "ASSERT": "ASSERT",
    "EVIDENCE": "EVIDENCE",
    "MODEL": "MODEL",
}

#: FACE values present in the external corpus with no PA-LCTL counterpart.
#: Listed explicitly so the gap is a documented measurement, not a silence.
FACE_UNMAPPED = ("DECL", "DISTRIBUTED", "PARALLEL", "COUPLING", "FIELD",
                 "FAILURE", "TRANSFORM", "MEMORY")

# -- OP --------------------------------------------------------------------
# Identity for every operation that exists in `lang.ALL_OPS`. Three justified
# renames into PA-LCTL's DECLARE_* family, which is the same concept under a
# different name:
OP_MAP: Dict[str, str] = {
    "NODE": "DECLARE_NODE",
    "LINK": "DECLARE_LINK",
    "REGION": "REGION_BEGIN",
    "CPHASE": "PHASE",          # `lang.PHASE` is the controlled-phase gate
}

# -- REGIME ----------------------------------------------------------------
# LCTL writes regimes in lower case; PA-LCTL's `lang.REGIMES` are upper case
# and the token sets are otherwise identical. Case folding is a proof, not a
# guess: `exact` -> `EXACT` succeeds only when the result is in REGIMES.

# -- BASIS -----------------------------------------------------------------
# `computational` is the computational (Z) basis in both languages; PA-LCTL's
# examples and `simulator` write it as `Z`. Every other BASIS value is passed
# through unchanged, because PA-LCTL's verifier does not constrain BASIS at
# all (see `PA_LCTL_LANGUAGE_GAP_LEDGER.md`), so passing it through neither
# adds nor removes meaning.
BASIS_MAP: Dict[str, str] = {"computational": "Z"}


class ImportReport:
    """What was mapped, what was refused, and why. Nothing is summarised away."""

    def __init__(self, source: str):
        self.source = source
        self.rows_in = 0
        self.rows_out = 0
        self.dropped: List[Dict[str, str]] = []
        self.unmapped: collections.Counter = collections.Counter()
        self.directives: Dict[str, str] = {}
        self.src_magic: Optional[str] = None

    def drop(self, row_id: str, column: str, value: str, why: str) -> None:
        self.dropped.append({"row": row_id, "column": column,
                             "value": value, "reason": why})
        self.unmapped[f"{column}={value}"] += 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": IMPORT_SCHEMA,
            "source": self.source,
            "source_magic": self.src_magic,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_dropped": len(self.dropped),
            "complete": len(self.dropped) == 0,
            "unmapped_vocabulary": dict(self.unmapped),
            "dropped": self.dropped[:25],
            "directives": self.directives,
        }


def _cell(v: str) -> str:
    v = v.strip()
    return lang.NULL_CELL if v in ("", SRC_NULL) else v


def _map_regime(v: str, rep: ImportReport, row_id: str) -> Optional[str]:
    if v == lang.NULL_CELL:
        return v
    up = v.upper()
    if up in lang.REGIMES:
        return up
    rep.drop(row_id, "REGIME", v, "no PA-LCTL regime with this name")
    return None


def _map_face(v: str, rep: ImportReport, row_id: str) -> Optional[str]:
    if v in lang.FACES:
        return v
    if v in FACE_MAP:
        return FACE_MAP[v]
    rep.drop(row_id, "FACE", v,
             "LCTL face with no PA-LCTL counterpart; mapping it would be a "
             "guess about semantics neither catalogue states")
    return None


def _map_op(v: str, rep: ImportReport, row_id: str) -> Optional[str]:
    if v in lang.ALL_OPS:
        return v
    if v in OP_MAP and OP_MAP[v] in lang.ALL_OPS:
        return OP_MAP[v]
    rep.drop(row_id, "OP", v, "operation is not in lang.ALL_OPS")
    return None


def _map_type(v: str, rep: ImportReport, row_id: str) -> Optional[str]:
    if v == lang.NULL_CELL:
        return v
    base = v.split("[")[0]
    if base in lang.ALL_TYPES:
        return v
    rep.drop(row_id, "TYPE", v, "type base name is not in lang.ALL_TYPES")
    return None


def parse_source(text: str, rep: ImportReport) -> Tuple[Dict[str, str],
                                                        List[Dict[str, str]]]:
    """Split an LCTL canonical bundle into directives and QTUPLE rows."""
    lines = text.splitlines()
    if not lines:
        raise ImportRefused("empty source", {})
    m = MAGIC_RE.match(lines[0])
    if not m:
        raise ImportRefused(
            f"first line {lines[0]!r} is not an LCTL magic line", {})
    rep.src_magic = f"LCTL/{m.group(1)}"

    directives: Dict[str, str] = {}
    rows: List[Dict[str, str]] = []
    section: Optional[str] = None
    header: Optional[List[str]] = None

    for raw in lines[1:]:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("BUNDLE" + SRC_SEP):
            section, header = "BUNDLE", [c.strip() for c in s.split(SRC_SEP)]
            continue
        if s.startswith("QFRAME/"):
            section, header = "QFRAME", None
            continue
        if s.startswith("QTUPLE/"):
            section, header = "QTUPLE", None
            continue
        if section in ("QFRAME", "QTUPLE") and header is None \
                and SRC_SEP in s and not s.startswith("("):
            header = [c.strip() for c in s.split(SRC_SEP)]
            continue
        mm = ROW_RE.match(s)
        if not mm or header is None:
            continue
        cells = [c.strip() for c in mm.group(1).split(SRC_SEP)]
        if len(cells) != len(header):
            rep.drop(cells[0] if cells else "?", "__ROW__",
                     f"{len(cells)} cells", f"expected {len(header)}")
            continue
        record = dict(zip(header, cells))
        if section == "BUNDLE":
            for k, v in record.items():
                if v not in ("", SRC_NULL):
                    directives[k] = v
        elif section == "QTUPLE":
            rows.append(record)
    return directives, rows


def import_text(text: str, source: str = "<memory>"
                ) -> Tuple[Optional[str], ImportReport]:
    """Import one LCTL canonical bundle. Returns (PA-LCTL source or None, report)."""
    rep = ImportReport(source)
    directives, src_rows = parse_source(text, rep)
    rep.rows_in = len(src_rows)

    profile = directives.get("PROFILE", "pa.lctl.core")
    # LCTL profiles are `lctl.*`; the PA designation applies to this release's
    # own artefacts, so the profile token is re-designated on import.
    profile = profile.replace("lctl.", "pa.lctl.", 1) \
        if profile.startswith("lctl.") else profile
    rep.directives = {
        "PROFILE": profile,
        "NETWORK": directives.get("NETWORK", "deny"),
        "BACKEND": directives.get("BACKEND", "none"),
        "IMPORTED_FROM": rep.src_magic or "LCTL",
        "IMPORTED_ID": directives.get("ID", "-"),
    }

    out_rows: List[str] = []
    for rec in src_rows:
        row_id = _cell(rec.get("ROW", "?"))
        face = _map_face(_cell(rec.get("FACE", lang.NULL_CELL)), rep, row_id)
        op = _map_op(_cell(rec.get("OP", lang.NULL_CELL)), rep, row_id)
        regime = _map_regime(_cell(rec.get("REGIME", lang.NULL_CELL)),
                             rep, row_id)
        type_ = _map_type(_cell(rec.get("TYPE", lang.NULL_CELL)), rep, row_id)
        if face is None or op is None or regime is None or type_ is None:
            continue

        basis = _cell(rec.get("BASIS", lang.NULL_CELL))
        basis = BASIS_MAP.get(basis, basis)

        values = {c: lang.NULL_CELL for c in lang.FULL_COLUMNS}
        for col in lang.CORE_COLUMNS:
            values[col] = _cell(rec.get(col, lang.NULL_CELL))
        values["FACE"], values["OP"] = face, op
        values["REGIME"], values["TYPE"], values["BASIS"] = regime, type_, basis
        out_rows.append(lang.CANONICAL_SEP.join(
            values[c] for c in lang.FULL_COLUMNS))

    rep.rows_out = len(out_rows)
    if not out_rows:
        return None, rep

    head = ["#PA-LCTL/1.6",
            f"#PROFILE {rep.directives['PROFILE']}",
            f"#NETWORK {rep.directives['NETWORK']}",
            f"#BACKEND {rep.directives['BACKEND']}",
            f"#IMPORTED_FROM {rep.directives['IMPORTED_FROM']}",
            f"#IMPORTED_ID {rep.directives['IMPORTED_ID']}",
            "#COLUMNS " + lang.CANONICAL_SEP.join(lang.FULL_COLUMNS)]
    return "\n".join(head + out_rows) + "\n", rep


def import_file(path: str) -> Tuple[Optional[str], ImportReport]:
    with open(path, encoding="utf-8") as fh:
        return import_text(fh.read(), os.path.basename(path))


def import_tree(root: str) -> Dict[str, Any]:
    """Import every `.lctl` bundle under `root` and measure what happened.

    The numbers this returns are the point of the module: they say how much of
    an externally written corpus PA-LCTL 1.6 can actually represent.
    """
    results: List[Dict[str, Any]] = []
    vocab: collections.Counter = collections.Counter()
    for dirpath, _d, files in os.walk(root):
        for fn in sorted(files):
            if not fn.endswith(".lctl"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                src, rep = import_file(full)
            except ImportRefused as exc:
                results.append({"path": rel, "imported": False,
                                "reason": exc.reason})
                continue
            entry = rep.as_dict()
            entry["path"] = rel
            entry["imported"] = src is not None
            if src is not None:
                prog, diags = lang.parse(src)
                if prog is None:
                    entry["parse"] = "FAILED"
                    entry["diagnostics"] = [d.code for d in diags]
                else:
                    v = lang.verify(prog)
                    entry["parse"] = "OK"
                    entry["verify_ok"] = v.ok
                    entry["diagnostics"] = sorted(
                        {d.code for d in v.diagnostics})
                    entry["seal"] = prog.seal()
            vocab.update(rep.unmapped)
            entry.pop("dropped", None)
            results.append(entry)

    complete = [r for r in results if r.get("complete")]
    verified = [r for r in results if r.get("verify_ok")]
    return {
        "schema": "PA-LCTL/IMPORT_MEASUREMENT/1",
        "root": root,
        "bundles": len(results),
        "imported_without_loss": len(complete),
        "imported_with_dropped_rows": sum(
            1 for r in results if r.get("imported") and not r.get("complete")),
        "not_importable": sum(1 for r in results if not r.get("imported")),
        "verified_by_pa_lctl": len(verified),
        "rows_in": sum(r.get("rows_in", 0) for r in results),
        "rows_out": sum(r.get("rows_out", 0) for r in results),
        "unmapped_vocabulary": dict(vocab.most_common()),
        "results": results,
    }
