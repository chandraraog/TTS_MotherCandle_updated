import streamlit as st
import pandas as pd
from .engine import analyze, catalog, MARKETS, refresh, default_count, NAKSHATRA, event_key
from .bootstrap import run

def panel(prefix, selected=None):
    names=catalog()
    if selected is None:
        name=st.selectbox('Event',names,key=prefix+'event')
    else:
        name=selected
        st.markdown(f'**{name}**')
    a,b,c=st.columns([2,1,2])
    market=a.selectbox('Instrument',list(MARKETS),key=prefix+'market')
    default=default_count(name)
    count=b.selectbox('Instances',[default]+[n for n in (5,10,20,30,50,100) if n!=default],key=prefix+'count_'+name)
    mode=c.selectbox('Session mapping',['post','event'],format_func=lambda x:'First relevant session' if x=='post' else 'Calendar event day',key=prefix+'mode')
    if event_key(name) in NAKSHATRA:
        st.caption('Nakshatra studies use the first overlapping regular session; intervals outside trading hours are excluded.')
    elif name in ('Krishna Trayodashi','Shukla Trayodashi','Purnima','Amavasya'):
        st.caption('Lunar studies use the first overlapping regular session in both modes; non-overlapping Tithis are excluded.')
    initial=analyze(name,market,count,mode)
    if not initial['rows'] and st.button('Download shared market history (all events)',key=prefix+'fetch'):
        try:
            with st.spinner('Downloading historical OHLC and analyzing all events…'): run(market, download=True)
            st.rerun()
        except Exception as e: st.error(str(e))
    try: r=analyze(name,market,count,mode)
    except Exception as e: st.error(str(e)); return
    st.caption(f'{r["available_events"]} event dates available / {count} requested · {len(r["rows"])} matched market observations · {r["status"]}')
    s=r['stats']
    if s:
        a,b,c,d=st.columns(4); a.metric('Gap-up',f'{s["gap_up"]}/{s["observations"]}'); b.metric('Positive close',f'{s["positive_close"]}/{s["observations"]}'); c.metric('Bullish candle',f'{s["bullish_candle"]}/{s["observations"]}'); d.metric('Median return',f'{s["median_return"]:+.3f}%')
    st.write(r['summary'])
    if s.get('duration_groups'): st.dataframe(pd.DataFrame(s['duration_groups']).T,width='stretch')
    with st.expander('Show more — historical observations'):
        if r['rows']:
            df=pd.DataFrame(r['rows']); st.dataframe(df,width='stretch',hide_index=True); st.bar_chart(df.set_index('session')['return_pct']); st.download_button('Export history CSV',df.to_csv(index=False),file_name='event_history.csv',key=prefix+'export')
        if r['missing']: st.dataframe(r['missing'],width='stretch',hide_index=True)
        st.caption('Daily candles cannot isolate price action after an intraday aspect. Post mode maps after-close/weekend events to the next available session; interval events use first overlapping session. Import continuous daily OHLC to avoid missing-session bias.')
