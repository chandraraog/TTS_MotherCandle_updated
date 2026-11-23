"""Optional, explicitly unverified calculated calendar from bundled research catalogs."""
from datetime import datetime, timedelta, time
from calendar import monthrange
from zoneinfo import ZoneInfo
from research.engine import events, event_key
from research.positional_events import stamp, FLAGS, SOURCE
from papakartari import RASHIS, classify_moon_papakartari, format_papakartari_summary
from astro_calendar_v3 import HEAD
import swisseph as swe

RULES = {
 'krishna_trayodashi':('Krishna Trayodashi','Must Watch','Bearish','P1'),
 'shukla_trayodashi':('Shukla Trayodashi','Must Watch','Bullish','P1'),
 'purnima':('Purnima','Must Watch','Reversal','P1'), 'amavasya':('Amavasya','Must Watch','Reversal','P1'),
 'moon_in_moola':('Moon in Moola','Nakshatra Transit','Bearish','P1'),
 'moon_in_pushya':('Moon in Pushya','Nakshatra Transit','Reversal · check previous trend','P1'),
 'moon_in_uttara_phalguni':('Moon in Uttara Phalguni','Nakshatra Transit','Bullish','P1'),
 'moon_in_jyeshtha':('Moon in Jyeshtha','Nakshatra Transit','Research','P2'),
 'moon_in_ashwini_pada_3':('Moon in Ashwini Pada 3','Nakshatra Transit','Bullish','P2'),
 'gandamool':('Gandamool','Gandamool','Research','P2'),
 'moon_mars':('Moon Mars','Conjunction','Bullish / reversal','P1'),
 'moon_jupiter':('Moon Jupiter','Conjunction','Bearish','P2'),
 'moon_saturn':('Moon Saturn','Conjunction','Bullish','P2'),
 'moon_venus':('Moon Venus','Conjunction','Bearish','P2'),
 'mars_jupiter':('Mars Jupiter','Conjunction','Directional watch','P1'),
 'mars_rahu':('Mars Rahu','Conjunction','Volatility','P1'),
 'mars_rahu_within_3':('Mars Rahu within 3','Massive Moves','Volatility','P1'),
 'mars_12th_from_saturn':('Mars 12th from Saturn','Massive Moves','Directional watch','P1'),
 'mercury_combustion':('Mercury Combustion','Combustion','Research','P2'),
 **{f'{p}_{s}':(f'{p.title()} {s}','Retrograde','Research','P2') for p in ('saturn','jupiter') for s in ('retrograde','direct')}
}
def calculated_month(year, month, imported):
    lo=datetime(year,month,1); hi=lo+timedelta(days=monthrange(year,month)[1]); result=[]
    for key,(name,category,bias,priority) in RULES.items():
        for r in events(key):
            a=datetime.fromisoformat(r['start_ist']); b=datetime.fromisoformat(r['end_ist'] or r['start_ist'])
            if not (a<hi and b>=lo): continue
            # Prefer published records for the same event in the same approximate window.
            if any(event_key(x['event'])==key and datetime.fromisoformat(x['start_ist'])<=b+timedelta(days=1) and datetime.fromisoformat(x['end_ist'] or x['start_ist'])>=a-timedelta(days=1) for x in imported): continue
            result.append(dict(zip(HEAD,[r['start_ist'],r['end_ist'],category,name,bias,priority,'All',r['source'],r['status'],'Calculated research catalog; not published source data; hypothesis only'])))
    return result

def rashi_observation_events(year, month, imported):
    """Calculate graha Rashi ingresses and the full Mars-in-Cancer interval."""
    ist=ZoneInfo('Asia/Kolkata'); utc=ZoneInfo('UTC')
    lo=datetime(year,month,1); hi=lo+timedelta(days=monthrange(year,month)[1])
    scan_start=datetime(year,month,1,tzinfo=ist)-timedelta(days=60)
    scan_end=datetime(year,month,1,tzinfo=ist)+timedelta(days=monthrange(year,month)[1]+60)
    def jd_at(local):
        instant=local.astimezone(utc)
        return swe.julday(instant.year,instant.month,instant.day,
                          instant.hour+instant.minute/60+instant.second/3600+instant.microsecond/3_600_000_000)
    grahas=(('Sun',swe.SUN,0),('Moon',swe.MOON,0),('Mars',swe.MARS,0),
            ('Mercury',swe.MERCURY,0),('Jupiter',swe.JUPITER,0),('Venus',swe.VENUS,0),
            ('Saturn',swe.SATURN,0),('Rahu',swe.MEAN_NODE,0),('Ketu',swe.MEAN_NODE,180))
    mool_trikona={'Sun':4,'Moon':1,'Mars':0,'Mercury':5,'Jupiter':8,
                  'Venus':6,'Saturn':10,'Rahu':2,'Ketu':8}
    try:
        from lookup import load_tables, planet as lookup_planet
        sector_rows,_,_,_,_=load_tables()
        focus_sectors={name:sorted({row['Sector'] for row in sector_rows
            if name.upper() in [lookup_planet(row.get(f'Planet {i}')) for i in (1,2,3)]})
            for name,_,_ in grahas}
    except Exception:
        focus_sectors={name:[] for name,_,_ in grahas}
    def sign_at(jd,planet,offset):
        return int(((swe.calc_ut(jd,planet,FLAGS)[0][0]+offset)%360)//30)
    edges={name:[] for name,_,_ in grahas}
    start_jd=jd_at(scan_start); end_jd=jd_at(scan_end); step=0.5
    cursor=start_jd
    while cursor<end_jd:
        nxt=min(cursor+step,end_jd)
        for name,planet,offset in grahas:
            before=sign_at(cursor,planet,offset); after=sign_at(nxt,planet,offset)
            if before==after: continue
            left,right=cursor,nxt
            while (right-left)*86400>0.5:
                middle=(left+right)/2
                if sign_at(middle,planet,offset)==before: left=middle
                else: right=middle
            crossing=(left+right)/2
            old_sign=sign_at(crossing-2/86400,planet,offset)
            new_sign=sign_at(crossing+2/86400,planet,offset)
            if old_sign!=new_sign:
                edges[name].append((crossing,old_sign,new_sign))
        cursor=nxt

    source=SOURCE; observation_note=('Observation only: review the chart on the previous day and record the '
                                    'following NSE session gap up/down. No direction is predicted.')
    result=[]
    def imported_match(name,start):
        for row in imported:
            if row.get('event','').casefold()!=name.casefold(): continue
            try: existing=datetime.fromisoformat(row['start_ist'])
            except (KeyError,ValueError): continue
            if abs(existing-start)<=timedelta(hours=6): return row
        return None
    def transit_exit(name,planet,offset,crossing,target_sign):
        for jd,old_sign,new_sign in edges[name]:
            if jd>crossing and old_sign==target_sign and new_sign!=target_sign: return jd
        max_days={'Moon':35,'Sun':45,'Mars':180,'Mercury':180,'Jupiter':520,
                  'Venus':180,'Saturn':1250,'Rahu':760,'Ketu':760}.get(name,180)
        left=crossing; cursor=crossing+1/86400
        limit=crossing+max_days
        while cursor<limit:
            right=min(cursor+1,limit)
            if sign_at(right,planet,offset)!=target_sign:
                lo_jd,hi_jd=left,right
                while (hi_jd-lo_jd)*86400>0.5:
                    middle=(lo_jd+hi_jd)/2
                    if sign_at(middle,planet,offset)==target_sign: lo_jd=middle
                    else: hi_jd=middle
                return (lo_jd+hi_jd)/2
            left=right; cursor=right
        return None
    for name,planet,offset in grahas:
        for crossing,old_sign,new_sign in edges[name]:
            start=datetime.fromisoformat(stamp(crossing))
            if not lo<=start<hi: continue
            if name=='Mars' and new_sign==3: continue
            is_mool_trikona=mool_trikona.get(name)==new_sign
            base_event=f'{name} enters {RASHIS[new_sign]}'
            event=(f'{base_event} · Mool Trikona focus' if is_mool_trikona else base_event)
            existing=imported_match(event,start)
            if is_mool_trikona and not existing: existing=imported_match(base_event,start)
            exit_jd=transit_exit(name,planet,offset,crossing,new_sign) if is_mool_trikona else None
            end_ist=stamp(exit_jd) if exit_jd else ''
            note=(f'{name} Rashi change: {RASHIS[old_sign]} → {RASHIS[new_sign]}. '
                  f'{observation_note}')
            bias='Gap Up/Down · observation'
            if is_mool_trikona:
                sectors=focus_sectors.get(name,[])
                sector_text=', '.join(sectors) if sectors else ('No sectors listed for Ketu in Astro Lookup.' if name=='Ketu' else 'Check Astro Lookup → Sector Lookup for this planet.')
                bias='Mool Trikona · sector focus'
                note=(f'{name} enters its Mool Trikona / focus Rashi, {RASHIS[new_sign]}. '
                      f'Related sectors from Astro Lookup: {sector_text}. Focus on stocks in these sectors for chart review. '
                      f'{observation_note}')
            if existing:
                if not is_mool_trikona: continue
                existing.update({'event':event,'end_ist':end_ist or existing.get('end_ist',''),'bias':bias,'notes':note,
                    'status':'Published ingress; Mool Trikona focus mapping calculated'})
                continue
            result.append(dict(zip(HEAD,[stamp(crossing),end_ist,'Rashi Transit',event,
                bias,'P2','All',source,
                'Calculated Lahiri ingress · verify against Drik Panchang',note])))

    mars_edges=edges['Mars']
    for entry,old_sign,new_sign in mars_edges:
        if new_sign!=3: continue
        exit_edge=next(((jd,old,new) for jd,old,new in mars_edges if jd>entry and old==3 and new!=3),None)
        if not exit_edge: continue
        exit_jd=exit_edge[0]; start=datetime.fromisoformat(stamp(entry)); end=datetime.fromisoformat(stamp(exit_jd))
        if start>=hi or end<lo: continue
        event='Mars in Cancer (Mangal in Karka)'
        note=('Mars in Cancer interval. Review the chart on the previous day; record the following NSE session '
              'gap up/down. Observation only; no direction is predicted.')
        existing=imported_match('Mars enters Cancer',start)
        if existing:
            existing.update({'end_ist':stamp(exit_jd),'category':'Rashi Transit','event':event,
                'bias':'Gap Up/Down · observation','priority':'P1',
                'status':'Published ingress; calculated Lahiri exit · verify against Drik Panchang',
                'notes':note})
        else:
            result.append(dict(zip(HEAD,[stamp(entry),stamp(exit_jd),'Rashi Transit',event,
                'Gap Up/Down · observation','P1','All',source,
                'Calculated Lahiri interval · verify against Drik Panchang',note])))
    return result


def sun_ketu_leo_observation_events(year, month, imported):
    """Track the interval when Sun and Ketu are within one degree in Leo."""
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    ist=ZoneInfo('Asia/Kolkata'); utc=ZoneInfo('UTC')
    lo=datetime(year,month,1); hi=lo+timedelta(days=monthrange(year,month)[1])
    scan_start=datetime(year,month,1,tzinfo=ist)-timedelta(days=60)
    scan_end=datetime(year,month,1,tzinfo=ist)+timedelta(days=monthrange(year,month)[1]+60)

    def jd_at(local):
        instant=local.astimezone(utc)
        return swe.julday(instant.year,instant.month,instant.day,
                          instant.hour+instant.minute/60+instant.second/3600+instant.microsecond/3_600_000_000)

    def separation(jd):
        sun=swe.calc_ut(jd,swe.SUN,FLAGS)[0][0]
        rahu=swe.calc_ut(jd,swe.MEAN_NODE,FLAGS)[0][0]
        ketu=(rahu+180)%360
        gap=abs((sun-ketu+180)%360-180)
        return gap if int(sun//30)==4 and int(ketu//30)==4 and gap<=1 else None

    cursor=jd_at(scan_start); finish=jd_at(scan_end); step=0.25
    previous=separation(cursor) is not None
    window_start=cursor if previous else None
    intervals=[]
    while cursor<finish:
        nxt=min(cursor+step,finish); current=separation(nxt) is not None
        if current!=previous:
            left,right=cursor,nxt
            while (right-left)*86400>1:
                middle=(left+right)/2
                if (separation(middle) is not None)==previous: left=middle
                else: right=middle
            boundary=(left+right)/2
            if current: window_start=boundary
            elif window_start is not None:
                intervals.append((window_start,boundary)); window_start=None
            previous=current
        cursor=nxt
    if window_start is not None: intervals.append((window_start,finish))

    note=('Sun and Ketu are in Leo within 1°. Observation only: review the chart the previous day, '
          'then record whether the next NSE session moved mainly in one direction. No bullish or bearish '
          'direction is predicted.')
    result=[]
    for begin,end in intervals:
        start=datetime.fromisoformat(stamp(begin)); stop=datetime.fromisoformat(stamp(end))
        duplicate=False
        for row in imported:
            if row.get('event','').casefold()!='sun–ketu within 1° in leo': continue
            try: existing=datetime.fromisoformat(row['start_ist'])
            except (KeyError,ValueError): continue
            if abs(existing-start)<=timedelta(hours=6):
                duplicate=True; break
        if start>=hi or stop<lo or duplicate: continue
        result.append(dict(zip(HEAD,[stamp(begin),stamp(end),'Conjunction','Sun–Ketu within 1° in Leo',
            'One-direction move · observation','P1','All',SOURCE,
            'Calculated Lahiri interval · verify against Drik Panchang',note])))
    return result


def daily_astro_events(year, month, imported):
    """Calendar observations and daily yogas calculated in IST."""
    lo=datetime(year,month,1); hi=lo+timedelta(days=monthrange(year,month)[1]); result=[]
    result.extend(rashi_observation_events(year,month,imported))
    result.extend(sun_ketu_leo_observation_events(year,month,imported))
    gandamool_source='https://www.drikpanchang.com/panchang/gandamool-dates-timings.html'
    for item in events('gandamool'):
        start=datetime.fromisoformat(item['start_ist']); end=datetime.fromisoformat(item['end_ist'])
        if not (start<hi and end>=lo): continue
        duplicate=False
        for row in imported:
            if event_key(row['event'])!='gandamool': continue
            a=datetime.fromisoformat(row['start_ist']); b=datetime.fromisoformat(row['end_ist'] or row['start_ist'])
            if a<=end+timedelta(minutes=2) and b>=start-timedelta(minutes=2):
                duplicate=True; break
        if duplicate: continue
        result.append(dict(zip(HEAD,[item['start_ist'],item['end_ist'],'Gandamool','Gandamool','Research','P2','All',
            gandamool_source,item['status'],'Calculated Lahiri Moon interval; verify against Drik Panchang.'])))

    # User-supplied daily market hypotheses for Moon nakshatra transits.
    nakshatra_rules=(
        ('moon_in_moola','Moon in Moola','Bearish · Super bearish day',
         'User market hypothesis: super bearish day; occurs about once per month while Moon transits Moola.'),
        ('moon_in_pushya','Moon in Pushya','Reversal · check previous trend',
         'User hypothesis: Pushya is a potential reversal point; check the previous trend. It is not always bearish.'),
        ('moon_in_uttara_phalguni','Moon in Uttara Phalguni','Mostly Bullish day',
         'User market hypothesis: mostly bullish while Moon transits Uttara Phalguni.'),
        ('moon_in_jyeshtha','Moon in Jyeshtha','Mostly Bullish day',
         'User market hypothesis: mostly bullish for about one day while Moon transits Jyeshtha.'),
    )
    for key,name,bias,note in nakshatra_rules:
        for item in events(key):
            start=datetime.fromisoformat(item['start_ist']); end=datetime.fromisoformat(item['end_ist'])
            if not (start<hi and end>=lo): continue
            duplicate=any(event_key(row['event'])==key and
                          datetime.fromisoformat(row['start_ist'])<=end+timedelta(minutes=2) and
                          datetime.fromisoformat(row['end_ist'] or row['start_ist'])>=start-timedelta(minutes=2)
                          for row in imported)
            if duplicate: continue
            result.append(dict(zip(HEAD,[item['start_ist'],item['end_ist'],'Nakshatra Transit',name,bias,'P2','All',
                item['source'],'Calculated · verify source',note])))

    swe.set_sid_mode(swe.SIDM_LAHIRI)
    planets=(('Saturn',swe.SATURN,False),('Mars',swe.MARS,False),('Sun',swe.SUN,False),
             ('Rahu',swe.MEAN_NODE,False),('Ketu',swe.MEAN_NODE,True))
    ist=ZoneInfo('Asia/Kolkata'); utc=ZoneInfo('UTC')
    def jd_at(local):
        instant=local.astimezone(utc)
        return swe.julday(instant.year,instant.month,instant.day,
                          instant.hour+instant.minute/60+instant.second/3600+instant.microsecond/3_600_000_000)
    def state_at(local):
        jd=jd_at(local); moon_lon=swe.calc_ut(jd,swe.MOON,FLAGS)[0][0]
        malefics=[]
        for name,planet,is_ketu in planets:
            coords=swe.calc_ut(jd,planet,FLAGS)[0]; position=(coords[0]+(180 if is_ketu else 0))%360
            malefics.append({'name':name,'longitude':position,'retrograde':coords[3]<0})
        return classify_moon_papakartari(moon_lon,malefics)
    start=datetime.combine(lo.date(),time.min,ist); end=datetime.combine(hi.date(),time.min,ist)
    boundaries=[start]; cursor=start; prior=state_at(cursor)['signature']; step=timedelta(minutes=15)
    while cursor<end:
        nxt=min(cursor+step,end); current=state_at(nxt)['signature']
        if current!=prior:
            left,right=cursor,nxt
            while (right-left).total_seconds()>1:
                middle=left+(right-left)/2
                if state_at(middle)['signature']==prior: left=middle
                else: right=middle
            boundaries.append(right); prior=current
        cursor=nxt
    boundaries.append(end)
    for begin,finish in zip(boundaries,boundaries[1:]):
        state=state_at(begin+(finish-begin)/2)
        if not state['formed']: continue
        label='Bearish' if state['mixed_motion'] else 'Bullish'
        source='https://www.astro.com/swisseph/'
        notes=format_papakartari_summary(state)
        result.append(dict(zip(HEAD,[stamp(jd_at(begin)),stamp(jd_at(finish)),'Yoga','Moon Papa Kartari',label,
                                   'P2','All',source,'Calculated · verify source',notes])))
    return result
