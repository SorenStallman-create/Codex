"""Build an offline report with embedded evidence and research notes."""
import html,json,re
from intraday_v2_data import ROOT,OUT

def main():
 data=json.loads((OUT/'results.json').read_text());template=(ROOT/'web/intraday_v2.html').read_text();memos=[]
 for name,title in [('FINDINGS.md','Results and interpretation'),('PLAN.md','Frozen experiment and exact rules'),('STRATEGY_RESEARCH.md','Strategy papers and Reddit research'),('API_IMPLEMENTATION.md','Data, API and broker implementation'),('IMPLEMENTATION_ROADMAP.md','Implementation stages and promotion gates'),('AUDIT.md','Independent audit and verification')]:
  p=OUT/name
  if not p.exists():continue
  escaped=html.escape(p.read_text());escaped=re.sub(r'(https://[^\s)<>]+)',r'<a href="\1" target="_blank" rel="noopener">\1</a>',escaped)
  memos.append('<details><summary>'+title+'</summary><div class="memo">'+escaped+'</div></details>')
 encoded=json.dumps(data,separators=(',',':'),allow_nan=False).replace('</','<\\/')
 (OUT/'index.html').write_text(template.replace('__DATA__',encoded).replace('__MEMOS__',''.join(memos)))
 print('Built',OUT/'index.html')
if __name__=='__main__':main()
