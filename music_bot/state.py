"""In-memory pending-action stores.

Callback data carries an opaque token that maps to the context needed to finish
an action (the original URL, the chat to reply in). Two things matter here:

* The originating user id is stored alongside, so a callback can verify that
  the person clicking is the person who started the action. In a group chat an
  inline keyboard is visible to everyone, so without this check any member can
  trigger a download on someone else's link and have the job recorded under
  their own id.
* Tokens expire, so abandoned keyboards do not leak memory for the process
  lifetime.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("music_bot.state")

PENDING_TTL_SEC = 900.0


@dataclass(slots=True)
class Entry:
    """A pending action together with who started it and when."""

    value: Any
    user_id: int
    chat_id: int
    created_at: float


class PendingStore:
    """Token -> Entry map with TTL and per-user ownership."""

    def __init__(self, ttl: float = PENDING_TTL_SEC) -> None:
        self.ttl = ttl
        self._items: dict[str, Entry] = {}

    def put(self, key: str, value: Any, *, user_id: int, chat_id: int) -> None:
        self._prune()
        self._items[key] = Entry(value, user_id, chat_id, time.monotonic())

    def peek(self, key: str, *, user_id: int) -> Entry | None:
        """Return an unexpired entry owned by user_id without removing it."""
        entry = self._items.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.created_at > self.ttl:
            self._items.pop(key, None)
            return None
        if entry.user_id != user_id:
            log.info(f"[STATE] Token {key} belongs to another user")
            return None
        return entry

    def take(self, key: str, *, user_id: int) -> Entry | None:
        """Consume an unexpired entry, but only for its owning user.

        A click by the wrong user must not consume the token, otherwise a
        bystander could invalidate the real user's keyboard.
        """
        entry = self.peek(key, user_id=user_id)
        if entry is None:
            return None
        self._items.pop(key, None)
        return entry

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [k for k, e in self._items.items() if now - e.created_at > self.ttl]
        for k in stale:
            self._items.pop(k, None)
