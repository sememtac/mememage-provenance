"""Payload staging — the gated middle layer between live sources and seal.

Workflow:
    edit sources (docs/, payload/) →
    `mememage payload build` (promote to Payload/) →
    `mememage payload status` (verify) →
    `python -m mememage.site_pack seal` (reads only from Payload/)

Payload/ is the single source of truth for what enters the chain at seal time.
Nothing ships unless it has been promoted.

The packed decoder is written to BOTH Payload/decoder.html (canonical for seal)
AND docs/standalone.html (publishable web version) from the same build, keeping
them in lockstep.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
PAYLOAD_ROOT = PROJECT_ROOT / "Payload"  # parent of per-chain subdirs
STANDALONE_PATH = DOCS_DIR / "standalone.html"


def payload_dir(chain_id: str | None = None) -> Path:
    """Return the staging directory for the given (or active) chain.

    Layout: ``Payload/<chain_id>/artifact_name``. Resolves the active
    chain via ``chains.current()`` at call time so switching chains in
    the dashboard takes effect immediately for the next build / inspect.
    """
    from mememage import chains
    cid = chain_id or chains.current()
    return PAYLOAD_ROOT / cid


def manifest_path(chain_id: str | None = None) -> Path:
    return payload_dir(chain_id) / "manifest.json"



# ---------------------------------------------------------------------------
# Source readers — one per artifact
# ---------------------------------------------------------------------------

def _resolve_source(src: str) -> Path:
    """Resolve a source path string. Absolute and ``~``-paths are kept;
    everything else is interpreted relative to the project root.
    """
    src = src.strip()
    if src.startswith("~"):
        return Path(src).expanduser()
    p = Path(src)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


def _read_entry_bytes(entry) -> bytes:
    """Concatenate all of an entry's sources into one byte blob.

    Source-type dispatch:
      - **Directory** → ``inline_all()`` (the legacy "decoder folder" packer
        — assumes index.html as the entry point and runs an asset-map glob
        for the decoder's runtime ``INLINE_ASSETS`` lookups).
      - **.html / .htm file** → ``inline_html()`` (the generic packer —
        inlines every ``<link>``, ``<script>``, ``<img>``, etc. relative
        to the HTML's own directory). Any user-authored web page works
        with this path.
      - **Anything else** → raw bytes.

    Multi-source entries get a single newline byte between parts.
    """
    parts = []
    for src in entry.sources:
        p = _resolve_source(src)
        if p.is_dir():
            from mememage.site_pack import inline_all
            parts.append(inline_all(p).encode("utf-8"))
        elif p.suffix.lower() in (".html", ".htm"):
            if not p.exists():
                raise FileNotFoundError(f"Source missing: {p}")
            from mememage.site_pack import inline_html
            parts.append(inline_html(p).encode("utf-8"))
        else:
            if not p.exists():
                raise FileNotFoundError(f"Source missing: {p}")
            parts.append(p.read_bytes())
    return b"\n".join(parts)


def _artifacts():
    """Iterate the active chain's entries.

    Yields dicts of {target, source_desc, reader} matching the previous
    shape, but the target is now the entry NAME (no extension) and the
    reader produces the concatenated bytes for that entry.
    """
    from mememage import chain_config
    cfg = chain_config.load()
    items = []
    for name, entry in cfg.entries.items():
        source_desc = ", ".join(entry.sources)
        def make_reader(e):
            return lambda: _read_entry_bytes(e)
        items.append({
            "target": name,
            "source_desc": source_desc,
            "reader": make_reader(entry),
        })
    return items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_manifest() -> dict | None:
    mp = manifest_path()
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build() -> dict:
    """Regenerate Payload/ from active sources. Returns the new manifest.

    One Payload file per chain-config entry. The file name = entry name
    (no extension). For directory-sourced entries (the "site" build step)
    the contents are the packed standalone HTML; for file-sourced entries
    the contents are the raw bytes (concatenated if multi-source).

    Also writes ``docs/standalone.html`` as the publishable mirror of
    the active chain's "decoder" entry (if any).
    """
    pdir = payload_dir()
    pdir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "built_at": _utc_now(),
        "artifacts": {},
    }

    for artifact in _artifacts():
        target = pdir / artifact["target"]
        data = artifact["reader"]()
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(data)
        manifest["artifacts"][artifact["target"]] = {
            "source": artifact["source_desc"],
            "sha256": _sha256(data),
            "size": len(data),
        }
        # Mirror the packed decoder entry to docs/standalone.html for serving.
        if artifact["target"] == "decoder":
            STANDALONE_PATH.write_bytes(data)

    manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def status() -> dict:
    """Return per-artifact status dict.

    Status values:
        in_sync         — Payload bytes match current source bytes
        drifted         — Payload exists but differs from source
        missing_payload — Payload missing (or no manifest entry)
        missing_source  — source not readable (file missing or build error)
    """
    manifest = _read_manifest()
    if manifest is None:
        return {"manifest_missing": True, "statuses": {}}

    pdir = payload_dir()
    statuses = {}
    for artifact in _artifacts():
        target_name = artifact["target"]
        target_path = pdir / target_name
        payload_entry = manifest.get("artifacts", {}).get(target_name)

        try:
            current = artifact["reader"]()
        except Exception as e:
            statuses[target_name] = {
                "status": "missing_source",
                "error": str(e),
            }
            continue

        current_hash = _sha256(current)

        if not target_path.exists() or payload_entry is None:
            statuses[target_name] = {
                "status": "missing_payload",
                "source_hash": current_hash,
                "source_size": len(current),
            }
        elif payload_entry["sha256"] != current_hash:
            statuses[target_name] = {
                "status": "drifted",
                "payload_hash": payload_entry["sha256"],
                "source_hash": current_hash,
                "payload_size": payload_entry["size"],
                "source_size": len(current),
            }
        else:
            statuses[target_name] = {
                "status": "in_sync",
                "sha256": current_hash,
                "size": len(current),
            }

    return {"built_at": manifest.get("built_at"), "statuses": statuses}


def print_status() -> None:
    """Human-readable status display."""
    s = status()
    if s.get("manifest_missing"):
        print("Payload/ has no manifest. Run `mememage payload build` to create one.")
        return

    print(f"Payload built: {s.get('built_at', '?')}")
    print()
    rows = list(s["statuses"].items())
    max_name = max((len(name) for name, _ in rows), default=0)

    for name, info in rows:
        st = info["status"]
        symbol = {"in_sync": "✓", "drifted": "≠",
                  "missing_payload": "✗", "missing_source": "?"}.get(st, " ")
        print(f"  {symbol}  {name:<{max_name}}  {st}")

    drifted = sum(1 for _, i in rows if i["status"] == "drifted")
    missing = sum(1 for _, i in rows if i["status"].startswith("missing"))
    print()
    if drifted or missing:
        print(f"{drifted} drifted, {missing} missing. Run `mememage payload build` to refresh.")
    else:
        print("All artifacts in sync.")


def print_diff() -> None:
    """Show what would change if rebuilt."""
    s = status()
    if s.get("manifest_missing"):
        print("Payload/ has no manifest. Run `mememage payload build` first.")
        return

    any_drift = False
    for name, info in s["statuses"].items():
        st = info["status"]
        if st == "drifted":
            any_drift = True
            print(f"≠ {name}")
            print(f"    Payload: sha={info['payload_hash'][:12]}…  size={info['payload_size']:,}")
            print(f"    Source:  sha={info['source_hash'][:12]}…  size={info['source_size']:,}")
        elif st == "missing_payload":
            any_drift = True
            print(f"✗ {name} — missing in Payload (would be added; size={info['source_size']:,})")
        elif st == "missing_source":
            print(f"? {name} — source unreadable: {info.get('error', 'unknown')}")

    if not any_drift:
        print("No drift. Payload matches sources.")


def inspect(target_name: str) -> None:
    """Show one artifact: metadata + small preview."""
    manifest = _read_manifest()
    if manifest is None:
        print("No manifest. Run `mememage payload build` first.")
        return

    entry = manifest.get("artifacts", {}).get(target_name)
    if not entry:
        print(f"No such artifact: {target_name}")
        print("Available:", ", ".join(sorted(manifest.get("artifacts", {}).keys())))
        return

    target = payload_dir() / target_name
    print(f"Artifact: {target_name}")
    print(f"  Source:  {entry['source']}")
    print(f"  Path:    {target}")
    print(f"  SHA-256: {entry['sha256']}")
    print(f"  Size:    {entry['size']:,} bytes")

    if not target.exists():
        print("\n(File missing on disk; manifest entry is stale.)")
        return

    try:
        text = target.read_text(encoding="utf-8")
        preview = text[:500]
        suffix = "…" if len(text) > 500 else ""
        print(f"\n--- First 500 chars ---\n{preview}{suffix}")
    except UnicodeDecodeError:
        print("\n(binary or non-UTF-8 content; not previewed)")


def get_artifact_bytes(target_name: str) -> bytes:
    """Read an artifact from the active chain's Payload subdir."""
    p = payload_dir() / target_name
    if not p.exists():
        raise FileNotFoundError(
            f"Payload artifact missing: {target_name}. "
            f"Run `mememage payload build` first."
        )
    return p.read_bytes()


def inspect_data(target_name: str) -> dict:
    """API-friendly counterpart to inspect(): returns a dict instead of printing.

    Returns:
        {name, source, sha256, size, exists, preview, preview_truncated, binary}
    Raises KeyError if the artifact isn't in the manifest.
    """
    manifest = _read_manifest()
    if manifest is None:
        raise FileNotFoundError("No manifest. Run `mememage payload build` first.")
    entry = manifest.get("artifacts", {}).get(target_name)
    if entry is None:
        raise KeyError(target_name)

    target = payload_dir() / target_name
    out = {
        "name": target_name,
        "source": entry.get("source", ""),
        "sha256": entry.get("sha256", ""),
        "size": entry.get("size", 0),
        "exists": target.exists(),
        "preview": "",
        "preview_truncated": False,
        "binary": False,
    }
    if not target.exists():
        return out

    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        out["binary"] = True
        return out

    LIMIT = 2000
    if len(text) > LIMIT:
        out["preview"] = text[:LIMIT]
        out["preview_truncated"] = True
    else:
        out["preview"] = text
    return out


# Entries map 1:1 to Payload artifact filenames: ``Payload/<entry_name>``.
# The previous extension-and-subdirectory naming scheme was removed when
# entries became typeless. site_pack.seal() reads via get_artifact_bytes(name).


def require_ready() -> None:
    """Raise if Payload is missing or any artifact is missing in Payload.

    Drift is allowed (the user may have intentionally pinned older content);
    only outright absence blocks the seal.
    """
    s = status()
    if s.get("manifest_missing"):
        raise RuntimeError(
            "Payload/ has no manifest. Run `mememage payload build` before sealing."
        )
    missing = [n for n, info in s["statuses"].items() if info["status"] == "missing_payload"]
    if missing:
        raise RuntimeError(
            "Payload is missing required artifacts: "
            + ", ".join(missing)
            + ". Run `mememage payload build`."
        )
