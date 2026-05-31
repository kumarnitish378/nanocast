# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
"""
transport/udp.py — UDP video transport with sideband.

UDP preserves datagram boundaries, so each send() = one datagram = one H.264
packet. No length-prefix framing needed for video.

Port layout:
  port    video           TX -> RX
  port+1  sideband        RX -> TX  (CH_CMD, CH_TELEM)
  port+2  sideband        TX -> RX  (CH_ACK)

Sideband frame:  [CH:1B][PAYLOAD]

Behaviour:
  - No connection handshake — start either side first.
  - UDPReceiver learns the transmitter's address from the first video datagram.
  - Packets may be lost or reordered; the H.264 decoder handles this gracefully.
  - Max safe payload ~65 KB per datagram (UDP limit). H.264 at 140p-480p is
    well within this; very large I-frames at 720p may rarely be fragmented by
    the OS and silently dropped.
"""
import socket
import struct
import threading
from typing import Callable
from .base import TransportSender, TransportReceiver

_SB_CH_FMT  = '>B'
_SB_CH_SIZE = struct.calcsize(_SB_CH_FMT)   # 1 byte
_MAX_DGRAM  = 65_507


# ── Sideband helper ───────────────────────────────────────────────────────────
class _UDPSideband:
    """
    One RX socket (binds recv_port) + one TX socket (sends to dest).
    dest is set lazily so either side can start first.
    """

    def __init__(self, recv_port: int):
        self._rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._rx.bind(('', recv_port))
        self._rx.settimeout(1.0)

        self._tx   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._dest: tuple | None = None
        self._callbacks: dict[int, Callable] = {}
        self._stop = threading.Event()

        threading.Thread(target=self._rx_loop, daemon=True,
                         name=f'udp-sb-{recv_port}').start()

    def set_dest(self, host: str, port: int) -> None:
        self._dest = (host, port)

    def send(self, data: bytes, channel: int) -> int:
        if self._dest is None:
            return 0
        frame = struct.pack(_SB_CH_FMT, channel) + data
        try:
            self._tx.sendto(frame, self._dest)
            return len(frame)
        except OSError:
            return 0

    def on_channel(self, ch: int, callback: Callable) -> None:
        self._callbacks[ch] = callback

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._rx.recvfrom(_MAX_DGRAM)
                if len(data) < _SB_CH_SIZE:
                    continue
                ch      = data[0]
                payload = data[_SB_CH_SIZE:]
                cb = self._callbacks.get(ch)
                if cb:
                    try:
                        cb(payload)
                    except Exception as e:
                        print(f"[UDP-SB] callback error ch={ch}: {e}")
            except socket.timeout:
                continue
            except OSError as e:
                if not self._stop.is_set():
                    print(f"[UDP-SB] RX error: {e}")
                break

    def close(self) -> None:
        self._stop.set()
        for s in (self._rx, self._tx):
            try: s.close()
            except OSError: pass


# ── UDPSender (transmitter side) ──────────────────────────────────────────────
class UDPSender(TransportSender):
    """
    Sends video datagrams to (host, port).
    Sideband in  : listens on port+1 for commands/telemetry from receiver.
    Sideband out : sends ACKs to (host, port+2).
    Start either side first — no handshake required.
    """

    def __init__(self, host: str, port: int):
        self._dest = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._sb = _UDPSideband(recv_port=port + 1)
        self._sb.set_dest(host, port + 2)

        print(f"[UDP-TX] Video -> {host}:{port}  "
              f"| sideband rx=:{port+1}  tx=:{port+2}")

    def send(self, data: bytes) -> int:
        if len(data) > _MAX_DGRAM:
            print(f"[UDP-TX] Warning: packet {len(data)}B exceeds UDP limit — may be dropped")
        try:
            self._sock.sendto(data, self._dest)
            return len(data)
        except OSError:
            return 0

    def send_side(self, data: bytes, channel: int = 1) -> int:
        return self._sb.send(data, channel)

    def on_side_channel(self, channel: int, callback: Callable) -> None:
        self._sb.on_channel(channel, callback)

    def close(self) -> None:
        self._sock.close()
        self._sb.close()


# ── UDPReceiver (receiver side) ───────────────────────────────────────────────
class UDPReceiver(TransportReceiver):
    """
    Listens for video datagrams on (host, port).
    Learns transmitter address from the first video datagram.
    Sideband in  : listens on port+2 for ACKs from transmitter.
    Sideband out : sends commands/telemetry to (tx_host, port+1).
    Start either side first — no handshake required.
    """

    def __init__(self, host: str, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))

        self._sb       = _UDPSideband(recv_port=port + 2)
        self._sb_port  = port + 1
        self._tx_known = False

        print(f"[UDP-RX] Listening for video on {host}:{port}  "
              f"| sideband rx=:{port+2}  tx=:{port+1}")

    def recv(self) -> bytes:
        data, (tx_host, _) = self._sock.recvfrom(_MAX_DGRAM)
        if not self._tx_known:
            self._tx_known = True
            self._sb.set_dest(tx_host, self._sb_port)
            print(f"[UDP-RX] Transmitter at {tx_host} — sideband enabled")
        return data

    def send_side(self, data: bytes, channel: int = 1) -> int:
        return self._sb.send(data, channel)

    def on_side_channel(self, channel: int, callback: Callable) -> None:
        self._sb.on_channel(channel, callback)

    def close(self) -> None:
        self._sock.close()
        self._sb.close()
