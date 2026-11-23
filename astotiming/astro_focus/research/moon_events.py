"""Geocentric conjunction catalogs; independently cross-check with Prokerala."""
from datetime import datetime, timezone, timedelta
import swisseph as swe
from .engine import ROOT, IST, write

def generate():
    for name, planet in [('moon_mars',swe.MARS),('moon_jupiter',swe.JUPITER),('moon_saturn',swe.SATURN),('moon_venus',swe.VENUS)]:
        def diff(j):
            return (swe.calc_ut(j,swe.MOON,swe.FLG_MOSEPH)[0][0]-swe.calc_ut(j,planet,swe.FLG_MOSEPH)[0][0]+180)%360-180
        a=swe.julday(2020,1,1); end=swe.julday(2027,1,1); rows=[]
        while a<end:
            b=a+0.5; x,y=diff(a),diff(b)
            if x*y<=0 and abs(x-y)<30:
                lo,hi=a,b
                for _ in range(35):
                    mid=(lo+hi)/2
                    if diff(lo)*diff(mid)<=0: hi=mid
                    else: lo=mid
                t=(datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(days=(lo+hi)/2-2440587.5)).astimezone(IST)
                rows.append(dict(start_ist=t.strftime('%Y-%m-%d %H:%M:%S'),end_ist='',source='https://www.astro.com/swisseph/',status='Calculated Swiss Ephemeris; Prokerala cross-check pending'))
            a=b
        write(ROOT/'data/event_history'/f'{name}.csv',rows,['start_ist','end_ist','source','status'])
if __name__=='__main__': generate()
