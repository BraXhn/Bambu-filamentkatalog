"""Flödestest av inloggning, behörighet, CSRF och kontolåsning mot Python-appen (utan nginx)."""
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

        a,_=client()
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
        ok(sqlite3.connect(D+'/katalog.db').execute("select locked_until>0 from users where username='lisa'").fetchone()[0]==1,'kontot är låst i databasen')
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
