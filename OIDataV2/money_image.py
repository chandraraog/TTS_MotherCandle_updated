"""Render saved Money rows as a legible Telegram table image."""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def table_image(payload, pairs, atm, threshold, top_ce, top_pe, collected, missing):
    width=1440
    header_y=108
    row_height=68
    bottom=header_y+48+len(pairs)*row_height
    image=Image.new('RGB',(width,bottom+96),'white')
    draw=ImageDraw.Draw(image)
    def font(size,bold=False):
        path=Path('C:/Windows/Fonts')/('segoeuib.ttf' if bold else 'segoeui.ttf')
        return ImageFont.truetype(str(path) if path.exists() else ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),size)
    normal=font(21);bold=font(21,True);small=font(18);title=font(27,True)
    ink='#143251';muted='#60758b'
    draw.text((24,15),'CHANDRA MARKET DESK  ·  MONEY BY STRIKE',font=title,fill=ink)
    draw.text((24,58),f"{payload['symbol']}  |  Expiry {payload.get('expiry',{}).get('date','—')}  |  Spot {payload['spot']:,.2f}",font=normal,fill=muted)
    edges=[0,210,430,650,850,1050,1165,1440]
    labels=['Strike','CE Money ₹ lakh','PE Money ₹ lakh','CE BEP ₹','PE BEP ₹','Diff %','View']
    draw.rectangle((0,header_y,width,header_y+48),fill='#e8eef5')
    for i,label in enumerate(labels):
        draw.text(((edges[i]+edges[i+1]-draw.textlength(label,font=small))/2,header_y+12),label,font=small,fill=ink)
    def value(x):return '—' if x is None else f'{x:,.2f}'
    for index,row in enumerate(sorted(pairs,key=lambda r:r['strike'],reverse=True)):
        y=header_y+48+index*row_height
        is_atm=row['strike']==atm
        if is_atm:draw.rectangle((0,y,width,y+row_height),fill='#e0efeb')
        for side,col,selected in [('CE',1,top_ce),('PE',2,top_pe)]:
            if row['strike'] in selected:draw.rectangle((edges[col],y,edges[col+1],y+row_height),fill='#dce4fa')
        draw.line((0,y+row_height,width,y+row_height),fill='#d9e2ec')
        ce,pe=row['CE'],row['PE']
        pct=abs(ce-pe)/max(ce,pe)*100 if min(ce,pe)>=0 and max(ce,pe)>0 else 0 if ce==pe==0 else None
        view='Unwinding / mixed' if min(ce,pe)<0 else 'Neutral' if ce==pe or pct<threshold else 'Bullish' if pe>ce else 'Bearish'
        values=[f"{row['strike']:,.0f}",value(ce),value(pe),value(row.get('CE_BEP')),value(row.get('PE_BEP')),value(pct)]
        for col,text in enumerate(values):
            selected=col==1 and row['strike'] in top_ce or col==2 and row['strike'] in top_pe
            f=bold if selected else normal
            x=(edges[col]+edges[col+1]-draw.textlength(text,font=f))/2
            draw.text((x,y+(5 if col==0 and is_atm else 20)),text,font=f,fill=ink)
        if is_atm:
            draw.rounded_rectangle((72,y+35,138,y+61),radius=13,fill='#fff1bb')
            draw.text(((210-draw.textlength('ATM',font=small))/2,y+35),'ATM',font=small,fill='#8b6800')
        color,bg=('#008575','#d5eee7') if view=='Bullish' else ('#cf3e36','#fde5e3') if view=='Bearish' else ('#a47700','#fbf3df')
        box_width=draw.textlength(view,font=small)+28
        x=(edges[6]+edges[7]-box_width)/2
        draw.rounded_rectangle((x,y+16,x+box_width,y+51),radius=17,fill=bg)
        draw.text((x+14,y+22),view,font=small,fill=color)
    draw.text((24,bottom+14),f'Saved: {collected}  ·  Not live  ·  Totals cover shown strikes',font=small,fill=muted)
    note='Partial data — missing/incomplete strikes: '+', '.join(sorted({v.split()[0] for v in missing},key=float)) if missing else 'Complete ATM window available.'
    draw.text((24,bottom+47),note,font=small,fill='#a47700' if missing else muted)
    output=BytesIO();image.save(output,format='PNG');return output.getvalue()
