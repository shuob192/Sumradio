from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._sequence = 0

    async def publish(self, event_type: str, payload: dict) -> None:
        self._sequence += 1
        event = {"id": self._sequence, "type": event_type, "payload": payload}
        for queue in tuple(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    data = json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers.discard(queue)
