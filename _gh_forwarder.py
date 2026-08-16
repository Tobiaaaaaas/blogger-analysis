# -*- coding: utf-8 -*-
"""临时 HTTP CONNECT 转发器：把 github.com:443 的 CONNECT 请求转发到可用边缘 IP 140.82.112.3。
用法: python _gh_forwarder.py   (监听 127.0.0.1:7891)
"""
import socket, threading

TARGET = ('140.82.121.3', 443)          # 主用：2.8s 稳定
FALLBACKS = [('140.82.121.4', 443), ('140.82.116.4', 443)]
LISTEN = ('127.0.0.1', 7891)


def pipe(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def handle(client):
    try:
        client.settimeout(30)
        req = b''
        while b'\r\n\r\n' not in req:
            chunk = client.recv(4096)
            if not chunk:
                return
            req += chunk
        line = req.split(b'\r\n')[0].decode('latin1')
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != 'CONNECT':
            return
        hostport = parts[1]
        host, _, port = hostport.partition(':')
        port = int(port)
        if host in ('github.com', 'www.github.com'):
            upstream = None
            for cand in (TARGET,) + tuple(FALLBACKS):
                try:
                    upstream = socket.create_connection(cand, timeout=10)
                    break
                except Exception:
                    continue
            if upstream is None:
                upstream = socket.create_connection(TARGET, timeout=20)
        else:
            upstream = socket.create_connection((host, port), timeout=20)
        client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        t1 = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
        t1.start()
        t2.start()
        t1.join(600)
        t2.join(600)
    except Exception as e:
        print('forward err:', e)
    finally:
        try:
            client.close()
        except Exception:
            pass


s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(LISTEN)
s.listen(50)
print(f'forwarder ready {LISTEN} -> {TARGET}', flush=True)
while True:
    c, _ = s.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
