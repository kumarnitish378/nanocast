"""
transport/uart.py  —  FTDI Full-Duplex UART Transport  (channel-multiplexed)
═══════════════════════════════════════════════════════════════════════════════

Physical wiring (2× FTDI adapters):

  Node A  (Transmitter PC)        Node B  (Receiver PC)
  ┌─────────────────┐             ┌─────────────────┐
  │  FTDI-A  COM_A  │             │  FTDI-B  COM_B  │
  │  TX ────────────┼─────────────┼──► RX           │
  │  RX ◄───────────┼─────────────┼─── TX           │
  │  GND ───────────┼─────────────┼─── GND          │
  └─────────────────┘             └─────────────────┘

Each node opens ONE serial port.  Both TX and RX pins are active simultaneously.
A background thread continuously reads and dispatches incoming frames by channel.

─────────────────────────────────────────────────────────────────────────────
Channel map

  0x00  CHANNEL_VIDEO   A → B   H.264 packets
  0x01  CHANNEL_TELEM   B → A   Telemetry JSON
  0x02  CHANNEL_CMD     B → A   Commands
  0x03  CHANNEL_ACK     A → B   ACK / heartbeat
  0x10+ user-defined

─────────────────────────────────────────────────────────────────────────────
Wire frame (9 bytes overhead):
  [0xAA][0x55][CH:1][LEN:4 BE][PAYLOAD:N][CRC16:2 BE]
  CRC covers CH + LEN + PAYLOAD.
─────────────────────────────────────────────────────────────────────────────
Baud-rate guide:
  FT232R  → 3 000 000  (~375 KB/s)   default
  FT2232H → up to 12 000 000
─────────────────────────────────────────────────────────────────────────────
"""

import struct
import queue
import threading
from typing import Callable

import serial          # pip install pyserial
from .base import TransportSender, TransportReceiver

# ── Channel IDs ───────────────────────────────────────────────────────────────
CHANNEL_VIDEO  = 0x00
CHANNEL_TELEM  = 0x01
CHANNEL_CMD    = 0x02
CHANNEL_ACK    = 0x03

# ── Frame ─────────────────────────────────────────────────────────────────────
SYNC          = b'\xAA\x55'
_HDR_FMT      = '>BI'                    # channel(1) + length(4)
_HDR_SIZE     = struct.calcsize(_HDR_FMT)
_MAX_PAYLOAD  = 1_048_576
_QSIZE        = 64

DEFAULT_BAUD  = 3_000_000


# ── CRC-16/CCITT-FALSE ────────────────────────────────────────────────────────
def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
        crc &= 0xFFFF
    return crc


def _open_port(port: str, baud: int, rtscts: bool) -> serial.Serial:
    return serial.Serial(
        port=port, baudrate=baud,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, rtscts=rtscts,
        timeout=2.0, write_timeout=2.0,
    )


def _read_exact(ser: serial.Serial, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = ser.read(n - len(buf))
        if not chunk:
            raise ConnectionError(f"UART read timeout (wanted {n-len(buf)} more bytes)")
        buf += chunk
    return buf


# ══════════════════════════════════════════════════════════════════════════════
class UARTNode:
    """
    Full-duplex UART node. Opens ONE serial port; RX thread dispatches by channel.

    Usage
    -----
    node = UARTNode('COM1')
    node.send(h264_bytes,  CHANNEL_VIDEO)
    node.send(telem_bytes, CHANNEL_TELEM)
    data = node.recv(CHANNEL_VIDEO)              # blocking
    node.on_channel(CHANNEL_CMD, my_callback)   # async callback
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, rtscts: bool = True):
        self._ser         = _open_port(port, baud, rtscts)
        self._tx_lock     = threading.Lock()
        self._queues:   dict[int, queue.Queue]  = {}
        self._callbacks: dict[int, Callable]    = {}
        self._crc_errors  = 0
        self._stop        = threading.Event()

        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

        threading.Thread(target=self._rx_loop, daemon=True,
                         name=f'uart-rx-{port}').start()

        eff = (baud / 10 * 8) / 1000
        print(f"[UART] {port}  @  {baud:,} baud  (~{eff:.0f} kbps)  full-duplex ready")

    # ── Channel management ────────────────────────────────────────────────────

    def on_channel(self, ch: int, callback: Callable) -> None:
        """Async callback — called from RX thread."""
        self._callbacks[ch] = callback

    def register_queue(self, ch: int) -> queue.Queue:
        if ch not in self._queues:
            self._queues[ch] = queue.Queue(maxsize=_QSIZE)
        return self._queues[ch]

    # ── Send ──────────────────────────────────────────────────────────────────

    def send(self, data: bytes, channel: int = CHANNEL_VIDEO) -> int:
        hdr   = struct.pack(_HDR_FMT, channel, len(data))
        frame = SYNC + hdr + data + struct.pack('>H', _crc16(hdr + data))
        try:
            with self._tx_lock:
                self._ser.write(frame)
        except serial.SerialTimeoutException:
            print("[UART] ⚠  Write timeout — check wiring or use --no-rtscts")
            return 0
        return len(frame)

    # ── Blocking receive ──────────────────────────────────────────────────────

    def recv(self, channel: int = CHANNEL_VIDEO) -> bytes:
        self.register_queue(channel)
        while not self._stop.is_set():
            try:
                return self._queues[channel].get(timeout=1.0)
            except queue.Empty:
                continue
        raise ConnectionError("UART node stopped.")

    # ── RX loop ───────────────────────────────────────────────────────────────

    def _hunt_sync(self) -> bool:
        """Scan for 0xAA 0x55 sync marker. Returns False if stopped."""
        while not self._stop.is_set():
            b = self._ser.read(1)
            if not b:
                continue   # read timeout — keep waiting
            if b != b'\xAA':
                continue
            nxt = self._ser.read(1)
            if not nxt:
                continue
            if nxt == b'\x55':
                return True
        return False

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._hunt_sync():
                    break
                hdr_bytes        = _read_exact(self._ser, _HDR_SIZE)
                channel, length  = struct.unpack(_HDR_FMT, hdr_bytes)
                if length == 0 or length > _MAX_PAYLOAD:
                    self._crc_errors += 1
                    continue
                payload  = _read_exact(self._ser, length)
                crc_recv = struct.unpack('>H', _read_exact(self._ser, 2))[0]
                if crc_recv != _crc16(hdr_bytes + payload):
                    self._crc_errors += 1
                    if self._crc_errors % 10 == 0:
                        print(f"[UART] ⚠  CRC errors: {self._crc_errors}")
                    continue
                self._dispatch(channel, payload)
            except ConnectionError:
                continue   # mid-frame read timeout — resync on next iteration
            except serial.SerialException as e:
                if not self._stop.is_set():
                    print(f"[UART] Serial error: {e}")
                break

    def _dispatch(self, channel: int, payload: bytes) -> None:
        if channel in self._callbacks:
            try:
                self._callbacks[channel](payload)
            except Exception as e:
                print(f"[UART] callback error ch={channel}: {e}")
        elif channel in self._queues:
            q = self._queues[channel]
            if q.full():
                try: q.get_nowait()
                except queue.Empty: pass
            q.put_nowait(payload)

    def close(self) -> None:
        self._stop.set()
        if self._ser.is_open:
            self._ser.close()
        print(f"[UART] Closed.  CRC errors: {self._crc_errors}")


# ══════════════════════════════════════════════════════════════════════════════
#  TransportSender / TransportReceiver wrappers
# ══════════════════════════════════════════════════════════════════════════════

class UARTSender(TransportSender):
    """Video sender — wraps UARTNode. Access .node for direct channel control."""

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, rtscts: bool = True):
        self.node = UARTNode(port, baud, rtscts)

    def send(self, data: bytes) -> int:
        return self.node.send(data, CHANNEL_VIDEO)

    def send_side(self, data: bytes, channel: int = CHANNEL_TELEM) -> int:
        return self.node.send(data, channel)

    def on_side_channel(self, channel: int, callback: Callable) -> None:
        self.node.on_channel(channel, callback)

    def close(self) -> None:
        self.node.close()


class UARTReceiver(TransportReceiver):
    """Video receiver — wraps UARTNode. Access .node for direct channel control."""

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, rtscts: bool = True):
        self.node = UARTNode(port, baud, rtscts)
        self.node.register_queue(CHANNEL_VIDEO)

    def recv(self) -> bytes:
        return self.node.recv(CHANNEL_VIDEO)

    def send_side(self, data: bytes, channel: int = CHANNEL_CMD) -> int:
        return self.node.send(data, channel)

    def on_side_channel(self, channel: int, callback: Callable) -> None:
        self.node.on_channel(channel, callback)

    def close(self) -> None:
        self.node.close()
