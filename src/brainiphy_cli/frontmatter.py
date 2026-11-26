"""Write normalized Markdown+frontmatter files that graphify ingests the same
way it treats its own `graphify add` output — same YAML shape, same escaping.

Naming is keyed by a stable slug of the remote record's ID (not a random/
timestamped name), so re-running a connector overwrites the same file in
place instead of accumulating duplicate nodes in the graph.
"""
from __future__ import annotations

import re
import hashlib
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

from brainiphy_cli.storage import atomic_write, contained_path


def yaml_str(s: object) -> str:
    """Escape a value for a YAML double-quoted scalar (mirrors graphify's own
    ingest.py _yaml_str — hostile field values, e.g. a CRM record title,
    must not be able to break out of the quoted scalar and inject keys)."""
    if s is None:
        return ""
    out: list[str] = []
    for ch in str(s):
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\0")
        elif cp == 0x2028:
            out.append("\\L")
        elif cp == 0x2029:
            out.append("\\P")
        elif cp < 0x20 or 0x7F <= cp < 0xA0:
            out.append(f"\\x{cp:02x}")
        else:
            out.append(ch)
    return "".join(out)


def slugify(record_id: str, max_len: int = 80) -> str:
    """Turn a remote record ID into a stable, filesystem-safe slug."""
    normalized = unicodedata.normalize("NFKD", str(record_id))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\-]", "_", ascii_only).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:max_len] or "item"


def record_filename(record_id: str) -> str:
    identity = str(record_id)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"{slugify(record_id, 40)}-{digest}.md"


def read_metadata(path: Path) -> dict:
    """Read only the frontmatter; never interpret a source document as code."""
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return {}
        data = yaml.safe_load(text.split("\n---", 1)[0][4:])
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def preserve_capture_time(content: str, previous: str) -> str:
    """Reuse the capture time only when all other bytes are identical."""
    stamp = re.search(r"^captured_at: .*$", previous, re.MULTILINE)
    if stamp:
        candidate = re.sub(r"^captured_at: .*$", lambda _: stamp.group(), content,
                           count=1, flags=re.MULTILINE)
        if candidate == previous:
            return previous
    return content


def write_record(
    out_dir: Path,
    *,
    record_id: str,
    title: str,
    body: str,
    source_system: str,
    contributor: str = "unknown",
    extra_fields: dict[str, object] | None = None,
) -> Path:
    """Write one normalized record as Markdown with YAML frontmatter.

    Returns the path written. Overwrites any prior file for the same
    record_id (idempotent sync — same slug in, same file out).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    lines = [
        "---",
        f'source_id: "{yaml_str(record_id)}"',
        f'source_system: "{yaml_str(source_system)}"',
        f'title: "{yaml_str(title)}"',
        f"captured_at: {now}",
        f'contributor: "{yaml_str(contributor)}"',
    ]
    for key, value in (extra_fields or {}).items():
        if key in {"source_id", "source_system", "title", "captured_at", "contributor"}:
            continue
        key = str(key)
        plain = (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                 and key.lower() not in {"y", "n", "yes", "no", "true", "false", "on", "off", "null"})
        safe_key = key if plain else f'"{yaml_str(key)}"'
        lines.append(f'{safe_key}: "{yaml_str(value)}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(body)

    path = contained_path(out_dir, record_filename(record_id))
    legacy = contained_path(out_dir, f"{slugify(record_id)}.md")
    metadata = read_metadata(legacy)
    owns_legacy = (str(metadata.get("source_id")) == str(record_id)
                   and metadata.get("source_system") == source_system)
    previous = path if path.exists() else legacy if owns_legacy else None
    content = "\n".join(lines) + "\n"
    if previous is not None:
        old = previous.read_text(encoding="utf-8")
        # The capture time describes the last content change, not every poll.
        content = preserve_capture_time(content, old)
        if content == old and previous == path:
            return path
    atomic_write(path, content)
    if owns_legacy and legacy != path:
        legacy.unlink()
    return path
