"""Ephemeral, per-browser conversation context for source-grounded follow-ups.

Conversation messages and repository summaries live in the user's browser storage.
This process-local cache contains only the bounded source bodies needed to avoid
re-downloading them during an active local session. It is intentionally not a
shared history service and is discarded when the server restarts.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any

from .config import MAX_CONVERSATION_CONTEXTS, MAX_CONVERSATION_SOURCE_FILES
from .errors import DiscoveryError


class ConversationCache:
    def __init__(self) -> None:
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def store(self, retrieval: dict[str, Any], conversation_id: object = None) -> str:
        """Save bounded context under an unguessable browser-provided or generated ID."""
        candidate = str(conversation_id or "").strip()
        identifier = candidate if 16 <= len(candidate) <= 128 else secrets.token_urlsafe(24)
        record = deepcopy(retrieval)
        with self._lock:
            self._records[identifier] = {"retrieval": record, "updated_at": time.monotonic()}
            self._records.move_to_end(identifier)
            while len(self._records) > MAX_CONVERSATION_CONTEXTS:
                self._records.popitem(last=False)
        return identifier

    def retrieval(self, conversation_id: object) -> dict[str, Any]:
        identifier = str(conversation_id or "").strip()
        with self._lock:
            record = self._records.get(identifier)
            if record is None:
                raise DiscoveryError(
                    "conversation_context_expired",
                    "This conversation's temporary source context is no longer available. Start a new analysis to continue.",
                    409,
                )
            record["updated_at"] = time.monotonic()
            self._records.move_to_end(identifier)
            return deepcopy(record["retrieval"])

    def merge_sources(self, conversation_id: object, follow_up: dict[str, Any]) -> None:
        """Retain a small reusable source set for later questions in this session."""
        identifier = str(conversation_id or "").strip()
        with self._lock:
            record = self._records.get(identifier)
            if record is None:
                return
            existing = {item["path"]: item for item in record["retrieval"].get("selected_files", [])}
            for item in follow_up.get("selected_files", []):
                existing[item["path"]] = item
            retained = list(existing.values())[-MAX_CONVERSATION_SOURCE_FILES:]
            record["retrieval"]["selected_files"] = deepcopy(retained)
            record["updated_at"] = time.monotonic()

