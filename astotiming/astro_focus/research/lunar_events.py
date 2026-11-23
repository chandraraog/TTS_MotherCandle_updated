"""Generate geocentric lunar elongation intervals, IST; Drik cross-check pending."""
from datetime import datetime, timezone, timedelta
import swisseph as swe
from .engine import IST, ROOT, write
TARGETS={'shukla_trayodashi':(144,156),'purnima':(168,180),'krishna_trayodashi':(324,336),'amavasya':(348,360)}
def generate(start_year=2020, end_year=2027):
    def angle(j):
        return (swe.calc_ut(j,swe.MOON,swe.FLG_MOSEPH)[0][0]-swe.calc_ut(j,swe.SUN,swe.FLG_MOSEPH)[0][0])%360
    def stamp(j):
        return (datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(days=j-2440587.5)).astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')
    boundaries={v%360:[] for pair in TARGETS.values() for v in pair}
    a=swe.julday(start_year,1,1,0); end=swe.julday(end_year,1,1,0)
    while a<end:
        b=a+0.25; x=angle(a); delta=(angle(b)-x)%360
        for target in boundaries:
            distance=(target-x)%360
            if 0<distance<=delta:
                lo,hi=a,b
                for _ in range(35):
                    mid=(lo+hi)/2
                    if (angle(mid)-x)%360<distance: lo=mid
                    else: hi=mid
                boundaries[target].append((lo+hi)/2)
        a=b
    for name,(start,finish) in TARGETS.items():
        rows=[]
        for a in boundaries[start]:
            b=next((b for b in boundaries[finish%360] if a<b<a+3),None)
            if b is not None: rows.append({'start_ist':stamp(a),'end_ist':stamp(b),'source':'https://www.astro.com/swisseph/','status':'Calculated Swiss Ephemeris; Drik Panchang cross-check pending'})
        write(ROOT/'data/event_history'/f'{name}.csv',rows,['start_ist','end_ist','source','status'])
if __name__=='__main__': generate()
