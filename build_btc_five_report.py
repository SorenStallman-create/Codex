"""Build the offline BTC five-bot visual report."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "research" / "btc_live_five"
DAY = ROOT / "research" / "btc_five_bots"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def payload(folder: Path, label: str):
    return {
        "label": label,
        "result": read_json(folder / "results.json"),
        "summary": read_csv(folder / "summary.csv"),
        "daily": read_csv(folder / "daily_results.csv"),
        "stress": read_csv(folder / "stress.csv"),
        "bots": read_json(folder / "results.json").get("model_catalog", []),
    }


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTC Five-Bot Research</title><style>
:root{--bg:#08111f;--panel:#101d31;--line:#263853;--text:#e9f1ff;--muted:#9aabc4;--green:#37d69a;--red:#ff7184;--amber:#ffd166;--blue:#76a9ff}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#07101e,#0b1527 55%,#111b32);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif}main{max-width:1250px;margin:auto;padding:36px 22px 70px}.eyebrow{color:var(--blue);text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:700}h1{font-size:38px;line-height:1.08;margin:8px 0 10px;letter-spacing:-.03em}h2{font-size:18px;margin:0 0 12px}.muted{color:var(--muted)}.warning{border:1px solid #9a6c2f;background:#241d16;border-radius:12px;padding:15px 18px;margin:22px 0}.pill{display:inline-block;background:#5c3516;color:var(--amber);border-radius:999px;padding:3px 9px;font-size:11px;font-weight:700;letter-spacing:.06em}.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:20px 0}.controls select{background:#0b172a;color:var(--text);border:1px solid var(--line);padding:9px 11px;border-radius:8px}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}.card,.panel{background:rgba(16,29,49,.88);border:1px solid var(--line);border-radius:12px;padding:16px}.card small{display:block;color:var(--muted);font-size:11px}.card strong{display:block;font-size:27px;margin:4px 0}.grid{display:grid;grid-template-columns:1.4fr .9fr;gap:16px}.chartbox{height:350px}.chartbox canvas{width:100%;height:100%}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;min-width:760px}th,td{padding:9px 10px;border-bottom:1px solid #21324b;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}td:first-child{font-weight:600}.positive{color:var(--green)}.negative{color:var(--red)}.gate{color:var(--amber);font-weight:700}.sources{display:flex;gap:10px;flex-wrap:wrap}.sources a{color:#9fc2ff}.footer{color:var(--muted);font-size:12px;margin-top:28px}@media(max-width:800px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:1fr}h1{font-size:30px}}
</style></head><body><main><div class="eyebrow">Trading Lab · BTC research</div><h1>Five BTC bots, tested twice</h1><p class="muted">A cost-aware comparison of a 15-minute day-trading screen and a 24/7 live-style screen. Historical paper research only.</p><div class="warning"><span class="pill">NO LIVE ORDERS · NO KIMI · KEEP CAPITAL DISABLED</span><div id="warning" style="margin-top:8px"></div></div><div class="controls"><label>Study <select id="study"></select></label><label>Chart bot <select id="bot"></select></label></div><div class="cards"><div class="card"><small>Validation champion</small><strong id="champion">—</strong><span class="muted">selected before holdout</span></div><div class="card"><small>Holdout leader</small><strong id="leader">—</strong><span class="muted">after modeled costs</span></div><div class="card"><small>Best holdout return</small><strong id="best">—</strong><span class="muted">not a promotion decision</span></div><div class="card"><small>Passing gates</small><strong id="passes">—</strong><span class="muted">all checks</span></div></div><div class="grid"><section class="panel"><h2>Holdout equity curve</h2><div class="chartbox"><canvas id="chart" width="1000" height="340"></canvas></div><div id="note" class="muted"></div></section><section class="panel"><h2>Bot catalog</h2><div id="catalog"></div></section></div><section class="panel" style="margin-top:16px"><h2>Chronological results</h2><div class="tablewrap"><table><thead><tr><th>Bot</th><th>Validation</th><th>Holdout</th><th>Regression</th><th>Trades</th><th>DD</th><th>PF</th><th>Gate</th></tr></thead><tbody id="results"></tbody></table></div></section><section class="panel" style="margin-top:16px"><h2>Holdout execution stress</h2><div class="tablewrap"><table><thead><tr><th>Bot</th><th>Base</th><th>Double cost</th><th>One-bar/hour delay</th></tr></thead><tbody id="stress"></tbody></table></div></section><section class="panel" style="margin-top:16px"><h2>Method</h2><p id="method" class="muted"></p><div class="sources"><a href="https://onlinelibrary.wiley.com/doi/abs/10.1111/fire.12290">Bitcoin intraday momentum</a><a href="https://arxiv.org/abs/2608.21888">15-minute crypto reversal</a><a href="https://eprints.soton.ac.uk/429352/">Bitcoin intraday dynamics</a><a href="BTC_FIVE_STRATEGY_MEMO.md">Local memo</a><a href="../btc_five_bots/index.html">15-minute details</a></div></section><div class="footer">Hash-checked public Binance data. The regression block overlaps earlier visible history. A positive backtest does not establish live profitability.</div></main><script>
const DATA=__DATA__;const $=id=>document.getElementById(id);const pct=x=>Number(x).toFixed(2)+'%';const cls=x=>Number(x)>=0?'positive':'negative';
function current(){return DATA[$('study').value]}
function by(s,p,b){return s.summary.find(x=>x.period===p&&x.bot===b)}
function render(){const s=current(),r=s.result,bots=[...new Set(s.summary.map(x=>x.bot))];$('bot').innerHTML=bots.map(x=>`<option>${x}</option>`).join('');const h=s.summary.filter(x=>x.period==='holdout').sort((a,b)=>Number(b.return_pct)-Number(a.return_pct));$('champion').textContent=r.frozen?.champion_before_holdout||'—';$('leader').textContent=h[0]?.bot||'—';$('best').textContent=h[0]?pct(h[0].return_pct):'—';$('passes').textContent=(r.gates||[]).filter(x=>x.passes).length+'/'+(r.gates||[]).length;$('warning').textContent=$('study').value==='live'?'The 24/7 BTC screen has positive holdout leaders, but uncertainty gates still fail.':'The 15-minute screen makes turnover and cost failure visible; all five candidates fail its promotion gates.';$('method').textContent=$('study').value==='live'?'Complete 4-hour signals feed hourly next-bar fills. BTC is treated as 24/7, with long/cash exposure, ATR sizing, stops, and drawdown guards.':'Each UTC day is a separate 15-minute test. Positions flatten at the next day boundary, with one entry per day and explicit fees/slippage.';$('results').innerHTML=bots.map(b=>{const v=by(s,'validation',b)||{},x=by(s,'holdout',b)||{},g=by(s,'regression',b)||{},gate=(r.gates||[]).find(q=>q.bot===b);return `<tr><td>${b}</td><td class="${cls(v.return_pct)}">${pct(v.return_pct)}</td><td class="${cls(x.return_pct)}">${pct(x.return_pct)}</td><td class="${cls(g.return_pct)}">${pct(g.return_pct)}</td><td>${x.trades}</td><td>${pct(x.max_drawdown_pct)}</td><td>${Number(x.profit_factor||0).toFixed(2)}</td><td class="${gate&&gate.passes?'positive':'gate'}">${gate&&gate.passes?'PASS':'RESEARCH ONLY'}</td></tr>`}).join('');$('stress').innerHTML=bots.map(b=>`<tr><td>${b}</td>${['base','double_cost','one_hour_delay'].map(sc=>{const x=s.stress.find(q=>q.bot===b&&q.scenario===sc);return `<td class="${cls(x?.return_pct||0)}">${x?pct(x.return_pct):'—'}</td>`}).join('')}</tr>`).join('');$('catalog').innerHTML=bots.map(b=>{const x=(s.bots||[]).find(q=>q.id===b)||{};return `<p><b>${x.name||b}</b><br><span class="muted">${x.thesis||''}</span></p>`}).join('');draw()}
function draw(){const s=current(),b=$('bot').value,rows=s.daily.filter(x=>x.period==='holdout'&&x.bot===b).sort((a,z)=>a.date.localeCompare(z.date)),c=$('chart'),ctx=c.getContext('2d'),w=c.width,h=c.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#101d31';ctx.fillRect(0,0,w,h);if(!rows.length)return;const vals=rows.map(x=>Number(x.equity)),lo=Math.min(...vals),hi=Math.max(...vals),L=58,R=18,T=18,B=32,X=i=>L+i*(w-L-R)/Math.max(1,rows.length-1),Y=v=>T+(hi-v)*(h-T-B)/Math.max(1e-9,hi-lo);ctx.strokeStyle='#263853';ctx.lineWidth=1;for(let j=0;j<5;j++){const yy=T+j*(h-T-B)/4;ctx.beginPath();ctx.moveTo(L,yy);ctx.lineTo(w-R,yy);ctx.stroke();ctx.fillStyle='#9aabc4';ctx.font='11px Arial';ctx.fillText('$'+(hi-(hi-lo)*j/4).toFixed(0),4,yy+4)}ctx.strokeStyle='#76a9ff';ctx.lineWidth=2;ctx.beginPath();rows.forEach((r,i)=>{const xx=X(i),yy=Y(Number(r.equity));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke();ctx.fillStyle='#9aabc4';ctx.font='11px Arial';ctx.fillText(rows[0].date,L,h-9);ctx.fillText(rows[rows.length-1].date,w-85,h-9);$('note').textContent=`${b} · ${rows.length} daily marks · final ${pct(Number(vals.at(-1))/100-1)} · range ${pct(lo/10000-1)} to ${pct(hi/10000-1)}`}
$('study').innerHTML=Object.entries(DATA).map(([k,v])=>`<option value="${k}">${v.label}</option>`).join('');$('study').onchange=render;$('bot').onchange=draw;render();
</script></body></html>'''


def main():
    LIVE.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"live": payload(LIVE, "BTC 24/7 live-style screen"), "day": payload(DAY, "BTC 15-minute day-trade screen")}, separators=(",", ":"))
    (LIVE / "index.html").write_text(HTML.replace("__DATA__", data), encoding="utf-8")
    print(LIVE / "index.html")


if __name__ == "__main__":
    main()
