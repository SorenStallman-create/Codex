import json
from intraday_data import OUT,ROOT
s=(ROOT/'web/intraday.html').read_text();d=json.loads((OUT/'results.json').read_text())
(OUT/'index.html').write_text(s.replace('/*RESULTS*/',json.dumps(d,separators=(',',':')).replace('</','<\\/')))
print(OUT/'index.html')
