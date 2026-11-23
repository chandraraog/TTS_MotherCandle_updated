"""Money alerts with durable, independent CE/PE reference values."""
import json
import hashlib
from html import escape
import math
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import requests
from dotenv import dotenv_values
from money_image import table_image

IST = timezone(timedelta(hours=5, minutes=30))

class MoneyAlerts:
    def __init__(self, root, demo=False):
        self.root, self.demo = root, demo
        self.path = root / 'data' / ('money_alerts.demo.sqlite' if demo else 'money_alerts.sqlite')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.settings = {'enabled': False, 'threshold': 20.0, 'near': 3, 'step': 100,
                         'movement_mode': 'top', 'movement_count': 4}
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS anchors (scope TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY, stamp TEXT, scope TEXT, state TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS manual_reports (key TEXT PRIMARY KEY, stamp TEXT, state TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS movement_events (
                id INTEGER PRIMARY KEY, stamp TEXT NOT NULL, symbol TEXT NOT NULL, expiry TEXT NOT NULL,
                strike REAL NOT NULL, side TEXT NOT NULL, direction TEXT NOT NULL, change_pct REAL NOT NULL,
                before_money REAL NOT NULL, after_money REAL NOT NULL, threshold REAL NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS movement_lookup ON movement_events(symbol,expiry,id DESC)')
            row = db.execute('SELECT payload FROM settings WHERE id=1').fetchone()
            if row: self.settings.update(json.loads(row[0]))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        try:
            with conn: yield conn
        finally: conn.close()

    def set_movement_tracking(self, values):
        if not isinstance(values, dict) or set(values) != {'mode', 'count'}:
            raise ValueError('Supply movement tracking mode and count.')
        mode, count = values['mode'], values['count']
        if mode not in ('top', 'all', 'atm'):
            raise ValueError('Select Top strikes, All strikes, or ATM range.')
        minimum = 0 if mode == 'top' else 1
        if type(count) is not int or not minimum <= count <= 100:
            raise ValueError('Strike count must be a whole number from 1 to 100 (Top strikes may be 0 for All).')
        with self.lock, self.db() as db:
            self.settings.update({'movement_mode': mode, 'movement_count': count})
            db.execute('INSERT OR REPLACE INTO settings VALUES (1,?)', (json.dumps(self.settings),))
        return {'mode': mode, 'count': count}

    @staticmethod
    def _top_strikes(payload, side, count):
        if not count:
            return {r.get('strike') for r in payload.get('rows', []) if r.get('type') == side and isinstance(r.get('money'), (int, float)) and not isinstance(r.get('money'), bool) and math.isfinite(r.get('money'))}
        rows = [r for r in payload.get('rows', []) if r.get('type') == side and isinstance(r.get('money'), (int, float)) and not isinstance(r.get('money'), bool) and math.isfinite(r.get('money'))]
        return {r.get('strike') for r in sorted(rows, key=lambda r: r['money'], reverse=True)[:count]}

    def track_movements(self, previous, payload, threshold):
        # Persist CE/PE money moves versus the preceding collection.
        if not previous or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold <= 0:
            return 0
        try:
            if datetime.fromisoformat(previous['collected_at']).astimezone(IST).date() != datetime.fromisoformat(payload['collected_at']).astimezone(IST).date():
                return 0
        except (KeyError, TypeError, ValueError):
            return 0
        mode = self.settings.get('movement_mode', 'top')
        count = int(self.settings.get('movement_count', 4))
        old = {(row.get('strike'), row.get('type')): row for row in previous.get('rows', [])}
        if mode == 'top':
            tracked = {side: self._top_strikes(previous, side, count) | self._top_strikes(payload, side, count) for side in ('CE', 'PE')}
        elif mode == 'atm':
            step = float(self.settings.get('step', 100))
            spot = payload.get('spot')
            if not isinstance(spot, (int, float)) or isinstance(spot, bool) or not math.isfinite(spot) or step <= 0:
                return 0
            center = math.floor(spot / step + 0.5) * step
            tracked_strikes = {row.get('strike') for row in payload.get('rows', []) if isinstance(row.get('strike'), (int, float)) and abs(row['strike'] - center) <= count * step + 1e-8}
            tracked = {'CE': tracked_strikes, 'PE': tracked_strikes}
        else:
            tracked = {side: {row.get('strike') for row in payload.get('rows', []) if row.get('type') == side} for side in ('CE', 'PE')}
        expiry = payload.get('expiry') or {}
        expiry = str(expiry.get('expiry', expiry.get('date', '')) if isinstance(expiry, dict) else expiry)
        stamp = payload.get('collected_at')
        symbol = str(payload.get('symbol') or '')
        if not stamp or not symbol:
            return 0
        events = []
        for row in payload.get('rows', []):
            side = row.get('type')
            if side not in ('CE', 'PE') or row.get('error') or row.get('strike') not in tracked[side]:
                continue
            prior = old.get((row.get('strike'), side))
            before = prior.get('money') if prior else None
            after = row.get('money')
            if not isinstance(before, (int, float)) or isinstance(before, bool) or not math.isfinite(before) or before == 0:
                continue
            if not isinstance(after, (int, float)) or isinstance(after, bool) or not math.isfinite(after):
                continue
            change_pct = (after - before) / abs(before) * 100
            if abs(change_pct) < threshold:
                continue
            events.append((stamp, symbol, expiry, float(row['strike']), side,
                           'rose' if change_pct > 0 else 'fell', change_pct,
                           float(before), float(after), float(threshold)))
        if not events:
            return 0
        with self.db() as db:
            db.executemany('''INSERT INTO movement_events
                (stamp,symbol,expiry,strike,side,direction,change_pct,before_money,after_money,threshold)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', events)
            return len(events)

    def clear_movement_history(self):
        with self.db() as db:
            deleted = db.execute('DELETE FROM movement_events').rowcount
        return max(0, deleted)

    def movement_history(self, symbol, expiry='', limit=100):
        with self.db() as db:
            rows = db.execute('''SELECT stamp,symbol,expiry,strike,side,direction,change_pct,
                before_money,after_money,threshold FROM movement_events
                WHERE symbol=? AND expiry=? ORDER BY id DESC LIMIT ?''',
                (symbol, expiry, max(1, min(int(limit), 500)))).fetchall()
        keys = ('stamp','symbol','expiry','strike','side','direction','change_pct',
                'before','after','threshold')
        return [dict(zip(keys, row)) for row in rows]

    def credentials(self):
        env = {**dotenv_values(self.root.parent / '.env'), **dotenv_values(self.root / '.env'), **os.environ}
        return env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')

    def status(self):
        token, chat = self.credentials()
        with self.lock, self.db() as db:
            row = db.execute('SELECT stamp,state FROM deliveries ORDER BY id DESC LIMIT 1').fetchone()
            return {**self.settings, 'telegram_ready': bool(token and chat),
                    'last_delivery': {'stamp':row[0],'state':row[1]} if row else None}

    def configure(self, values):
        self.validate_settings(values)
        if values['enabled'] and not all(self.credentials()):
            raise ValueError('Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.')
        with self.lock, self.db() as db:
            self.settings.update(dict(values))
            db.execute('INSERT OR REPLACE INTO settings VALUES (1,?)',(json.dumps(self.settings),))
        return self.status()

    @staticmethod
    def validate_settings(values):
        if set(values) != {'enabled','threshold','near','step'} or type(values['enabled']) is not bool:
            raise ValueError('Supply enabled, threshold and ATM window for Money alerts.')
        threshold = values['threshold']
        if isinstance(threshold,bool) or not isinstance(threshold,(float,int)) or not math.isfinite(threshold) or not 0 < threshold <= 1000:
            raise ValueError('Alert threshold must be greater than 0 and at most 1000%.')
        if type(values['near']) is not int or values['near'] not in (2,3):
            raise ValueError('Select ATM plus/minus 2 or 3 strikes.')
        if isinstance(values['step'],bool) or not isinstance(values['step'],(int,float)) or not math.isfinite(values['step']) or not 0 < values['step'] <= 10000:
            raise ValueError('Strike step must be greater than 0 and at most 10000.')

    def send_now(self, payload, options):
        if self.demo:
            raise ValueError('Telegram messages are disabled in demo mode.')
        self.validate_settings({'enabled':False, **{k:options.get(k) for k in ('threshold','near','step')}})
        top=options.get('top')
        if type(top) is not int or not 0 <= top <= 100:
            raise ValueError('Top strikes must be a whole number between 0 and 100.')
        token,chat=self.credentials()
        if not token or not chat:
            raise ValueError('Configure Telegram credentials first.')
        spot=payload.get('spot')
        if not isinstance(spot,(int,float)) or not math.isfinite(spot):
            raise ValueError('No spot price in this snapshot. Collect Money data first.')
        step,near,threshold=options['step'],options['near'],options['threshold']
        atm=round(math.floor(spot/step+0.5)*step,8)
        pairs=[]
        missing=[]
        for offset in range(-near,near+1):
            strike=round(atm+offset*step,8)
            pair={'strike':strike}
            for side in ('CE','PE'):
                matches=[r for r in payload.get('rows',[]) if r['strike']==strike and r['type']==side]
                if len(matches)!=1 or not isinstance(matches[0].get('money'),(int,float)) or not math.isfinite(matches[0]['money']):
                    missing.append(f'{strike:g} {side}')
                    continue
                pair[side]=matches[0]['money']
                atp=matches[0].get('atp')
                pair[side+'_BEP']=strike+(atp if side=='CE' else -atp) if isinstance(atp,(int,float)) and math.isfinite(atp) else None
            if 'CE' in pair and 'PE' in pair:
                pairs.append(pair)
        if not pairs:
            raise ValueError('No complete CE/PE strike pairs are available in this window. Collect Money data before sending.')
        available_count=len(pairs)
        atm_pair=next((r for r in pairs if r['strike']==atm),None)
        top_ce={r['strike'] for r in sorted(pairs,key=lambda r:r['CE'],reverse=True)[:top]} if top else set()
        top_pe={r['strike'] for r in sorted(pairs,key=lambda r:r['PE'],reverse=True)[:top]} if top else set()
        if top:
            selected=top_ce|top_pe
            pairs=[r for r in pairs if r['strike'] in selected]
        pairs.sort(key=lambda r:max(r['CE'],r['PE']),reverse=True)
        ce=sum(r['CE'] for r in pairs);pe=sum(r['PE'] for r in pairs)
        collected=datetime.fromisoformat(payload['collected_at']).astimezone(IST).strftime('%d %b %Y %H:%M IST')
        names={'NSE:NIFTY50-INDEX':'NIFTY 50','NSE:NIFTYBANK-INDEX':'BANK NIFTY','BSE:SENSEX-INDEX':'SENSEX','MCX:NATURALGAS':'NATURAL GAS'}
        name=names.get(payload['symbol'],payload['symbol'].split(':')[-1].removesuffix('-INDEX').removesuffix('-EQ'))
        expiry=payload.get('expiry',{}).get('date','—')
        try: expiry=datetime.strptime(expiry,'%d-%m-%Y').strftime('%d %b')
        except (ValueError,TypeError): pass
        data_time=datetime.fromisoformat(payload['collected_at']).astimezone(IST).strftime('%d %b, %H:%M IST')
        if atm_pair:
            atm_ce,atm_pe=atm_pair['CE'],atm_pair['PE']
            pct=abs(atm_ce-atm_pe)/max(atm_ce,atm_pe)*100 if min(atm_ce,atm_pe)>=0 and max(atm_ce,atm_pe)>0 else 0 if atm_ce==atm_pe==0 else None
            view=self.leader(atm_ce,atm_pe,threshold).split(' (')[0]
            indicator='🟢' if view=='Bullish' else '🔴' if view=='Bearish' else '🟡'
            money_line=f'CE ₹{atm_ce:,.0f}L | PE ₹{atm_pe:,.0f}L'
            view_line=f'ATM view: {view}'+(f' · Gap {pct:.0f}%' if pct is not None else '')
        else:
            indicator='🟡';money_line='Unavailable';view_line='ATM view: unavailable'
        message=(f"📊 <b>{escape(name)} · Expiry {escape(str(expiry))}</b>\n"
                 f"💰 <b>ATM {atm:,.0f}:</b> {money_line}\n"
                 f"{indicator} <b>{escape(view_line)}</b>\n"
                 f"🕒 Data: {data_time}")
        photo=table_image(payload,pairs,atm,threshold,top_ce,top_pe,collected,missing)
        key=hashlib.sha256(json.dumps(['table-image-v1',payload.get('id'),payload['symbol'],payload['collected_at'],near,step,top,threshold,pairs],sort_keys=True).encode()).hexdigest()
        with self.lock,self.db() as db:
            previous=db.execute('SELECT state FROM manual_reports WHERE key=?',(key,)).fetchone()
            if previous:
                if previous[0]=='sent':
                    return {'sent':False,'message':'This snapshot and strike selection was already sent. No duplicate message sent.'}
                raise ValueError('A send was already attempted for this snapshot; delivery is uncertain. Check Telegram before collecting a new snapshot.')
            db.execute('INSERT INTO manual_reports VALUES (?,?,?)',(key,datetime.now(IST).isoformat(),'reserved'))
            db.commit()
            try:
                response=requests.post('https://api.telegram.org/bot'+token+'/sendPhoto',data={'chat_id':chat,'caption':message,'show_caption_above_media':'false','parse_mode':'HTML'},files={'photo':('money-table.png',photo,'image/png')},timeout=25)
                if not response.ok or not response.json().get('ok'): raise ValueError('Rejected')
            except (requests.RequestException,ValueError):
                db.execute('UPDATE manual_reports SET state=? WHERE key=?',('failed_or_uncertain',key))
                db.commit()
                raise ValueError('Telegram send failed or delivery is uncertain; no automatic retry. Check Telegram before sending a new snapshot.') from None
            db.execute('UPDATE manual_reports SET state=? WHERE key=?',('sent',key))
        return {'sent':True,'message':f'Sent {len(pairs)} high-money strikes as a table image to Telegram.'+(' Partial window — missing data is listed in the message.' if missing else '')}

    @staticmethod
    def leader(ce, pe, threshold):
        if ce < 0 or pe < 0: return 'Unwinding / mixed'
        base = max(ce, pe)
        if ce == pe or not base or abs(ce-pe)*100/base < threshold: return 'Neutral'
        return 'Bullish (PE leads)' if pe > ce else 'Bearish (CE leads)'

    @staticmethod
    def changes(old, current, threshold):
        if old is None:
            return ['Initial Money reference'], dict(current)
        updated = dict(old)
        reasons = []
        for side in ('CE','PE'):
            before, after = old[side], current[side]
            crossed = (before < 0) != (after < 0)
            moved = after != before and (before == 0 or abs(after-before)*100 >= abs(before)*threshold-1e-9)
            if crossed or moved:
                reason = 'turned negative' if after < 0 <= before else 'returned non-negative' if before < 0 <= after else 'increased' if after > before else 'decreased'
                reasons.append(f'{side} {reason}: {before:,.2f} → {after:,.2f} lakh')
                updated[side] = after
        view = MoneyAlerts.leader(current['CE'],current['PE'],threshold)
        if view in ('Bullish (PE leads)','Bearish (CE leads)') and view != old.get('view'):
            reasons.append('View changed: '+str(old.get('view','Neutral'))+' → '+view)
            updated['view'] = view
        elif view == 'Unwinding / mixed' and reasons:
            updated['view'] = view
        return reasons, updated

    def observe(self, payload, cfg):
        if self.demo or payload.get('errors'): return
        with self.lock:
            if not self.settings['enabled']: return
            threshold, near, step = self.settings['threshold'], self.settings['near'], self.settings['step']
            rows = payload.get('rows',[])
            spot = payload.get('spot')
            if not isinstance(spot,(float,int)) or not math.isfinite(spot): return
            atm = round(math.floor(spot/step+0.5)*step,8)
            selected = {round(atm+offset*step,8) for offset in range(-near,near+1)}
            current = {'CE':0.0,'PE':0.0}
            for strike in selected:
                for side in ('CE','PE'):
                    matching = [r for r in rows if r['strike']==strike and r['type']==side]
                    if len(matching)!=1: return
                    value = matching[0].get('money')
                    if not isinstance(value,(int,float)) or not math.isfinite(value): return
                    current[side] += value
            scope = json.dumps([payload['symbol'],payload.get('expiry',{}).get('expiry'),cfg.get('oi_multiplier',1),near,step])
            current['view'] = self.leader(current['CE'],current['PE'],threshold)
            label = f'ATM {atm:g} ±{near} strikes · step {step:g} ({min(selected):g}–{max(selected):g})'
            self.deliver(scope,label,current,payload,threshold)

    def deliver(self, scope, label, current, payload, threshold):
        with self.db() as db:
            row = db.execute('SELECT payload FROM anchors WHERE scope=?',(scope,)).fetchone()
            reasons, anchor = self.changes(json.loads(row[0]) if row else None,current,threshold)
            if not reasons: return
            stamp = datetime.now(IST).isoformat()
            db.execute('INSERT OR REPLACE INTO anchors VALUES (?,?)',(scope,json.dumps(anchor)))
            ident = db.execute('INSERT INTO deliveries(stamp,scope,state) VALUES (?,?,?)',(stamp,scope,'reserved')).lastrowid
            db.commit()  # reserve before I/O to suppress duplicate attempts after timeouts/restarts
            message = f"Chandra Market Desk · Money\n{payload['symbol']} · {payload.get('expiry',{}).get('date','')}\n{label}\n"+'\n'.join(reasons)
            message += f"\nCE ₹{current['CE']:,.2f} lakh | PE ₹{current['PE']:,.2f} lakh\n{current['view']} · Threshold {threshold:g}%\nCollected {payload['collected_at']}"
            token, chat = self.credentials()
            try:
                response = requests.post('https://api.telegram.org/bot'+str(token)+'/sendMessage',json={'chat_id':chat,'text':message},timeout=15)
                if not response.ok or not response.json().get('ok'): raise ValueError('Rejected')
                state = 'sent'
            except (requests.RequestException,ValueError):
                state = 'failed_or_uncertain — reference reserved; no automatic resend'
            db.execute('UPDATE deliveries SET state=? WHERE id=?',(state,ident))
