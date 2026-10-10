import json,re,html,urllib.request,concurrent.futures,datetime
from pathlib import Path
ROOT=Path(__file__).parent; S=ROOT/'sources/prices'; DATA=json.loads((ROOT/'dist/data.json').read_text())
def norm(s):
 s=html.unescape(s).lower().replace('grey','gray').replace('silk+','silkplus').replace('filament',' ').replace('bambu lab',' ').replace('spule','spole')
 return re.sub(r'[^a-z0-9]+',' ',s).strip()
def match(title):
 codes=re.findall(r'\b\d{5}\b',title);found=[d for d in DATA if d['code'] and d['code'] in codes]
 if len(found)==1:return found[0]
 t=' '+norm(title)+' ';types=[d['type'] for d in DATA if ' '+norm(d['type'])+' ' in t]
 if not types:return None
 typ=max(types,key=lambda x:len(norm(x)));t=t.replace(' '+norm(typ)+' ',' ')
 found=[]
 for d in DATA:
  if d['type']!=typ:continue
  c=d['name'];c=re.sub(r'\([^)]*\)','',c).strip();c=re.sub(r'^(Matte |ABS |Translucent |Glow )','',c);c=c.replace(' Sparkle','').replace(' Metallic','')
  if typ=='PLA Marble' and c=='White Marble':c='White'
  if ' '+norm(c)+' ' in t:found.append((len(norm(c)),d))
 if not found:return None
 found.sort(key=lambda x:x[0],reverse=True)
 return found[0][1] if len(found)==1 or found[0][0]>found[1][0] else None
if __name__=='__main__':
 items=json.loads((S/'tradera-listings.json').read_text());candidates=[]
 for i in items:
  d=match(i['shortDescription'])
  if d and i.get('isActive') and i.get('sellerCountryCodeIso2')=='SE':candidates.append(dict(item=i,key=d['type']+'|'+(d['code'] or d['name'])))
 (S/'tradera-candidates.json').write_text(json.dumps(candidates,ensure_ascii=False))
 j=[]
 for c in json.loads((S/'jake-candidates.json').read_text()):
  d=match(c['title'])
  if d:j.append(dict(c,key=d['type']+'|'+(d['code'] or d['name'])))
 (S/'jake-matched.json').write_text(json.dumps(j,ensure_ascii=False));print('Matched Tradera',len(candidates),'Jake',len(j));print([(c['title'],c['key']) for c in j[:10]])
