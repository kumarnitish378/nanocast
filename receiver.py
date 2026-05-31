#!/usr/bin/env python3
"""
H.264 Video Receiver  —  with telemetry reporting + live command control.

Usage
─────
# TCP (default)
python receiver.py

# UART
python receiver.py --transport uart --rx-port COM2

Keyboard shortcuts (video window)
──────────────────────────────────
  1-4    Set resolution: 240p / 360p / 480p / 720p
  +/=    Raise quality  (CRF -2)
  -      Lower quality  (CRF +2, smaller bitrate)
  k      Force keyframe
  p      Pause / Resume toggle
  q      Quit
"""

import time
import argparse

import cv2
import av
import numpy as np

from transport import get_transport
from app.command   import CommandSender
from app.telemetry import TelemetrySender

# ── Resolution presets ───────────────────────────────────────────────────────
RESOLUTIONS: dict[str, tuple[int, int]] = {
    '140p': (256,  140),
    '240p': (426,  240),
    '360p': (640,  360),
    '480p': (854,  480),
    '720p': (1280, 720),
}


# ── Decoder ───────────────────────────────────────────────────────────────────
def create_decoder() -> av.CodecContext:
    dec = av.CodecContext.create('h264', 'r')
    dec.open()
    return dec


def decode_packet(decoder: av.CodecContext, raw: bytes) -> list[np.ndarray]:
    frames = []
    try:
        for av_frame in decoder.decode(av.Packet(raw)):
            bgr  = av_frame.to_ndarray(format='bgr24')
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            bgr  = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            frames.append(bgr)
    except av.InvalidDataError:
        # UDP may deliver P-frames before the first I-frame — skip until resync
        pass
    return frames


# ── RX statistics ─────────────────────────────────────────────────────────────
class RxStats:
    def __init__(self, interval: float = 1.0):
        self._interval  = interval
        self._bytes     = 0
        self._frames    = 0
        self._t0        = time.time()
        self.kbps       = 0.0
        self.fps        = 0.0
        self.total_pkts = 0
        self.frames_dropped = 0

    def update(self, nbytes: int) -> None:
        self._bytes     += nbytes
        self._frames    += 1
        self.total_pkts += 1

    def tick(self) -> bool:
        now     = time.time()
        elapsed = now - self._t0
        if elapsed < self._interval:
            return False
        self.kbps    = (self._bytes * 8) / 1000 / elapsed
        self.fps     = self._frames / elapsed
        self._bytes  = 0
        self._frames = 0
        self._t0     = now
        print(f"[RX] {self.kbps:8.1f} kbps  |  {self.fps:5.1f} fps  "
              f"|  frames={self.total_pkts}")
        return True


# ── Overlay rendering ─────────────────────────────────────────────────────────
def draw_overlay(frame: np.ndarray, stats: RxStats,
                 hint: str, paused: bool) -> np.ndarray:
    h, w    = frame.shape[:2]
    overlay = frame.copy()

    # Top-left stats box
    cv2.rectangle(overlay, (0, 0), (290, 82), (10, 10, 10), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    font, sc, co, th = cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 230, 80), 1
    cv2.putText(frame, f"Data Rate : {stats.kbps:7.1f} kbps", (8, 22), font, sc, co, th)
    cv2.putText(frame, f"FPS       : {stats.fps:.1f}",         (8, 44), font, sc, co, th)
    cv2.putText(frame, f"Res       : {w}x{h}   pkts={stats.total_pkts}",
                (8, 66), font, sc, co, th)

    # PAUSED banner
    if paused:
        cv2.putText(frame, "PAUSED", (w//2 - 60, h//2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 60, 255), 2)

    # Bottom hint bar
    bar_y = h - 4
    cv2.rectangle(overlay, (0, h - 22), (w, h), (10, 10, 10), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
    cv2.putText(frame, hint, (6, bar_y), cv2.FONT_HERSHEY_SIMPLEX,
                0.42, (200, 200, 200), 1)

    return frame


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='H.264 Video Receiver')
    parser.add_argument('--transport', default='tcp', choices=['tcp', 'udp', 'uart'])
    # TCP / UDP
    parser.add_argument('--host',      default='127.0.0.1')
    parser.add_argument('--port',      type=int, default=5000)
    # UART
    parser.add_argument('--rx-port',   default='COM2')
    parser.add_argument('--baud',      type=int, default=3_000_000)
    parser.add_argument('--no-rtscts', action='store_true')
    # Display
    parser.add_argument('--res',       default=None, choices=list(RESOLUTIONS.keys()),
                        help='Force display resolution (optional)')
    parser.add_argument('--no-overlay', action='store_true')
    args = parser.parse_args()

    if args.transport == 'uart':
        print(f"[RX] Transport : UART  on  {args.rx_port}  @  {args.baud:,} baud")
    else:
        print(f"[RX] Transport : {args.transport.upper()}  on  {args.host}:{args.port}")

    # ── Setup ─────────────────────────────────────────────────────────────────
    transport = get_transport(
        args.transport, mode='recv',
        host=args.host, port=args.port,
        rx_port=args.rx_port, baud=args.baud, rtscts=not args.no_rtscts,
    )

    stats      = RxStats()
    decoder    = create_decoder()

    # Wire up sideband app layer
    cmd_sender  = CommandSender(transport)
    telem_sender = TelemetrySender(transport, stats, interval=1.0)
    telem_sender.start()

    hint   = CommandSender.hint_text()
    paused = False

    print("[RX] Decoding … (press Q in video window to stop)\n")

    try:
        while True:
            raw = transport.recv()
            stats.update(len(raw))
            stats.tick()

            frames = decode_packet(decoder, raw)
            for frame in frames:

                # Optional force resolution
                if args.res:
                    tw, th = RESOLUTIONS[args.res]
                    if frame.shape[1] != tw or frame.shape[0] != th:
                        frame = cv2.resize(frame, (tw, th))

                if not args.no_overlay:
                    frame = draw_overlay(frame, stats, hint, paused)

                cv2.imshow('H.264 Receiver', frame)
                key = cv2.waitKey(1) & 0xFF

                # Let CommandSender handle the key; track pause state locally too
                consumed = cmd_sender.handle_key(key)

                if key == ord('p'):
                    paused = not paused

                if key == ord('q'):
                    return   # quit

    except KeyboardInterrupt:
        print("\n[RX] Interrupted.")
    except ConnectionError as e:
        print(f"[RX] Connection lost: {e}")
    finally:
        telem_sender.stop()
        try:
            decoder.decode(av.Packet(b''))
        except Exception:
            pass
        cv2.destroyAllWindows()
        transport.close()
        print("[RX] Done.")


if __name__ == '__main__':
    main()
