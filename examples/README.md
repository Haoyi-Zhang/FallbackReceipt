# Local reply-loss example

Run `python -S examples/run_contract.py` from the artifact root. It uses a temporary directory and removes only its own files. To retain the two SQLite stores and exported history, use `python -S examples/run_contract.py --output EMPTY_DIRECTORY`.

The three tasks are exactly the paper's running example. The program synthesizes and checks the strategy, executes job 0, deliberately suppresses delivery of its reply, closes and reopens the sender store, charges recovery, and closes the original identifier. It then completes the remaining jobs and checks the exported history. Expected output: `valid: true`, `incomplete_rejected: true`, `actual_effects: 3`, `actual_cost: 9`, and `certified_worst_cost: 10`.

The experiment intentionally does not claim an OS process crash: it exercises the receipt-loss API path. Owned process-exit fault experiments are separately reproduced by `reproduce.py --phase faults`. Real applications must justify that the receiver transaction covers all effects and that their measured or proven envelopes dominate the actual operation.
