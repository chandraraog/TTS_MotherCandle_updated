import streamlit as st
from focus import ROOT, OUT, DATA, build, load
from today_highlight import styled_events
import hashlib
from timing_integration import fetch_day, line, FIELDS
from datetime import datetime, date
import calendar, csv, io
from zoneinfo import ZoneInfo
st.set_page_config(page_title='Astro Focus V4.7.13',layout='wide')
st.markdown("""
<style>
/* Compact desktop layout; retain Streamlit toolbar and sidebar controls. */
[data-testid="stMainBlockContainer"], .main .block-container {
    padding-top: 3.5rem !important;
    padding-bottom: 1rem !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
    max-width: 100% !important;
}
[data-testid="stSidebarUserContent"] { padding-top: 0.75rem !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.55rem !important; }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] { gap: 0.55rem !important; }
[data-testid="stHeading"] h1 { font-size: 26px !important; line-height: 1.2 !important; padding: 0 !important; margin: 0 !important; }
[data-testid="stMetricValue"] { font-size: 26px !important; line-height: 1.2 !important; }
[data-testid="stMetricLabel"] p { font-size: 13px !important; }
[data-testid="stMetric"] { gap: 0.15rem !important; }
[data-testid="stTabs"] [role="tablist"] { gap: 0.65rem !important; }
[data-testid="stTabs"] [role="tab"] { padding-top: 0.3rem !important; padding-bottom: 0.3rem !important; min-height: 2rem !important; }
</style>
""", unsafe_allow_html=True)
st.title('Astro Focus Dashboard V4.7.13')
def query_index(options, key):
    value=st.query_params.get(key, '')
    return options.index(value) if value in options else 0
now=datetime.now(ZoneInfo('Asia/Kolkata'))
MIN_YEAR, MAX_YEAR=2000,2050
if 'calendar_year' not in st.session_state:
    try:
        initial=date.fromisoformat(st.query_params.get('month',now.strftime('%Y-%m'))+'-01')
        if not MIN_YEAR<=initial.year<=MAX_YEAR: raise ValueError('Out of range')
    except ValueError: initial=now.date()
    st.session_state.calendar_year=initial.year
    st.session_state.calendar_month=initial.month

def move_month(step):
    y=st.session_state.calendar_year; m=st.session_state.calendar_month
    if step==0: y,m=now.year,now.month
    else:
        y,m=divmod(y*12+m-1+step,12); m+=1
    if MIN_YEAR<=y<=MAX_YEAR:
        st.session_state.calendar_year=y; st.session_state.calendar_month=m
        st.session_state.pop('focus_history_event',None)

def month_changed():
    st.session_state.pop('focus_history_event',None)

with st.sidebar:
    left,right=st.columns(2)
    y=left.selectbox('Year',list(range(MIN_YEAR,MAX_YEAR+1)),key='calendar_year',on_change=month_changed)
    m=right.selectbox('Month',list(range(1,13)),format_func=lambda n:calendar.month_name[n],key='calendar_month',on_change=month_changed)
    month=f'{y}-{m:02}'
    st.caption('Selected month: '+month)
    prev,current,nxt=st.columns(3)
    prev.button('Previous',on_click=move_month,args=(-1,),disabled=(y,m)==(MIN_YEAR,1))
    current.button('Current',on_click=move_month,args=(0,))
    nxt.button('Next',on_click=move_month,args=(1,),disabled=(y,m)==(MAX_YEAR,12))
    include_calculated=True
    mode_file=OUT/f'mode_{month}.txt'
    mode_changed=not mode_file.exists() or mode_file.read_text(encoding='utf-8')!=str(include_calculated)
    refresh=st.button('Generate / refresh selected month')
    if refresh or mode_changed or not all((OUT/f'{prefix}_{month}.{ext}').exists() for prefix,ext in [('positional','csv'),('events','csv'),('focus','csv'),('Astro_Focus','xlsx')]):
        try:
            with st.spinner('Building '+month+' from available source records…'): build(y,m,include_calculated=include_calculated)
        except Exception as exc:
            st.error(f'Month generation failed: {exc}'); st.stop()
    from nse_calendar import holidays
    if holidays(y) is None:
        st.warning(f'NSE holiday calendar for {y} is missing. Focus Dates are withheld until a verified annual calendar is added.')
rows=load(OUT/f'events_{month}.csv'); focus=load(OUT/f'focus_{month}.csv')
if not rows:
    st.warning(f'No imported planetary records for {month}. Add verified Drik Panchang / Prokerala records to data/{month}/events.csv and refresh. Empty does not mean no events.')
st.caption('Monthly coverage is partial; calculated research events are not independently source-verified.')
a,b,c=st.columns(3); a.metric('Focus records',len(focus)); b.metric('Imported / derived events',len(rows)); c.metric('P1 focus records',sum(r['priority']=='P1' for r in focus))
categories=['All']+sorted({r['category'] for r in rows}); biases=['All']+sorted({r['bias'] for r in rows}); priorities=['All','P1','P2','P3']
category=st.sidebar.selectbox('Category',categories,index=query_index(categories,'category')); bias=st.sidebar.selectbox('Possible direction',biases,index=query_index(biases,'bias')); priority=st.sidebar.selectbox('Priority',priorities,index=query_index(priorities,'priority'))
def filt(items): return [r for r in items if (category=='All' or r['category']==category) and (bias=='All' or r['bias']==bias) and (priority=='All' or r['priority']==priority)]
tabs=st.tabs(['Focus dates','All events','Positional','Lunar & yoga','D9 / timings','Master rules','Coverage','Daily Timing Candles','Event History','Lookup','Inference rules'])
from research.engine import availability, event_key
from research.ui import panel
from inference import decorate, RULES
import pandas as pd

with tabs[0]:
    items=filt(focus)
    counts={name:availability(name) for name in {r['event'] for r in items}}
    if items:
        display=decorate(items, rows)
        for item in display:
            item['History']='History' if counts[item['event']] else ''
        order=list(display[0]); order.remove('_inference_tooltip'); order.remove('History'); order.remove('I')
        order.insert(order.index('event')+1,'I')
        order.insert(order.index('bias')+1,'History')
        # Scope selection to the exact visible rows so changing filters cannot
        # accidentally open history for a different event at the same index.
        signature=hashlib.sha256(repr((month,category,bias,priority,display)).encode()).hexdigest()[:16]
        grid_key='focus_grid_'+signature
        def select_history():
            cells=st.session_state[grid_key]['selection'].get('cells',[])
            if cells:
                idx,col=cells[0]
                if col=='History' and 0 <= idx < len(display) and display[idx]['History']:
                    st.session_state['focus_history_event']=display[idx]['event']
        inference_details={r['event']:r.get('_inference_tooltip','') for r in display}
        for item in display: item.pop('_inference_tooltip',None)
        # Keep the Focus Dates view compact; detailed provenance remains in All events / Excel.
        focus_columns=['start_ist','end_ist','category','event','I','bias','History','priority']
        focus_columns=[c for c in focus_columns if c in display[0]]
        # Native Streamlit table; font-size adjustment removed because
        # Streamlit's canvas-based dataframe does not reliably apply CSS font sizing.
        styled=styled_events(display,market_only=True)
        styled=styled.apply(lambda col: ['color: #2563eb; font-weight: bold' if v else '' for v in col],subset=['History'])
        # Let Streamlit's native dataframe size columns from their contents
        # and available viewport. Explicit pixel widths caused wasted space
        # on some screens and horizontal scrolling on others.
        column_config={
            'start_ist':st.column_config.TextColumn('Start (IST)'),
            'end_ist':st.column_config.TextColumn('End (IST)'),
            'category':st.column_config.TextColumn('Category'),
            'event':st.column_config.TextColumn('Event'),
            'I':st.column_config.TextColumn('I',help='Select this cell to view the matching user-supplied inference hypothesis.'),
            'bias':st.column_config.TextColumn('Bias'),
            'History':st.column_config.TextColumn('History',help='Select a History cell to open research below this table.'),
            'priority':st.column_config.TextColumn('Priority'),
        }
        st.dataframe(
            styled, column_order=focus_columns, column_config=column_config,
            width='stretch',hide_index=True,
            key=grid_key,on_select=select_history,selection_mode='single-cell',
        )
        selected_cells=st.session_state.get(grid_key,{}).get('selection',{}).get('cells',[])
        if selected_cells:
            selected_row,selected_col=selected_cells[0]
            if selected_col=='I' and 0 <= selected_row < len(display):
                selected_event=display[selected_row]['event']
                detail=inference_details.get(selected_event,'')
                if detail:
                    with st.expander('Inference details — '+selected_event,expanded=True):
                        st.info(detail+'\n\nResearch hypothesis only; not a validated market signal.')
        chosen=st.session_state.get('focus_history_event','')
        if counts.get(chosen,0):
            def close_history():
                st.session_state.pop('focus_history_event',None)
                st.session_state[grid_key]={'selection':{'cells':[],'rows':[],'columns':[]}}
            with st.expander('History — '+chosen,expanded=True):
                st.button('Close history',key='close_focus_history',on_click=close_history)
                panel('focus_'+event_key(chosen)+'_',chosen)
    else: st.caption('No matching focus events.')
for tab,items in zip(tabs[1:5],[rows,load(OUT/f'positional_{month}.csv'),[r for r in rows if r['category'] in ('Must Watch','Gandamool','Yoga')],[r for r in rows if r['category'] in ('D9','Timing Candles','Conjunction')]]):
    with tab:
        shown=filt(items)
        decorated=decorate(shown,rows)
        details={r.get('event',''):r.get('_inference_tooltip','') for r in decorated}
        for item in decorated: item.pop('_inference_tooltip',None)
        df=pd.DataFrame(decorated)
        if not df.empty:
            sty=styled_events(df.to_dict('records'),market_only=(tab is tabs[2]))
            grid='inference_grid_'+str(tabs.index(tab))
            st.dataframe(sty,width='stretch',hide_index=True,key=grid,selection_mode='single-cell',on_select='rerun',column_config={'I':st.column_config.TextColumn('I',help='Select this cell to view matching user-supplied inference hypotheses.')})
            selected=st.session_state.get(grid,{}).get('selection',{}).get('cells',[])
            if selected and selected[0][1]=='I' and 0 <= selected[0][0] < len(decorated):
                selected_event=decorated[selected[0][0]].get('event','')
                if details.get(selected_event):
                    with st.expander('Inference details — '+selected_event,expanded=True):
                        st.info(details[selected_event]+'\n\nResearch hypothesis only; not a validated market signal.')
        else: st.caption('No matching events.')
with tabs[8]:
    from research.ui import panel
    panel('history_')
with tabs[5]: st.dataframe(load(ROOT/'rules.csv'),hide_index=True,width='stretch')
with tabs[6]:
    st.write('Partial source coverage. Legacy Delhi data is labelled; Mumbai verification and full monthly source imports are pending. Directions are unvalidated hypotheses.')
    st.write('See Excel Coverage sheet. Empty categories mean not established, not necessarily no events.')
    st.dataframe(load(ROOT/'coverage.csv'),hide_index=True,width='stretch')
st.download_button('Download monthly Excel',(OUT/f'Astro_Focus_{month}.xlsx').read_bytes(),file_name=f'Astro_Focus_{month}.xlsx')

with tabs[7]:
    from datetime import timedelta
    today=datetime.now(ZoneInfo('Asia/Kolkata')).date()
    mode=st.radio('Timing date',['Today','Tomorrow','Choose date'],horizontal=True)
    selected=today if mode=='Today' else today+timedelta(days=1) if mode=='Tomorrow' else st.date_input('Choose timing date',value=today)
    st.caption(f'Selected: {selected} | Mumbai IST (UTC+05:30); source timezone validated before display.')
    if st.button('Fetch selected day timings'):
        try:
            with st.spinner('Fetching selected date from Prokerala…'):
                st.session_state['daily_timing_result']=(selected.isoformat(),fetch_day(selected))
        except Exception as exc:
            st.error(f'Timing fetch failed: {exc}')
    result=st.session_state.get('daily_timing_result')
    if result and result[0]==selected.isoformat():
        timings=result[1]
        if not timings: st.info('No matching aspects returned for this date.')
        aspects=st.multiselect('Aspect filter',sorted({r['aspect'] for r in timings}),default=sorted({r['aspect'] for r in timings}))
        daily=[r for r in timings if r['aspect'] in aspects]
        st.dataframe(daily,hide_index=True,width='stretch')
        txt='\n'.join(map(line,daily))
        st.code(txt or 'No matching rows',language=None)
        st.download_button('Download daily text',txt,file_name=f'timing_candles_{selected}.txt')
        buf=io.StringIO(); writer=csv.DictWriter(buf,fieldnames=FIELDS); writer.writeheader(); writer.writerows(daily)
        st.download_button('Download daily CSV',buf.getvalue(),file_name=f'timing_candles_{selected}.csv')

with tabs[9]:
    from lookup import render
    render()
with tabs[10]:
    st.subheader('User-supplied inference rules')
    st.caption('These are your supplied market hypotheses. They are displayed as additional context only; they are not independently verified, backtested or trading recommendations.')
    inference_df=pd.DataFrame(RULES,columns=['Inference','Market','Hypothesis','Condition','Interpretation'])
    st.dataframe(inference_df,width='stretch',hide_index=True)
    st.download_button('Download inference rules CSV',inference_df.to_csv(index=False).encode('utf-8'),file_name='astro_inference_rules.csv',mime='text/csv')
