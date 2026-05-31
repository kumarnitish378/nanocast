# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
"""
TCP transport with sideband.

The transmitter is the server — start it first, it waits for the receiver.
The receiver is the client — connects when ready.

Video channel  : TCPSender listens on port,   TCPReceiver connects.
Side-channel   : TCPSender listens on port+1, TCPReceiver connects.

Frame (video)   : [LEN:4 BE][PAYLOAD]
Frame (sideband): [CH:1][LEN:4 BE][PAYLOAD]
No CRC — TCP guarantees delivery and order.
"""
import socket
import struct
import threading
from typing import Callable
from .base import TransportSender, TransportReceiver

# ── Framing constants ─────────────────────────────────────────────────────────
_V_HDR   = '>I'
_V_SIZE  = struct.calcsize(_V_HDR)   # 4 bytes

_SB_HDR  = '>BI'                     # channel(1) + length(4)
_SB_SIZE = struct.calcsize(_SB_HDR)  # 5 bytes


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed.")
        buf += chunk
    return buf


def _make_server(host: str, port: int) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    return srv


# ── Sideband helper ───────────────────────────────────────────────────────────
class _TCPSideband:
    """Bidirectional side-channel over an already-connected TCP socket."""

    def __init__(self):
        self._sock: socket.socket | None = None
        self._callbacks: dict[int, Callable] = {}
        self._tx_lock = threading.Lock()
        self._stop    = threading.Event()

    def attach(self, sock: socket.socket) -> None:
        self._sock = sock
        threading.Thread(target=self._rx_loop, daemon=True,
                         name='tcp-sb-rx').start()

    def send(self, data: bytes, channel: int) -> int:
        if self._sock is None:
            return 0
        frame = struct.pack(_SB_HDR, channel, len(data)) + data
        try:
            with self._tx_lock:
                self._sock.sendall(frame)
            return len(frame)
        except OSError:
            return 0

    def on_channel(self, ch: int, callback: Callable) -> None:
        self._callbacks[ch] = callback

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                hdr        = _recv_exact(self._sock, _SB_SIZE)
                ch, length = struct.unpack(_SB_HDR, hdr)
                payload    = _recv_exact(self._sock, length)
                cb = self._callbacks.get(ch)
                if cb:
                    try:
                        cb(payload)
                    except Exception as e:
                        print(f"[TCP-SB] callback error ch={ch}: {e}")
            except Exception as e:
                if not self._stop.is_set():
                    print(f"[TCP-SB] RX error: {e}")
                break

    def close(self) -> None:
        self._stop.set()
        if self._sock:
            try: self._sock.close()
            except OSError: pass


# ── TCPSender (server — transmitter side) ────────────────────────────────────
class TCPSender(TransportSender):
    """
    Binds port (video) and port+1 (sideband).
    Blocks in __init__ until the receiver connects to both.
    Start the transmitter first; the receiver connects when ready.
    """

    def __init__(self, host: str, port: int):
        vid_srv = _make_server(host, port)
        sb_srv  = _make_server(host, port + 1)
        vid_srv.settimeout(1.0)
        sb_srv.settimeout(1.0)
        print(f"[TCP-TX] Listening on {host}:{port} — waiting for receiver … (Ctrl-C to abort)")

        conn = None
        while conn is None:
            try:
                conn, (rx_host, _) = vid_srv.accept()
            except socket.timeout:
                continue
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        vid_srv.close()
        self._conn = conn
        print(f"[TCP-TX] Receiver connected from {rx_host}")

        sb_conn = None
        while sb_conn is None:
            try:
                sb_conn, _ = sb_srv.accept()
            except socket.timeout:
                continue
        sb_conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sb_srv.close()

        self._sb = _TCPSideband()
        self._sb.attach(sb_conn)

    def send(self, data: bytes) -> int:
        self._conn.sendall(struct.pack(_V_HDR, len(data)) + data)
        return _V_SIZE + len(data)

    def send_side(self, data: bytes, channel: int = 1) -> int:
        return self._sb.send(data, channel)

    def on_side_channel(self, channel: int, callback: Callable) -> None:
        self._sb.on_channel(channel, callback)

    def close(self) -> None:
        self._conn.close()
        self._sb.close()


# ── TCPReceiver (client — receiver side) ─────────────────────────────────────
class TCPReceiver(TransportReceiver):
    """
    Connects to transmitter on port (video) and port+1 (sideband).
    Retries for up to ~15 s so the receiver can be started before the transmitter
    is fully ready, or slightly after.
    """

    def __init__(self, host: str, port: int, retries: int = 30):
        import time
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for attempt in range(retries):
            try:
                sock.connect((host, port))
                break
            except OSError:
                if attempt == 0:
                    print(f"[TCP-RX] Waiting for transmitter on {host}:{port} …")
                time.sleep(0.5)
        else:
            raise ConnectionError(
                f"[TCP-RX] Could not reach transmitter at {host}:{port} "
                f"after {retries} attempts."
            )
        self._sock = sock
        print(f"[TCP-RX] Connected to transmitter at {host}:{port}")

        sb_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sb_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sb_sock.connect((host, port + 1))

        self._sb = _TCPSideband()
        self._sb.attach(sb_sock)

    def recv(self) -> bytes:
        length = struct.unpack(_V_HDR, _recv_exact(self._sock, _V_SIZE))[0]
        return _recv_exact(self._sock, length)

    def send_side(self, data: bytes, channel: int = 1) -> int:
        return self._sb.send(data, channel)

    def on_side_channel(self, channel: int, callback: Callable) -> None:
        self._sb.on_channel(channel, callback)

    def close(self) -> None:
        self._sock.close()
        self._sb.close()
