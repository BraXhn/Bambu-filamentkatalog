"""Flödestest av inloggning, behörighet, CSRF och kontolåsning mot webbservern."""
import http.cookiejar
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class WebFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.port = free_port()
        env = dict(os.environ, KATALOG_DATA_DIR=self.tmp + '/data', 
                   KATALOG_SEED_DIR=R + '/dist', PORT=str(self.port), PRICE_UPDATE_TIME='')
        env.pop('ADMIN_USERNAME', None); env.pop('ADMIN_PASSWORD', None)
        self.proc = subprocess.Popen([sys.executable, '-m', 'katalog', 'serve'], cwd=R, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(('127.0.0.1', self.port), 0.2).close(); break
            except OSError:
                time.sleep(0.1)

    def tearDown(self):
        self.proc.terminate(); self.proc.wait(5); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flow(self):
        D = self.tmp + '/data'
        B = f'http://127.0.0.1:{self.port}'
        fails = []

        def client():
            cj = http.cookiejar.CookieJar()
            return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), NoRedir()), cj

        def req(op, path, data=None, method=None, headers=None):
            h = {'Host': f'127.0.0.1:{self.port}', 'Origin': B}; h.update(headers or {})
            body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
            r = urllib.request.Request(B + path, data=body, method=method, headers=h)
            try:
                with op.open(r) as x:
                    return x.status, dict(x.headers), x.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read().decode()

        def csrf(html):
            return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

        def ok(c, m):
            if not c:
                fails.append(m)

        a,acj=client()
        s,h,b=req(a,'/data.json'); ok(s==303,'auth/check utan session = 401')
        s,h,b=req(a,'/login'); ok(s==303 and h['Location']=='/setup','login -> setup när inga användare')
        s,h,b=req(a,'/setup'); t=csrf(b); ok('Installationskod' in b,'setup kräver kod')
        s,h,b=req(a,'/setup',{'csrf':t,'code':'FEL-KOD-XXX','username':'anton','password':'Langt-losen-123','password2':'Langt-losen-123'}); ok(s==403,'fel setupkod nekas')
        import sqlite3; code=sqlite3.connect(D+'/katalog.db').execute("select value from settings where key='setup_code'").fetchone()[0]
        s,h,b=req(a,'/setup',{'csrf':t,'code':code.lower(),'username':'anton','password':'kort','password2':'kort'}); ok(s==400 and 'minst' in b,'för kort lösenord nekas')
        s,h,b=req(a,'/setup',{'csrf':'fel','code':code,'username':'anton','password':'Langt-losen-123','password2':'Langt-losen-123'}); ok(s==403,'setup utan rätt csrf nekas')
        s,h,b=req(a,'/setup',{'csrf':t,'code':code,'username':'anton','password':'Langt-losen-123','password2':'Langt-losen-123'}); ok(s==303 and h['Location']=='/admin','setup skapar admin och loggar in')
        s,h,b=req(a,'/data.json'); ok(s==200,'auth/check med session = 204')
        s,h,b=req(a,'/setup'); ok(s==303,'setup stängd efter första användaren')
        s,h,b=req(a,'/admin'); ok(s==200 and 'Prisuppdatering' in b,'adminsida visas'); t=csrf(b)
        s,h,b=req(a,'/admin/anvandare',{'csrf':t,'username':'lisa','password':'','role':'user'}); pw=re.search(r'class="secret">([^<]+)<',b).group(1); ok(s==200 and pw,'admin skapar användare med slumpat lösenord')
        s,h,b=req(a,'/admin/anvandare',{'csrf':'fel','username':'eve','password':'','role':'admin'}); ok(s==403,'admin-POST utan csrf nekas')
        s,h,b=req(a,'/admin/anvandare',{'csrf':t,'username':'eve2','password':'','role':'admin'},headers={'Origin':'https://evil.example'}); ok(s==403,'admin-POST från annan origin nekas')
        s,h,b=req(a,'/api/me'); me=json.loads(b); ok(me['username']=='anton' and me['isAdmin'],'api/me')
        s,h,b=req(a,'/api/owned',json.dumps({'items':['PLA Basic|10101']}).encode(),'PUT',{'Content-Type':'application/json','X-CSRF-Token':me['csrf']}); ok(s==200,'spara markeringar')
        s,h,b=req(a,'/api/owned',json.dumps({'items':['x']}).encode(),'PUT',{'Content-Type':'application/json'}); ok(s==403,'PUT utan csrf nekas')
        s,h,b=req(a,'/api/owned'); ok(json.loads(b)['items']==['PLA Basic|10101'],'läs markeringar')
        J={'Content-Type':'application/json','X-CSRF-Token':me['csrf']}
        req(a,'/api/cart',json.dumps({'key':'PLA Basic|10101','qty':1,'pack':'Refill'}).encode(),'PUT',J)
        s,h,b=req(a,'/api/cart',json.dumps({'key':'PLA Basic|10101','qty':2}).encode(),'PUT',J); ok(s==200 and json.loads(b)['items']==[{'key':'PLA Basic|10101','qty':2,'pack':'Refill'}],'vald förpackning behålls utan pack: %s'%b)
        s,h,b=req(a,'/api/cart',b'{"key":"PLA Basic|10101","qty":1e999}','PUT',J); ok(s==400,'qty=1e999 ger 400: %s'%s)
        sid='; '.join(f'{c.name}={c.value}' for c in acj)
        s,h,b=req(a,'/api/me',headers={'Cookie':'pref={"t":1}; '+sid}); ok(s==200,'främmande cookie med JSON stör inte sessionen: %s'%s)
        # önskelista, inställningar, flera i listan, prishistorik
        s,h,b=req(a,'/api/wish',json.dumps({'items':['PLA Basic|10101','PLA Basic|10101']}).encode(),'PUT',J); ok(s==200 and json.loads(b)['items']==['PLA Basic|10101'],'spara önskelista: %s'%b)
        s,h,b=req(a,'/api/wish',json.dumps({'items':['finns inte']}).encode(),'PUT',J); ok(s==400,'okänd färg på önskelistan nekas')
        s,h,b=req(a,'/api/wish'); ok(json.loads(b)['items']==['PLA Basic|10101'],'läs önskelista')
        s,h,b=req(a,'/api/prefs',json.dumps({'lowGrams':300}).encode(),'PUT',J); ok(s==200,'spara gräns för snart slut')
        s,h,b=req(a,'/api/me'); ok(json.loads(b)['prefs']['lowGrams']==300,'gränsen syns i api/me')
        s,h,b=req(a,'/api/cart/add',json.dumps({'keys':['PLA Basic|10101','PLA Basic|10100']}).encode(),'PUT',J); c=json.loads(b)
        ok(s==200 and c['added']==1 and {i['key']:i['qty'] for i in c['items']}=={'PLA Basic|10101':2,'PLA Basic|10100':1},'lägg flera i listan, befintliga orörda: %s'%b)
        s,h,b=req(a,'/api/history?key=PLA%20Basic%7C10101'); ok(s==200 and json.loads(b)['series']==[],'prishistorik utan data')
        s,h,b=req(a,'/api/history?key=x'); ok(s==400,'prishistorik för okänd färg nekas')
        # prisbevakning på önskelistan
        s,h,b=req(a,'/onskelista'); t8=csrf(b); ok(s==200 and 'Bevakning' in b and 'ntfy' in b,'önskelistesidan visas')
        s,h,b=req(a,'/onskelista/bevaka',{'csrf':t8,'key':'PLA Basic|10101','target':'100000','discount':''}); ok(s==303,'spara målpris')
        s,h,b=req(a,'/onskelista'); ok('Ditt pris är nått' in b,'träff visas när målpriset nås')
        s,h,b=req(a,'/api/wish'); ok(json.loads(b)['hits']==['PLA Basic|10101'],'träffen finns i api/wish')
        s,h,b=req(a,'/onskelista/bevaka',{'csrf':t8,'key':'PLA Basic|10101','target':'abc'}); ok(s==400,'ogiltigt målpris nekas')
        s,h,b=req(a,'/onskelista/bevaka',{'csrf':t8,'key':'PLA Basic|10100','target':'200'}); ok(s==400,'bevakning kräver att färgen är på önskelistan')
        s,h,b=req(a,'/onskelista/avisering',{'csrf':t8,'target':'https://evil.example/x','do':'spara'}); ok(s==400,'avisering till otillåten värd nekas')
        s,h,b=req(a,'/onskelista/avisering',{'csrf':t8,'target':'filament-test-123','do':'spara'}); ok(s==200 and 'https://ntfy.sh/filament-test-123' in b,'ntfy-ämne sparas')
        # köpt här
        s,h,b=req(a,'/inkopslista'); t9=csrf(b); shop=re.search(r'name="shop" value="([^"]+)"',b)
        ok(shop is not None,'köpt-knapp per butik')
        if shop:
            import html as _h
            s,h,b=req(a,'/inkopslista/kopt',{'csrf':t9,'shop':_h.unescape(shop.group(1))}); ok(s==200 and 'Markerat som köpt' in b and 'Köphistorik' in b,'markera som köpt: %s'%s)
            s,h,b=req(a,'/inkopslista/kopt',{'csrf':t9,'shop':'Finns inte'}); ok(s==409,'okänd butik ger 409')
        req(a,'/api/cart',json.dumps({'key':'PLA Basic|10101','qty':1}).encode(),'PUT',J)
        # kostnadsräknare (fast elpris: inga nätanrop)
        s,h,b=req(a,'/kostnad'); t10=csrf(b); ok(s==200 and 'Kostnadsräknare' in b and 'cost.js' in b,'kostnadsräknaren visas')
        f={'csrf':t10,'k1':'PLA Basic|10101','g1':'50','k2':'','g2':'10','h':'2','m':'30','w':'100','src':'fast','fixed':'2','extra':'0,6',
           'mode':'eget','kg':'200','wear':'','area':'SE3','vat':'1','do':'rakna'}
        s,h,b=req(a,'/kostnad/rakna',f); ok(s==200 and 'Kostnad för utskriften' in b and '12,65 kr' in b,'kostnad räknas ut (12 kr filament + 0,65 kr el)')
        s,h,b=req(a,'/kostnad/rakna',dict(f,do='spara',w='150')); ok(s==200 and 'sparade som standard' in b,'spara standardvärden')
        s,h,b=req(a,'/kostnad'); ok('value="150"' in b,'sparad effekt används som standard')
        s,h,b=req(a,'/kostnad/rakna',dict(f,g1='abc')); ok('måste vara ett tal' in b,'ogiltigt tal visas som fel')
        s,h,b=req(a,'/kostnad/rakna',{**f,'csrf':'fel'}); ok(s==403,'kostnadsberäkning kräver csrf')
        s,h,b=req(a,'/kostnad/tibber',{'csrf':t10,'token':'kort','do':'spara'}); ok(s==400,'ogiltigt Tibber-token nekas')
        os.makedirs(D+'/state',exist_ok=True); open(D+'/state/bambu_color_codes.json','w').write('[]')
        s,h,b=req(a,'/api/match',json.dumps({'items':[{'id':'GFA00','type':'PLA Basic','color':'#000000'}]}).encode(),'PUT',J); ok(s==200 and 'keys' in json.loads(b),'matcha slicerns filament: %s'%b)
        # budget och delning
        s,h,b=req(a,'/inkopslista'); t6=csrf(b); ok(s==200 and 'Dela listan' in b and 'data-print' in b,'inköpslistan har delning och utskrift')
        s,h,b=req(a,'/inkopslista/budget',{'csrf':t6,'budget':'1','do':'spara'}); ok(s==303,'spara budget')
        s,h,b=req(a,'/inkopslista'); ok('Kan vänta' in b or 'Inget köpbart' in b,'budget visar det som får vänta')
        s,h,b=req(a,'/inkopslista/budget',{'csrf':t6,'budget':'abc','do':'spara'}); ok(s==400,'ogiltig budget nekas')
        s,h,b=req(a,'/inkopslista/dela',{'csrf':t6}); link=re.search(r'value="(https?://[^"]+/delad/[^"]+)"',b); ok(s==200 and link,'skapa delningslänk')
        anon,_=client(); path=urllib.parse.urlsplit(link.group(1)).path
        s,h,b=req(anon,path); ok(s==200 and 'Delad av' in b and 'csrf' not in b,'delad lista visas utan inloggning')
        s,h,b=req(a,'/inkopslista/dela',{'csrf':t6}); s,h,b=req(anon,path); ok(s==404,'gammal länk slutar gälla när en ny skapas')
        s,h,b=req(anon,'/delad/'+'x'*43); ok(s==404,'påhittad länk ger 404')
        # säkerhetskopia
        s,h,b=req(a,'/admin'); t7=csrf(b)
        r=urllib.request.Request(B+'/admin/sakerhetskopia',data=urllib.parse.urlencode({'csrf':t7}).encode(),headers={'Host':f'127.0.0.1:{self.port}','Origin':B})
        with a.open(r) as x: z=x.read(); ct=x.headers['Content-Type']
        import io, zipfile; names=zipfile.ZipFile(io.BytesIO(z)).namelist(); ok(ct=='application/zip' and 'katalog.db' in names,'säkerhetskopia: %s'%names)
        s,h,b=req(a,'/admin/sakerhetskopia',{'csrf':'fel'}); ok(s==403,'säkerhetskopia kräver csrf')
        s,h,b=req(a,'/login?next=/%E2%98%83'); ok(s==303 and h.get('Location')=='/%E2%98%83','next utanför latin-1 procentkodas: %s %s'%(s,h.get('Location')))
        # lisa
        l,_=client(); s,h,b=req(l,'/login?next=/'); t2=csrf(b)
        s,h,b=req(l,'/login',{'csrf':t2,'username':'lisa','password':pw,'next':'//evil.com'}); ok(s==303 and h['Location']=='/konto','lisa loggar in, skickas till byt lösenord (och öppen redirect blockeras)')
        s,h,b=req(l,'/data.json'); ok(s==303,'katalogen spärrad tills lösenord bytts')
        s,h,b=req(l,'/admin'); ok(s==303 and h['Location']=='/konto','admin -> konto när byte krävs')
        s,h,b=req(l,'/konto'); t3=csrf(b)
        s,h,b=req(l,'/konto/losenord',{'csrf':t3,'current':pw,'new':'Gron-katt-hoppar-77','new2':'Gron-katt-hoppar-77'}); ok(s==200 and 'bytt' in b,'lisa byter lösenord')
        s,h,b=req(l,'/data.json'); ok(s==200,'lisa har tillgång nu')
        s,h,b=req(l,'/admin'); ok(s==403,'vanlig användare nekas admin')
        s,h,b=req(l,'/api/owned'); ok(json.loads(b)['items']==[],'markeringar är per användare')
        # brute force
        x,_=client(); s,h,b=req(x,'/login'); t4=csrf(b)
        X={'X-Forwarded-For':'10.9.9.9'}  # egen klient-IP (loopback räknas som betrodd proxy)
        codes=[req(x,'/login',{'csrf':t4,'username':'lisa','password':'fel-%d'%i},headers=X)[0] for i in range(8)]
        ok(codes==[401]*8,'samma svar oavsett låsning: %s'%codes)
        s,h,b=req(x,'/login',{'csrf':t4,'username':'lisa','password':'Gron-katt-hoppar-77'},headers=X); ok(s==401,'rätt lösenord nekas medan låst')
        y,_=client(); s,h,b=req(y,'/login'); t5=csrf(b)
        s,h,b=req(y,'/login',{'csrf':t5,'username':'lisa','password':'Gron-katt-hoppar-77'},headers={'X-Forwarded-For':'10.9.9.11'}); ok(s==303,'ägaren kan logga in från annan adress trots angrepp')
        s,h,b=req(x,'/login',{'csrf':t4,'username':'finnsinte','password':'x'},headers=X); ok(s==401 and 'Fel användarnamn eller lösenord' in b,'okänt konto ger samma fel')
        s,h,b=req(x,'/login',{'csrf':t4,'username':'finnsinte','password':'x'},headers=X); ok(s==429,'högst 10 inloggningsförsök per minut och IP')
        s,h,b=req(x,'/login',{'csrf':t4,'username':'finnsinte','password':'x'},headers={'X-Forwarded-For':'10.9.9.10'}); ok(s==401,'andra IP-adresser påverkas inte')
        # logout
        s,h,b=req(a,'/admin'); t=csrf(b)
        uid=sqlite3.connect(D+'/katalog.db').execute("select id from users where username='lisa'").fetchone()[0]
        s,h,b=req(a,f'/admin/anvandare/{uid}/las-upp',{'csrf':t}); ok('upplåst' in b,'admin låser upp')
        s,h,b=req(a,f'/admin/anvandare/{uid}/roll',{'csrf':t}); ok('administratör' in b,'admin gör lisa till admin')
        s,h,b=req(a,'/logout',{'csrf':'fel'},headers={'Origin':'https://evil.example'}); ok(s==303 and 'Set-Cookie' not in h,'utloggning från annan sajt ignoreras')
        s,h,b=req(a,'/data.json'); ok(s==200,'fortfarande inloggad efter falsk utloggning')
        s,h,b=req(a,'/logout',{'csrf':t}); ok(s==303,'logga ut')
        s,h,b=req(a,'/data.json'); ok(s==303,'session ogiltig efter utloggning')
        db=sqlite3.connect(D+'/katalog.db'); ok(all(len(r[0])==64 for r in db.execute('select id from sessions')),'endast token-hash lagras')
        ok(db.execute("select pw_hash from users").fetchone()[0].startswith('scrypt$32768'),'scrypt-hash')
        self.assertEqual(fails, [])


if __name__ == '__main__':
    unittest.main()
