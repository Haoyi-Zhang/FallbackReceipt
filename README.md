# Receipt-Closed Fallback

Companion artifact for **Receipt-Closed Fallback: Budgeted Recovery for Service Controllers**, targeting ACM Transactions on Computer Systems (TOCS).

The implementation connects a finite accept/fallback/reject selector to persistent local effects. A stable attempt closes as `DONE` (one bound effect) or `CANCELED` (no effect and future execution fenced). Recovery charges and tickets precede closure. A separately implemented certificate checker reconstructs the finite certified-charge optimum; another program replays complete event histories.

The Bellman objective is worst certified charge, not actual cost for a particular receiver. A one-job counterexample with certified accept/fallback charges 10/5 and actual costs 1/5 is checked in `tests/test_cost_semantics.py`: the certified optimum selects fallback while accept is cheaper in actual cost. Per-effect actual <= certified still gives actual budget safety; tightness is not assumed.

## Requirements

Python 3.11 or newer with standard-library SQLite, on a POSIX host for process crash tests. Experiments use at most four CPU affinities, synchronous crash workers, and four local race threads. The experiment runner limits address space to 3,250 MiB and CPU to 600 seconds per process. This is a finite/local prototype, not a distributed production implementation.

## Run the artifact

From this directory:

```sh
python -S verify_all.py
python -S verify_all.py --reproduce
```

The first command runs all **80 tests**, reconstructs and checks retained results, recomputes exact comparisons, graph checks, and identity-retention checks, and runs the recovery example. The second also generates all ten experiment phases in a fresh temporary directory and compares logical results. A nonzero exit is a failed check; finite checks do not establish general correctness.

Individual entry points:

```sh
PYTHONWARNINGS=error::ResourceWarning python -S -m unittest discover -s tests -v
python -S verify_results.py --results results
python -S verify_results.py --phase databases --results results
python -S verify_results.py --phase model --results results/model
python -S comparisons.py --verify
python -S retention_checks.py --verify
python -S examples/run_contract.py
python -S src/game.py results/pilot/spec.json /tmp/receipt-policy.json
python -S src/checker.py results/pilot/spec.json /tmp/receipt-policy.json
```

`examples/run_contract.py --output EMPTY_DIRECTORY` preserves the example databases and JSON history. An existing nonempty directory is refused. The example suppresses a reply after the first effect, reopens the controller, closes that same attempt, completes all three jobs, and rejects an incomplete exported history. It produces three effects, actual cost nine, and a valid history under certified worst cost ten. This example is not itself an OS crash experiment; the fault campaign uses separate process cut points.

The standalone artifact does not need the manuscript directory. In the full project only, the following optional command also compiles it:

```sh
python -S verify_all.py --paper ../paper
```

## What the retained evidence says

The finite selector agrees with an uncached tree oracle on 1,200 generated cases (911 feasible) and 23,040 exhaustive micro-cases (16,864 feasible). The checker rejects 2,072 of 2,072 structural/value mutations. Semantic producer changes are separate: 49/296 recovery changes and 132/296 score changes are incompatible; unchanged valid certificates are not false negatives.

There are 576 eight-task contracts per policy from two 128-row Azure excerpts. The mapping is fitted only to the code excerpt and reused unchanged for conversation. The optimal fixed-sequence baseline enumerates **6,561 sequences per contract**. Both adaptive and optimal open-loop are feasible in **345/576**, with equal worst certified charge in every common feasible case. The four restricted physical policies minimize over a score-prescribed physical candidate and reject at each full budget state; they may choose cheaper reject even when the physical action is safe. They do not choose between accept and fallback based on remaining budgets. The 22 extra contracts relative to the union of restricted threshold/constant policies do **not** establish feedback advantage: optimal open-loop finds them too. A separate Cartesian enumeration checks the fixed-sequence formula on 240 specifications and 3,120 action sequences, with zero disagreement.

Five exploratory fixed-budget perturbations cover 320 further comparisons. Feasible counts per 64 are 45, 45, 37, 41, and 52 for unchanged, recovery+1, durations+1, costs+1, and score rotation; adaptive and optimal open-loop tie throughout. This is post-development sensitivity analysis, not preregistered statistical generalization.

The two-crash protocol model contains 79 states and 91 edges; the four-crash model has 495 states and 633 edges. After removing further-crash transitions, every reached nonterminal state has a productive successor, and there is no productive cycle. All maximal productive paths terminate within six steps in the checked graphs. Idle/service-unavailable stuttering is excluded; fairness and bounded service times remain assumptions.

The local implementation evidence comprises 30 fault schedules with complete replay, five expected negative controls, 30 strategy paths, 171 adapter executions, and 270 receiver races. The verifier requires the exact 62-file pilot/30-case database inventory, not a nonempty glob. All 31 pairs match their JSON bindings, states, ordered events, attempts and receiver terminal/effect records, with job counters and local adapter/target effects checked as well. Integrity, foreign-key, terminal-schema and one-pending-index checks pass without sidecars. All evidence reads use immutable, read-only SQLite connections; negative tests edit disposable copies only. Scaling and latency raw samples are retained; their quantiles are descriptive measurements, not production SLOs.

## Implementation and evidence map

The trace grid's `planning_ms` times each complete policy branch: adaptive includes synthesis and checking for feasible cases, restricted policies include setup and synthesis without checking, and the recovery ablation includes true-model reevaluation. Those timings are not a synthesis-only comparison. The separate horizon-scaling campaign measures synthesis alone.

- `src/game.py`: finite minimax synthesis and reachable strategy export.
- `src/evidence_check.py`: exact database inventory and read-only database/JSON/local-effect correspondence, with no runtime constructors.
- `src/checker.py`: separately implemented transition and optimality reconstruction, strict schemas, exact closure, bounded-state refusal.
- `src/open_loop.py`: exact fixed-sequence envelope comparator with input-bound catalogue and explicit search-size refusal.
- `src/oracle.py`: direct small-game tree enumeration.
- `src/protocol_model.py`: protocol state explorer and four weakened variants, without importing runtime or replay logic.
- `src/runtime.py`: read-only validated input snapshots, persistent namespace and adapter/target bindings, one pending attempt, receiver terminal facts, recovery tickets, and settlement.
- `src/runtime_worker.py`: restartable local operations and deliberate owned fault exits.
- `src/trace_check.py`: strict record typing and complete-history reconstruction; no runtime import.
- `src/workload.py`: deterministic request-shape mapping, not a predictor.
- `reproduce.py`, `verify_results.py`: ten experiments and result reconstruction.
- `comparisons.py`: strong comparator, independent Cartesian check, productive-path check, and exploratory perturbations.
- `retention_checks.py`: completed-history identity counts and three disposable delayed-request/tombstone controls.
- `proofs/`: complete hand arguments for the stated abstractions.
- `docs/`: methods, source provenance, and boundaries.
- `claim_evidence_ledger.csv`: each retained claim and its proof/test/raw evidence.
- `bibliography_audit.csv`: 64 cited records, 63 scholarly publications plus one dataset. Original source checks are inherited and dated; individual source-check dates are retained. The table is not a claim that every full text was freshly reviewed.

## Trust and limits

Stable identifiers and atomic local transactions are assumed. The receiver must represent all charged effects within its terminal transaction. An actual cache or storage system outside that boundary needs its own binding and envelope argument. Final rows cannot prove cross-database real-time order. Recovery tickets correlate records; they are not cryptographic signatures. The checker re-solves the finite game and is not a small verified proof kernel. Separate programs share the mathematical specification and Python semantics, so common-mode errors remain possible. No proof assistant verifies the Python/SQLite implementation.

The public excerpts contribute request order, timestamps, and token counts only. Safety labels, envelopes, budgets, and service behavior are synthetic. There is no predictor training/inference, production utility claim, multi-machine latency, replication, power-loss test, or Byzantine guarantee. Protocol correctness and adaptive-control advantage are different questions; the latter is not demonstrated on this grid.

## Retained identity evidence

For one completed n-job contract with F charged recoveries, attempt identifiers are bounded by n+F. The check replays 31 full histories and separately validates the counts in 171 refinement rows; three full histories attain the bound. Each of three local negative controls retains a cancellation across a receiver reopen (zero delayed effects), then deliberately erases that owned tombstone (one delayed effect). This demonstrates an assumption boundary, not a supported reclamation operation or a global storage bound.
