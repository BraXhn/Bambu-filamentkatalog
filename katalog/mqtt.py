"""Minimal MQTT 3.1.1-klient (bara stdlib): CONNECT, SUBSCRIBE, PUBLISH (QoS 0), PING.

Används för Bambu-skrivarnas status (lokalt på port 8883 med skrivarens åtkomstkod, eller via Bambu-molnet)
och för att skicka sensorer till Home Assistant.
"""
import os
import socket
import ssl
import struct
import threading
import time


class MqttError(Exception):
    pass


def _str(s):
    b = s.encode('utf-8')
    return struct.pack('>H', len(b)) + b


def _varint(n):
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class Client:
    def __init__(self, host, port=8883, username=None, password=None, tls=True, verify=True, client_id=None,
                 timeout=10, keepalive=60):
        self.host, self.port, self.username, self.password = host, port, username, password
        self.tls, self.verify, self.timeout, self.keepalive = tls, verify, timeout, keepalive
        self.client_id = client_id or 'katalog-' + os.urandom(4).hex()
        self.sock = None
        self._buf = b''
        self._lock = threading.Lock()
        self._pid = 0
        self._last_out = 0.0

    # --- anslutning ---
    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        if self.tls:
            ctx = ssl.create_default_context()
            if not self.verify:            # Bambu-skrivare på lokala nätet har självsignerat certifikat
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        flags = 0x02 | (0x80 if self.username else 0) | (0x40 if self.password else 0)
        var = _str('MQTT') + bytes([4, flags]) + struct.pack('>H', self.keepalive)
        payload = _str(self.client_id) + (_str(self.username) if self.username else b'') + (_str(self.password) if self.password else b'')
        try:
            self._send(0x10, var + payload)
            ptype, body = self._read_packet()
        except BaseException:
            self.sock.close()
            raise
        if ptype != 0x20 or len(body) < 2:
            raise MqttError('oväntat svar vid anslutning')
        if body[1] != 0:
            self.sock.close()
            raise MqttError({4: 'fel användarnamn eller lösenord', 5: 'inte behörig'}.get(body[1], f'anslutningen nekades ({body[1]})'))
        return self

    def close(self):
        try:
            if self.sock:
                self._send(0xE0, b'')
                self.sock.close()
        except OSError:
            pass
        self.sock = None

    # --- paket ---
    def _send(self, header, body):
        with self._lock:
            self.sock.sendall(bytes([header]) + _varint(len(body)) + body)
            self._last_out = time.time()

    def _recv(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise MqttError('anslutningen stängdes')
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_packet(self):
        first = self._recv(1)[0]
        mult, length = 1, 0
        for _ in range(4):
            b = self._recv(1)[0]
            length += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        if length > 4_000_000:
            raise MqttError('för stort meddelande')
        return first & 0xF0, self._recv(length)

    def subscribe(self, topic):
        self._pid = self._pid % 65535 + 1
        self._send(0x82, struct.pack('>H', self._pid) + _str(topic) + b'\x00')

    def publish(self, topic, payload, retain=False):
        data = payload if isinstance(payload, bytes) else str(payload).encode('utf-8')
        self._send(0x30 | (0x01 if retain else 0), _str(topic) + data)

    def ping_if_due(self):
        if time.time() - self._last_out > self.keepalive / 2:
            self._send(0xC0, b'')

    def messages(self, idle=1.0):
        """Generator med (topic, payload). Ger None vid tystnad (efter idle sekunder) så att anroparen kan göra annat."""
        self.sock.settimeout(idle)
        while True:
            try:
                ptype, body = self._read_packet()
            except (socket.timeout, TimeoutError):
                self.ping_if_due()
                yield None
                continue
            except ssl.SSLError as ex:
                if 'timed out' in str(ex):
                    self.ping_if_due()
                    yield None
                    continue
                raise MqttError(str(ex)) from None
            if ptype == 0x30:
                n = struct.unpack('>H', body[:2])[0]
                topic = body[2:2 + n].decode('utf-8', 'replace')
                yield topic, body[2 + n:]
            self.ping_if_due()
