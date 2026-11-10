"""Shared assistant activity state without desktop or GUI dependencies."""

from dataclasses import dataclass
from enum import Enum
import os
import tempfile
import threading
from typing import Optional


class ToleshState(Enum):
    """Assistant activity shared with local observers."""
    ASLEEP = "asleep"          # Daemon not started yet
    IDLE = "idle"              # Awake and ready, waiting for wake word
    LISTENING = "listening"    # Actively listening (collecting or hot window)
    THINKING = "thinking"      # Processing query
    SPEAKING = "speaking"      # Speaking response
    DICTATING = "dictating"    # Hold-to-dictate recording active
    DICTATION_PROCESSING = "dictation_processing"  # Transcribing & pasting captured dictation


def _get_tolesh_state_file() -> str:
    """Get the path to the Tolesh state file."""
    return os.path.join(tempfile.gettempdir(), "tolesh_state")


@dataclass(frozen=True, eq=False)
class _StateOwnership:
    previous: ToleshState
    publication: tuple[int, int, int, int]


def _file_identity(info) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


class ToleshStateManager:
    """File-backed activity shared by in-process and subprocess observers."""

    def __init__(self):
        self._state = ToleshState.ASLEEP  # Start asleep
        self._state_lock = threading.Lock()
        self._owner: Optional[_StateOwnership] = None
        self._state_file = _get_tolesh_state_file()
        # Always start fresh in ASLEEP state on app launch
        # (state file is for cross-process communication during a session,
        # not for persisting state across app restarts)
        self._write_state(ToleshState.ASLEEP)

    def _published_state(self) -> Optional[tuple[ToleshState, tuple[int, int, int, int]]]:
        try:
            with open(self._state_file, 'r') as published:
                state = ToleshState(published.read().strip())
                identity = _file_identity(os.fstat(published.fileno()))
            return state, identity
        except (ValueError, OSError):
            return None

    @property
    def state(self) -> ToleshState:
        """Read a complete publication, with the last local state as fallback."""
        published = self._published_state()
        if published is not None:
            return published[0]
        with self._state_lock:
            return self._state

    def _write_state(self, state: ToleshState) -> Optional[tuple[int, int, int, int]]:
        """Publish a complete snapshot without following the shared path."""
        pending_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".tolesh-state-",
                dir=os.path.dirname(self._state_file) or ".", delete=False,
            ) as pending:
                pending_path = pending.name
                pending.write(state.value)
            identity = _file_identity(os.stat(pending_path, follow_symlinks=False))
            os.replace(pending_path, self._state_file)
            return identity
        except OSError:
            # Publication is best-effort; retain the previous shared snapshot.
            pass
        finally:
            if pending_path is not None:
                try:
                    os.unlink(pending_path)
                except OSError:
                    pass

    def set_state(self, state: ToleshState) -> None:
        """Set the Tolesh state (thread-safe, cross-process)."""
        with self._state_lock:
            self._owner = None
            self._state = state
            self._write_state(state)


    def acquire_state(self, state: ToleshState, *,
                      protected_states: set[ToleshState] | frozenset[ToleshState] = frozenset(),
                      restore_state: Optional[ToleshState] = None) -> Optional[_StateOwnership]:
        """Own a temporary local activity unless protected work is visible."""
        with self._state_lock:
            published = self._published_state()
            previous = published[0] if published is not None else self._state
            if previous in protected_states:
                return None
            if restore_state is not None:
                previous = restore_state
            elif self._owner is not None and published is not None and published[1] == self._owner.publication:
                previous = self._owner.previous
            identity = self._write_state(state)
            if identity is None:
                return None
            owner = _StateOwnership(previous, identity)
            self._state = state
            self._owner = owner
            return owner

    def release_state(self, owner: Optional[_StateOwnership]) -> bool:
        """Restore only the current local owner's unchanged publication."""
        with self._state_lock:
            if owner is None or self._owner is not owner:
                return False
            self._owner = None
            published = self._published_state()
            if published is None or published[1] != owner.publication:
                return False
            identity = self._write_state(owner.previous)
            if identity is None:
                return False
            self._state = owner.previous
            return True


# Module-level singleton instance
_tolesh_state_instance: Optional[ToleshStateManager] = None
_tolesh_state_lock = threading.Lock()

def get_tolesh_state() -> ToleshStateManager:
    """Get the global Tolesh state singleton."""
    global _tolesh_state_instance
    with _tolesh_state_lock:
        if _tolesh_state_instance is None:
            _tolesh_state_instance = ToleshStateManager()
        return _tolesh_state_instance

