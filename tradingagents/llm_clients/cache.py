"""SQLite-backed LangChain LLM cache.

Same prompt + same model = same response, served from disk in milliseconds
instead of a fresh API call. Most useful when:

- The user re-runs a ticker shortly after a previous run.
- A sector run analyzes 12 tickers and several share identical sub-prompts
  (e.g. macro context, identical tool-call results).
- The Reflector re-walks prior decisions when scoring.

Cache file lives next to portfolio.db in ~/.tradingagents/. Safe to delete
at any time — it's a pure perf optimization, not a source of truth.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_INITIALIZED = False


def enable_llm_cache(db_path: Path | None = None) -> Path | None:
    """Install a global SQLite cache on LangChain. Idempotent.

    Returns the cache db path, or None when the cache cannot be set up
    (e.g. opt-out via ``TRADINGAGENTS_LLM_CACHE=0`` or library missing)."""
    global _INITIALIZED
    if _INITIALIZED:
        return None
    if os.environ.get("TRADINGAGENTS_LLM_CACHE", "1") == "0":
        log.info("LLM cache disabled via TRADINGAGENTS_LLM_CACHE=0")
        _INITIALIZED = True
        return None

    path = Path(db_path) if db_path else Path.home() / ".tradingagents" / "llm_cache.db"
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from langchain_community.cache import SQLiteCache
        from langchain_core.globals import set_llm_cache
    except ImportError as e:  # pragma: no cover - dependency hint only
        log.warning("LLM cache unavailable: %s", e)
        _INITIALIZED = True
        return None

    set_llm_cache(SQLiteCache(database_path=str(path)))
    _INITIALIZED = True
    log.debug("LLM cache enabled at %s", path)
    return path
