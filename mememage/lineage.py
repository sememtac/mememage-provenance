"""Lineage tracking — chain of creation linking each image to its predecessor.

Uses SQLite for atomic writes and crash recovery. The database is the
single source of truth for the chain state. Falls back to JSON for
backward compatibility if the database doesn't exist yet.
"""

import contextlib
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from mememage.config import IA_DOWNLOAD_URL
from mememage.net import fetch_json

log = logging.getLogger(__name__)

from mememage import chains
# Paths must resolve per call, not at import — chains.path() reads the
# active chain from ~/.mememage/current_chain at call time, and the
# server switches chains mid-process. Caching either path at module
# load would bind every mint to whatever chain happened to be active
# the moment lineage.py was first imported, causing parent_id to flow
# across chains.


def _db_path() -> Path:
    return chains.path("mememage.db")


def _state_file_path() -> Path:
    return chains.path("last_id.json")  # legacy JSON migration source


def _get_db():
    """Get a SQLite connection with WAL mode for crash safety."""
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lineage (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_archive_id TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _migrate_from_json():
    """One-time migration: if the old JSON file exists and DB is empty, import it."""
    state_file = _state_file_path()
    if not state_file.exists():
        return
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        last_id = data.get("last_archive_id")
        if not last_id:
            return
        with contextlib.closing(_get_db()) as conn:
            existing = conn.execute("SELECT last_archive_id FROM lineage WHERE id=1").fetchone()
            if existing is None:
                conn.execute("INSERT INTO lineage (id, last_archive_id) VALUES (1, ?)", (last_id,))
                conn.commit()
                log.info("Migrated lineage from JSON: %s", last_id)
    except Exception as e:
        log.warning("Lineage JSON migration failed: %s", e)


def get_parent_id(state_file: Path | None = None) -> str | None:
    """Read the last generated archive_id.
    Returns None if no previous generation exists (genesis image)."""
    # Legacy path for tests
    if state_file is not None:
        if not state_file.exists():
            return None
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return data.get("last_archive_id")
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(
                f"Lineage state file exists but is corrupt or unreadable: {state_file}: {e}"
            ) from e

    # Migrate from JSON on first call
    _migrate_from_json()

    try:
        with contextlib.closing(_get_db()) as conn:
            row = conn.execute("SELECT last_archive_id FROM lineage WHERE id=1").fetchone()
            return row[0] if row else None
    except Exception as e:
        raise RuntimeError(
            f"Failed to read lineage from DB: {e}"
        ) from e


def set_parent_id(archive_id: str, state_file: Path | None = None) -> None:
    """Write the current archive_id as the new parent for the next image."""
    # Legacy path for tests
    if state_file is not None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"last_archive_id": archive_id})
        fd, tmp_path = tempfile.mkstemp(dir=str(state_file.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp_path, str(state_file))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return

    try:
        with contextlib.closing(_get_db()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO lineage (id, last_archive_id) VALUES (1, ?)",
                (archive_id,)
            )
            conn.commit()
            log.debug("Lineage updated: %s", archive_id)
    except Exception as e:
        log.error("Failed to write lineage to DB: %s", e)
        raise


def clear_lineage(chain_id: str | None = None) -> str | None:
    """Delete a chain's lineage thread so the NEXT mint is a genesis.

    Returns the prior ``last_archive_id`` (for rollback — re-set it with
    ``set_parent_id`` if reset by accident), or None if the chain had no
    lineage. After this, ``get_parent_id()`` returns None and
    ``core._step_identifier`` treats the next mint as genesis (parent_id
    null) — which, on a chain that pins ``genesis_identifier``, lands the
    next mint on the pinned slot. This is the deliberate "start the chain
    over" operation that ``chains.reset_state(to_genesis=True)`` wraps; it
    does NOT touch souls or chunk_state.
    """
    if chain_id is None:
        db_path = _db_path()
    else:
        db_path = chains.chain_dir(chain_id) / "mememage.db"
    if not db_path.exists():
        return None
    try:
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='lineage'"
            ).fetchone()
            if tbl is None:
                return None
            row = conn.execute(
                "SELECT last_archive_id FROM lineage WHERE id=1"
            ).fetchone()
            prior = row[0] if row else None
            conn.execute("DELETE FROM lineage WHERE id=1")
            conn.commit()
            return prior
    except sqlite3.Error as e:
        raise RuntimeError(
            f"Failed to clear lineage for {chain_id or '<active>'}: {e}"
        ) from e


def fetch_lineage_chain(identifier: str, max_depth: int = 10) -> list[dict]:
    """Walk backwards through parent_id links, fetching each metadata record from IA.
    Returns list of {identifier, timestamp, prompt_preview, parent_id} dicts.
    Stops at genesis (no parent_id) or max_depth."""
    chain: list[dict] = []
    current_id = identifier

    for _ in range(max_depth):
        url = f"{IA_DOWNLOAD_URL}/{current_id}/metadata.json"
        try:
            record = fetch_json(url)
        except Exception as e:
            log.warning("Lineage chain walk stopped at %s: %s", current_id, e)
            break
        if record is None:
            break

        prompt = record.get("prompt", "")
        prompt_preview = prompt[:80] if prompt else ""

        entry = {
            "identifier": current_id,
            "timestamp": record.get("timestamp"),
            "prompt_preview": prompt_preview,
            "parent_id": record.get("parent_id"),
        }
        chain.append(entry)

        parent = record.get("parent_id")
        if not parent:
            break
        current_id = parent

    return chain
