"""Skriver nginx-konfigurationen och startar nginx som barnprocess."""
import os
import subprocess
from pathlib import Path

from . import config

CONF = Path('/tmp/nginx/nginx.conf')

CSP = ("default-src 'self'; img-src 'self' https://cdn.shopify.com data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")


def render():
    real_ip = '\n'.join(f'    set_real_ip_from {n};' for n in config.TRUSTED_PROXIES)
    geo_trusted = '\n'.join(f'        {n} 1;' for n in config.TRUSTED_PROXIES)
    app = f'http://127.0.0.1:{config.APP_PORT}'
    return f"""# Genereras vid start av katalog/nginx.py – ändra inte här.
worker_processes 2;  # räcker gott; "auto" ger en process per kärna och kan slå i pids-gränsen
pid /tmp/nginx/nginx.pid;
error_log stderr warn;
events {{ worker_connections 1024; }}

http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    client_body_temp_path /tmp/nginx/client;
    proxy_temp_path /tmp/nginx/proxy;
    fastcgi_temp_path /tmp/nginx/fastcgi;
    uwsgi_temp_path /tmp/nginx/uwsgi;
    scgi_temp_path /tmp/nginx/scgi;

    # Logga utan cookies eller frågesträngar (next=… kan innehålla sökvägar).
    log_format main '$remote_addr "$request_method $uri" $status $body_bytes_sent "$http_user_agent" $request_time';
    access_log /dev/stdout main;

    server_tokens off;
    sendfile on;
    tcp_nopush on;
    keepalive_timeout 30s;
    client_max_body_size 64k;
    client_body_timeout 15s;
    client_header_timeout 15s;
    send_timeout 30s;
    charset utf-8;
    charset_types application/json application/javascript text/javascript text/css text/plain;

    # Riktig klient-IP bakom omvänd proxy (bara från betrodda adresser).
{real_ip}
    real_ip_header X-Forwarded-For;
    real_ip_recursive on;

    # Inloggning: max 10 POST per minut och IP (GET begränsas inte).
    map $request_method $login_limit_key {{ POST $binary_remote_addr; default ""; }}
    limit_req_zone $login_limit_key zone=login:10m rate=10r/m;
    limit_req_zone $binary_remote_addr zone=general:10m rate=30r/s;
    limit_req_status 429;
    limit_conn_zone $binary_remote_addr zone=perip:10m;

    # X-Forwarded-Proto godtas bara från betrodda proxyer (TRUSTED_PROXIES).
    geo $realip_remote_addr $from_trusted_proxy {{
        default 0;
{geo_trusted}
    }}
    map "$from_trusted_proxy:$http_x_forwarded_proto" $fwd_proto {{ "1:https" https; default $scheme; }}
    map $fwd_proto $hsts {{ https "max-age=31536000"; default ""; }}
    map $uri $cache_control {{
        ~^/(login|logout|setup|konto|admin|api)  "no-store";
        ~^/assets/                                "public, max-age=86400";
        default                                   "no-cache";
    }}

    gzip on;
    gzip_vary on;
    gzip_types application/json application/javascript text/javascript text/css text/plain image/svg+xml;
    gzip_min_length 1024;

    server {{
        listen {config.LISTEN_PORT} default_server;
        server_name _;
        absolute_redirect off;   # relativa Location-rubriker: fungerar bakom proxy och portmappning
        root {config.SEED_DIR};
        index index.html;

        limit_req zone=general burst=80 nodelay;
        limit_conn perip 40;

        add_header Content-Security-Policy "{CSP}" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "same-origin" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), interest-cohort=()" always;
        add_header Cross-Origin-Opener-Policy "same-origin" always;
        add_header Cross-Origin-Resource-Policy "same-origin" always;
        add_header Strict-Transport-Security $hsts always;
        add_header Cache-Control $cache_control always;
        add_header X-Robots-Tag "noindex, nofollow" always;
        # (inga add_header i location-blocken, så rubrikerna ovan ärvs överallt)

        location = /healthz {{ access_log off; default_type text/plain; return 200 "ok\\n"; }}
        location = /robots.txt {{ default_type text/plain; return 200 "User-agent: *\\nDisallow: /\\n"; }}

        # Kontroll av inloggning inför varje skyddad fil.
        location = /_auth {{
            internal;
            proxy_pass {app}/auth/check;
            proxy_pass_request_body off;
            proxy_set_header Content-Length "";
            proxy_set_header Host $http_host;
            proxy_set_header Cookie $http_cookie;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-Proto $fwd_proto;
        }}
        location @login {{ return 303 /login?next=$request_uri; }}

        # Stil och ikon för inloggningssidan (inga hemligheter).
        location /assets/ {{ alias {config.ASSETS_DIR}/; access_log off; }}

        # Sidor som Python-appen hanterar (den kontrollerar själv inloggning och behörighet).
        location ~ ^/(login|setup)$ {{
            limit_req zone=login burst=5 nodelay;
            include /tmp/nginx/proxy_app.conf;
        }}
        location ~ ^/(logout|konto|admin|api)(/|$) {{
            include /tmp/nginx/proxy_app.conf;
        }}

        # Prisdata som uppdateras nattligen ligger på datavolymen.
        location = /offers.json {{
            auth_request /_auth;
            error_page 401 = @login;
            root {config.WWW_DIR};
            gzip_static on;
        }}

        # Själva katalogen (index.html, app.js, data.json …) – bara för inloggade.
        location ~ \\.gz$ {{ return 404; }}
        location / {{
            auth_request /_auth;
            error_page 401 = @login;
            gzip_static on;
            try_files $uri $uri/ =404;
        }}
    }}
}}
"""


PROXY_APP = f"""proxy_pass http://127.0.0.1:{config.APP_PORT};
proxy_http_version 1.0;
proxy_set_header Connection "close";
proxy_set_header Host $http_host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-Proto $fwd_proto;
proxy_set_header X-Forwarded-For "";
proxy_set_header X-Forwarded-Host "";
proxy_read_timeout 60s;
"""


def start():
    CONF.parent.mkdir(parents=True, exist_ok=True)
    for d in ('client', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
        (CONF.parent / d).mkdir(exist_ok=True)
    CONF.write_text(render(), encoding='utf-8')
    (CONF.parent / 'proxy_app.conf').write_text(PROXY_APP, encoding='utf-8')
    test = subprocess.run(['nginx', '-e', 'stderr', '-t', '-q', '-c', str(CONF)], capture_output=True, text=True)
    if test.returncode != 0:
        raise SystemExit('nginx-konfigurationen är ogiltig:\n' + test.stderr)
    print(f'[nginx] lyssnar på port {config.LISTEN_PORT}', flush=True)
    return subprocess.Popen(['nginx', '-e', 'stderr', '-c', str(CONF), '-g', 'daemon off;'], env=os.environ.copy())
