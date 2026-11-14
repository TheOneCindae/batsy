"""Thread-safe in-process event fanout for the voice and chat surfaces."""
import queue
import threading
from collections import deque


class EventBus:
    def __init__(self, history=300):
        self.lock = threading.RLock()
        self.subscribers = set()
        self.history = deque(maxlen=history)
        self.seq = 0

    def publish(self, kind, **data):
        with self.lock:
            self.seq += 1
            event = {"seq": self.seq, "type": kind, **data}
            self.history.append(event)
            for q in tuple(self.subscribers):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except queue.Empty:
                        pass
            return event

    def subscribe(self, after=0):
        q = queue.Queue(maxsize=200)
        with self.lock:
            for event in self.history:
                if event["seq"] > after:
                    try:
                        q.put_nowait(event)
                    except queue.Full:
                        break
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subscribers.discard(q)
