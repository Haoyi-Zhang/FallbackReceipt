# Source and literature notes

## Public trace inputs

The only third-party experimental bytes are two attributed excerpts from Microsoft Azure's `Azure/AzurePublicDataset`: the header and first 128 rows of the code and conversation LLM inference traces. Their upstream blob identifiers are `62bd8109d0f2b1bc48206d397ecca48d8de03aab` and `5b9219f2caf93bbbf95ec29894e206115658e235`. The retained columns are timestamp, context-token count, and generated-token count. Attribution and license scope are in `data/AZURE-DATA-NOTICE.md`.

The code rows freeze empirical tercile thresholds. The conversation rows then receive the same bounded ordinal mapping with no per-source refit. This makes the workload specification identifiable and exactly replayable across two different public trace sources while retaining the finite model. No Azure model, endpoint, latency measurement, safety label, resource price, confidence score, or private telemetry is used. The mapping in `src/workload.py` is an artifact-defined stress model and is labeled accordingly in the paper; the transfer source is not presented as statistical model validation.

## Anchor and closest systems work

The MLSys 2026 anchor studies uncertainty estimators and fallback workflows for server provisioning, cluster management, and storage I/O admission. Its storage case extends Heimdall, a public user-space controller that admits I/O or redirects it across SSDs. We inspected the public Heimdall repository at main-tree identifier `dd0e0d1903e9c9a2c5c62c8056035d18860c40e1`. It publishes feature extraction, training, and C inference code, but the client inference file includes generated `2ssds_weights_header` files that are not present in the public tree; the repository also has no root license file. Consequently this artifact neither redistributes Heimdall code/data nor claims to reproduce its trained controller. It reuses only the cited outer placement-effect shape.

The anchor motivates task-dependent fallback but does not supply the receiver-closed persistent contract evaluated here. Learned-OS guardrail work motivates explicit properties and corrective actions. Birrell and Nelson's RPC semantics and RIFL establish the lost-reply/identity boundary. RIFL Section 6.5 also records transaction aborts under prepare identities to fence delayed prepares; cancellation fencing is therefore prior art. AFT, Beldi, distributed speculative execution, and transactional stateful serverless systems establish relevant durability or workflow mechanisms. The paper does not claim to supersede their execution substrates; it claims a bounded interface between a selective planner and a one-effect-or-none receiver outcome.

The novelty boundary is therefore narrow: combining a finite multi-budget selector, an optimality-checkable reachable certificate, a two-world necessity argument, independently model-checked receipt-closing order, persistent recovery-before-close accounting, and independent event-to-game replay. Standard components are credited rather than renamed as new primitives.

## Statistical and runtime-assurance context

Selective classification, calibration, deep ensembles, Bayesian approximations, out-of-distribution detection, conformal prediction, and conformal risk control concern the quality or statistical validity of prediction and abstention. The artifact consumes frozen score classes and makes no statistical guarantee. Simplex, shielding, safe reinforcement learning, predictive safety filters, and neural-controller verification concern action safety under their models. Receipt closure addresses a different post-selection ambiguity: whether a crash-obscured effect already happened.

## Durability and verification context

The argument relies on established concepts including linearizability, write-ahead recovery, state-machine replication, retry identity, and proof producer/checker separation. The paper explicitly distinguishes its hand proof plus executable witness checking from mechanized implementation refinements such as seL4, FSCQ, IronFleet, Verdi, and Perennial.

## Bibliography practice

The manuscript bibliography contains 64 unique records: 63 scholarly publications plus the official public-dataset record. Every record is cited in the main text. It includes foundational and recent papers across selective prediction, runtime assurance, crash recovery, proof artifacts, and serverless systems, plus the public dataset record. Ordinary venue-rule pages and template documentation are not used as scientific references. Every key is mapped in `bibliography_audit.csv` to a DOI or official scholarly/dataset record, verification basis, correction note, and check date. `paper/check_bibliography.py` fails closed if the BibTeX, citation set, or audit set diverges or the bibliography falls below 55 entries. Bibliographic fields not established by those records are omitted rather than invented.

No paper PDF or third-party implementation is included in the artifact. `external_resources.csv` inventories all 64 cited records plus the two exact consumed data files and the venue/template workflow sources, with access, license, integration, and modification fields.

## Quantitative verification and source-check scope

PRISM-games and Storm are cited for multi-objective synthesis and modular verification. Their established capabilities rule out treating those generic features as new here. Neither was executed as a performance baseline. Publication venues in their citations identify the actual published research and are not alternative targets for this manuscript.

The literature audit retains each source check's recorded date. All retained keys, normalized titles, required metadata, DOI/source locators, and actual citation use are checked structurally. Selected load-bearing records and the ICML/PMLR citation metadata were checked against official records on 2026-09-23. Structural consistency is not a fresh full-text semantic review of every attribution. Fields absent from an official source are omitted, not guessed to suppress optional BibTeX warnings. No third-party full paper is redistributed.
