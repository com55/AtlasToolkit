# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Thread-safe desktop events with a bounded initial backlog."""
from collections import deque
import json
import queue
import threading


_END = object()


class _Subscription:
    def __init__(self, broker):
        self.broker = broker
        self.queue = queue.Queue()
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return self.get()

    def get(self, timeout=None):
        """Read an event, optionally timing out for an SSE heartbeat."""
        if self.closed:
            raise StopIteration
        item = self.queue.get(timeout=timeout)
        if item is _END:
            self.closed = True
            raise StopIteration
        return item

    def close(self):
        with self.broker._lock:
            self.broker._subscribers.discard(self)
            self.queue.put(_END)


class EventBroker:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = set()
        self._backlog = deque(maxlen=100)
        self._subscribed = False
        self._closed = False

    @property
    def has_subscriber(self):
        with self._lock:
            return bool(self._subscribers)

    def publish(self, kind: str, **data):
        event = {"kind": kind, **data}
        json.dumps(event)
        with self._lock:
            if self._closed:
                return
            if not self._subscribed:
                self._backlog.append(event)
            for subscriber in self._subscribers:
                subscriber.queue.put(event)

    def subscribe(self):
        subscriber = _Subscription(self)
        with self._lock:
            if self._closed:
                subscriber.queue.put(_END)
            else:
                if not self._subscribed:
                    for event in self._backlog:
                        subscriber.queue.put(event)
                    self._backlog.clear()
                    self._subscribed = True
                self._subscribers.add(subscriber)
        return subscriber

    def close(self):
        with self._lock:
            self._closed = True
            self._backlog.clear()
            for subscriber in self._subscribers:
                subscriber.queue.put(_END)
            self._subscribers.clear()
