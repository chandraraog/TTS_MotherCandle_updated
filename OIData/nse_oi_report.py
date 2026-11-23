"""
NSE Futures + CE/PE Open-Interest interpretation report (last 5 trading days) -> HTML

Run:   python nse_oi_report.py            # real NSE data
       python nse_oi_report.py --demo     # synthetic data, to preview the HTML
Needs: pip install requests pandas
Output: oi_report.html

Interpretation follows the "Open Interest" sheet:
  FUTURES  Price/OI   Up/Up Long Buildup (Aggressively Bullish) | Down/Up Short Buildup (Aggressively Bearish)
                      Up/Down Short Covering (Bullish)          | Down/Down Long Covering (Bearish)
  CE       Prem/OI    Up/Up Long Buildup (Bullish) | Down/Up Short Buildup TMJ (Aggressive Bearish)
                      Up/Down Short Covering TMG (Aggressive Bullish) | Down/Down Long Covering (Bearish)
  PE       Prem/OI    Up/Up Long Buildup (Bearish) | Up/Down Short Covering TMG (Aggressive Bearish)
                      Down/Up Short Buildup TMJ (Aggressive Bullish) | Down/Down Long Covering (Bullish)

Options are read at the ATM strike (strike nearest the futures close) of the nearest expiry.
Net View: all three (Future, CE, PE) on the same side -> Bullish/Bearish;
          same side AND at least 2 of them "Aggressive" -> Strong Bullish/Strong Bearish (yellow row);
          otherwise Mixed.
"""
import io, sys, time, zipfile, random, json
from datetime import date, timedelta, datetime
import pandas as pd
import requests

N_DAYS = 5
OUT_FILE = "oi_report.html"
# columns hidden by default (tick them on in the "Columns" panel of the report)
DEFAULT_HIDDEN = ["Fut Close", "Price Chg %", "Fut OI Chg %", "CE Prem Chg %", "PE Prem Chg %",
                  "PCR", "Total CE OI", "Total CE OI Chg", "Total PE OI", "Total PE OI Chg"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.nseindia.com/all-reports-derivatives",
    "Accept-Language": "en-US,en;q=0.9",
}

# (price/premium direction, OI direction) -> (term, view)   -- from the Open Interest sheet
FUT_MAP = {("Up", "Up"): ("Long Buildup", "Aggressively Bullish"),
           ("Down", "Up"): ("Short Buildup", "Aggressively Bearish"),
           ("Up", "Down"): ("Short Covering", "Bullish"),
           ("Down", "Down"): ("Long Covering", "Bearish")}
CE_MAP = {("Up", "Up"): ("Long Buildup", "Bullish"),
          ("Down", "Up"): ("Short Buildup (TMJ)", "Aggressive Bearish"),
          ("Up", "Down"): ("Short Covering (TMG)", "Aggressive Bullish"),
          ("Down", "Down"): ("Long Covering", "Bearish")}
PE_MAP = {("Up", "Up"): ("Long Buildup", "Bearish"),
          ("Up", "Down"): ("Short Covering (TMG)", "Aggressive Bearish"),
          ("Down", "Up"): ("Short Buildup (TMJ)", "Aggressive Bullish"),
          ("Down", "Down"): ("Long Covering", "Bullish")}


# ---------------------------------------------------------------- download
def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=15)  # cookies
    except Exception as e:
        print("Warning: could not warm up NSE session:", e)
    return s


def fetch_bhavcopy(s, d):
    url = (f"https://nsearchives.nseindia.com/content/fo/"
           f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
    try:
        r = s.get(url, timeout=30)
    except Exception as e:
        print(f"  {d}: request failed ({e})")
        return None
    if r.status_code != 200:
        return None  # weekend / holiday / not published yet
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return pd.read_csv(z.open(z.namelist()[0]))


def last_n_trading_days(n):
    s = make_session()
    out, d, tries = [], date.today(), 0
    while len(out) < n and tries < 20:
        if d.weekday() < 5:
            df = fetch_bhavcopy(s, d)
            if df is not None:
                print(f"  got {d}")
                out.append((d, df))
            else:
                print(f"  no data for {d} (holiday / not published yet)")
            time.sleep(0.5)
        d -= timedelta(days=1)
        tries += 1
    return sorted(out, key=lambda x: x[0], reverse=True)


# ---------------------------------------------------------------- analysis
def interp(table, price_chg, oi_chg):
    if pd.isna(price_chg) or pd.isna(oi_chg) or price_chg == 0 or oi_chg == 0:
        return "-", "-"
    return table[("Up" if price_chg > 0 else "Down", "Up" if oi_chg > 0 else "Down")]


def side_of(view):
    if "Bullish" in view:
        return 1
    if "Bearish" in view:
        return -1
    return 0


def net_view(fv, cv, pv):
    sides = [side_of(v) for v in (fv, cv, pv)]
    if all(x == 1 for x in sides):
        direction = "Bullish"
    elif all(x == -1 for x in sides):
        direction = "Bearish"
    else:
        return "Mixed"
    n_aggr = sum("Aggressive" in v for v in (fv, cv, pv))
    return f"Strong {direction}" if n_aggr >= 2 else direction


def analyse_day(df, d):
    df = df.copy()
    df["XpryDt"] = pd.to_datetime(df["XpryDt"])

    # ---- futures (nearest expiry per symbol)
    fut = df[df["FinInstrmTp"].isin(["STF", "IDF"])].copy()
    fut = fut.sort_values("XpryDt").groupby("TckrSymb", as_index=False).first()
    fut["Price Chg %"] = ((fut["ClsPric"] - fut["PrvsClsgPric"]) / fut["PrvsClsgPric"] * 100).round(2)
    fut["_pc"] = fut["ClsPric"] - fut["PrvsClsgPric"]
    fut = fut.rename(columns={"ClsPric": "Fut Close", "OpnIntrst": "Fut OI", "ChngInOpnIntrst": "Fut OI Chg"})
    prev_oi = (fut["Fut OI"] - fut["Fut OI Chg"]).replace(0, pd.NA)
    fut["Fut OI Chg %"] = (fut["Fut OI Chg"] / prev_oi * 100).astype(float).round(2)
    tv = [interp(FUT_MAP, p, o) for p, o in zip(fut["_pc"], fut["Fut OI Chg"])]
    fut["Future"] = [t[0] for t in tv]
    fut["Fut View"] = [t[1] for t in tv]
    fut = fut[["TckrSymb", "Fut Close", "Price Chg %", "Fut OI", "Fut OI Chg", "Fut OI Chg %", "Future", "Fut View"]]

    # ---- options: nearest expiry per symbol
    opt = df[df["FinInstrmTp"].isin(["STO", "IDO"])].copy()
    near = opt.groupby("TckrSymb")["XpryDt"].min().rename("near").reset_index()
    opt = opt.merge(near, on="TckrSymb")
    opt = opt[opt["XpryDt"] == opt["near"]].copy()

    # totals across all strikes
    tot_oi = opt.pivot_table(index="TckrSymb", columns="OptnTp", values="OpnIntrst", aggfunc="sum", fill_value=0)
    tot_chg = opt.pivot_table(index="TckrSymb", columns="OptnTp", values="ChngInOpnIntrst", aggfunc="sum", fill_value=0)
    totals = pd.DataFrame({"Total CE OI": tot_oi.get("CE"), "Total CE OI Chg": tot_chg.get("CE"),
                           "Total PE OI": tot_oi.get("PE"), "Total PE OI Chg": tot_chg.get("PE")})
    totals["PCR"] = (totals["Total PE OI"] / totals["Total CE OI"].replace(0, pd.NA)).astype(float).round(2)
    totals = totals.reset_index()

    # ATM strike = strike closest to the futures close
    und = fut.set_index("TckrSymb")["Fut Close"]
    opt["und"] = opt["TckrSymb"].map(und)
    opt = opt.dropna(subset=["und"])
    opt["dist"] = (opt["StrkPric"] - opt["und"]).abs()
    idx = opt.groupby("TckrSymb")["dist"].idxmin()
    atm = opt.loc[idx, ["TckrSymb", "StrkPric"]].rename(columns={"StrkPric": "ATM"})
    opt = opt.merge(atm, on="TckrSymb")
    a = opt[opt["StrkPric"] == opt["ATM"]]

    def side(tp, table):
        x = a[a["OptnTp"] == tp].groupby("TckrSymb").first()
        pchg = x["ClsPric"] - x["PrvsClsgPric"]
        res = pd.DataFrame({
            f"{tp} Prem Chg %": (pchg / x["PrvsClsgPric"].replace(0, pd.NA) * 100).astype(float).round(2),
            f"{tp} OI": x["OpnIntrst"], f"{tp} OI Chg": x["ChngInOpnIntrst"]})
        tv = [interp(table, p, o) for p, o in zip(pchg, x["ChngInOpnIntrst"])]
        res[f"{tp} Term"] = [t[0] for t in tv]
        res[f"{tp} View"] = [t[1] for t in tv]
        return res.reset_index()

    m = fut.merge(atm, on="TckrSymb", how="left")
    m = m.merge(side("CE", CE_MAP), on="TckrSymb", how="left")
    m = m.merge(side("PE", PE_MAP), on="TckrSymb", how="left")
    m = m.merge(totals, on="TckrSymb", how="left")
    for c in ("CE Term", "CE View", "PE Term", "PE View"):
        m[c] = m[c].fillna("-")
    m["Net View"] = [net_view(f, c, p) for f, c, p in zip(m["Fut View"], m["CE View"], m["PE View"])]
    m = m.rename(columns={"TckrSymb": "Symbol"})
    m.insert(0, "Date", d)
    return m


# ---------------------------------------------------------------- html
COLS = ["Symbol", "Fut Close", "Price Chg %", "Fut OI", "Fut OI Chg", "Fut OI Chg %", "Future", "Fut View",
        "ATM", "CE Prem Chg %", "CE OI", "CE OI Chg", "CE Term", "CE View",
        "PE Prem Chg %", "PE OI", "PE OI Chg", "PE Term", "PE View",
        "PCR", "Total CE OI", "Total CE OI Chg", "Total PE OI", "Total PE OI Chg", "Net View"]
TEXT_COLS = ("Symbol", "Future", "Fut View", "CE Term", "CE View", "PE Term", "PE View", "Net View")
SIGNED = ("Price Chg %", "Fut OI Chg", "Fut OI Chg %", "CE Prem Chg %", "CE OI Chg",
          "PE Prem Chg %", "PE OI Chg", "Total CE OI Chg", "Total PE OI Chg")
VIEW_COLS = ("Fut View", "CE View", "PE View", "Net View")


def fmt(c, v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, str):
        return v
    if c == "ATM":
        return f"{v:g}"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return f"{int(v):,}"


def num_cls(c, v):
    if c in SIGNED and not isinstance(v, str) and not pd.isna(v):
        return "pos" if v > 0 else "neg" if v < 0 else ""
    return ""


def view_cls(v):
    return {"Aggressively Bullish": "ab", "Aggressive Bullish": "ab", "Strong Bullish": "ab",
            "Aggressively Bearish": "ar", "Aggressive Bearish": "ar", "Strong Bearish": "ar",
            "Bullish": "bu", "Bearish": "be"}.get(v, "")


def rules_html():
    def tbl(title, mp, first):
        rows = "".join(
            f"<tr><td>{p}</td><td>{o}</td><td>{t}</td><td class='{view_cls(v)}'>{v}</td></tr>"
            for (p, o), (t, v) in mp.items())
        return (f"<table><tr><th colspan=4>{title}</th></tr><tr><th>{first}</th><th>OI</th><th>Term</th><th>View</th></tr>"
                f"{rows}</table>")
    return ('<details class="rules" id="rules" open><summary><b>Interpretation</b> <span class="hint">(click to expand / hide)</span></summary><div class="rt">'
            + tbl("FUTURES", FUT_MAP, "Price") + tbl("CE (Call)", CE_MAP, "Premium") + tbl("PE (Put)", PE_MAP, "Premium")
            + "</div><div style='margin-top:6px'><b>Net View</b>: Future, CE and PE all on the same side = Bullish/Bearish; "
              "plus at least 2 &ldquo;Aggressive&rdquo; views = <span class='ab'>Strong</span> (yellow row). "
              "Options read at ATM strike, nearest expiry.</div></details>")


def render_html(days):
    alldf = pd.concat([d[1] for d in days])
    sig = alldf[alldf["Net View"].str.startswith("Strong")]
    summ = sig.pivot_table(index="Symbol", columns="Date", values="Net View", aggfunc="first").fillna("")
    summ = summ.reindex(columns=[d for d, _ in days])

    h = ["""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>NSE OI Interpretation Report</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:20px;background:#f5f6f8;color:#222}
h1{margin:0 0 4px} .sub{color:#666;margin-bottom:14px;font-size:13px}
.rules{background:#fff;border:1px solid #ddd;border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:12px}
.rules summary{cursor:pointer;font-size:13px;user-select:none}
.rules .hint{color:#888;font-weight:400;font-size:12px}
.rt{display:flex;gap:18px;flex-wrap:wrap;margin-top:6px}
.rules table{border-collapse:collapse} .rules td,.rules th{padding:3px 10px;border:1px solid #e3e3e3;text-align:left}
.rules th{background:#eef1f6}
.colbox{background:#fff;border:1px solid #ddd;border-radius:6px;padding:8px 14px;margin-bottom:12px;font-size:13px}
.colbox label{margin-right:12px;white-space:nowrap;cursor:pointer}
.tabs button{padding:8px 14px;border:1px solid #bbb;background:#fff;cursor:pointer;border-radius:5px 5px 0 0;margin-right:2px}
.tabs button.on{background:#1f3a5f;color:#fff}
.bar{margin:10px 0} .bar input,.bar select{padding:6px;margin-right:8px}
.wrap{overflow-x:auto;background:#fff;border:1px solid #ddd}
table.d{border-collapse:collapse;width:100%;font-size:13px}
table.d th{background:#1f3a5f;color:#fff;padding:7px 9px;position:sticky;top:0;cursor:pointer;white-space:nowrap}
table.d td{padding:5px 9px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap}
table.d td.t{text-align:left}
tr.hl td{background:#ffff00 !important}
.pos{color:#0a7d33} .neg{color:#c0262d}
.bu{color:#0a7d33;font-weight:600} .be{color:#c0262d;font-weight:600}
.ab{background:#92d050;color:#111;font-weight:600} .ar{background:#ff9b9b;color:#111;font-weight:600}
td.ab,td.ar{border-radius:3px}
.pane{display:none} .pane.on{display:block}
</style></head><body>
<h1>NSE Futures &amp; Options OI Interpretation</h1>
<div class="sub">Last @N@ trading days &bull; generated @TS@</div>""".replace("@N@", str(len(days)))
         .replace("@TS@", datetime.now().strftime("%d-%b-%Y %H:%M"))]
    h.append(rules_html())
    h.append('<div class="colbox"><b>Columns:</b> <span id="colbox"></span> <button onclick="resetCols()">Reset</button></div>')
    h.append("<script>var COLS=%s,DEFHID=%s;</script>" % (json.dumps(COLS), json.dumps(DEFAULT_HIDDEN)))

    h.append('<div class="tabs"><button class="on" onclick="show(\'sum\',this)">Strong Signal Summary</button>')
    for d, _ in days:
        h.append(f'<button onclick="show(\'d{d:%Y%m%d}\',this)">{d:%d-%b-%Y}</button>')
    h.append("</div>")

    # summary pane
    h.append('<div class="pane on" id="sum"><div class="bar"><input id="q_sum" placeholder="Search symbol" oninput="filt(\'sum\')"></div>'
             '<div class="wrap"><table class="d" id="t_sum"><tr><th>Symbol</th>')
    for c in summ.columns:
        h.append(f"<th>{c:%d-%b}</th>")
    h.append("</tr>")
    for sym, row in summ.iterrows():
        h.append(f'<tr><td class="t">{sym}</td>')
        for v in row:
            h.append(f'<td style="text-align:center" class="{view_cls(v)}">{v}</td>' if v else "<td></td>")
        h.append("</tr>")
    h.append("</table></div></div>")

    # day panes
    for d, df in days:
        k = f"d{d:%Y%m%d}"
        h.append(f'<div class="pane" id="{k}"><div class="bar"><input id="q_{k}" placeholder="Search symbol" oninput="filt(\'{k}\')">'
                 f'<label><input type="checkbox" id="o_{k}" onchange="filt(\'{k}\')"> Only Strong</label> '
                 f'<select id="s_{k}" onchange="filt(\'{k}\')"><option value="">All net views</option>'
                 '<option>Strong Bullish</option><option>Strong Bearish</option><option>Bullish</option>'
                 '<option>Bearish</option><option>Mixed</option></select></div>'
                 f'<div class="wrap"><table class="d" id="t_{k}"><tr>')
        for i, c in enumerate(COLS):
            h.append(f"<th onclick=\"sortT('{k}',{i})\">{c}</th>")
        h.append("</tr>")
        for _, r in df.sort_values("Symbol").iterrows():
            hl = str(r["Net View"]).startswith("Strong")
            h.append('<tr class="hl">' if hl else "<tr>")
            for c in COLS:
                v = r[c]
                if c in VIEW_COLS and not hl:
                    h.append(f'<td class="t {view_cls(v)}">{fmt(c, v)}</td>')
                elif c in TEXT_COLS:
                    h.append(f'<td class="t">{fmt(c, v)}</td>')
                else:
                    h.append(f'<td class="{num_cls(c, v)}">{fmt(c, v)}</td>')
            h.append("</tr>")
        h.append("</table></div></div>")

    h.append("""<script>
function show(id,btn){document.querySelectorAll('.pane').forEach(p=>p.classList.remove('on'));
document.getElementById(id).classList.add('on');
document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));btn.classList.add('on');}
function filt(k){var q=document.getElementById('q_'+k).value.toUpperCase();
var s=document.getElementById('s_'+k);var sv=s?s.value:'';var o=document.getElementById('o_'+k);
var rows=document.getElementById('t_'+k).rows;
for(var i=1;i<rows.length;i++){var r=rows[i];var ok=r.cells[0].innerText.toUpperCase().includes(q);
var nv=r.cells[r.cells.length-1].innerText.trim();
if(ok&&sv)ok=nv==sv;if(ok&&o&&o.checked)ok=nv.indexOf('Strong')==0;r.style.display=ok?'':'none';}}
function sortT(k,c){var t=document.getElementById('t_'+k);var rows=Array.from(t.rows).slice(1);
var asc=t.dataset.c==c&&t.dataset.a=='1'?false:true;t.dataset.c=c;t.dataset.a=asc?'1':'0';
rows.sort(function(a,b){var x=a.cells[c].innerText.replace(/,/g,''),y=b.cells[c].innerText.replace(/,/g,'');
var nx=parseFloat(x),ny=parseFloat(y);var r=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y);return asc?r:-r;});
rows.forEach(r=>t.tBodies[0].appendChild(r));}
var rl=document.getElementById('rules');
try{if(localStorage.getItem('oi_rules_open')==='0')rl.open=false;}catch(e){}
rl.addEventListener('toggle',function(){try{localStorage.setItem('oi_rules_open',rl.open?'1':'0');}catch(e){}});
var vis={};
function applyCols(){document.querySelectorAll('table.d[id^="t_d"]').forEach(function(t){
Array.from(t.rows).forEach(function(r){Array.from(r.cells).forEach(function(c,i){c.style.display=vis[COLS[i]]?'':'none';});});});}
function saveCols(){try{localStorage.setItem('oi_cols2',JSON.stringify(vis));}catch(e){}}
function buildCols(){var box=document.getElementById('colbox');box.innerHTML='';
COLS.forEach(function(c){if(c==='Symbol'||c==='Net View')return;var l=document.createElement('label');var cb=document.createElement('input');
cb.type='checkbox';cb.checked=!!vis[c];cb.onchange=function(){vis[c]=cb.checked;saveCols();applyCols();};
l.appendChild(cb);l.appendChild(document.createTextNode(' '+c));box.appendChild(l);});}
function resetCols(){COLS.forEach(function(c){vis[c]=DEFHID.indexOf(c)<0;});saveCols();buildCols();applyCols();}
(function(){var saved=null;try{saved=JSON.parse(localStorage.getItem('oi_cols2'));}catch(e){}
COLS.forEach(function(c){vis[c]=(saved&&c in saved)?saved[c]:DEFHID.indexOf(c)<0;});
vis['Symbol']=true;vis['Net View']=true;buildCols();applyCols();})();
</script></body></html>""")
    return "\n".join(h)


# ---------------------------------------------------------------- demo data
def demo_bhavcopy(d, seed):
    random.seed(seed)
    rows = []
    for sym, tp_f, tp_o, px in [("NIFTY", "IDF", "IDO", 24500), ("BANKNIFTY", "IDF", "IDO", 52000),
                                ("RELIANCE", "STF", "STO", 1450), ("TCS", "STF", "STO", 3900),
                                ("INFY", "STF", "STO", 1800), ("SBIN", "STF", "STO", 820),
                                ("ABB", "STF", "STO", 6800), ("ANGELONE", "STF", "STO", 330)]:
        exp = "2026-11-24"
        step = 50 if px > 5000 else 20 if px > 1000 else 5
        prev = px * (1 + random.uniform(-.015, .015))
        rows.append(dict(FinInstrmTp=tp_f, TckrSymb=sym, XpryDt=exp, OptnTp=None, StrkPric=0,
                         ClsPric=px, PrvsClsgPric=prev, OpnIntrst=random.randint(100000, 1000000),
                         ChngInOpnIntrst=random.randint(-50000, 50000)))
        for k in range(-5, 6):
            for ot in ("CE", "PE"):
                p0 = random.uniform(30, 70)
                rows.append(dict(FinInstrmTp=tp_o, TckrSymb=sym, XpryDt=exp, OptnTp=ot,
                                 StrkPric=px + k * step, ClsPric=p0, PrvsClsgPric=p0 * random.uniform(.8, 1.2),
                                 OpnIntrst=random.randint(10000, 200000),
                                 ChngInOpnIntrst=random.randint(-30000, 30000)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    if "--demo" in sys.argv:
        raw, d = [], date.today()
        while len(raw) < N_DAYS:
            if d.weekday() < 5:
                raw.append((d, demo_bhavcopy(d, d.toordinal())))
            d -= timedelta(days=1)
    else:
        print("Downloading NSE F&O bhavcopy ...")
        raw = last_n_trading_days(N_DAYS)
        if not raw:
            sys.exit("No data downloaded. Check internet / NSE blocking (see notes).")

    days = [(d, analyse_day(df, d)) for d, df in raw]
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(render_html(days))
    print(f"Done -> {OUT_FILE}")