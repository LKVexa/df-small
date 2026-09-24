# Security scope

Use trusted local source, executable, state and output directories. Inventory
verification rejects links, traversal, malformed hashes and duplicate records,
but assumes that an attacker cannot concurrently replace the source tree.

Key generation preserves existing paths. A failed second-file write may leave
an explicitly requested new private key; inspect command results before use.
Never commit generated keys or use development trust as production authority.

Native regression and sanitizer results are not formal proofs of sandbox isolation.
Physical cards, production key provisioning and hardware qualification are outside
this host model. Report reproducible issues without uploading private key material.
