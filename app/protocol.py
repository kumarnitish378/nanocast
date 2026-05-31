"""
app/protocol.py — Sideband message protocol (JSON over side-channel).

All messages:  {"type": <str>, "ts": <float>, ...fields}

Side-channel mapping (shared across all transports):
  channel 1 → TELEM   (receiver → transmitter)
  channel 2 → CMD     (receiver → transmitter)
  channel 3 → ACK     (transmitter → receiver)
"""
import json
import time

# ── Message types ─────────────────────────────────────────────────────────────
TELEM = 'telem'
CMD   = 'cmd'
ACK   = 'ack'

# ── Side-channel IDs (transport-agnostic) ─────────────────────────────────────
CH_TELEM = 1
CH_CMD   = 2
CH_ACK   = 3

# ── Command names ─────────────────────────────────────────────────────────────
CMD_SET_RES   = 'set_res'    # value: '240p' | '360p' | '480p' | '720p'
CMD_SET_CRF   = 'set_crf'    # value: int  0–51
CMD_SET_FPS   = 'set_fps'    # value: int  5–60
CMD_KEYFRAME  = 'keyframe'   # force next I-frame
CMD_PAUSE     = 'pause'
CMD_RESUME    = 'resume'
CMD_STOP      = 'stop'


# ── Codec helpers ─────────────────────────────────────────────────────────────
def encode(msg_type: str, **fields) -> bytes:
    return json.dumps({'type': msg_type, 'ts': time.time(), **fields}).encode()


def decode(data: bytes) -> dict:
    return json.loads(data.decode())
