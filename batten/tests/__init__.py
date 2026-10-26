"""Batten's offline test suite.

Run:  batten selftest   |   python3 -m unittest discover -s batten/tests -v

Nothing here touches the network: HTTP transports are patched, and any test
that touches on-disk state runs inside a throwaway BATTON_HOME.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
import urllib.error

from .. import noaa


@contextlib.contextmanager
def temp_home():
    """Isolate BATTON_HOME (config/state/supplies/log) for the block."""
    old = os.environ.get("BATTON_HOME")
    os.environ["BATTON_HOME"] = tempfile.mkdtemp(prefix="batten-test-")
    try:
        yield os.environ["BATTON_HOME"]
    finally:
        if old is None:
            os.environ.pop("BATTON_HOME", None)
        else:
            os.environ["BATTON_HOME"] = old


def write_config(**overrides):
    from .. import config
    cfg = dict(config.DEFAULTS)
    cfg.update({"place": "Testville", "lat": 25.7617, "lon": -80.1918,
                "timezone": "America/New_York", "household": 3})
    cfg.update(overrides)
    config.save_config(cfg)
    return cfg


def respond(body: bytes, status: int = 200):
    """Build a noaa.http_get replacement returning fixed bytes."""
    def fake(url, timeout, headers):
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "error", {}, body)
        return status, body
    return fake


def raise_fetch(exc: Exception):
    def fake(url, timeout, headers):
        raise exc
    return fake


def sample_storm(**over):
    s = {"id": "al912026", "name": "Test", "bin": "AL9", "classification": "HU",
         "kt": 83.4, "mph": 96, "pressure": 970, "lat": 20.0, "lon": -105.0,
         "movement_dir": 320, "movement_kt": 8, "advisory": "12",
         "issued": "2026-10-17T21:00:00Z", "advisory_url": "",
         "discussion_url": "", "graphics_url": "", "last_update": ""}
    s.update(over)
    return s


def sample_alert(**over):
    a = {"id": "a1", "event": "Hurricane Warning", "severity": "Severe",
         "headline": "HW for coast", "area": "Coastal County", "sent": "2026-10-17T21:00Z",
         "ends": "2026-10-18T21:00Z", "description": "desc", "instruction": "instr"}
    a.update(over)
    return a
