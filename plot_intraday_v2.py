"""Standalone evidence figure; uses the existing visualization environment."""
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from intraday_v2_data import OUT
r=json.loads((OUT/'results.json').read_text());models=list(r['markets']['SPY']);colors=['#238f99','#d08335','#7566bc'];names=['Momentum band','Opening range','Closing momentum']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,axs=plt.subplots(2,2,figsize=(15,9),layout='constrained');fig.suptitle('Daily trading research — no candidate qualified',fontsize=23,fontweight='bold')
for ax,symbol in zip(axs[0],('SPY','QQQ')):
 ds=r['freeze']['splits']['evaluation'];x=np.arange(len(ds))
 for i,m in enumerate(models):
  rows=[z for z in r['daily'] if z['symbol']==symbol and z['phase']=='evaluation' and z['model']==m];ys=[z['pnl'] for z in rows]
  ax.bar(x+(i-1)*.23,ys,.22,color=colors[i],label=names[i]);ax.scatter(x+(i-1)*.23,[0 if v==0 else float('nan') for v in ys],color=colors[i],s=12)
 ax.axhline(0,color='#8893a4',lw=.8);ax.set_xticks(x,[d[5:] for d in ds]);ax.set_ylabel('Net P&L ($ / $10,000 day)');ax.set_title(symbol+' · every evaluation session',loc='left',fontweight='bold');ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
axs[0,0].legend(fontsize=9,ncol=1)
ax=axs[1,0];primary=r['freeze']['primary'];labels=['Base','Double costs','5min delay'];x=np.arange(3)
for i,s in enumerate(('SPY','QQQ')):
 z=r['markets'][s][primary];vals=[z['total_pnl'],z['stress']['double_cost']['total_pnl'],z['stress']['delay_5min']['total_pnl']];ax.bar(x+(i-.5)*.3,vals,.28,label=s,color=['#344f70','#9787d8'][i])
ax.axhline(0,color='#8893a4',lw=.8);ax.set_xticks(x,labels);ax.set_ylabel('Total net P&L ($, seven sessions)');ax.set_title('Frozen primary: Closing Momentum',loc='left',fontweight='bold');ax.legend();ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
ax=axs[1,1];ax.axis('off');ax.text(0,1,'What the evidence says',fontsize=17,fontweight='bold',va='top');ax.text(0,.84,'SPY primary: −$1.79 total / 1 trade\nQQQ primary: +$1.56 total / 1 trade\nQQQ gain fails cost and delay stress.\nThe momentum band did not trade.',fontsize=14,va='top',linespacing=1.6);ax.text(0,.40,'42 sessions downloaded per ETF; 14 warmup.\n9 prespecified candidates → 3 frozen bots.\n28 scoring days; only 7 evaluation days.\nPreviously seen SPY dates: exploratory evidence.\nNext: longer SIP history and prospective paper.',fontsize=11,color='#46566b',va='top',linespacing=1.7)
fig.supxlabel('Unleveraged, long-only simulations · modeled costs included · infrastructure excluded · no live orders',fontsize=11)
fig.savefig(OUT/'comparison.png',dpi=160);fig.savefig(OUT/'comparison.svg');print(OUT/'comparison.png')
