import socket
import select
import threading

LISTEN_HOST = '0.0.0.0'
LISTEN_PORT = 17890
TARGET_HOST = '127.0.0.1'
TARGET_PORT = 7890


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass


def handle(client):
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        upstream.connect((TARGET_HOST, TARGET_PORT))
        client.setblocking(False)
        upstream.setblocking(False)
        sockets = [client, upstream]
        while True:
            readable, _, _ = select.select(sockets, [], [], 60)
            if not readable:
                continue
            for s in readable:
                data = s.recv(65536)
                if not data:
                    return
                peer = upstream if s is client else client
                peer.sendall(data)
    except Exception:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            upstream.close()
        except Exception:
            pass


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(128)
    while True:
        c, _ = server.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == '__main__':
    main()
