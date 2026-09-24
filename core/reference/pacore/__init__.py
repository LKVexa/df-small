"""
PA-LCTL reference core.

PA-LCTL is the PA-designated realization of the QUORUM LCTL language family
described by the QUORUM LCTL 1.1.x - 1.6.x prompt-and-workflow documents.

This package is the *reference authority*: it is locally executable, requires
no server, database, remote model, or network connection, and every capability
it reports as OPERATIONAL is backed by executable evidence produced by this
same code.

Normative security constants (LCTL 1.3 s50, 1.4 s4, 1.5, 1.6):

    BACKEND = none
    NETWORK = deny
"""

__all__ = [
    "lang", "ses", "commutation", "planner", "simulator",
    "protocols", "fabric", "crdt", "resilience", "erroralgebra",
    "ledgers", "conformance", "cli", "redesignate",
]

LANGUAGE = "PA-LCTL"
PROFILE = "pa.lctl.quantum.parallel.distributed"
BUNDLE_MAGIC = "PA-LCTL/1.6"
BACKEND = "none"
NETWORK = "deny"

# Version chain realized by this core, in the order the QUORUM documents
# require them to be applied.
VERSION_CHAIN = (
    ("1.1.x", "QUANTUM_COMPUTING_TECHNICAL_LANGUAGE_CREATION"),
    ("1.2.x", "PARALLEL_DISTRIBUTED_SYSTEM_EXPANSION"),
    ("1.3.x", "NATIVE_PARALLEL_DISTRIBUTED_EXECUTION_FABRIC"),
    ("1.4.x", "ADAPTIVE_PARALLEL_DISTRIBUTED_MESH"),
    ("1.5.x", "FEDERATED_PARALLEL_DISTRIBUTED_RUNTIME_MESH"),
    ("1.6.x", "HYPERFEDERATED_MASSIVELY_PARALLEL_DISTRIBUTED_EXECUTION_FABRIC"),
)

CORE_VERSION = "1.6.0-rc1"
