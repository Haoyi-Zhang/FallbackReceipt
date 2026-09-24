# Trust boundary and non-claims

## Required assumptions

1. Frozen score classes and accept/fallback time, cost, and unsafe envelopes are sound declarations.
2. The checked certificate is valid for the exact canonical specification bound by the controller.
3. Controller transactions are atomic and durable, and serialize initialization, reservation, rejection, recovery charging, and settlement.
4. A persistent controller namespace plus durable sequence prevents accidental attempt-ID collision; every attempt is immutably bound to adapter and target.
5. Receiver execute and close are linearizable per attempt identifier and atomically bind the represented effect to one terminal `DONE` or `CANCELED` row.
6. Recovery commits a fresh charge and ticket before close; close echoes that ticket; settlement accepts only the latest matching close-origin receipt after a restart.
7. Actual cost and unsafe values are no larger than the reservation envelope, and no relevant external effect escapes the receiver transaction.
8. Event export is complete and untampered for a positive replay verdict.
9. Faults eventually cease and enabled receiver operations eventually return for the conditional progress statement.
10. Storage and receiver are non-Byzantine. Tickets are correlation values, not authentication.

## What the evidence establishes

Within those assumptions, the finite-game hand proof, independent certificate/oracle checks, protocol-model exploration, runtime tests, complete crash histories, immutable database inspection, and independent replay support the bounded paper claims. They show neither that SQLite provides a replicated receiver nor that final rows alone prove real-time order between separate database files.

## Explicit non-claims

The artifact does not establish predictor accuracy, calibration, train/test generalization, real Azure labels, production unsafe-action rates, real device or cluster performance, network tail latency, general exactly-once execution, Byzantine safety, power-loss behavior beyond SQLite's local contract, fairness, unbounded horizons, partial observability, or machine-checked refinement of the Python implementation. The three adapters are effect-shape tests (append, overwrite, placement), not competitive implementations of the cited systems.

The two public excerpts are used only for arrival/token shape. Code-trace terciles freeze the final ordinal mapping; conversation rows receive it without source-specific refitting. The mapping from rows to jobs and budgets is synthetic and published, and the transfer comparison is not a statistical generalization guarantee. Local latency measures bookkeeping cost; race-winner counts are not probabilities.

## Evidence interpretation

- A completed passing checker supports a finite certificate for the declared game under its implementation assumptions, not the truth of the envelopes.
- A passing replay checks that the supplied history matches a certified path, not that events could not have been omitted before export.
- A passing protocol sweep is a bounded model check through four crashes, not a general theorem for arbitrary failure counts.
- A clean reproduction proves executable repeatability for the delivered inputs and environment class, not independent external review.

## Shared semantics and model selection

The separate checker re-solves the same Bellman problem. It is not a small trusted proof kernel or a machine-verified program. The producer, checker, and direct enumerator share a mathematical specification and language arithmetic, despite different implementations. The evidence does not exclude a common mistaken specification. The strongest open-loop baseline ties the adaptive strategy; no strict separation example or workload feedback advantage is part of the retained result.

The 320 fixed-budget perturbations are explicitly exploratory. No unseen production distribution, measured action envelope, learned checkpoint, or full Azure source file is integrated. Only the actual two 128-row excerpts in `data/` are consumed.


## Identity retention and shared resources

Receiver terminal facts remain valid only while retained. The implementation has no supported garbage collection or namespace-retirement operation. Deleting a CANCELED tombstone can let a delayed old request take effect. One completed n-job contract with at most F charged recoveries has at most n+F attempt identifiers, n effects, and F canceled identifiers. This is not a storage-byte bound or a service-wide bound over successive contracts. The shared cache/tier example keys also do not provide tenant isolation or globally shared-capacity admission; such uses require a separate allocator, resource namespace, and atomic effect-binding argument.
