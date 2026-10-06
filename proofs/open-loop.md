# Exact fixed-sequence comparator

The input fragment is the same finite, fully observed contract used by the adaptive game. All task score classes and envelopes are known when choosing a fixed sequence. Recovery and label-flip budgets are independent upper bounds; neither must be exhausted. Reject contributes one bad outcome, zero cost, and no physical effect. These assumptions are essential.

## Envelope proposition

For a fixed sequence a[0..n-1] define K as the sum of physical-action certified costs. Let B be the number of accepted tasks with nominal unsafe bit one, and G the accepted tasks with nominal unsafe bit zero. Let L0 count all rejects and all physical actions whose base duration exceeds deadline H. For each remaining physical action and positive recovery duration rho, define q_i = floor((H-h_i)/rho)+1. Sort these positive thresholds increasingly. Let r be the largest number of cheapest thresholds whose sum is at most the crash budget F; when rho=0 set r=0.

The sequence is feasible exactly when K<=C, B+min(D,G)<=U, and L0+r<=L. Among feasible sequences the minimum K is its robust open-loop optimum.

Proof. Canceled attempts have no effect; the eventual completed action is charged its declared cost envelope once. Thus the certified charge K is constant across outcomes. Actual physical cost is at most K but need not attain K or be constant across executions. The adversary maximizes unsafe effects by leaving the B nominally unsafe accepts unchanged and flipping at most D of the G other accepts. It need never spend a flip that improves a label. The maximum is attained and is B+min(D,G). An otherwise timely physical action is late exactly when it receives at least q_i recoveries. Any set of r late actions therefore costs at least the sum of the r cheapest q values; assigning precisely those recoveries attains this lower bound. Rejects and already-late actions contribute L0 without consuming crashes. These expressions are exact maxima for each abstract budget coordinate. A universal conjunction is satisfied iff each coordinate maximum fits: they do not need to be attained on the same adversarial path. This proves necessity and sufficiency. Exhausting the finite sequence set and choosing the least feasible K proves certified-charge optimality. QED.

`tests/test_cost_semantics.py` checks two complete executions of the same fixed fallback action with actual costs 1 and 4 (one with a lost reply and recovery). Both charge the same certified envelope 5 and pass replay with one effect each. This illustrates the distinction without claiming a causal cost effect of recovery.

## Implementation and differential check

`src/open_loop.py` enumerates all 3^n sequences up to an explicit catalogue-size ceiling of 100,000. It imports neither `game.py` nor its transition functions. A cached catalogue is bound to the exact tasks, deadline, and recovery charge; changing those inputs cannot reuse it. A resource-limit exception means no decision, never infeasibility.

`comparisons.baseline_crosscheck` is a separate Cartesian enumerator: it explicitly lists each legal label-flip vector and recovery allocation. With seed 20260923 it checks 240 generated specifications, 80 at each horizon 1--3, and 3,120 fixed sequences. Budgets include 0--3 drift/crashes, recovery duration 0--3, and deadlines 1--6. Every coordinate maximum agrees. This finite comparison supports the code, not a general machine proof.

## Interpretation of the negative result

All 576 eight-task trace-derived contracts receive an exact comparison over 6,561 sequences each. Adaptive and optimal open-loop both admit 345, and minimum worst costs agree in every jointly feasible case. The 22 contracts missed by the union of four restricted threshold/constant policies are also found by optimal open-loop. This grid therefore provides no evidence that outcome feedback improves feasibility or resource cost. No strict adaptive-separation witness is claimed.

## Productive-path termination check

The added `progress` traversal reuses the independently specified protocol transition system, not runtime or replay code. It first builds the complete reached graph with crash bound b, for each b=0..4. It then removes all transitions that increase the crash count. For every reached state it checks that (i) a nonterminal state has a remaining successor and (ii) the remaining productive graph is acyclic. A longest-path dynamic program bounds all such paths by six transitions (four when b=0). In a finite directed graph, absence of cycles implies every path is finite, and absence of nonterminal dead ends makes each maximal path terminate in a terminal state. This proves the stated finite graph property.

Idle/stuttering and indefinitely unavailable-service transitions are not present. Eventual execution of enabled operations remains an environment assumption. This is not a fairness proof, wall-clock guarantee, TLA+/TLC run, or implementation refinement proof.
