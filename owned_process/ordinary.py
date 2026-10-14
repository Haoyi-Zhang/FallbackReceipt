"""Ordinary transactional result/fence receiver for a common-interface control.

No reference receiver code is imported. Same SQLite mode, six tables, fields,
effect shapes and one audit entry per terminal installation are retained to
avoid comparing a stronger obligation against a cheaper receipt-free store.
This is not RIFL or Beldi software, a new primitive, or an independent team.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

MAXIMUM = (1 << 63) - 1
KINDS = {'admission', 'cache', 'tier'}


class OrdinaryReceiver:
    def __init__(self, path: Path, before_commit=None):
        self.path, self.before_commit = path, before_commit
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS outcomes(
              id TEXT PRIMARY KEY,
              state TEXT NOT NULL CHECK(state IN ('done','canceled')),
              adapter TEXT CHECK(adapter IS NULL OR adapter IN ('admission','cache','tier')),
              job TEXT,
              cost INTEGER NOT NULL CHECK(cost >= 0),
              unsafe INTEGER NOT NULL CHECK(unsafe IN (0,1)),
              CHECK((state='done' AND adapter IS NOT NULL AND job IS NOT NULL) OR
                    (state='canceled' AND adapter IS NULL AND job IS NULL AND cost=0 AND unsafe=0)));
            CREATE TABLE IF NOT EXISTS effects(id TEXT PRIMARY KEY, adapter TEXT NOT NULL,
              job TEXT NOT NULL, cost INTEGER NOT NULL CHECK(cost>=0),
              unsafe INTEGER NOT NULL CHECK(unsafe IN (0,1)));
            CREATE TABLE IF NOT EXISTS queue_effects(seq INTEGER PRIMARY KEY AUTOINCREMENT,
              id TEXT UNIQUE NOT NULL, job TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cache_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tier_state(key TEXT PRIMARY KEY,location TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL);
        ''')

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def row(self, key):
        return self.db.execute('SELECT state,adapter,job,cost,unsafe FROM outcomes WHERE id=?',
                               (key,)).fetchone()

    def append_audit(self, event):
        self.db.execute('INSERT INTO audit(event) VALUES(?)',(json.dumps(event,sort_keys=True),))
        if self.before_commit is not None:
            self.before_commit()

    @staticmethod
    def identifier(value):
        return type(value) is str and 1 <= len(value) <= 120

    @staticmethod
    def receipt(key, row, via, ticket):
        status, adapter, job, cost, unsafe = row
        return dict(id=key,status=status,adapter=adapter,job=job,cost=cost,unsafe=unsafe,
                    via=via,ticket=ticket)

    def execute_once(self, key, adapter, job, cost, unsafe):
        if (not self.identifier(key) or not self.identifier(job) or adapter not in KINDS
                or type(cost) is not int or not 0 <= cost <= MAXIMUM
                or type(unsafe) is not int or unsafe not in (0,1)):
            raise ValueError('unsupported ordinary receiver request')
        with self.transaction():
            previous = self.row(key)
            if previous is None:
                if adapter == 'admission':
                    self.db.execute('INSERT INTO queue_effects(id,job) VALUES(?,?)',(key,job))
                elif adapter == 'cache':
                    self.db.execute('INSERT INTO cache_state VALUES(?,?) ON CONFLICT(key) '
                                    'DO UPDATE SET value=excluded.value',(job,'computed'))
                else:
                    self.db.execute('INSERT INTO tier_state VALUES(?,?) ON CONFLICT(key) '
                                    'DO UPDATE SET location=excluded.location',(job,'fallback'))
                self.db.execute('INSERT INTO effects VALUES(?,?,?,?,?)',(key,adapter,job,cost,unsafe))
                previous = ('done',adapter,job,cost,unsafe)
                self.db.execute('INSERT INTO outcomes VALUES(?,?,?,?,?,?)',(key,*previous))
                self.append_audit(dict(kind='done',id=key,adapter=adapter,job=job,cost=cost,unsafe=unsafe))
            elif previous[0] == 'done' and previous[1:] != (adapter,job,cost,unsafe):
                raise ValueError('conflicting ordinary receiver identity')
        # Return is after the transaction context completed its COMMIT.
        return self.receipt(key,previous,'execute',None)

    def close_attempt(self, key, ticket):
        if (not self.identifier(key) or type(ticket) is not str or len(ticket)!=32
                or any(c not in '0123456789abcdef' for c in ticket)):
            raise ValueError('unsupported ordinary close request')
        with self.transaction():
            previous = self.row(key)
            if previous is None:
                previous = ('canceled',None,None,0,0)
                self.db.execute('INSERT INTO outcomes VALUES(?,?,?,?,?,?)',(key,*previous))
                self.append_audit(dict(kind='canceled',id=key))
        return self.receipt(key,previous,'close',ticket)

    def outcomes(self):
        return {r[0]:dict(status=r[1],adapter=r[2],job=r[3],cost=r[4],unsafe=r[5])
                for r in self.db.execute('SELECT id,state,adapter,job,cost,unsafe FROM outcomes ORDER BY id')}

    def effects(self):
        return {r[0]:(r[1],r[2]) for r in self.db.execute('SELECT id,cost,unsafe FROM effects ORDER BY id')}

    def events(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT event FROM audit ORDER BY seq')]

    def close(self):
        self.db.close()
