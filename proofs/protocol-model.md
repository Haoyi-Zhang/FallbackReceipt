# Independent receipt-closure protocol model

## Purpose and independence

`src/protocol_model.py` is a bounded labelled-transition model for one non-idempotent logical action. It imports neither the SQLite runtime nor the event replay checker. It tests the declared ordering/fencing rules and produces shortest traces when one rule is removed. It is not a proof of the Python implementation or of an arbitrary distributed receiver.

## State and transitions

A state records controller phase, active attempt identifier, per-identifier reservation and dispatched-request bits, receiver status (`NONE`, `DONE`, or `CANCELED`), crash/restart counters, and durable recovery-charge count. Receiver execute atomically changes `NONE` to `DONE` and represents one effect. Receiver close atomically changes `NONE` to `CANCELED`; delayed execute cannot cross that terminal fence.

The correct variant enforces durable reservation before dispatch, durable recovery charge before close/settle, stable closure before replacing an identifier, and a fresh identifier only after `CANCELED`. Delayed execute may occur while the controller is live, crashed, recovering, closed, or done.

## Checked properties

The explorer reports shortest paths to:

- more than one represented effect for the logical action;
- an effect without durable reservation;
- abandonment of an identifier still capable of a delayed effect;
- settlement after restart before the corresponding recovery charge.

For conditional completion it removes additional crash edges, computes reverse reachability from quiescent terminals, and requires every reached correct-model state to have at least one path to quiescence. This is existential progress after faults cease, not fairness or wait-freedom.

## Weakened variants

| Variant | Removed condition | Shortest decisive trace |
|---|---|---:|
| `send_before_reserve` | dispatch may precede durable reservation | unreserved effect 2; duplicate effect 6 |
| `fresh_retry` | recovery abandons the old identifier | duplicate effect 8 |
| `query_without_fence` | negative query is treated as cancellation | duplicate effect 8 |
| `settle_before_charge` | recovered `DONE` settles before charge | undercharged terminal 6 |

## Retained results

At the exact two-crash comparison point, the correct model reaches 79 states and 91 edges, has no safety violation, and has maximum shortest no-further-crash completion distance six. The same checks are swept through four crashes:

| Max crashes | States | Edges | Max completion steps |
|---:|---:|---:|---:|
| 0 | 5 | 4 | 4 |
| 1 | 23 | 24 | 6 |
| 2 | 79 | 91 | 6 |
| 3 | 211 | 260 | 6 |
| 4 | 495 | 633 | 6 |

Run:

```sh
set -eu
tmp=$(mktemp -d)
python -S reproduce.py --phase model --output "$tmp/model"
python -S verify_results.py --phase model --results "$tmp/model"
rm -rf "$tmp"
```

Run these commands from the standalone artifact root. `--phase model` writes `model.json` and `summary.json` directly into the selected output directory. The matching verifier recomputes the independent model, complete crash-sweep fields, shortest counterexamples, and summary; it neither reads nor creates `pilot/spec.json`. The default all-phase verifier instead expects a complete tree from `reproduce.py --phase all --output DIRECTORY`.

`tests/test_evidence.py::ModelCommandTests` executes the command pair in an empty temporary directory. It verifies a successful clean result and nonzero exits after corrupting or removing either model record, then restores and rechecks the original records. No missing experiment stages are manufactured.

The emitted lost-reply witness contains two worlds with the same durable sender observation: one with no effect and one with one effect whose reply was lost. A deterministic sender-only rule must make the same choice in both; fresh retry duplicates one world and never retrying strands the other. This establishes the need for some stable distinguishing fact under the assumptions, not the uniqueness of this API.
