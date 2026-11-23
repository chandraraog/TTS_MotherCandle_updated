"""Read reference rules from the supplied workbook, not market predictions."""
from pathlib import Path
import openpyxl
import pandas as pd

WORKBOOK = Path(__file__).resolve().parent / 'data/lookup/Combined_Lookup.xlsx'

def load_tables(path=WORKBOOK):
    w = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        def records(name, width):
            rows = list(w[name].values)
            return [dict(zip(rows[0][:width], r[:width])) for r in rows[1:] if isinstance(r[0], int)]
        sectors = records('Sector Planets', 5)
        d9 = records('D9 - Conjunction', 7)
        rashi = records('Rashi Aspects', 8)
        keys = {r[0]:r[1] for r in list(w['Keys_D9'].values)[1:] if r[0]}
        aliases = {str(r[3]).upper():r[4] for r in list(w['Keys_Rashi'].values)[1:] if r[3] is not None}
        return sectors,d9,rashi,keys,aliases
    finally:
        w.close()

def planet(value):
    return str(value or '').strip().split()[-1].upper() if str(value or '').strip() else ''

def sector_matches(rows, query='', p=''):
    p = 'RAHU' if p.upper()=='NODE' else p.upper()
    return [r for r in rows if query.strip().casefold() in r['Sector'].casefold() and (not p or p in [planet(r.get(f'Planet {i}')) for i in (1,2,3)])]

def d9_matches(rows, keys, a,b):
    a,b=a.upper(),b.upper()
    found=[]
    for x in (('RAHU','KETU') if a=='NODE' else (a,)):
        for y in (('RAHU','KETU') if b=='NODE' else (b,)):
            idx=keys.get(f'{x}|{y}')
            if idx is not None and rows[idx-2] not in found: found.append(rows[idx-2])
    return found

def rashi_matches(rows,a,b):
    return [r for r in rows if {r['No. 1'],r['No. 2']}=={a,b}]

def render():
    import streamlit as st
    st.subheader('Astro Lookup')
    st.caption('Reference rules from Combined_Lookup.xlsx — not validated market predictions.')
    try: sectors,d9,rashi,keys,aliases=load_tables()
    except Exception as exc:
        st.error(f'Cannot read lookup workbook: {exc}'); return
    def table(rows, cols=None):
        if not rows: st.info('No matching reference entry.'); return
        df=pd.DataFrame(rows)
        st.dataframe(df[cols] if cols else df,hide_index=True,width='stretch')
    planets=sorted({planet(r.get(f'Planet {i}')) for r in sectors for i in (1,2,3)}-{''})
    a,b,c=st.tabs(['Sector Lookup','D9 Lookup','Rashi Lookup'])
    with a:
        q=st.text_input('Enter sector (partial name supported)',key='lookup_sector')
        p=st.selectbox('Filter by planet',['All']+planets+['NODE','KETU'],key='lookup_sector_planet')
        table(sector_matches(sectors,q,'' if p=='All' else p))
        st.caption('Node = Rahu for sector lookup. Ketu has no sectors listed in the workbook.')
    with b:
        options=sorted({r[k] for r in d9 for k in ('P1 (plain)','P2 (plain)')}|{'NODE','KETU','RAHU'})
        x,y=st.columns(2)
        p1=x.selectbox('Planet 1',options,index=options.index('MOON'),key='lookup_d9_p1')
        p2=y.selectbox('Planet 2',options,index=options.index('KETU'),key='lookup_d9_p2')
        cols=['Sr. No.','Planet 1','Planet 2','Outcome']
        table(d9_matches(d9,keys,p1,p2),cols)
        st.caption('Node searches Rahu and Ketu; shared source rows appear once.')
        with st.expander('All D9 conjunction rules'): table(d9,cols)
    with c:
        x,y=st.columns(2)
        r1=x.text_input('Rashi 1 (number or name)', 'Taurus',key='lookup_rashi_1')
        r2=y.text_input('Rashi 2 (number or name)', 'Gemini',key='lookup_rashi_2')
        n1,n2=aliases.get(r1.strip().upper()),aliases.get(r2.strip().upper())
        if n1 is None or n2 is None: st.warning('Enter a valid Rashi number (1–12), English or Sanskrit name.')
        else:
            diff=abs(n1-n2); angle=min(diff,12-diff)*30
            st.write(f'Rashi relationship: {angle}° (sign-based)')
            table(rashi_matches(rashi,n1,n2))
        with st.expander('All Rashi aspect rules'): table(rashi)
    st.download_button('Download original lookup workbook',WORKBOOK.read_bytes(),file_name=WORKBOOK.name,key='lookup_download')
