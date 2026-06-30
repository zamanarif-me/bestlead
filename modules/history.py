"""Persistent session history + cross-session deduplication.

Files (created under ./data):
    data/history/_seen_index.json   -> {identity_key: session_id}  (fast dedup)
    data/history/session_*.csv      -> full snapshot of each run
    data/history/session_*.meta.json
    data/exports/<ts>_<name>.csv    -> a copy of every downloaded export

Cross-session flow:
    flag_seen_before(leads)   # mark leads already seen in PAST sessions
    ...show / export...
    save_session(leads)       # persist run + add its NEW keys to the index
Always call flag_seen_before BEFORE save_session so a run isn't compared to itself.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

from .dedup import identity_keys

BASE_DIR = "data"
HISTORY_DIR = os.path.join(BASE_DIR, "history")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
INDEX_FILE = os.path.join(HISTORY_DIR, "_seen_index.json")


def _ensure_dirs() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(EXPORTS_DIR, exist_ok=True)


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """Write to a temp file in the same dir, then os.replace() into place.

    os.replace() is atomic on a single volume, so a crash or interrupt mid-write
    can never leave a half-written file. Critical for _seen_index.json: a
    truncated index is silently swallowed by load_seen_index() and would wipe
    all cross-session dedup memory.
    """
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_text(path: str, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


# ─────────────── seen index ───────────────

def load_seen_index() -> dict:
    _ensure_dirs()
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def flag_seen_before(leads: list[dict], index: dict | None = None) -> list[dict]:
    """Mark leads whose identity was recorded in a PAST session."""
    index = index if index is not None else load_seen_index()
    for lead in leads:
        lead["seen_before"] = any(k in index for k in identity_keys(lead))
    return leads


# ─────────────── sessions ───────────────

def save_session(leads: list[dict], meta: dict | None = None) -> dict:
    """Persist a CSV snapshot of the run and add its new keys to the seen index."""
    from .exporter import to_full_csv  # local import avoids circular import

    _ensure_dirs()
    index = load_seen_index()
    session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    added = 0
    for lead in leads:
        if lead.get("is_duplicate"):
            continue
        for k in identity_keys(lead):
            if k not in index:
                index[k] = session_id
                added += 1

    csv_path = os.path.join(HISTORY_DIR, f"{session_id}.csv")
    _atomic_write_bytes(csv_path, to_full_csv(leads))

    meta = dict(meta or {})
    meta.update({"rows": len(leads), "new_keys": added, "saved_at": datetime.now().isoformat()})
    _atomic_write_text(os.path.join(HISTORY_DIR, f"{session_id}.meta.json"), json.dumps(meta))

    # Write the index LAST and atomically: the session snapshot is on disk before
    # we commit its keys, so a crash never leaves keys pointing at a missing CSV.
    _atomic_write_text(INDEX_FILE, json.dumps(index))

    return {"session_id": session_id, "path": csv_path, "new_keys": added, "rows": len(leads)}


def list_sessions() -> list[dict]:
    _ensure_dirs()
    out: list[dict] = []
    for fn in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if not (fn.startswith("session_") and fn.endswith(".csv")):
            continue
        full = os.path.join(HISTORY_DIR, fn)
        meta: dict = {}
        meta_path = full[:-4] + ".meta.json"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        out.append({
            "session_id": fn[:-4],
            "path": full,
            "modified": datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
            "rows": meta.get("rows"),
            "meta": meta,
        })
    return out


def read_session_csv(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


# ─────────────── exports ───────────────

def save_export(filename: str, content) -> str:
    _ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(EXPORTS_DIR, f"{stamp}_{filename}")
    data = content if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    return path
