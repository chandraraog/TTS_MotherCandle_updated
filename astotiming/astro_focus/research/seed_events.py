"""Calculate exact geocentric apparent ecliptic longitude conjunctions via Swiss Ephemeris (Moshier)."""
from datetime import datetime, timezone, timedelta
from .engine import IST, ROOT, write
import swisseph as swe

def generate():
    def diff(j):
        a=swe.calc_ut(j,swe.MARS,swe.FLG_MOSEPH)[0][0]; b=swe.calc_ut(j,swe.JUPITER,swe.FLG_MOSEPH)[0][0]; return (a-b+180)%360-180
    a=swe.julday(1995,1,1); end=swe.julday(2026,11,14); rows=[]
    while a<end:
        b=a+1; x,y=diff(a),diff(b)
        if x*y<=0 and abs(x-y)<10:
            lo,hi=a,b
            for _ in range(35):
                mid=(lo+hi)/2
                if diff(lo)*diff(mid)<=0: hi=mid
                else: lo=mid
            j=(lo+hi)/2; t=(datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(days=j-2440587.5)).astimezone(IST)
            rows.append({'start_ist':t.strftime('%Y-%m-%d %H:%M:%S'),'end_ist':'','source':'https://www.astro.com/swisseph/','status':'Calculated Swiss Ephemeris; Prokerala cross-check pending'})
        a=b
    write(ROOT/'data/event_history/mars_jupiter.csv',rows,['start_ist','end_ist','source','status'])
if __name__=='__main__': generate()
