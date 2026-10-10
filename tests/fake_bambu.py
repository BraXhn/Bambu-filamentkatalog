"""Låtsasserver för Bambu Labs moln-API (bara för tester)."""
import base64, json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def jwt(exp):
    b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip('=')
    return b({'alg': 'none'}) + '.' + b({'exp': exp}) + '.sig'

TOKEN = jwt(int(time.time()) + 80 * 86400)
SPOOLS = [
    {'id': 1, 'filamentVendor': 'Bambu Lab', 'filamentName': 'PLA Basic', 'filamentId': 'GFA00', 'colors': ['#000000FF'], 'netWeight': 640, 'totalNetWeight': 1000, 'status': 0},
    {'id': 2, 'filamentVendor': 'Bambu Lab', 'filamentName': 'PLA Basic', 'filamentId': 'GFA00', 'colors': ['#000000FF'], 'netWeight': 1000, 'totalNetWeight': 1000, 'status': 0},
    {'id': 3, 'filamentVendor': 'Bambu Lab', 'filamentName': 'PETG HF', 'filamentId': 'GFG02', 'color': '#000000FF', 'netWeight': 210, 'totalNetWeight': 1000, 'status': 0},
    {'id': 4, 'filamentVendor': 'Bambu Lab', 'filamentName': 'PLA Matte', 'filamentId': 'GFA01', 'colors': ['#FFFFFFFF'], 'netWeight': 0, 'totalNetWeight': 1000, 'status': 1},
    {'id': 5, 'filamentVendor': 'Polymaker', 'filamentName': 'PolyTerra PLA', 'colors': ['#<script>'], 'netWeight': 500, 'totalNetWeight': 1000, 'status': 0},
]
COLORS = {'data': [
    {'fila_color_code': '10101', 'fila_id': 'GFA00', 'fila_type': 'PLA Basic', 'fila_color': ['#000000FF']},
    {'fila_color_code': '33102', 'fila_id': 'GFG02', 'fila_type': 'PETG HF', 'fila_color': ['#000000FF']},
]}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def out(self, code, obj=None, headers=()):
        body = json.dumps(obj).encode() if obj is not None else b''
        self.send_response(code)
        for k, v in headers: self.send_header(k, v)
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
    def do_GET(self):
        if self.path == '/colors.json': return self.out(200, COLORS)
        if self.path == '/api/csrf': return self.out(204, None, [('Set-Cookie', 'bbl_csrf_token=abc; Path=/')])
        if self.path.startswith('/v1/design-user-service/my/filament/v2'):
            if self.headers.get('Authorization') != 'Bearer ' + TOKEN: return self.out(401)
            return self.out(200, {'total': len(SPOOLS), 'hits': SPOOLS})
        self.out(404)
    def do_POST(self):
        b = self.body()
        if self.path == '/v1/user-service/user/login':
            if b.get('code') == '123456': return self.out(200, {'accessToken': TOKEN})
            if b.get('code'): return self.out(400, {'error': 'bad code'})
            if b.get('password') == 'ratt-losenord': return self.out(200, {'accessToken': TOKEN})
            if b.get('password') == 'kod-kravs': return self.out(200, {'loginType': 'verifyCode'})
            if b.get('password') == 'tfa-kravs': return self.out(200, {'loginType': 'tfa', 'tfaKey': 'K1'})
            return self.out(400, {'code': 1, 'error': 'Incorrect account or password.'})
        if self.path == '/v1/user-service/user/sendemail/code': return self.out(200, {})
        if self.path == '/api/sign-in/tfa':
            if b.get('tfaCode') == '654321' and self.headers.get('x-bbl-csrf-token') == 'abc':
                return self.out(200, {}, [('Set-Cookie', f'token={TOKEN}; Path=/; HttpOnly')])
            return self.out(400, {})
        self.out(404)

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
