import json,sys
vid=sys.argv[1]
g=json.load(open(f'data/gold/{vid}.json',encoding='utf-8'))
t=json.load(open(f'scribe_dataset/{vid}/{vid}_scribe_transcript.json',encoding='utf-8'))
W=t['word_timestamps'];E=t['audio_events']
def fmt(s): return f"{int(s//60):02d}:{s%60:04.1f}"
for c in g['clips']:
    segs=c['time_segments']; off=0.0
    print(f"\n===== {c['id']} | {c['title']} | play {sum(s['end']-s['start'] for s in segs):.1f}s | purpose={c.get('clip_purpose')} cold_open={c.get('cold_open')} reordered={c.get('reordered')} spoiler={c.get('spoiler')} conf={c.get('confidence')}")
    print(f"  PAYOFF[{c.get('payoff_type')}]: {c.get('payoff_line')}")
    for i,s in enumerate(segs):
        if i>0: print(f"  --- JOIN #{i} at play {fmt(off)} (source {fmt(segs[i-1]['end'])} -> {fmt(s['start'])}, gap {s['start']-segs[i-1]['end']:+.1f}s) ---")
        else: print(f"  [seg {i} source {fmt(s['start'])}-{fmt(s['end'])}]")
        items=[]
        for w in W:
            m=(w['start']+w['end'])/2
            if s['start']<=m<=s['end']: items.append((w['start'],'w',w))
        for e in E:
            if e['end']>s['start'] and e['start']<s['end']: items.append((e['start'],'e',e))
        items.sort(key=lambda x:x[0])
        cur=None;buf=[]
        def flush():
            if buf: print(f"  [{fmt(buf[0][0]-s['start']+off)}] ({fmt(buf[0][0])}) {cur}: "+' '.join(b[1] for b in buf))
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
