"""A local server that fails instantly, for trigger tests that need a network failure.

Pointing a client at a closed port works too, but on Windows each refused connection
takes about 2 seconds, and clients that retry multiply that into minutes.

Usage in a trigger test:

    import sys
    sys.path.insert(0, r"<plugin root>/scripts")
    from fail_server import failing_server

    with failing_server("500") as url:      # url is like http://127.0.0.1:54321
        result = fetch_exchange_rate(base_url=url)

Modes:
    "500"       answers every request with HTTP 500
    "reset"     accepts the connection, then drops it (connection reset)
    "bad_json"  answers HTTP 200 with a body that isn't valid JSON

Standard library only.
"""

import contextlib
import socket
import struct
import threading

RESPONSES = {
    "500": b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"
           b"Content-Length: 35\r\nConnection: close\r\n\r\n"
           b'{"error": "simulated server error"}',
    "bad_json": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 12\r\nConnection: close\r\n\r\nnot json {{{",
}


def _read_request_head(conn):
    conn.settimeout(1)
    data = b""
    with contextlib.suppress(OSError):
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk


def _serve(sock, mode, stop):
    while not stop.is_set():
        try:
            conn, _ = sock.accept()
        except OSError:
            return  # socket closed by failing_server on exit
        with conn:
            if mode == "reset":
                # Linger 0 makes close() send a reset instead of a normal goodbye.
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                continue
            _read_request_head(conn)
            with contextlib.suppress(OSError):
                conn.sendall(RESPONSES[mode])


@contextlib.contextmanager
def failing_server(mode="500"):
    if mode not in ("500", "reset", "bad_json"):
        raise ValueError(f"unknown mode {mode!r}; use '500', 'reset' or 'bad_json'")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    stop = threading.Event()
    thread = threading.Thread(target=_serve, args=(sock, mode, stop), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        stop.set()
        sock.close()
