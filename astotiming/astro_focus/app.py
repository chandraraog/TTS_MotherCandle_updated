from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import date, datetime, timedelta, time as wall_time
from zoneinfo import ZoneInfo
from pathlib import Path
from focus import ROOT, DATA, OUT, build, load, next_month
from timing_integration import fetch_day, line
from threading import Lock
from papakartari import classify_moon_papakartari, format_papakartari_summary
import re
import swisseph as swe
app=FastAPI(title='Astro Focus V4.7.3')
lock=Lock()
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
def month_parts(month):
    if not re.fullmatch(r'\d{4}-\d{2}',month): raise HTTPException(400,'Use YYYY-MM')
    try: d=date.fromisoformat(month+'-01')
    except ValueError: raise HTTPException(400,'Invalid month')
    return d.year,d.month
@app.get('/')
def home(): return FileResponse(ROOT/'static/index.html')
@app.get('/api/months')
def months():
    today=datetime.now(ZoneInfo('Asia/Kolkata')).date(); y,m=next_month(today.year,today.month)
    return {'today':today.isoformat(),'months':[f'{year}-{month:02}' for year in range(2000,2051) for month in range(1,13)]}
@app.get('/api/calendar/{month}')
def calendar(month:str):
    y,m=month_parts(month)
    with lock:
        version=OUT/f'daily_events_{month}.version'
        if (not (OUT/f'positional_{month}.csv').exists() or not version.exists()
                or version.read_text(encoding='utf-8').strip()!='10'):
            build(y,m)
        return {'focus':load(OUT/f'focus_{month}.csv'),'positional':load(OUT/f'positional_{month}.csv'),'events':load(OUT/f'events_{month}.csv'),'rules':load(ROOT/'rules.csv'),'coverage':load(ROOT/'coverage.csv'),'research_available':{r['event']:availability(r['event']) for r in load(OUT/f'events_{month}.csv')}}
@app.post('/api/calendar/{month}')
def generate(month:str, include_calculated:bool=False):
    y,m=month_parts(month)
    with lock: build(y,m,include_calculated=include_calculated)
    return calendar(month)
@app.get('/api/excel/{month}')
def excel(month:str):
    calendar(month)
    return FileResponse(OUT/f'Astro_Focus_{month}.xlsx',filename=f'Astro_Focus_{month}.xlsx')
@app.get('/api/timings/{day}')
def timings(day:date):
    try: rows=fetch_day(day)
    except Exception as e: raise HTTPException(502,f'Prokerala fetch failed: {e}')
    # D1/Rashi whole-sign houses for the NSE reference location. The provider's
    # event timezone is not verified, so its displayed clock is treated as IST.
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    planets={'sun':swe.SUN,'moon':swe.MOON,'mars':swe.MARS,'mercury':swe.MERCURY,
             'jupiter':swe.JUPITER,'venus':swe.VENUS,'saturn':swe.SATURN,
             'uranus':swe.URANUS,'neptune':swe.NEPTUNE,'pluto':swe.PLUTO,
             'node':swe.MEAN_NODE,'rahu':swe.MEAN_NODE}
    rashis=('Aries','Taurus','Gemini','Cancer','Leo','Virgo','Libra','Scorpio',
            'Sagittarius','Capricorn','Aquarius','Pisces')
    rashi_lords=('Mars','Venus','Mercury','Moon','Sun','Mercury','Venus','Mars',
                 'Jupiter','Saturn','Saturn','Jupiter')
    papa_planets=(('Saturn',swe.SATURN,False),('Mars',swe.MARS,False),
                  ('Sun',swe.SUN,False),('Rahu',swe.MEAN_NODE,False),
                  ('Ketu',swe.MEAN_NODE,True))
    nakshatras=('Ashwini','Bharani','Krittika','Rohini','Mrigashira','Ardra','Punarvasu',
                'Pushya','Ashlesha','Magha','Purva Phalguni','Uttara Phalguni','Hasta',
                'Chitra','Swati','Vishakha','Anuradha','Jyeshtha','Moola','Purva Ashadha',
                'Uttara Ashadha','Shravana','Dhanishta','Shatabhisha','Purva Bhadrapada',
                'Uttara Bhadrapada','Revati')
    gandamool_indices={0,8,9,17,18,26}
    conjunction_views={'sun':'Bullish','mercury':'Sudden bounce','jupiter':'Bearish',
                       'venus':'Bearish','saturn':'Bullish','uranus':'Bullish',
                       'neptune':'Bearish','pluto':'Bullish','mars':'Bullish',
                       'rahu':'Bullish','ketu':'Bearish','node':'Bullish'}
    def jd_at(local):
        utc=local.astimezone(ZoneInfo('UTC'))
        return swe.julday(utc.year,utc.month,utc.day,utc.hour+utc.minute/60+utc.second/3600+utc.microsecond/3_600_000_000)
    def longitude(jd,planet_id,offset=0):
        return (swe.calc_ut(jd,planet_id,swe.FLG_MOSEPH|swe.FLG_SIDEREAL)[0][0]+offset)%360
    def sign_name(longitude_value):
        index=int(longitude_value//30)
        return index,rashis[index]
    def papakartari_at(jd):
        moon_lon=longitude(jd,swe.MOON)
        malefics=[]
        for planet_name,planet_id,is_ketu in papa_planets:
            coords=swe.calc_ut(jd,planet_id,swe.FLG_MOSEPH|swe.FLG_SIDEREAL|swe.FLG_SPEED)[0]
            malefics.append({'name':planet_name,'longitude':(coords[0]+(180 if is_ketu else 0))%360,
                             'retrograde':coords[3]<0})
        state=classify_moon_papakartari(moon_lon,malefics)
        formed=state['formed']; mixed_motion=state['mixed_motion']; moon_sign=state['moon_sign']
        view=('Bearish · usual bullish effect reversed' if mixed_motion else 'Bullish') if formed else '—'
        banknifty=('Bank Nifty · stronger bullish bias' if formed and not mixed_motion
                   else 'Bank Nifty · bearish reversal' if formed else '—')
        note=format_papakartari_summary(state) if formed else 'No qualifying degree/sign flanks'
        return {'formed':formed,'mixed_motion':mixed_motion,'view':view,'banknifty':banknifty,
                'note':note,'moon_sign':moon_sign,'moon_degree':state['moon_degree'],
                'planets':state['planets'],'signature':state['signature']}
    def market_d9_windows():
        if day.weekday()>=5: return []
        start=datetime.combine(day,wall_time(9,15),ZoneInfo('Asia/Kolkata'))
        end=datetime.combine(day,wall_time(15,30),ZoneInfo('Asia/Kolkata'))
        navamsa_sign=lambda instant:int(longitude(jd_at(instant),swe.MOON)/(10/3))%12
        boundaries=[start]
        cursor=start; prior=navamsa_sign(cursor); step=timedelta(minutes=5)
        while cursor<end:
            nxt=min(cursor+step,end); current=navamsa_sign(nxt)
            if current!=prior:
                lo,hi=cursor,nxt
                while (hi-lo).total_seconds()>1:
                    mid=lo+(hi-lo)/2
                    if navamsa_sign(mid)==prior: lo=mid
                    else: hi=mid
                boundaries.append(hi); prior=current
            cursor=nxt
        boundaries.append(end); windows=[]
        for begin,finish in zip(boundaries,boundaries[1:]):
            if finish<=begin: continue
            sample=begin+(finish-begin)/2; jd=jd_at(sample)
            moon=longitude(jd,swe.MOON); d1_sign,d1_name=sign_name(moon)
            d9_sign=int(moon/(10/3))%12; d9_name=rashis[d9_sign]
            readings=[]
            if d9_sign==1: readings.append('Small bullish move · Moon exalted')
            elif d9_sign==3: readings.append('Small bullish move · Moon own sign')
            if d9_sign==d1_sign: readings.append('Vargottama · Bullish')
            d9_pair_names={'sun':'Sun','mercury':'Mercury','jupiter':'Jupiter','venus':'Venus',
                            'saturn':'Saturn','uranus':'Uranus','neptune':'Neptune','pluto':'Pluto',
                            'mars':'Mars','rahu':'Rahu','ketu':'Ketu'}
            d9_pairs=[]
            for planet_name,planet_label in d9_pair_names.items():
                planet_id=swe.MEAN_NODE if planet_name in ('rahu','ketu') else planets[planet_name]
                planet_lon=longitude(jd,planet_id,180 if planet_name=='ketu' else 0)
                if int(planet_lon/(10/3))%12==d9_sign:
                    view=conjunction_views.get(planet_name)
                    if view: d9_pairs.append(f'Moon–{planet_label} · {view}')
            if d9_pairs: readings.extend('D9 '+item for item in d9_pairs)
            windows.append({'start':begin.strftime('%H:%M:%S'),'end':finish.strftime('%H:%M:%S'),
                            'd1_rashi':d1_name,'d9_rashi':d9_name,
                            'view':' · '.join(readings) if readings else 'No supplied directional rule'})
        return windows
    def papakartari_events():
        start=datetime.combine(day,wall_time(0,0),ZoneInfo('Asia/Kolkata'))
        end=start+timedelta(days=1)
        signature_at=lambda instant:papakartari_at(jd_at(instant))['signature']
        boundaries=[start]; cursor=start; prior=signature_at(cursor); step=timedelta(minutes=5)
        while cursor<end:
            nxt=min(cursor+step,end); current=signature_at(nxt)
            if current!=prior:
                lo,hi=cursor,nxt
                while (hi-lo).total_seconds()>1:
                    mid=lo+(hi-lo)/2
                    if signature_at(mid)==prior: lo=mid
                    else: hi=mid
                boundaries.append(hi); prior=current
            cursor=nxt
        boundaries.append(end); events=[]
        for begin,finish in zip(boundaries,boundaries[1:]):
            if finish<=begin: continue
            state=papakartari_at(jd_at(begin+(finish-begin)/2))
            if not state['formed']: continue
            view='Bearish' if state['mixed_motion'] else 'Bullish'
            planet_summary=', '.join(f"{p['name']} {p['degree']:.1f}° {rashis[p['sign']]} ({'retrograde' if p['retrograde'] else 'direct'})"
                                      for p in state['planets'])
            events.append({'event_type':'yoga','date':day.isoformat(),
                'time':begin.strftime('%H:%M:%S')+'–'+finish.strftime('%H:%M:%S'),
                'planet1':'Moon','planet2':'Papakartari','aspect':'Papakartari Yoga','degree':'—',
                'bias':view,'moon_rashi_number':state['moon_sign']+1,
                'moon_rashi':rashis[state['moon_sign']],
                'papakartari_planets':'Flanking malefics: '+planet_summary,
                'moon_papakartari':True,'moon_papakartari_view':state['view'],
                'moon_papakartari_banknifty':state['banknifty'],
                'moon_papakartari_note':state['note'],
                'astro_rule_signals':['Papa Kartari Yoga active · '+state['view']]})
        return events
    for row in rows:
        try:
            event_date=date.fromisoformat(row['date'])
            event_clock=row['time'].strip().upper()
            try: clock=datetime.strptime(event_clock,'%H:%M:%S').time()
            except ValueError: clock=datetime.strptime(event_clock,'%H:%M').time()
            local=datetime.combine(event_date,clock).replace(tzinfo=ZoneInfo('Asia/Kolkata'))
            jd=jd_at(local)
            _, angles=swe.houses_ex(jd,19.0760,72.8777,b'W',swe.FLG_SIDEREAL)
            asc_sign=int(angles[0]//30)
            def position(name):
                key=name.strip().lower()
                if key=='ketu': planet_longitude=longitude(jd,swe.MEAN_NODE,180)
                elif key in planets: planet_longitude=longitude(jd,planets[key])
                else: return ('Not in 9-planet chart','—','—','—')
                sign=int(planet_longitude//30)
                return (f'House {(sign-asc_sign)%12+1}',sign+1,rashis[sign],rashi_lords[sign])
            for prefix, name in (('planet1',row['planet1']),('planet2',row['planet2'])):
                row[f'{prefix}_house'],row[f'{prefix}_rashi_number'],row[f'{prefix}_rashi'],row[f'{prefix}_rashi_lord']=position(name)
            calculated={name:longitude(jd,pid) for name,pid in planets.items() if name not in ('node','rahu')}
            calculated['rahu']=longitude(jd,swe.MEAN_NODE); calculated['ketu']=(calculated['rahu']+180)%360
            moon_lon=calculated['moon']
            papakartari=papakartari_at(jd)
            moon_sign=papakartari['moon_sign']
            formed=papakartari['formed']
            row['moon_papakartari']=formed
            row['moon_papakartari_view']=papakartari['view']
            row['moon_papakartari_banknifty']=papakartari['banknifty']
            row['moon_papakartari_note']=papakartari['note']
            moon_d9=int(moon_lon/(10/3))%12
            row['moon_d9_rashi_number']=moon_d9+1; row['moon_d9_rashi']=rashis[moon_d9]
            row['moon_vargottama']=moon_d9==moon_sign
            sun_lon=calculated['sun']; elongation=(moon_lon-sun_lon)%360
            signals=[]
            nakshatra_index=int(moon_lon/(360/27)); nakshatra=nakshatras[nakshatra_index]
            row['moon_nakshatra']=nakshatra
            if nakshatra_index in gandamool_indices: signals.append(f'Gandamool · {nakshatra} · Research')
            mars=calculated['mars']; rahu=calculated['rahu']; mars_rahu_gap=abs((mars-rahu+180)%360-180)
            row['mars_rahu_gap_deg']=round(mars_rahu_gap,3)
            if int(mars//30)==int(rahu//30) and mars_rahu_gap<=3:
                signals.append(f'Mars–Rahu close conjunction · {mars_rahu_gap:.2f}° · Explosive volatility')
            sun_ketu_gap=abs((calculated['sun']-calculated['ketu']+180)%360-180)
            if int(calculated['sun']//30)==4 and int(calculated['ketu']//30)==4 and sun_ketu_gap<=1:
                signals.append(f'Sun–Ketu within 1° in Leo · {sun_ketu_gap:.2f}° · One-direction move observation')
            row['moon_solar_elongation_deg']=round(elongation,3)
            if elongation>=180: signals.append('Post-Purnima · Reversal watch')
            if moon_d9 in (1,3):
                sign_label='Taurus · exalted' if moon_d9==1 else 'Cancer · own sign'
                signals.append(f'Moon D9 {sign_label} · Strong small bullish move')
            if moon_d9==moon_sign: signals.append(f'Moon Vargottama · {rashis[moon_sign]} · Bullish')
            d9_planet_ids={'sun':swe.SUN,'mercury':swe.MERCURY,'jupiter':swe.JUPITER,
                           'venus':swe.VENUS,'saturn':swe.SATURN,'uranus':swe.URANUS,
                           'neptune':swe.NEPTUNE,'pluto':swe.PLUTO,'mars':swe.MARS,
                           'rahu':swe.MEAN_NODE,'ketu':swe.MEAN_NODE}
            for name,planet_id in d9_planet_ids.items():
                planet_lon=longitude(jd,planet_id,180 if name=='ketu' else 0)
                if int(planet_lon/(10/3))%12==moon_d9:
                    mapped_view=conjunction_views.get(name)
                    if mapped_view: signals.append(f'D9 Moon–{name.title()} conjunction · {mapped_view}')
            pair={row['planet1'].strip().lower(),row['planet2'].strip().lower()}
            aspect=row['aspect'].strip().lower()
            if 'conj' in aspect and 'moon' in pair:
                other=next((name for name in pair if name!='moon'),'')
                other={'north node':'rahu','south node':'ketu','mean node':'node'}.get(other,other)
                view=conjunction_views.get(other)
                if view: signals.append(f'Moon–{other.title()} conjunction · {view}')
            row['astro_rule_signals']=signals
            row['astro_rule_note']='; '.join(signals) if signals else 'No supplied rule triggered at this timestamp'
        except (KeyError,ValueError,TypeError):
            for prefix in ('planet1','planet2'):
                row[f'{prefix}_house']=row[f'{prefix}_rashi_number']=row[f'{prefix}_rashi']=row[f'{prefix}_rashi_lord']='Unavailable'
            row['moon_papakartari']=False
            row['moon_papakartari_view']='Unavailable'
            row['moon_papakartari_banknifty']='—'
            row['moon_papakartari_note']='Planetary positions unavailable'
            row['astro_rule_signals']=[]; row['astro_rule_note']='Planetary positions unavailable'
    rows.extend(papakartari_events())
    rows.sort(key=lambda item:(item.get('date',''),item.get('time','')))
    return {'rows':rows,'moon_d9_market_windows':market_d9_windows(),
            'text':'\n'.join(map(line,rows)),'timezone':'Mumbai · D1 whole-sign · D9/Navamsa · Lahiri/Chitra Paksha · mean Rahu/Ketu · listed event time assumed IST'}

@app.get('/api/lookup')
def lookup_data():
    try:
        from lookup import load_tables, planet
        sectors,d9,rashi,keys,aliases=load_tables()
        clean=lambda rows:[{str(k):v for k,v in row.items() if k is not None} for row in rows]
        planets=sorted({planet(row.get(f'Planet {i}')) for row in sectors for i in (1,2,3)}-{''})
        return {'sectors':clean(sectors),'d9':clean(d9),'rashi':clean(rashi),'d9_keys':keys,
                'rashi_aliases':aliases,'sector_planets':planets}
    except Exception as exc:
        raise HTTPException(500,f'Cannot read Combined_Lookup.xlsx: {exc}')

@app.get('/api/lookup/workbook')
def lookup_workbook():
    from lookup import WORKBOOK
    if not WORKBOOK.exists(): raise HTTPException(404,'Combined_Lookup.xlsx not found.')
    return FileResponse(WORKBOOK,filename=WORKBOOK.name)

from research.engine import analyze, catalog, refresh, MARKETS, availability, default_count, event_key
@app.get('/api/research/catalog')
def research_catalog(): return {'events':catalog(),'markets':list(MARKETS),'defaults':{n:default_count(n) for n in catalog()},'aliases':{r['event']:event_key(r['event']) for month in ('2026-11','2026-12') for r in load(OUT/f'events_{month}.csv')},'available':{event_key(n):availability(n) for n in catalog()}}
@app.get('/api/research')
def research(event:str, market:str='NIFTY 50', count:int=10, mode:str='post'):
    try: return analyze(event,market,count,mode)
    except (ValueError, KeyError) as e: raise HTTPException(400,str(e))
@app.post('/api/research/fetch')
def research_fetch(event:str,market:str='NIFTY 50',count:int=10):
    try:
        if count not in (5,10,20,30,50,100): raise ValueError('Invalid count')
        with lock: return {'downloaded':refresh(event,market,count)}
    except Exception as e: raise HTTPException(502,str(e))
