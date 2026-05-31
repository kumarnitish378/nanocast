from abc import ABC, abstractmethod
from typing import Callable


class TransportSender(ABC):
    @abstractmethod
    def send(self, data: bytes) -> int:
        """Send one video packet. Returns bytes written."""

    def send_side(self, data: bytes, channel: int = 1) -> int:
        """Send on side-channel. Returns 0 if not supported by this transport."""
        return 0

    def on_side_channel(self, channel: int, callback: Callable[[bytes], None]) -> None:
        """Register callback for incoming side-channel data (no-op if unsupported)."""
        pass

    @abstractmethod
    def close(self): ...


class TransportReceiver(ABC):
    @abstractmethod
    def recv(self) -> bytes:
        """Block until one video packet arrives."""

    def send_side(self, data: bytes, channel: int = 1) -> int:
        """Send on side-channel. Returns 0 if not supported by this transport."""
        return 0

    def on_side_channel(self, channel: int, callback: Callable[[bytes], None]) -> None:
        """Register callback for incoming side-channel data (no-op if unsupported)."""
        pass

    @abstractmethod
    def close(self): ...
