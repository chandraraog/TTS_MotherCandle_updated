'use strict';
const $ = id => document.getElementById(id);
const page = document.body.dataset.page;
let sourceChosen = false, moneyAlertsInitialized=false;
let theme = 'light';
try { theme = localStorage.getItem('market-desk-theme') || 'light'; } catch {}
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  $('theme').textContent = theme === 'dark' ? 'White theme' : 'Black theme';
  $('theme').setAttribute('aria-label', 'Switch to ' + (theme === 'dark' ? 'white' : 'black') + ' theme');
}
applyTheme();
$('theme').addEventListener('click', () => {
  theme = theme === 'dark' ? 'light' : 'dark'; applyTheme();
  try { localStorage.setItem('market-desk-theme', theme); } catch {}
});
let state = null, snapshot = null, initialized = false, polling = false, exportRows = [], exportColumns = [], movementPolling = false;
let oiStatusFilter = 'all';
let oiHoverHistory=[], oiHoverJob='', oiHoverCacheKey='', oiHoverSnapshots=[], oiHoverLoading=null, activeOiHoverCell=null, oiHoverHideTimer=null;
let moneyRecentOiSnapshots=[], moneyRecentOiCacheKey='', moneyRecentOiMonthSnapshots=[], moneyRecentOiMonthCacheKey='', moneyRecentOiLoading=null, moneyRecentOiSymbol='', moneyRecentOiSourceStamp='', moneyRecentOiReady=false;
const fmt = v => v === null || v === undefined ? '—' : typeof v === 'number' ? v.toLocaleString('en-IN', {maximumFractionDigits: 2}) : String(v);
const fmtOiLakh = v => v === null || v === undefined ? '—' : `${(Number(v)/100000).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})}L`;
const shortOiDate = value => {const match=String(value||'').match(/^(\d{4})-(\d{2})-(\d{2})$/);return match?`${match[3]}/${match[2]}`:String(value||'—');};
function oiCePeDominance(ce,pe){
 if(typeof ce!=='number'||typeof pe!=='number')return null;
 const ceAbs=Math.abs(ce),peAbs=Math.abs(pe),gross=ceAbs+peAbs;
 if(gross===0)return 'CE/PE 0%';
 if(ceAbs===peAbs)return 'CE/PE 50%';
 const side=ceAbs>peAbs?'CE':'PE',share=Math.round(Math.max(ceAbs,peAbs)/gross*100);
 return `${side} ${share}%`;
}
function oiCePeDominanceClass(ce,pe){if(typeof ce!=='number'||typeof pe!=='number'||Math.abs(ce)===Math.abs(pe))return 'oi-hover-even';return Math.abs(ce)>Math.abs(pe)?'oi-hover-ce-lead':'oi-hover-pe-lead';}
const when = v => v ? new Date(v).toLocaleString('en-IN', {timeZone:'Asia/Kolkata', hour12:false}) : '—';
const todayIST = () => new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const job = () => page === 'money' ? 'money' : $('source').value;
function oiSignal(term, view) {
  term=String(term || '—'); view=String(view || '—');
  if(term==='—')return '—';
  const title=term.replace(/\b\w/g,letter=>letter.toUpperCase());
  return view==='—' ? title : `${title} (${view.replace(/^Aggressive /,'Agg ')})`;
}
function oiSetupView(row) {
  const values=[row.Future,row['CE Term'],row['PE Term']];
  if(values.some(value=>!value||String(value).trim()==='—'))return 'Incomplete';
  const [futureValue,ceValue,peValue]=values;
  const future=String(futureValue).toLowerCase(),ce=String(ceValue).toLowerCase(),pe=String(peValue).toLowerCase();
  if(future==='long buildup' && ce==='short covering' && pe==='short buildup')return 'Long setup';
  if(future==='short buildup' && ce==='short buildup' && pe==='short covering')return 'Short setup';
  return 'Mixed / no match';
}
function monthlyOiValues(row,targetExpiry){
 if(!row||!targetExpiry)return [null,null,null];
 const monthlyPrefix=String(row['Monthly Future Expiry']||'')===String(targetExpiry)?'Monthly ':String(row['Next Monthly Future Expiry']||'')===String(targetExpiry)?'Next Monthly ':null;
 const futureKey=monthlyPrefix?`${monthlyPrefix}Fut OI Chg`:String(row['Future Expiry']||'')===String(targetExpiry)?'Fut OI Chg':String(row['Next Future Expiry']||'')===String(targetExpiry)?'Next Fut OI Chg':null;
 const future=futureKey&&typeof row[futureKey]==='number'?row[futureKey]:null;
 const optionPrefix=String(row['Monthly Option Expiry']||'')===String(targetExpiry)?'Monthly ':String(row['Next Monthly Option Expiry']||'')===String(targetExpiry)?'Next Monthly ':String(row['Option Expiry']||'')===String(targetExpiry)?'':String(row['Next Option Expiry']||'')===String(targetExpiry)?'Next ':null;
 const ceKey=optionPrefix===null?null:`${optionPrefix}Total CE OI Chg`,peKey=optionPrefix===null?null:`${optionPrefix}Total PE OI Chg`,ce=ceKey&&typeof row[ceKey]==='number'?row[ceKey]:null,pe=peKey&&typeof row[peKey]==='number'?row[peKey]:null;
 return [future,ce,pe];
}
function oiRowsForSelectedExpiry(rows) {
  if(page!=='oi')return rows;
  const next=job()==='nse' && $('expiryRank')?.value==='next';
  const fields=['Future Expiry','Fut Close','Fut OI','Fut OI Chg','Future','Fut View','ATM','Option Expiry','CE OI','CE OI Chg','CE Term','CE View','Total CE OI','Total CE OI Chg','PE OI','PE OI Chg','PE Term','PE View','Total PE OI','Total PE OI Chg','PCR'];
  return rows.map(source=>{
    const row={...source};
    if(next){for(const field of fields)row[field]=source[`Next ${field}`];row['Net View']=source['Next Net View'] || 'Incomplete';}
    row['Future Signal']=oiSignal(row.Future,row['Fut View']);
    row['CE Signal']=oiSignal(row['CE Term'],row['CE View']);
    row['PE Signal']=oiSignal(row['PE Term'],row['PE View']);
    row['Our View']=oiSetupView(row);
    return row;
  });
}
const noticeTimers=new Map(), passiveNotices=new Map();
function notice(id,text,passive=false){
 const node=$(id);if(!node)return;
 text=text||'';
 if(passive&&passiveNotices.get(id)===text)return;
 if(passive)passiveNotices.set(id,text);
 clearTimeout(noticeTimers.get(id));
 node.textContent=text;node.hidden=!text;
 if(text)noticeTimers.set(id,setTimeout(()=>{node.hidden=true;node.textContent='';noticeTimers.delete(id);},60000));
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function option(value, text) { const node = document.createElement('option'); node.value=value; node.textContent=text; return node; }
function syncInstrumentPreset(){
 const preset=$('instrumentPreset');if(!preset)return;
 const value=$('symbol').value.trim().toUpperCase();
 preset.value=[...preset.options].some(o=>o.value===value)?value:'custom';
 $('customInstrumentField').hidden=preset.value!=='custom';
}
if($('instrumentPreset'))$('instrumentPreset').addEventListener('change',()=>{
 const custom=$('instrumentPreset').value==='custom';
 $('customInstrumentField').hidden=!custom;
 if(custom){$('symbol').value='';$('symbol').focus();}
 else $('symbol').value=$('instrumentPreset').value;
 $('symbol').dispatchEvent(new Event('change'));
});
function fillSettings(cfg) {
  if (page === 'money') {
    $('symbol').value=cfg.money.symbol; syncInstrumentPreset(); $('strikes').value=cfg.money.strikes; if(![...$('interval').options].some(o=>Number(o.value)===cfg.money.interval_seconds))$('interval').append(option(cfg.money.interval_seconds,`${cfg.money.interval_seconds / 60} min`)); $('interval').value=cfg.money.interval_seconds;
    if (cfg.money.expiry) $('expiry').append(option(cfg.money.expiry, cfg.money.expiry));
    $('expiry').value=cfg.money.expiry;
  } else {
    $('oiMinPrice').value=cfg.oi.min_price ?? 1000; $('universe').value=cfg.oi.universe; $('schedule').value=cfg.oi.mode; $('interval').value=cfg.oi.interval_seconds / 60;
    $('dailyTime').value=cfg.oi.daily_time; $('symbols').value=cfg.oi.symbols.join(', ');
    $('nseEnabled').checked=cfg.nse.enabled; $('nseTime').value=cfg.nse.daily_time;
    $('nseRetry').value=cfg.nse.retry_seconds / 60; $('nseDays').value=cfg.nse.history_days; $('retentionDays').value=String(cfg.retention_days);
  }
}
const expiryChoices=new Map();
let expiryRequest=0;
function updateExpiries() {
  if(page!=='money')return;
  const symbol=$('symbol').value.trim().toUpperCase();
  if($('expiry').disabled)return;
  const expiries=expiryChoices.get(symbol) || (snapshot?.symbol===symbol?snapshot.expiries:null);
  if(!expiries)return;
  const value=$('expiry').value;
  $('expiry').replaceChildren(option('','Nearest expiry'));
  for(const e of expiries)$('expiry').append(option(String(e.expiry),e.date));
  if(value&&![...$('expiry').options].some(o=>o.value===value))$('expiry').append(option(value,`${value} (unavailable)`));
  $('expiry').value=value;
}
async function loadInstrumentExpiries(){
  const request=++expiryRequest,symbol=$('symbol').value.trim().toUpperCase();
  $('expiry').replaceChildren(option('','Nearest expiry'));
  $('expiry').disabled=false;
  if(!symbol)return;
  if(expiryChoices.has(symbol)){updateExpiries();return;}
  $('expiry').disabled=true;$('expiry').replaceChildren(option('','Loading expiries…'));
  try{
    const data=await api('/api/money/expiries',{symbol});
    if(request!==expiryRequest)return;
    expiryChoices.set(symbol,data.expiries);$('expiry').disabled=false;updateExpiries();
    notice('message',data.expiries.length?'Expiries updated. Choose an expiry and Save settings.':'No expiries returned for this instrument.');
  }catch(e){if(request!==expiryRequest)return;$('expiry').disabled=false;$('expiry').replaceChildren(option('','Nearest expiry'));notice('error','Could not load expiries: '+e.message);}
}
function renderTable(columns, rows) {
  exportColumns=columns; exportRows=rows;
  if ($('columns').value === 'compact') {
    const keys = page === 'money' ? ['strike','type','oi_change','atp','money','snapshot_money_change'] : ['Symbol','Our View','Future Expiry','Fut Close','Fut OI Chg','Future Signal','Option Expiry','ATM','CE OI Chg','CE Signal','PE OI Chg','PE Signal','PCR'];
    columns = columns.filter(([key]) => keys.includes(key));
  }
  const header=document.createElement('tr');
  for (const [key,label] of columns) { const th=document.createElement('th'); th.textContent=label; header.append(th); }
  $('thead').replaceChildren(header);
  const fragment=document.createDocumentFragment();
  for (const row of rows) {
    const tr=document.createElement('tr');
    for (const [key] of columns) {
      const td=document.createElement('td'); td.textContent=fmt(row[key]);
      if(page==='oi' && key==='Symbol'){td.classList.add('oi-hover-trigger');td.title='Hover to compare the last 3 days';}
      if (typeof row[key] === 'number' && (key.toLowerCase().includes('change') || key.includes('Chg') || key.includes('Since') || key === 'money')) td.className=row[key] < 0 ? 'negative' : row[key] > 0 ? 'positive' : '';
      if (String(row[key]).includes('Bullish')) td.className='positive';
      if (String(row[key]).includes('Bearish')) td.className='negative';
      if (key==='Our View') td.className=row[key]==='Long setup'?'positive':row[key]==='Short setup'?'negative':'';
      if (row.error) td.title=row.error;
      tr.append(td);
    }
    fragment.append(tr);
  }
  $('tbody').replaceChildren(fragment); $('empty').hidden=rows.length > 0;
  $('empty').textContent = snapshot ? 'No rows match the current filter.' : 'No saved snapshot for this source yet. Collection progress is shown above; select NSE daily bhavcopy to view saved daily reports.';
}
function moneyView(ce,pe) {
  if(ce==null||pe==null)return {view:'Incomplete',pct:null,leader:'—'};
  const leader=ce===pe?'Equal':ce>pe?'CE more':'PE more';
  if(ce<0||pe<0)return {view:'Unwinding / mixed',pct:null,leader};
  const divisor=$('moneyBase').value==='min'?Math.min(ce,pe):Math.max(ce,pe);
  const pct=divisor>0?Math.abs(ce-pe)/divisor*100:ce===pe?0:null;
  return {pct,leader,view:ce===pe||pct!==null&&pct<Math.max(0,Number($('moneyThreshold').value)||0)?'Neutral':pe>ce?'Bullish':'Bearish'};
}
function renderMoneyPairs(rows,filter) {
  for(const button of document.querySelectorAll('[data-diff]')){const active=Number(button.dataset.diff)===Number($('moneyThreshold').value);button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));}
  for(const button of document.querySelectorAll('[data-top]')){const active=Number(button.dataset.top)===Number($('moneyTop').value);button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));}
  const groups=new Map();
  for(const r of rows){if(!groups.has(r.strike))groups.set(r.strike,{strike:r.strike,ce:null,pe:null,ceBep:null,peBep:null});const p=groups.get(r.strike);if(r.type==='CE'){p.ce=r.money;p.ceBep=r.atp==null?null:r.strike+r.atp;}if(r.type==='PE'){p.pe=r.money;p.peBep=r.atp==null?null:r.strike-r.atp;}}
  const step=Math.max(0.01,Number($('moneyStep').value)||100);
  const atmStrike=snapshot?.spot==null?null:Math.round(snapshot.spot/step)*step;
  let visible=[...groups.values()].filter(r=>Math.abs(r.strike/step-Math.round(r.strike/step))<1e-8).sort((a,b)=>a.strike-b.strike);
  const atm=visible.find(r=>r.strike===atmStrike)||null;
  let atmView='—';
  let ceTop=new Set(),peTop=new Set();
  const top=Math.max(0,Math.floor(Number($('moneyTop').value)||0));
  if(top){ceTop=new Set([...visible].filter(r=>r.ce!=null).sort((a,b)=>b.ce-a.ce).slice(0,top).map(r=>r.strike));peTop=new Set([...visible].filter(r=>r.pe!=null).sort((a,b)=>b.pe-a.pe).slice(0,top).map(r=>r.strike));visible=visible.filter(r=>ceTop.has(r.strike)||peTop.has(r.strike));}
  visible=visible.filter(r=>String(r.strike).includes(filter)).sort((a,b)=>b.strike-a.strike).map(r=>({...r,...moneyView(r.ce,r.pe)}));
  const columns=[['strike','Strike'],['ce','CE Money ₹ lakh'],['pe','PE Money ₹ lakh'],['ceBep','CE BEP ₹'],['peBep','PE BEP ₹'],['pct','Diff %'],['leader','Money leader'],['view','View']];
  renderTable(columns,visible);
  [...$('tbody').children].forEach((tr,i)=>{const r=visible[i];if(ceTop.has(r.strike))tr.children[1].classList.add('money-top');if(peTop.has(r.strike))tr.children[2].classList.add('money-top');const leader=document.createElement('span');leader.className='money-leader '+(r.leader==='CE more'?'money-leader-ce':r.leader==='PE more'?'money-leader-pe':'');leader.textContent=r.leader;tr.children[6].replaceChildren(leader);tr.lastChild.classList.add(r.view==='Bullish'?'money-bull':r.view==='Bearish'?'money-bear':'money-neutral');if(r.strike===atm?.strike){tr.classList.add('money-atm');const badge=document.createElement('span');badge.className='money-atm-badge';badge.textContent='ATM';tr.firstChild.append(' ',badge);}if(r.ceBep!=null&&snapshot.spot>=r.ceBep)tr.children[3].classList.add('money-bep-hit');if(r.peBep!=null&&snapshot.spot<=r.peBep)tr.children[4].classList.add('money-bep-hit');const viewBadge=document.createElement('span');viewBadge.className='money-badge '+(r.view==='Bullish'?'money-bull':r.view==='Bearish'?'money-bear':'money-neutral');viewBadge.textContent=r.view;tr.lastChild.className='';tr.lastChild.replaceChildren(viewBadge);});
  const total=key=>visible.length&&visible.every(r=>r[key]!=null)?visible.reduce((sum,r)=>sum+r[key],0):null;
  const ce=total('ce'),pe=total('pe'),view=moneyView(ce,pe);
  $('atmStrike').textContent=atm?' → ATM '+fmt(atm.strike):'';$('atmSummary').replaceChildren();if(atm){const j=moneyView(atm.ce,atm.pe);atmView=j.view;$('atmSummary').append(`CE ${fmt(atm.ce)} vs PE ${fmt(atm.pe)} `);const gap=document.createElement('strong');gap.className='money-gap-badge';gap.textContent=j.pct==null?'Gap —':`${fmt(j.pct)}%`;gap.title='Difference between CE and PE Money';$('atmSummary').append(gap,' ');const tag=document.createElement('span');tag.className='money-badge '+(j.view==='Bullish'?'money-bull':j.view==='Bearish'?'money-bear':'money-neutral');tag.textContent=j.view;$('atmSummary').append(tag);}$('ce').textContent=fmt(ce);$('pe').textContent=fmt(pe);$('bias').textContent=view.view;$('bias').className='money-badge '+(view.view==='Bullish'?'money-bull':view.view==='Bearish'?'money-bear':'money-neutral');$('biasNote').textContent=`Difference ${fmt(view.pct)}% · ${visible.length} displayed strikes`;$('atmSummaryCard').classList.toggle('money-match-pulse',atmView!=='—'&&atmView===view.view);$('overallSummaryCard').classList.toggle('money-match-pulse',atmView!=='—'&&atmView===view.view);
}

async function loadOiHoverSnapshots(){
 const ordered=[...oiHoverHistory].sort((a,b)=>String(b.scope||b.stamp).localeCompare(String(a.scope||a.stamp)));
 const seenDays=new Set();
 const items=ordered.filter(item=>{const day=String(item.scope||item.stamp).slice(0,10);if(seenDays.has(day))return false;seenDays.add(day);return true;}).slice(0,3);
 const key=oiHoverJob+':'+items.map(item=>item.id).join(',');
 if(key===oiHoverCacheKey)return oiHoverSnapshots;
 if(oiHoverLoading)return oiHoverLoading;
 oiHoverLoading=Promise.all(items.map(item=>api(`/api/snapshot?job=${encodeURIComponent(oiHoverJob)}&id=${encodeURIComponent(item.id)}`)))
   .then(results=>{oiHoverSnapshots=results.map(result=>result.snapshot).filter(Boolean);oiHoverCacheKey=key;return oiHoverSnapshots;})
   .finally(()=>{oiHoverLoading=null;});
 return oiHoverLoading;
}
async function loadOiMonthSnapshots(){
 const ordered=[...oiHoverHistory].sort((a,b)=>String(b.scope||b.stamp).localeCompare(String(a.scope||a.stamp))),month=String(ordered[0]?.scope||ordered[0]?.stamp||'').slice(0,7);
 if(!month)return [];
 const items=await api(`/api/history?job=nse&month=${encodeURIComponent(month)}`);
 const results=await Promise.all(items.map(item=>api(`/api/snapshot?job=nse&id=${encodeURIComponent(item.id)}`)));
 return results.map(result=>result.snapshot).filter(Boolean);
}
async function copyOiSummaryImage(table,symbol,longShortView,titleText,monthLines){
 if(!navigator.clipboard?.write||!window.ClipboardItem)throw new Error('Image clipboard is not supported by this browser.');
 const rows=[...table.rows].map(row=>[...row.cells]);
 const scale=2,padding=10,contentWidth=518,width=contentWidth+padding*2,titleHeight=32,netHeight=25,mtdFont='700 12px Segoe UI,Arial,sans-serif',valueFont='700 12px Segoe UI,Arial,sans-serif',mtdLineHeight=15,mtdPadY=5,mtdGap=7,headHeight=25,rowHeight=34,colWidths=[150,125,115,128];
 const measure=ctx=>{ctx.font=mtdFont;return monthLines.map(line=>{const maxWidth=contentWidth-16,valueParts=(line.values||[]).map(value=>({text:String(value.text||''),color:value.color||'#17632b',bg:value.bg||'#d8ead1'})),words=String(line.label||'').split(/\s+/),lines=[];let current='';for(const word of words){const candidate=current?`${current} ${word}`:word;if(current&&ctx.measureText(candidate).width>maxWidth){lines.push(current);current=word;}else current=candidate;}if(current)lines.push(current);ctx.font=valueFont;let chipRows=0,chipRowWidth=0;for(const value of valueParts){const chipWidth=ctx.measureText(value.text).width+10;if(chipRowWidth&&chipRowWidth+7+chipWidth>maxWidth){chipRows++;chipRowWidth=0;}chipRowWidth+=(chipRowWidth?7:0)+chipWidth;}if(chipRowWidth)chipRows++;ctx.font=mtdFont;return{...line,lines,valueParts,chipRows,height:(lines.length+chipRows)*mtdLineHeight+mtdPadY*2};});};
 const measureCanvas=document.createElement('canvas'),measureCtx=measureCanvas.getContext('2d'),measured=measure(measureCtx),mtdHeight=measured.reduce((sum,line)=>sum+line.height,0)+mtdGap*Math.max(0,measured.length-1),height=padding+titleHeight+netHeight+mtdHeight+headHeight+Math.max(1,rows.length-1)*rowHeight+padding;
 const canvas=document.createElement('canvas');canvas.width=width*scale;canvas.height=height*scale;
 const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.fillStyle='#ffffff';ctx.fillRect(0,0,width,height);
 ctx.fillStyle='#172433';ctx.font='700 14px Segoe UI,Arial,sans-serif';ctx.textBaseline='middle';ctx.textAlign='left';const titleLines=[];let titleCurrent='';for(const word of titleText.split(/\s+/)){const candidate=titleCurrent?`${titleCurrent} ${word}`:word;if(titleCurrent&&ctx.measureText(candidate).width>contentWidth){titleLines.push(titleCurrent);titleCurrent=word;}else titleCurrent=candidate;}if(titleCurrent)titleLines.push(titleCurrent);titleLines.slice(0,2).forEach((line,index)=>ctx.fillText(line,padding,padding+9+index*15));
 ctx.fillStyle='#26384a';ctx.font='600 13px Segoe UI,Arial,sans-serif';ctx.textAlign='left';ctx.fillText(`Final View: ${longShortView||'—'}`,padding,padding+titleHeight+netHeight/2);
 let mtdY=padding+titleHeight+netHeight;ctx.textBaseline='middle';ctx.textAlign='left';for(const line of measured){ctx.fillStyle='#edf4ef';ctx.fillRect(padding,mtdY,contentWidth,line.height);ctx.font=mtdFont;ctx.fillStyle='#26384a';line.lines.forEach((text,index)=>ctx.fillText(text,padding+8,mtdY+mtdPadY+mtdLineHeight*(index+.5)));ctx.font=valueFont;let valueX=padding+8,valueRow=0;for(const value of line.valueParts||[]){const chipWidth=ctx.measureText(value.text).width+10;if(valueX>padding+8&&valueX+chipWidth>padding+contentWidth-8){valueX=padding+8;valueRow++;}const valueY=mtdY+mtdPadY+mtdLineHeight*(line.lines.length+valueRow+.5);ctx.fillStyle=value.bg;ctx.fillRect(valueX,valueY-mtdLineHeight/2+1,chipWidth,mtdLineHeight-2);ctx.fillStyle=value.color;ctx.fillText(value.text,valueX+5,valueY);valueX+=chipWidth+7;}mtdY+=line.height+mtdGap;}
 const labels=['Date','Future','CE','PE'];let x=padding,y=padding+titleHeight+netHeight+mtdHeight+mtdGap;
 for(let col=0;col<labels.length;col++){ctx.fillStyle='#e8eef4';ctx.fillRect(x,y,colWidths[col],headHeight);ctx.strokeStyle='#cbd5e1';ctx.strokeRect(x,y,colWidths[col],headHeight);ctx.fillStyle='#26384a';ctx.font='600 11px Segoe UI,Arial,sans-serif';ctx.textAlign=col===0?'left':'right';ctx.fillText(labels[col],x+(col===0?8:colWidths[col]-8),y+headHeight/2);x+=colWidths[col];}
 ctx.textBaseline='middle';
 for(let rowIndex=1;rowIndex<rows.length;rowIndex++){x=padding;y=padding+titleHeight+netHeight+mtdHeight+mtdGap+headHeight+(rowIndex-1)*rowHeight;for(let col=0;col<4;col++){const cell=rows[rowIndex][col];ctx.fillStyle=cell.classList.contains('oi-hover-up')?'#d8ead1':cell.classList.contains('oi-hover-down')?'#f8d7d4':'#ffffff';ctx.fillRect(x,y,colWidths[col],rowHeight);ctx.strokeStyle='#cbd5e1';ctx.strokeRect(x,y,colWidths[col],rowHeight);ctx.fillStyle=cell.classList.contains('oi-hover-up')?'#17632b':cell.classList.contains('oi-hover-down')?'#b42318':'#26384a';ctx.font='11px Segoe UI,Arial,sans-serif';ctx.textAlign=col===0?'left':'right';ctx.fillText(cell.textContent.trim(),x+(col===0?8:colWidths[col]-8),y+rowHeight/2);x+=colWidths[col];}}
 const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('Could not render the table image.');
 await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
}
function paintOiHoverSummary(tooltip,symbol,snapshots,monthSnapshots){
 tooltip.replaceChildren();
 const nextExpiry=oiHoverJob==='nse'&&$('expiryRank')?.value==='next',oiField=key=>`${nextExpiry?'Next ':''}${key}`;
 const latestSource=(snapshots[0]?.rows||[]).find(item=>String(item.Symbol||'').toUpperCase()===symbol),latestRow=latestSource?({...latestSource,Future:latestSource[oiField('Future')], 'CE Term':latestSource[oiField('CE Term')], 'PE Term':latestSource[oiField('PE Term')]}):null;
 const futureExpiry=latestSource?.[oiField('Future Expiry')]||'—',optionExpiry=latestSource?.[oiField('Option Expiry')]||'—';
 const title=document.createElement('div');title.className='oi-hover-title';const titleText=document.createElement('span');titleText.textContent=`${symbol} · Fut ${futureExpiry} · Opt ${optionExpiry} · last ${snapshots.length} days`;title.append(titleText);tooltip.append(title);
 const longShortView=latestRow?oiSetupView(latestRow):'Incomplete';const net=document.createElement('div');net.className='oi-hover-net';net.textContent=`Final View: ${longShortView}`;tooltip.append(net);
 const totals=['Fut OI Chg','CE OI Chg','PE OI Chg'].map((key,index)=>monthSnapshots.reduce((sum,saved)=>{const oiRow=(saved.rows||[]).find(item=>String(item.Symbol||'').toUpperCase()===symbol);const expiryField=index===0?'Future Expiry':'Option Expiry',selectedExpiry=index===0?futureExpiry:optionExpiry;const rank=String(oiRow?.[expiryField]||'')===String(selectedExpiry)?'':String(oiRow?.[`Next ${expiryField}`]||'')===String(selectedExpiry)?'Next ':null;const value=rank===null?null:oiRow?.[`${rank}${key}`];return sum+(typeof value==='number'?value:0);},0));
 const monthLine=document.createElement('div');monthLine.className='oi-hover-mtd';const oldest=monthSnapshots[monthSnapshots.length-1],oldDate=oldest?.trade_date||oldest?.scope||'month start',shownDate=shortOiDate(oldDate),expected=Number(state?.config?.nse?.history_days||0),partial=monthSnapshots.length<expected,selectedExpired=futureExpiry<todayIST()||optionExpiry<todayIST();const selectedLabel=document.createElement('span');selectedLabel.className='oi-hover-mtd-label';selectedLabel.textContent=selectedExpired?`Selected · ${nextExpiry?'Next':'Current'} · expired; daily history only`:`${nextExpiry?'Next':'Current'} · ATM · Fut ${shortOiDate(futureExpiry)} · Opt ${shortOiDate(optionExpiry)} · from ${shownDate} · ${monthSnapshots.length} sessions${partial?' · partial':''}`;monthLine.append(selectedLabel);const mtdValues=document.createElement('span');mtdValues.className='oi-hover-mtd-values';if(!selectedExpired){['Fut','CE','PE'].forEach((label,index)=>{const value=document.createElement('strong');value.className=totals[index]>0?'oi-hover-up':totals[index]<0?'oi-hover-down':'oi-hover-flat';value.textContent=`${label} ${fmtOiLakh(totals[index])}`;mtdValues.append(value);});const dominance=document.createElement('strong');dominance.className=`oi-hover-dominance ${oiCePeDominanceClass(totals[1],totals[2])}`;dominance.textContent=oiCePeDominance(totals[1],totals[2]);if(dominance.textContent)mtdValues.append(dominance);monthLine.append(mtdValues);}tooltip.append(monthLine);
 const monthlyExpiry=latestSource?.['Monthly Future Expiry']||latestSource?.['Future Expiry']||'—',monthlyOptionExpiry=latestSource?.['Monthly Option Expiry']||latestSource?.['Next Monthly Option Expiry']||monthlyExpiry;
 const monthlyTotals=[0,0,0],monthlyCounts=[0,0,0];for(const saved of monthSnapshots){const row=(saved.rows||[]).find(item=>String(item.Symbol||'').toUpperCase()===symbol),values=monthlyOiValues(row,monthlyExpiry);values.forEach((value,index)=>{if(value!==null){monthlyTotals[index]+=value;monthlyCounts[index]++;}});}
 const monthlyLine=document.createElement('div');monthlyLine.className='oi-hover-mtd oi-hover-monthly';const monthlyExpired=monthlyExpiry<todayIST()||monthlyOptionExpiry<todayIST();const monthlyLabel=document.createElement('span');monthlyLabel.className='oi-hover-mtd-label';monthlyLabel.textContent=monthlyExpired?`Monthly · expired; totals hidden`:`Monthly · all strikes · Fut ${shortOiDate(monthlyExpiry)} · Opt ${shortOiDate(monthlyOptionExpiry)} · Fut ${monthlyCounts[0]} / Opt ${monthlyCounts[1]} sessions${monthlyCounts[1]<monthSnapshots.length||monthlyCounts[2]<monthSnapshots.length?' · partial':''}`;monthlyLine.append(monthlyLabel);const monthlyValues=document.createElement('span');monthlyValues.className='oi-hover-mtd-values';if(!monthlyExpired){['Fut','CE','PE'].forEach((label,index)=>{const value=document.createElement('strong');value.className=monthlyTotals[index]>0?'oi-hover-up':monthlyTotals[index]<0?'oi-hover-down':'oi-hover-flat';value.textContent=`${label} ${fmtOiLakh(monthlyTotals[index])}`;monthlyValues.append(value);});const dominance=document.createElement('strong');dominance.className=`oi-hover-dominance ${oiCePeDominanceClass(monthlyTotals[1],monthlyTotals[2])}`;dominance.textContent=oiCePeDominance(monthlyTotals[1],monthlyTotals[2]);if(dominance.textContent)monthlyValues.append(dominance);monthlyLine.append(monthlyValues);}tooltip.append(monthlyLine);
 const table=document.createElement('table');
 const head=document.createElement('tr');for(const label of ['Date','Future','CE','PE']){const th=document.createElement('th');th.textContent=label;head.append(th);}table.append(head);
 for(const saved of snapshots){
  const row=(saved.rows||[]).find(item=>String(item.Symbol||'').toUpperCase()===symbol);
  const tr=document.createElement('tr');
  const rawDate=saved.trade_date||saved.scope||saved.collected_at;
  const date=rawDate&&/^\d{4}-\d{2}-\d{2}$/.test(rawDate)?rawDate.split('-').reverse().join('/'):rawDate?new Date(rawDate).toLocaleDateString('en-IN',{timeZone:'Asia/Kolkata',month:'2-digit',day:'2-digit',year:'numeric'}):'—';
  const dateCell=document.createElement('td');dateCell.textContent=date;tr.append(dateCell);
  for(const key of ['Fut OI Chg','CE OI Chg','PE OI Chg']){
   const td=document.createElement('td');const value=row?.[oiField(key)];td.textContent=value==null?'—':fmt(value);td.title=`Daily change in ${nextExpiry?'next':'current'} expiry open interest (contracts)`;
   if(typeof value==='number')td.className=value>0?'oi-hover-up':value<0?'oi-hover-down':'oi-hover-flat';
   tr.append(td);
  }
  table.append(tr);
 }
 if(!snapshots.length){const empty=document.createElement('p');empty.textContent='No saved snapshots found.';tooltip.append(empty);}else{tooltip.append(table);const actions=document.createElement('div');actions.className='oi-hover-actions';
  const copyImage=document.createElement('button');copyImage.type='button';copyImage.className='oi-hover-copy';copyImage.textContent='Copy as image';
  const summaryImageLines=[monthLine,monthlyLine].map(line=>({label:line.querySelector('.oi-hover-mtd-label')?.textContent||'',values:[...line.querySelectorAll('.oi-hover-mtd-values strong')].map(item=>({text:item.textContent,color:getComputedStyle(item).color,bg:getComputedStyle(item).backgroundColor}))}));
  copyImage.addEventListener('click',async event=>{event.preventDefault();event.stopPropagation();try{await copyOiSummaryImage(table,symbol,longShortView,titleText.textContent,summaryImageLines);copyImage.textContent='Image copied';setTimeout(()=>{if(copyImage.isConnected)copyImage.textContent='Copy as image';},1500);}catch{copyImage.textContent='Image copy unavailable';setTimeout(()=>{if(copyImage.isConnected)copyImage.textContent='Copy as image';},1800);}});
  const copyText=document.createElement('button');copyText.type='button';copyText.className='oi-hover-copy';copyText.textContent='Copy text';
  copyText.addEventListener('click',async event=>{event.preventDefault();event.stopPropagation();const rows=[...table.rows].map(row=>[...row.cells].map(cell=>cell.textContent.trim()));const selectedMtdText=monthLine.textContent.trim(),monthlyMtdText=monthlyLine.textContent.trim();const text=`${titleText.textContent}\nFinal View: ${longShortView}\n${selectedMtdText}\n${monthlyMtdText}\n\n`+rows.slice(1).map(row=>`${row[0]}\nFuture: ${row[1]} | CE: ${row[2]} | PE: ${row[3]}`).join('\n\n');try{await navigator.clipboard.writeText(text);copyText.textContent='Text copied';setTimeout(()=>{if(copyText.isConnected)copyText.textContent='Copy text';},1400);}catch{copyText.textContent='Copy unavailable';setTimeout(()=>{if(copyText.isConnected)copyText.textContent='Copy text';},1800);}});
  actions.append(copyImage,copyText);title.append(actions);}
}
async function showOiHoverSummary(cell){
 const tooltip=$('oiHoverSummary');if(!tooltip)return;
 const symbol=String(cell.textContent||'').trim().toUpperCase();if(!symbol)return;
 activeOiHoverCell=cell;tooltip.hidden=false;tooltip.textContent='Loading recent daily data…';
 const rect=cell.getBoundingClientRect();
 const width=Math.min(440,window.innerWidth-20);
 tooltip.style.width=`${width}px`;
 tooltip.style.left=`${Math.max(10,Math.min(rect.left,window.innerWidth-width-10))}px`;
 tooltip.style.top=`${Math.max(10,Math.min(rect.bottom+7,window.innerHeight-205))}px`;
 try{const [snapshots,monthSnapshots]=await Promise.all([loadOiHoverSnapshots(),loadOiMonthSnapshots()]);if(activeOiHoverCell!==cell)return;paintOiHoverSummary(tooltip,symbol,snapshots,monthSnapshots);}
 catch(error){if(activeOiHoverCell===cell)tooltip.textContent=`Could not load snapshot summary: ${error.message}`;}
}
function hideOiHoverSummary(){clearTimeout(oiHoverHideTimer);oiHoverHideTimer=setTimeout(()=>{activeOiHoverCell=null;if($('oiHoverSummary'))$('oiHoverSummary').hidden=true;},120);}
if(page==='oi'){
 $('tbody').addEventListener('mouseover',event=>{const cell=event.target.closest('td.oi-hover-trigger');if(!cell||cell===activeOiHoverCell)return;clearTimeout(oiHoverHideTimer);showOiHoverSummary(cell);});
 $('tbody').addEventListener('mouseout',event=>{const cell=event.target.closest('td.oi-hover-trigger');if(cell&&!cell.contains(event.relatedTarget))hideOiHoverSummary();});
 $('oiHoverSummary').addEventListener('mouseenter',()=>clearTimeout(oiHoverHideTimer));
 $('oiHoverSummary').addEventListener('mouseleave',hideOiHoverSummary);
 window.addEventListener('scroll',()=>{if(activeOiHoverCell)hideOiHoverSummary();},{passive:true});
}

function render() {
  const rows = oiRowsForSelectedExpiry(snapshot?.rows || []);
  const filter=$('filter').value.trim().toUpperCase();
  if (page === 'money') {
    $('atmStrike').textContent='';$('atmSummary').replaceChildren();$('bias').className='money-badge';$('spot').textContent='Spot '+fmt(snapshot?.spot); $('expiryText').textContent=snapshot ? `${snapshot.symbol} · ${snapshot.expiry?.date || '—'}` : 'Waiting for first collection';
    $('ce').textContent=fmt(snapshot?.totals?.CE); $('pe').textContent=fmt(snapshot?.totals?.PE);
    $('bias').textContent=snapshot?.bias?.view || '—';
    $('biasNote').textContent=snapshot ? `Difference ${fmt(snapshot.bias?.difference_pct)}% · selected strikes only` : 'Not measured cash inflow';
    if($('columns').value==='paired')renderMoneyPairs(rows,filter);
    else { $('atmSummaryCard').classList.remove('money-match-pulse'); $('overallSummaryCard').classList.remove('money-match-pulse'); renderTable([['strike','Strike'],['type','Side'],['oi','OI'],['previous_oi','Previous-day OI'],['oi_change','OI Δ day'],['atp','ATP ₹'],['ltp','LTP ₹'],['money','Money ₹ lakh'],['snapshot_oi_change','OI Δ snapshot'],['snapshot_money_change','Money Δ snapshot'],['error','Data issue']], rows.filter(r => String(r.strike).includes(filter))); }
  } else {
    const bull=rows.filter(r => (r['Net View'] || '').includes('Bullish')).length;
    const bear=rows.filter(r => (r['Net View'] || '').includes('Bearish')).length;
    $('count').textContent=snapshot ? fmt(rows.length) : '—'; $('bullish').textContent=snapshot ? fmt(bull) : '—'; $('bearish').textContent=snapshot ? fmt(bear) : '—'; $('mixed').textContent=snapshot ? fmt(rows.length-bull-bear) : '—';
    document.querySelectorAll('.oi-filter-card').forEach(card => { card.setAttribute('aria-pressed', String(card.dataset.oiFilter === oiStatusFilter)); });
    $('coverage').textContent=snapshot ? `${snapshot.source} · ${snapshot.errors?.length || 0} failed${snapshot.requested ? ` / ${snapshot.requested} requested` : ''}` : 'Waiting for collection';
    $('comparison').textContent=snapshot?.comparison || '';
    const termFilter=$('termFilter').value.toLowerCase();
    const setupFilter=$('setupFilter').value;
    const futureTermFilter=$('futureTermFilter').value.toLowerCase();
    const selectedRows=oiRowsForSelectedExpiry(snapshot?.rows || []);
    const filteredRows=selectedRows.filter(r => {
      const view=String(r['Net View'] || '');
      const matchesStatus=oiStatusFilter==='all' || (oiStatusFilter==='bullish' && view.includes('Bullish')) || (oiStatusFilter==='bearish' && view.includes('Bearish')) || (oiStatusFilter==='mixed' && !view.includes('Bullish') && !view.includes('Bearish'));
      const terms=['Future','CE Term','PE Term'].map(key=>String(r[key] || '—').toLowerCase());
      const matchesTerm=termFilter==='all' || (termFilter==='missing' ? terms.some(term=>term==='—') : terms.includes(termFilter));
      const matchesSetup=setupFilter==='all' || r['Our View']===setupFilter;
      const matchesFutureTerm=futureTermFilter==='all' || String(r.Future || '').toLowerCase()===futureTermFilter;
      return matchesStatus && matchesTerm && matchesSetup && matchesFutureTerm && String(r.Symbol || '').toUpperCase().includes(filter);
    });
    renderTable([['Symbol','Instrument'],['Our View','Final View'],['Future Expiry','Future expiry'],['Fut Close','Future price'],['Fut OI','Future OI'],['Fut OI Chg','Future OI Δ day'],['Fut OI Since Snapshot','Future OI Δ snapshot'],['Future Signal','Future: term (view)'],['Option Expiry','Options expiry'],['ATM','ATM'],['CE OI','CE OI'],['CE OI Chg','CE OI Δ day'],['CE Signal','CE: term (view)'],['PE OI','PE OI'],['PE OI Chg','PE OI Δ day'],['PE Signal','PE: term (view)'],['Total CE OI','Total CE OI'],['Total PE OI','Total PE OI'],['PCR','PCR']], filteredRows);
  }
  if (!snapshot) {
    $('freshness').textContent='No saved snapshot for this selection.';$('freshness').title='';
  } else {
    const age=Math.max(0, Math.round((Date.now()-Date.parse(snapshot.collected_at))/1000));
    const interval=job()==='money' ? state.config.money.interval_seconds : state.config.oi.interval_seconds;
    const label=$('history').value ? 'HISTORICAL SNAPSHOT' : job()==='nse' ? `TRADING DATE ${snapshot.trade_date}` : interval===0 ? 'SAVED SNAPSHOT · AUTO REFRESH OFF' : age > interval*2 ? 'STALE SNAPSHOT' : 'LATEST SNAPSHOT';
    const expiryView=page==='oi'&&job()==='nse'?` · ${$('expiryRank').value==='next'?'Next':'Current'} expiry view`:'';
    const needsNextRefresh=page==='oi'&&job()==='nse'&&$('expiryRank').value==='next'&&!(snapshot.rows||[]).some(row=>row['Next Future Expiry']||row['Next Option Expiry']);
    $('freshness').textContent=`${label} · ${snapshot.source} · Collected ${when(snapshot.collected_at)} IST · ${age < 3600 ? Math.floor(age/60)+'m' : Math.floor(age/3600)+'h'} old${snapshot.previous_snapshot_at ? ' · Compared with '+when(snapshot.previous_snapshot_at) : ''}${expiryView}${needsNextRefresh?' · Collect now to load the next expiry':''}`;
    $('freshness').title=$('freshness').textContent;
    if(page==='money'){
      const updated=new Date(snapshot.collected_at);
      const time=updated.toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',hour12:false});
      const day=v=>v.toLocaleDateString('en-IN',{timeZone:'Asia/Kolkata'});
      const date=day(updated)===day(new Date())?'':updated.toLocaleDateString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short'})+' · ';
      const mode=$('history').value?'Historical':interval===0?'Auto off':`Auto ${interval/60}m`;
      $('freshness').textContent=`Updated ${date}${time} IST · ${mode}`;
    }

  }
  const fragments=document.createDocumentFragment();
  for (const e of snapshot?.errors || []) { const p=document.createElement('p'); p.textContent=`${e.symbol}: ${e.error}`; fragments.append(p); }
  $('rowErrors')?.replaceChildren(fragments);
}
function moneyOiSymbol(){
 const raw=String($('symbol')?$('symbol').value:state?.config?.money?.symbol||'').trim().toUpperCase();
 const known={'NSE:NIFTY50-INDEX':'NIFTY','NSE:NIFTYBANK-INDEX':'BANKNIFTY','NSE:FINNIFTY-INDEX':'FINNIFTY','NSE:MIDCPNIFTY-INDEX':'MIDCPNIFTY','NSE:NIFTYNXT50-INDEX':'NIFTYNXT50','BSE:SENSEX-INDEX':'SENSEX'};
 return known[raw]||raw.split(':').pop().replace(/-(?:INDEX|EQ)$/,'');
}
function paintMoneyRecentOi(symbol,snapshots,monthSnapshots=[]){
 $('moneyRecentOiSymbol').textContent=symbol||'—';$('moneyRecentOiRows').replaceChildren();
 const matches=snapshots.map(saved=>({saved,row:(saved.rows||[]).find(item=>String(item.Symbol||'').toUpperCase()===symbol)})).filter(item=>item.row);
 const totals=['Fut OI Chg','CE OI Chg','PE OI Chg'].map(key=>monthSnapshots.reduce((sum,saved)=>{const value=(saved.rows||[]).find(item=>String(item.Symbol||'').toUpperCase()===symbol)?.[key];return sum+(typeof value==='number'?value:0);},0));
 const oldest=monthSnapshots[monthSnapshots.length-1],from=oldest?.trade_date||oldest?.scope||'month start',shownDate=shortOiDate(from),expected=Number(state?.config?.nse?.history_days||0),partial=monthSnapshots.length<expected,summary=$('moneyRecentOiMtd'),latest=monthSnapshots[0]?.rows?.find(item=>String(item.Symbol||'').toUpperCase()===symbol),selectedExpired=String(latest?.['Future Expiry']||'')<todayIST()||String(latest?.['Option Expiry']||'')<todayIST();summary.replaceChildren();const selectedLabel=document.createElement('span');selectedLabel.className='oi-hover-mtd-label';selectedLabel.textContent=selectedExpired?'Current · expired; daily history only':`Current · ATM · Fut ${shortOiDate(latest?.['Future Expiry'])} · Opt ${shortOiDate(latest?.['Option Expiry'])} · from ${shownDate} · ${monthSnapshots.length} sessions${partial?' · partial':''}`;summary.append(selectedLabel);const values=document.createElement('span');values.className='oi-hover-mtd-values';if(!selectedExpired){['Fut','CE','PE'].forEach((label,index)=>{const value=document.createElement('strong');value.className=totals[index]>0?'oi-hover-up':totals[index]<0?'oi-hover-down':'oi-hover-flat';value.textContent=`${label} ${fmtOiLakh(totals[index])}`;values.append(value);});const dominance=document.createElement('strong');dominance.className=`oi-hover-dominance ${oiCePeDominanceClass(totals[1],totals[2])}`;dominance.textContent=oiCePeDominance(totals[1],totals[2]);if(dominance.textContent)values.append(dominance);summary.append(values);}
 const monthlyExpiry=latest?.['Monthly Future Expiry']||latest?.['Future Expiry']||'—',monthlyOptionExpiry=latest?.['Monthly Option Expiry']||latest?.['Next Monthly Option Expiry']||monthlyExpiry,monthlyTotals=[0,0,0],monthlyCounts=[0,0,0];for(const saved of monthSnapshots){const row=(saved.rows||[]).find(item=>String(item.Symbol||'').toUpperCase()===symbol),values=monthlyOiValues(row,monthlyExpiry);values.forEach((value,index)=>{if(value!==null){monthlyTotals[index]+=value;monthlyCounts[index]++;}});}const monthlyExpired=monthlyExpiry<todayIST()||monthlyOptionExpiry<todayIST(),monthlySummary=$('moneyRecentOiMonthly');monthlySummary.replaceChildren();const monthlyLabel=document.createElement('span');monthlyLabel.className='oi-hover-mtd-label';monthlyLabel.textContent=monthlyExpired?`Monthly · expired; totals hidden`:`Monthly · all strikes · Fut ${shortOiDate(monthlyExpiry)} · Opt ${shortOiDate(monthlyOptionExpiry)} · Fut ${monthlyCounts[0]} / Opt ${monthlyCounts[1]} sessions${monthlyCounts[0]<monthSnapshots.length||monthlyCounts[1]<monthSnapshots.length||monthlyCounts[2]<monthSnapshots.length?' · partial':''}`;monthlySummary.append(monthlyLabel);const monthlyValues=document.createElement('span');monthlyValues.className='oi-hover-mtd-values';if(!monthlyExpired){['Fut','CE','PE'].forEach((label,index)=>{const value=document.createElement('strong');value.className=monthlyTotals[index]>0?'oi-hover-up':monthlyTotals[index]<0?'oi-hover-down':'oi-hover-flat';value.textContent=`${label} ${fmtOiLakh(monthlyTotals[index])}`;monthlyValues.append(value);});const dominance=document.createElement('strong');dominance.className=`oi-hover-dominance ${oiCePeDominanceClass(monthlyTotals[1],monthlyTotals[2])}`;dominance.textContent=oiCePeDominance(monthlyTotals[1],monthlyTotals[2]);if(dominance.textContent)monthlyValues.append(dominance);monthlySummary.append(monthlyValues);}
 if(!matches.length){$('moneyRecentOiNet').textContent='—';$('moneyRecentOiStatus').textContent=`No recent Open Interest history found for ${symbol}.`;return;}
 $('moneyRecentOiNet').textContent=oiSetupView(matches[0].row);
 for(const {saved,row} of matches){
  const tr=document.createElement('tr');const rawDate=saved.trade_date||saved.scope||saved.collected_at;const date=rawDate&&/^\d{4}-\d{2}-\d{2}$/.test(rawDate)?rawDate.split('-').reverse().join('/'):rawDate?new Date(rawDate).toLocaleDateString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'2-digit',year:'numeric'}):'—';
  const values=[date,row['Fut OI Chg'],row['CE OI Chg'],row['PE OI Chg']];
  values.forEach((value,index)=>{const td=document.createElement('td');td.textContent=index===0?value:value==null?'—':fmt(value);if(index>0&&typeof value==='number')td.className=value>0?'oi-hover-up':value<0?'oi-hover-down':'oi-hover-flat';tr.append(td);});$('moneyRecentOiRows').append(tr);
 }
 $('moneyRecentOiStatus').textContent=`Showing the latest saved observation from ${matches.length} distinct day${matches.length===1?'':'s'}.`;
}
async function loadMoneyRecentOi(force=false){
 if(page!=='money'||!$('moneyRecentOi'))return;
 const symbol=moneyOiSymbol();if(!symbol){$('moneyRecentOiStatus').textContent='Select an underlying to view recent Open Interest.';return;}
 const sourceStamp=state?.jobs?.nse?.last_success||'';
 if(moneyRecentOiReady&&sourceStamp===moneyRecentOiSourceStamp){moneyRecentOiSymbol=symbol;paintMoneyRecentOi(symbol,moneyRecentOiSnapshots,moneyRecentOiMonthSnapshots);return;}
 moneyRecentOiSymbol=symbol;moneyRecentOiSourceStamp=sourceStamp;$('moneyRecentOiSymbol').textContent=symbol;$('moneyRecentOiStatus').textContent='Loading recent OI history…';
 if(moneyRecentOiLoading){try{await moneyRecentOiLoading;}catch{}if(symbol!==moneyOiSymbol())return;}
 moneyRecentOiLoading=(async()=>{
  const history=await api('/api/history?job=nse');const ordered=[...history].sort((a,b)=>String(b.scope||b.stamp).localeCompare(String(a.scope||a.stamp)));const days=new Set();
  const selected=ordered.filter(item=>{const day=String(item.scope||item.stamp).slice(0,10);if(days.has(day))return false;days.add(day);return true;}).slice(0,3);
  const key=selected.map(item=>item.id).join(',');
  if(key!==moneyRecentOiCacheKey){const results=await Promise.all(selected.map(item=>api(`/api/snapshot?job=nse&id=${encodeURIComponent(item.id)}`)));moneyRecentOiSnapshots=results.map(result=>result.snapshot).filter(Boolean);moneyRecentOiCacheKey=key;}
  const month=String(ordered[0]?.scope||ordered[0]?.stamp||'').slice(0,7),monthHistory=month?await api(`/api/history?job=nse&month=${encodeURIComponent(month)}`):[];
  const monthKey=monthHistory.map(item=>item.id).join(',');if(monthKey!==moneyRecentOiMonthCacheKey){const monthResults=await Promise.all(monthHistory.map(item=>api(`/api/snapshot?job=nse&id=${encodeURIComponent(item.id)}`)));moneyRecentOiMonthSnapshots=monthResults.map(result=>result.snapshot).filter(Boolean);moneyRecentOiMonthCacheKey=monthKey;}
  moneyRecentOiReady=true;return moneyRecentOiSnapshots;
 })();
 try{const snapshots=await moneyRecentOiLoading;if(symbol!==moneyOiSymbol())return;paintMoneyRecentOi(symbol,snapshots,moneyRecentOiMonthSnapshots);}
 catch(error){$('moneyRecentOiStatus').textContent=`Could not load recent OI history: ${error.message}`;}
 finally{moneyRecentOiLoading=null;}
}
if(page==='money'){
 $('symbol')?.addEventListener('change',()=>loadMoneyRecentOi());
}

async function loadMoneyMovements() {
  if(page!=='money'||movementPolling||!$('movementRows'))return;
  movementPolling=true;
  try{
    const symbol=(state?.config?.money?.symbol||$('symbol').value||'').trim().toUpperCase();
    const expiry=String(state?.config?.money?.expiry||snapshot?.expiry?.expiry||snapshot?.expiry?.date||$('expiry').value||'').trim();
    $('movementThreshold').textContent=fmt(Number($('moneyThreshold').value));
    const selectedThreshold=Number($('moneyThreshold').value);
    if(!symbol){$('movementStatus').textContent='Select an instrument to view its movement history.';return;}
    const q=new URLSearchParams({symbol,expiry});
    const result=await api('/api/money/movements?'+q);
    const body=$('movementRows');body.replaceChildren();
    const highlightedRecentDrops=new Set();
    let highlightedRecentCERise=false;
    for(const event of result.events||[]){
      const tr=document.createElement('tr');
      const side=String(event.side||'').toUpperCase();
      if(event.direction==='fell'&&(side==='CE'||side==='PE')&&Math.abs(Number(event.threshold)-selectedThreshold)<1e-9&&!highlightedRecentDrops.has(side)){
        tr.classList.add(side==='CE'?'movement-first-ce-drop':'movement-first-pe-drop');
        tr.title=side==='CE'?'Most recent CE drop · bullish':'Most recent PE drop · bearish';
        highlightedRecentDrops.add(side);
      }
      const firstCERise=event.direction==='rose'&&side==='CE'&&Math.abs(Number(event.threshold)-selectedThreshold)<1e-9&&!highlightedRecentCERise;
      if(firstCERise){tr.classList.add('movement-first-ce-rise');tr.title='First CE Rose in the displayed history';highlightedRecentCERise=true;}
      const moveLabel=event.direction==='fell'?`${side} Drop · ${side==='CE'?'Bullish':'Bearish'}`:side==='CE'?'CE Rose':`${side} rose`;
      const cells=[when(event.stamp),fmt(event.strike),moveLabel,`${event.change_pct>0?'+':''}${fmt(event.change_pct)}%`,fmt(event.before),fmt(event.after),`${fmt(event.threshold)}%`];
      for(const value of cells){const td=document.createElement('td');td.textContent=value;tr.append(td);}
      body.append(tr);
    }
    $('movementStatus').textContent=result.events?.length?`${result.events.length} most recent threshold moves · threshold ${fmt(Number($('moneyThreshold').value))}%`:'No threshold moves recorded for this instrument and expiry yet.';
  }catch(e){
    $('movementStatus').textContent=e.message==='Not found'
      ?'Movement history is unavailable in the running server. Restart server.py, then reload this page.'
      :`Movement history could not be loaded: ${e.message}`;
  }
  finally{movementPolling=false;}
}
async function poll() {
  if (polling) return;
  polling=true;
  let requestedJob=job(); const selected=$('history').value;
  try {
    let [nextState, data, history] = await Promise.all([api('/api/state'), api(`/api/snapshot?job=${requestedJob}${selected ? '&id='+encodeURIComponent(selected) : ''}`), api(`/api/history?job=${requestedJob}`)]);
    if (requestedJob !== job() || selected !== $('history').value) return;
    if (!initialized && page === 'oi' && !sourceChosen && !data.snapshot && requestedJob === 'oi') {
      const [daily, dailyHistory] = await Promise.all([api('/api/snapshot?job=nse'), api('/api/history?job=nse')]);
      if (!sourceChosen && daily.snapshot) {
        $('source').value='nse'; requestedJob='nse'; data=daily; history=dailyHistory;
        notice('message','Showing saved NSE daily data because no Fyers snapshot is available yet. Select Fyers intraday to follow that scan.');
      }
    }
    state=nextState; snapshot=data.snapshot;
    if(page==='oi'){oiHoverHistory=history;oiHoverJob=requestedJob;oiHoverCacheKey='';oiHoverSnapshots=[];oiHoverLoading=null;}
    if(page==='money'&&state.money_alerts){const a=state.money_alerts;if(!moneyAlertsInitialized){$('moneyAlertEnabled').checked=a.enabled;$('moneyThreshold').value=a.threshold;$('moneyNear').value=a.near;$('moneyStep').value=a.step;if($('movementScope')){$('movementScope').value=a.movement_mode||'top';$('movementCount').value=a.movement_count??4;syncMovementScopeUI();saveMovementScope();}moneyAlertsInitialized=true;}if($('moneyAlertStatus'))$('moneyAlertStatus').textContent=a.last_delivery?'Last alert: '+a.last_delivery.state+' · '+when(a.last_delivery.stamp):a.enabled?'Waiting for next complete collection':a.telegram_ready?'Alerts off · Telegram configured':'Telegram credentials missing';}

    if (!initialized) { fillSettings(state.config); initialized=true; }
    $('mode').textContent=state.demo ? 'DEMO · SYNTHETIC DATA' : state.in_session ? 'LIVE MODE · IN SESSION' : 'LIVE MODE · OUTSIDE SESSION';
    $('history').replaceChildren(option('', 'Latest'));
    for (const item of history) $('history').append(option(item.id, requestedJob==='nse' ? `${item.scope} · daily` : when(item.stamp)));
    $('history').value=selected;
    updateExpiries();
    if($('expiryRankLabel'))$('expiryRankLabel').hidden=requestedJob!=='nse';
    const active=Object.entries(state.jobs).filter(([name,s])=>name===requestedJob && s.running);
    $('progress').textContent=active.map(([name,s])=>`${name.toUpperCase()}: ${s.progress || 'Collecting…'}`).join(' · ');
    $('progress').hidden=!active.length;
    const current=state.jobs[requestedJob]; $('refresh').disabled=current.running;
    $('refresh').textContent=current.running ? 'Collecting…' : 'Collect now';
    notice('error', current.error, true);
    if($('status'))$('status').textContent=`FYERS REQUESTS TODAY (V2): ${state.request_usage ?? 0} / ${state.config.requests_per_day} budget\nBudgets apply to V2 only; other apps share the broker allowance.\n\n`+Object.entries(state.jobs).map(([name,s]) => `${name.toUpperCase()} ${s.running ? '• collecting' : '• idle'}\n${s.progress || 'Waiting for schedule'}\nLast successful run: ${when(s.last_success)}\nNext: ${s.next_run || 'Pending'}${s.error ? '\nError: '+s.error : ''}`).join('\n\n');
    render();
    if(page==='money')loadMoneyRecentOi();
    if(page==='money'&&$('moneyMovementPanel').open)loadMoneyMovements();
  } catch (e) { notice('error', `Connection problem: ${e.message}. Displayed data may be stale.`, true); }
  finally { polling=false; }
}
$('settings').addEventListener('submit', async event => {
  event.preventDefault();
  if(page==='money'&&$('expiry').disabled){notice('message','Please wait for the expiry list to load.');return;}
  try {
    const cfg=(await api('/api/state')).config;
    if (page === 'money') {
      cfg.money={...cfg.money, symbol:$('symbol').value.trim().toUpperCase(), expiry:$('expiry').value, strikes:Number($('strikes').value), interval_seconds:Number($('interval').value), oi_multiplier:1};
    } else {
      cfg.oi={...cfg.oi, min_price:Number($('oiMinPrice').value), universe:$('universe').value, mode:$('schedule').value, interval_seconds:Number($('interval').value)*60, daily_time:$('dailyTime').value, symbols:$('symbols').value.split(/[\s,]+/).filter(Boolean).map(s=>s.toUpperCase())};
      cfg.nse={...cfg.nse, enabled:$('nseEnabled').checked, daily_time:$('nseTime').value, retry_seconds:Number($('nseRetry').value)*60, history_days:Number($('nseDays').value)}; cfg.retention_days=Number($('retentionDays').value);
    }
    await api('/api/config',cfg); $('history').value='';
    notice('message',page === 'oi' ? 'Settings saved. Snapshots older than the selected retention were removed; click Collect now to fetch new data.' : cfg.money.interval_seconds===0 ? 'Settings saved. Auto refresh is Off; use Collect now when needed.' : 'Settings saved. Money collection follows the selected interval during market hours.');
    await poll();
  } catch(e) { notice('error',e.message); }
});
$('refresh').addEventListener('click',async()=>{
  try { if(page==='oi'){const min=Number($('oiMinPrice').value);if(!$('oiMinPrice').value || !Number.isFinite(min) || min<0 || min>10000000)throw new Error('Enter a minimum price between 0 and 10000000.');const cfg=structuredClone(state.config);cfg.oi.min_price=min;const saved=await api('/api/config',cfg);state.config=saved.config;} const result=await api('/api/refresh',{job:job()}); notice('message',result.queued ? 'Collection queued. This page will update when the snapshot is saved.' : 'This collector is already running or queued.'); $('history').value=''; await poll(); }
  catch(e) { notice('error',e.message); }
});
$('filter').addEventListener('input',render);
$('termFilter')?.addEventListener('change',render);
$('setupFilter')?.addEventListener('change',render);
$('futureTermFilter')?.addEventListener('change',render);
$('expiryRank')?.addEventListener('change',render);
$('columns').addEventListener('change',render);
document.querySelectorAll('.oi-filter-card[data-oi-filter]').forEach(card=>card.addEventListener('click',()=>{
  const selected=card.dataset.oiFilter;
  oiStatusFilter=selected==='all'||oiStatusFilter===selected?'all':selected;
  render();
}));
$('history').addEventListener('change',poll);
if ($('source')) $('source').addEventListener('change',()=>{ sourceChosen=true; $('history').value=''; snapshot=null; render(); poll(); });
if ($('symbol')) $('symbol').addEventListener('change',()=>{ loadInstrumentExpiries();if(page==='money'){$('moneyStep').value=$('symbol').value.toUpperCase().startsWith('MCX:NATURALGAS')?'5':'100';$('moneyStep').dispatchEvent(new Event('change'));} });
$('export').addEventListener('click',()=>{
  if (!exportRows.length) return notice('message','No rows to export.');
  const csvCell = value => { let s=value === null || value === undefined ? '' : String(value); if (typeof value === 'string' && /^[=+\-@\t\r]/.test(s)) s="'"+s; return '"'+s.replace(/"/g,'""')+'"'; };
  const lines=[exportColumns.map(c=>csvCell(c[1])).join(','), ...exportRows.map(r=>exportColumns.map(c=>csvCell(r[c[0]])).join(','))];
  const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a'); a.href=url; a.download=`${state.demo ? 'DEMO-' : ''}${job()}-${snapshot?.id || 'snapshot'}.csv`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
});
if(page==='money'){
 $('sendMoneyNow').onclick=async()=>{
  const button=$('sendMoneyNow');
  if(!snapshot || snapshot.symbol!==$('symbol').value.trim().toUpperCase() || ($('expiry').value && String(snapshot.expiry?.expiry)!==$('expiry').value)){notice('error','Save the instrument settings and collect its Money data first.');return;}
  button.disabled=true;
  try{const result=await api('/api/money/send-alert',{snapshot_id:snapshot.id,threshold:Number($('moneyThreshold').value),near:Number($('moneyNear').value),step:Number($('moneyStep').value),top:0});notice('message',result.message);}
  catch(e){notice('error',e.message);}finally{button.disabled=false;}
 };
 for(const id of ['moneyAlertEnabled','moneyThreshold','moneyNear','moneyStep'])$(id).addEventListener('input',()=>{moneyAlertsInitialized=true;});
 let alertSaveTimer,alertSaveQueue=Promise.resolve(),alertEdit=0;
 const saveAlertsAutomatically=(immediate=false)=>{
  clearTimeout(alertSaveTimer);const revision=++alertEdit;
  const save=()=>{
   if(!['moneyThreshold','moneyStep'].every(id=>$(id).value!==''&&$(id).checkValidity()))return;
   const values={enabled:$('moneyAlertEnabled').checked,threshold:Number($('moneyThreshold').value),near:Number($('moneyNear').value),step:Number($('moneyStep').value)};
   alertSaveQueue=alertSaveQueue.then(async()=>{
    if(revision!==alertEdit)return;
    try{const saved=await api('/api/money/alerts',values);if(state)state.money_alerts=saved;if(revision===alertEdit)notice('message',saved.enabled?'Auto alerts on · settings saved':'Auto alerts off · settings saved');}
    catch(e){if(revision===alertEdit){if(state?.money_alerts)$('moneyAlertEnabled').checked=state.money_alerts.enabled;notice('error','Alert settings were not saved: '+e.message);}}
   });
  };
  if(immediate)save();else alertSaveTimer=setTimeout(save,500);
 };
 for(const id of ['moneyAlertEnabled','moneyNear'])$(id).addEventListener('change',()=>saveAlertsAutomatically(true));
 for(const id of ['moneyThreshold','moneyStep']){$(id).addEventListener('input',()=>saveAlertsAutomatically());$(id).addEventListener('change',()=>saveAlertsAutomatically(true));}
 const ids=['moneyTop'];
 try{const saved=JSON.parse(localStorage.getItem('money-display-options')||'{}');for(const id of ids)if(saved[id]!=null)$(id).value=saved[id];}catch{}
 const changed=()=>{try{localStorage.setItem('money-display-options',JSON.stringify(Object.fromEntries(ids.map(id=>[id,$(id).value]))));}catch{}render();if($('movementScope')?.value==='top')saveMovementScope();};
 for(const id of [...ids,'moneyThreshold','moneyStep'])$(id).addEventListener('input',()=>{moneyAlertsInitialized=true;changed();});
 for(const button of document.querySelectorAll('[data-diff]'))button.onclick=()=>{$('moneyThreshold').value=button.dataset.diff;moneyAlertsInitialized=true;changed();saveAlertsAutomatically(true);};
 for(const button of document.querySelectorAll('[data-top]'))button.onclick=()=>{$('moneyTop').value=button.dataset.top;changed();};
}
function syncMovementScopeUI(){
 const mode=$('movementScope')?.value||'top';
 $('movementCountWrap').hidden=mode!=='atm';
 const top=Number($('moneyTop')?.value||0);
 $('movementScopeNote').textContent=mode==='top'?(top?`Uses the table's top ${top} CE/PE strikes`:'Top strikes is set to All; tracking every strike'):mode==='all'?'Tracks every collected strike':`Tracks ATM ± ${$('movementCount').value||3} strike levels`;
}
let movementScopeTimer;
function saveMovementScope(){
 if(!$('movementScope'))return;
 const mode=$('movementScope').value;
 const count=mode==='top'?Math.max(0,Math.min(100,Math.floor(Number($('moneyTop').value)||0))):mode==='atm'?Math.max(1,Math.min(100,Math.floor(Number($('movementCount').value)||3))):1;
 syncMovementScopeUI();clearTimeout(movementScopeTimer);
 movementScopeTimer=setTimeout(async()=>{try{await api('/api/money/movement-settings',{mode,count});if(state?.money_alerts){state.money_alerts.movement_mode=mode;state.money_alerts.movement_count=count;}if($('moneyMovementPanel').open)loadMoneyMovements();}catch(e){$('movementStatus').textContent=`Could not save tracking scope: ${e.message}`;}},250);
}
if(page==='money'){
 $('movementScope')?.addEventListener('change',saveMovementScope);
 $('movementCount')?.addEventListener('input',saveMovementScope);
}
if(page==='money')$('clearMovementHistory')?.addEventListener('click',async event=>{
 event.preventDefault();event.stopPropagation();
 if(!window.confirm('Clear all saved Money threshold movement history? This does not change snapshots or alert settings.'))return;
 const button=$('clearMovementHistory');button.disabled=true;
 try{const result=await api('/api/money/movements/clear',{});await loadMoneyMovements();$('movementStatus').textContent=`Cleared ${fmt(result.deleted)} saved threshold movement records.`;}
 catch(e){$('movementStatus').textContent=`Could not clear movement history: ${e.message}`;}
 finally{button.disabled=false;}
});
if(page==='money')$('moneyMovementPanel').addEventListener('toggle',()=>{if($('moneyMovementPanel').open)loadMoneyMovements();});
poll(); setInterval(poll,5000);
