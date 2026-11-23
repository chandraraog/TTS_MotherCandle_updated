from contextlib import contextmanager
import json
import sqlite3
from datetime import datetime, timedelta, timezone


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, job TEXT, scope TEXT, stamp TEXT, payload TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS snapshots_lookup ON snapshots(job,scope,id DESC)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, job, scope, payload):
        with self.connect() as db:
            cursor = db.execute("INSERT INTO snapshots(job,scope,stamp,payload) VALUES(?,?,?,?)",
                                (job, scope, payload["collected_at"], json.dumps(payload, allow_nan=False)))
            return cursor.lastrowid

    def latest(self, job, scope=None, snapshot_id=None):
        sql, args = "SELECT id,payload FROM snapshots WHERE job=?", [job]
        if scope is not None:
            sql += " AND scope=?"
            args.append(scope)
        if snapshot_id is not None:
            sql += " AND id=?"
            args.append(snapshot_id)
        with self.connect() as db:
            order = "scope DESC,id DESC" if job == "nse" else "id DESC"
            row = db.execute(sql + f" ORDER BY {order} LIMIT 1", args).fetchone()
        return dict(json.loads(row[1]), id=row[0]) if row else None

    def history(self, job, scope=None):
        sql, args = "SELECT id,stamp,scope FROM snapshots WHERE job=?", [job]
        if scope is not None:
            sql += " AND scope=?"
            args.append(scope)
        with self.connect() as db:
            return [dict(zip(("id", "stamp", "scope"), row)) for row in
                    db.execute(sql + (" ORDER BY scope DESC,id DESC LIMIT 100" if job == "nse" else " ORDER BY id DESC LIMIT 100"), args)]

    def nse_month_history(self, month):
        """Return the newest NSE snapshot for every trade date in YYYY-MM."""
        with self.connect() as db:
            rows = db.execute("SELECT id,stamp,scope FROM snapshots WHERE job='nse' AND scope LIKE ? ORDER BY scope DESC,id DESC", (month + '-%',)).fetchall()
        result, seen = [], set()
        for ident, stamp, scope in rows:
            if scope not in seen:
                seen.add(scope)
                result.append({'id': ident, 'stamp': stamp, 'scope': scope})
        return result

    def prune(self, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            db.execute("DELETE FROM snapshots WHERE stamp < ?", (cutoff,))

    def money_history(self, cfg):
        """Match a selected expiry against the actual saved contract expiry."""
        matches = []
        with self.connect() as db:
            for ident, scope, stamp, raw in db.execute("SELECT id,scope,stamp,payload FROM snapshots WHERE job='money' ORDER BY id DESC"):
                saved, payload = json.loads(scope), json.loads(raw)
                if any(saved.get(k) != cfg.get(k) for k in ('symbol','strikes','oi_multiplier')):
                    continue
                expiry = cfg.get('expiry','')
                if expiry:
                    if saved.get('expiry','') != expiry and str(payload.get('expiry',{}).get('expiry','')) != expiry:
                        continue
                elif saved.get('expiry',''):
                    continue
                matches.append({'id':ident,'scope':scope,'stamp':stamp})
                if len(matches) == 100:
                    break
        return matches
