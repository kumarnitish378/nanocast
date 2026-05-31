# Low Data Rate H.264 Video Transport Layer

Real-time H.264 video streaming system designed for low-bandwidth channels (UART at 3 Mbaud, TCP). Supports live remote control of encoder parameters (resolution, CRF, FPS) and telemetry feedback from receiver to transmitter via multiplexed sideband channels.

## Features

- H.264 encoding via libx264 (`ultrafast` / `zerolatency`) using PyAV
- Resolution presets: 140p, 240p, 360p, 480p, 720p
- Grayscale (default) or color streaming
- Live encoder parameter control from the receiver window
- Telemetry feedback (bitrate, FPS, drop count) over a reverse sideband channel
- ACK/NACK for each applied command
- Multiplexed sideband channels — TCP uses a second connection, UART uses in-band framing with CRC16-CCITT
- Per-run log files under `logs/`

## Requirements

```bash
pip install opencv-python av pyserial numpy
```

On Linux, FFmpeg dev libraries may be needed:

```bash
sudo apt install libavformat-dev libavdevice-dev libavcodec-dev
```

## Running

### TCP — loopback test

Start the transmitter first; it binds and waits for the receiver.

```bash
# Terminal 1 — transmitter (server, binds port 5000 and 5001)
python transmitter.py --transport tcp --host 127.0.0.1 --port 5000

# Terminal 2 — receiver (client, connects when ready)
python receiver.py --transport tcp --host 127.0.0.1 --port 5000
```

### UART — two physical nodes

```bash
# Node A (transmitter)
python transmitter.py --transport uart --tx-port COM1 --baud 3000000

# Node B (receiver)
python receiver.py --transport uart --rx-port COM2 --baud 3000000
```

## CLI Flags

### transmitter.py

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `tcp` | Transport type: `tcp`, `uart` |
| `--host` | `127.0.0.1` | Bind address (TCP) |
| `--port` | `5000` | Video port (TCP); sideband uses `port+1` |
| `--tx-port` | `COM1` | Serial port (UART) |
| `--baud` | `3000000` | Baud rate (UART) |
| `--no-rtscts` | off | Disable RTS/CTS hardware flow control |
| `--res` | `480p` | Initial resolution: `140p` `240p` `360p` `480p` `720p` |
| `--fps` | `30` | Initial frame rate |
| `--crf` | `28` | Initial CRF quality (0 = lossless, 51 = worst) |
| `--source` | `0` | Video source: device index or file path |
| `--color` | off | Stream in color (default: grayscale) |

### receiver.py

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `tcp` | Transport type: `tcp`, `uart` |
| `--host` | `127.0.0.1` | Transmitter address (TCP) |
| `--port` | `5000` | Video port (TCP) |
| `--rx-port` | `COM2` | Serial port (UART) |
| `--baud` | `3000000` | Baud rate (UART) |
| `--no-rtscts` | off | Disable RTS/CTS hardware flow control |
| `--res` | — | Force display resolution (optional override) |
| `--no-overlay` | off | Disable stats/hint overlay |

## Keyboard Controls (receiver window)

| Key | Action |
|-----|--------|
| `1` | 240p |
| `2` | 360p |
| `3` | 480p |
| `4` | 720p |
| `+` / `=` | Raise quality (CRF −2) |
| `-` | Lower quality (CRF +2) |
| `k` | Force keyframe |
| `p` | Pause / Resume |
| `q` | Quit |

## Architecture

```
transmitter.py / receiver.py     ← entry points
app/
  protocol.py    ← JSON encode/decode for sideband messages; channel IDs; command names
  command.py     ← StreamController (TX), CommandReceiver (TX), CommandSender (RX)
  telemetry.py   ← TelemetrySender (RX→TX background thread), TelemetryReceiver (TX)
  logger.py      ← Per-run file logger (logs/<role>_YYYYMMDD_HHMMSS.log)
transport/
  __init__.py    ← get_transport(type, mode, **kwargs) factory
  base.py        ← Abstract TransportSender / TransportReceiver
  tcp.py         ← TCP video on port, reverse sideband on port+1
  uart.py        ← Full-duplex UART with CRC16-CCITT channel multiplexing
  udp.py         ← Implemented but not wired into CLI (no handshake, connectionless)
```

## Sideband Channel Design

All transports carry three multiplexed out-of-band channels alongside the video stream:

| Channel | ID | Direction | Purpose |
|---------|----|-----------|---------|
| `CH_TELEM` | 1 | RX → TX | JSON stats (fps, kbps, drops) |
| `CH_CMD` | 2 | RX → TX | Encoder parameter change requests |
| `CH_ACK` | 3 | TX → RX | ACK/NACK for applied commands |

**TCP** uses a second TCP connection on `port+1` for all sideband traffic. No CRC — TCP guarantees delivery and ordering.

**UART** multiplexes all channels on the same serial link using a sync-word + CRC16-CCITT wire frame (see below).

## UART Wire Frame

```
[0xAA][0x55] | [CH:1B][LEN:4B BE] | [PAYLOAD] | [CRC16-CCITT:2B BE]
```

CRC covers `CH + LEN + PAYLOAD`. Channel byte values:

| Value | Channel |
|-------|---------|
| `0x00` | Video |
| `0x01` | Telemetry |
| `0x02` | Command |
| `0x03` | ACK |

## Encoder Configuration

libx264 is configured for minimum latency:

```python
preset  = 'ultrafast'
tune    = 'zerolatency'
repeat_headers = 1        # SPS/PPS before every keyframe
```

- Resolution changes trigger a full encoder/decoder reinit.
- CRF and FPS changes reinit the encoder without dropping the connection.
- Forced keyframes are inserted on the next encoded frame (no reinit).

## Logging

Each run writes a timestamped log to `logs/`:

```
logs/tx_20260531_143022.log
logs/rx_20260531_143025.log
```

Log lines record config, per-second stats (`[STATS]`), commands applied (`[CMD]`), events (`[EVENT]`), and a final summary with total frames, megabytes, and average bitrate.

## Known Issues

- UDP transport (`transport/udp.py`) is implemented but raises `NotImplementedError` in the factory — it is not exposed via the CLI. The code is present and functional for direct use.
