"""Låtsas-MQTT-broker för tester: godkänner CONNECT (valfritt lösenordskrav), svarar på SUBSCRIBE med ett
inspelat meddelande och sparar allt som publiceras."""
import socket
import struct
import threading


class Broker:
    def __init__(self, password=None, reports=None):
        self.password, self.reports = password, reports or {}
        self.published = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    @staticmethod
    def _read(c):
        h = c.recv(1)
        if not h:
            raise EOFError
        mult, n = 1, 0
        while True:
            b = c.recv(1)[0]
            n += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        body = b''
        while len(body) < n:
            body += c.recv(n - len(body))
        return h[0], body

    def _serve(self, c):
        try:
            while True:
                h, body = self._read(c)
                t = h & 0xF0
                if t == 0x10:
                    ok = True
                    if self.password is not None:
                        ok = self.password.encode() in body
                    c.sendall(bytes([0x20, 2, 0, 0 if ok else 5]))
                    if not ok:
                        return
                elif t == 0x80:
                    pid = body[:2]
                    n = struct.unpack('>H', body[2:4])[0]
                    topic = body[4:4 + n].decode()
                    c.sendall(bytes([0x90, 3]) + pid + b'\x00')
                    for payload in self.reports.get(topic, []):
                        tb = topic.encode()
                        pkt = struct.pack('>H', len(tb)) + tb + payload.encode()
                        c.sendall(bytes([0x30, len(pkt)]) if len(pkt) < 128 else bytes([0x30]) + _vl(len(pkt)))
                        c.sendall(pkt)
                elif t == 0x30:
                    n = struct.unpack('>H', body[:2])[0]
                    self.published.append((body[2:2 + n].decode(), body[2 + n:].decode(), bool(h & 1)))
                elif t == 0xC0:
                    c.sendall(bytes([0xD0, 0]))
                elif t == 0xE0:
                    return
        except (EOFError, OSError, IndexError):
            pass
        finally:
            c.close()

    def close(self):
        self.sock.close()


def _vl(n):
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)
