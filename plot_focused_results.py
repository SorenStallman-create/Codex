"""Render the frozen historical findings as a shareable PNG and SVG."""
import json
import datetime as dt
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import dates as mdates,ticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'research/focused_bot'
data=json.loads((OUT/'results.json').read_text())
runs=[r for r in data['runs'] if r['period']=='holdout']
colors=['#007F88','#C27918','#7856B4']
styles=['-','--',':']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.titlesize':14,'axes.labelsize':11,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.spines.bottom':False})
fig=plt.figure(figsize=(14,12),facecolor='#F6F8F9')
grid=fig.add_gridspec(2,2,left=.14,right=.96,bottom=.20,top=.79,hspace=.68,wspace=.58,height_ratios=[1.35,1])
fig.text(.06,.95,'THREE-BOT EXPERIMENT',fontsize=11,color='#435661',weight='bold')
fig.text(.06,.905,'Trend Guard is the frozen candidate. Uncertainty remains.',fontsize=22,weight='bold',color='#172C35')
fig.text(.06,.867,'Reserved historical test · 1 Jan–18 Nov 2025 · 322 days · BTC / ETH / SOL · $10,000 per bot',fontsize=12,color='#435661')
ax=fig.add_subplot(grid[0,:]);ax.set_facecolor('#F6F8F9')
for run,color,style in zip(runs,colors,styles):
 dates=[dt.datetime.fromisoformat(p['date']) for p in run['daily']]
 ax.plot([dt.datetime(2025,1,1)]+dates,[10000]+[p['equity'] for p in run['daily']],color=color,linestyle=style,lw=2.2,label=f"{run['name']} · ${run['summary']['ending_equity']:,.0f}")
ax.axhline(10000,color='#77848B',lw=.8,alpha=.7)
ax.set_title('Account equity after modeled fees and slippage',loc='left',pad=38)
ax.legend(loc='lower left',bbox_to_anchor=(0,1.01),ncol=3,frameon=False,fontsize=11,borderaxespad=0)
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2));ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.0f}'));ax.set_ylabel('Portfolio equity (USD)');ax.set_xlabel('Date (2025)')
ax.grid(axis='y',alpha=.18);ax.tick_params(axis='both',length=0,pad=8)
ax.margins(x=.01,y=.12)

bar=fig.add_subplot(grid[1,0]);bar.set_facecolor('#F6F8F9')
labels=['Trend Guard\nMain candidate','Breakout Guard\nChallenger','Pullback Guard\nChallenger']
for i,(r,color) in enumerate(zip(runs,colors)):
 s=r['summary'];bar.barh(i-.16,s['return_pct'],height=.27,color=color)
 bar.barh(i+.16,s['max_drawdown_pct'],height=.27,facecolor='none',edgecolor=color,hatch='///',linewidth=0)
 bar.text(s['return_pct']+.06,i-.16,f"+{s['return_pct']:.2f}%",va='center',fontsize=10)
 bar.text(s['max_drawdown_pct']+.06,i+.16,f"{s['max_drawdown_pct']:.2f}%",va='center',fontsize=10)
bar.set_yticks(range(3),labels);bar.invert_yaxis();bar.set_xlim(0,max(r['summary']['return_pct'] for r in runs)*1.32)
bar.set_xlabel('Percent of account');bar.set_title('Return and maximum drawdown',loc='left',pad=40)
bar.legend(handles=[Patch(facecolor='#536D77',label='Net return'),Patch(facecolor='none',edgecolor='#536D77',hatch='///',label='Drawdown magnitude')],loc='lower left',bbox_to_anchor=(0,1.01),ncol=2,frameon=False,fontsize=10,borderaxespad=0)
bar.grid(axis='x',alpha=.15);bar.set_axisbelow(True);bar.tick_params(length=0,pad=8)

ci=fig.add_subplot(grid[1,1]);ci.set_facecolor('#F6F8F9')
lo=min(r['uncertainty']['lower_95_pct'] for r in runs);hi=max(r['uncertainty']['upper_95_pct'] for r in runs)
for i,(r,color) in enumerate(zip(runs,colors)):
 u=r['uncertainty'];ci.plot([u['lower_95_pct'],u['upper_95_pct']],[i,i],color=color,lw=4,solid_capstyle='round')
 ci.scatter([r['summary']['return_pct']],[i],s=65,color=color,marker=['o','s','D'][i],zorder=3)
 ci.text((u['lower_95_pct']+u['upper_95_pct'])/2,i-.22,f"{u['lower_95_pct']:+.2f}% to {u['upper_95_pct']:+.2f}%",ha='center',fontsize=10)
ci.axvline(0,color='#536D77',lw=1);ci.set_yticks(range(3),['Trend','Breakout','Pullback']);ci.set_ylim(2.55,-.65);ci.set_xlim(lo-.8,hi+.8)
ci.set_title('95% resampled return interval',loc='left',pad=40)
ci.text(0,1.065,'Dots: observed return · lines: seven-day block bootstrap',transform=ci.transAxes,fontsize=10,color='#435661')
ci.set_xlabel('Total return (%)');ci.grid(axis='x',alpha=.15);ci.tick_params(length=0,pad=8)

fig.text(.06,.117,'Research gates: none passed all checks',fontsize=14,weight='bold',color='#172C35')
fig.text(.06,.091,'Trend: interval includes losses  •  Breakout: interval includes losses  •  Pullback: only 12 trades (30 required)',fontsize=11,color='#435661')
fig.text(.06,.057,'Average capital invested: Trend 2.18% · Breakout 1.84% · Pullback 0.25%. Low exposure helps explain low account drawdown.',fontsize=10,color='#435661')
fig.text(.06,.035,'Historical simulations, not forward results. Bootstrap intervals omit selection uncertainty and are not forecasts. Source: frozen local research results.',fontsize=9,color='#435661')
for extension in ('png','svg'):
 fig.savefig(OUT/('bot-comparison.'+extension),dpi=180,facecolor=fig.get_facecolor(),bbox_inches='tight',pad_inches=.3)
print(OUT/'bot-comparison.png')
