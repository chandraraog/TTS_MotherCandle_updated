"""Regular NSE equity sessions only. Annual holiday files must be verified before use."""
from datetime import date, datetime, time, timedelta
from pathlib import Path
import calendar, json
ROOT=Path(__file__).parent
SOURCE='https://www.nseindia.com/resources/exchange-communication-holidays'
POSITIONAL={'Massive Moves','Combustion','Retrograde'}
def holidays(year):
    path=ROOT/'market_calendars'/f'{year}.json'
    if not path.exists(): return None
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('year')!=year or not data.get('source'): raise ValueError('Invalid market calendar')
    return {date.fromisoformat(d) for d in data['holidays']}
def sessions(year,month):
    closed=holidays(year)
    if closed is None: return []
    return [date(year,month,d) for d in range(1,calendar.monthrange(year,month)[1]+1) if date(year,month,d).weekday()<5 and date(year,month,d) not in closed]
def overlaps(row,day):
    start=row['start_ist']; end=row['end_ist']; a=datetime.fromisoformat(start)
    op=datetime.combine(day,time(9,15)); cl=datetime.combine(day,time(15,30))
    if end:
        b=datetime.fromisoformat(end)
        if len(end)==10: b+=timedelta(days=1)
        return a<cl and b>op
    if len(start)==10: return a.date()==day # date-only observation, not exact time
    return op<=a<cl

def market_rows(rows,year,month):
    days=sessions(year,month); result=[]
    for r in rows:
        hits=[d for d in days if overlaps(r,d)]
        if not hits: continue
        item=dict(r)
        # Keep actual astronomical interval intact; expose observation dates separately in notes.
        dates=', '.join(d.isoformat() for d in hits)
        item['notes']=(item['notes']+' | ' if item['notes'] else '')+'NSE observation: '+dates
        result.append(item)
    return result
