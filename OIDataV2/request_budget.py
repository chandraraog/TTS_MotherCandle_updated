"""Persistent request budgeting shared by the V2 collectors; counts attempted calls."""
import sqlite3
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


class RequestBudget:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS calls (stamp REAL NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS calls_stamp ON calls(stamp)')
                db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value REAL)')
        finally:
            db.close()

    def reserve(self, per_minute, per_day):
        """Return a wait duration, or reserve one request atomically. Caller serializes."""
        now = time.time()
        today = datetime.fromtimestamp(now, IST).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                cooldown = db.execute("SELECT value FROM settings WHERE name='cooldown'").fetchone()
                if cooldown and cooldown[0] > now:
                    raise RuntimeError('FYERS rate limit cooldown active. Wait five minutes before retrying.')
                db.execute('DELETE FROM calls WHERE stamp < ?', (today - 86400,))
                count = db.execute('SELECT COUNT(*) FROM calls WHERE stamp >= ?', (today,)).fetchone()[0]
                if count >= per_day:
                    raise RuntimeError('FYERS daily request budget reached. Collection resumes next IST day; increase intervals or reduce scope.')
                recent = db.execute('SELECT stamp FROM calls WHERE stamp > ? ORDER BY stamp', (now - 60,)).fetchall()
                if len(recent) >= per_minute:
                    return max(0.05, 60.1 - (now - recent[0][0]))
                db.execute('INSERT INTO calls(stamp) VALUES(?)', (now,))
                return 0
        finally:
            db.close()

    def cooldown(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                db.execute("INSERT OR REPLACE INTO settings(name,value) VALUES('cooldown',?)", (time.time() + 300,))
        finally:
            db.close()

    def usage(self):
        start = datetime.now(IST).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        db = sqlite3.connect(self.path, timeout=15)
        try:
            return db.execute('SELECT COUNT(*) FROM calls WHERE stamp>=?', (start,)).fetchone()[0]
        finally:
            db.close()
