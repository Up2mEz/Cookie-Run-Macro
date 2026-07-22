from __future__ import annotations

from enum import Enum
from threading import RLock
from typing import Callable


class AppState(str, Enum):
    IDLE = "IDLE"
    DISCOVERING_ADB = "DISCOVERING_ADB"
    CONNECTING = "CONNECTING"
    WAITING_FOR_AUTO_SYNC = "WAITING_FOR_AUTO_SYNC"
    WAITING_FOR_MANUAL_SYNC = "WAITING_FOR_MANUAL_SYNC"
    SYNCED = "SYNCED"
    PLAYING = "PLAYING"
    RECORDING_WAITING_SYNC = "RECORDING_WAITING_SYNC"
    RECORDING = "RECORDING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


ALLOWED_TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.IDLE: {
        AppState.DISCOVERING_ADB,
        AppState.CONNECTING,
        AppState.RECORDING_WAITING_SYNC,
        AppState.WAITING_FOR_AUTO_SYNC,
        AppState.STOPPED,
        AppState.ERROR,
    },
    AppState.DISCOVERING_ADB: {AppState.IDLE, AppState.CONNECTING, AppState.ERROR, AppState.STOPPING},
    AppState.CONNECTING: {
        AppState.WAITING_FOR_AUTO_SYNC,
        AppState.WAITING_FOR_MANUAL_SYNC,
        AppState.RECORDING_WAITING_SYNC,
        AppState.ERROR,
        AppState.STOPPING,
    },
    AppState.WAITING_FOR_AUTO_SYNC: {
        AppState.WAITING_FOR_MANUAL_SYNC,
        AppState.SYNCED,
        AppState.STOPPING,
        AppState.ERROR,
    },
    AppState.WAITING_FOR_MANUAL_SYNC: {AppState.SYNCED, AppState.STOPPING, AppState.ERROR},
    AppState.SYNCED: {AppState.PLAYING, AppState.RECORDING, AppState.STOPPING, AppState.ERROR},
    AppState.PLAYING: {AppState.STOPPING, AppState.STOPPED, AppState.ERROR},
    AppState.RECORDING_WAITING_SYNC: {
        AppState.WAITING_FOR_AUTO_SYNC,
        AppState.WAITING_FOR_MANUAL_SYNC,
        AppState.SYNCED,
        AppState.STOPPING,
        AppState.ERROR,
    },
    AppState.RECORDING: {AppState.STOPPING, AppState.STOPPED, AppState.ERROR},
    AppState.STOPPING: {AppState.STOPPED, AppState.ERROR},
    AppState.STOPPED: {
        AppState.IDLE,
        AppState.DISCOVERING_ADB,
        AppState.CONNECTING,
        AppState.RECORDING_WAITING_SYNC,
        AppState.ERROR,
    },
    AppState.ERROR: {AppState.IDLE, AppState.DISCOVERING_ADB, AppState.CONNECTING, AppState.STOPPING},
}


class InvalidStateTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, on_change: Callable[[AppState], None] | None = None) -> None:
        self._state = AppState.IDLE
        self._lock = RLock()
        self._on_change = on_change

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    def transition(self, new_state: AppState, *, force: bool = False) -> AppState:
        with self._lock:
            old_state = self._state
            if new_state == old_state:
                return old_state
            if not force and new_state not in ALLOWED_TRANSITIONS[old_state]:
                raise InvalidStateTransition(f"เปลี่ยนสถานะจาก {old_state.value} ไป {new_state.value} ไม่ได้")
            self._state = new_state
        if self._on_change:
            self._on_change(new_state)
        return new_state
