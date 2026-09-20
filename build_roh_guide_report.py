"""Build a compact offline visual report for the guide-specific study."""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research" / "roh_guide_v1"


def money(value):
    return ("−" if value < 0 else "") + "$" + f"{abs(value):,.2f}"


def main():
    report = json.loads((OUT / "results.json").read_text())
    rows = report["daily"]
    models = sorted({r["model"] for r in rows})
    summaries = report["summary"]
    cards = []
    for model in models:
        s = summaries[model]["evaluation"]
        cards.append(f"<div class='card'><small>{html.escape(model)}</small><strong>{money(s['total_pnl'])}</strong><span>{s['trades']} trades · {s['positive_days']} up / {s['negative_days']} down days</span></div>")
    chart_rows = []
    eval_days = report["freeze"]["splits"]["evaluation"]
    for model in models:
        values = [next(r["net_pnl"] for r in rows if r["model"] == model and r["phase"] == "evaluation" and r["day"] == day) for day in eval_days]
        chart_rows.append({"model": model, "values": values})
    table_rows = []
    for model in models:
        s = summaries[model]["evaluation"]
        table_rows.append(f"<tr><td>{html.escape(model)}</td><td>{money(s['mean_pnl'])}</td><td>{money(s['median_pnl'])}</td><td>{money(s['total_pnl'])}</td><td>{s['trades']}</td><td>{s['positive_days']} / {s['flat_days']} / {s['negative_days']}</td><td>{s['max_drawdown_pct']:.3f}%</td></tr>")
    data = json.dumps({"days": eval_days, "series": chart_rows}, separators=(",", ":"))
    page = f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Guide study · statistical arbitrage</title>
<style>:root{{--bg:#0b111b;--card:#141e2e;--line:#2c3a50;--ink:#eff4ff;--muted:#a7b5ca;--teal:#67d5d2;--orange:#ffc47e;--purple:#9c9eff;--red:#ff97a0}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:34px 22px 60px}}h1{{font-size:clamp(29px,4vw,48px);line-height:1.1;margin:8px 0 14px;letter-spacing:-1.5px}}h2{{font-size:20px;margin:0 0 8px}}p{{margin:8px 0 15px}}small,.muted{{color:var(--muted)}}.eyebrow{{color:var(--teal);letter-spacing:2px;text-transform:uppercase;font-size:12px}}.warning{{background:#2a2420;border-left:3px solid var(--orange);padding:15px 18px;margin:20px 0}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}}.card,.panel{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}}.card strong{{display:block;font-size:28px;margin:5px 0}}.card span{{font-size:12px;color:var(--muted)}}.panel{{margin:17px 0}}svg{{width:100%;height:auto;display:block}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:17px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}th{{color:var(--muted);font-size:11px;font-weight:500}}a{{color:#92e5e1}}.pill{{display:inline-block;padding:5px 9px;border:1px solid #74474c;border-radius:15px;color:#ffb6bd;font-size:12px}}.roles{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.role{{border-top:2px solid var(--teal);padding:10px 0;font-size:13px}}.role b{{display:block}}.footer{{color:var(--muted);font-size:12px;margin-top:25px}}@media(max-width:760px){{.cards,.grid,.roles{{grid-template-columns:1fr}}}} </style>
<main><div class='eyebrow'>RohOnChain guide · deterministic paper study</div><h1>The guide’s first direct test:<br>market-neutral pairs.</h1><p class='muted'>SPY / QQQ five-minute residual z-score strategy · no Kimi · no broker · no live orders</p><div class='warning'><span class='pill'>RESEARCH ONLY · KEEP CASH</span><p>The canonical ±2σ strategy lost {money(summaries[report['freeze']['lead']]['evaluation']['total_pnl'])} across {len(eval_days)} evaluation sessions. The sample was already visible in the earlier lab, so it is exploratory—not a new proof.</p></div><div class='cards'>{''.join(cards)}</div>
<section class='panel'><h2>Net P&amp;L per evaluation session</h2><p class='muted'>Fresh $10,000 account each day; long one leg/short the other; both legs cost 0.005% fee plus 0.01% adverse slippage per side.</p><svg id='chart' viewBox='0 0 900 300' role='img' aria-label='Daily pair strategy returns'></svg></section>
<section class='panel'><h2>Daily evidence</h2><div style='overflow:auto'><table><thead><tr><th>Rule</th><th>Mean/day</th><th>Median/day</th><th>Total</th><th>Trades</th><th>Up / flat / down</th><th>Max close DD</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div></section>
<div class='grid'><section class='panel'><h2>What the guide changed</h2><p>Maker and checker roles are separated. A strategy runner writes raw fills and daily rows; a separate deterministic checker reads them and can decline promotion. No LLM or Kimi integration is present.</p><p>The tested category is statistical arbitrage: causal residual beta, z-score entry, mean-reversion exit and same-day flattening. The earlier opening-range and closing-momentum bots remain separate challengers.</p></section><section class='panel'><h2>Not tested with current data</h2><p>Volatility surface needs synchronized option quotes, IV, expiries and greeks. Factor decomposition needs a point-in-time cross-sectional universe. Insider clusters need filing arrival timestamps and a multi-day event horizon.</p><p>Those are adapters for the next research block, not numbers to invent from two ETF candles.</p></section></div>
<section class='panel'><h2>Eight-role workflow, without Kimi</h2><div class='roles'><div class='role'><b>Research / hypothesis maker</b>Literature registry and fixed candidates.</div><div class='role'><b>Data / feature desk</b>Hash checks, sessions and causal features.</div><div class='role'><b>Backtest desk</b>Next-bar paper fills and raw ledgers.</div><div class='role'><b>Checker / risk</b>Costs, delay, drawdown and sample gates.</div><div class='role'><b>Paper execution</b>Planned adapter; no live endpoint.</div><div class='role'><b>Monitoring</b>Options, SEC and macro adapters planned.</div><div class='role'><b>Coordinator</b>Ranks evidence, never grades its own proposal.</div><div class='role'><b>Audit</b>Frozen hashes, reconciliation and failure log.</div></div></section>
<section class='panel'><h2>Promotion requirements</h2><ol><li>Obtain 12–24 months of consolidated minute bars and quotes.</li><li>Reserve at least 120 genuinely unseen historical sessions.</li><li>Run the fixed candidate set with short borrow, margin, spread and early-close rules.</li><li>Run at least 60 new paper sessions and 100 completed fills.</li><li>Keep execution disabled until a human authorizes a bounded mandate.</li></ol><p class='muted'>Sources: <a href='https://www.nber.org/papers/w7032'>Gatev pairs study</a> · <a href='https://tau-home.com/en/post/gpt-6-astra-quant-trading-agent-maker-checker-architecture/'>independent guide summary</a> · <a href='https://www.sciencedirect.com/science/article/pii/S1544612324015435'>insider-signal evidence</a> · <a href='ROH_GUIDE_IMPLEMENTATION.md'>local implementation memo</a> · <a href='../intraday_v2/index.html'>earlier intraday dashboard</a></p></section><div class='footer'>Built September 19, 2026 · Public Yahoo five-minute data · 11 evaluation sessions · no credentials, Kimi or real orders.</div></main>
<script>const DATA={data};const colors=['#67d5d2','#ffc47e','#9c9eff'];const svg=document.querySelector('#chart');const W=900,H=300,L=58,R=20,T=20,B=38;const vals=DATA.series.flatMap(s=>s.values);let lo=Math.min(0,...vals),hi=Math.max(0,...vals);if(hi-lo<1){{lo-=1;hi+=1}}const y=v=>T+(hi-v)/(hi-lo)*(H-T-B),x=i=>L+i/Math.max(1,DATA.days.length-1)*(W-L-R);svg.innerHTML='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y(0)+'" y2="'+y(0)+'" stroke="#65748b"/>'+DATA.series.map((s,j)=>s.values.map((v,i)=>'<rect x="'+(x(i)+(j-1)*8)+'" y="'+Math.min(y(0),y(v))+'" width="7" height="'+Math.max(1,Math.abs(y(v)-y(0)))+'" fill="'+colors[j]+'"><title>'+s.model+' · '+DATA.days[i]+' · '+(v<0?'−':'')+'$'+Math.abs(v).toFixed(2)+'</title></rect>').join('')).join('')+DATA.days.map((d,i)=>'<text x="'+x(i)+'" y="'+(H-10)+'" text-anchor="middle" fill="#a7b5ca" font-size="10">'+d.slice(5)+'</text>').join('')+'<text x="'+(L-8)+'" y="'+(y(hi)+4)+'" text-anchor="end" fill="#a7b5ca" font-size="10">$'+hi.toFixed(0)+'</text><text x="'+(L-8)+'" y="'+(y(lo)+4)+'" text-anchor="end" fill="#a7b5ca" font-size="10">−$'+Math.abs(lo).toFixed(0)+'</text>';</script></html>"""
    (OUT / "index.html").write_text(page)
    print(OUT / "index.html")


if __name__ == "__main__":
    main()
