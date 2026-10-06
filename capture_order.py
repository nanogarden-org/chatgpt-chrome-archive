"""Reconstruct DOM order across overlapping snapshots without text deduplication."""
from __future__ import annotations


class CaptureOrder:
    def __init__(self):
        self.messages = {}
        self.edges = {}
        self.problems = set()
        self.snapshots = 0

    def add(self, batch):
        self.snapshots += 1
        keys = []
        for i, item in enumerate(batch):
            key = ("message", item["message_id"]) if item.get("message_id") else ("node", item.get("capture_id"))
            if key[1] is None:
                key = ("unidentified", self.snapshots, i)
                self.problems.add("message identity unavailable")
            if key in keys:
                self.problems.add("duplicate identity in one snapshot")
                continue
            if key in self.messages and any(self.messages[key].get(k) != item.get(k) for k in ("role", "text")):
                self.problems.add("message content changed during capture")
            self.messages[key] = item
            self.edges.setdefault(key, set())
            keys.append(key)
        for left, right in zip(keys, keys[1:]):
            self.edges[left].add(right)

    def ordered(self):
        incoming = {key: 0 for key in self.messages}
        for targets in self.edges.values():
            for target in targets:
                incoming[target] += 1
        ordered = []
        while incoming:
            ready = [key for key, count in incoming.items() if count == 0]
            if not ready:
                self.problems.add("conflicting snapshot order")
                ordered.extend(incoming)
                break
            if len(ready) > 1:
                self.problems.add("disconnected or ambiguous snapshot order")
            key = ready[0]
            ordered.append(key)
            del incoming[key]
            for target in self.edges[key]:
                if target in incoming:
                    incoming[target] -= 1
        return [self.messages[key] for key in ordered]
