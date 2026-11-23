'use strict';
const $ = id => document.getElementById(id);
const page = document.body.dataset.page;
let sourceChosen = false, moneyAlertsInitialized=false;
let theme = 'dark';
try { theme = localStorage.getItem('market-desk-theme') || 'dark'; } catch {}
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
let state = null, snapshot = null, initialized = false, polling = false, exportRows = [], exportColumns = [];
const fmt = v => v === null || v === undefined ? '—' : typeof v === 'number' ? v.toLocaleString('en-IN', {maximumFractionDigits: 2}) : String(v);
const when = v => v ? new Date(v).toLocaleString('en-IN', {timeZone:'Asia/Kolkata', hour12:false}) : '—';
const job = () => page === 'money' ? 'money' : $('source').value;
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
    $('universe').value=cfg.oi.universe; $('schedule').value=cfg.oi.mode; $('interval').value=cfg.oi.interval_seconds / 60;
    $('dailyTime').value=cfg.oi.daily_time; $('symbols').value=cfg.oi.symbols.join(', ');
    $('nseEnabled').checked=cfg.nse.enabled; $('nseTime').value=cfg.nse.daily_time;
    $('nseRetry').value=cfg.nse.retry_seconds / 60; $('nseDays').value=cfg.nse.history_days;
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
    const keys = page === 'money' ? ['strike','type','oi_change','atp','money','snapshot_money_change'] : ['Symbol','Net View','Fut Close','Fut OI Chg','Fut View','ATM','CE OI Chg','CE View','PE OI Chg','PE View','PCR'];
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
      if (typeof row[key] === 'number' && (key.toLowerCase().includes('change') || key.includes('Chg') || key.includes('Since') || key === 'money')) td.className=row[key] < 0 ? 'negative' : row[key] > 0 ? 'positive' : '';
      if (String(row[key]).includes('Bullish')) td.className='positive';
      if (String(row[key]).includes('Bearish')) td.className='negative';
      if (row.error) td.title=row.error;
      tr.append(td);
    }
    fragment.append(tr);
  }
  $('tbody').replaceChildren(fragment); $('empty').hidden=rows.length > 0;
  $('empty').textContent = snapshot ? 'No rows match the current filter.' : 'No saved snapshot for this source yet. Collection progress is shown above; select NSE daily bhavcopy to view saved daily reports.';
}
function moneyView(ce,pe) {
  if(ce==null||pe==null)return {view:'Incomplete',pct:null};
  if(ce<0||pe<0)return {view:'Unwinding / mixed',pct:null};
  const divisor=$('moneyBase').value==='min'?Math.min(ce,pe):Math.max(ce,pe);
  const pct=divisor>0?Math.abs(ce-pe)/divisor*100:ce===pe?0:null;
  return {pct,view:ce===pe||pct!==null&&pct<Math.max(0,Number($('moneyThreshold').value)||0)?'Neutral':pe>ce?'Bullish':'Bearish'};
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
  let ceTop=new Set(),peTop=new Set();
  const top=Math.max(0,Math.floor(Number($('moneyTop').value)||0));
  if(top){ceTop=new Set([...visible].filter(r=>r.ce!=null).sort((a,b)=>b.ce-a.ce).slice(0,top).map(r=>r.strike));peTop=new Set([...visible].filter(r=>r.pe!=null).sort((a,b)=>b.pe-a.pe).slice(0,top).map(r=>r.strike));visible=visible.filter(r=>ceTop.has(r.strike)||peTop.has(r.strike));}
  visible=visible.filter(r=>String(r.strike).includes(filter)).sort((a,b)=>b.strike-a.strike).map(r=>({...r,...moneyView(r.ce,r.pe)}));
  const columns=[['strike','Strike'],['ce','CE Money ₹ lakh'],['pe','PE Money ₹ lakh'],['ceBep','CE BEP ₹'],['peBep','PE BEP ₹'],['pct','Diff %'],['view','View']];
  renderTable(columns,visible);
  [...$('tbody').children].forEach((tr,i)=>{const r=visible[i];if(ceTop.has(r.strike))tr.children[1].classList.add('money-top');if(peTop.has(r.strike))tr.children[2].classList.add('money-top');tr.lastChild.classList.add(r.view==='Bullish'?'money-bull':r.view==='Bearish'?'money-bear':'money-neutral');if(r.strike===atm?.strike){tr.classList.add('money-atm');const badge=document.createElement('span');badge.className='money-atm-badge';badge.textContent='ATM';tr.firstChild.append(' ',badge);}if(r.ceBep!=null&&snapshot.spot>=r.ceBep)tr.children[3].classList.add('money-bep-hit');if(r.peBep!=null&&snapshot.spot<=r.peBep)tr.children[4].classList.add('money-bep-hit');const viewBadge=document.createElement('span');viewBadge.className='money-badge '+(r.view==='Bullish'?'money-bull':r.view==='Bearish'?'money-bear':'money-neutral');viewBadge.textContent=r.view;tr.lastChild.className='';tr.lastChild.replaceChildren(viewBadge);});
  const total=key=>visible.length&&visible.every(r=>r[key]!=null)?visible.reduce((sum,r)=>sum+r[key],0):null;
  const ce=total('ce'),pe=total('pe'),view=moneyView(ce,pe);
  $('atmStrike').textContent=atm?' → ATM '+fmt(atm.strike):'';$('atmSummary').replaceChildren();if(atm){const j=moneyView(atm.ce,atm.pe);$('atmSummary').append(`CE ${fmt(atm.ce)} vs PE ${fmt(atm.pe)} `);const gap=document.createElement('strong');gap.className='money-gap-badge';gap.textContent=j.pct==null?'Gap —':`${fmt(j.pct)}%`;gap.title='Difference between CE and PE Money';$('atmSummary').append(gap,' ');const tag=document.createElement('span');tag.className='money-badge '+(j.view==='Bullish'?'money-bull':j.view==='Bearish'?'money-bear':'money-neutral');tag.textContent=j.view;$('atmSummary').append(tag);}$('ce').textContent=fmt(ce);$('pe').textContent=fmt(pe);$('bias').textContent=view.view;$('bias').className='money-badge '+(view.view==='Bullish'?'money-bull':view.view==='Bearish'?'money-bear':'money-neutral');$('biasNote').textContent=`Difference ${fmt(view.pct)}% · ${visible.length} displayed strikes`;
}

function render() {
  const rows = snapshot?.rows || [];
  const filter=$('filter').value.trim().toUpperCase();
  if (page === 'money') {
    $('atmStrike').textContent='';$('atmSummary').replaceChildren();$('bias').className='money-badge';$('spot').textContent='Spot '+fmt(snapshot?.spot); $('expiryText').textContent=snapshot ? `${snapshot.symbol} · ${snapshot.expiry?.date || '—'}` : 'Waiting for first collection';
    $('ce').textContent=fmt(snapshot?.totals?.CE); $('pe').textContent=fmt(snapshot?.totals?.PE);
    $('bias').textContent=snapshot?.bias?.view || '—';
    $('biasNote').textContent=snapshot ? `Difference ${fmt(snapshot.bias?.difference_pct)}% · selected strikes only` : 'Not measured cash inflow';
    if($('columns').value==='paired')renderMoneyPairs(rows,filter);
    else renderTable([['strike','Strike'],['type','Side'],['oi','OI'],['previous_oi','Previous-day OI'],['oi_change','OI Δ day'],['atp','ATP ₹'],['ltp','LTP ₹'],['money','Money ₹ lakh'],['snapshot_oi_change','OI Δ snapshot'],['snapshot_money_change','Money Δ snapshot'],['error','Data issue']], rows.filter(r => String(r.strike).includes(filter)));
  } else {
    const bull=rows.filter(r => (r['Net View'] || '').includes('Bullish')).length;
    const bear=rows.filter(r => (r['Net View'] || '').includes('Bearish')).length;
    $('count').textContent=snapshot ? fmt(rows.length) : '—'; $('bullish').textContent=snapshot ? fmt(bull) : '—'; $('bearish').textContent=snapshot ? fmt(bear) : '—'; $('mixed').textContent=snapshot ? fmt(rows.length-bull-bear) : '—';
    $('coverage').textContent=snapshot ? `${snapshot.source} · ${snapshot.errors?.length || 0} failed${snapshot.requested ? ` / ${snapshot.requested} requested` : ''}` : 'Waiting for collection';
    $('comparison').textContent=snapshot?.comparison || '';
    renderTable([['Symbol','Instrument'],['Net View','Net view'],['Fut Close','Future price'],['Fut OI','Future OI'],['Fut OI Chg','Future OI Δ day'],['Fut OI Since Snapshot','Future OI Δ snapshot'],['Future','Future term'],['Fut View','Future view'],['ATM','ATM'],['CE OI','CE OI'],['CE OI Chg','CE OI Δ day'],['CE Term','CE term'],['CE View','CE view'],['PE OI','PE OI'],['PE OI Chg','PE OI Δ day'],['PE Term','PE term'],['PE View','PE view'],['Total CE OI','Total CE OI'],['Total PE OI','Total PE OI'],['PCR','PCR'],['Future Expiry','Future expiry'],['Option Expiry','Option expiry']], rows.filter(r => r.Symbol.toUpperCase().includes(filter)));
  }
  if (!snapshot) {
    $('freshness').textContent='No saved snapshot for this selection.';$('freshness').title='';
  } else {
    const age=Math.max(0, Math.round((Date.now()-Date.parse(snapshot.collected_at))/1000));
    const interval=job()==='money' ? state.config.money.interval_seconds : state.config.oi.interval_seconds;
    const label=$('history').value ? 'HISTORICAL SNAPSHOT' : job()==='nse' ? `TRADING DATE ${snapshot.trade_date}` : interval===0 ? 'SAVED SNAPSHOT · AUTO REFRESH OFF' : age > interval*2 ? 'STALE SNAPSHOT' : 'LATEST SNAPSHOT';
    $('freshness').textContent=`${label} · ${snapshot.source} · Collected ${when(snapshot.collected_at)} IST · ${age < 3600 ? Math.floor(age/60)+'m' : Math.floor(age/3600)+'h'} old${snapshot.previous_snapshot_at ? ' · Compared with '+when(snapshot.previous_snapshot_at) : ''}`;
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
    if(page==='money'&&state.money_alerts){const a=state.money_alerts;if(!moneyAlertsInitialized){$('moneyAlertEnabled').checked=a.enabled;$('moneyThreshold').value=a.threshold;$('moneyNear').value=a.near;$('moneyStep').value=a.step;moneyAlertsInitialized=true;}if($('moneyAlertStatus'))$('moneyAlertStatus').textContent=a.last_delivery?'Last alert: '+a.last_delivery.state+' · '+when(a.last_delivery.stamp):a.enabled?'Waiting for next complete collection':a.telegram_ready?'Alerts off · Telegram configured':'Telegram credentials missing';}

    if (!initialized) { fillSettings(state.config); initialized=true; }
    $('mode').textContent=state.demo ? 'DEMO · SYNTHETIC DATA' : state.in_session ? 'LIVE MODE · IN SESSION' : 'LIVE MODE · OUTSIDE SESSION';
    $('history').replaceChildren(option('', 'Latest'));
    for (const item of history) $('history').append(option(item.id, requestedJob==='nse' ? `${item.scope} · daily` : when(item.stamp)));
    $('history').value=selected;
    updateExpiries();
    const active=Object.entries(state.jobs).filter(([name,s])=>name===requestedJob && s.running);
    $('progress').textContent=active.map(([name,s])=>`${name.toUpperCase()}: ${s.progress || 'Collecting…'}`).join(' · ');
    $('progress').hidden=!active.length;
    const current=state.jobs[requestedJob]; $('refresh').disabled=current.running;
    $('refresh').textContent=current.running ? 'Collecting…' : 'Collect now';
    notice('error', current.error, true);
    if($('status'))$('status').textContent=`FYERS REQUESTS TODAY (V2): ${state.request_usage ?? 0} / ${state.config.requests_per_day} budget\nBudgets apply to V2 only; other apps share the broker allowance.\n\n`+Object.entries(state.jobs).map(([name,s]) => `${name.toUpperCase()} ${s.running ? '• collecting' : '• idle'}\n${s.progress || 'Waiting for schedule'}\nLast successful run: ${when(s.last_success)}\nNext: ${s.next_run || 'Pending'}${s.error ? '\nError: '+s.error : ''}`).join('\n\n');
    render();
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
      cfg.oi={...cfg.oi, universe:$('universe').value, mode:$('schedule').value, interval_seconds:Number($('interval').value)*60, daily_time:$('dailyTime').value, symbols:$('symbols').value.split(/[\s,]+/).filter(Boolean).map(s=>s.toUpperCase())};
      cfg.nse={...cfg.nse, enabled:$('nseEnabled').checked, daily_time:$('nseTime').value, retry_seconds:Number($('nseRetry').value)*60, history_days:Number($('nseDays').value)};
    }
    await api('/api/config',cfg); $('history').value='';
    notice('message',page === 'oi' ? 'Settings saved. Saved data stays available; click Collect now to fetch new data.' : cfg.money.interval_seconds===0 ? 'Settings saved. Auto refresh is Off; use Collect now when needed.' : 'Settings saved. Money collection follows the selected interval during market hours.');
    await poll();
  } catch(e) { notice('error',e.message); }
});
$('refresh').addEventListener('click',async()=>{
  try { const result=await api('/api/refresh',{job:job()}); notice('message',result.queued ? 'Collection queued. This page will update when the snapshot is saved.' : 'This collector is already running or queued.'); $('history').value=''; await poll(); }
  catch(e) { notice('error',e.message); }
});
$('filter').addEventListener('input',render);
$('columns').addEventListener('change',render);
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
 const changed=()=>{try{localStorage.setItem('money-display-options',JSON.stringify(Object.fromEntries(ids.map(id=>[id,$(id).value]))));}catch{}render();};
 for(const id of [...ids,'moneyThreshold','moneyStep'])$(id).addEventListener('input',()=>{moneyAlertsInitialized=true;changed();});
 for(const button of document.querySelectorAll('[data-diff]'))button.onclick=()=>{$('moneyThreshold').value=button.dataset.diff;moneyAlertsInitialized=true;changed();saveAlertsAutomatically(true);};
 for(const button of document.querySelectorAll('[data-top]'))button.onclick=()=>{$('moneyTop').value=button.dataset.top;changed();};
}
poll(); setInterval(poll,5000);
