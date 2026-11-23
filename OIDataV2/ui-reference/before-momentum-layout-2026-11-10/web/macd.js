'use strict';
const $=id=>document.getElementById(id);
let automationInitialized=false;
for(const id of ['hourlyScan','telegramAlerts'])$(id).onchange=()=>{automationInitialized=true;};
$('saveAutomation').onclick=async()=>{try{const r=await fetch('/api/macd/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hourly:$('hourlyScan').checked,alerts:$('telegramAlerts').checked})});const data=await r.json();if(!r.ok)throw Error(data.error);$('automationStatus').textContent='Settings saved';}catch(e){$('automationStatus').textContent=e.message;}};
let report=null, busy=false, theme='dark', priceInitialized=false;
$('scanMinPrice').addEventListener('input',()=>{priceInitialized=true;});
try {theme=localStorage.getItem('market-desk-theme') || 'light';} catch {}
function applyTheme(){document.documentElement.dataset.theme=theme; $('theme').textContent=theme==='dark'?'White theme':'Black theme'; $('theme').setAttribute('aria-label','Switch to '+(theme==='dark'?'white':'black')+' theme');}
applyTheme();
try {const saved=localStorage.getItem('macd-universe');if(['watchlist','bhavcopy'].includes(saved))$('universe').value=saved;} catch {}
$('theme').onclick=()=>{theme=theme==='dark'?'light':'dark';applyTheme();try{localStorage.setItem('market-desk-theme',theme);}catch{}};
function render(){
 if(!report)return;
 const groups={FULL:'Full alignment',NEAR:'Entry zone',PULLBACK:'Pullback / bounce'};
 const q=$('filter').value.trim().toUpperCase(), side=$('side').value;
 const rows=(report[side]||[]).filter(r=>(r.short||'').toUpperCase().includes(q)&&(!$('minprice').valueAsNumber || (r.price ?? 0)>=$('minprice').valueAsNumber)&&($('group').value==='all'||r.category===$('group').value));
 rows.sort((a,b)=>$('sort').value==='name'?a.short.localeCompare(b.short):$('sort').value==='price'?(b.price||0)-(a.price||0):(b.count-a.count)||a.short.localeCompare(b.short));
 const fragment=document.createDocumentFragment();
 for(const r of rows){
  const tr=document.createElement('tr');
  const cell=(value,className='')=>{const td=document.createElement('td');td.textContent=value;td.className=className;tr.append(td);return td;};
  cell(r.short,'macd-symbol');
  cell(r.price==null?'—':Number(r.price).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2}),'macd-price');
  const tfCell=cell('','macd-timeframes');
  const chips=document.createElement('div');chips.className='tf-chips';
  for(const tf of report.order){
   const chip=document.createElement('span');
   const aligned=(r.aligned||[]).includes(tf),waiting=tf===r.waiting;
   chip.className='tf-chip'+(aligned?(side==='buy'?' aligned-bull':' aligned-bear'):waiting?' waiting':'');
   chip.textContent=tf;
   chip.title=`${tf}: ${aligned?'aligned':waiting?'waiting — '+(r.waiting_state||'unknown'):'not checked'}`;
   chip.setAttribute('aria-label',chip.title);
   chips.append(chip);
  }
  tfCell.append(chips);
  cell(`${r.count}/${report.order.length}`,side==='buy'?'positive':'negative');
  cell(groups[r.category]||r.category);
  cell(r.waiting==='-'?'All aligned':`${r.waiting} · ${r.waiting_state||'—'}`);
  fragment.append(tr);
 }
 $('rows').replaceChildren(fragment);$('empty').hidden=rows.length>0;$('counts').textContent=`${rows.length} shown · ${report.buy.length} bullish · ${report.sell.length} bearish`;
}
async function scanStatus(){
 try{const response=await fetch('/api/macd/status');const s=await response.json();if(!response.ok)throw Error(s.error||'Scanner status unavailable');if(s.automation){if(!automationInitialized){$('hourlyScan').checked=s.automation.hourly;$('telegramAlerts').checked=s.automation.alerts;automationInitialized=true;}$('automationStatus').textContent=s.automation.error || (s.automation.telegram_ready?'Telegram configured':'Telegram credentials missing');}if(!priceInitialized){$('scanMinPrice').value=s.min_price ?? 1000;priceInitialized=true;}$('run').disabled=s.running||!s.enabled;$('run').textContent=s.running?'Scanning…':'Run scan';$('scanStatus').textContent=`${s.running?'RUNNING · ':''}${s.source==='bhavcopy'?'Bhavcopy F&O':'Watchlist'} · Price > ₹${s.min_price ?? 1000} · ${s.progress}${s.error?' · '+s.error:''}${s.finished_at?' · Finished '+new Date(s.finished_at).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'}):''}`;}
 catch(e){$('scanStatus').textContent=e.message+' · Restart the updated dashboard server if this route is missing.';}
}
$('run').onclick=async()=>{
 if(!$('scanMinPrice').reportValidity() || !Number.isFinite($('scanMinPrice').valueAsNumber)){$('scanStatus').textContent='Enter a valid scan price (0 disables the cutoff).';return;}
 $('run').disabled=true;
 try{const response=await fetch('/api/macd/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:$('universe').value,min_price:$('scanMinPrice').valueAsNumber})});const result=await response.json();if(!response.ok)throw Error(result.error||'Could not start scan');await scanStatus();}
 catch(e){$('scanStatus').textContent=e.message;$('run').disabled=false;}
};
async function load(){if(busy)return;busy=true;const source=$('universe').value;try{const response=await fetch('/api/macd?source='+encodeURIComponent(source));const data=await response.json();if(source!==$('universe').value)return;if(!response.ok)throw Error(data.error||'Report unavailable');report=data.report;$('error').hidden=true;$('meta').textContent=`Stock list: ${report.universe_label || 'Watchlist'}${report.bhavcopy_date?' ('+report.bhavcopy_date+')':''} · Scan price: ${report.scan_min_price == null ? 'not recorded' : report.scan_min_price === 0 ? 'no cutoff' : '> ₹'+report.scan_min_price} · Candles: FYERS · Report: ${report.stamp} · ${report.scanned} stocks scanned · ${report.order.join(' › ')} · minimum chain ${report.min} · Saved file, not a live scan` ;render();}catch(e){if(source===$('universe').value){$('error').hidden=false;$('error').textContent=e.message+(report?' · Displaying the last loaded report for this stock list.':'');}}finally{busy=false;}}
for(const id of ['side','group','filter','minprice','sort'])$(id).addEventListener('input',render);
$('universe').addEventListener('change',()=>{report=null;$('rows').replaceChildren();$('counts').textContent='';$('meta').textContent='Loading selected stock list report…';$('empty').hidden=true;try{localStorage.setItem('macd-universe',$('universe').value);}catch{}load();});
$('reload').onclick=()=>{load();scanStatus();};load();scanStatus();setInterval(()=>{load();scanStatus();},5000);
