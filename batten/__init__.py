"""Batten — an offline-first severe weather copilot.

Inspired by a hurricane-season Discord watcher; rebuilt to run its intelligence
on a local open-weight model (via Ollama) so chat, briefings, and worst-case
planning keep working when the internet does not.

Zero runtime dependencies: everything is Python stdlib. The only optional
external component is Ollama (http://localhost:11434) for the AI layer.
"""

__version__ = "0.1.0"
APP_NAME = "batten"
USER_AGENT = f"batten/{__version__} (offline-first severe weather assistant)"
