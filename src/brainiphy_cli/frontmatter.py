"""Write normalized Markdown+frontmatter files that graphify ingests the same
way it treats its own `graphify add` output — same YAML shape, same escaping.

Naming is keyed by a stable slug of the remote record's ID (not a random/
timestamped name), so re-running a connector overwrites the same file in
place instead of accumulating duplicate nodes in the graph.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


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
        elif cp < 0x20 or cp == 0x7F:
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
        lines.append(f'{key}: "{yaml_str(value)}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(body)

    path = out_dir / f"{slugify(record_id)}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
