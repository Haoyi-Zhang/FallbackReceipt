# Experimental method

## Frozen question

The executable question is whether a fully observed finite controller over frozen score classes can choose accept, fallback, or reject within declared drift, recovery, unsafe-effect, bad-outcome, and certified-cost budgets, and whether a persistent receiver-closed execution can realize the same transitions after owned crashes.

All claim-bearing grids, seeds, mappings, baselines, and failure criteria are encoded in `reproduce.py`, `src/workload.py`, and `verify_results.py`. The microdomain and contract grids are exact populations for their stated inputs. Generated cases and race repetitions are bounded samples, not exhaustive domains or production-service samples. Timing is descriptive and excluded from equality comparison.

## Finite selector and certificates

`src/game.py` computes a minimax Bellman value over `(job, drift, recoveries, unsafe, bad, cost)`. It exports only selected nonterminal rows reachable from the root. `src/checker.py` imports no producer code: it reconstructs transitions, recomputes optimality or infeasibility, validates every selected outcome, recomputes the producer's visited-state count, and requires exact reachable-row equality. State-limit refusal is distinct from infeasibility.

The generated-oracle phase uses fixed seed `902771` and 1,200 instances over horizons 1--4. The exhaustive microdomain enumerates 23,040 contracts from the frozen tiny domains in `reproduce.py`; it is not random. `src/oracle.py` independently enumerates alternating game trees. Both populations have zero producer/oracle mismatch.

The mutation phase uses 300 deterministic source candidates, 296 of which are feasible. Structural/value mutations alter root value, root presence, action, extraneous row, immediate charge, outcome data, visited-state metadata, and related witness surfaces, totaling 2,072; every one is rejected. Two semantic producer mutations change the model itself. Their partial rejection rates measure witness sensitivity and are not presented as malformed-certificate coverage.

## Protocol model

`src/protocol_model.py` imports neither runtime nor replay code. For one non-idempotent logical action, it records controller phase, active identifier, durable reservation, dispatched requests, receiver terminal status, crash/restart counts, and durable recovery charges. Execute and close are atomic; delayed execute may arrive after a crash or close attempt.

The exact comparison point uses two crashes and four identifiers (79 states, 91 edges). A retained sweep repeats the same safety and no-further-crash completion checks for 0--4 crashes, reaching 495 states and 633 edges at four. An existential completion check and a universal productive-path traversal are both executed. The latter rejects every nonterminal dead end and productive cycle after further-crash edges are removed, for every reached state at bounds 0--4. It establishes termination of maximal productive paths, not wait-freedom, fairness, or a wall-clock bound.

Four localized mutations remove reserve-before-send, stable-ID recovery, cancellation fencing, or charge-before-close. The explorer retains shortest decisive traces and the verifier checks their lengths exactly. A separate two-world witness records identical sender observations with and without an already-completed effect.

## Cross-trace workload-derived policy grid

The header and first 128 rows of both `AzureLLMInferenceTrace_code.csv` and `AzureLLMInferenceTrace_conv.csv` are frozen under `data/`. The code excerpt is the calibration source: empirical terciles of context tokens, generated tokens, and positive interarrival gaps freeze an ordinal pressure score and bounded resource envelopes. The same mapping is applied to the conversation excerpt without per-source refitting. Thirty-two nonoverlapping eight-row windows contribute only timestamp and token-count shape. The derived score classes, durations, costs, labels, and budgets are artifact-defined finite declarations, not Azure measurements or predictor outputs.

Each of adaptive, always-accept, threshold-1, threshold-2, always-fallback, and ignore-recovery sees the same 576 contracts: 288 from each source. Fixed policies may reject only when their prescribed physical action has no safe continuation. The ignore-recovery policy is synthesized optimistically and reevaluated under the true recovery charge. `verify_results.py` reconstructs the calibration, all 32 windows, and every one of the 3,456 method/case rows before accepting the retained counts. The conversation source is a no-refit transfer check, not a statistical train/test generalization claim.

## Runtime, crash, and replay campaigns

A persistent controller namespace and durable sequence create attempt identifiers unique across controllers. Each reservation immutably binds identifier, adapter, target, and envelope. Recovery atomically increments the job and attempt recovery counters and stores a fresh 32-hex ticket before `close`; the receiver echoes that ticket, and settlement requires exact match and receipt origin.

The fault phase runs ten owned cut schedules on each of admission, cache, and tier adapters. Exit code 73 denotes the injected crash. Every schedule retains a complete event history plus controller/receiver database snapshots, is completed by bounded clean restarts, and is replayed independently. Five negative controls target fresh-ID duplication, unfenced negative query, uncharged recovery, a new attempt while pending, and progress after fault-budget exhaustion.

The refinement phase enumerates all 30 adversarial paths through the pilot strategy, then runs each physical step over all three adapters as applicable (171 executions). It realizes requested recovery counts with canceled attempts or post-effect lost replies and requires replay to reconstruct the exact abstract path.

The concurrency phase executes duplicate-execute, execute/close, and conflicting-reuse races for all three adapters, 30 repetitions each (270 cases). Controller initialization, reserve, and reject races are covered by unit tests. Winner frequencies are scheduler-dependent and have no probabilistic interpretation.

## Database and result verification

At phase completion, databases are checkpointed and closed; retained results contain no WAL/SHM sidecars. `verify_results.py` opens 62 databases with SQLite immutable read-only URIs, runs integrity and foreign-key checks, verifies the receiver's state-dependent terminal-row constraint and the controller's partial unique one-pending index, reconciles raw rows with summaries, replays all 30 complete histories, reruns the protocol model, regenerates all 3,456 cross-trace policy rows, and checks all certificate/oracle populations. It rejects missing raw records rather than trusting summaries.

## Scale and timing

Scaling uses 20 feasible generated contracts at each horizon 4, 6, 8, 10, and 12. Reported state counts and certificate bytes are complete per retained case. Local timing alternates direct receiver execution with reserve--execute--settle for eight repetitions and 2,048 operations per method; medians/p95/p99 characterize only this host and storage mode.

## Strong comparison and exploratory sensitivity

`comparisons.py` records and recomputes four JSON evidence files. The exact fixed-sequence comparator exhausts 6,561 sequences per eight-task contract. Its closed-form coordinate maxima are proved in `proofs/open-loop.md` and checked by independent Cartesian enumeration on 240 specifications and 3,120 sequences. Adaptive and optimal open-loop each admit 345/576 trace contracts, with no cost advantage. Restricted-policy union differences must not be described as feedback gains.

The five sensitivity variants are unchanged inputs, recovery_time+1, both physical durations+1, both costs+1, and cyclic score relabeling. For D=F=1, all 32 windows and both budget levels are retained, with original budgets held fixed rather than compensated. Counts per 64 are 45,45,37,41,52 for both adaptive and optimal open-loop, with equal worst costs wherever feasible. These 320 cases were selected as transparent one-step perturbations after inspecting the original grid. This is exploratory, not preregistered or statistically independent validation. No predictive model is trained, so selection of synthetic mappings and baselines, rather than fitting a model to labels, is the main overfitting concern.

## Current API boundary tests

The runtime deep-copies and recursively freezes validated specification/certificate inputs before exposing read-only accessors. The replayer checks exact scalar kinds before comparing values, preventing float/int and Boolean/int aliases. The 60 regression tests cover those failures, catalogue binding, refusal semantics, the comparator oracle, productive paths, retained-identity bounds, delayed-request cancellation fences, and a full reply-loss example. Test count is coverage evidence, not a measure of novelty or proof completeness.

## Reproduction interpretation

The retained measurements describe the September 23 local run only; the logic checker excludes timing and race-winner equality. Logical results must match independently reconstructed values, whereas latency and planner timings may vary between runs. Fresh experiments may have different duration and p99 ordering on another host. `verify_all.py --reproduce` creates its output under a temporary directory, leaves retained results unchanged, and removes its own scratch directory on exit.

The result verifier and crash-driver read helper use explicit connection closing. A regression test checks that the helper returns only closed SQLite connections; the integrated runner rejects subprocess resource warnings as well as nonzero exits.
