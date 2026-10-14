# Typed realization and canonical cancellation

This is a hand argument about the present Python/SQLite implementation, not a
proof-assistant derivation. AI assistance was used in the added argument,
regressions, comparison implementation and exposition. Executed checks are
computational evidence, not independent human review.

## Domains

The finite game uses unbounded Python/JSON integers. Let B = 2^63 - 1. The
unchanged local schema binds effect charges, reservation charges, and attempt
counters to SQLite INTEGER via CPython's sqlite3 adapter. These fields need
signed 64-bit representability. Aggregate cost, remaining budgets, job recovery
metadata and identifiers in JSON/TEXT do not acquire an artificial B cap.

For a checked reachable strategy, local admission additionally requires every
**selected physical** action charge at most B and, at every physical policy
row, remaining recovery budget f < B. The latter leaves room for at most f
canceled attempts followed by the completing attempt, so next_attempt is at
most f+1 <= B for a job first selected there. This check is over the chosen
strategy, not all available actions: reject and unselected oversized actions
do not cause refusal. RuntimeDomainError means representation refusal, never
model infeasibility or a successful controller creation. Checker state limits
remain separate; no enormous-f certificate was actually synthesized in tests.

### Conditional range lemma

Assume (i) a completed feasible certificate check, (ii) local admission above,
(iii) valid nonnegative integer receiver costs no larger than the selected
reservation and no larger than B, and (iv) the existing per-job recovery and
single-pending invariants. Every integer charge and per-attempt counter bound
by the admitted execution fits the stated SQLite INTEGER fields.

Proof. Each selected charge is checked before opening the controller path.
Actual cost is separately checked before receiver BEGIN and before settlement.
Per-attempt recoveries are at most the remaining declared recovery budget.
Each canceled attempt needs at least one charged recovery; job attempt-counter
increments are consequently at most f+1. Both quantities fit B. Aggregate
subtraction is exact Python integer arithmetic serialized as JSON/TEXT. The
guard does not reject merely because the sum of several valid charges exceeds
B. This establishes the stated range property, not immunity to all database
errors. QED.

The adapter audit AUTOINCREMENT lifetime, total row counts, allocation failures,
disk full, locks/timeouts and underlying I/O errors are **not** bounded by this
lemma. Concrete progress requires storage and computation resources adequate
for the finite accepted execution, returning receiver operations and enabled
step progress, in addition to the abstract fault bound. A successfully
admitted strategy is not a capacity reservation or a total-completion promise.

## Canonical cancellation and idempotent settlement

A canonical canceled receipt has (cost, unsafe, adapter, job) =
(0, 0, None, None), a valid origin and the required latest recovery ticket.
Validation occurs before either new or duplicate settlement. Previously the
duplicate branch compared terminal numeric values but could accept a canceled
receipt carrying a non-null adapter/target. The repaired branch first enforces
the same canonical shape; an identical valid duplicate is a no-op, while a
malformed duplicate is rejected without changing state/events. This repair
does not imply Byzantine receipt authentication or malicious-tamper resistance.

## Receipt visibility and the ordinary comparator

After the first **committed** receiver execute or close, the primary-keyed
terminal row is absorbing: DONE has one atomic bound effect, CANCELED has none
and fences future execution of that identifier. Before that first commit, the
identifier may be absent; an interrupted uncommitted transaction is not DONE.
Returning a receipt after COMMIT allows a second connection to observe its
outcome and effect in a fresh read transaction. This relies on SQLite's local
transaction guarantee; a read transaction started before commit need not see
it. There is no atomic snapshot or real-time attestation across the separate
controller/receiver files.

An ordinary result-table/fence transaction can satisfy exactly this bounded
interface with the same information and retained tables. The independently
coded ordinary receiver in owned_process/ordinary.py uses BEGIN IMMEDIATE,
atomic effect/outcome insertion and return-after-COMMIT. The 84 correctness
pairs and 42 alternating cost pairs agree after renaming only fresh ticket
strings. This is bounded executable agreement, not all-API equivalence or a
new exactly-once primitive. RIFL already co-locates completion records with
effects and fences delayed prepare via requestAbort under the original ID;
Beldi supplies logged replay/transactions and explicit retention premises.
No native execution of either prior system is reported here.

## Count and byte boundary

For one completed n-job contract and F charged recoveries, the existing n+F
attempt bound follows from one final DONE per physical job and at least one
recovery per preceding canceled attempt. It is not a universal compressed
representation lower bound, nor a DB/WAL byte budget or a reclamation policy.
The owned study retains all history, checkpoints each file and records sampled
logical DB/WAL/SHM lengths, end-of-case input/output lengths and checkpoint
time. Continuous disk peaks, device write traffic, physical allocation,
power-loss durability and multi-contract storage remain unmeasured.
