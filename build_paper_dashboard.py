"""Build the lightweight Phase 2 safety and execution dashboard."""
from __future__ import annotations

import html
import json
from pathlib import Path

from paper_execution import measurement_summary


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research" / "overnight_equities"
PAPER = OUT / "paper"


def number(value, suffix="", digits=2):
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}{suffix}"


def build() -> Path:
    summary = measurement_summary(PAPER / "fills.csv")
    focused = json.loads((OUT / "focused_top30" / "results.json").read_text(encoding="utf-8"))
    rows = [row for row in focused.get("summary", []) if row.get("model") == "focused_portfolio"]
    bars = []
    for row in rows:
        scenario = f"{row.get('window')} · {row.get('scenario', 'base')}"
        # The focused report's summary rows have no scenario label; the first
        # row per window is the base run. Stress rows below supply doubled cost.
        if not row.get("scenario"):
            bars.append({"label": f"{row.get('window')} · base", "value": row.get("return_pct", 0.0)})
    for row in focused.get("stress", []):
        if row.get("model") == "focused_portfolio" and row.get("scenario") == "double_cost_50pct":
            bars.append({"label": f"{row.get('window')} · 2× cost", "value": row.get("return_pct", 0.0)})
    bars_json = json.dumps(bars, separators=(",", ":"))
    status = summary["decision"].replace("_", " ")
    status_class = "wait" if summary["completed_sessions"] < 60 else ("stop" if "STOP" in summary["decision"] else "go")
    html_text = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phase 2 · Safe paper execution</title>
<style>
:root{{--bg:#09111f;--panel:#111d30;--panel2:#17263d;--ink:#edf4ff;--muted:#9db0c8;--line:#29405e;--green:#52d39b;--amber:#f4c96a;--red:#ff7f86;--blue:#74b9ff}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#09111f,#0e1a2c 55%,#122541);color:var(--ink);font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:auto;padding:30px 22px 54px}}h1{{font-size:32px;margin:0 0 5px}}h2{{font-size:18px;margin:0 0 14px}}p{{color:var(--muted);margin:6px 0 0}}.eyebrow{{color:var(--blue);letter-spacing:.14em;text-transform:uppercase;font-size:12px;font-weight:700}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}}.card,.panel{{background:rgba(17,29,48,.93);border:1px solid var(--line);border-radius:15px;box-shadow:0 14px 40px rgba(0,0,0,.18)}}.card{{padding:18px;min-height:112px}}.label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}.value{{font-size:27px;font-weight:760;margin-top:9px}}.small{{font-size:12px;color:var(--muted);margin-top:3px}}.panel{{padding:20px;margin-top:14px}}
.badge{{display:inline-flex;align-items:center;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}}.wait{{color:#2b2310;background:#f4c96a}}.go{{color:#072518;background:#52d39b}}.stop{{color:#34090c;background:#ff7f86}}
.safety{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.guard{{background:var(--panel2);border-radius:11px;padding:13px;border:1px solid #2c4565}}.guard strong{{display:block;color:var(--green);font-size:16px}}.guard span{{color:var(--muted);font-size:12px}}
.bars{{display:grid;gap:11px}}.barrow{{display:grid;grid-template-columns:160px 1fr 70px;align-items:center;gap:10px}}.barlabel{{color:var(--muted);font-size:12px}}.track{{height:22px;background:#0b1627;border-radius:6px;overflow:hidden;border:1px solid #263b58}}.bar{{height:100%;border-radius:5px;background:linear-gradient(90deg,#4da5ff,#70d9ae);min-width:2px}}.bar.negative{{background:linear-gradient(90deg,#fb8b8b,#b94c68)}}.barvalue{{text-align:right;font-variant-numeric:tabular-nums}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left}}th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}.links{{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px}}a{{color:#9bcaff;text-decoration:none}}a:hover{{text-decoration:underline}}.foot{{font-size:12px;color:var(--muted);margin-top:20px}}
@media(max-width:800px){{.grid{{grid-template-columns:repeat(2,1fr)}}.safety{{grid-template-columns:repeat(2,1fr)}}.barrow{{grid-template-columns:110px 1fr 60px}}}}@media(max-width:520px){{.grid,.safety{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="eyebrow">Trading Lab · Phase 2</div><h1>Safe paper execution</h1>
<p>The two frozen model lines keep their measured signal while the execution layer records real quote costs. The ledger is paper-only and sends no order by default.</p>
<div class="grid">
<div class="card"><div class="label">Measurement status</div><div class="value"><span class="badge {status_class}">{html.escape(status)}</span></div><div class="small">60 completed calendar sessions required</div></div>
<div class="card"><div class="label">Completed sessions</div><div class="value">{summary['completed_sessions']} / 60</div><div class="small">{summary['completed_trades']} matched entry/exit trades</div></div>
<div class="card"><div class="label">Median round trip</div><div class="value">{number(summary['median_round_trip_bps'], ' bps')}</div><div class="small">Decision threshold: &lt;8 continue · &gt;12 archive</div></div>
<div class="card"><div class="label">75th percentile</div><div class="value">{number(summary['p75_round_trip_bps'], ' bps')}</div><div class="small">Measured from quoted ask/bid fills</div></div>
</div>
<section class="panel"><h2>Hard safety envelope</h2><div class="safety">
<div class="guard"><strong>50% total</strong><span>Maximum notional across all model lines</span></div>
<div class="guard"><strong>25% / model</strong><span>Equal sleeves keep each line bounded</span></div>
<div class="guard"><strong>1× leverage</strong><span>No borrowing or margin in the paper path</span></div>
<div class="guard"><strong>5 seconds</strong><span>Older quotes are blocked</span></div>
<div class="guard"><strong>12 bps</strong><span>Wider spreads are blocked</span></div>
<div class="guard"><strong>News + reconcile</strong><span>Severe news blocks; errors halt the loop</span></div>
</div></section>
<section class="panel"><h2>Historical performance kept in view</h2><p>These are the archived focused replay values, shown for context while quote-aware observations accumulate. They are not a live performance claim.</p><div class="bars" id="bars"></div></section>
<section class="panel"><h2>What the recorder measures</h2><table><thead><tr><th>Field</th><th>Recorded rule</th><th>Purpose</th></tr></thead><tbody>
<tr><td>Entry fill</td><td>Quoted ask</td><td>Conservative cost before the position exists</td></tr><tr><td>Exit fill</td><td>Quoted bid</td><td>Conservative cost at the next open</td></tr><tr><td>Slippage</td><td>Compared with official close/open</td><td>Direct break-even test for the overnight edge</td></tr><tr><td>Ledger</td><td>Append-only CSV with trade IDs</td><td>Prevents duplicate legs and silent retries</td></tr></tbody></table>
<div class="links"><a href="fills.csv">Open fills.csv</a><a href="measurement_state.json">Open measurement state</a><a href="../SCOREBOARD.md">Open historical scoreboard</a><a href="../index.html">Open full research visual</a></div></section>
<div class="foot">Preregistered contract: phase2_paper_20260919 · signal and parameters frozen for the first 60 sessions · last generated from the local ledger.</div>
</main><script>
const bars={bars_json};const root=document.querySelector('#bars');const max=Math.max(1,...bars.map(x=>Math.abs(x.value)));for(const item of bars){{const row=document.createElement('div');row.className='barrow';const label=document.createElement('div');label.className='barlabel';label.textContent=item.label;const track=document.createElement('div');track.className='track';const bar=document.createElement('div');bar.className='bar'+(item.value<0?' negative':'');bar.style.width=(Math.max(2,Math.abs(item.value)/max*100))+'%';track.appendChild(bar);const value=document.createElement('div');value.className='barvalue';value.textContent=(item.value>=0?'+':'')+item.value.toFixed(2)+'%';row.append(label,track,value);root.appendChild(row)}}
</script></body></html>"""
    path = PAPER / "index.html"
    path.write_text(html_text, encoding="utf-8")
    return path


if __name__ == "__main__":
    print(build())
