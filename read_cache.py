"""Short-lived, bounded coalescing for observational reads only.

Never use this for authorization, mutations, or mutation preflight. Values retain
their original observation timestamps; a failed refresh never serves stale data.
"""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
import threading
import time


class ReadCache:
    def __init__(self, capacity=128, clock=time.monotonic):
        self.capacity = capacity
        self.clock = clock
        self.lock = threading.Lock()
        self.values = OrderedDict()
        self.running = {}
        self.generation = 0

    def clear(self):
        with self.lock:
            self.generation += 1
            self.values.clear()
            self.running.clear()

    def get(self, key, read, ttl):
        with self.lock:
            value = self.values.get(key)
            if value and value[0] > self.clock():
                self.values.move_to_end(key)
                return deepcopy(value[1])
            future = self.running.get(key)
            owner = future is None
            if owner:
                future = Future()
                self.running[key] = future
                generation = self.generation
        if not owner:
            return deepcopy(future.result())
        try:
            value = read()
            with self.lock:
                if generation == self.generation:
                    self.values[key] = (self.clock() + ttl, deepcopy(value))
                    self.values.move_to_end(key)
                    while len(self.values) > self.capacity:
                        self.values.popitem(last=False)
            future.set_result(deepcopy(value))
            return value
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                if self.running.get(key) is future:
                    self.running.pop(key)
