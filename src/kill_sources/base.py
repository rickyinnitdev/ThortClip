from abc import ABC, abstractmethod
from typing import Callable, Dict, Any, Optional

class KillSourceBase(ABC):
    """Abstract base class for all kill detection sources (Hotkey, Screen OCR, Riot API, etc.)."""

    @abstractmethod
    def start(self, on_trigger: Callable[[float, Optional[int], Dict[str, Any]], None]):
        """Starts monitoring. on_trigger callback signature: (wall_timestamp, kill_count, metadata)."""
        pass

    @abstractmethod
    def stop(self):
        """Stops the source."""
        pass
