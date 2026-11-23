"""Historical event study; no order execution. All timestamps are IST."""
import csv, re, math
from pathlib import Path
from datetime import date, datetime, time, timedelta
from bisect import bisect_left, bisect_right
from statistics import mean, median
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
IST=ZoneInfo('Asia/Kolkata')
MARKETS={'NIFTY 50':'^NSEI','BANKNIFTY':'^NSEBANK'}
def key(s): return re.sub(r'[^a-z0-9]+','_',s.lower()).strip('_')
def event_key(s):
    k=key(s)
    k=k.removesuffix('_conjunction')
    aliases={'jupiter_mars':'mars_jupiter','mars_moon':'moon_mars','jupiter_moon':'moon_jupiter','saturn_moon':'moon_saturn','venus_moon':'moon_venus','saturn_0':'saturn_rashi_change','jupiter_boundary':'jupiter_rashi_change','mercury_asta_37_published_days':'mercury_combustion','mercury_asta':'mercury_combustion'}
    aliases.update({'moola':'moon_in_moola','pushya':'moon_in_pushya','uttara_phalguni':'moon_in_uttara_phalguni','jyeshtha':'moon_in_jyeshtha','moon_ashwini_pada_3':'moon_in_ashwini_pada_3','ashwini_pada_3':'moon_in_ashwini_pada_3','ashlesha_magha':'gandamool','jyeshtha_moola':'gandamool','revati_ashwini':'gandamool','moon_in_gandamool':'gandamool'})
    return aliases.get(k,k)

def read(path):
    if not path.exists(): return []
    with path.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))
def write(path, rows, fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
def events(name): return read(ROOT/'data/event_history'/f'{event_key(name)}.csv')
def catalog():
    return sorted(p.stem.replace('_',' ').title() for p in (ROOT/'data/event_history').glob('*.csv') if read(p))
POSITIONAL={'saturn_rashi_change','jupiter_rashi_change','saturn_retrograde','saturn_direct','jupiter_retrograde','jupiter_direct','mars_rahu','mars_rahu_within_3','mars_12th_from_saturn','mercury_combustion'}
NAKSHATRA={"moon_in_moola","moon_in_pushya","moon_in_uttara_phalguni","moon_in_jyeshtha","moon_in_ashwini_pada_3","gandamool"}
def default_count(name):
    k=event_key(name)
    if k in NAKSHATRA: return 100
    if k=='saturn_rashi_change': return 50
    if k=='mercury_combustion': return 30
    if k in {'jupiter_rashi_change','saturn_retrograde','saturn_direct','jupiter_retrograde','jupiter_direct'}: return 20
    return 10 if k in POSITIONAL|{'mars_jupiter'} else 50
def availability(name, market='NIFTY 50'):
    if not events(name): return 0
    return len(analyze(name,market,default_count(name))['rows'])
def market_path(market):
    if market not in MARKETS: raise ValueError('Unsupported instrument')
    return ROOT/'data/market_history'/f'{key(market)}.csv'
def fetch_market(market, start, end):
    """Optional Yahoo Finance import. Not exchange-verified; user initiates."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ValueError("Install market downloader: python -m pip install yfinance") from exc
    df=yf.download(MARKETS[market],start=start.isoformat(),end=(end+timedelta(days=1)).isoformat(),auto_adjust=False,progress=False,threads=False)
    if df.empty: raise ValueError('No market data returned; import NSE Indices CSV instead.')
    if getattr(df.columns,'nlevels',1)>1: df.columns=df.columns.get_level_values(0)
    rows=[]
    for idx,r in df.iterrows():
        vals=[float(r[c]) for c in ('Open','High','Low','Close')]
        if not all(math.isfinite(v) and v>0 for v in vals): continue
        rows.append(dict(zip(['date','open','high','low','close','source','status'],[idx.date().isoformat(),*vals,'https://finance.yahoo.com/quote/'+MARKETS[market]+'/history/','Provider data; not NSE cross-checked'])))
    if not rows: raise ValueError('No valid OHLC records')
    path=market_path(market); merged={r['date']:r for r in read(path)}; merged.update({r['date']:r for r in rows}); write(path,[merged[d] for d in sorted(merged)],['date','open','high','low','close','source','status'])
    return len(rows)
def analyze(name, market='NIFTY 50', count=10, mode='post', as_of=None):
    if mode not in ('post','event'): raise ValueError('Invalid session mode')
    if count not in (5,10,20,30,50,100): raise ValueError('Invalid sample size')
    cutoff=as_of or datetime.now(IST).date()
    nakshatra=event_key(name) in NAKSHATRA
    lunar=nakshatra or event_key(name) in ('krishna_trayodashi','shukla_trayodashi','purnima','amavasya')
    es=sorted((e for e in events(name) if datetime.fromisoformat(e.get('end_ist') or e['start_ist']).date()<cutoff),key=lambda e:e['start_ist'],reverse=True)[:count]
    prices={}
    for r in read(market_path(market)):
        d=date.fromisoformat(r['date']); v=[float(r[k]) for k in ('open','high','low','close')]
        if all(math.isfinite(x) and x>0 for x in v) and v[2]<=min(v[0],v[3])<=max(v[0],v[3])<=v[1] and d<cutoff: prices[d]=r
    days=sorted(prices); results=[]; missing=[]
    if (lunar or event_key(name).startswith('moon_') or event_key(name) in POSITIONAL) and (len(days)<100 or not es or sum(date.fromisoformat(es[-1]['start_ist'][:10])<=d<=date.fromisoformat(es[0]['start_ist'][:10]) for d in days)<(date.fromisoformat(es[0]['start_ist'][:10])-date.fromisoformat(es[-1]['start_ist'][:10])).days*0.55):
        return {'event':name,'market':market,'requested':count,'available_events':len(es),'mode':mode,'rows':[],'missing':[],'stats':{},'summary':'This research requires continuous daily OHLC. Select Fetch / cache market history or import an official NSE CSV. The bundled sparse conjunction cache is not sufficient.','status':'Market history required'}
    def pct(a,b): return round((a/b-1)*100,4)
    for e in es:
        t=datetime.fromisoformat(e['start_ist']); d=t.date(); end=e.get('end_ist',''); i=bisect_left(days,d)
        if mode=='event' and not lunar and not end:
            if i==len(days) or days[i]!=d: missing.append({'event':e['start_ist'],'reason':'No same-day session / data'}); continue
        elif not end and len(e['start_ist'])>10 and t.time()>=time(15,30): i=bisect_right(days,d)
        if i>=len(days) or i==0 or (days[i]-d).days>7 or (days[i]-days[i-1]).days>7:
            missing.append({'event':e['start_ist'],'reason':'Missing nearby market history'}); continue
        if end:
            finish=datetime.fromisoformat(end)
            while i<len(days) and datetime.combine(days[i],time(15,30))<=t: i+=1
            if i>=len(days) or datetime.combine(days[i],time(9,15))>=finish:
                missing.append({'event':e['start_ist'],'reason':'No overlapping session'}); continue
        p=prices[days[i]]; prev=float(prices[days[i-1]]['close']); o,h,l,c=[float(p[k]) for k in ('open','high','low','close')]
        row={'event':e['start_ist'],'session':days[i].isoformat(),'previous_close':prev,'open':o,'high':h,'low':l,'close':c,'gap_points':round(o-prev,2),'gap_pct':pct(o,prev),'intraday_pct':pct(c,o),'return_pct':pct(c,prev),'range_pct':pct(h,l),'event_source':e.get('source',''),'event_status':e.get('status',''),'market_source':p.get('source',''),'market_status':p.get('status','')}
        for n in (1,3,5,10,20): row[f'next_{n}_pct']=pct(float(prices[days[i+n]]['close']),c) if i+n<len(days) and all((days[j+1]-days[j]).days<=7 for j in range(i,i+n)) and (days[i+n]-days[i]).days<=n*3+7 else None
        if lunar:
            row['end_ist']=end
            row['prior_3_pct']=pct(prev,float(prices[days[i-4]]['close'])) if i>=4 and (days[i-1]-days[i-4]).days<=7 else None
            before=row['prior_3_pct']; after=row['next_3_pct']
            row['reversal_3']=None if before is None or after is None or not before or not after else before*after<0
        if event_key(name) in POSITIONAL:
            row['prior_10_pct']=pct(prev,float(prices[days[i-11]]['close'])) if i>=11 and (days[i-1]-days[i-11]).days<=20 else None
            if end:
                finish=datetime.fromisoformat(end)
                j=i
                while j+1<len(days) and datetime.combine(days[j+1],time(9,15))<finish: j+=1
                complete=(days[-1]>=finish.date() and (finish.date()-days[j]).days<=7 and all((days[k+1]-days[k]).days<=7 for k in range(i,j)))
                row.update(end_ist=end,duration_days=round((finish-t).total_seconds()/86400,2),period_sessions=j-i+1,period_return_pct=pct(float(prices[days[j]]['close']),prev) if complete else None)
                if event_key(name)=='mercury_combustion':
                    duration=row['duration_days']; row['duration_group']='Under 20 days' if duration<20 else '20 to under 29 days' if duration<29 else '29 days or more'
        results.append(row)
    n=len(results); stats={}; summary='No historical market observations available. Import market OHLC and event history to begin.'
    if n:
        stats={'observations':n,'gap_up':sum(r['gap_pct']>0 for r in results),'positive_close':sum(r['return_pct']>0 for r in results),'bullish_candle':sum(r['intraday_pct']>0 for r in results),'mean_return':round(mean(r['return_pct'] for r in results),3),'median_return':round(median(r['return_pct'] for r in results),3),'average_range':round(mean(r['range_pct'] for r in results),3)}
        nxt=[r['next_1_pct'] for r in results if r['next_1_pct'] is not None]; stats.update(next_positive=sum(x>0 for x in nxt),next_count=len(nxt))
        base=[pct(float(prices[b]['close']),float(prices[a]['close'])) for a,b in zip(days,days[1:]) if (b-a).days<=7 and days[0]<=a and date.fromisoformat(results[-1]['session'])<=b<=date.fromisoformat(results[0]['session'])]; stats['baseline_positive_pct']=round(100*sum(x>0 for x in base)/len(base),2) if base and len(base) >= (date.fromisoformat(results[0]['session'])-date.fromisoformat(results[-1]['session'])).days*0.55 and len(base)>20 else None
        g=stats['gap_up']; p=stats['positive_close']; summary=f'Gap-up openings occurred in {g}/{n} observations; {p}/{n} closed above the previous close. The median session return was {stats["median_return"]:+.3f}%. Bullish intraday candles occurred in {stats["bullish_candle"]}/{n} sessions.'
        if nxt: summary+=f' The following session was positive in {stats["next_positive"]}/{len(nxt)} available observations.'
        if stats['baseline_positive_pct'] is not None: summary+=f' Ordinary-session positive frequency over the same date span was {stats["baseline_positive_pct"]:.1f}% (includes event sessions).'
        if lunar:
            rev=[r['reversal_3'] for r in results if r['reversal_3'] is not None]
            stats['reversal_count']=len(rev); stats['reversal_3']=sum(rev)
            if rev: summary+=f' Three-session direction reversed in {sum(rev)}/{len(rev)} observations with nonzero prior and subsequent returns.'
        if nakshatra: summary+=' Nakshatra and Gandamool intervals use the first overlapping regular session; nested events are not independent confirmations.'
        elif lunar: summary+=' Lunar intervals use the first overlapping regular session; non-overlapping intervals are excluded.'
        if event_key(name) in POSITIONAL:
            for horizon in (5,10,20):
                vals=[r[f'next_{horizon}_pct'] for r in results if r[f'next_{horizon}_pct'] is not None]
                if vals:
                    stats[f'next_{horizon}_median']=round(median(vals),3)
                    summary+=f' Next {horizon} sessions: median {median(vals):+.3f}% ({len(vals)} observations).'
            vals=[r['period_return_pct'] for r in results if r.get('period_return_pct') is not None]
            if vals:
                stats['period_median']=round(median(vals),3); summary+=f' Full interval median return {median(vals):+.3f}% ({len(vals)} completed intervals).'
            if event_key(name)=='mercury_combustion':
                groups={}
                for r in results:
                    if r.get('period_return_pct') is not None: groups.setdefault(r['duration_group'],[]).append(r['period_return_pct'])
                stats['duration_groups']={k:{'count':len(v),'median_return':round(median(v),3),'positive':sum(x>0 for x in v)} for k,v in groups.items()}
                for k,v in stats['duration_groups'].items(): summary+=f' {k}: {v["positive"]}/{v["count"]} positive, median {v["median_return"]:+.3f}%.'
        summary+=' These descriptive results do not establish a predictive edge.'
    return {'event':name,'market':market,'requested':count,'available_events':len(es),'mode':mode,'rows':results,'missing':missing,'stats':stats,'summary':summary,'status':'Partial history' if n<count else 'Sample complete; source verification required'}

def refresh(name, market, count=10):
    es=sorted((e for e in events(name) if datetime.fromisoformat(e.get('end_ist') or e['start_ist']).date()<datetime.now(IST).date()),key=lambda e:e['start_ist'],reverse=True)[:count]
    if not es: raise ValueError('No historical event dates imported for this event.')
    start=datetime.fromisoformat(es[-1]['start_ist']).date()-timedelta(days=15)
    end=min(datetime.now(IST).date()-timedelta(days=1),datetime.fromisoformat(es[0]['start_ist']).date()+timedelta(days=30))
    return fetch_market(market,start,end)
