"""Starta katalogen lokalt: python start.py"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import webbrowser

if __name__ == '__main__':
    folder = Path(__file__).resolve().parent / 'dist'
    handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
    try:
        server = ThreadingHTTPServer(('127.0.0.1', 8000), handler)
    except OSError as error:
        print('Kunde inte starta pa port 8000:', error)
        raise SystemExit(1)
    print('Katalogen finns pa http://127.0.0.1:8000')
    print('Stoppa med Ctrl+C. Lat detta fonster vara oppet.')
    threading.Timer(0.8, lambda: webbrowser.open('http://127.0.0.1:8000')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
