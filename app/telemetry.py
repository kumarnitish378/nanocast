"""
app/telemetry.py — Stream statistics over side-channel.

TelemetrySender  — runs on Receiver. Collects RxStats, pushes JSON every N sec.
TelemetryReceiver — runs on Transmitter. Prints incoming telemetry; stores latest.
"""
import threading
from . import protocol as proto


class TelemetrySender:
    """
    Attach to the receiver. Periodically serialises RxStats and sends via
    transport.send_side(channel=CH_TELEM).

    Parameters
    ----------
    transport   : the receiver's transport object (must support send_side)
    stats       : an RxStats instance (needs .fps, .kbps, .total_pkts attributes)
    interval    : seconds between pushes (default 1.0)
    """

    def __init__(self, transport, stats, interval: float = 1.0):
        self._transport = transport
        self._stats     = stats
        self._interval  = interval
        self._stop      = threading.Event()
        self._thread    = threading.Thread(
            target=self._loop, daemon=True, name='telem-sender'
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                msg = proto.encode(
                    proto.TELEM,
                    fps           = round(self._stats.fps, 1),
                    kbps          = round(self._stats.kbps, 1),
                    total_frames  = self._stats.total_pkts,
                    frames_dropped= getattr(self._stats, 'frames_dropped', 0),
                )
                sent = self._transport.send_side(msg, proto.CH_TELEM)
                if sent == 0 and not self._stop.is_set():
                    pass   # side-channel not available — silently back off
            except Exception:
                pass


class TelemetryReceiver:
    """
    Attach to the transmitter. Registers a callback on CH_TELEM.
    Prints each telemetry update and exposes .latest dict.

    Parameters
    ----------
    transport : the transmitter's transport object (must support on_side_channel)
    """

    def __init__(self, transport):
        self.latest: dict = {}
        transport.on_side_channel(proto.CH_TELEM, self._on_telem)

    def _on_telem(self, data: bytes) -> None:
        try:
            msg = proto.decode(data)
            if msg.get('type') != proto.TELEM:
                return
            self.latest = msg
            print(
                f"[TX ← TELEM]  fps={msg.get('fps','?'):>5}  "
                f"kbps={msg.get('kbps','?'):>8}  "
                f"frames={msg.get('total_frames','?')}  "
                f"dropped={msg.get('frames_dropped', 0)}"
            )
        except Exception:
            pass
