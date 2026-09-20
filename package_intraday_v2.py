"""Make the user-requested Desktop handoff; no environment secrets or raw chat logs."""
import hashlib,json,shutil,zipfile
from pathlib import Path
from intraday_v2_data import ROOT,OUT
DEST=Path('/Users/sorenstallman/Desktop/Trading Research 2026-09-18')
def main():
 DEST.mkdir(exist_ok=True)
 shutil.copytree(OUT,DEST/'research/intraday_v2',dirs_exist_ok=True)
 for n in ['intraday_v2_data.py','intraday_v2_engine.py','intraday_v2_research.py','build_intraday_v2_report.py','plot_intraday_v2.py','alpaca_history_import.py','roh_guide_research.py','roh_guide_checker.py','build_roh_guide_report.py','plot_roh_guide.py','btc_five_data.py','btc_five_research.py','btc_live_five.py','build_btc_five_report.py','plot_btc_five.py']:
  shutil.copy2(ROOT/n,DEST/n)
 (DEST/'web').mkdir(exist_ok=True);shutil.copy2(ROOT/'web/intraday_v2.html',DEST/'web/intraday_v2.html')
 (DEST/'tests').mkdir(exist_ok=True)
 for n in ['test_intraday_v2.py','test_alpaca_import.py','test_roh_guide.py','test_btc_five.py']:shutil.copy2(ROOT/'tests'/n,DEST/'tests'/n)
 old=DEST/'research/intraday_v1/data';old.mkdir(parents=True,exist_ok=True)
 for p in (ROOT/'research/intraday_v1/data').glob('SPY*'):shutil.copy2(p,old/p.name)
 shutil.copy2(ROOT/'research/intraday_v1/spy_audit.json',old.parent/'spy_audit.json')
 for n in ['Conversation.html','Conversation.md','conversation_export.json','comparison.png','FINDINGS.md','IMPLEMENTATION_ROADMAP.md','API_IMPLEMENTATION.md']:
  shutil.copy2(OUT/n,DEST/n)
 shutil.copy2(ROOT/'README.md',DEST/'README.md')
 shutil.copy2(OUT.parent/'roh_guide_v1'/'ROH_GUIDE_IMPLEMENTATION.md',DEST/'ROH_GUIDE_IMPLEMENTATION.md')
 shutil.copytree(OUT.parent/'roh_guide_v1',DEST/'research/roh_guide_v1',dirs_exist_ok=True)
 shutil.copytree(OUT.parent/'btc_five_bots',DEST/'research/btc_five_bots',dirs_exist_ok=True)
 shutil.copytree(OUT.parent/'btc_five_bots_first_pass',DEST/'research/btc_five_bots_first_pass',dirs_exist_ok=True,ignore=shutil.ignore_patterns('data'))
 shutil.copytree(OUT.parent/'btc_live_five',DEST/'research/btc_live_five',dirs_exist_ok=True)
 shutil.copy2(ROOT/'roh_guide_checker.py',DEST/'roh_guide_checker.py')
 shutil.copy2(ROOT/'build_roh_guide_report.py',DEST/'build_roh_guide_report.py')
 page=(OUT/'index.html').read_text().replace('<head>','<head><base href="research/intraday_v2/">',1)
 (DEST/'Open Report.html').write_text(page)
 (DEST/'START HERE.txt').write_text('''TRADING RESEARCH — SEPTEMBER 18, 2026

Double-click Open Report.html to open the interactive report in your browser.
No local server or internet connection is needed for the charts/replays.
Research source links require internet. CSV/data links are local.

Conversation.html / Conversation.md: available human-facing current-thread chat,
including earlier history pasted by the user. Export cutoff and scope appear inside.
Tool logs, hidden reasoning, credentials and unrelated conversations are excluded.

comparison.png: shareable result figure.
FINDINGS.md: executed evidence and limitations.
IMPLEMENTATION_ROADMAP.md / API_IMPLEMENTATION.md: next steps and APIs.
research/intraday_v2: raw data, CSVs, frozen parameters, all sources and report.

RESULT: No bot qualifies for capital. Closing Momentum is the frozen research
candidate, not a demonstrated winner. Seven evaluation sessions and one primary
trade per ETF are insufficient. No real orders or credentials were used.

ROHONCHAIN GUIDE STUDY:
  Open research/roh_guide_v1/index.html for the new visual pair-trading report.
  guide_comparison.png is a static version of its result chart.
  It tests the guide's statistical-arbitrage category using a causal SPY/QQQ
  residual z-score strategy. The canonical ±2σ version lost $63.86 across 11
  evaluation sessions, so it also remains research-only. ROH_GUIDE_IMPLEMENTATION.md
  maps the maker/checker roles and explains why volatility surface, factors and
  insider clusters need different data. Kimi is not integrated.

BTC FIVE-BOT RESET:
  Open research/btc_live_five/index.html for the BTC visual comparison.
  It tests five BTC-only live-style bots: Trend Guard, Breakout Guard,
  Pullback Guard, Andean/Laguerre and McGinley. The 2024 holdout leaders were
  Trend Guard (+4.54%) and Breakout (+4.99%), but both fail the bootstrap
  uncertainty gate and remain paper-only. The separate research/btc_five_bots
  folder contains the stricter 15-minute day-trade screen; all five fail after
  realistic costs. No Kimi or live endpoint is present.

REPRODUCE (Python 3.10+, standard library):
  cd into this folder in Terminal
  python3 -m unittest discover -s tests -v
  python3 intraday_v2_research.py evaluate
  python3 build_intraday_v2_report.py
The bundle includes the BTC reset tests alongside the earlier test suites. The
current full project run is 64 tests; its log is preserved under
research/intraday_v2/tests.log.
The frozen code/data hashes are checked before evaluation. Do not overwrite
freeze or retune the inspected dates. A new hypothesis needs a new study.
Plotting is optional and requires matplotlib/numpy; its outputs are included.

LONGER HISTORY:
  python3 alpaca_history_import.py --help
Requires your locally configured Alpaca key pair plus an authoritative session
calendar CSV. Read-only data host only; never paste credentials into chat.
No paid feed purchase or authenticated download has been performed.

V2 is historical research. Broker paper/live integration is a documented next
stage, not an active live execution service. See API_IMPLEMENTATION.md.
''')
 files={str(p.relative_to(DEST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.rglob('*')) if p.is_file() and p.name!='BUNDLE_MANIFEST.json' and '__pycache__' not in p.parts}
 (DEST/'BUNDLE_MANIFEST.json').write_text(json.dumps({'files':files,'note':'Research/source only; no credentials or raw session logs.'},indent=2))
 archive=DEST.with_suffix('.zip')
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for p in sorted(DEST.rglob('*')):
   if p.is_file() and '__pycache__' not in p.parts:z.write(p,Path(DEST.name)/p.relative_to(DEST))
 print(json.dumps({'folder':str(DEST),'archive':str(archive),'files':len(files)+1,'zip_bytes':archive.stat().st_size},indent=2))
if __name__=='__main__':main()
