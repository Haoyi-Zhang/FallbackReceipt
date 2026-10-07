# Experimental method

## Frozen question

The executable question is whether a fully observed finite controller over frozen score classes can choose accept, fallback, or reject within declared drift, recovery, unsafe-effect, bad-outcome, and certified-cost budgets, and whether a persistent receiver-closed execution can realize the same transitions after owned crashes.

All claim-bearing grids, seeds, mappings, baselines, and failure criteria are encoded in `reproduce.py`, `src/workload.py`, and `verify_results.py`. The microdomain and contract grids are exact populations for their stated inputs. Generated cases and race repetitions are bounded samples, not exhaustive domains or production-service samples. Timing is descriptive and excluded from equality comparison.

## Finite selector and certificates

`src/game.py` computes a minimax Bellman certified-charge value over `(job, drift, recoveries, unsafe, bad, cost)`. It exports only selected nonterminal rows reachable from the root. `src/checker.py` imports no producer code: it reconstructs transitions, recomputes optimality or infeasibility, validates every selected outcome, recomputes the producer's visited-state count, and requires exact reachable-row equality. State-limit refusal is distinct from infeasibility.

The generated-oracle phase uses fixed seed `902771` and 1,200 instances over horizons 1--4. The exhaustive microdomain enumerates 23,040 contracts from the frozen tiny domains in `reproduce.py`; it is not random. `src/oracle.py` independently enumerates alternating game trees. Both populations have zero producer/oracle mismatch.

The mutation phase uses 300 deterministic source candidates, 296 of which are feasible. Structural/value mutations alter root value, root presence, action, extraneous row, immediate charge, outcome data, visited-state metadata, and related witness surfaces, totaling 2,072; every one is rejected. Two semantic producer mutations change the model itself. Their partial rejection rates measure witness sensitivity and are not presented as malformed-certificate coverage.

## Protocol model

`src/protocol_model.py` imports neither runtime nor replay code. For one non-idempotent logical action, it records controller phase, active identifier, durable reservation, dispatched requests, receiver terminal status, crash/restart counts, and durable recovery charges. Execute and close are atomic; delayed execute may arrive after a crash or close attempt.

The exact comparison point uses two crashes and four identifiers (79 states, 91 edges). A retained sweep repeats the same safety and no-further-crash completion checks for 0--4 crashes, reaching 495 states and 633 edges at four. An existential completion check and a universal productive-path traversal are both executed. The latter rejects every nonterminal dead end and productive cycle after further-crash edges are removed, for every reached state at bounds 0--4. It establishes termination of maximal productive paths, not wait-freedom, fairness, or a wall-clock bound.

Four localized mutations remove reserve-before-send, stable-ID recovery, cancellation fencing, or charge-before-close. The explorer retains shortest decisive traces and the verifier checks their lengths exactly. A separate two-world witness records identical sender observations with and without an already-completed effect.

## Cross-trace workload-derived policy grid

The header and first 128 rows of both `AzureLLMInferenceTrace_code.csv` and `AzureLLMInferenceTrace_conv.csv` are frozen under `data/`. The code excerpt is the calibration source: empirical terciles of context tokens, generated tokens, and positive interarrival gaps freeze an ordinal pressure score and bounded resource envelopes. The same mapping is applied to the conversation excerpt without per-source refitting. Thirty-two nonoverlapping eight-row windows contribute only timestamp and token-count shape. The derived score classes, durations, costs, labels, and budgets are artifact-defined finite declarations, not Azure measurements or predictor outputs.

Each of adaptive, always-accept, threshold-1, threshold-2, always-fallback, and ignore-recovery sees the same 576 contracts: 288 from each source. The four restricted physical policies allow `(score-prescribed physical action, reject)` at each job. The Bellman minimization still uses the full remaining-budget state and may reject for lower certified charge even if the physical action has a safe continuation. Only the physical candidate is fixed; the execute/reject choice is state-dependent. The one-job safe-accept charge 1, L=1, reject charge 0 boundary is exercised in `tests/test_cost_semantics.py`. The ignore-recovery policy is synthesized optimistically and reevaluated under the true recovery charge. `verify_results.py` reconstructs the calibration, all 32 windows, and every one of the 3,456 method/case rows before accepting the retained counts. The conversation source is a no-refit transfer check, not a statistical train/test generalization claim.

## Runtime, crash, and replay campaigns

A persistent controller namespace and durable sequence create attempt identifiers unique across controllers. Each reservation immutably binds identifier, adapter, target, and envelope. Recovery atomically increments the job and attempt recovery counters and stores a fresh 32-hex ticket before `close`; the receiver echoes that ticket, and settlement requires exact match and receipt origin.

The fault phase runs ten owned cut schedules on each of admission, cache, and tier adapters. Exit code 73 denotes the injected crash. Every schedule retains a complete event history plus controller/receiver database snapshots, is completed by bounded clean restarts, and is replayed independently. Its five mechanism controls comprise two legal operation orderings (close before execute and execute before close), plus fresh-ID duplication, release without a receiver fence, and reservation without completion. The independent protocol model separately checks unfenced queries and uncharged settlement; unit tests separately check reservation while pending and fault-budget exhaustion.

The refinement phase enumerates all 30 adversarial paths through the pilot strategy, then runs each physical step over all three adapters as applicable (171 executions). It realizes requested recovery counts with canceled attempts or post-effect lost replies and requires replay to reconstruct the exact abstract path.

The concurrency phase executes duplicate-execute, execute/close, and conflicting-reuse races for all three adapters, 30 repetitions each (270 cases). Controller initialization, reserve, and reject races are covered by unit tests. Winner frequencies are scheduler-dependent and have no probabilistic interpretation.

## Database and result verification

At phase completion, databases are checkpointed and closed. `verify_results.py` uses `src/evidence_check.py` to require exactly the pilot controller/receiver pair and ten named schedules for each of admission, cache and tier (62 databases). Missing or extra databases, missing case records, or WAL/SHM/journal sidecars are rejected. There is no JSON-only fallback. Connections use escaped `mode=ro&immutable=1` URIs and `PRAGMA query_only=ON`; no runtime constructor or schema-writing operation is invoked.

Retained workflow output is a separate optional cohort: only the example pair
under `current/example/` and the same exact pilot/fault database paths under
`current/reproduced/` are recognized alongside the canonical packet. They never
replace missing canonical files or increase its checked counts. Unlisted
databases in either cohort or anywhere else remain errors; sidecar rejection
is unchanged. Selecting `results/current/reproduced` as the verifier root checks
that cohort's own complete 62-database packet rather than counting it twice.

For every pair, the verifier checks integrity and foreign keys; bound specification/certificate, namespace, adapter and initial budget; controller state, ordered events and all attempt columns; reconstructed job state, recovery and attempt counters; terminal rows and full effect adapter/target/quantity bindings; and the admission queue, cache and tier side-effect tables. It compares the database-derived history to JSON and runs the existing replay checker on it. The pilot's two copies of each exported JSON record and all 30 schedule summaries must agree as well. `python -S verify_results.py --phase databases --results results` runs this exact packet check alone.

`tests/test_evidence.py` checks unchanged bytes across read-only verification and mutates only temporary copies. It exercises all 62 one-at-a-time database omissions, DONE effect deletion for all three adapters, wrong effect/terminal/attempt targets, metadata bindings, states, events, counters and local side-effect tables. These are executed negative controls for packet consistency, not claims of damage to the retained originals, hostile tamper resistance, authenticated provenance, or cross-database real-time ordering. The supplied databases matched all 31 JSON histories in a read-only intake check.

The full verifier additionally reruns the protocol model and oracles, regenerates all 3,456 policy rows and descriptive quantiles, and checks retained summaries. The `model` phase has a separate flat-output validator described in `proofs/protocol-model.md`; it does not require unrelated phases or fabricate their files.

## Scale and timing

Scaling uses 20 feasible generated contracts at each horizon 4, 6, 8, 10, and 12. Reported state counts and certificate bytes are complete per retained case. Local timing alternates direct receiver execution with reserve--execute--settle for eight repetitions and 2,048 operations per method; medians/p95/p99 characterize only this host and storage mode.

## Strong comparison and exploratory sensitivity

`comparisons.py` records and recomputes four JSON evidence files. The exact fixed-sequence comparator exhausts 6,561 sequences per eight-task contract. Its closed-form coordinate maxima are proved in `proofs/open-loop.md` and checked by independent Cartesian enumeration on 240 specifications and 3,120 sequences. Adaptive and optimal open-loop each admit 345/576 trace contracts, with no cost advantage. Restricted-policy union differences must not be described as feedback gains.

The five sensitivity variants are unchanged inputs, recovery_time+1, both physical durations+1, both costs+1, and cyclic score relabeling. For D=F=1, all 32 windows and both budget levels are retained, with original budgets held fixed rather than compensated. Counts per 64 are 45,45,37,41,52 for both adaptive and optimal open-loop, with equal worst costs wherever feasible. These 320 cases were selected as transparent one-step perturbations after inspecting the original grid. This is exploratory, not preregistered or statistically independent validation. No predictive model is trained, so selection of synthetic mappings and baselines, rather than fitting a model to labels, is the main overfitting concern.

## Current API boundary tests

The runtime deep-copies and recursively freezes validated specification/certificate inputs before exposing read-only accessors. The replayer checks exact scalar kinds before comparing values, preventing float/int and Boolean/int aliases. The core `tests/` tree schedules 87 regression tests covering those failures, catalogue binding, refusal semantics, the comparator oracle, productive paths, retained-identity bounds, delayed-request cancellation fences, a full reply-loss example, and refusal to overwrite an existing reproduction directory. A cost-semantics regression also distinguishes fixed certified charge from variable admissible actual costs. Two owned SQLite regressions check current-cohort storage compatibility and rejection of unlisted databases at historical and current roots. The integrated runner and scientific CI additionally schedule six portable frontier-limit tests from `regressions/test_frontier_limits.py`; their own 1,152-contract concrete-world check does not alter the retained 3,024-contract grid or campaign denominators. Dated test receipts are retained for their original cohorts. Test inventory is not a measure of novelty or proof completeness, and does not imply that every test has run on every platform.

## Reproduction interpretation

The retained measurements describe the September 23 local run only; the logic checker excludes timing and race-winner equality. Logical results must match independently reconstructed values, whereas latency and planner timings may vary between runs. Fresh experiments may have different duration and p99 ordering on another host. `verify_all.py --reproduce` creates its output under a temporary directory, leaves retained results unchanged, and removes its own scratch directory on exit.

The result verifier and crash-driver read helper use explicit connection closing. A regression test checks that the helper returns only closed SQLite connections; the integrated runner rejects subprocess resource warnings as well as nonzero exits.
