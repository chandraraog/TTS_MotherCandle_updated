"""User-supplied astrology/market inference notes. Hypotheses only; not validated signals."""
from datetime import datetime

RULES = [
    # Index / general market
    ('Amavasya on Tuesday', 'Index', 'Bullish', 'Amavasya occurs on Tuesday', 'Amavasya weekday rule'),
    ('Ekadashi on Friday', 'Index', 'Bearish', 'Ekadashi occurs on Friday', 'Ekadashi weekday rule'),
    ('Mars Retrograde', 'Index; Gold', 'Bullish', 'Mars is retrograde', 'Mars retrograde'),
    ('Jupiter Retrograde', 'Index', 'Bullish', 'Jupiter is retrograde', 'Jupiter retrograde'),
    ('Mercury Retrograde', 'Index', 'Bearish', 'Mercury is retrograde', 'Mercury retrograde'),
    ('Saturn Retrograde', 'Index', 'Bearish', 'Saturn is retrograde', 'Saturn retrograde'),
    ('Jupiter in Pisces or Sagittarius', 'Index', 'Bullish', 'Jupiter transits Pisces or Sagittarius', 'Jupiter sign transit'),
    ('Venus Retrograde', 'Index', 'Big top or big bottom possible', 'Venus is retrograde', 'Potential turning point'),
    ('Uranus–Venus conjunction', 'Index', 'Big top or big bottom possible', 'Uranus and Venus conjunct', 'Potential turning point'),
    ('Saturn + Mars both retrograde', 'Index', 'Bullish', 'Saturn and Mars retrograde periods overlap', 'Combined retrograde condition'),
    ('Saturn + Mercury both retrograde', 'Index', 'Bullish', 'Saturn and Mercury retrograde periods overlap', 'Combined retrograde condition'),
    ('Saturn + Venus both retrograde', 'Index', 'High Bullish', 'Saturn and Venus retrograde periods overlap', 'Combined retrograde condition'),
    ('Saturn + Venus + Mercury retrograde', 'Index', 'Extreme Bullish', 'All three retrograde periods overlap', 'Combined retrograde condition'),
    ('Saturn + Mars + Mercury + Jupiter retrograde', 'Index', 'Extreme Bull Bull', 'All four retrograde periods overlap', 'Combined retrograde condition'),
    # Gold
    ('Lunar eclipse on Wednesday', 'Gold', 'Major bullish', 'Lunar eclipse occurs on Wednesday', 'Gold-specific hypothesis'),
    ('Jupiter in Aries', 'Gold', 'Bearish', 'Jupiter transits Aries', 'Gold-specific hypothesis'),
    ('Saturn or Mercury in Gemini', 'Gold', 'Bearish', 'Saturn or Mercury transits Gemini', 'Gold-specific hypothesis'),
    # Silver
    ('Venus + Mars same sign', 'Silver', 'Bullish', 'Venus and Mars occupy the same sign', 'Silver-specific hypothesis'),
    ('Sun + Mars same sign', 'Silver', 'Bullish', 'Sun and Mars occupy the same sign', 'Silver-specific hypothesis'),
    ('Mars in Aquarius', 'Silver', 'Bullish', 'Mars transits Aquarius', 'Silver-specific hypothesis'),
    ('Saturn in Aries or Aquarius', 'Silver', 'Bullish', 'Saturn transits Aries or Aquarius', 'Silver-specific hypothesis'),
    ('Venus in Leo or Libra', 'Silver', 'Bearish', 'Venus transits Leo or Libra', 'Silver-specific hypothesis'),
    ('Mercury in Aries or Virgo', 'Silver', 'Bearish', 'Mercury transits Aries or Virgo', 'Silver-specific hypothesis'),
    ('Amavasya on Monday', 'Silver', 'Bearish', 'Amavasya occurs on Monday', 'Silver-specific hypothesis'),
    ('Saturn turns direct', 'Silver', 'Bearish', 'Saturn changes from retrograde to direct', 'Silver-specific hypothesis'),
    ('Mercury + Venus in Pisces', 'Silver', 'Bearish', 'Mercury and Venus both transit Pisces', 'Silver-specific hypothesis'),
]

def _date(row):
    try: return datetime.fromisoformat(str(row.get('start_ist','')).replace('Z','+00:00')).date()
    except Exception: return None

def _txt(row): return ' '.join(str(row.get(k,'')) for k in ('event','category','notes','source')).lower()

def _overlap(a,b):
    try:
        a0=datetime.fromisoformat(str(a['start_ist']).replace('Z','+00:00'))
        b0=datetime.fromisoformat(str(b['start_ist']).replace('Z','+00:00'))
        ae=datetime.fromisoformat(str(a.get('end_ist') or a['start_ist']).replace('Z','+00:00'))
        be=datetime.fromisoformat(str(b.get('end_ist') or b['start_ist']).replace('Z','+00:00'))
        return a0 <= be and b0 <= ae
    except Exception: return False

def _planet_state(rows, planet, state):
    p=planet.lower(); s=state.lower()
    return [r for r in rows if p in _txt(r) and s in _txt(r)]

def _has_sign(text, signs): return any(s in text for s in signs)

def inferences_for(row, all_rows=None):
    """Return only directly matched user rules; no inferred planetary positions are fabricated."""
    all_rows=all_rows or [row]
    text=_txt(row); day=_date(row); matches=[]
    def add(label,scope,bias,condition,detail):
        matches.append({'rule':label,'scope':scope,'bias':bias,'condition':condition,'detail':detail})
    event=text
    # Calendar weekday rules
    if day and 'amavasya' in event and day.weekday()==1: add('Amavasya on Tuesday','Index','Bullish','Tuesday','User-supplied weekday hypothesis')
    if day and ('ekadashi' in event or 'ekadasi' in event) and day.weekday()==4: add('Ekadashi on Friday','Index','Bearish','Friday','User-supplied weekday hypothesis')
    if day and 'amavasya' in event and day.weekday()==0: add('Amavasya on Monday','Silver','Bearish','Monday','User-supplied weekday hypothesis')
    # Individual retrogrades/transits/conjunctions only when present in event metadata.
    for planet,bias in [('mars','Bullish'),('jupiter','Bullish'),('mercury','Bearish'),('saturn','Bearish')]:
        if planet in event and 'retrograde' in event: add(planet.title()+' Retrograde','Index',bias,'Retrograde','User-supplied hypothesis')
    if 'venus' in event and 'retrograde' in event: add('Venus Retrograde','Index','Big top or big bottom possible','Retrograde','Potential turning point hypothesis')
    if 'jupiter' in event and _has_sign(event,['pisces','sagittarius']): add('Jupiter in Pisces or Sagittarius','Index','Bullish','Jupiter in Pisces / Sagittarius','User-supplied transit hypothesis')
    if 'uranus' in event and 'venus' in event and ('conjunction' in event or 'conjunct' in event): add('Uranus–Venus conjunction','Index','Big top or big bottom possible','Conjunction','Potential turning point hypothesis')
    if 'eclipse' in event and 'lunar' in event and day and day.weekday()==2: add('Lunar eclipse on Wednesday','Gold','Major bullish','Wednesday','Gold-specific user hypothesis')
    if 'jupiter' in event and 'aries' in event: add('Jupiter in Aries','Gold','Bearish','Jupiter in Aries','Gold-specific user hypothesis')
    if ('saturn' in event or 'mercury' in event) and 'gemini' in event: add('Saturn or Mercury in Gemini','Gold','Bearish','Gemini transit','Gold-specific user hypothesis')
    if 'mars' in event and 'aquarius' in event: add('Mars in Aquarius','Silver','Bullish','Mars in Aquarius','Silver-specific user hypothesis')
    if 'saturn' in event and _has_sign(event,['aries','aquarius']): add('Saturn in Aries or Aquarius','Silver','Bullish','Aries / Aquarius','Silver-specific user hypothesis')
    if 'venus' in event and _has_sign(event,['leo','libra']): add('Venus in Leo or Libra','Silver','Bearish','Leo / Libra','Silver-specific user hypothesis')
    if 'mercury' in event and _has_sign(event,['aries','virgo']): add('Mercury in Aries or Virgo','Silver','Bearish','Aries / Virgo','Silver-specific user hypothesis')
    if 'saturn' in event and ('direct' in event or 'station direct' in event): add('Saturn turns direct','Silver','Bearish','Saturn direct','Silver-specific user hypothesis')
    if 'venus' in event and 'mercury' in event and 'pisces' in event: add('Mercury + Venus in Pisces','Silver','Bearish','Both in Pisces','Silver-specific user hypothesis')
    # Simultaneous retrograde combinations require explicit overlapping event intervals.
    retro={p:_planet_state(all_rows,p,'retrograde') for p in ('saturn','mars','venus','mercury','jupiter')}
    combos=[(('saturn','mars'),'Saturn + Mars both retrograde','Bullish'),(('saturn','mercury'),'Saturn + Mercury both retrograde','Bullish'),(('saturn','venus'),'Saturn + Venus both retrograde','High Bullish'),(('saturn','venus','mercury'),'Saturn + Venus + Mercury retrograde','Extreme Bullish'),(('saturn','mars','mercury','jupiter'),'Saturn + Mars + Mercury + Jupiter retrograde','Extreme Bull Bull')]
    for planets,label,bias in combos:
        lists=[retro[p] for p in planets]
        if all(lists) and any(all(_overlap(x[0],x[i]) for i in range(1,len(x))) for x in __import__('itertools').product(*lists)):
            if any(_overlap(row,x) for x in lists[0]): add(label,'Index',bias,'Overlapping retrograde periods','Combined user-supplied hypothesis')
    # Same-sign combinations only when the event record itself explicitly identifies both planets and same sign.
    if 'venus' in event and 'mars' in event and ('same sign' in event or 'conjunction' in event): add('Venus + Mars same sign','Silver','Bullish','Same sign','Silver-specific user hypothesis')
    if 'sun' in event and 'mars' in event and ('same sign' in event or 'conjunction' in event): add('Sun + Mars same sign','Silver','Bullish','Same sign','Silver-specific user hypothesis')
    return matches

def decorate(items, all_rows=None):
    out=[]
    for row in items:
        matches=inferences_for(row,all_rows)
        item=dict(row)
        item['I']='ⓘ' if matches else ''
        item['_inference_tooltip']='\n'.join(f"{m['scope']}: {m['rule']} — {m['bias']}\nCondition: {m['condition']}" for m in matches)
        out.append(item)
    return out
