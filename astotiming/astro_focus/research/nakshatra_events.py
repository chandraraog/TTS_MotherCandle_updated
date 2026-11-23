"""Generate complete Moon nakshatra intervals, Lahiri sidereal, IST."""
from datetime import timedelta
import swisseph as swe
from .positional_events import lon, stamp, SOURCE
from .engine import ROOT, write

# Gandamool pairs are contiguous across the zodiac boundary or within it.
SPECS = {
    'moon_in_moola': (240, 253+1/3),
    'moon_in_pushya': (93+1/3, 106+2/3),
    'moon_in_uttara_phalguni': (146+2/3, 160),
    'moon_in_jyeshtha': (226+2/3, 240),
    'moon_in_ashwini_pada_3': (6+2/3, 10),
}
GANDAMOOL = [(346+2/3, 13+1/3), (106+2/3, 133+1/3), (226+2/3, 253+1/3)]

def generate(start_year=2014, end_year=2027):
    start=swe.julday(start_year,1,1,0); end=swe.julday(end_year,1,1,0)
    def inside(j, bounds):
        a,b=bounds; return (lon(j,swe.MOON)-a)%360 < (b-a)%360
    def intervals(bounds):
        a=start; state=inside(a,bounds); entry=None; out=[]
        while a<end:
            b=min(a+1/12,end); new=inside(b,bounds)
            if new!=state:
                lo,hi=a,b
                for _ in range(30):
                    m=(lo+hi)/2
                    if inside(m,bounds)==state: lo=m
                    else: hi=m
                edge=(lo+hi)/2
                if new: entry=edge
                elif entry is not None: out.append((entry,edge)); entry=None
            a,state=b,new
        return out
    for name,bounds in {**{k:[v] for k,v in SPECS.items()},'gandamool':GANDAMOOL}.items():
        pairs=sorted(p for bound in bounds for p in intervals(bound))
        rows=[dict(start_ist=stamp(a),end_ist=stamp(b),source=SOURCE,status='Calculated Lahiri Moon longitude; Drik Panchang cross-check pending') for a,b in pairs]
        write(ROOT/'data/event_history'/f'{name}.csv',rows,['start_ist','end_ist','source','status'])
        print(name,len(rows))
if __name__=='__main__': generate()
