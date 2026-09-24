"""JSON schemas for every DF artifact, and a small self-contained validator.

Gate G2 of the corpora ("every artifact validates against every schema
shipped beside it") is applied here: each schema below is written into the
container's `schemas/` directory and VERIFY validates the corresponding
artifact against it. The validator deliberately depends on nothing outside
the standard library, so VERIFY runs offline on a clean machine (gate G1).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_TYPES = {"object": dict, "array": list, "string": str, "integer": int,
          "number": (int, float), "boolean": bool, "null": type(None)}


def validate(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Return a list of violations (empty == valid)."""
    errs: List[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        ok = False
        for tt in types:
            py = _TYPES[tt]
            if tt == "integer" and isinstance(instance, bool):
                continue
            if tt == "number" and isinstance(instance, bool):
                continue
            if isinstance(instance, py):
                ok = True
                break
        if not ok:
            errs.append(f"{path}: expected type {t}, got {type(instance).__name__}")
            return errs
    if "const" in schema and instance != schema["const"]:
        errs.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, str) and "pattern" in schema:
        if not re.search(schema["pattern"], instance):
            errs.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                errs.append(f"{path}: missing required property {req!r}")
        for k, v in instance.items():
            if k in props:
                errs.extend(validate(v, props[k], f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: additional property {k!r} not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs.extend(validate(v, schema["additionalProperties"], f"{path}.{k}"))
    if isinstance(instance, list):
        it = schema.get("items")
        if isinstance(it, dict):
            for i, v in enumerate(instance):
                errs.extend(validate(v, it, f"{path}[{i}]"))
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
    return errs


_FILE = {"type": "object", "required": ["path", "bytes", "sha256"], "additionalProperties": False,
         "properties": {"path": {"type": "string"}, "bytes": {"type": "integer", "minimum": 0},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}}

STATUS_ENUM = ["BLOCKED", "SPECIFIED", "SCAFFOLDED", "IMPLEMENTED", "VERIFIED", "OPERATIONAL",
               "QUALIFIED", "IMPLEMENTED_PARTIAL", "BLOCKED_EXTERNAL_AUTHORITY",
               "BLOCKED_CAPABILITY_ABSENT", "SKIPPED", "NOT_CLAIMED"]

FEATURE_ENUM = ["SUPPORTED", "SUPPORTED_WITH_LIMITS", "APPROXIMATE", "TARGET_SPECIFIC", "UNSUPPORTED"]

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "DF_PACKAGE_MANIFEST": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_PACKAGE_MANIFEST.json",
        "title": "DF package manifest", "type": "object",
        "required": ["schema", "df_release", "package_name", "package_kind", "language",
                     "core_version", "assembled_at_utc", "file_count", "total_bytes", "files",
                     "inventory_exclusions", "inventory_exclusion_reason", "physical_execution",
                     "source_corpora", "embedded_payload", "gates_measured_here"],
        "properties": {
            "schema": {"const": "DF/PACKAGE_MANIFEST/1"},
            "df_release": {"type": "string"},
            "package_name": {"type": "string"},
            "package_kind": {"enum": ["fabric_node", "fabric"]},
            "language": {"const": "PA-LCTL"},
            "core_version": {"type": "string"},
            "assembled_at_utc": {"type": "string"},
            "file_count": {"type": "integer", "minimum": 1},
            "total_bytes": {"type": "integer", "minimum": 1},
            "files": {"type": "array", "items": _FILE, "minItems": 1},
            "inventory_exclusions": {"type": "array", "items": {"type": "string"}},
            "inventory_exclusion_reason": {"type": "string"},
            "physical_execution": {"type": "object", "required": ["NETWORK", "BACKEND"]},
            "source_corpora": {"type": "object"},
            "embedded_payload": {"type": ["object", "null"]},
            "gates_measured_here": {"type": "object"},
        },
    },
    "DF_NODE_DESCRIPTOR": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_NODE_DESCRIPTOR.json",
        "title": "DF fabric node descriptor", "type": "object",
        "required": ["schema", "df_release", "node_id", "container", "worker_id", "domain_id",
                     "group_id", "failure_domain", "trust_domain", "execution_label", "target_id",
                     "vm", "adapter", "resource_limits", "feature_classes", "bounds",
                     "physical", "self_reported"],
        "properties": {
            "schema": {"const": "DF/NODE_DESCRIPTOR/1"},
            "node_id": {"enum": ["N_SMALL", "N_MEDIUM", "N_LARGE", "N_XLARGE"]},
            "trust_domain": {"enum": ["LOCAL_TRUSTED", "LOCAL_UNTRUSTED"]},
            "execution_label": {"type": "string", "pattern": "^CLASSICAL_"},
            "feature_classes": {"type": "object", "additionalProperties": {"enum": FEATURE_ENUM}},
            "physical": {"type": "object", "required": ["physical_qpu", "physical_parallel", "physical_distributed"],
                         "properties": {"physical_qpu": {"const": False}, "physical_parallel": {"const": False},
                                        "physical_distributed": {"const": False}}},
            "resource_limits": {"type": "object", "required": ["cpu_slots", "memory_bytes", "qpu_slots", "ebit_budget"],
                                "properties": {"qpu_slots": {"const": 0}, "ebit_budget": {"const": 0}}},
        },
    },
    "DF_GATE_RESULTS": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_GATE_RESULTS.json",
        "title": "DF gate results (measured)", "type": "object",
        "required": ["schema", "df_release", "subject", "executed_at_utc", "host", "gates", "totals", "verdict"],
        "properties": {
            "schema": {"const": "DF/GATE_RESULTS/1"},
            "gates": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["id", "name", "status", "seconds", "detail"],
                "properties": {"status": {"enum": ["PASS", "FAIL", "SKIPPED"]},
                               "seconds": {"type": "number", "minimum": 0}}}},
            "totals": {"type": "object", "required": ["run", "passed", "failed", "skipped"]},
            "verdict": {"enum": ["PASS", "FAIL"]},
        },
    },
    "DF_TRANSLATION_CORPUS_RECORD": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_TRANSLATION_CORPUS_RECORD.json",
        "title": "DF translation corpus record (one JSON object per line)", "type": "object",
        "additionalProperties": False,
        "required": ["record_id", "container", "source", "target", "translation_rule", "status",
                     "evidence", "confidence_score"],
        "properties": {
            "record_id": {"type": "string"},
            "container": {"type": "string"},
            "source": {"type": "object", "required": ["artifact", "claim"],
                       "properties": {"artifact": {"type": "string"}, "claim": {"type": "string"},
                                      "measured": {"type": ["string", "number", "boolean", "null", "object", "array"]}}},
            "target": {"type": "object", "required": ["language", "construct", "text"],
                       "properties": {"language": {"const": "PA-LCTL"}, "construct": {"type": "string"},
                                      "text": {"type": "string"}, "row": {"type": ["string", "null"]}}},
            "translation_rule": {"type": "string"},
            "status": {"enum": STATUS_ENUM},
            "evidence": {"type": ["string", "null"]},
            "confidence_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
    },
    "DF_CAPABILITY_LEDGER": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_CAPABILITY_LEDGER.json",
        "title": "DF capability ledger", "type": "object",
        "required": ["schema", "df_release", "subject", "rule", "items", "distribution", "integrity"],
        "properties": {
            "schema": {"const": "DF/CAPABILITY_LEDGER/1"},
            "items": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["id", "title", "status", "statement", "evidence"],
                "properties": {"status": {"enum": STATUS_ENUM},
                               "evidence": {"type": ["array", "null"], "items": {"type": "string"}}}}},
            "integrity": {"type": "object", "required": ["operational_without_evidence"]},
        },
    },
    "DF_NODES": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_NODES.json",
        "title": "DF fabric node registry (pinned digests)", "type": "object",
        "required": ["schema", "df_release", "nodes"],
        "properties": {"schema": {"const": "DF/NODES/1"},
                       "nodes": {"type": "array", "minItems": 1, "items": {
                           "type": "object", "required": ["node_id", "container", "relative_path", "sums_sha256",
                                                          "zip_sha256", "payload_tree_sha256"]}}},
    },
    "DF_PROVENANCE": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_PROVENANCE.json",
        "title": "DF provenance", "type": "object",
        "required": ["schema", "df_release", "assembled_at_utc", "assembly_host", "lineage", "ladders"],
        "properties": {"schema": {"const": "DF/PROVENANCE/1"},
                       "ladders": {"type": "object", "required": ["quantum_boundary", "physical_qpu", "physical_parallel", "physical_distributed"],
                                   "properties": {"quantum_boundary": {"const": "QUANTUM_BOUNDARY_NOT_CROSSED"},
                                                  "physical_qpu": {"const": False}, "physical_parallel": {"const": False},
                                                  "physical_distributed": {"const": False}}}},
    },
    "DF_SOURCE_AUTHORITY": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://df.local/schema/DF_SOURCE_AUTHORITY.json",
        "title": "DF source authority", "type": "object",
        "required": ["schema", "df_release", "language_corpora", "vm_containers", "translation_rules"],
        "properties": {"schema": {"const": "DF/SOURCE_AUTHORITY/1"},
                       "language_corpora": {"type": "object", "required": ["packages"]},
                       "vm_containers": {"type": "array", "minItems": 1},
                       "translation_rules": {"type": "array", "minItems": 1}},
    },
}

#: artifact path -> schema name (per container kind)
ARTIFACT_SCHEMA_MAP_NODE = {
    "MANIFEST.json": "DF_PACKAGE_MANIFEST",
    "node/NODE_DESCRIPTOR.json": "DF_NODE_DESCRIPTOR",
    "conformance/DF_GATE_RESULTS.json": "DF_GATE_RESULTS",
    "reports/DF_CAPABILITY_LEDGER.json": "DF_CAPABILITY_LEDGER",
    "provenance/DF_PROVENANCE.json": "DF_PROVENANCE",
    "authority/DF_SOURCE_AUTHORITY.json": "DF_SOURCE_AUTHORITY",
}
ARTIFACT_SCHEMA_MAP_FABRIC = {
    "MANIFEST.json": "DF_PACKAGE_MANIFEST",
    "fabric/NODES.json": "DF_NODES",
    "conformance/DF_GATE_RESULTS.json": "DF_GATE_RESULTS",
    "reports/DF_CAPABILITY_LEDGER.json": "DF_CAPABILITY_LEDGER",
    "provenance/DF_PROVENANCE.json": "DF_PROVENANCE",
    "authority/DF_SOURCE_AUTHORITY.json": "DF_SOURCE_AUTHORITY",
}
