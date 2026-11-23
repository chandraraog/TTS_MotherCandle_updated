"""Calculated research catalogs, Lahiri sidereal, mean Rahu. Source cross-check pending."""
from datetime import datetime, timezone, timedelta
import swisseph as swe
from .engine import ROOT, IST, write
swe.set_sid_mode(swe.SIDM_LAHIRI)
FLAGS=swe.FLG_MOSEPH|swe.FLG_SIDEREAL|swe.FLG_SPEED
SOURCE='https://www.astro.com/swisseph/'
def pos(j,p): return swe.calc_ut(j,p,FLAGS)[0]
def lon(j,p): return pos(j,p)[0]
def signed(x): return (x+180)%360-180
def stamp(j): return (datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(days=j-2440587.5)).astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')
def generate(start_year=1995,end_year=2027):
    start=swe.julday(start_year,1,1,0); end=swe.julday(end_year,1,1,0)
    def roots(fn):
        a=start; x=fn(a); out=[]
        while a<end:
            b=min(a+2,end); y=fn(b)
            if x*y<0 and abs(x-y)<180:
                lo,hi=a,b
                for _ in range(30):
                    m=(lo+hi)/2
                    if fn(lo)*fn(m)<=0: hi=m
                    else: lo=m
                out.append((lo+hi)/2)
            a,x=b,y
        return out
    def save(name, pairs, note):
        rows=[dict(start_ist=stamp(a),end_ist=stamp(b) if b else '',source=SOURCE,status='Calculated; source cross-check pending; '+note) for a,b in pairs]
        write(ROOT/'data/event_history'/f'{name}.csv',rows,['start_ist','end_ist','source','status']); print(name,len(rows))
    def intervals(fn):
        r=roots(fn); return [(a,b) for a,b in zip(r,r[1:]) if fn((a+b)/2)<0]
    for name,p in [('saturn',swe.SATURN),('jupiter',swe.JUPITER)]:
        crossings=sorted(j for k in range(12) for j in roots(lambda j,k=k:signed(lon(j,p)-30*k)))
        save(name+'_rashi_change',[(j,None) for j in crossings],'Lahiri sign boundary; includes retrograde crossings')
        stations=roots(lambda j:pos(j,p)[3]);
        for direction in ('retrograde','direct'):
            save(name+'_'+direction,[(j,None) for j in stations if (pos(j+0.1,p)[3]<0)==(direction=='retrograde')],'Longitude speed zero')
    save('mars_rahu',[(j,None) for j in roots(lambda j:signed(lon(j,swe.MARS)-lon(j,swe.MEAN_NODE)))],'Exact mean-node conjunction')
    save('mars_rahu_within_3',intervals(lambda j:abs(signed(lon(j,swe.MARS)-lon(j,swe.MEAN_NODE)))-3),'Mean node; separation below 3 degrees')
    # Rashi membership changes only at a sign crossing of either planet.
    cuts=sorted({start,end,*[j for p in (swe.MARS,swe.SATURN) for k in range(12) for j in roots(lambda j,p=p,k=k:signed(lon(j,p)-30*k))]})
    pairs=[]
    for a,b in zip(cuts,cuts[1:]):
        m=(a+b)/2
        if (int(lon(m,swe.MARS)//30)-int(lon(m,swe.SATURN)//30))%12==11:
            if pairs and abs(pairs[-1][1]-a)<1e-7: pairs[-1]=(pairs[-1][0],b)
            else: pairs.append((a,b))
    save('mars_12th_from_saturn',[(a,b) for a,b in pairs if a>start and b<end],'Mars one Rashi behind Saturn')
    # Fixed 14 degree direct/retrograde-independent definition, explicit research convention.
    save('mercury_combustion',intervals(lambda j:abs(signed(lon(j,swe.MERCURY)-lon(j,swe.SUN)))-14),'Fixed 14 degree separation; research convention, not Drik Asta timings')
if __name__=='__main__': generate()
