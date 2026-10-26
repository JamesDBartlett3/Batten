"""Notifications: console always; optional Discord webhook.

The webhook path bakes in the original storm bot's operational scars:
  * NHC/CloudFront 403s bot user-agents on images — so we send text only
    (the cone image is downloadable via `batten cone` instead).
  * Silent webhook failures are unacceptable — log every HTTP code, retry
    with backoff, honor 429 Retry-After, and always fall back to console.
  * Discord caps messages at 2000 chars — chunk rather than truncate.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

MAX_DISCORD_CHARS = 1900


def console(text: str) -> None:
    print(text, file=sys.stderr if "\n⚠" in text[:200] else sys.stdout)


def send_discord(webhook_url: str, text: str, quiet: bool = False) -> bool:
    """Post text to a Discord webhook with retries. Returns final success.

    Chunks long messages. Never raises: failures are reported on stderr and
    the console fallback is the caller's business.
    """
    if not webhook_url:
        return False
    chunks = _chunk(text)
    ok_all = True
    for i, chunk in enumerate(chunks):
        body = json.dumps({"content": chunk}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "batten"},
        )
        sent = False
        for attempt in (1, 2, 3):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    code = resp.status
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code == 429:
                    retry_after = 10.0
                    try:
                        data = json.loads(exc.read().decode("utf-8"))
                        retry_after = float(data.get("retry_after", 10.0))
                    except Exception:
                        pass
                    if not quiet:
                        print(f"discord webhook 429, sleeping {retry_after:.0f}s", file=sys.stderr)
                    time.sleep(retry_after)
                    continue
            except (urllib.error.URLError, OSError) as exc:
                if not quiet:
                    print(f"discord webhook attempt {attempt} failed: {exc}", file=sys.stderr)
                time.sleep(5 * attempt)
                continue
            if code in (200, 204):
                sent = True
                break
            if not quiet:
                print(f"discord webhook attempt {attempt} http={code}", file=sys.stderr)
            time.sleep(5 * attempt)
        if not sent:
            ok_all = False
            if not quiet:
                print("discord webhook failed for chunk "
                      f"{i + 1}/{len(chunks)} — showing on console instead", file=sys.stderr)
                console(chunk)
        elif i + 1 < len(chunks):
            time.sleep(1)  # stay well under the 5 msgs / 2s webhook limit
    return ok_all


def _chunk(text: str) -> list[str]:
    chunks = []
    remaining = text
    while len(remaining) > MAX_DISCORD_CHARS:
        cut = remaining.rfind("\n", 0, MAX_DISCORD_CHARS)
        if cut < 200:
            cut = MAX_DISCORD_CHARS
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks
