"""Emergency event with a generation counter so rearming cannot erase a newer panic."""

from threading import Event, Lock


class PanicEvent(Event):
    def __init__(self) -> None:
        super().__init__()
        self._generation_lock = Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._generation_lock:
            return self._generation

    def set(self) -> None:
        with self._generation_lock:
            self._generation += 1
            super().set()

    def clear_if_generation(self, expected: int) -> bool:
        with self._generation_lock:
            if expected != self._generation:
                return False
            super().clear()
            return True
