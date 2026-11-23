"""Persistent hourly schedule and once-per-IST-day MACD transition alerts."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
import requests
from dotenv import dotenv_values
from engine import now_ist, in_session

class MacdAutomation:
    def __init__(self, job):
        self.job = job
        self.path = job.engine.root / 'data' / 'macd_automation.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.settings = {'hourly': True, 'alerts': False}
        self.error = None
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS momentum_events (day TEXT, symbol TEXT, side TEXT, timeframe TEXT, state TEXT, PRIMARY KEY(day,symbol,side,timeframe))')
            db.execute('CREATE TABLE IF NOT EXISTS observations (symbol TEXT PRIMARY KEY, states TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS deliveries (day TEXT, symbol TEXT, state TEXT, PRIMARY KEY(day,symbol))')
            for key,value in db.execute('SELECT key,value FROM settings'):
                if key in self.settings:
                    self.settings[key] = json.loads(value)
        env = {**dotenv_values(job.engine.root.parent / '.env'), **dotenv_values(job.engine.root / '.env'), **os.environ}
        self.token = env.get('TELEGRAM_BOT_TOKEN')
        self.chat = env.get('TELEGRAM_CHAT_ID')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def status(self):
        with self.lock:
            return {**self.settings, 'telegram_ready': bool(self.token and self.chat), 'error': self.error}

    def configure(self, values):
        if set(values) != {'hourly','alerts'} or any(type(v) is not bool for v in values.values()):
            raise ValueError('Hourly and alerts must be boolean settings.')
        if values['alerts'] and not (self.token and self.chat):
            raise ValueError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env, then restart.')
        with self.lock, self.db() as db:
            for key,value in values.items():
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))
            self.settings.update(values)
        return self.status()

    @staticmethod
    def aligned_chain(states, side):
        chain = []
        for tf in ('M','W','D','240','60','15','5'):
            value = states.get(tf)
            monthly_weak = tf == 'M' and value == ('Recovering' if side == 'Strong Bull' else 'Weakening')
            if value != side and not monthly_weak:
                break
            chain.append(tf)
        return chain

    @classmethod
    def transition(cls, old, new):
        for side in ('Strong Bull','Strong Bear'):
            before, after = cls.aligned_chain(old, side), cls.aligned_chain(new, side)
            if len(before) >= 2 and len(after) >= 2 and any(value == side and old.get(tf) != side for tf, value in new.items() if tf != 'M'):
                return side
        return None

    def observe(self, symbol, states, price):
        order = ('M','W','D','240','60','15','5')
        if any(states.get(tf) is None for tf in order):
            return  # Missing candles cannot establish a transition.
        day = now_ist().date().isoformat()
        with self.lock, self.db() as db:
            row = db.execute('SELECT states FROM observations WHERE symbol=?',(symbol,)).fetchone()
            old = json.loads(row[0]) if row else {}
            # Older four-timeframe observations are not a comparable baseline.
            side = self.transition(old, states) if all(tf in old for tf in order) else None
            db.execute('INSERT OR REPLACE INTO observations VALUES (?,?)',(symbol,json.dumps(states)))
            if not side or not self.settings['alerts'] or self.job.engine.demo:
                return
            before, after = self.aligned_chain(old,side), self.aligned_chain(states,side)
            joined = []
            for tf in order[1:]:
                if states[tf] != side or old.get(tf) == side:
                    continue
                if db.execute('INSERT OR IGNORE INTO momentum_events VALUES (?,?,?,?,?)',(day,symbol,side,tf,'reserved')).rowcount:
                    joined.append(tf)
            db.commit()  # Reserve before sending, including uncertain network failures.
            if not joined:
                return
            labels = {'M':'Monthly','W':'Weekly','D':'Daily','240':'4H','60':'1H','15':'15m','5':'5m'}
            weak = states['M'] in ('Recovering','Weakening')
            message = ('Chandra Trend Engine · '+('Weak' if weak else 'Strong')+' momentum\n'
                       +symbol+' · '+('Bullish' if side == 'Strong Bull' else 'Bearish')+' · Price '+str(price)+'\n'
                       +', '.join(labels[tf] for tf in joined)+' joined · Chain '+str(len(before))+' → '+str(len(after))+'\n'
                       +' → '.join(labels[tf] for tf in after)+'\n'+now_ist().strftime('%d %b %Y %H:%M IST'))
            try:
                response = requests.post('https://api.telegram.org/bot'+self.token+'/sendMessage',json={'chat_id':self.chat,'text':message},timeout=15)
                if not response.ok or not response.json().get('ok'):
                    raise ValueError('Telegram rejected delivery')
                status = 'sent'
                self.error = None
            except (requests.RequestException,ValueError):
                status = 'failed_or_uncertain'
                self.error = 'Telegram delivery failed or is uncertain; this transition will not be retried today.'
            for tf in joined:
                db.execute('UPDATE momentum_events SET state=? WHERE day=? AND symbol=? AND side=? AND timeframe=?',(status,day,symbol,side,tf))

    def tick(self, now=None):
        now = now or now_ist()
        if self.job.engine.demo or not self.settings['hourly'] or not in_session(now,self.job.engine.get_config()):
            return False
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='last_watchlist'").fetchone()
        if row and now.timestamp()-float(json.loads(row[0])) < 3600:
            return False
        return self.job.start('watchlist')

    def started(self, source):
        if source == 'watchlist':
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('last_watchlist',json.dumps(now_ist().timestamp())))

    def start(self):
        def loop():
            while not self.job.engine.stop.wait(15):
                try:
                    self.tick()
                except Exception:
                    self.error = 'Hourly scan could not start; will retry.'
        threading.Thread(target=loop,name='macd-hourly',daemon=True).start()
