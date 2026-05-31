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
| `--res` | `480p` | Initial resolution: `140p` `240p` `360p` `480p` `720p` |
| `--fps` | `30` | Initial frame rate (1–60) |
| `--crf` | `28` | Initial quality (0 = lossless → 51 = worst) |
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
| `--res` | — | Force display resolution (optional) |
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

## Architecture

```
nanocast/
├── transmitter.py        entry point — encode, send, apply commands
├── receiver.py           entry point — decode, display, send commands
├── uart_diag.py          UART cable diagnostic (list / loopback / link)
├── app/
│   ├── protocol.py       JSON sideband messages, channel IDs, command names
│   ├── command.py        StreamState, StreamController, CommandReceiver, CommandSender
│   ├── telemetry.py      TelemetrySender (RX→TX thread), TelemetryReceiver (TX)
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
