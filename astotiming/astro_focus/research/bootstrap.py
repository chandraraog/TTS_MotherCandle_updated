"""Populate one shared daily market cache and export every research study."""
import argparse, csv, json
from datetime import date, datetime, timedelta
from pathlib import Path
from .engine import ROOT, IST, catalog, events, default_count, analyze, fetch_market, market_path, read, write, key

def import_csv(path, market):
    import pandas as pd
    df=pd.read_csv(path); df.columns=[str(c).strip().lower().replace(' ','_') for c in df.columns]
    aliases={'historical_date':'date','index_date':'date','open_index_value':'open','high_index_value':'high','low_index_value':'low','closing_index_value':'close','close_index_value':'close'}
    df=df.rename(columns=aliases)
    required=['date','open','high','low','close']
    if not set(required)<=set(df.columns): raise ValueError('CSV requires Date, Open, High, Low, Close (NSE headers also supported).')
    rows={}
    for _,r in df.iterrows():
        d=pd.to_datetime(r['date'],dayfirst=True,errors='raise').date()
        if d>=datetime.now(IST).date(): continue
        v=[float(str(r[k]).replace(',','')) for k in required[1:]]
        import math
        if not all(math.isfinite(x) and x>0 for x in v) or not v[2]<=min(v[0],v[3])<=max(v[0],v[3])<=v[1]: raise ValueError(f'Invalid OHLC: {d}')
        row=dict(zip(required,[d.isoformat(),*v])); row.update(source=str(path),status='User imported; source verification pending'); rows[d.isoformat()]=row
    if not rows: raise ValueError('No completed market sessions in CSV')
    p=market_path(market); merged={r['date']:r for r in read(p)}; merged.update(rows); write(p,[merged[d] for d in sorted(merged)],required+['source','status']); return len(rows)

def run(market='NIFTY 50', download=False, csv_path=None):
    if csv_path: import_csv(csv_path,market)
    if download:
        start=date(1994,12,1); end=datetime.now(IST).date()-timedelta(days=1)
        fetch_market(market,start,end)
    out=ROOT/'output/research'; out.mkdir(parents=True,exist_ok=True)
    results=[]
    for name in catalog():
        r=analyze(name,market,default_count(name)); results.append(r)
        (out/f'{key(name)}_{key(market)}.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
        print(f'{name}: {len(r["rows"])}/{r["requested"]} matched | {r["status"]}')
    return results

def main():
    p=argparse.ArgumentParser(); p.add_argument('--market',default='NIFTY 50'); g=p.add_mutually_exclusive_group(); g.add_argument('--download',action='store_true'); g.add_argument('--csv',type=Path); a=p.parse_args(); run(a.market,a.download,a.csv)
if __name__=='__main__': main()
