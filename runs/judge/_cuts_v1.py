import json,sys
vid=sys.argv[1]
d=json.load(open(f'runs/v1_test/{vid}.json',encoding='utf-8'))
s=json.load(open(f'runs/video/{vid}/shots.json'))
cuts=[c if isinstance(c,(int,float)) else c.get('t',c.get('time')) for c in s['cuts']]
for p in d['proposals']:
    off=0; out=[]
    for seg in p['time_segments']:
        out+= [round(c-seg['start']+off,1) for c in cuts if seg['start']<c<seg['end']]
        off+=seg['end']-seg['start']
    print(p['id'], 'shot cuts (clip-rel):', out)
