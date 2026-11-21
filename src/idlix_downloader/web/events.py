"""Structured, bounded progress; provider strings are deliberately discarded."""

import math
import time

PHASES = {"extracting", "waiting_source", "downloading", "merging", "validating", "subtitles"}


class Events:
    def __init__(self, emit, *, clock=time.monotonic):
        self.emit = emit
        self.clock = clock
        self.last = float("-inf")

    def phase(self, phase):
        if phase in PHASES:
            self.emit({"type": "progress", "phase": phase})

    def progress(self, data):
        if data.get("status") == "finished":
            self.phase("merging")
            return
        now = self.clock()
        if now - self.last < 1:
            return
        self.last = now
        event = {"type": "progress", "phase": "downloading"}
        for source, target in (
            ("downloaded_bytes", "downloaded_bytes"),
            ("total_bytes", "total_bytes"),
            ("total_bytes_estimate", "total_bytes_estimate"),
            ("fragment_index", "fragment_index"),
            ("fragment_count", "fragment_count"),
            ("speed", "speed"),
            ("eta", "eta"),
        ):
            value = data.get(source)
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                event[target] = value
        self.emit(event)

    def postprocess(self, _data):
        self.phase("merging")


class SafeLogger:
    def __init__(self, events):
        self.events = events

    def debug(self, message):
        if "Menunggu sesi situs" in str(message):
            self.events.phase("waiting_source")

    def info(self, _message):
        pass

    def warning(self, message):
        text = str(message).lower()
        if "http error 401" in text or "http error 403" in text:
            self.events.session_expired = True

    error = warning
