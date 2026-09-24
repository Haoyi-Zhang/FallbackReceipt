# Mathematical arguments and implementation connection

These are hand-written arguments over the explicit finite model and the stated persistent protocol. The executable checker, protocol model, and replay programs validate concrete finite witnesses, but no proof assistant verifies the general statements or the Python/SQLite implementation.

## 1. Finite selective-fallback game

A specification contains a nonempty ordered list of jobs. Job `i` has score class `q_i` in `{0,1,2}`, certified cost charges `k_i(accept)` and `k_i(fallback)`, and base service times `h_i(accept)` and `h_i(fallback)`. Every cost and time is a positive integer. The remaining contract is represented by

`(i, d, f, u, l, c)`,

where `i` is the next job, `d` is remaining accepted-label drift, `f` remaining recoveries, `u` remaining unsafe effects, `l` remaining late-or-rejected outcomes, and `c` remaining certified cost charge. Score class zero is nominally unsafe for accept; fallback is declared safe. Recovery time is `rho` and the per-job deadline is `H`.

For reject, the successor advances `i` and consumes one `l` token. For accept, the adversary may keep the nominal unsafe bit or, when `d>0`, flip it and consume one drift token. Fallback has unsafe bit zero. For either physical action, the adversary chooses `r` recoveries from `0..f`. The action consumes exactly one certified cost charge `k_i(a)`, one unsafe token when its realized unsafe bit is one, `r` recovery tokens, and one late token precisely when

`h_i(a) + r*rho > H`.

Receipt closure is represented by the fact that `k_i(a)` and the unsafe bit occur once, independent of `r`. A physical receiver may report actual cost below `k_i(a)`; the abstract game still charges `k_i(a)`, which conservatively implies actual-cost safety.

A state with any negative budget is losing. A nonnegative state with `i=n` is terminal with continuation value zero. Otherwise,

`V(s) = min_a max_{e in E(s,a)} (charge(s,a,e) + V(next(s,a,e)))`,

where an action with any losing successor has infinite value.

### Theorem 1: finite-game soundness, completeness, and minimax value

For every valid finite specification and state, `V(s)` is finite exactly when a deterministic state-observing controller exists whose every declared adversarial continuation remains within all budgets. When finite, `V(s)` is the least worst-case remaining certified cost among such controllers.

**Proof.** Induct on the number of jobs remaining. At `i=n`, a nonnegative state is already complete and has value zero; a negative state violates a budget. At a nonterminal state, fix an action. If one legal outcome has a losing successor, the adversary can choose it, so no safe controller can begin with that action. If all successors are winning, the induction hypothesis supplies a safe continuation for every observed successor. Combining those continuations with the fixed first action gives a safe state-observing controller whose worst value is the maximum immediate charge plus successor value. Conversely, every controller beginning with that action must tolerate each legal outcome and therefore cannot beat that maximum by the induction hypothesis. Taking the minimum over the finite action set establishes existence, nonexistence, and minimax optimality. The state contains the complete information on which future transitions depend, so additional history cannot improve the value. QED.

The implementation memoizes this recurrence and raises a resource error when its explicit state ceiling is exceeded. Reaching that ceiling is never converted into an infeasibility certificate.

## 2. Strategy certificates

A feasible certificate contains one row for each nonterminal state reachable from the root under the selected strategy. A row names the selected action, its exact worst continuation value, and every reconstructed adversarial outcome with its successor, immediate charge, and continuation value. An infeasible certificate contains no policy rows.

The checker is independently implemented: it does not import the producer. First it recomputes the Bellman optimum from the specification. Then it traverses the serialized selected-strategy closure from the root, reconstructs all outcomes, and requires exact agreement of every successor, charge, continuation, row maximum, and row optimum. Finally it requires the serialized row set to equal the reachable nonterminal closure, rejecting both omissions and extras.

### Theorem 2: bounded certificate soundness and completeness

If the checker accepts a feasible certificate, the exported strategy is safe for every declared outcome, its root value is the exact minimax value, and its rows are exactly the reachable selected-strategy closure. If the checker accepts an infeasible record, no safe strategy exists in the declared finite game. For every valid bounded instance that the producer completes, the producer's canonical certificate is accepted.

**Proof.** The independently recomputed Bellman value equals Theorem 1's `V`. For a feasible record, every transition increases `i`, so the selected closure is acyclic. Backward induction from terminal successors proves that each accepted row's claimed continuation is exact for every outcome and that the selected action's row maximum is exact. Requiring equality to the independently recomputed optimum proves optimality, not merely feasibility. Exact row-set equality proves closure completeness. For an infeasible record, acceptance requires the independent root value to be infinite. For producer completeness, the producer serializes the same validated transition relation and follows every selected-action successor, so its canonical closure satisfies each checker obligation. QED.

Mutation tests provide finite implementation evidence for this theorem; they are not a substitute for the argument.

## 3. Lost-reply ambiguity

Consider one non-idempotent physical action. The sender durably knows only that an attempt is unresolved and receives no reply. Assume the sender cannot atomically observe the receiver's effect, query a stable terminal result, or install a receiver-side cancellation fence. A recovery rule is deterministic with respect to this durable sender observation.

### Proposition 3: a sender-only rule cannot provide both one-effect safety and conditional completion

Under the assumptions above, no deterministic sender-only recovery rule can guarantee both (i) at most one physical effect and (ii) completion after faults cease and the receiver is available.

**Proof.** Construct two executions that have the same durable sender observation after the reply is missing. In world A, the request has not executed. In world B, the receiver executed the action once and the reply was lost. Because the sender's observation is identical, a deterministic rule makes the same recovery choice in both worlds. If it eventually issues an effective request under a fresh identity, world B can contain two effects. If it never issues another effective request, world A remains incomplete. Waiting without acquiring a new stable receiver fact leaves the worlds indistinguishable. Therefore a stable distinguishing mechanism is necessary to satisfy both properties. QED.

The proposition does not require the exact API used below. A cross-system transaction, a durable result table keyed by the original identifier, or another atomic receiver fence could also distinguish the worlds. The artifact's `ambiguity_witness` serializes the two worlds and their shared sender observation.

## 4. Receiver closure

For each attempt identifier, the receiver exposes two linearizable operations:

- `execute_once(id,adapter,target,effect)` installs `DONE` and the effect atomically when the identifier is unseen, or returns the existing compatible direct-execute receipt;
- `close_attempt(id,ticket)` returns an existing `DONE` or atomically installs `CANCELED`, and echoes the supplied recovery ticket in a close-origin receipt before any later execute can take effect.

The local prototype implements both operations with a serialized SQLite transaction over one primary-keyed outcome row and one primary-keyed effect row.

### Lemma 4: stable receiver outcome

For every valid identifier in a receiver history, exactly one stable case holds: `DONE` with one matching effect, or `CANCELED` with no effect and no possibility of a later effect for that identifier.

**Proof.** The first serialized transaction that observes no outcome row installs one terminal state. An execute transaction inserts the adapter effect and `DONE` in the same transaction. A close transaction inserts only `CANCELED`. Primary keys prevent a second outcome or effect. Every later execute or close observes the terminal row. A repeated execute under `DONE` must carry the same adapter, target, cost, and unsafe values; conflicting reuse is rejected. A close receipt binds the identifier, terminal state, adapter, target, origin, and supplied ticket. A repeated execute under `CANCELED` returns cancellation without invoking the adapter. QED.

This lemma assumes the receiver's local transaction semantics. A distributed implementation needs a linearizable substrate that realizes the same interface. A negative status query is not closure because a delayed request may still cross it.

## 5. Persistent controller invariants

The controller durably binds canonical specification and certificate text on first creation. For each logical job it persists the selected action and aggregate recovery count. Each controller has a persistent namespace; a durable sequence produces an attempt identifier unique across controller instances. Every attempt persists that identifier, selected action, immutable adapter and target, upper cost and unsafe envelope, state, per-attempt recovery count, latest recovery ticket, and eventual actual receipt.

At committed controller boundaries, the following invariants hold under serialized controller operations:

1. **Binding.** The stored state can be resumed only with the exact bound specification and certificate.
2. **Single pending attempt.** No new reservation commits while any attempt is pending. The reservation check is repeated while holding the database write lock.
3. **Single logical transition.** Reject and physical settlement re-read the current job/state while holding the write lock, so concurrent callers cannot commit the same logical transition twice.
4. **Prefix physical liability.** Completed actual cost/unsafe effects plus the single pending upper envelope do not exceed the original physical budgets.
5. **Abstract charge.** Each completed physical action subtracts the certified cost charge from the abstract game state, even if the receiver reports a smaller actual cost. Therefore cumulative physical cost is no larger than cumulative abstract charge.
6. **Recovery accounting.** Every restart that finds a pending identifier records one recovery before asking the receiver to close it. Recoveries across canceled attempts of the same logical job accumulate and cannot be reset by changing identifiers.
7. **Identity and terminal agreement.** Every terminal attempt agrees with one stable receiver outcome under the same controller namespace, identifier, adapter, and target; `DONE` agrees with exactly one effect and `CANCELED` with none. After recovery, settlement also requires a close-origin receipt echoing the latest durable ticket.

### Lemma 5: invariant preservation

Assume atomic durable controller transactions, Lemma 4, a checked certificate, valid receipt quantities no larger than the reservation, and serialized database writes. Initialization and every successful select, reserve, recover, cancel-settle, done-settle, or reject transaction preserve Invariants 1--7.

**Proof sketch.** Initialization writes the binding and root state before use. Selection records only the certificate action for the current state. Reservation runs under a write lock, rechecks that no pending row exists and that the envelope fits, then adds exactly one liability. Recovery updates the pending attempt and current job, generates a fresh ticket, and commits both charge and ticket in one transaction before receiver close; it refuses to exceed the remaining fault budget. `CANCELED` settlement removes the pending liability and records zero actual effect without advancing the job. `DONE` settlement first verifies identifier, adapter, target, origin/ticket when recovered, and receiver values against the envelope, computes the accepted-label flip and late bit, conservatively subtracts the certified cost charge, rejects a negative successor, and commits the terminal attempt and successor state atomically. Reject rechecks the current job/state under the same write lock and consumes one bad-outcome token exactly once. Duplicate identical settlement is a no-op; conflicting settlement is rejected. Lemma 4 provides terminal agreement. QED.

Final database rows alone do not prove real-time order between the separate controller and receiver files. The invariant argument uses the program order and local transaction assumptions; the independent protocol model, crash-cut runs, controller race tests, and replay are supporting executable evidence.

## 6. Event-to-game composition

A complete well-formed history satisfies:

- the certificate checker accepts the bound specification/certificate;
- the controller preserves Lemma 5's invariants;
- reserve precedes receiver execute for each globally namespaced identifier and immutably binds adapter and target;
- every restart that finds a pending attempt durably records recovery and a fresh ticket before close;
- every recovered settlement uses a close-origin receipt that echoes the latest ticket;
- the receiver satisfies Lemma 4;
- actual receipt cost and unsafe quantities do not exceed the reservation;
- faults eventually cease and receiver operations eventually return; and
- the exported controller events, final attempts, receiver outcomes, and effects are complete and untampered.

### Theorem 6: event-to-game refinement and budget safety

Every complete well-formed history with no more than the declared recoveries and accepted-label flips projects to exactly one abstract path of the certified finite strategy. Every abstract budget remains nonnegative. Cumulative physical cost is no larger than the certified cost charged by that path; unsafe effects, late outcomes, rejects, drift use, and recovery use equal their abstract charges.

**Proof.** Induct over completed logical jobs. A select event must name the certificate action for the current state. Reject directly realizes its unique successor. For a physical action, single-pending closure partitions the job history into zero or more canceled attempts followed by one `DONE` attempt in a complete history. Lemma 4 gives zero effects for each canceled identifier and one bounded effect for the `DONE` identifier. The durable recovery events sum to `r`, so the abstract crash charge is `r` and the late bit is exactly the predicate using `r*rho`. The actual unsafe bit determines the allowed accept-label flip (or is zero for fallback). The controller subtracts the action's certified cost charge, which is the game's `k_i(a)`, and actual cost is no greater. The resulting persisted state is therefore the corresponding declared abstract successor. The checker-established strategy closure contains that successor and keeps it nonnegative. Induction reaches the terminal state. QED.

### Corollary 6.1: no crash-amplified physical effect

Under the same assumptions, recoveries can increase declared delay and consume fault budget but cannot multiply the physical effect for a logical action. Canceled attempts contribute no effect; the final done attempt contributes one.

### Corollary 6.2: conditional progress after faults cease

If a certified action is physical, faults cease before the remaining crash budget is exhausted, and receiver close/execute operations eventually return, repeated recovery closes every unresolved attempt and a fault-free new attempt eventually reaches `DONE`. Reject completes immediately. Thus each logical job advances. This is not a bound under permanent receiver outage or an unfair scheduler that indefinitely withholds enabled operations.

## 7. Independent finite protocol model

`src/protocol_model.py` imports neither the SQLite runtime nor the replay checker. At the exact comparison point it explores a labelled transition system for one non-idempotent action, at most two crashes, and at most four attempt identifiers; the retained safety/completion sweep repeats the correct model through four crashes. The state records durable reservation, dispatched requests, per-identifier receiver status, controller phase, restart count, and durable recovery charges. Receiver execute and close are abstract atomic operations.

The correct variant requires reserve-before-dispatch, recovery-charge-before-close/settle, and stable receiver closure before replacement. At two crashes it reaches 79 states and 91 edges with no flagged safety violation. The sweep reaches 495 states and 633 edges at four crashes. After all further crash edges are removed, every reached state in each bound has at least one path to a quiescent terminal state, with maximum shortest distance six.

The same explorer localizes four weakened variants. Send-before-reserve reaches an unreserved effect in two steps and a duplicate effect in six. Fresh-identifier retry and negative-query-without-fence each reach a duplicate effect in eight. Settle-before-charge reaches an undercharged terminal state in six. The verifier reruns the model and checks these claim-bearing lengths exactly.

These results are finite model checks, not a general theorem or a proof that the runtime refines the model. Their value is separation: the model cannot pass by importing runtime state transitions, and its delayed-request transitions attack the specific ambiguity hidden by final database snapshots.

## 8. Independent replay obligations

`src/trace_check.py` reconstructs a complete history without importing runtime code. It checks:

- exact event schemas, controller namespaces, and namespaced identifiers;
- immutable adapter/target binding and receipt origin;
- one-time recovery tickets and charge-before-close order;
- certificate-selected actions;
- the single-pending rule and exact reservation envelope;
- prefix actual-plus-liability budgets;
- per-attempt and per-job recovery counts;
- receiver `DONE`/`CANCELED` closure;
- conservative certified cost charge and actual-effect dominance;
- accepted-label flip, late bit, and exact abstract successor;
- exact agreement of final attempt rows, outcomes, and effects;
- rejection of orphan records, duplicate identifiers, pending attempts, incomplete histories, and final-state disagreement.

Acceptance is evidence that the exported witness satisfies these executable obligations. It does not independently establish the storage engine's durability, cross-file real-time order, an adapter's real resource envelope, or the absence of omitted events.

## 9. Scope of the proofs

The results do not cover Byzantine receivers, forged receipts, an adapter that emits unrecorded external effects, unbounded pre-commit work, permanent outages, unbounded horizons, hidden controller state, score-estimation correctness, correlated uncertainty outside the declared finite set, replicated deployment, or queueing/network latency. The independent protocol model covers one action, atomic receiver operations, an exact two-crash comparison point, and a bounded sweep through four crashes; it is not an unbounded proof. The three SQLite adapters exercise append, overwrite, and placement shapes only. General systems claims beyond these assumptions remain unsupported.

## 10. Fixed-sequence and productive-progress arguments

The complete fixed-sequence envelope proof and its exact comparison domain are in `proofs/open-loop.md`. The productive-graph argument checks all maximal paths after removing further-crash edges; idle and unavailable-service stuttering is outside that graph. Runtime input snapshots and strict JSON scalar checks are implementation safeguards tested in `tests/test_comparisons.py`, not a mechanized proof of these general arguments.

## 11. Per-contract retained-identity bound

Assume a completed replay-valid execution of n jobs within F charged recoveries, the one-pending invariant, and no deletion or reuse of receiver terminal facts. Every attempt is either DONE or CANCELED at completion. A DONE settlement advances the current job, so there are at most n DONE attempts/effects. A CANCELED settlement requires at least one charged recovery for that same stable identifier before replacement; different canceled identifiers have different recovery events. Thus the number of CANCELED identifiers is at most F, and total attempts equal DONE plus CANCELED, at most n+F. A rejected job contributes no attempt. This argument bounds terminal record counts per completed contract; it does not bound global lifetime storage, bytes, database index overhead, or a partial crashed prefix.

`retention_checks.py` replays the pilot plus 30 full fault histories and checks 171 already-reconstructed refinement count rows. Three retained histories attain the n+F bound. In three disposable local controls a retained CANCELED row fences a delayed execute across a receiver reopen, while direct deletion of that row permits an effect. The control intentionally violates the retention assumption; deletion is not a supported receiver operation. Safe retirement needs a separate protocol that prevents old messages from acting after their identifying evidence has been discarded.
