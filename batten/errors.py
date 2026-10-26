"""Shared error type for data fetching.

Lives in its own module so the providers package and noaa can both import it
without a circular import. `noaa.FetchError` is a re-export of this class.
"""

from __future__ import annotations


class FetchError(Exception):
    """A data-fetch failure with a machine-readable kind.

    Kinds:
      dns         — cannot resolve the host (internet down / captive portal)
      timeout     — host unreachable or too slow (weak signal / congestion)
      http        — server answered with an error status
      parse       — response was not the shape we expected
      unsupported — the endpoint does not cover this location (NOT an outage)
    """

    def __init__(self, kind: str, detail: str, status: int | None = None):
        super().__init__(f"[{kind}] {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status
