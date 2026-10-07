import json,sys
vid=sys.argv[1]; only=sys.argv[2:] 
d=json.load(open(f'runs/v1_test/{vid}.json',encoding='utf-8'))
t=json.load(open(f'scribe_dataset/{vid}/{vid}_scribe_transcript.json',encoding='utf-8'))
W=[dict(w) for w in t['word_timestamps']];E=t['audio_events']
try:
    C=json.load(open(f'runs/asr_fix/{vid}/corrections.json',encoding='utf-8'))['corrections']
    for c in C:
        if c.get('applied'):
            W[c['start_word']]['word']='{'+c['corrected']+'}'
            for k in range(c['start_word']+1,c['end_word']+1): W[k]['word']=''
except Exception as e: print('no corr',e)
def fmt(s): return f"{int(s//60):02d}:{s%60:04.1f}"
for c in d['proposals']:
    if only and c['id'] not in only: continue
    segs=c['time_segments']; off=0.0
    print(f"\n===== {c['rank']} {c['id']} [{c['tier']}] | {c['title']} | play {sum(s['end']-s['start'] for s in segs):.1f}s")
    print(f"  PAYOFF[{c['discovery'].get('payoff_type')}]: {c['discovery'].get('payoff_line')}")
    for i,s in enumerate(segs):
        if i>0: print(f"  --- JOIN #{i} at play {fmt(off)} (source {fmt(segs[i-1]['end'])} -> {fmt(s['start'])}, gap {s['start']-segs[i-1]['end']:+.1f}s) [{s['kind']}] ---")
        else: print(f"  [seg {i} {s['kind']} source {fmt(s['start'])}-{fmt(s['end'])}]")
        items=[]
        for w in W:
            m=(w['start']+w['end'])/2
            if s['start']<=m<=s['end'] and w['word']: items.append((w['start'],'w',w))
        for e in E:
            if e['end']>s['start'] and e['start']<s['end']: items.append((e['start'],'e',e))
        items.sort(key=lambda x:x[0])
        cur=None;buf=[]
        def flush():
            if buf: print(f"  [{fmt(buf[0][0]-s['start']+off)}] ({fmt(buf[0][0])}) {cur[-1] if cur else ''}: "+' '.join(b[1] for b in buf))
        for st,k,o in items:
            if k=='e':
                flush();buf=[];cur=None
                print(f"  [{fmt(max(o['start'],s['start'])-s['start']+off)}] ({fmt(o['start'])}-{fmt(o['end'])}) EVENT {o['text']}")
            else:
                if o['speaker']!=cur: flush();buf=[];cur=o['speaker']
                buf.append((o['start'],o['word']))
        flush()
        ws=[w for w in W if s['start']<=(w['start']+w['end'])/2<=s['end']]
        if ws: print(f"  (first word at +{ws[0]['start']-s['start']:.2f}s; last word ends {s['end']-ws[-1]['end']:.2f}s before seg end)")
        off+=s['end']-s['start']
    # what comes right after
    last=segs[-1]['end']
    nx=[w for w in W if w['start']>=last][:12]
    if nx: print(f"  >> NEXT after end (+{nx[0]['start']-last:.1f}s): "+' '.join(w['word'] for w in nx))
