#!/usr/bin/env python3
# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
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
from app.logger    import RunLogger
from app.upscaler  import Upscaler

# ── Resolution presets ───────────────────────────────────────────────────────
RESOLUTIONS: dict[str, tuple[int, int]] = {
    '64p':  (96,   64),
    '90p':  (160,  90),
    '140p': (256,  140),
    '240p': (426,  240),
    '360p': (640,  360),
    '480p': (854,  480),
    '720p': (1280, 720),
}


# ── Decoder ───────────────────────────────────────────────────────────────────
def create_decoder(codec: str = 'h264') -> av.CodecContext:
    name = 'hevc' if codec == 'h265' else 'h264'
    dec  = av.CodecContext.create(name, 'r')
    dec.open()
    return dec


def decode_packet(decoder: av.CodecContext, raw: bytes) -> list[np.ndarray]:
    frames = []
    try:
        for av_frame in decoder.decode(av.Packet(raw)):
            frames.append(av_frame.to_ndarray(format='bgr24'))
    except av.InvalidDataError:
        # UDP may deliver P-frames before the first I-frame — skip until resync
        pass
    return frames


# ── RX statistics ─────────────────────────────────────────────────────────────
class RxStats:
    def __init__(self, interval: float = 1.0, logger=None):
        self._interval      = interval
        self._bytes         = 0
        self._frames        = 0
        self._total_bytes   = 0
        self._total_frames  = 0
        self._t_start       = time.time()   # never reset — used for summary
        self._t0            = self._t_start
        self._logger        = logger
        self.kbps           = 0.0
        self.fps            = 0.0
        self.total_pkts     = 0
        self.frames_dropped = 0

    def update(self, nbytes: int) -> None:
        self._bytes        += nbytes
        self._frames       += 1
        self._total_bytes  += nbytes
        self._total_frames += 1
        self.total_pkts    += 1

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
        if self._logger:
            self._logger.info(f'[STATS]  kbps={self.kbps:.1f}  fps={self.fps:.1f}  '
                              f'frames={self.total_pkts}  dropped={self.frames_dropped}')
        return True

    def summary(self) -> str:
        elapsed  = time.time() - self._t_start
        avg_kbps = (self._total_bytes * 8) / 1000 / max(elapsed, 1)
        return (f'total_frames={self._total_frames}  '
                f'total_MB={self._total_bytes/1e6:.2f}  '
                f'avg_kbps={avg_kbps:.1f}  '
                f'dropped={self.frames_dropped}')


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
    parser.add_argument('--transport', default='udp', choices=['tcp', 'udp', 'uart'])
    # TCP / UDP
    parser.add_argument('--host',      default='127.0.0.1')
    parser.add_argument('--port',      type=int, default=5000)
    # UART
    parser.add_argument('--rx-port',   default='COM2')
    parser.add_argument('--baud',      type=int, default=3_000_000)
    parser.add_argument('--no-rtscts', action='store_true')
    # Codec — must match the transmitter
    parser.add_argument('--codec',     default='h264', choices=['h264', 'h265'])
    # GCS display resolution — any received frame is scaled to this size
    parser.add_argument('--upscale',     default='none',
                        choices=['none', 'lanczos', 'espcn'],
                        help='Scaling method to reach --display-res. '
                             'espcn = neural SR (torch).')
    parser.add_argument('--display-res', default='480p',
                        choices=list(RESOLUTIONS.keys()),
                        help='GCS display resolution (default 480p). Used when '
                             '--upscale is not none.')
    parser.add_argument('--no-overlay',  action='store_true')
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

    logger       = RunLogger('rx')
    stats        = RxStats(logger=logger)
    decoder      = create_decoder(args.codec)
    target_wh    = RESOLUTIONS[args.display_res] if args.upscale != 'none' else None
    upscaler     = Upscaler(args.upscale, target_wh)

    if args.transport == 'uart':
        logger.info(f'[CONFIG]  transport=uart  port={args.rx_port}  baud={args.baud}  '
                    f'codec={args.codec}  upscale={upscaler.label()}')
    else:
        logger.info(f'[CONFIG]  transport={args.transport}  host={args.host}:{args.port}  '
                    f'codec={args.codec}  upscale={upscaler.label()}')

    # Wire up sideband app layer
    cmd_sender   = CommandSender(transport)
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

                # Scale any received resolution to the GCS display resolution
                # (espcn = neural detail reconstruction; lanczos = classical).
                frame = upscaler.upscale(frame)

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
        logger.info('[EVENT]  KeyboardInterrupt')
    except ConnectionError as e:
        print(f"[RX] Connection lost: {e}")
        logger.info(f'[EVENT]  ConnectionError: {e}')
    finally:
        telem_sender.stop()
        try:
            decoder.decode(av.Packet(b''))
        except Exception:
            pass
        cv2.destroyAllWindows()
        transport.close()
        logger.close(stats.summary())
        print("[RX] Done.")


if __name__ == '__main__':
    main()
