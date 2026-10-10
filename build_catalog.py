import json,re,html,glob
from pathlib import Path
root=Path(__file__).parent
ps={}
for file in [str(root/'sources/products.json')]+sorted(glob.glob(str(root/'sources/eu*.json'))):
 for p in json.load(open(file)).get('products',[]):
  if re.match(r'^(PLA|PETG?|ABS|ASA|TPU|PC|PAHT|PPA|PA|PPS|Support|PVA)(?:\b|\d)',p['title']) and not re.search('bundle|pack|swatch|sample|combo',p['title'],re.I):
   old=ps.get(p['handle'])
   if old:p['body_html']=old[0].get('body_html','')+p.get('body_html','')
   ps[p['handle']]=(p,'EU' if '/eu' in file else 'USA')
p=json.load(open(root/'sources/ppa.json'));p['body_html']=p['description'];ps.setdefault(p['handle'],(p,'USA'))
def clean(s):return html.unescape(re.sub('<[^>]+>','',s)).strip()
def norm(s):return re.sub(r'[^a-z0-9]','',re.sub(r'\([^)]*\)','',s.lower().replace('grey','gray').replace('matte ','').removeprefix('abs ')))
data=[]
for p,region in ps.values():
 cmap={}
 for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',p.get('body_html',''),re.S|re.I):
  cs=[clean(c) for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',row,re.S|re.I)]
  if len(cs)>1:
   hx=re.findall(r'#[0-9a-fA-F]{6}(?![0-9a-fA-F])',cs[1])
   if hx:cmap[norm(cs[0])]=list(dict.fromkeys(h.upper() for h in hx))
 options=p['options'];pos=next((i+1 for i,o in enumerate(options) if 'color' in (o['name'] if isinstance(o,dict) else o).lower()),None)
 if not pos:print('NO COLOR',p['title']);continue
 groups={}
 for v in p['variants']:
  name=v.get('option'+str(pos))
  if name:groups.setdefault(name,[]).append(v)
 for raw,vs in groups.items():
  code=re.search(r'\((\d{5})\)',raw);name=re.sub(r'\s*\(\d{5}\)','',raw).strip();hx=cmap.get(norm(name),[])
  v=next((v for v in vs if v.get('featured_image')),vs[0]);im=v.get('featured_image') or {};im=im.get('src','') if isinstance(im,dict) else im
  family=re.match(r'^[A-Za-z]+',p['title'])[0].upper()
  if family=='SUPPORT' or family=='PVA':family='Support'
  if family.startswith('PA') or family=='PPA':family='PA / Nylon'
  data.append(dict(id=str(p['id'])+'-'+str(len(data)),type=p['title'],family=family,name=name,code=code.group(1) if code else '',hex=hx,image=im,source='https://'+('eu' if region=='EU' else 'us')+'.store.bambulab.com/products/'+p['handle'],region=region))
data.sort(key=lambda d:(d['type'],d['name']))
(root/'dist/data.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
print('COLORS',len(data),'TYPES',len(set(d['type'] for d in data)),'HEX',sum(bool(d['hex']) for d in data),'IMAGES',sum(bool(d['image']) for d in data))
print(sorted(set(d['type'] for d in data)))
