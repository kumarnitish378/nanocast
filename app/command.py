"""
app/command.py — Live stream control over side-channel.

StreamState      — mutable config that the TX main loop reads.
StreamController — thread-safe queue; TX polls this each iteration.
CommandReceiver  — runs on Transmitter; parses CMD messages → StreamController.
CommandSender    — runs on Receiver; sends CMD messages; keyboard helper.
"""
import queue
import threading
from dataclasses import dataclass, field
from typing import Optional

from . import protocol as proto


# ── Stream state ──────────────────────────────────────────────────────────────
@dataclass
class StreamState:
    res           : str  = '480p'
    fps           : int  = 30
    crf           : int  = 28
    paused        : bool = False
    stop          : bool = False
    force_keyframe: bool = False


# ── Controller ────────────────────────────────────────────────────────────────
class StreamController:
    """
    Holds the live stream configuration.
    CommandReceiver pushes commands in; TX main loop polls them out.
    """

    def __init__(self, initial: StreamState):
        self.state  = initial
        self._queue: queue.Queue = queue.Queue()

    def push(self, cmd: dict) -> None:
        self._queue.put(cmd)

    def poll(self) -> Optional[dict]:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


# ── Command receiver (runs on Transmitter) ────────────────────────────────────
class CommandReceiver:
    """
    Registers on CH_CMD.  Parses incoming commands → StreamController.
    Sends ACK back via transport.send_side(CH_ACK).

    Parameters
    ----------
    transport   : transmitter's transport (send_side + on_side_channel)
    controller  : StreamController instance shared with the TX main loop
    """

    def __init__(self, transport, controller: StreamController):
        self._transport  = transport
        self._controller = controller
        transport.on_side_channel(proto.CH_CMD, self._on_cmd)

    def _on_cmd(self, data: bytes) -> None:
        try:
            msg = proto.decode(data)
            if msg.get('type') != proto.CMD:
                return
            cmd = msg.get('cmd', '')
            val = msg.get('value')
            print(f"[TX ← CMD]  {cmd}" + (f"={val}" if val is not None else ""))
            self._controller.push(msg)
            # Send ACK back to receiver
            ack = proto.encode(proto.ACK, cmd=cmd, status='queued')
            self._transport.send_side(ack, proto.CH_ACK)
        except Exception as e:
            print(f"[TX] CommandReceiver error: {e}")


# ── Command sender (runs on Receiver) ─────────────────────────────────────────
class CommandSender:
    """
    Sends control commands to the transmitter via transport.send_side(CH_CMD).
    Listens for ACKs on CH_ACK and prints confirmation.

    Keyboard map (use in OpenCV waitKey loop):
    ─────────────────────────────────────────────────────────────
    Key   Action
    ───   ──────────────────────────────────────────────────────
    1     set_res → 240p
    2     set_res → 360p
    3     set_res → 480p
    4     set_res → 720p
    +/=   set_crf  -2  (raise quality)
    -     set_crf  +2  (lower quality / lower bitrate)
    k     force keyframe
    p     toggle pause / resume
    q     stop stream

    Parameters
    ----------
    transport : receiver's transport (send_side + on_side_channel)
    """

    _RES_KEYS = {
        ord('1'): '240p',
        ord('2'): '360p',
        ord('3'): '480p',
        ord('4'): '720p',
    }

    def __init__(self, transport):
        self._transport = transport
        self._paused    = False
        self._crf       = 28     # local mirror so +/- are relative

        transport.on_side_channel(proto.CH_ACK, self._on_ack)

    # ── Public API ────────────────────────────────────────────────────────────

    def send(self, cmd: str, value=None) -> None:
        msg  = proto.encode(proto.CMD, cmd=cmd, value=value)
        sent = self._transport.send_side(msg, proto.CH_CMD)
        if sent == 0:
            print("[RX] ⚠  Side-channel unavailable for this transport.")
        else:
            print(f"[RX → CMD]  {cmd}" + (f"={value}" if value is not None else ""))

    def handle_key(self, key: int) -> bool:
        """
        Call with cv2.waitKey() result.
        Returns True if key was consumed (caller should not also handle it).
        Returns False for unknown keys.
        """
        if key in self._RES_KEYS:
            self.send(proto.CMD_SET_RES, self._RES_KEYS[key])
            return True

        if key == ord('+') or key == ord('='):
            self._crf = max(0, self._crf - 2)
            self.send(proto.CMD_SET_CRF, self._crf)
            return True

        if key == ord('-'):
            self._crf = min(51, self._crf + 2)
            self.send(proto.CMD_SET_CRF, self._crf)
            return True

        if key == ord('k'):
            self.send(proto.CMD_KEYFRAME)
            return True

        if key == ord('p'):
            self._paused = not self._paused
            self.send(proto.CMD_PAUSE if self._paused else proto.CMD_RESUME)
            return True

        if key == ord('q'):
            self.send(proto.CMD_STOP)
            return False   # let caller also quit

        return False

    @staticmethod
    def hint_text() -> str:
        return "[1-4]:res  [+/-]:quality  [k]:keyframe  [p]:pause  [q]:quit"

    # ── ACK handler ───────────────────────────────────────────────────────────

    def _on_ack(self, data: bytes) -> None:
        try:
            msg = proto.decode(data)
            print(
                f"[RX ← ACK]  {msg.get('cmd')} → {msg.get('status')}"
                + (f"  ({msg.get('detail')})" if msg.get('detail') else "")
            )
        except Exception:
            pass
