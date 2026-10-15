# Owned local process comparison

This optional study changes no historical `artifact/results/` data or schema,
dependency format, license or security mechanism. The old exact 62-database
packet checker remains unchanged; additional databases are checked separately
through `derive.py` and the existing per-case read-only replayer. All effects
are benign owned SQL fixtures. The checks provide computational evidence, not
independent human review or machine-checked proof.

## Contract and same-information comparator

Each case forces three fallback jobs: score 0, U=L=D=0, F=2, duration/deadline
1, recovery delay 0 and total charge three times the selected action charge.
The ordinary receiver is separately coded without importing ReceiverStore.
Both receivers use the same six-table schema, terminal audit and
admission/cache/tier effects, WAL, FULL synchronous commits and 5-second busy
timeout. They share ControllerStore, checked policy and raw-output obligations.
Only the receiver implementation changes. This is not all-API equivalence.

RIFL's atomic stable completion record and requestAbort fence (Sections 3--4,
6.5--6.6) and Beldi's logged replay/transactions and retention premises
(Sections 2--6) directly overlap. Neither prior implementation was executed.
Gohil et al.'s MLSys 2026 Sections 4--5 compare estimator/fallback choices.
The contribution under examination is the finite policy budget's connection
to recoverable effects, not a new exactly-once primitive or proof that ordinary
transactions/prior workflow systems cannot provide that connection.

## Reproduction

From `artifact/`, use standard-library Python and fresh output names:

```text
python -B -S owned_process/campaign.py --small --out owned-process-results/NEW-small
python -B -S owned_process/campaign.py --out owned-process-results/NEW-full
python -B -S owned_process/cost_pairs.py --out owned-process-results/NEW-cost
python -B -S owned_process/derive.py --out owned-process-results/NEW-derived
python -B -S -O owned_process/derive.py --out owned-process-results/NEW-derived-O
```

Derivation reads the three fixed `*-20261014-001` acquisition directories,
not arbitrary new paths; a new acquisition needs deliberate input-name/table
binding regeneration. Drivers refuse existing output paths. Raw commands use
`-B -S -W error::ResourceWarning` and `-O` for the optimized half. Exit 73 is an
intentional owned process cut, not power loss. Unexpected exit/stderr aborts
the activity without dropping the case. Live-WAL observations use `mode=ro`
and a fresh read transaction, not `immutable=1`; checkpointed closed files
are read immutably. There is no external service or target.

## Actual retained results

The full acquisition has 168 cases/84 pairs, 696 launches (216 deliberate
cuts/480 normal exits), 504 effects, 648 attempts and 696 returned receipts.
All pairs agree after renaming only fresh UUID ticket strings; raw tickets
remain and replay checks their recovery order. Twenty-four cases attain
n+F=5 attempts. The separate four-case smoke cohort has 14 launches and is
not pooled into the full denominator. The separate alternating-order cost
cohort has 84 cases/42 pairs and 252 launches/effects/returned receipts, also
zero disagreement. It has seven pairs per adapter/mode (four AB and three BA).

Every case retains spec/certificate JSON, two DBs, runtime JSON and one stdout
JSONL plus stderr file per actual launch. Rows, summaries and source hashes
are retained. `owned-process-results/derived-20261014-003/` contains the current
re-derivation and paper table. The deriver checks exact matrix coverage,
source hashes, raw streams, exit/mode records, commit observations, closed
DBs, end-point lengths and pair equality using explicit exceptions under -O.
These checks establish consistency, not signed provenance or cross-database
atomic/real-time order. No current full CI or ten-phase reproduction is claimed.
Selected 52 core plus six frontier-limit tests passed in each normal/-O mode.

## Costs and environment

Observed Windows 11 Pro build 28000; Intel Core i7-12700KF, 12 cores/20 logical
processors; Python 3.12.14; SQLite 3.53.1; NTFS. QPC wall/GetProcessTimes CPU
report 100-ns resolution, not accuracy. Storage medium, virtualization, CPU
affinity, allocation limits and outside host load are unknown.

Parent time includes process startup/output/exit. Successful-worker CPU excludes
killed-worker CPU and final JSON encoding. Case process time excludes input
synthesis and final verification, the latter separately logged. Explicit
TRUNCATE checkpoint time is already included in worker/parent time. Modes run
in sequential cohorts; their difference is not an optimization effect. Six
seven-pair median parent-time ratios range 0.992--1.029; no established speed
advantage follows.

All full/cost cases retain 90,112 DB bytes after close for both receivers. Full
cohort endpoints retain 26,083,864 logical bytes in 2,235 files; cost endpoints
11,757,568 bytes in 927 files, including raw input/output and driver records.
DB/WAL/SHM samples and checkpoint receipts do not measure continuous peaks,
physical allocation or device write traffic. Synthetic charge 2 or 2^63-1 is
not CPU time, money or bytes. No reclamation, finite physical-byte budget,
production/network/replication or unbounded progress claim follows. September
timings and all retained negative results remain separate and unchanged.

Sources: [RIFL](https://web.stanford.edu/~ouster/cgi-bin/papers/rifl.pdf),
[Beldi](https://www.usenix.org/system/files/osdi20-zhang_haoran.pdf),
[fallback study](https://proceedings.mlsys.org/paper_files/paper/2026/file/96aca14d6c4dcd3adf54bc2c5ad7f138-Paper-Conference.pdf),
[SQLite INTEGER](https://www.sqlite.org/datatype3.html),
[SQLite WAL](https://www.sqlite.org/wal.html),
[CPython 3.12.14](https://raw.githubusercontent.com/python/cpython/v3.12.14/Modules/_sqlite/util.c).
