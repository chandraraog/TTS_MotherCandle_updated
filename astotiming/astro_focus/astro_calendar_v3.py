import csv, json, argparse, calendar
from datetime import datetime, timedelta, date
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).parent
D='https://www.drikpanchang.com/'
P='https://www.prokerala.com/'
SIGNS='Aries Taurus Gemini Cancer Leo Virgo Libra Scorpio Sagittarius Capricorn Aquarius Pisces'.split()
PLANETS='Sun Moon Mars Mercury Jupiter Venus Saturn Rahu Ketu'.split()
LORDS='Mars Venus Mercury Moon Sun Mercury Venus Mars Jupiter Saturn Saturn Jupiter'.split()
MAL={'Sun','Mars','Saturn','Rahu','Ketu'}
HEAD=['start_ist','end_ist','category','event','bias','priority','instruments','source','status','notes']
def dt(s): return datetime.fromisoformat(s)
def mercury(days): return 'Bearish' if days>=29 else 'Bullish' if days<20 else 'Neutral'
def classify_positions(p):
    if set(p)!=set(PLANETS) or any(not 0<=v<360 for v in p.values()): raise ValueError('Need all nine sidereal longitudes in [0,360)')
    s={k:int(v//30) for k,v in p.items()}; out=[]
    for i in range(12):
        members=[k for k in PLANETS if s[k]==i]
        if len(members)>=5: out.append(('Massive Moves','Cluster '+SIGNS[i]+': '+', '.join(members),'Directional watch'))
    gap=abs(p['Mars']-p['Rahu']); gap=min(gap,360-gap)
    if s['Mars']==s['Rahu'] and gap<=3: out.append(('Massive Moves','Mars–Rahu within 3°','Volatility'))
    for k in ['Moon','Mercury']:
        if all(any(s[m]==(s[k]+offset)%12 for m in MAL) for offset in [-1,1]): out.append(('Yoga',k+' Papa Kartari','Bullish' if k=='Mercury' else 'Research'))
    for i,a in enumerate(PLANETS[:7]):
        for b in PLANETS[i+1:7]:
            if LORDS[s[a]]==b and LORDS[s[b]]==a: out.append(('Yoga',a+' / '+b+' exchange','Research'))
    if (s['Saturn']-s['Mars'])%12==1: out.append(('Massive Moves','Mars 12th from Saturn','Directional watch'))
    if p['Jupiter']%30>=29 or p['Jupiter']%30<1: out.append(('Massive Moves','Jupiter boundary '+SIGNS[s['Jupiter']],'Directional watch'))
    n=int(p['Moon']/(10/3))%12
    if n==s['Moon']: out.append(('D9','Moon Vargottama '+SIGNS[n],'Bullish' if n==3 else 'Research'))
    if n in (1,3): out.append(('D9','Moon Navamsa '+SIGNS[n],'Bullish'))
    if 6+2/3<=p['Moon']<10: out.append(('D9','Moon Ashwini Pada 3','Bullish'))
    for k in PLANETS:
        if k!='Moon' and int(p[k]/(10/3))%12==n: out.append(('D9','Moon / '+k+' same Navamsa','Research'))
    for k in ['Mars','Mercury','Jupiter','Venus','Saturn','Sun','Moon']:
        offsets={'Mars':[3,6,7],'Jupiter':[4,6,8],'Saturn':[2,6,9]}.get(k,[6])
        if k!='Mercury' and (s['Mercury']-s[k])%12 in offsets: out.append(('Yoga',k+' Drishti on Mercury','Bullish' if k in MAL else 'Bearish' if k in ['Jupiter','Venus'] else 'Research'))
    return out

def load(path):
    with open(path,encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))
def save(path, rows, fields):
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
def generate(year,month,events,positions=None,holidays=()):
    lo=datetime(year,month,1); hi=lo+timedelta(days=calendar.monthrange(year,month)[1]); rows=[]
    for r in events:
        if not r['source'].startswith((D,P)): raise ValueError('Unsupported source '+r['source'])
        a=dt(r['start_ist']); b=dt(r['end_ist']) if r['end_ist'] else a
        if b<a: raise ValueError('End before start')
        if a<hi and b>=lo: rows.append(dict(r))
    if positions:
        prev=None
        for r in sorted(positions,key=lambda x:x['timestamp_ist']):
            if not r['source'].startswith(D): raise ValueError('Positions must be Drik Panchang')
            t=dt(r['timestamp_ist']); p={k:float(r[k]) for k in PLANETS}; flags=classify_positions(p)
            if prev and int(prev[1]['Saturn']//30)!=int(p['Saturn']//30): flags.append(('Massive Moves','Saturn boundary between samples','Bearish'))
            prev=(t,p)
            if lo<=t<hi:
                for cat,event,bias in flags: rows.append(dict(zip(HEAD,[t.isoformat(' '),'',cat,event,bias,'P1' if cat=='Massive Moves' else 'P2','All',r['source'],'Derived snapshot','Sample only; not exact start/end'])))
    rows.sort(key=lambda r:r['start_ist']); daily=[]
    for i in range((hi-lo).days):
        day=(lo+timedelta(days=i)).date(); a=datetime.combine(day,datetime.min.time()); b=a+timedelta(days=1)
        hits=[r for r in rows if dt(r['start_ist'])<b and (dt(r['end_ist']) if r['end_ist'] else dt(r['start_ist']))>=a and r['priority']=='P1']
        biases={r['bias'] for r in hits}; daily.append([str(day),day.strftime('%a'),'Closed' if day.weekday()>=5 or str(day) in holidays else 'Weekday / verify exchange',len(hits),'Conflict' if {'Bullish','Bearish'}<=biases else ' / '.join(sorted(biases)), '; '.join(dict.fromkeys(r['event'] for r in hits))])
    return rows,daily

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('year',type=int); ap.add_argument('month',type=int); ap.add_argument('--events',type=Path,default=ROOT/'december_events.csv'); ap.add_argument('--positions',type=Path); ap.add_argument('--output',type=Path,default=ROOT/'output'); args=ap.parse_args()
    rows,daily=generate(args.year,args.month,load(args.events),load(args.positions) if args.positions else None, ['2026-12-25']); args.output.mkdir(exist_ok=True,parents=True)
    from artifact_tool import Workbook, SpreadsheetFile
    wb=Workbook.create()
    def sheet(name,headers,data,widths):
        s=wb.worksheets.add(name); matrix=[headers]+data; s.get_range_by_indexes(0,0,len(matrix),len(headers)).values=matrix
        s.get_range_by_indexes(0,0,1,len(headers)).format={'fill':'#17365D','font':{'bold':True,'color':'#FFFFFF'},'row_height':30,'wrap_text':True}; s.freeze_panes.freeze_rows(1)
        for i,w in enumerate(widths): s.get_range_by_indexes(0,i,len(matrix),1).format.column_width=w
        if data: s.get_range_by_indexes(1,0,len(data),len(headers)).format.wrap_text=True; s.get_range_by_indexes(1,0,len(data),len(headers)).format.row_height=44
        return s
    sheet('Dashboard',['Date','Day','NSE status','P1 events','Hypotheses','Focus events'],daily,[15,10,27,12,32,75])
    sheet('All Events',HEAD,[[r[k] for k in HEAD] for r in rows],[22,22,20,44,23,12,20,55,26,55])
    for cat in ['Must Watch','Gandamool','Rashi Transit','Nakshatra Transit','Combustion','Retrograde','Eclipse','Conjunction','Yoga','Massive Moves','D9','Sector Watch','Timing Candles']:
        subset=[r for r in rows if r['category']==cat]; sheet(cat,HEAD,[[r[k] for k in HEAD] for r in subset],[22,22,20,44,23,12,20,55,26,55])
    rules=load(ROOT/'rules.csv'); sheet('Master Rules',list(rules[0]),[list(r.values()) for r in rules],[20,48,30,60])
    coverage=load(ROOT/'coverage.csv'); sheet('Coverage',list(coverage[0]),[list(r.values()) for r in coverage],[28,28,85])
    sheet('Trade Journal',['Date','Instrument','Event','POB high','POB low','Entry','Stop','Target','Exit','Net PnL','Notes'],[],[15,20,35,15,15,15,15,15,15,15,45])
    stem=f'{args.year}-{args.month:02}'; SpreadsheetFile.export_xlsx(wb).save(str(args.output/f'Astro_V3_{stem}.xlsx')); save(args.output/f'events_{stem}.csv',rows,HEAD)
    print(f'{len(rows)} events; {len(daily)} days; coverage sheet must be reviewed')
if __name__=='__main__': main()
