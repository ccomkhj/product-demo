"""Post drawing: the SVGs a post plan overlays or renders (stdlib only, no ffmpeg).

Cards, callouts, a text watermark and the contact sheet's labelled grid. The runner turns
them into PNGs with rsvg-convert (ADR-0010).
"""

import math
import textwrap
from xml.sax.saxutils import escape as xml_escape

FONT = "Helvetica, Arial, sans-serif"
CALLOUT_COLOR = "#ff3b30"


def card_svg(card, width, height, logo_href=None):
    """One card as an SVG: its background and its lines centred, the logo above them."""
    sizes = {"title": 0.08, "subtitle": 0.045, "text": 0.065, "url": 0.045}
    blocks = []  # (height, svg drawn from y=0)
    if logo_href:
        logo_h = round(height * 0.2)
        blocks.append(
            (
                logo_h,
                f'<image x="{width * 0.2:.0f}" y="0" width="{width * 0.6:.0f}" '
                f'height="{logo_h}" href="{xml_escape(logo_href)}" '
                f'preserveAspectRatio="xMidYMid meet"/>',
            )
        )
    for text, role in card["lines"]:
        font = max(12, round(height * sizes[role]))
        rows = textwrap.wrap(text, max(10, int(width * 0.85 / (font * 0.55)))) or [""]
        weight = ' font-weight="700"' if role in ("title", "text") else ""
        tspans = "".join(
            f'<tspan x="{width / 2:.0f}" y="{font + row * font * 1.25:.0f}">'
            f"{xml_escape(line)}</tspan>"
            for row, line in enumerate(rows)
        )
        blocks.append(
            (
                round(font * (1.25 * len(rows) + 0.25)),
                f'<text font-family="{FONT}" font-size="{font}"'
                f'{weight} text-anchor="middle" fill="{card["color"]}">{tspans}</text>',
            )
        )
    gap = round(height * 0.03)
    y = (height - sum(h for h, _ in blocks) - gap * (len(blocks) - 1)) / 2
    body = []
    for block_h, block in blocks:
        body.append(f'<g transform="translate(0 {y:.0f})">{block}</g>')
        y += block_h + gap
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f'<rect width="100%" height="100%" fill="{card["background"]}"/>{"".join(body)}</svg>'
    )


def callout_svg(callout, width, height):
    """A callout drawn on a transparent ``width`` x ``height`` SVG: an arrow from ``from``
    to ``to``, or ``text`` in a box at ``position`` (its top-left corner).

    Returns (svg, box): box is (x0, y0, x1, y1), the pixels it covers.
    """
    color = callout.get("color", CALLOUT_COLOR)
    if callout["type"] == "arrow":
        (x0, y0), (x1, y1) = callout["from"], callout["to"]
        stroke = max(4, round(height * 0.008))
        head = stroke * 5
        length = math.hypot(x1 - x0, y1 - y0) or 1
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        bx, by = x1 - ux * head, y1 - uy * head  # base of the head
        wing = [
            (bx - uy * head * 0.6, by + ux * head * 0.6),
            (bx + uy * head * 0.6, by - ux * head * 0.6),
        ]
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in [(x1, y1), *wing])
        body = (
            f'<g stroke-linecap="round" stroke-linejoin="round">'
            f'<line x1="{x0}" y1="{y0}" x2="{bx:.1f}" y2="{by:.1f}" stroke="white" '
            f'stroke-width="{stroke + 4}"/><polygon points="{points}" fill="white" '
            f'stroke="white" stroke-width="4"/>'
            f'<line x1="{x0}" y1="{y0}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{color}" '
            f'stroke-width="{stroke}"/><polygon points="{points}" fill="{color}"/></g>'
        )
        box = (min(x0, x1) - head, min(y0, y1) - head, max(x0, x1) + head, max(y0, y1) + head)
    else:
        x, y = callout["position"]
        font = max(14, round(height * 0.035))
        pad = font // 2
        box_w, box_h = (
            round(len(callout["text"]) * font * 0.6) + 2 * pad,
            round(font * 1.3) + 2 * pad,
        )
        body = (
            f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="6" '
            f'fill="#141418" fill-opacity="0.88" stroke="{color}" stroke-width="3"/>'
            f'<text x="{x + pad}" y="{y + pad + font}" font-family="{FONT}" '
            f'font-size="{font}" font-weight="600" fill="white">'
            f"{xml_escape(callout['text'])}</text>"
        )
        box = (x, y, x + box_w, y + box_h)
    return _svg(body, width, height), box


def _svg(body, width, height):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">{body}</svg>'


def watermark_svg(text, corner, font, opacity, margin, width, height):
    """``text`` as a ``font``-pixel watermark in ``corner``, ``margin`` pixels from the
    edges, on a transparent ``width`` x ``height`` SVG."""
    right = corner.endswith("right")
    x = width - margin if right else margin
    y = height - margin - round(font * 0.25) if corner.startswith("bottom") else margin + font
    return _svg(
        f'<text x="{x}" y="{y}" text-anchor="{"end" if right else "start"}" '
        f'font-family="{FONT}" font-size="{font}" font-weight="700" fill="white" '
        f'stroke="black" stroke-width="{max(1, font // 16)}" opacity="{opacity:g}">'
        f"{xml_escape(text)}</text>",
        width,
        height,
    )


def contact_sheet_svg(frames, columns, width, height):
    """A grid of frames, each labelled with its time: ``frames`` is [(href, time_s)]."""
    label_h = max(18, height // 8)
    pad = 8
    rows = math.ceil(len(frames) / columns)
    cell_w, cell_h = width + pad, height + label_h + pad
    parts = []
    for index, (href, time_s) in enumerate(frames):
        x, y = pad + (index % columns) * cell_w, pad + (index // columns) * cell_h
        parts.append(
            f'<image x="{x}" y="{y}" width="{width}" height="{height}" href="{href}"/>'
            f'<text x="{x + 4}" y="{y + height + label_h * 0.8:.0f}" '
            f'font-family="{FONT}" font-size="{label_h * 0.8:.0f}" '
            f'fill="white">{time_s:.2f}s</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{pad + columns * cell_w}" '
        f'height="{pad + rows * cell_h}"><rect width="100%" height="100%" fill="#202020"/>'
        f"{''.join(parts)}</svg>"
    )
