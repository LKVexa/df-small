"""
JA -> PA re-designation engine.

The rewrite is *token-structural*, never a blind substring substitution.
A blind `JA` -> `PA` replace would corrupt ordinary English and technical
vocabulary that happens to contain the bigram: Java, JavaScript, Jacobi,
Jacobian, January, jagged, jargon, trajectory, adjacency, major, ninja.
Every rule below is anchored to a token boundary or to a case transition
that only occurs in the JA designation family.

Rules, applied in order, first match wins, on each identifier-like token:

  R1  exact token           JA / Ja / ja                     -> PA / Pa / pa
  R2  exact token           JAI/Jai/jai, JAXD/Jaxd/jaxd,
                            JAUI/Jaui/jaui, JXD/jxd          -> PAI/... /pxd
  R3  prefix + delimiter    JA_x, JA-x, ja_x, ja-x           -> PA_x, ...
  R4  designation prefix    JAXD*, jaxd*, JAI_*, jai_*       -> PAXD*, ...
  R5  camel prefix          Ja<Upper>...                     -> Pa<Upper>...
  R6  camel infix           <Upper>Ja<Upper>                 -> <Upper>Pa<Upper>
  R7  suffix + delimiter    x_JA, x-JA, x_ja, x-ja           -> x_PA, ...
  R8  infix + delimiters    x_JA_y, x-JA-y, x_ja_y           -> x_PA_y, ...
  R9  file extensions       .jai .jaui .ja .jxd              -> .pai .paui .pa .pxd

Anything not matched is left byte-identical. Every applied rule is counted,
so the emitted mapping ledger is auditable and the transform is reversible.
"""

from __future__ import annotations

import collections
import re
from typing import Dict, Iterable, List, Tuple

# A token is a maximal run of identifier / path characters.
TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*|[A-Za-z0-9_]")

# Words that contain the bigram but are never designations. Kept as an
# explicit assertion set: the rules below must not touch any of them, and
# `self_check()` proves it.
PROTECTED_WORDS = (
    "Java", "java", "JavaScript", "javascript", "Jacobi", "Jacobian",
    "jacobian", "January", "january", "jagged", "jargon", "jam", "Jakarta",
    "trajectory", "Trajectory", "TRAJECTORY", "trajectories", "adjacency",
    "adjacent", "Adjacency", "major", "Major", "MAJOR", "majority", "ninja",
    "jasmine", "Jaccard", "jaccard", "JavaVM", "objectja",
)

_EXACT: Dict[str, str] = {
    "JA": "PA", "Ja": "Pa", "ja": "pa",
    "JAI": "PAI", "Jai": "Pai", "jai": "pai",
    "JAXD": "PAXD", "Jaxd": "Paxd", "jaxd": "paxd",
    "JAUI": "PAUI", "Jaui": "Paui", "jaui": "paui",
    "JXD": "PXD", "Jxd": "Pxd", "jxd": "pxd",
}

_EXT: Dict[str, str] = {
    ".jai": ".pai", ".jaui": ".paui", ".ja": ".pa",
    ".jxd": ".pxd", ".JAI": ".PAI", ".JA": ".PA",
}

_CASE = {"JA": "PA", "Ja": "Pa", "ja": "pa"}

# Segment-level rules. A *segment* is a run between `.`, `_` and `-`
# delimiters, so `JA_Installer_jai_Corpus` is segmented as
# JA / Installer / jai / Corpus and every segment is judged on its own.
_DELIM_SPLIT = re.compile(r"([._\-]+)")

# Camel prefix is restricted to the mixed-case form `Ja<Upper>` so that
# all-caps vocabulary such as JAVA is never touched.
_S_CAMEL_PREFIX = re.compile(r"^(Ja)(?=[A-Z])")
_S_CAMEL_INFIX = re.compile(r"([A-Za-z0-9])(Ja)(?=[A-Z])")
_S_NUM_PREFIX = re.compile(r"^(JA|Ja|ja)(?=\d)")
_S_DESIG_PREFIX = re.compile(r"^(JAXD|JAUI|JAI|jaxd|jaui|jai|Jaxd|Jaui|Jai)(?=[A-Za-z0-9])")


class Redesignator:
    """Deterministic JA -> PA token rewriter with a full audit trail."""

    def __init__(self) -> None:
        self.rule_counts: collections.Counter = collections.Counter()
        self.token_map: Dict[str, str] = {}
        self.token_counts: collections.Counter = collections.Counter()

    # -- single segment ----------------------------------------------------
    @staticmethod
    def map_segment(seg: str) -> Tuple[str, str]:
        if seg in _EXACT:
            return _EXACT[seg], ("R1" if seg in ("JA", "Ja", "ja") else "R2")

        m = _S_DESIG_PREFIX.match(seg)
        if m:
            return _EXACT[m.group(1)] + seg[m.end():], "R4"

        m = _S_CAMEL_PREFIX.match(seg)
        if m:
            return "Pa" + seg[2:], "R5"

        if _S_CAMEL_INFIX.search(seg):
            return _S_CAMEL_INFIX.sub(
                lambda mm: mm.group(1) + _CASE[mm.group(2)], seg), "R6"

        m = _S_NUM_PREFIX.match(seg)
        if m:
            return _CASE[m.group(1)] + seg[2:], "R3"

        return seg, ""

    # -- single token ------------------------------------------------------
    def map_token(self, tok: str) -> Tuple[str, str]:
        """Return (new_token, rules). `rules` is '' when unchanged."""
        low = tok.lower()
        if "ja" not in low and "jxd" not in low:
            return tok, ""

        parts = _DELIM_SPLIT.split(tok)
        rules: List[str] = []
        changed = False
        for i, part in enumerate(parts):
            if i % 2:                      # delimiter run, keep verbatim
                continue
            new, rule = self.map_segment(part)
            if rule:
                parts[i] = new
                rules.append(rule)
                changed = True
        if not changed:
            return tok, ""
        return "".join(parts), "+".join(sorted(set(rules)))

    # -- whole text --------------------------------------------------------
    def map_text(self, text: str) -> str:
        # The guard must match `map_token`'s guard exactly. `jxd` carries no
        # `ja` bigram, so testing for `ja` alone silently skipped every
        # JAXD-family extension. Regression-covered by `self_check`.
        low = text.lower()
        if "ja" not in low and "jxd" not in low:
            return text

        def _sub(m: re.Match) -> str:
            tok = m.group(0)
            new, rule = self.map_token(tok)
            if rule:
                self.rule_counts[rule] += 1
                self.token_counts[tok] += 1
                self.token_map.setdefault(tok, new)
            return new

        return TOKEN_RE.sub(_sub, text)

    # -- JSON values -------------------------------------------------------
    def map_json(self, obj):
        """Recursively rewrite every string in a decoded JSON value."""
        if isinstance(obj, str):
            return self.map_text(obj)
        if isinstance(obj, list):
            return [self.map_json(v) for v in obj]
        if isinstance(obj, dict):
            return {self.map_text(k) if isinstance(k, str) else k:
                    self.map_json(v) for k, v in obj.items()}
        return obj

    # -- audit -------------------------------------------------------------
    def report(self) -> dict:
        return {
            "rule_counts": dict(sorted(self.rule_counts.items())),
            "total_substitutions": int(sum(self.rule_counts.values())),
            "distinct_tokens": len(self.token_map),
            "token_map_sample": dict(sorted(self.token_map.items())[:400]),
            "top_tokens": [
                {"from": t, "to": self.token_map[t], "count": int(c)}
                for t, c in self.token_counts.most_common(200)
            ],
        }


def self_check() -> List[str]:
    """Prove the rules never touch protected vocabulary and do the right
    thing on the designation family. Returns a list of failure strings."""
    r = Redesignator()
    failures: List[str] = []

    for w in PROTECTED_WORDS:
        out, rule = r.map_token(w)
        if out != w:
            failures.append(f"PROTECTED word {w!r} was rewritten to {out!r} by {rule}")

    expected = {
        "JA": "PA", "ja": "pa", "Ja": "Pa",
        "JA_CONFORMANCE": "PA_CONFORMANCE",
        "OMEGA_JA_CONFORMANCE_TYPE": "OMEGA_PA_CONFORMANCE_TYPE",
        "OMEGA_JA_TECH_lexer": "OMEGA_PA_TECH_lexer",
        "JA-TECH-CONF-0034899": "PA-TECH-CONF-0034899",
        "JA_R12": "PA_R12",
        "IJaStage": "IPaStage",
        "JaStage": "PaStage",
        "ja-agent": "pa-agent",
        "ja_certifier": "pa_certifier",
        "JAXD": "PAXD",
        "JAXD_Corpus_10000.jsonl": "PAXD_Corpus_10000.jsonl",
        "jaxd_compiler": "paxd_compiler",
        "JA_Installer_jai_Corpus_v0.1.0": "PA_Installer_pai_Corpus_v0.1.0",
        "experience.jaui": "experience.paui",
        "01_vapor_marble_workspace.jxd": "01_vapor_marble_workspace.pxd",
        "model.jai": "model.pai",
        "SOPHIA_JA_Language": "SOPHIA_PA_Language",
        "reducedSOPHIA_JA_Language_Technical_Suite": "reducedSOPHIA_PA_Language_Technical_Suite",
        "jai": "pai",
        "corpus_ja": "corpus_pa",
        "x_JA_y": "x_PA_y",
    }
    for src, want in expected.items():
        got, rule = r.map_token(src)
        if got != want:
            failures.append(f"token {src!r}: expected {want!r}, got {got!r} (rule {rule or 'none'})")

    # text-level checks: surrounding prose must be untouched
    cases = [
        ("The Java runtime computes a Jacobian along the trajectory.",
         "The Java runtime computes a Jacobian along the trajectory."),
        ("JA_CONFORMANCE runs on the JA language in January.",
         "PA_CONFORMANCE runs on the PA language in January."),
        ('{"title": "JA Function", "file": "a.jai"}',
         '{"title": "PA Function", "file": "a.pai"}'),
        # Regression: a string carrying jxd but no `ja` bigram must still be
        # rewritten. The original guard short-circuited and leaked these.
        ("compile_jxd.cmd invokes jxd_compiler on x.jxd",
         "compile_pxd.cmd invokes pxd_compiler on x.pxd"),
        ("JXD", "PXD"),
    ]
    for src, want in cases:
        got = r.map_text(src)
        if got != want:
            failures.append(f"text {src!r}: expected {want!r}, got {got!r}")

    return failures


if __name__ == "__main__":  # pragma: no cover
    fails = self_check()
    if fails:
        print("SELF_CHECK_FAIL")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)
    print("SELF_CHECK_PASS")


# --------------------------------------------------------------------------
# Delivery audit (PA21.3)
# --------------------------------------------------------------------------
#
# `self_check()` proves the rewriter is correct. It does not prove the rewriter
# was ever *run* on what shipped -- and in PA21.2 it had not been: all 292
# capability-ledger items still read `"target": "JA21 ..."` while `selfcheck`
# reported the redesignation gate as passing. `audit_tree()` closes that gap by
# walking the delivered bytes.
#
# Not every JA token is a defect. Two categories must be distinguished:
#
#   * a designation of *this* release's own artefacts  -> must be rewritten;
#   * a citation of the *historical* lineage this release came from -> must be
#     preserved, because rewriting it would falsify provenance.
#
# The allowlist below is the second category, written down. It is deliberately
# narrow and deliberately explicit: an entry here is a claim that the token
# names a historical fact.

#: JSON object keys whose values legitimately retain a JA designation because
#: they name the source lineage rather than this release.
PROVENANCE_KEY_ALLOWLIST: Tuple[str, ...] = (
    "path_on_delivery_device",   # where the JA21 source tree sat on the host
    "source_package",            # the JA-era package this was derived from
    "designation",               # the literal string "JA21 -> PA21"
    "from",                      # manifest lineage: {"from": "JA21"}
    "file",                      # a source-series document, not delivered here
    "from_designation",
    "previous_designation",
    "historical_name",
    "source_series",
)

#: Whole-file paths (relative, POSIX) whose JA tokens are historical record.
PROVENANCE_PATH_ALLOWLIST: Tuple[str, ...] = (
    "authority/PA21_2_SOURCE_AUTHORITY.json",
    "authority/PA21_3_SOURCE_AUTHORITY.json",
    "authority/SOURCE_AUTHORITY_MANIFEST.json",
    "authority/REDESIGNATION_AUDIT.json",
    "authority/VERIFICATION_REDESIGNATION.json",
    "provenance/PA21_2_PROVENANCE.json",
    "provenance/PA21_3_PROVENANCE.json",
    "reference/pacore/redesignate.py",   # this file: the rules name JA by design
)

#: Substrings that mark a line as a lineage citation wherever it appears.
#
# A second, narrower category: prose that *discusses* the designation change.
# A document that explains "this field previously read JA21" must be allowed to
# say so. These markers are deliberately phrase-shaped rather than token-shaped
# so that a bare leaked token is still caught.
PROVENANCE_LINE_MARKERS: Tuple[str, ...] = (
    "JA21 -> PA21",
    "JA21 upgrade",
    "formerly JA21",
    "JA21 ->",
    "JA -> PA",
    "formerly",
    "previously read",
    "previously reported",
    "used to read",
    "shipped reading",
    "prompts_workflows/",
)

#: Extensions worth walking. Binary payloads are hashed, not rewritten.
_TEXT_EXT = (".json", ".md", ".txt", ".py", ".ebnf", ".pal", ".cfg", ".ini",
             ".yaml", ".yml", ".lctlc")


_KEY_RE = None


def _line_is_allowlisted(line: str) -> bool:
    """True when a line is a lineage citation rather than a leaked token."""
    global _KEY_RE
    if any(marker in line for marker in PROVENANCE_LINE_MARKERS):
        return True
    if _KEY_RE is None:
        import re as _re
        _KEY_RE = _re.compile(
            r'"(' + "|".join(_re.escape(k)
                             for k in PROVENANCE_KEY_ALLOWLIST) + r')"\s*:')
    return bool(_KEY_RE.search(line))


def audit_tree(root: str) -> Dict[str, object]:
    """Walk `root` and report designation tokens that should have been rewritten.

    Returns a record; the caller decides whether a non-empty result is fatal.
    Nothing is modified.
    """
    import os as _os

    r = Redesignator()
    unrewritten: List[Dict[str, object]] = []
    files_scanned = 0
    token_total = 0
    allowlisted_hits = 0

    for dirpath, _dirs, files in _os.walk(root):
        for name in sorted(files):
            full = _os.path.join(dirpath, name)
            rel = _os.path.relpath(full, root).replace(_os.sep, "/")
            if not rel.endswith(_TEXT_EXT):
                continue
            if rel in PROVENANCE_PATH_ALLOWLIST:
                allowlisted_hits += 1
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            files_scanned += 1
            hits: List[Dict[str, object]] = []
            for lineno, line in enumerate(text.splitlines(), 1):
                if _line_is_allowlisted(line):
                    allowlisted_hits += 1
                    continue
                if r.map_text(line) != line:
                    hits.append({"line": lineno,
                                 "before": line.strip()[:160],
                                 "after": r.map_text(line).strip()[:160]})
            if hits:
                token_total += len(hits)
                unrewritten.append({"path": rel, "count": len(hits),
                                    "sample": hits[:3]})

    return {
        "schema": "PA-LCTL/REDESIGNATION_AUDIT/2",
        "root": root,
        "files_scanned": files_scanned,
        "allowlisted": allowlisted_hits,
        "unrewritten_files": unrewritten,
        "unrewritten_tokens": token_total,
        "key_allowlist": list(PROVENANCE_KEY_ALLOWLIST),
        "path_allowlist": list(PROVENANCE_PATH_ALLOWLIST),
        "verdict": "AUDIT_PASS" if not unrewritten else "AUDIT_FAIL",
    }
