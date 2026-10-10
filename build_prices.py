from prices import *
NOW='2026-10-10T18:52:30.681957+00:00';offers=[];rejected=[]
def add(o):o['checkedAt']=NOW;offers.append(o)
for x in json.loads((S/'jake-refreshed.json').read_text()):
 t=x['text'];c=x['candidate'];head=re.search(r'L\d+: # (.+)',t)
 if not head or 'Crawled: today' not in t:continue
 title=re.sub(r'【\d+†([^】]+)】',r'\1',head[1]);d=match(title)
 if not d:continue
 tail=t[head.end():];p=re.search(r'L\d+:[^\n]*?([\d \xa0]+)\s*kr',tail)
 if not p:continue
 price=int(re.sub(r'\s','',p[1]));w=re.search(r'(\d[\d.]*)\s*g\b',title)
 if not w:continue
 weight=int(w[1].replace('.',''));pack='Refill' if 'refill' in title.lower() else 'Spole'
 # Product page stock, not a category's possibly stale availability.
 stock=bool(re.search(r'L\d+: I lager\b',t));url=re.search(r'\((https://www.3djake.se/[^)]+)\)',t)
 if not stock or not url:continue
 add(dict(key=d['type']+'|'+(d['code'] or d['name']),shop='3DJake Sverige',title=title,url=url[1],price=price,shipping=0 if price>=1099 else 115,fee=0,weight=weight,pack=pack,kind='fixed',condition='Ny',endAt=None,shippingNote='Standardfrakt i Sverige. Fri frakt från 1 099 kr.',sourceNote='Produktsida och svenska fraktvillkor.'))
for f in S.glob('t-*.json'):
 x=json.loads(f.read_text());a=x.get('details');i=x['listing']
 if not a or not a.get('isActive') or a.get('hasEnded'):continue
 title=a['title'];desc=re.split(r'Sökord|sökord',x['description'])[0];d=match(title)
 if not d:continue
 key=d['type']+'|'+(d['code'] or d['name'])
 if key!=x['key']:continue
 # If the description names a different catalog color code, do not assign the offer.
 codes=set(re.findall(r'\b\d{5}\b',desc));valid={v['code'] for v in DATA if v['code']};codes&=valid
 if codes and d['code'] not in codes:continue
 if len(codes)>1:continue
 text=title+' '+desc
 w=re.search(r'\b(\d+(?:[.,]\d+)?)\s*kg\b',title,re.I)
 if not w:w=re.search(r'(?:netto\s?vikt(?:en)?(?:\s*är)?|vikt)\s*:?\s*(\d+(?:[.,]\d+)?)\s*kg',desc,re.I)
 if not w:w=re.search(r'\b(1)\s*kg\b',desc,re.I)
 weight=round(float(w[1].replace(',','.'))*1000) if w else None
 multi=re.search(r'(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*kg',title,re.I)
 if multi:weight=round(int(multi[1])*float(multi[2].replace(',','.'))*1000)
 if re.search(r'\b(?:[2-9]\s*(?:st|rullar|rolls)|paket)\b',title,re.I) and not multi and not re.search(r'\b[2-9]\s*kg',title,re.I):weight=None
 pack='Refill' if re.search(r'refill',title,re.I) else 'Spole' if re.search(r'spole',title,re.I) else 'Refill' if re.search(r'\brefill\b',desc,re.I) else 'Spole' if re.search(r'(?:med|återanvändbar)\s+spole',desc,re.I) else 'Ej angiven'
 shipping=[s['cost'] for s in a.get('shippingOptions',[]) if s.get('toCountryCodeIso2')=='SE' and not s.get('isTakeaway') and not s.get('isNotSpecified')]
 ship=min(shipping) if shipping else None
 pay=a.get('paymentCalculations',{});fee=pay.get('buyerProtectionCostForFixedPrice',0) if pay.get('isBuyerProtectionMandatory') else 0
 attrs=str(a.get('attributes',[]))+str(i.get('attributes',[]));condition='Oanvänt' if 'Oanvänt' in attrs else 'Kontrollera skick'
 kind='fixed' if a.get('fixedPrice',0)>0 and a.get('canBeBought') else 'auction'
 price=a.get('fixedPrice') if kind=='fixed' else i['price']
 if not price:continue
 add(dict(key=key,shop='Tradera · '+a['seller']['alias'],title=title,url=i['itemUrl'],price=price,shipping=ship,fee=fee,weight=weight,pack=pack,kind=kind,condition=condition,endAt=a['endDate'],shippingNote='Billigaste angivna frakt inom Sverige. '+('Obligatoriskt köparskydd inkluderat.' if fee else 'Eventuellt valfritt köparskydd tillkommer.'),sourceNote='Annons kontrollerad. '+('Bud/utropspris är inte slutpris.' if kind=='auction' else 'Köp nu-pris.')))
for o in json.loads((S/'prima.json').read_text()):add(o)
unique={ (o['key'],o['url'],o['pack']):o for o in offers};offers=list(unique.values())
(ROOT/'dist/offers.json').write_text(json.dumps({'checkedAt':NOW,'offers':offers},ensure_ascii=False))
print('OFFERS',len(offers),'COLORS',len({o['key'] for o in offers}),'JAKES',sum(o['shop']=='3DJake Sverige' for o in offers));print('TRADERA unknown weight',sum(o['weight'] is None for o in offers))
