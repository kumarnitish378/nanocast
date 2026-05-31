#!/usr/bin/env python3
# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
"""
H.264 Video Transmitter  —  with live command control + telemetry display.

Usage
─────
# TCP, 480p, default
python transmitter.py

# UART, 240p
python transmitter.py --transport uart --tx-port COM1 --res 240p

# From file
python transmitter.py --source /path/to/video.mp4

Sideband (when transport supports it)
──────────────────────────────────────
Receives commands from receiver (res / crf / fps / keyframe / pause / stop).
Sends ACK after each command is applied.
Prints incoming telemetry from receiver.
"""

import time
import argparse
from fractions import Fraction

import cv2
import av

from transport import get_transport
from app.protocol import (
    CH_ACK,
    CMD_SET_RES, CMD_SET_CRF, CMD_SET_FPS,
    CMD_KEYFRAME, CMD_SET_COLOR, CMD_PAUSE, CMD_RESUME, CMD_STOP,
)
from app import protocol as proto
from app.command   import StreamController, CommandReceiver, StreamState
from app.telemetry import TelemetryReceiver
from app.logger    import RunLogger

# ── Resolution presets ───────────────────────────────────────────────────────
RESOLUTIONS: dict[str, tuple[int, int]] = {
    '140p': (256,  140),
    '240p': (426,  240),
    '360p': (640,  360),
    '480p': (854,  480),
    '720p': (1280, 720),
}


# ── Encoder factory ───────────────────────────────────────────────────────────
def create_encoder(width: int, height: int, fps: int, crf: int) -> av.CodecContext:
    enc            = av.CodecContext.create('libx264', 'w')
    enc.width      = width
    enc.height     = height
    enc.pix_fmt    = 'yuv420p'
    enc.framerate  = fps
    enc.time_base  = Fraction(1, fps)
    enc.options    = {
        'preset':      'ultrafast',
        'tune':        'zerolatency',
        'crf':         str(crf),
        'x264-params': 'repeat-headers=1',
    }
    enc.open()
    return enc


# ── Frame conversion ──────────────────────────────────────────────────────────
def gray_to_av_frame(gray, pts: int,
                     force_keyframe: bool = False) -> av.VideoFrame:
    import numpy as np
    h, w  = gray.shape
    yuv   = np.full((h * 3 // 2, w), 128, dtype=np.uint8)
    yuv[:h] = gray
    frame     = av.VideoFrame.from_ndarray(yuv, format='yuv420p')
    frame.pts = pts
    if force_keyframe:
        frame.pict_type = 1  # AV_PICTURE_TYPE_I
    return frame


def bgr_to_av_frame(bgr, pts: int,
                    force_keyframe: bool = False) -> av.VideoFrame:
    frame     = av.VideoFrame.from_ndarray(bgr, format='bgr24')
    frame.pts = pts
    if force_keyframe:
        frame.pict_type = 1  # AV_PICTURE_TYPE_I
    return frame


# ── TX stats ──────────────────────────────────────────────────────────────────
class TxStats:
    def __init__(self, interval: float = 1.0, logger=None):
        self._interval    = interval
        self._bytes       = self._frames = 0
        self._total_bytes = self._total_frames = 0
        self._t_start     = time.time()   # never reset — used for summary
        self._t0          = self._t_start
        self._logger      = logger

    def update(self, sent: int) -> None:
        self._bytes        += sent
        self._frames       += 1
        self._total_bytes  += sent
        self._total_frames += 1

    def tick(self, label: str) -> None:
        now     = time.time()
        elapsed = now - self._t0
        if elapsed < self._interval:
            return
        kbps = (self._bytes * 8) / 1000 / elapsed
        fps  = self._frames / elapsed
        line = f"[TX] {label}  |  {kbps:8.1f} kbps  |  {fps:5.1f} fps"
        print(line)
        if self._logger:
            self._logger.info(f'[STATS]  {label}  kbps={kbps:.1f}  fps={fps:.1f}')
        self._bytes = self._frames = 0
        self._t0    = now

    def summary(self) -> str:
        elapsed  = time.time() - self._t_start
        avg_kbps = (self._total_bytes * 8) / 1000 / max(elapsed, 1)
        return (f'total_frames={self._total_frames}  '
                f'total_MB={self._total_bytes/1e6:.2f}  '
                f'avg_kbps={avg_kbps:.1f}')


# ── Command application ───────────────────────────────────────────────────────
def apply_command(msg: dict, state: StreamState,
                  transport) -> tuple[bool, bool]:
    """
    Apply one command to stream state.
    Returns (encoder_needs_reinit, continue_running).
    Sends final ACK with detail.
    """
    cmd    = msg.get('cmd', '')
    value  = msg.get('value')
    reinit = False

    if cmd == CMD_SET_RES:
        if value in RESOLUTIONS:
            state.res = value
            reinit = True
            detail = f"→ {value} ({RESOLUTIONS[value][0]}×{RESOLUTIONS[value][1]})"
        else:
            detail = f"unknown res '{value}'"

    elif cmd == CMD_SET_CRF:
        try:
            state.crf = max(0, min(51, int(value)))
            reinit    = True
            detail    = f"→ CRF {state.crf}"
        except (TypeError, ValueError):
            detail = "invalid value"

    elif cmd == CMD_SET_FPS:
        try:
            state.fps = max(1, min(60, int(value)))
            reinit    = True
            detail    = f"→ {state.fps} fps"
        except (TypeError, ValueError):
            detail = "invalid value"

    elif cmd == CMD_SET_COLOR:
        state.color = bool(value)
        detail = 'color' if state.color else 'gray'

    elif cmd == CMD_KEYFRAME:
        state.force_keyframe = True
        detail = "scheduled"

    elif cmd == CMD_PAUSE:
        state.paused = True
        detail = "paused"

    elif cmd == CMD_RESUME:
        state.paused = False
        detail = "resumed"

    elif cmd == CMD_STOP:
        state.stop = True
        detail = "stopping"

    else:
        detail = "unknown command"

    print(f"[TX]  CMD applied: {cmd}  {detail}")
    ack = proto.encode(proto.ACK, cmd=cmd, status='ok', detail=detail)
    transport.send_side(ack, CH_ACK)
    return reinit, not state.stop


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='H.264 Video Transmitter')
    parser.add_argument('--res',       default='480p', choices=RESOLUTIONS.keys())
    parser.add_argument('--fps',       type=int, default=30)
    parser.add_argument('--crf',       type=int, default=28)
    parser.add_argument('--transport', default='udp', choices=['tcp', 'udp', 'uart'])
    # TCP / UDP
    parser.add_argument('--host',      default='127.0.0.1')
    parser.add_argument('--port',      type=int, default=5000)
    # UART
    parser.add_argument('--tx-port',   default='COM1')
    parser.add_argument('--baud',      type=int, default=3_000_000)
    parser.add_argument('--no-rtscts', action='store_true')
    # Source / color mode
    parser.add_argument('--source',    default='0')
    parser.add_argument('--color',     action='store_true',
                        help='Stream in color (RGB). Default: grayscale.')
    args = parser.parse_args()

    source = int(args.source) if args.source.isdigit() else args.source

    if args.transport == 'uart':
        print(f"[TX] Transport : UART → {args.tx_port} @ {args.baud:,} baud")
    else:
        print(f"[TX] Transport : {args.transport.upper()} → {args.host}:{args.port}")

    # ── Setup ─────────────────────────────────────────────────────────────────
    transport = get_transport(
        args.transport, mode='send',
        host=args.host, port=args.port,
        tx_port=args.tx_port, baud=args.baud, rtscts=not args.no_rtscts,
    )

    state      = StreamState(res=args.res, fps=args.fps, crf=args.crf,
                              color=args.color)
    controller = StreamController(state)

    logger = RunLogger('tx')

    # Wire up sideband app layer
    CommandReceiver(transport, controller)
    TelemetryReceiver(transport)

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"[TX] ERROR: cannot open source '{args.source}'")
        transport.close()
        logger.close()
        return

    width, height = RESOLUTIONS[state.res]
    encoder       = create_encoder(width, height, state.fps, state.crf)
    stats                 = TxStats(logger=logger)
    pts                   = 0
    udp_keyframe_interval = 3.0
    t_last_keyframe       = time.time()

    color_mode = 'color' if state.color else 'gray'
    print(f"[TX] {state.res} ({width}×{height})  fps={state.fps}  crf={state.crf}  mode={color_mode}")
    logger.info(f'[CONFIG]  transport={args.transport}  res={state.res}  '
                f'fps={state.fps}  crf={state.crf}  mode={color_mode}  '
                f'source={args.source}')
    print("[TX] Streaming … (Ctrl-C to stop)\n")

    try:
        while True:
            t_start = time.time()

            # ── Poll pending commands ─────────────────────────────────────────
            while (msg := controller.poll()) is not None:
                reinit, running = apply_command(msg, state, transport)
                logger.info(f'[CMD]   {msg.get("cmd")}={msg.get("value")}  '
                            f'mode={"color" if state.color else "gray"}  '
                            f'res={state.res}  crf={state.crf}  fps={state.fps}')
                if not running:
                    return
                if reinit:
                    for pkt in encoder.encode(None):   # flush
                        transport.send(bytes(pkt))
                    width, height = RESOLUTIONS[state.res]
                    encoder       = create_encoder(width, height, state.fps, state.crf)
                    print(f"[TX] Encoder reinit: {state.res}  fps={state.fps}  crf={state.crf}")

            # ── Paused? ───────────────────────────────────────────────────────
            if state.paused:
                time.sleep(0.05)
                continue

            # ── Capture ───────────────────────────────────────────────────────
            ret, bgr = cap.read()
            if not ret:
                break

            bgr = cv2.resize(bgr, (width, height))

            # ── Periodic keyframe for UDP late-join / resync ──────────────────
            if args.transport == 'udp' and (time.time() - t_last_keyframe) >= udp_keyframe_interval:
                state.force_keyframe = True
                t_last_keyframe = time.time()

            # ── Encode (color or gray based on live state) ────────────────────
            if state.color:
                av_frame = bgr_to_av_frame(bgr, pts, state.force_keyframe)
            else:
                gray     = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                av_frame = gray_to_av_frame(gray, pts, state.force_keyframe)
            state.force_keyframe = False
            pts += 1

            for pkt in encoder.encode(av_frame):
                sent = transport.send(bytes(pkt))
                stats.update(sent)

            stats.tick(state.res)

            # ── FPS throttle ──────────────────────────────────────────────────
            sleep = (1.0 / state.fps) - (time.time() - t_start)
            if sleep > 0:
                time.sleep(sleep)

    except KeyboardInterrupt:
        print("\n[TX] Interrupted.")
        logger.info('[EVENT]  KeyboardInterrupt')
    finally:
        print("[TX] Flushing …")
        for pkt in encoder.encode(None):
            transport.send(bytes(pkt))
        cap.release()
        transport.close()
        logger.close(stats.summary())
        print("[TX] Done.")


if __name__ == '__main__':
    main()
