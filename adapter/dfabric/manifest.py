"""Inventory, hashing and integrity checks for DF containers.

Conventions (inherited from the PA21.2 packages):
* `SHA256SUMS.txt` binds every delivered file except itself
  (`sha256sum -c SHA256SUMS.txt` format);
* `MANIFEST.json.files[]` is the inventory {path, bytes, sha256}, excluding
  MANIFEST.json and SHA256SUMS.txt (both derived from the inventory) and the
  build/run artefact directories listed in `INVENTORY_EXCLUSIONS`;
* the embedded VM payload under `vm/<package>/` keeps its own sums file
  untouched, and `node/PAYLOAD_DIGEST.json` pins every payload file.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

INVENTORY_EXCLUSIONS = (".git", "__pycache__", "*.pyc", ".build", "_runs", "_scratch",
                        ".conformance.json", ".DS_Store", "Thumbs.db")
EXCLUSION_REASON = (
    "Python bytecode caches, the VM build directory (.build), fabric run "
    "outputs (_runs) and VERIFY scratch space (_scratch) are machine-specific "
    "artefacts, not release content. MANIFEST.json and SHA256SUMS.txt are "
    "excluded from their own inventory because they are derived from it. "
    "The auxiliary root FILES.sha256 is excluded to avoid circular digests.")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_excluded(rel: str) -> bool:
    if rel.startswith(("PHOTON/control_plane/runtime/", "PHOTON/shell/VEC1/runtime_state/")):
        return True
    parts = rel.split("/")
    for p in parts:
        for pat in INVENTORY_EXCLUSIONS:
            if fnmatch.fnmatch(p, pat):
                return True
    return False


def _safe_file(root: str, rel: str) -> str:
    """Reject noncanonical paths and filesystem indirection before opening."""
    if (not isinstance(rel, str) or not rel or rel != rel.strip()
            or any(c in rel for c in ("\\", ":", "\0", "\r", "\n"))
            or any(part in ("", ".", "..") or part.endswith((".", " ")) for part in rel.split("/"))):
        raise ValueError("invalid repository-relative path")
    base = Path(root).absolute()
    current = base
    for part in (None, *rel.split("/")):
        if part is not None:
            current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("filesystem link/reparse point refused")
    if not current.resolve().is_relative_to(base.resolve()):
        raise ValueError("path leaves repository")
    if current.exists() and not current.is_file():
        raise ValueError("not a regular file")
    return str(current)


def _tree(root, excluded):
    if not os.path.isdir(root):
        raise ValueError("repository directory missing")
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if not excluded(d))
        for dn in dns:
            # A sentinel checks each directory without allowing a directory as a file.
            rel = os.path.relpath(os.path.join(dp, dn, ".integrity-probe"), root).replace(os.sep, "/")
            _safe_file(root, rel)
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/")
            if not excluded(rel):
                yield rel, _safe_file(root, rel)


def walk(root: str, exclude_top: Iterable[str] = ("MANIFEST.json", "SHA256SUMS.txt")) -> List[str]:
    """Sorted paths; generated metadata cannot recursively hash itself."""
    omit = set(exclude_top) | {"FILES.sha256"}
    return sorted(rel for rel, _ in _tree(root, is_excluded) if rel not in omit)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _records(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list) or not doc["files"]:
        raise ValueError("nonempty files inventory required")
    records = {}
    for item in doc["files"]:
        if not isinstance(item, dict):
            raise ValueError("invalid inventory entry")
        rel = item.get("path")
        if not isinstance(rel, str) or rel in records:
            raise ValueError("invalid or duplicate inventory path")
        if type(item.get("bytes")) is not int or item["bytes"] < 0:
            raise ValueError("invalid byte count")
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("invalid SHA-256")
        records[rel] = item
    if type(doc.get("file_count")) is not int or doc["file_count"] != len(records):
        raise ValueError("file_count mismatch")
    return records


def inventory(root: str) -> List[Dict[str, object]]:
    inv = []
    for rel in walk(root):
        p = os.path.join(root, rel)
        inv.append({"path": rel, "bytes": os.path.getsize(p), "sha256": sha256_file(p)})
    return inv


def write_sums(root: str, extra_files: Iterable[str] = ("MANIFEST.json",)) -> str:
    """Write SHA256SUMS.txt over every inventoried file plus MANIFEST.json."""
    rels = walk(root)
    for e in extra_files:
        if os.path.isfile(os.path.join(root, e)) and e not in rels:
            rels.append(e)
    rels = sorted(rels)
    lines = [f"{sha256_file(os.path.join(root, r))}  {r}" for r in rels]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(root, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_sums(root: str, sums_name: str = "SHA256SUMS.txt", dynamic_prefixes=()) -> Dict[str, object]:
    """Strict sha256sum verification; malformed or incomplete evidence fails."""
    ok, bad, missing, invalid, unbound = 0, [], [], [], []
    bound = set()
    try:
        with open(_safe_file(root, sums_name), encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                line = line.rstrip("\r\n")
                if not line:
                    continue
                match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
                if not match:
                    invalid.append(f"line {number}: malformed checksum")
                    continue
                digest, rel = match.groups()
                try:
                    if rel in bound or rel.startswith(tuple(dynamic_prefixes)):
                        raise ValueError("duplicate checksum path")
                    p = _safe_file(root, rel)
                    bound.add(rel)
                    if not os.path.isfile(p):
                        missing.append(rel)
                    elif sha256_file(p) == digest:
                        ok += 1
                    else:
                        bad.append(rel)
                except (OSError, ValueError) as exc:
                    invalid.append(f"line {number}: {type(exc).__name__}")
        unbound = [r for r in walk(root, exclude_top=(sums_name,))
                   if r not in bound and not r.startswith(tuple(dynamic_prefixes))]
        if not bound:
            invalid.append("empty checksum inventory")
    except (OSError, ValueError) as exc:
        invalid.append(type(exc).__name__)
    return {"ok": ok, "bad": bad, "missing": missing, "unbound": unbound,
            "invalid": invalid, "pass": not bad and not missing and not unbound and not invalid}


def check_manifest_inventory(root: str) -> Dict[str, object]:
    files, on_disk, mismatched, missing, extra, invalid = {}, [], [], [], [], []
    try:
        with open(_safe_file(root, "MANIFEST.json"), encoding="utf-8") as fh:
            m = json.load(fh, object_pairs_hook=_unique_object)
        files = _records(m)
        on_disk = walk(root)
        for rel, f in files.items():
            p = _safe_file(root, rel)
            if not os.path.isfile(p):
                missing.append(rel)
            elif os.path.getsize(p) != f["bytes"] or sha256_file(p) != f["sha256"]:
                mismatched.append(rel)
        extra = [r for r in on_disk if r not in files]
    except (OSError, ValueError, TypeError) as exc:
        invalid.append(type(exc).__name__)
    return {"inventoried": len(files), "on_disk": len(on_disk), "mismatched": mismatched,
            "missing": missing, "extra": extra, "invalid": invalid,
            "pass": not mismatched and not missing and not extra and not invalid}


def payload_digest(payload_root: str) -> Dict[str, object]:
    """Digest of an embedded VM package: every file, plus a tree digest."""
    files = []
    tree = hashlib.sha256()
    for rel, p in _tree(payload_root, lambda r: any(x in (".build", "__pycache__") or x.endswith(".pyc") for x in r.split("/"))):
        d = sha256_file(p)
        files.append({"path": rel, "bytes": os.path.getsize(p), "sha256": d})
        tree.update(f"{d}  {rel}\n".encode("utf-8"))
    return {"file_count": len(files), "total_bytes": sum(f["bytes"] for f in files),
            "tree_sha256": tree.hexdigest(), "files": files}


def check_payload(payload_root: str, digest_doc: Dict[str, object]) -> Dict[str, object]:
    try:
        records = _records(digest_doc)
        if not isinstance(digest_doc.get('tree_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', digest_doc['tree_sha256']):
            raise ValueError('invalid payload tree digest')
        for rel in records:
            _safe_file(payload_root, rel)
        now = payload_digest(payload_root)
    except (OSError, ValueError, TypeError) as exc:
        return {"pass": False, "invalid": [type(exc).__name__]}
    exp = {rel: f["sha256"] for rel, f in records.items()}
    got = {f["path"]: f["sha256"] for f in now["files"]}
    sizes = {f['path']: f['bytes'] for f in now['files']}
    changed = sorted(p for p in exp if p in got and (got[p] != exp[p] or sizes[p] != records[p]['bytes']))
    missing = sorted(p for p in exp if p not in got)
    extra = sorted(p for p in got if p not in exp)
    return {"expected_files": len(exp), "found_files": len(got), "changed": changed,
            "missing": missing, "extra": extra,
            "tree_sha256_expected": digest_doc["tree_sha256"], "tree_sha256_found": now["tree_sha256"],
            "pass": not changed and not missing and not extra and now["tree_sha256"] == digest_doc["tree_sha256"]}


def dir_digest(root: str, skip=("__pycache__",)) -> Tuple[str, int]:
    """Deterministic digest of a directory tree (used to pin core/pacore)."""
    h = hashlib.sha256()
    n = 0
    for rel, p in _tree(root, lambda r: any(x in skip or x.endswith(".pyc") for x in r.split("/"))):
        h.update(f"{sha256_file(p)}  {rel}\n".encode("utf-8"))
        n += 1
    return h.hexdigest(), n
