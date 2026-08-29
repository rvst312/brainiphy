#!/usr/bin/env python3
"""Regenerate the SVG figures the README embeds, into docs/.

Run it after changing a screen the figures depict:

    python3 scripts/gen-docs-images.py

Why generated rather than hand-drawn: the terminal mockups mirror real
`brain` output, so when a screen changes the figure has to change with it,
and editing a wall of <text> elements by hand is how a figure quietly stops
matching the tool. Every run is emitted with an explicit textLength so the
columns line up whatever monospace face the reader's browser falls back to
-- GitHub renders these through <img>, where no webfont is available.
"""
from __future__ import annotations

import sys
from pathlib import Path

FS = 13.5   # terminal font size
CW = 8.1    # monospace cell width (0.6 em, forced by textLength)
LH = 22.0   # terminal line height
PAD_X = 26.0
COLS = 72

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
SANS = "ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

# The Rich theme in ui.py, rendered for a dark terminal. Keep in step with
# THEME there: these are the colors a reader sees when they run the tool.
BG, CARD, BORDER = "#111419", "#151922", "#2A303C"
INK, DIM, HEAD = "#C9CEDA", "#6B7482", "#E8EBF1"
OK, CYAN, WARN, PURPLE = "#6FD08C", "#5CC8D8", "#E0B44A", "#B07CF6"

STYLE = {
    "txt": (INK, "400"), "head": (HEAD, "600"), "dim": (DIM, "400"),
    "ok": (OK, "600"), "cyan": (CYAN, "400"), "warn": (WARN, "400"),
    "purple": (PURPLE, "400"), "sel": (HEAD, "600"),
}
ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def esc(s: str) -> str:
    return "".join(ESC.get(c, c) for c in s)


def mono(x: float, y: float, text: str, fill: str, *, size: float = FS,
         weight: str = "400", cw: float | None = None) -> str:
    cw = cw if cw is not None else size * 0.6
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-family="{MONO}" '
            f'font-size="{size}" font-weight="{weight}" '
            f'textLength="{len(text) * cw:.1f}" lengthAdjust="spacing">{esc(text)}</text>')


def sans(x: float, y: float, text: str, fill: str, *, size: float = 13,
         weight: str = "400", anchor: str = "start", spacing: str = "") -> str:
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-family="{SANS}" '
            f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"{ls}>'
            f'{esc(text)}</text>')


def _run(col: float, text: str, cls: str, y: float, x0: float) -> str:
    fill, weight = STYLE[cls]
    return mono(x0 + col * CW, y, text, fill, weight=weight, cw=CW)


def write(path: Path, parts: list[str], w: float, h: float) -> None:
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {path}  {w:.0f}x{h:.0f}")


# --------------------------------------------------------------- screens --

def panel(path: Path, title: str, subtitle: str, lines, *, sub_right: bool = False) -> None:
    """An app screen: `ui.app_panel` drawn as the card itself."""
    w = PAD_X * 2 + COLS * CW
    top = bot = 34.0
    h = top + len(lines) * LH + bot
    x0 = PAD_X + 8

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
        f'viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-label="{esc(title)} — a brainiphy screen">',
        f'<rect width="{w:.0f}" height="{h:.0f}" rx="10" fill="{BG}"/>',
        f'<rect x="10.5" y="10.5" width="{w - 21:.1f}" height="{h - 21:.1f}" rx="8" '
        f'fill="{CARD}" stroke="{BORDER}" stroke-width="1"/>',
        f'<rect x="26" y="4" width="{(len(title) + 2) * CW:.1f}" height="13" fill="{CARD}"/>',
        mono(26 + CW, 15.5, title, PURPLE, weight="600", cw=CW),
    ]
    sw = (len(subtitle) + 2) * CW
    sx = w - 26 - sw if sub_right else 26.0
    parts += [
        f'<rect x="{sx:.1f}" y="{h - 17:.1f}" width="{sw:.1f}" height="13" fill="{CARD}"/>',
        mono(sx + CW, h - 6.5, subtitle, DIM, cw=CW),
    ]
    for i, line in enumerate(lines):
        y = top + i * LH + FS
        parts += [_run(col, text, cls, y, x0) for col, text, cls in line]
    parts.append("</svg>")
    write(path, parts, w, h)


def terminal(path: Path, command: str, lines, label: str) -> None:
    """A named command and the output it prints."""
    w = PAD_X * 2 + COLS * CW
    bar = 40.0
    h = bar + 18 + len(lines) * LH + 22

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
        f'viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-label="{esc(label)}">',
        f'<rect width="{w:.0f}" height="{h:.0f}" rx="10" fill="{CARD}" '
        f'stroke="{BORDER}" stroke-width="1"/>',
        f'<path d="M0 {bar}h{w:.0f}" stroke="{BORDER}" stroke-width="1"/>',
    ]
    parts += [f'<circle cx="{cx}" cy="{bar / 2:.0f}" r="4.5" fill="#333A47"/>' for cx in (22, 40, 58)]
    parts.append(mono(82, bar / 2 + 4.5, command, DIM, size=12.5, cw=7.5))
    for i, line in enumerate(lines):
        y = bar + 18 + i * LH + FS
        parts += [_run(col, text, cls, y, PAD_X) for col, text, cls in line]
    parts.append("</svg>")
    write(path, parts, w, h)


# --------------------------------------------------------------- diagram --

def card(x: float, y: float, w: float, h: float, *, stroke: str = BORDER,
         fill: str = CARD, dash: str = "") -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"{d}/>')


def arrow(x1: float, y: float, x2: float) -> str:
    return (f'<path d="M{x1:.1f} {y:.1f}H{x2:.1f}" stroke="{DIM}" stroke-width="1.5" '
            f'marker-end="url(#a)"/>')


def pipeline(path: Path) -> None:
    W, H = 980, 392
    ax, aw = 24.0, 196.0
    bx, bw = 252.0, 248.0
    cx_, cw_ = 532.0, 196.0
    dx, dw = 760.0, 196.0
    head_y, top = 32.0, 50.0

    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="How brainiphy turns scattered '
        f'business data into a graph Claude can query">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto"><path d="M0 0 10 5 0 10z" fill="{DIM}"/></marker>'
        '<marker id="p" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto"><path d="M0 0 10 5 0 10z" fill="{PURPLE}"/></marker></defs>',
        f'<rect width="{W}" height="{H}" rx="12" fill="{BG}"/>',
    ]

    for x, w, label in ((ax, aw, "YOUR DATA"), (bx, bw, "CONNECTORS"),
                        (cx_, cw_, "THE GRAPH"), (dx, dw, "CLAUDE")):
        p.append(sans(x + w / 2, head_y, label, DIM, size=10.5, weight="600",
                      anchor="middle", spacing="1.4"))

    # A — the four kinds of source the app offers, with what each still costs you.
    for i, (name, badge, tone) in enumerate((
            ("local folder", "ready", OK), ("preset (CRM)", "ready", OK),
            ("http api", "needs code", WARN), ("custom", "needs code", WARN))):
        y = top + i * 44
        p += [card(ax, y, aw, 36),
              mono(ax + 14, y + 23, name, CYAN, size=12.5),
              sans(ax + aw - 14, y + 23, badge, tone, size=10.5, anchor="end")]

    # B — one script per source, and the contract it satisfies.
    p += [card(bx, top, bw, 168, stroke="#3A3050"),
          mono(bx + 16, top + 28, "connectors/<name>/sync.py", HEAD, size=12.5, weight="600"),
          f'<path d="M{bx + 16} {top + 44}H{bx + bw - 16}" stroke="{BORDER}"/>']
    for i, line in enumerate(("one executable script per source",
                              "writes Markdown + YAML frontmatter",
                              "file name = stable slug of the",
                              "remote id, so re-runs overwrite",
                              "in place instead of duplicating")):
        p.append(sans(bx + 16, top + 66 + i * 19, line, INK if i < 2 else DIM, size=12))
    p += [card(bx, top + 182, bw, 34, stroke="#3A3050"),
          sans(bx + 16, top + 204, "credentials", DIM, size=12),
          mono(bx + 100, top + 204, "macOS Keychain", PURPLE, size=12)]

    # C — raw corpus, the indexing pass, the artifact it produces.
    p.append(card(cx_, top, cw_, 168))
    for i, (label, text, tone) in enumerate((
            ("", "raw/<name>/*.md", CYAN),
            ("indexed by", "graphify extract", HEAD),
            ("", "graphify-out/graph.json", OK))):
        y = top + 34 + i * 52
        if label:
            p.append(sans(cx_ + cw_ / 2, y - 22, label, DIM, size=10.5, anchor="middle"))
        p.append(mono(cx_ + cw_ / 2 - len(text) * 3.45, y, text, tone, size=11.5))
        if i < 2:
            p.append(f'<path d="M{cx_ + cw_ / 2} {y + 10}v18" stroke="{DIM}" '
                     f'stroke-width="1.5" marker-end="url(#a)"/>')

    # D — the two ways the graph reaches Claude.
    for i, (name, how) in enumerate((("Claude Code", "CLAUDE.md + hooks"),
                                     ("Claude Desktop", "graphify-mcp server"))):
        y = top + i * 92
        p += [card(dx, y, dw, 76, stroke="#3A3050"),
              sans(dx + 16, y + 30, name, HEAD, size=13.5, weight="600"),
              mono(dx + 16, y + 52, how, PURPLE, size=11.5)]

    # A collects into one spine before it enters the connectors box; the graph
    # forks into both ways of reaching Claude.
    spine = ax + aw + 14
    p.append(f'<path d="M{spine} {top + 18}V{top + 150}" stroke="{DIM}" stroke-width="1.5"/>')
    for i in range(4):
        p.append(f'<path d="M{ax + aw} {top + 18 + i * 44}H{spine}" stroke="{DIM}" stroke-width="1.5"/>')
    p.append(arrow(spine, top + 84, bx - 8))
    p.append(arrow(bx + bw + 8, top + 84, cx_ - 8))
    fork = dx - 26
    p.append(f'<path d="M{cx_ + cw_ + 8} {top + 84}H{fork}" stroke="{DIM}" stroke-width="1.5"/>')
    p.append(f'<path d="M{fork} {top + 38}V{top + 130}" stroke="{DIM}" stroke-width="1.5"/>')
    for y in (top + 38, top + 130):
        p.append(arrow(fork, y, dx - 8))

    # The band: what makes it stay true after the first build.
    by = 294.0
    p += [card(ax, by, W - 48, 74, stroke=PURPLE, dash="5 5", fill="#171423"),
          f'<path d="M{ax + 30} {by + 30}a13 13 0 1 0 13-13" stroke="{PURPLE}" '
          f'stroke-width="1.8" fill="none" marker-end="url(#p)"/>',
          mono(ax + 66, by + 30, "brain sync", PURPLE, size=13.5, weight="600"),
          sans(ax + 66, by + 52, "runs only the connectors whose interval has elapsed, and "
               "rebuilds the graph only if one of them actually ran", DIM, size=12),
          mono(W - 40, by + 30, "brain schedule", HEAD, size=12.5),
          sans(W - 40, by + 52, "a LaunchAgent that does it for you", DIM, size=12)]
    # right-align that pair by hand: mono() anchors at start, so shift it back
    p[-2] = mono(W - 40 - 14 * 7.5, by + 30, "brain schedule", HEAD, size=12.5)
    p[-1] = sans(W - 40, by + 52, "a LaunchAgent that does it for you", DIM, size=12, anchor="end")
    p.append("</svg>")
    write(path, p, W, H)


# ------------------------------------------------------------------ build --

def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    B: list = []

    panel(out / "cli-checklist.svg", "acme", "4/7  ·  ~/clients/acme", [
        B,
        [(4, "✓", "ok"), (6, "1", "dim"), (10, "Install graphify", "txt")],
        [(4, "✓", "ok"), (6, "2", "dim"), (10, "Scaffold the project", "txt")],
        [(4, "✓", "ok"), (6, "3", "dim"), (10, "Add data sources", "txt")],
        [(4, "✓", "ok"), (6, "4", "dim"), (10, "Finish the custom connectors", "txt")],
        B,
        [(2, "❯", "purple"), (4, "○", "dim"), (6, "5", "dim"), (10, "Run the first sync", "sel")],
        [(10, "pull every source in and index it — documents need a", "dim")],
        [(10, "model: an API key, or your Claude Code subscription", "dim")],
        [(10, "graph not built yet", "warn")],
        [(10, "↵ do this now", "purple")],
        B,
        [(4, "○", "dim"), (6, "6", "dim"), (10, "Connect it to Claude", "txt")],
        [(4, "○", "dim"), (6, "7", "dim"), (10, "Keep it in sync", "txt")],
        B,
        [(4, "↑↓ move", "dim"), (14, "↵ run step", "dim"), (27, "v graph", "dim"),
         (37, "t tools", "dim"), (47, "b brains", "dim"), (58, "q quit", "dim")],
    ])

    panel(out / "cli-brains.svg", "your brains", "2 brains", [
        B,
        [(3, "Each brain is a folder with its own sources and its own", "dim")],
        [(3, "graph. Open one to carry on setting it up.", "dim")],
        B,
        [(2, "❯", "purple"), (5, "acme", "sel"), (12, "7/7 set up", "ok")],
        [(7, "2 sources  ·  2431 nodes  ·  synced 2h ago", "dim")],
        [(7, "in ~/clients", "dim")],
        B,
        [(5, "clinica", "cyan"), (15, "4/7 set up", "warn")],
        [(7, "2 sources  ·  graph not built yet  ·  synced never", "dim")],
        [(7, "in ~/clients", "dim")],
        B,
        [(3, "↑↓ move", "dim"), (13, "↵ open", "dim"), (22, "n new", "dim"),
         (30, "a add", "dim"), (38, "v graph", "dim"), (48, "d remove", "dim"),
         (59, "q quit", "dim")],
    ], sub_right=True)

    terminal(out / "cli-status.svg", "$ brain status ~/clients/acme", [
        [(0, "brain status", "head")],
        [(0, "~/clients/acme", "cyan")],
        [(0, "─" * COLS, "dim")],
        [(0, "source", "dim"), (10, "type", "dim"), (24, "ready", "dim"),
         (31, "runs", "dim"), (59, "records", "dim")],
        [(0, "docs", "cyan"), (10, "local folder", "txt"), (24, "yes", "ok"),
         (31, "every 60 min · up to date", "dim"), (59, "1204", "txt")],
        [(0, "hubspot", "cyan"), (10, "http api", "txt"), (24, "yes", "ok"),
         (31, "every 30 min · due now", "warn"), (59, "1227", "txt")],
        B,
        [(0, "✓", "ok"), (2, "graph: 2431 nodes, 5870 edges", "txt"),
         (33, "graphify-out/graph.json", "cyan")],
        [(0, "↳", "purple"), (2, "see it as a picture:", "dim")],
        [(4, "brain view ~/clients/acme", "head")],
        B,
        [(0, "✓", "ok"), (2, "setup complete (7/7 steps)", "txt")],
    ], "brain status: the connectors table, the graph size, and how far the setup got")

    pipeline(out / "how-it-works.svg")
    return 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else root / "docs"))
