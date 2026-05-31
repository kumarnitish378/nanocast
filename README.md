# nanocast

**Real-time H.264 video streaming over low-bandwidth channels.**

Designed for constrained links — UART at 230 kbaud, UDP, or TCP — with live encoder
control from the receiver and telemetry feedback flowing back to the transmitter.
Supports grayscale and color modes, multiplexed sideband channels, and per-run logging.

---

## Features

- H.264 encoding via libx264 (`ultrafast` + `zerolatency`) using PyAV
- Resolution presets: 140p, 240p, 360p, 480p, 720p
- Grayscale (default, lower bitrate) or color streaming — switchable at runtime
- Three transports: **UDP** (default), **TCP**, **UART**
- Live encoder control from the receiver keyboard (resolution, CRF, FPS, pause, color)
- Telemetry feedback — bitrate, FPS, frame count, drops — sent receiver → transmitter
- ACK/NACK for every applied command
- Multiplexed sideband channels alongside the video stream
- Per-run timestamped log files under `logs/`
- UART diagnostic tool (`uart_diag.py`) for pre-flight cable testing

---

## Setup

```bash
pip install -r requirements.txt
```

On Linux, FFmpeg dev libraries may be needed:

```bash
sudo apt install libavformat-dev libavdevice-dev libavcodec-dev
```

---

## Quick Start

### UDP (default — start either side first)

```bash
# Terminal 1 — transmitter
python transmitter.py --res 480p --crf 28

# Terminal 2 — receiver
python receiver.py
```

### TCP (start transmitter first — it waits for the receiver)

```bash
python transmitter.py --transport tcp --res 480p
python receiver.py   --transport tcp
```

### UART (two physical nodes, cross-wired TX/RX)

```bash
python transmitter.py --transport uart --tx-port COM7 --baud 230400 --res 140p --crf 40 --no-rtscts
python receiver.py   --transport uart --rx-port COM8 --baud 230400 --no-rtscts
```

---

## CLI Reference

### transmitter.py

| Flag | Default | Description |
|---|---|---|
| `--transport` | `udp` | `udp` \| `tcp` \| `uart` |
| `--host` | `127.0.0.1` | Destination host (UDP/TCP) |
| `--port` | `5000` | Base port — sideband uses `port+1` / `port+2` |
| `--tx-port` | `COM1` | Serial port (UART) |
| `--baud` | `3000000` | Baud rate (UART) |
| `--no-rtscts` | off | Disable RTS/CTS hardware flow control |
| `--res` | `480p` | Initial resolution: `64p` `90p` `140p` `240p` `360p` `480p` `720p` |
| `--fps` | `30` | Initial frame rate (1–60) |
| `--crf` | `28` | Initial quality (0 = lossless → 51 = worst) |
| `--codec` | `h264` | `h264` \| `h265` (h265 ≈ 40–50% smaller) |
| `--bpp` | — | **Recommended.** Bits-per-pixel target (e.g. `0.12`); bitrate scales with resolution so quality-per-pixel is constant. Higher res → better quality *and* more data |
| `--max-bitrate` | — | Hard ceiling (kbps) for `--bpp` mode — protects the radio budget |
| `--bitrate` | — | Fixed bitrate cap in kbps (same at every resolution). ⚠ Higher res looks *worse* at a fixed bitrate; prefer `--bpp` |
| `--intra-refresh` | off | Moving refresh for fast packet-loss recovery (keeps periodic keyframes for stream entry) |
| `--skip-threshold` | `0` | Motion gate: skip near-identical frames (0 = send every frame). Parked rover ≈ 0 kbps |
| `--heartbeat` | `2.0` | Max seconds between sent frames when motion-gated |
| `--source` | `0` | Webcam index or video file path |
| `--color` | off | Start in color mode (default: grayscale) |

### receiver.py

| Flag | Default | Description |
|---|---|---|
| `--transport` | `udp` | `udp` \| `tcp` \| `uart` |
| `--host` | `127.0.0.1` | Transmitter address (UDP/TCP) |
| `--port` | `5000` | Base port |
| `--rx-port` | `COM2` | Serial port (UART) |
| `--baud` | `3000000` | Baud rate (UART) |
| `--no-rtscts` | off | Disable RTS/CTS hardware flow control |
| `--codec` | `h264` | Must match the transmitter |
| `--upscale` | `none` | Scaling method to reach `--display-res`: `none` \| `lanczos` \| `espcn` |
| `--display-res` | `480p` | GCS display resolution — any received frame is scaled to this |
| `--no-overlay` | off | Disable on-screen stats and hint bar |

---

## Keyboard Controls

All shortcuts act on the live transmitter — commands are sent over the sideband channel
and take effect on the next encoded frame. No restart required.

| Key | Action |
|---|---|
| `1` | Resolution → 240p |
| `2` | Resolution → 360p |
| `3` | Resolution → 480p |
| `4` | Resolution → 720p |
| `+` / `=` | Raise quality (CRF − 2) |
| `-` | Lower quality (CRF + 2, lower bitrate) |
| `c` | Toggle color / grayscale |
| `k` | Force keyframe |
| `p` | Pause / Resume |
| `q` | Quit |

---

## Baud Rate Guide (UART)

| Resolution | Typical bitrate | Recommended baud |
|---|---|---|
| 140p | ~50 kbps | 230,400 |
| 240p | ~150 kbps | 460,800 |
| 360p | ~300 kbps | 921,600 |
| 480p | ~600 kbps | 1,500,000 |
| 720p | ~1,500 kbps | 3,000,000 |

Use `--no-rtscts` if your USB-UART adapter only has TX/RX/GND wired (no RTS/CTS).

---

## Minimum-Bitrate Profile (rover → GCS radio link)

Send a tiny, bitrate-capped, loss-resilient stream and reconstruct quality at the
GCS with neural super-resolution:

```bash
# Rover — bitrate scales with resolution (constant quality), ceiling protects link
python transmitter.py --res 90p --codec h264 --bpp 0.12 --max-bitrate 150 --fps 15 \
                      --intra-refresh --skip-threshold 2.0

# GCS — decode whatever arrives, reconstruct to a 480p display with ESPCN
python receiver.py --codec h264 --upscale espcn --display-res 480p
```

A parked/slow rover idles near 0 kbps (motion gate); while driving, bitrate scales
with the chosen resolution and is capped at `--max-bitrate`. ESPCN reconstructs
~5× sharper detail than plain interpolation.

The transmitter resolution and the GCS display resolution are independent: the
rover can drop to 64p to save bandwidth and the GCS still renders at your chosen
size. Change rover resolution live with keys `1`–`4`; the GCS target stays fixed.

### Bitrate, resolution & quality — the key trade-off

At a **fixed** bitrate, raising resolution makes quality *worse*, because the same
bits are spread over more pixels:

```
bits/pixel = bitrate ÷ (width × height × fps)
  90p  @ 25 kbps = 0.116 bits/pixel   (clean)
  480p @ 25 kbps = 0.004 bits/pixel   (blocky mush)
```

Use `--bpp` so bitrate tracks resolution and quality-per-pixel stays constant
(`--bpp 0.12` @ 15 fps): 64p≈11 kbps, 90p≈26, 140p≈65, 240p≈184, 360p≈415 kbps.
Within a 50–200 kbps radio budget, ~140p is the practical ceiling — so the winning
strategy is **low resolution + AI upscaling**, not high resolution.

---

## AI Super-Resolution (GCS-side)

The rover sends small frames to save bandwidth; the GCS reconstructs a larger,
sharper image. This stage is purely receiver-side — it never touches the radio
path or the transmitter.

```bash
# One-time: fetch the pretrained ESPCN models into models/
python tools/get_sr_models.py

# Optional (only for --upscale espcn): CPU build is enough
pip install torch

python receiver.py --upscale espcn   --display-res 480p  # neural SR
python receiver.py --upscale lanczos --display-res 480p  # classical (no torch)
```

Any received resolution is scaled to `--display-res`. For ESPCN the model scale
(×2/×3/×4) is chosen automatically to overshoot the target, then resized to the
exact display size; if a frame already exceeds the target it is downscaled.

| Mode | Needs | Quality | Speed (CPU, 90p→480p) |
|---|---|---|---|
| `none` | — | native (no scaling) | — |
| `lanczos` | — | soft upscale | instant |
| `espcn` | torch + model | ~5× sharper detail | hundreds of fps |

ESPCN runs in PyTorch; its weights are extracted from the OpenCV `ESPCN_x*.pb`
files (this sidesteps an OpenCV 4.13 TensorFlow-import bug). If torch or the
model file is missing, the receiver automatically falls back to Lanczos.

> ⚠ Super-resolution reconstructs *plausible* detail and can invent texture that
> is not really there. Treat the upscaled view as enhanced situational awareness,
> not ground truth for navigation decisions.

---

## Architecture

```
nanocast/
├── transmitter.py        entry point — encode, send, apply commands
├── receiver.py           entry point — decode, upscale, display, send commands
├── uart_diag.py          UART cable diagnostic (list / loopback / link)
├── tools/
│   └── get_sr_models.py  download pretrained ESPCN super-resolution models
├── app/
│   ├── protocol.py       JSON sideband messages, channel IDs, command names
│   ├── command.py        StreamState, StreamController, CommandReceiver, CommandSender
│   ├── telemetry.py      TelemetrySender (RX→TX thread), TelemetryReceiver (TX)
│   ├── upscaler.py       GCS-side super-resolution (none / lanczos / espcn)
│   └── logger.py         Per-run timestamped file logger
└── transport/
    ├── __init__.py       get_transport(type, mode, **kwargs) factory
    ├── base.py           Abstract TransportSender / TransportReceiver
    ├── tcp.py            TX=server (waits), RX=client; sideband on port+1
    ├── uart.py           Full-duplex, CRC16-CCITT framing, channel multiplexing
    └── udp.py            Connectionless; sideband on port+1 (RX→TX) / port+2 (TX→RX)
```

---

## Sideband Channel Design

All transports carry three multiplexed control channels alongside video:

| Channel | ID | Direction | Content |
|---|---|---|---|
| `CH_TELEM` | 1 | RX → TX | JSON: fps, kbps, total frames, drops |
| `CH_CMD` | 2 | RX → TX | Encoder parameter requests |
| `CH_ACK` | 3 | TX → RX | ACK/NACK with applied detail |

**UDP** — sideband uses two extra ports: `port+1` (RX→TX) and `port+2` (TX→RX).  
**TCP** — sideband uses a second TCP connection on `port+1` (bidirectional).  
**UART** — sideband multiplexed on the same serial link via channel byte in the wire frame.

### UART Wire Frame

```
[0xAA][0x55]  [CH:1B][LEN:4B BE]  [PAYLOAD:N]  [CRC16-CCITT:2B BE]
```

CRC covers `CH + LEN + PAYLOAD`. Channel values: `0x00` video, `0x01` telemetry,
`0x02` command, `0x03` ACK.

---

## Grayscale vs Color

H.264 encodes in YUV 4:2:0, not RGB. Grayscale sets the chroma channels (U, V) to
neutral (128), which compresses to near-zero bits. The actual bitrate saving over color
is **~15–20%**, not 3×, because luma (Y) carries the dominant share of bitrate regardless
of color mode. For larger reductions use lower resolution or higher CRF.

| Mode | Bitrate (480p, CRF 28) |
|---|---|
| Color | ~1050 kbps |
| Grayscale | ~865 kbps |

---

## Logging

Each run writes a timestamped log to `logs/`:

```
logs/tx_20260531_143022.log
logs/rx_20260531_143025.log
```

Each line is prefixed with `HH:MM:SS` and tagged `[CONFIG]`, `[STATS]`, `[CMD]`,
`[EVENT]`, or `[END]`. Useful for comparing gray vs color bitrate across runs.

---

## UART Diagnostics

```bash
# List available serial ports
python uart_diag.py list

# Single-port loopback test (short TX to RX with a jumper wire)
python uart_diag.py loopback COM7 --baud 230400

# Two-port cross-cable test (verify both directions before streaming)
python uart_diag.py link COM7 COM8 --baud 230400
```

---

## License

Copyright (c) 2026 Nitish NS. All rights reserved.  
This software is proprietary and confidential. See [LICENSE](LICENSE) for details.
