"""SQLite prototype for receipt-closed selective fallback.

The controller and receiver intentionally use separate databases.  The receiver
provides two linearizable operations per attempt id:

* execute_once(id, ...): atomically performs one local effect and stores DONE;
* close(id): returns DONE or atomically stores CANCELED before any later execute.

The controller never releases a pending reservation without one of those stable
terminal records.  This is a local prototype, not a distributed consensus or
cryptographic receipt service.
"""
from __future__ import annotations

import copy
import json
from types import MappingProxyType
from collections.abc import Mapping
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from checker import check as check_certificate
from game import encode, nominal_unsafe


def _freeze(value):
    """Detach verified JSON inputs and make their nested containers read-only."""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        # sqlite3's connect timeout does not reliably cover a simultaneous
        # journal-mode transition on a freshly created database.  Install the
        # busy handler first and retry only the bounded, known lock case.
        db.execute("PRAGMA busy_timeout=5000")
        for attempt in range(100):
            try:
                db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 99:
                    raise
                time.sleep(0.01)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        return db
    except BaseException:
        db.close()
        raise


def _nat(x: object) -> bool:
    return type(x) is int and x >= 0


def _valid_controller_id(value: object) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 48
            and all(ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                    for ch in value))


class ReceiverStore:
    def __init__(self, path: Path):
        self.path = path
        self.db = _connect(path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS outcomes(
              id TEXT PRIMARY KEY,
              state TEXT NOT NULL CHECK(state IN ('done','canceled')),
              adapter TEXT CHECK(adapter IS NULL OR adapter IN ('admission','cache','tier')),
              job TEXT,
              cost INTEGER NOT NULL CHECK(cost >= 0),
              unsafe INTEGER NOT NULL CHECK(unsafe IN (0,1)),
              CHECK(
                (state='done' AND adapter IS NOT NULL AND job IS NOT NULL) OR
                (state='canceled' AND adapter IS NULL AND job IS NULL AND cost=0 AND unsafe=0)
              )
            );
            CREATE TABLE IF NOT EXISTS effects(
              id TEXT PRIMARY KEY,
              adapter TEXT NOT NULL,
              job TEXT NOT NULL,
              cost INTEGER NOT NULL CHECK(cost >= 0),
              unsafe INTEGER NOT NULL CHECK(unsafe IN (0,1))
            );
            CREATE TABLE IF NOT EXISTS queue_effects(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              id TEXT UNIQUE NOT NULL,
              job TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cache_state(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tier_state(
              key TEXT PRIMARY KEY,
              location TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              event TEXT NOT NULL
            );
            """
        )

    def _event(self, payload: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO audit(event) VALUES(?)", (json.dumps(payload, sort_keys=True),))

    @staticmethod
    def _valid_id(key: object) -> bool:
        return isinstance(key, str) and 1 <= len(key) <= 120

    @staticmethod
    def _valid_ticket(value: object) -> bool:
        return (isinstance(value, str) and len(value) == 32
                and all(ch in "0123456789abcdef" for ch in value))

    def execute_once(self, key: str, adapter: str, job: str, cost: int, unsafe: int) -> dict[str, Any]:
        if not self._valid_id(key) or not self._valid_id(job):
            raise ValueError("invalid identifier")
        if adapter not in {"admission", "cache", "tier"}:
            raise ValueError("invalid adapter")
        if not _nat(cost) or not _nat(unsafe) or unsafe > 1:
            raise ValueError("invalid effect quantities")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT state,adapter,job,cost,unsafe FROM outcomes WHERE id=?", (key,)
            ).fetchone()
            if row is not None:
                state, old_adapter, old_job, old_cost, old_unsafe = row
                if state == "canceled":
                    self.db.execute("COMMIT")
                    return {"id": key, "status": "canceled", "adapter": None, "job": None,
                            "cost": 0, "unsafe": 0, "via": "execute", "ticket": None}
                if (old_adapter, old_job, old_cost, old_unsafe) != (adapter, job, cost, unsafe):
                    raise ValueError("attempt id reused with conflicting effect")
                self.db.execute("COMMIT")
                return {"id": key, "status": "done", "adapter": adapter, "job": job,
                        "cost": cost, "unsafe": unsafe, "via": "execute", "ticket": None}

            if adapter == "admission":
                self.db.execute("INSERT INTO queue_effects(id,job) VALUES(?,?)", (key, job))
            elif adapter == "cache":
                self.db.execute(
                    "INSERT INTO cache_state(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (job, "computed"),
                )
            else:
                self.db.execute(
                    "INSERT INTO tier_state(key,location) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET location=excluded.location",
                    (job, "fallback"),
                )
            self.db.execute("INSERT INTO effects VALUES(?,?,?,?,?)", (key, adapter, job, cost, unsafe))
            self.db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)", (key, "done", adapter, job, cost, unsafe))
            self._event({"kind": "done", "id": key, "adapter": adapter, "job": job,
                         "cost": cost, "unsafe": unsafe})
            self.db.execute("COMMIT")
            return {"id": key, "status": "done", "adapter": adapter, "job": job,
                    "cost": cost, "unsafe": unsafe, "via": "execute", "ticket": None}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close_attempt(self, key: str, ticket: str) -> dict[str, Any]:
        """Return a stable terminal outcome using a charged recovery capability.

        The receiver treats ``ticket`` as an opaque capability.  The controller
        generates and persists it while charging recovery, then verifies that
        the same value is echoed in the receipt.  This makes the supported
        recovery path charge-before-query rather than a caller convention.
        """
        if not self._valid_id(key) or not self._valid_ticket(ticket):
            raise ValueError("invalid identifier or recovery ticket")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT state,adapter,job,cost,unsafe FROM outcomes WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                self.db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)", (key, "canceled", None, None, 0, 0))
                self._event({"kind": "canceled", "id": key})
                result = {"id": key, "status": "canceled", "adapter": None, "job": None,
                          "cost": 0, "unsafe": 0, "via": "close", "ticket": ticket}
            elif row[0] == "done":
                result = {"id": key, "status": "done", "adapter": row[1], "job": row[2],
                          "cost": row[3], "unsafe": row[4], "via": "close", "ticket": ticket}
            else:
                result = {"id": key, "status": "canceled", "adapter": None, "job": None,
                          "cost": 0, "unsafe": 0, "via": "close", "ticket": ticket}
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def outcomes(self) -> dict[str, dict[str, Any]]:
        rows = self.db.execute("SELECT id,state,adapter,job,cost,unsafe FROM outcomes ORDER BY id")
        return {r[0]: {"status": r[1], "adapter": r[2], "job": r[3], "cost": r[4], "unsafe": r[5]}
                for r in rows}

    def effects(self) -> dict[str, tuple[int, int]]:
        return {r[0]: (r[1], r[2]) for r in self.db.execute("SELECT id,cost,unsafe FROM effects ORDER BY id")}

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT event FROM audit ORDER BY seq")]

    def close(self) -> None:
        self.db.close()


class ControllerStore:
    @staticmethod
    def _valid_ticket(value: object) -> bool:
        return (isinstance(value, str) and len(value) == 32
                and all(ch in "0123456789abcdef" for ch in value))

    def __init__(self, path: Path, spec: dict[str, Any], cert: dict[str, Any],
                 controller_id: str | None = None, adapter: str = "admission"):
        # The runtime is a consumer of the certificate, not a second unchecked
        # strategy interpreter.  Validate before creating or reopening durable
        # state so a malformed or incomplete witness cannot become executable.
        if not isinstance(spec, dict) or not isinstance(cert, dict):
            raise ValueError("runtime requires specification and certificate objects")
        spec, cert = copy.deepcopy(spec), copy.deepcopy(cert)
        if not cert.get("feasible") or not check_certificate(spec, cert):
            raise ValueError("runtime requires a valid feasible certificate")
        if controller_id is not None and not _valid_controller_id(controller_id):
            raise ValueError("invalid controller identifier")
        if adapter not in {"admission", "cache", "tier"}:
            raise ValueError("invalid adapter")

        self.path = path
        self._spec = _freeze(spec)
        self._cert = _freeze(cert)
        self.db = _connect(path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
              job_index INTEGER PRIMARY KEY,
              action TEXT NOT NULL CHECK(action IN ('accept','fallback','reject')),
              state TEXT NOT NULL CHECK(state IN ('selected','done')),
              crashes INTEGER NOT NULL CHECK(crashes >= 0),
              next_attempt INTEGER NOT NULL CHECK(next_attempt >= 0)
            );
            CREATE TABLE IF NOT EXISTS attempts(
              id TEXT PRIMARY KEY,
              job_index INTEGER NOT NULL,
              action TEXT NOT NULL CHECK(action IN ('accept','fallback')),
              adapter TEXT NOT NULL CHECK(adapter IN ('admission','cache','tier')),
              target TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('pending','done','canceled')),
              upper_cost INTEGER NOT NULL CHECK(upper_cost >= 0),
              upper_unsafe INTEGER NOT NULL CHECK(upper_unsafe IN (0,1)),
              recoveries INTEGER NOT NULL CHECK(recoveries >= 0),
              recovery_ticket TEXT,
              actual_cost INTEGER CHECK(actual_cost IS NULL OR actual_cost >= 0),
              actual_unsafe INTEGER CHECK(actual_unsafe IS NULL OR actual_unsafe IN (0,1)),
              FOREIGN KEY(job_index) REFERENCES jobs(job_index)
            );
            CREATE TABLE IF NOT EXISTS audit(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              event TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending_attempt
              ON attempts((1)) WHERE state='pending';
            """
        )
        spec_text = json.dumps(spec, sort_keys=True, separators=(",", ":"))
        cert_text = json.dumps(cert, sort_keys=True, separators=(",", ":"))
        requested_id = controller_id
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute("SELECT value FROM meta WHERE key='state'").fetchone()
            if existing is None:
                root = (0, spec["drift"], spec["crashes"], spec["unsafe"], spec["late"], spec["cost"])
                controller_id = requested_id or uuid.uuid4().hex
                self.db.execute("INSERT INTO meta VALUES('state',?)", (json.dumps(root),))
                self.db.execute("INSERT INTO meta VALUES('initial_budget',?)",
                                (json.dumps([spec["cost"], spec["unsafe"]]),))
                self.db.execute("INSERT INTO meta VALUES('bound_spec',?)", (spec_text,))
                self.db.execute("INSERT INTO meta VALUES('bound_certificate',?)", (cert_text,))
                self.db.execute("INSERT INTO meta VALUES('controller_id',?)", (controller_id,))
                self.db.execute("INSERT INTO meta VALUES('adapter',?)", (adapter,))
            else:
                state_value = json.loads(existing[0])
                if (not isinstance(state_value, list) or len(state_value) != 6
                        or any(type(value) is not int for value in state_value)):
                    raise ValueError("malformed persisted controller state")
                bound_spec = self.db.execute("SELECT value FROM meta WHERE key='bound_spec'").fetchone()
                bound_cert = self.db.execute("SELECT value FROM meta WHERE key='bound_certificate'").fetchone()
                bound_id = self.db.execute("SELECT value FROM meta WHERE key='controller_id'").fetchone()
                bound_adapter = self.db.execute("SELECT value FROM meta WHERE key='adapter'").fetchone()
                if bound_spec is None or bound_cert is None or bound_id is None or bound_adapter is None:
                    raise ValueError("controller database predates the binding contract")
                if bound_spec[0] != spec_text or bound_cert[0] != cert_text:
                    raise ValueError("persisted controller is bound to a different specification or certificate")
                controller_id = bound_id[0]
                if requested_id is not None and requested_id != controller_id:
                    raise ValueError("persisted controller is bound to a different controller identifier")
                if bound_adapter[0] != adapter:
                    raise ValueError("persisted controller is bound to a different adapter")
            if not _valid_controller_id(controller_id):
                raise ValueError("malformed persisted controller identifier")
            self.controller_id = controller_id
            self.adapter = adapter
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            finally:
                self.db.close()
            raise

    @property
    def spec(self):
        return self._spec

    @property
    def cert(self):
        return self._cert

    def _event(self, payload: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO audit(event) VALUES(?)", (json.dumps(payload, sort_keys=True),))

    def state(self) -> tuple[int, int, int, int, int, int]:
        row = self.db.execute("SELECT value FROM meta WHERE key='state'").fetchone()
        if row is None:
            raise ValueError("missing state")
        values = tuple(json.loads(row[0]))
        if len(values) != 6 or any(type(x) is not int or x < 0 for x in values):
            raise ValueError("invalid state")
        return values  # type: ignore[return-value]

    def done(self) -> bool:
        return self.state()[0] == len(self.spec["jobs"])

    def ensure_selected(self) -> str:
        """Atomically bind the current logical state to its certified action."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            i = state[0]
            if i >= len(self.spec["jobs"]):
                self.db.execute("COMMIT")
                return "done"
            row = self.db.execute("SELECT action,state FROM jobs WHERE job_index=?", (i,)).fetchone()
            if row is not None:
                if row[1] != "selected":
                    raise ValueError("current job is not in selected state")
                self.db.execute("COMMIT")
                return row[0]
            policy_row = self.cert.get("policy", {}).get(encode(state))
            if not isinstance(policy_row, Mapping) or policy_row.get("action") not in {"accept", "fallback", "reject"}:
                raise ValueError("certificate has no action for persisted state")
            action = policy_row["action"]
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (i, action, "selected", 0, 0))
            self._event({"kind": "select", "job": i, "action": action, "state": list(state)})
            self.db.execute("COMMIT")
            return action
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def apply_reject(self) -> None:
        # Selection can race safely, but the current state, selected action, and
        # one-time state advance must be re-read under the same write lock.
        # Otherwise two local controller processes could both use a stale state
        # and append duplicate reject events.
        self.ensure_selected()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            i, d, f, u, t, c = state
            if i >= len(self.spec["jobs"]):
                raise ValueError("controller is already done")
            row = self.db.execute(
                "SELECT action,state FROM jobs WHERE job_index=?", (i,)
            ).fetchone()
            if row is None or row[0] != "reject":
                raise ValueError("selected action is not reject")
            if row[1] != "selected":
                raise ValueError("rejection was already applied")
            nxt = (i + 1, d, f, u, t - 1, c)
            if min(nxt[1:]) < 0:
                raise ValueError("certificate selected a losing rejection")
            updated = self.db.execute(
                "UPDATE jobs SET state='done' WHERE job_index=? AND state='selected'", (i,)
            ).rowcount
            if updated != 1:
                raise ValueError("rejection was already applied")
            self.db.execute("UPDATE meta SET value=? WHERE key='state'", (json.dumps(nxt),))
            self._event({"kind": "reject", "job": i, "next": list(nxt)})
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def pending(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT id FROM attempts WHERE state='pending' ORDER BY id")]

    def reserve(self) -> tuple[str, str, int, int]:
        # Selection and reservation are separate durable events, but all checks
        # that enforce the single-pending invariant are repeated under the
        # reservation write lock.  This prevents two local controller processes
        # from both observing an empty pending set.
        self.ensure_selected()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            i = state[0]
            if i >= len(self.spec["jobs"]):
                raise ValueError("controller is already done")
            row = self.db.execute(
                "SELECT action,state,crashes,next_attempt FROM jobs WHERE job_index=?", (i,)
            ).fetchone()
            if row is None:
                raise ValueError("current job has no selected action")
            action, job_state, consumed_crashes, seq = row
            if job_state != "selected":
                raise ValueError("current job is not in selected state")
            if action == "reject":
                raise ValueError("reject has no physical attempt")
            if self.db.execute("SELECT 1 FROM attempts WHERE state='pending' LIMIT 1").fetchone():
                raise ValueError("pending attempts must be reconciled before a new reservation")
            if consumed_crashes > state[2]:
                raise ValueError("declared crash budget exhausted")
            job = self.spec["jobs"][i]
            upper_cost = job[action + "_cost"]
            if action == "accept":
                nominal = nominal_unsafe(job["score"])
                upper_unsafe = max(nominal, nominal ^ 1) if state[1] > 0 else nominal
            else:
                upper_unsafe = 0
            if upper_cost > state[5] or upper_unsafe > state[3]:
                raise ValueError("reservation exceeds remaining contract budget")
            key = f"{self.controller_id}-j{i}-a{seq}"
            target = f"job-{i}"
            self.db.execute("UPDATE jobs SET next_attempt=? WHERE job_index=?", (seq + 1, i))
            self.db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,0,NULL,NULL,NULL)",
                            (key, i, action, self.adapter, target, "pending",
                             upper_cost, upper_unsafe))
            self._event({"kind": "reserve", "id": key, "job": i, "action": action,
                         "adapter": self.adapter, "target": target,
                         "upper": [upper_cost, upper_unsafe]})
            self.db.execute("COMMIT")
            return key, action, upper_cost, upper_unsafe
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def record_recovery(self, key: str) -> str:
        """Durably count one restart that found ``key`` pending.

        A caller invokes this exactly once at the beginning of each recovery
        process.  Repeated recovery processes are intentionally counted again;
        this is how a crash after receiver closure still consumes fault budget.
        """
        if not isinstance(key, str):
            raise ValueError("invalid recovery identifier")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT job_index,state,recoveries FROM attempts WHERE id=?", (key,)
            ).fetchone()
            if row is None or row[1] != "pending":
                raise ValueError("recovery requires a pending attempt")
            job_index, _, recoveries = row
            state = self.state()
            if job_index != state[0]:
                raise ValueError("recovery does not match current logical job")
            job_row = self.db.execute(
                "SELECT state,crashes FROM jobs WHERE job_index=?", (job_index,)
            ).fetchone()
            if job_row is None or job_row[0] != "selected":
                raise ValueError("recovery requires a selected logical job")
            consumed = job_row[1]
            if consumed >= state[2]:
                raise ValueError("declared crash budget exhausted")
            recoveries += 1
            ticket = uuid.uuid4().hex
            self.db.execute(
                "UPDATE attempts SET recoveries=?,recovery_ticket=? WHERE id=?",
                (recoveries, ticket, key),
            )
            self.db.execute("UPDATE jobs SET crashes=crashes+1 WHERE job_index=?", (job_index,))
            self._event({"kind": "recover", "id": key, "job": job_index,
                         "recoveries": recoveries, "ticket": ticket})
            self.db.execute("COMMIT")
            return ticket
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def settle(self, receipt: dict[str, Any]) -> None:
        required = {"id", "status", "adapter", "job", "cost", "unsafe", "via", "ticket"}
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise ValueError("malformed receipt")
        key = receipt["id"]
        if (not isinstance(key, str) or receipt["status"] not in {"done", "canceled"}
                or receipt["via"] not in {"execute", "close"}):
            raise ValueError("malformed receipt")
        if receipt["via"] == "execute" and receipt["ticket"] is not None:
            raise ValueError("direct execution receipt carries a recovery ticket")
        if receipt["via"] == "close" and not self._valid_ticket(receipt["ticket"]):
            raise ValueError("close receipt lacks a valid recovery ticket")
        if not _nat(receipt["cost"]) or not _nat(receipt["unsafe"]) or receipt["unsafe"] > 1:
            raise ValueError("malformed quantities")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT job_index,action,adapter,target,state,upper_cost,upper_unsafe,recoveries,"
                "recovery_ticket,actual_cost,actual_unsafe FROM attempts WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown reservation")
            (i, action, adapter, target, old_state, upper_cost, upper_unsafe, recoveries,
             recovery_ticket, actual_cost, actual_unsafe) = row
            if receipt["via"] == "close":
                if recoveries == 0 or receipt["ticket"] != recovery_ticket:
                    raise ValueError("close receipt does not match the latest charged recovery ticket")
            elif recoveries != 0:
                raise ValueError("recovered attempts must be settled from receiver close")
            if old_state != "pending":
                expected_status = old_state
                if (expected_status != receipt["status"]
                        or (actual_cost or 0, actual_unsafe or 0) != (receipt["cost"], receipt["unsafe"])):
                    raise ValueError("conflicting duplicate receipt")
                if old_state == "done" and (receipt["adapter"], receipt["job"]) != (adapter, target):
                    raise ValueError("conflicting duplicate receipt target")
                self.db.execute("COMMIT")
                return
            if receipt["status"] == "canceled":
                if (receipt["cost"], receipt["unsafe"], receipt["adapter"], receipt["job"]) != (0, 0, None, None):
                    raise ValueError("canceled receipt carries an effect")
                if receipt["via"] != "close":
                    raise ValueError("canceled settlement requires a charged recovery close")
                self.db.execute(
                    "UPDATE attempts SET state='canceled',actual_cost=0,actual_unsafe=0 WHERE id=?", (key,)
                )
                self._event({"kind": "settle", "id": key, "status": "canceled",
                             "via": "close", "ticket": receipt["ticket"],
                             "adapter": adapter, "target": target, "actual": [0, 0]})
                self.db.execute("COMMIT")
                return
            if (receipt["adapter"], receipt["job"]) != (adapter, target):
                raise ValueError("receiver effect does not match reserved adapter and target")
            if receipt["cost"] > upper_cost or receipt["unsafe"] > upper_unsafe:
                raise ValueError("receiver effect exceeds reserved envelope")
            state = self.state()
            if state[0] != i:
                raise ValueError("receipt does not match current logical job")
            crashes = self.db.execute("SELECT crashes FROM jobs WHERE job_index=?", (i,)).fetchone()[0]
            job = self.spec["jobs"][i]
            flip = (nominal_unsafe(job["score"]) ^ receipt["unsafe"]) if action == "accept" else 0
            if action == "fallback" and receipt["unsafe"] != 0:
                raise ValueError("fallback receipt is unsafe")
            late = int(job[action + "_time"] + crashes * self.spec["recovery_time"] > self.spec["deadline"])
            # The finite game charges the declared action envelope.  The receiver
            # may report a smaller physical cost, which is retained for audit,
            # but conservatively charging ``upper_cost`` keeps the next state in
            # the certified strategy and implies actual physical cost safety.
            nxt = (i + 1, state[1] - flip, state[2] - crashes, state[3] - receipt["unsafe"],
                   state[4] - late, state[5] - upper_cost)
            if min(nxt[1:]) < 0:
                raise ValueError("physical outcome violates certified remaining budget")
            self.db.execute(
                "UPDATE attempts SET state='done',actual_cost=?,actual_unsafe=? WHERE id=?",
                (receipt["cost"], receipt["unsafe"], key),
            )
            updated = self.db.execute(
                "UPDATE jobs SET state='done' WHERE job_index=? AND state='selected'", (i,)
            ).rowcount
            if updated != 1:
                raise ValueError("logical job was not in selected state")
            self.db.execute("UPDATE meta SET value=? WHERE key='state'", (json.dumps(nxt),))
            self._event({"kind": "settle", "id": key, "status": "done",
                         "via": receipt["via"], "ticket": receipt["ticket"],
                         "adapter": adapter, "target": target,
                         "actual": [receipt["cost"], receipt["unsafe"]],
                         "charge": upper_cost, "crashes": crashes, "late": late,
                         "next": list(nxt)})
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT event FROM audit ORDER BY seq")]

    def attempts(self) -> dict[str, dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id,job_index,action,adapter,target,state,upper_cost,upper_unsafe,recoveries,"
            "recovery_ticket,actual_cost,actual_unsafe FROM attempts ORDER BY id"
        )
        return {r[0]: {"job": r[1], "action": r[2], "adapter": r[3], "target": r[4],
                       "status": r[5], "upper": [r[6], r[7]], "recoveries": r[8],
                       "ticket": r[9],
                       "actual": [r[10], r[11]] if r[10] is not None else None} for r in rows}

    def attempt_descriptor(self, key: str) -> tuple[str, str]:
        row = self.db.execute(
            "SELECT adapter,target FROM attempts WHERE id=?", (key,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown reservation")
        return row[0], row[1]

    def snapshot(self) -> dict[str, Any]:
        return {"controller_id": self.controller_id, "adapter": self.adapter,
                "state": list(self.state()), "done": self.done(),
                "attempts": self.attempts(), "events": self.events()}

    def close(self) -> None:
        self.db.close()


def recover_pending(controller: ControllerStore, receiver: ReceiverStore) -> list[dict[str, Any]]:
    receipts = []
    for key in controller.pending():
        ticket = controller.record_recovery(key)
        receipt = receiver.close_attempt(key, ticket)
        controller.settle(receipt)
        receipts.append(receipt)
    return receipts
