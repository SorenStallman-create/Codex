"""Export only human-facing messages from the explicitly identified current thread."""
import argparse,datetime as dt,html,json
from pathlib import Path
SOURCE=Path('/Users/sorenstallman/.codex/sessions/2026/09/17/rollout-2026-09-17T20-30-30-01a0b1ec-0686-78b1-9ce6-9b6eadbfecb7.jsonl')
def extract():
 messages=[];seen=set()
 for line in SOURCE.read_text().splitlines():
  r=json.loads(line);p=r.get('payload',{});role=phase=turn=None;parts=[]
  if r.get('type')=='response_item' and p.get('type')=='message':
   role=p.get('role');phase=p.get('phase') or p.get('channel');parts=p.get('content',[]);turn=p.get('internal_chat_message_metadata_passthrough',{}).get('turn_id')
  elif r.get('type')=='event_msg' and p.get('type')=='item_completed':
   it=p.get('item',{});typ=it.get('type')
   if typ not in ('AgentMessage','UserMessage'):continue
   role='assistant' if typ=='AgentMessage' else 'user';phase=it.get('phase');parts=it.get('content',[]);turn=p.get('turn_id')
  else:continue
  if role not in ('user','assistant'):continue
  if role=='assistant' and phase not in ('commentary','final','final_answer'):continue
  text='\n'.join(x.get('text','') for x in parts if x.get('type') in ('input_text','output_text','text','Text')).strip()
  if not text or text.startswith('<environment_context>'):continue
  key=(role,turn,text)
  if key in seen:continue
  seen.add(key);messages.append({'timestamp':r['timestamp'],'role':role,'phase':phase,'text':text})
 return messages

def export(folder):
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);messages=extract();cutoff=dt.datetime.now(dt.timezone.utc).isoformat()
 scope=f'Exported {cutoff}. {len(messages)} available user-visible messages from this current thread. Includes earlier history as pasted by the user. System/developer instructions, hidden reasoning, tool logs and agent-internal messages are excluded. Images/attachments are not embedded; research artifacts are supplied separately. Messages after this export cutoff are not included.'
 markdown='# Trading research conversation\n\n'+scope+'\n\n'+'\n\n---\n\n'.join('## '+m['role'].title()+' · '+m['timestamp']+'\n\n'+m['text'] for m in messages)
 (folder/'Conversation.md').write_text(markdown)
 body=''.join('<section class="'+m['role']+'"><h2>'+m['role'].title()+' <small>'+html.escape(m['timestamp'])+'</small></h2><div>'+html.escape(m['text'])+'</div></section>' for m in messages)
 page='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading research conversation</title><style>body{font:15px/1.65 system-ui;background:#eef1f5;color:#1a2636;max-width:1000px;margin:auto;padding:26px}section{background:white;padding:23px;margin:18px 0;border-radius:12px;border-left:4px solid #669499}section.user{border-color:#8570b3}section div{white-space:pre-wrap;overflow-wrap:anywhere}h1{line-height:1.2}h2{font-size:16px}small{font-weight:400;color:#687386;font-size:11px}p{color:#526074}</style><h1>Trading research conversation</h1><p>'+html.escape(scope)+'</p>'+body+'</html>'
 (folder/'Conversation.html').write_text(page)
 meta={'exported_utc':cutoff,'message_count':len(messages),'user_messages':sum(m['role']=='user' for m in messages),'assistant_messages':sum(m['role']=='assistant' for m in messages),'first_timestamp':messages[0]['timestamp'],'last_timestamp':messages[-1]['timestamp'],'scope':scope}
 (folder/'conversation_export.json').write_text(json.dumps(meta,indent=2));print(json.dumps(meta,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('folder');a=p.parse_args();export(a.folder)
