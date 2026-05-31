# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Real-time H.264 video streaming system designed for low-bandwidth/low-data-rate channels (UART at 3 Mbaud, TCP). Supports live remote control of encoder parameters (resolution, CRF, FPS) and telemetry feedback from receiver to transmitter via multiplexed sideband channels.

## Setup

```bash
pip install opencv-python av pyserial numpy
```

On Linux, FFmpeg dev libraries may be needed: `sudo apt install libavformat-dev libavdevice-dev libavcodec-dev`

## Running

**TCP (loopback test) — start transmitter first, it waits for the receiver:**
```bash
# Terminal 1 — transmitter (server, binds port 5000 and 5001)
python transmitter.py --transport tcp --host 127.0.0.1 --port 5000

# Terminal 2 — receiver (client, connects when ready)
python receiver.py --transport tcp --host 127.0.0.1 --port 5000
```

**UART (two physical nodes):**
```bash
python transmitter.py --transport uart --tx-port COM1 --baud 3000000
python receiver.py   --transport uart --rx-port COM2 --baud 3000000
```

Key CLI flags: `--res {140p,240p,360p,480p,720p}`, `--fps`, `--crf`, `--source` (file or device index), `--no-rtscts`, `--no-overlay`.

Keyboard controls in receiver window: `1–4` = resolution presets, `+/-` = quality (CRF ±2), `k` = force keyframe, `p` = pause, `q` = quit.

## Architecture

```
transmitter.py / receiver.py     ← entry points
app/
  protocol.py    ← JSON encode/decode for sideband messages; channel IDs; command names
  command.py     ← StreamController (TX), CommandReceiver (TX), CommandSender (RX)
  telemetry.py   ← TelemetrySender (RX→TX background thread), TelemetryReceiver (TX)
transport/
  __init__.py    ← get_transport(type, mode, **kwargs) factory
  base.py        ← Abstract TransportSender / TransportReceiver (send_side/on_side_channel default no-ops)
  tcp.py         ← TCP video on port, reverse sideband on port+1
  uart.py        ← Full-duplex UART with CRC16-CCITT channel multiplexing
  udp.py         ← Stub (raises NotImplementedError); sideband intentionally not supported
```

### Sideband Channel Design

All transports carry three multiplexed out-of-band channels alongside the video stream:

| Channel   | Direction | Purpose                          |
|-----------|-----------|----------------------------------|
| CH_TELEM  | RX → TX   | JSON stats (fps, kbps, drops)    |
| CH_CMD    | RX → TX   | Encoder parameter change requests|
| CH_ACK    | TX → RX   | ACK/NACK for applied commands    |

TCP uses a second TCP connection on `port+1` for sideband. UART multiplexes all channels on the same serial port using a sync-word + CRC16-CCITT wire frame.

### UART Wire Frame

```
[0xAA][0x55] | [CH:1B][LEN:4B BE] | [PAYLOAD] | [CRC16-CCITT:2B BE]
```

CRC covers CH + LEN + PAYLOAD. Channels: `0x00` video, `0x01` telemetry, `0x02` command, `0x03` ACK.

### Encoder Tuning

libx264 is configured with `preset=ultrafast`, `tune=zerolatency`, `repeat_headers=1`. Resolution changes trigger a full encoder/decoder reinit. CRF and FPS changes are applied to the running encoder without reinit where possible.

## Known Issues

- UDP transport is referenced in CLI help strings but `transport/udp.py` raises `NotImplementedError`.
