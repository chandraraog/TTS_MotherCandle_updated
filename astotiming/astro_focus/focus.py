import argparse, calendar, csv, json
from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from timing_integration import read_timings, line, FIELDS
from astro_calendar_v3 import generate, load, save, HEAD, PLANETS
from nse_calendar import market_rows, sessions, holidays, overlaps, POSITIONAL
ROOT=Path(__file__).parent
DATA=ROOT/'data'; OUT=ROOT/'output'
CATS=['Must Watch','Gandamool','Rashi Transit','Nakshatra Transit','Combustion','Retrograde','Eclipse','Conjunction','Yoga','Massive Moves','D9','Sector Watch','Timing Candles']
def next_month(y,m): return (y+1,1) if m==12 else (y,m+1)
def build(y,m, include_calculated=False):
    stem=f'{y}-{m:02}'; path=DATA/stem; path.mkdir(parents=True,exist_ok=True); OUT.mkdir(exist_ok=True)
    # Read all archives so active intervals carry over; deduplicate exact records.
    events=[]; positions=[]; seen=set()
    for f in sorted(DATA.glob('*/events.csv')):
        for r in load(f):
            key=tuple(r[k] for k in HEAD)
            if key not in seen: events.append(r); seen.add(key)
    for f in sorted(DATA.glob('*/positions.csv')): positions.extend(load(f))
    from monthly_catalog import daily_astro_events
    calculated_daily=daily_astro_events(y,m,events)
    rows,daily=generate(y,m,events,positions)
    rows.extend(calculated_daily)
    moon_market_views={
        'Moon in Moola':('Bearish · Super bearish day','User market hypothesis: super bearish day; occurs about once per month while Moon transits Moola.'),
        'Moon in Pushya':('Reversal · check previous trend','User hypothesis: Pushya is a potential reversal point; check the previous trend. It is not always bearish.'),
        'Moon in Uttara Phalguni':('Mostly Bullish day','User market hypothesis: mostly bullish while Moon transits Uttara Phalguni.'),
        'Moon in Jyeshtha':('Mostly Bullish day','User market hypothesis: mostly bullish for about one day while Moon transits Jyeshtha.'),
    }
    for row in rows:
        if row['category']=='Rashi Transit' and row['bias']!='Mool Trikona · sector focus':
            row['bias']='Gap Up/Down · observation'
            note='Observation only: review the chart on the previous day and record the following NSE session gap up/down. No direction is predicted.'
            if note not in row['notes']:
                row['notes']=(row['notes']+' | ' if row['notes'] else '')+note
        rule=moon_market_views.get(row['event'])
        if rule:
            row['bias'],note=rule
            if note not in row['notes']:
                row['notes']=(row['notes']+' | ' if row['notes'] else '')+note
    rows.sort(key=lambda r:r['start_ist'])
    if include_calculated:
        from monthly_catalog import calculated_month
        rows.extend(calculated_month(y,m,rows))
        rows.sort(key=lambda r:r['start_ist'])
    eligible=market_rows(rows,y,m)
    focus=[]
    for r in eligible:
        if r['category'] in ('D9','Timing Candles') or r['priority']=='P3': continue
        focus.append(r)
    save(OUT/f'positional_{stem}.csv',[r for r in eligible if r['category'] in POSITIONAL],HEAD)
    days=set(sessions(y,m))
    for d in daily:
        day=date.fromisoformat(d[0]); hits=[r for r in focus if r['priority']=='P1' and overlaps(r,day)] if day in days else []
        d[2]='Regular NSE session' if day in days else 'Closed' if holidays(y) is not None else 'Calendar missing'
        d[3]=len(hits); d[4]=' / '.join(sorted({r['bias'] for r in hits})); d[5]='; '.join(r['event'] for r in hits)
    save(OUT/f'events_{stem}.csv',rows,HEAD); save(OUT/f'focus_{stem}.csv',focus,HEAD)
    (OUT/f'daily_events_{stem}.version').write_text('10',encoding='utf-8')
    coverage=[['Source coverage','Partial' if rows else 'No imported records','Missing records do not establish absence of events.'],['Location','Mumbai target','Legacy Delhi records remain labelled; Mumbai verification pending.'],['Trayodashi','Imported intervals',f"{sum(r['event'] in ('Krishna Trayodashi','Shukla Trayodashi') for r in rows)} intervals overlap selected month; check annual source for completeness."],['Positions','Snapshots only' if positions else 'Missing','No exact yoga boundaries inferred.'],['Market calendar','NSE regular sessions' if holidays(y) is not None else 'Missing annual calendar','Weekends and official holidays excluded; special sessions excluded. Add verified annual JSON for future years.']]
    coverage.append(["Calculated catalogs", "Enabled" if include_calculated else "Disabled", "Swiss Ephemeris research records, not Drik Panchang / Prokerala verified; incomplete event coverage."])
    timing_error = None
    timings=read_timings(y,m)
    coverage.append(["Daily Timing Candles", "Cached / parser coverage unverified" if timings is not None else "Missing", timing_error or "Prokerala; IST normalized. Full parser coverage not independently verified."])
    if timings is not None:
        save(OUT/f"timing_candles_{stem}.csv",timings,FIELDS)
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    wb=Workbook(); wb.remove(wb.active)
    def sheet(name,headers,records,widths):
        s=wb.create_sheet(name); s.append(headers)
        for r in records: s.append(r)
        s.freeze_panes='A2'; s.auto_filter.ref=s.dimensions
        for c in s[1]: c.fill=PatternFill('solid',fgColor='17365D'); c.font=Font(color='FFFFFF',bold=True); c.alignment=Alignment(wrap_text=True)
        s.row_dimensions[1].height=30
        for i,w in enumerate(widths,1): s.column_dimensions[get_column_letter(i)].width=w
        for row in s.iter_rows(min_row=2):
            for c in row: c.alignment=Alignment(vertical='top',wrap_text=True)
            s.row_dimensions[row[0].row].height=48
        return s
    widths=[22,22,20,48,24,12,25,55,28,65]
    sheet('Focus Dates',HEAD,[[r[k] for k in HEAD] for r in focus],widths)
    sheet('Calendar',['Date','Day','Market status','P1 count','Hypotheses','P1 events'],daily,[15,12,30,14,32,75])
    sheet('All Events',HEAD,[[r[k] for k in HEAD] for r in rows],widths)
    for cat in CATS: sheet(cat,HEAD,[[r[k] for k in HEAD] for r in rows if r['category']==cat],widths)
    sheet('Daily Timing Candles',FIELDS+['TradingView text'],[[r[k] for k in FIELDS]+[line(r)] for r in (timings or [])],[15,12,16,16,22,10,35,65,95])
    rules=load(ROOT/'rules.csv'); sheet('Master Rules',list(rules[0]),[list(r.values()) for r in rules],[12,55,28,70])
    from inference import RULES
    sheet('Inference Rules',['Inference','Market','Hypothesis','Condition','Interpretation'],[list(r) for r in RULES],[42,20,32,55,38])
    sheet('Coverage',['Area','Status','Notes'],coverage,[25,28,85])
    wb.save(OUT/f'Astro_Focus_{stem}.xlsx')
    (OUT/f"mode_{stem}.txt").write_text(str(include_calculated),encoding="utf-8")
    return rows,focus

def main():
    p=argparse.ArgumentParser(); p.add_argument('year',type=int,nargs='?'); p.add_argument('month',type=int,nargs='?'); p.add_argument('--rolling',action='store_true'); p.add_argument('--year',type=int,dest='full_year'); a=p.parse_args()
    if a.rolling:
        now=datetime.now(ZoneInfo('Asia/Kolkata')); months=[(now.year,now.month),next_month(now.year,now.month)]
    elif a.full_year: months=[(a.full_year,m) for m in range(1,13)]
    elif a.year and a.month: months=[(a.year,a.month)]
    else: p.error('Specify YEAR MONTH, --rolling, or --year YEAR')
    for y,m in months:
        rows,focus=build(y,m); print(f'{y}-{m:02}: {len(rows)} events, {len(focus)} focus records; review Coverage')
if __name__=='__main__': main()
