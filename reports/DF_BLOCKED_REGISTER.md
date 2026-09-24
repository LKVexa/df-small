# DF_Small (N_SMALL) -- what does not work, and why

Release DF-PA21.2-1.0.0. Every item below is stated in the same voice as the operational ones: an undisclosed gap is the defect, a disclosed one is scope.

## `DFN-01` container integrity -- `VERIFIED`

every delivered byte is hashed (SHA256SUMS.txt) and the MANIFEST inventory matches disk

*G0.1/G0.2 run after sealing; the shipped results file predates the seal by construction (see provenance/DF_PROVENANCE.json assembly record) and VERIFY re-runs them live*

## `DFN-30` classical-face execution -- `BLOCKED_CAPABILITY_ABSENT`

executing the classical rows of a bundle rather than witnessing them

## `DFN-31` quantum-face execution -- `BLOCKED`

the machine has no qubit; every quantum feature is UNSUPPORTED

## `DFN-32` cross-machine federation -- `BLOCKED`

NETWORK=deny; the fabric is executed on one host

## `DFN-33` physical quantum outputs -- `BLOCKED_EXTERNAL_AUTHORITY`

PHYSICAL_PARALLEL_QPU_EXECUTION, PHYSICAL_DISTRIBUTED_QPU_EXECUTION

## `DFN-34` the target's own blockers -- `BLOCKED`

card target/AID; production trust authority; NVM/watchdog; hardware/security qualification; deployment authorization

*restated verbatim from MANIFEST.json / spec/PROFILE.md of the package (`MODEL_OPERATIONAL`; physical SIM/UICC `EXTERNAL_NOT_QUALIFIED`); not lifted by the fabric*

## Inherited from the target, verbatim

Source: MANIFEST.json / spec/PROFILE.md of the package (`MODEL_OPERATIONAL`; physical SIM/UICC `EXTERNAL_NOT_QUALIFIED`). Binding the VM to the fabric closes none of these.

* card target/AID
* production trust authority
* NVM/watchdog
* hardware/security qualification
* deployment authorization

## Findings recorded, not adjudicated

* CLI `run` is unauthenticated; the fabric uses `run-signed` (signature enforced).
* The result convention is R2.low64; arbitrary read-back needs `serve` + APDU STATE.
* Every live VM allocates ~34 MB (128 KiB words x registers + stack); the fabric records this as the worker's memory limit.
* `--image-version` changes the header, not the sealed payload.
