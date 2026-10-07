# Dominance-pruned fixed-sequence compilation

The compiler solves the fixed-sequence contract in the manuscript's exact
open-loop proposition. It does not replace the adaptive Bellman solver or its
reachable-strategy certificate.

A prefix label contains its deterministic certified charge K, a vector U[d]
of maximum unsafe count under at most d flips, a vector L[f] of maximum late
or rejected count under at most f recoveries, and its action witness. Empty
prefixes have zero charge and zero vectors.

For accept with nominal unsafe bit b, the unsafe extension is
U'[d]=max(U[d]+b, U[d-1]+1-b), omitting the second term when d=0.
This partitions legal executions into no flip on the new job and one flip
on it. A harmful flip is not forced: budgets are upper bounds. Fallback and
reject add no unsafe outcome. Reject adds one to every lateness coordinate;
a physical action of duration h gives
L'[f]=max_{0<=r<=f}(L[f-r]+1[h+r*rho>H]).
This partitions every legal recovery allocation by the count assigned to the
new job. The two vectors maximize different coordinates, which is legitimate
for a universal conjunction and does not assert that their maxima co-occur.
Induction therefore gives the exact coordinate envelopes of every label.

If a label has no larger charge or vector coordinate than another, every
common suffix preserves that ordering: each extension is an addition or a
maximum of ordered expressions. It can replace the dominated prefix without
losing any feasible completion or increasing its charge. Sorting by charge
and then by the vectors guarantees a dominator precedes its dominated label;
the retained witnesses make tie handling deterministic. A prefix whose charge,
U[D], or L[F] exceeds a hard final budget can also be removed: all future
increments are nonnegative. Induction over prefixes shows that the surviving
frontier includes a representative of every potentially optimal fixed
sequence. The least-charge surviving full label is consequently the exact
open-loop optimum; an empty frontier is infeasible only after all such labels
have been processed. Exceeding an explicit resource bound is no decision.

Dominance may leave exponentially many incomparable labels. With frontier
width W, an extension costs O(D+F^2), and the implemented pairwise pruning
costs O(W^2(D+F)) per layer plus sorting and action-copy costs. There is no
polynomial-time claim for unrestricted contracts. This is an application of
compositional envelopes and Pareto pruning, not a new general DP principle.

The implementation imports neither the Cartesian comparator nor the adaptive
game. The tests compare 3,024 small exhaustive contracts; the campaign compares
256 deterministic random contracts and all 576 existing eight-job contracts.
Feasible returned actions are also evaluated with the separate fixed-sequence
envelope. Four preselected windows retain seven alternating timing pairs,
including full setup in both branches. Twenty-four additional 16/32-job
contracts use explicitly scaled lateness budgets and retain their full inputs.
Their completion establishes scale on those contracts, not the same budget
experiment as the original eight-job grid or an advantage of feedback.
