"""Import exact Mumbai Trayodashi intervals from Drik Panchang's annual Pradosh page."""
import re
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from astro_calendar_v3 import HEAD, load, save
ROOT=Path(__file__).parent
URL='https://www.drikpanchang.com/vrats/pradoshdates.html'
MONTHS='Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()

def parse_year(html, year):
    text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
    if not re.search(r'Mumbai,\s*(?:Maharashtra|India)',text,re.I): raise ValueError('Mumbai location not verified')
    if not re.search(rf'\b{year}\s+Pradosh',text,re.I): raise ValueError('Requested year not verified')
    clock=r'(\d{1,2}:\d{2}\s*[AP]M)\s*,?\s*(?:on\s+)?([A-Za-z]{3})\s+(\d{1,2})'
    pattern=rf'(Krishna|Shukla)\s+Trayodashi\s+Begins\s*-\s*{clock}\s+Ends\s*-\s*{clock}'
    rows=[]
    for paksha,t1,m1,d1,t2,m2,d2 in re.findall(pattern,text,re.I):
        a=datetime.strptime(f'{year} {m1} {d1} {t1.upper()}','%Y %b %d %I:%M %p')
        b=datetime.strptime(f'{year} {m2} {d2} {t2.upper()}','%Y %b %d %I:%M %p')
        if b<a: b=b.replace(year=year+1)
        rows.append(record(a,b,paksha.title()))
    if not 22<=len(rows)<=26: raise ValueError(f'Incomplete annual Trayodashi parse: {len(rows)} intervals; expected 22–26')
    if len({(r['start_ist'],r['event']) for r in rows})!=len(rows): raise ValueError('Duplicate intervals')
    return rows

def record(a,b,paksha):
    return dict(zip(HEAD,[a.strftime('%Y-%m-%d %H:%M'),b.strftime('%Y-%m-%d %H:%M'),'Must Watch',paksha+' Trayodashi','Bullish' if paksha=='Shukla' else 'Bearish','P2','NIFTY / BANKNIFTY',f'{URL}?year={a.year}&geoname-id=1275339','Published IST / Mumbai','Exact Tithi interval; direction is research hypothesis']))

def fetch_year(year):
    r=requests.get(URL,params={'year':year,'geoname-id':1275339},headers={'User-Agent':'Mozilla/5.0'},timeout=30); r.raise_for_status()
    return parse_year(r.text,year)

def merge(rows):
    groups={}
    for r in rows: groups.setdefault(r['start_ist'][:7],[]).append(r)
    for month,items in groups.items():
        p=ROOT/'data'/month/'events.csv'; p.parent.mkdir(parents=True,exist_ok=True)
        old=load(p) if p.exists() else []
        old=[r for r in old if r['event'] not in ('Krishna Trayodashi','Shukla Trayodashi')]
        save(p,sorted(old+items,key=lambda r:r['start_ist']),HEAD)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('year',type=int); a=p.parse_args()
    rows=fetch_year(a.year); merge(rows); print(f'Imported {len(rows)} exact Mumbai intervals. Run focus.py to regenerate calendars.')
