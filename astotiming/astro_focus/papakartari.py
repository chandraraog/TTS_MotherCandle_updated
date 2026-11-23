"""Degree-aware Moon Papa Kartari classification for the live calendar."""

RASHIS = ('Aries', 'Taurus', 'Gemini', 'Cancer', 'Leo', 'Virgo', 'Libra', 'Scorpio',
          'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces')


def format_papakartari_summary(state):
    """Build a short, readable three-Rashi explanation for the UI tooltip."""
    moon_sign = state['moon_sign']
    previous_sign, next_sign = (moon_sign - 1) % 12, (moon_sign + 1) % 12
    positions = state.get('positions', state['planets'])

    def rashi_line(label, sign):
        items = [f"{p['name']} {p['degree']:.1f}° {'Retrograde' if p['retrograde'] else 'Direct'}"
                 for p in positions if p['sign'] == sign]
        if sign == moon_sign:
            items.append(f"Moon {state['moon_degree']:.1f}°")
        return f"{label} · {RASHIS[sign]}: {', '.join(items) if items else '—'}"

    involved = state['planets']
    motions = ' + '.join(f"{p['name']} {'Retrograde' if p['retrograde'] else 'Direct'}"
                         for p in involved)
    if state['mixed_motion']:
        motion_result = f"{motions} → Mixed → Bearish (usual bullish effect reverses)"
    elif involved and all(p['retrograde'] for p in involved):
        motion_result = 'All Retrograde → Bullish'
    else:
        motion_result = 'All Direct → Bullish'
    view = 'Bearish' if state['mixed_motion'] else 'Bullish'
    return '\n'.join((f"Moon Papa Kartari · {view}",
                      rashi_line('Previous Rashi', previous_sign),
                      rashi_line('Moon Rashi', moon_sign),
                      rashi_line('Next Rashi', next_sign),
                      f"Motion: {motion_result}"))


def classify_moon_papakartari(moon_longitude, malefics):
    """Classify sign flanks, same-sign degree flanks, and the user's mixed-house case.

    Each malefic is a mapping with name, longitude, and retrograde fields. The
    adjacent-sign rule remains; degree positions also detect same-sign hemming
    and a malefic in the previous/next sign paired with one in the Moon's sign.
    """
    moon_longitude %= 360
    moon_sign = int(moon_longitude // 30)
    moon_degree = moon_longitude % 30
    previous_sign, next_sign = (moon_sign - 1) % 12, (moon_sign + 1) % 12
    planets = []
    for item in malefics:
        longitude = item['longitude'] % 360
        planets.append({
            'name': item['name'],
            'sign': int(longitude // 30),
            'degree': longitude % 30,
            'retrograde': bool(item['retrograde']),
        })

    previous = [p for p in planets if p['sign'] == previous_sign]
    current = [p for p in planets if p['sign'] == moon_sign]
    following = [p for p in planets if p['sign'] == next_sign]
    before = [p for p in current if p['degree'] < moon_degree]
    after = [p for p in current if p['degree'] > moon_degree]

    formations = []
    involved = []
    if previous and following:
        formations.append('malefics in both adjacent Rashis')
        involved.extend(previous + following)
    if before and after:
        formations.append('malefics on both degree sides in the Moon’s Rashi')
        involved.extend(before + after)
    if previous and (before or after):
        formations.append('malefics in the previous and Moon Rashis; same-Rashi degree checked')
        involved.extend(previous + before + after)
    if following and (before or after):
        formations.append('malefics in the Moon and next Rashis; same-Rashi degree checked')
        involved.extend(following + before + after)

    unique = {(p['name'], p['sign'], p['degree'], p['retrograde']): p for p in involved}
    involved = sorted(unique.values(), key=lambda p: (p['sign'], p['degree'], p['name']))
    formed = bool(formations)
    signature = (formed, tuple(sorted((p['name'], p['sign'], p['retrograde']) for p in involved)),
                 tuple(formations))
    return {
        'formed': formed,
        'mixed_motion': len({p['retrograde'] for p in involved}) > 1,
        'moon_sign': moon_sign,
        'moon_degree': moon_degree,
        'formation': '; '.join(formations),
        'planets': involved,
        'positions': sorted(planets, key=lambda p: (p['sign'], p['degree'], p['name'])),
        'signature': signature,
    }
