#!/usr/bin/env python3
"""Synthesize the per-provider combine lockups in `logos/combine/<id>.svg`.

A combine lockup is the chip's "brand row" artwork: the vendor mark and the
vendor wordmark composed into ONE coordinate system, with the mark shipped in
two theme layers (a color layer for light surfaces, a currentColor layer for
dark ones). The mono tree (`logos/<id>.svg`) stays untouched — it remains the
fallback and compact-face asset; this tree adds the lockup face on top.

Sourcing (same priority ruling as the mono tree, 2026-11-11): the pinned
@lobehub/icons-static-svg package carries the pieces per vendor —
`<mark>.svg` (mono mark), `<mark>-color.svg` (original color artwork),
`<text>-text.svg` (wordmark, path outlines only). The per-provider
`[<id>.combine]` manifest table maps a registry id onto its lobehub names
(e.g. moonshot -> kimi) and is HAND-MAINTAINED except for `sha256` /
`fetched_at`, which this tool owns (same convention as scripts/fetch_logos.py).
`local_mark` / `local_color` / `local_text` (logos/-relative paths) override
the network fetch for pieces that only exist as committed local files.

Pipeline per provider (threaded, deterministic bytes):
  1. fetch the pieces (a missing `-color` degrades to a tinted mono mark;
     a missing `-text` SKIPS the provider — a lockup without a wordmark is
     not synthesized, the gap stays visible in the run report);
  2. normalize every piece through fetch_logos' screening (active content,
     external references, style attributes and metadata stripped);
  3. measure each piece's ink box (fontTools parses the SVG path data into a
     bounds pen — no rasterization, no font files involved);
  4. compose: one lockup unit = the mark ink height (the box is exactly
     `0 0 W 1` and the mark box fills the full unit height — every layer
     box stays inside the canvas, validate_composed enforces it); the
     wordmark's measured ink band maps to 1/MARK_HEIGHT of the box,
     vertically centered (the mark stands MARK_HEIGHT cap-bands tall —
     the consumer skins' proportion); the mark↔word gap is MARK_GAP cap
     units; an over-wide mark keeps its aspect and shrinks below the full
     height (MAX_MARK_WIDTH clamp — never distorted);
  5. emit the layers (`pr-combine-mark-color`, `pr-combine-mark-mono`,
     `pr-combine-text`), each stamped with its placed box in
     `data-pr-box="x y w h"` so consumers can re-crop a single layer without
     re-measuring; the text layer also carries `data-combine-word`;
  6. mechanical validation (no active content, no external references,
     viewBox pinned, paint allowlist, byte cap);
  7. write the file, refresh the manifest hash fields.

Layer policy (the readability contract, aligned with the consumer skins):
  * with color artwork: the SINGLE most-carried flat fill (the "main" fill)
    is contrast-checked against the LIGHT FABRIC (#ffffff); below
    ART_MIN_CONTRAST (1.5 — effectively invisible) it is replaced with
    currentColor. Gradient paints are kept
    verbatim — a gradient's own light-to-dark span carries the shape's
    readability, and rewriting one would erase multi-color identities (the
    Gemini spark). Knockout accents inside color artwork stay untouched:
    only the main flat fill is ever rewritten.
  * without color artwork: the color layer is the mono mark tinted with the
    brand hex (manifest `tint` override, else the provider `brand_color`) —
    but only when the tint itself clears TINT_MIN_CONTRAST (3.0) on the
    light fabric
    (a tint that fails would ship an invisible mark); otherwise the color
    layer is dropped and the mono layer doubles as the color layer (it then
    carries BOTH classes, mirroring the consumer skins' contract).
  * the mono layer and the text layer always carry currentColor and follow
    the consumer's theme text.

Determinism: no timestamps inside the SVG, fixed attribute insertion order,
stable float formatting — a re-run over unchanged sources is byte-identical,
so the manifest hash only moves when the library moves.

Dependencies: fontTools (measuring only). Install it into a project venv,
NOT the system interpreter:

    python3 -m venv .venv-combine && .venv-combine/bin/pip install "fonttools>=4.43"
    .venv-combine/bin/python scripts/fetch_logos_combines.py

Usage:
    python3 scripts/fetch_logos_combines.py                # fetch + compose (network)
    python3 scripts/fetch_logos_combines.py --only deepseek
    python3 scripts/fetch_logos_combines.py --check        # offline: hashes + invariants

Exit codes: 0 ok / 1 a fetch, a compose or a check failed / 2 layout error.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import threading
import tomllib  # noqa: F401 — manifest parsing rides on fetch_logos; kept for symmetry
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_logos import (  # noqa: E402
    LOGOS_TOP,
    MAX_SVG_BYTES,
    MAX_TOTAL_BYTES,
    LogoError,
    load_manifest,
    rewrite_manifest,
    sha256_file,
)

# fontTools is a measuring dependency, not a hard import: `--check` works
# without it, so the guidance fires only when a compose actually needs it.
try:
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.svgLib.path import parse_path

    HAS_FONTTOOLS = True
except ModuleNotFoundError:  # pragma: no cover - exercised via --check
    HAS_FONTTOOLS = False

COMBINE_TOP = "combine"

LOBEHUB_PACKAGE = "@lobehub/icons-static-svg"
LOBEHUB_PIN = "1.102.0"
LOBEHUB_URL = "https://unpkg.com/{package}@{pin}/icons/{name}.svg"

# ── the composition constants (cap-band units) ──────────────────────────────
# The lockup box is 1 unit TALL and the MARK BOX fills the full height, so
# one lockup unit equals one mark height (the viewBox is pinned to
# `0 0 W 1` and every layer box stays inside it). The wordmark's measured
# ink band is 1/MARK_HEIGHT tall and vertically centered; the mark↔word gap
# is MARK_GAP cap-bands. MAX_MARK_WIDTH is the over-width constraint: a mark
# wider than the box is tall (aspect > 1 in lockup units) keeps its aspect
# and shrinks below the full height — never distorted.
MARK_HEIGHT = 1.45
MARK_GAP = 0.3
MAX_MARK_WIDTH = 1.0  # lockup units — the over-wide clamp (aspect > 1)
MAX_LOCKUP_WIDTH = 12.0  # sanity cap — a lockup wider than this is refused

# The readability fabrics: the color layer is validated against the LIGHT
# surface it will be displayed on (the dark theme displays the mono layer).
# Two floors with different jobs (claude-style skins' intent): the color
# artwork's MAIN fill only flips to currentColor when it is effectively
# invisible (ART_MIN_CONTRAST — brand hues like perplexity's teal at 2.4
# survive), while a mono-mark TINT must clear the stricter bar because the
# tint IS the whole layer (TINT_MIN_CONTRAST — a failing tint would ship an
# invisible mark).
ART_MIN_CONTRAST = 1.5
TINT_MIN_CONTRAST = 3.0
LIGHT_FABRIC = (255, 255, 255)

MAX_WORKERS = 6

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Class/attribute vocabulary of the composed file — the mechanical validator
# pins these so a composition bug cannot ship a layer the consumer cannot see.
MARK_COLOR_CLASS = "pr-combine-mark-color"
MARK_MONO_CLASS = "pr-combine-mark-mono"
TEXT_CLASS = "pr-combine-text"
BOX_ATTR = "data-pr-box"
WORD_ATTR = "data-combine-word"

FORBIDDEN_ATTR = re.compile(r"^on[a-z]+$", re.IGNORECASE)
EXTERNAL_REF = re.compile(r"(?:https?:)?//")
SCRIPT_SCHEMES = ("javascript:", "vbscript:", "data:")
URL_PROBE_STRIP = re.compile(r"[\x00-\x20]")
FRAGMENT_URL = re.compile(r"^url\(#[^)]*\)$", re.IGNORECASE)
URL_CALL = re.compile(r"url\(", re.IGNORECASE)
NUMERIC = re.compile(r"^-?[0-9]*\.?[0-9]+$")

# Paint allowlist for the composed file: theme-following ink, opaque brand
# hex, internal gradient references, and explicit transparency. Anything
# else (named colors, rgb() functions) is refused — the composer resolves
# every color it needs to reason about contrast, so an unresolved color in
# the output would mean an unmeasured paint shipped.
PAINT_ALLOWED = re.compile(
    r"^(currentColor|none|(?:#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6})|url\(#[^)]*\))$"
)

# A minimal named-color table: lobehub color artwork uses plain SVG named
# colors occasionally (mistral ships `gold`). Anything outside the table
# fails loudly — an unmeasurable paint must not sail through the contrast
# contract as a silent unknown.
NAMED_COLORS = {
    "black": "#000000", "silver": "#c0c0c0", "gray": "#808080", "white": "#ffffff",
    "maroon": "#800000", "red": "#ff0000", "purple": "#800080", "fuchsia": "#ff00ff",
    "green": "#008000", "lime": "#00ff00", "olive": "#808000", "yellow": "#ffff00",
    "navy": "#000080", "blue": "#0000ff", "teal": "#008080", "aqua": "#00ffff",
    "gold": "#ffd700", "orange": "#ffa500", "pink": "#ffc0cb", "brown": "#a52a2a",
}


def require_fonttools() -> None:
    if not HAS_FONTTOOLS:
        print(
            "fontTools is required to measure the wordmark/mark ink boxes.\n"
            "Install it into a project venv (not the system interpreter):\n"
            '  python3 -m venv .venv-combine && .venv-combine/bin/pip install "fonttools>=4.43"\n'
            "  .venv-combine/bin/python scripts/fetch_logos_combines.py",
            file=sys.stderr,
        )
        raise SystemExit(2)


# ── colors ───────────────────────────────────────────────────────────────────


def parse_paint(value: str) -> tuple[str, tuple[int, int, int] | None]:
    """Classify one paint value -> (kind, rgb). kind: hex|named|currentColor|
    none|url. Raises on anything unclassifiable (an unmeasured paint would
    silently bypass the contrast contract)."""
    v = value.strip()
    low = v.lower()
    if low == "currentcolor":
        return "currentColor", None
    if low == "none":
        return "none", None
    if re.fullmatch(r"url\(#\s*[^)]*\)", low):
        return "url", None
    if low.startswith("#"):
        hexpart = v[1:]
        if re.fullmatch(r"[0-9a-fA-F]{3}", hexpart):
            hexpart = "".join(ch * 2 for ch in hexpart)
        if not re.fullmatch(r"[0-9a-fA-F]{6}", hexpart):
            raise LogoError(f"unsupported color {value!r}")
        h = hexpart.lower()
        return "hex", (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if low in NAMED_COLORS:
        return parse_paint(NAMED_COLORS[low])
    raise LogoError(f"unsupported color {value!r} — extend NAMED_COLORS or normalize upstream")


def _channel(c: int) -> float:
    v = c / 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[int, int, int]) -> float:
    return 0.2126 * _channel(rgb[0]) + 0.7152 * _channel(rgb[1]) + 0.0722 * _channel(rgb[2])


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hex6(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


# ── ink measurement ──────────────────────────────────────────────────────────

Bounds = tuple[float, float, float, float]  # x0, y0, x1, y1


def _num(el: ET.Element, attr: str, default: float = 0.0) -> float:
    v = el.get(attr)
    if v is None:
        return default
    if not NUMERIC.match(v.strip()):
        raise LogoError(f"<{el.tag.rsplit('}', 1)[-1]}> {attr}={v!r} is not a plain number")
    return float(v)


def _points(el: ET.Element) -> list[tuple[float, float]]:
    raw = el.get("points", "")
    nums: list[float] = []
    for token in raw.replace(",", " ").split():
        if not NUMERIC.match(token):
            raise LogoError(f"<{el.tag.rsplit('}', 1)[-1]}> points carry {token!r}")
        nums.append(float(token))
    if len(nums) % 2:
        raise LogoError(f"<{el.tag.rsplit('}', 1)[-1]}> points list is not paired")
    return list(zip(nums[::2], nums[1::2]))


class _Matrix:
    """A 2×3 affine transform. Only axis-aligned members are accepted: a
    rotation or skew would turn the exact box arithmetic below into an
    approximation, and the pinned sources carry none."""

    def __init__(self, a: float = 1.0, b: float = 0.0, c: float = 0.0,
                 d: float = 1.0, e: float = 0.0, f: float = 0.0) -> None:
        if abs(b) > 1e-9 or abs(c) > 1e-9:
            raise LogoError("rotation/skew transforms are not supported in mark sources")
        self.a, self.d, self.e, self.f = a, d, e, f

    def __mul__(self, other: "_Matrix") -> "_Matrix":
        # self ∘ other (other applied first — SVG nesting order)
        return _Matrix(
            self.a * other.a, 0.0, 0.0, self.d * other.d,
            self.a * other.e + self.e, self.d * other.f + self.f,
        )

    def box(self, bounds: Bounds) -> Bounds:
        x0, y0, x1, y1 = bounds
        ax0, ax1 = sorted((self.a * x0 + self.e, self.a * x1 + self.e))
        ay0, ay1 = sorted((self.d * y0 + self.f, self.d * y1 + self.f))
        return (ax0, ay0, ax1, ay1)


_IDENTITY = _Matrix()
_TRANSFORM_PART = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")


def parse_transform(text: str) -> _Matrix:
    result = _IDENTITY
    for name, args in _TRANSFORM_PART.findall(text):
        nums = [float(p) for p in re.split(r"[\s,]+", args.strip()) if p]
        if name == "translate":
            result = result * _Matrix(e=nums[0], f=nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale":
            sy = nums[1] if len(nums) > 1 else nums[0]
            result = result * _Matrix(a=nums[0], d=sy)
        elif name == "matrix":
            if len(nums) != 6:
                raise LogoError("matrix transform needs six numbers")
            result = result * _Matrix(*nums)
        else:
            raise LogoError(f"unsupported transform {name!r} in mark sources")
    return result


def _path_bounds(d: str) -> Bounds | None:
    require_fonttools()
    pen = BoundsPen(None)
    try:
        parse_path(d, pen)
    except Exception as exc:  # noqa: BLE001 — surface as a policy failure
        raise LogoError(f"unparseable path data: {exc}") from exc
    return pen.bounds


def _shape_bounds(el: ET.Element) -> Bounds | None:
    """Analytic bounds for the drawable shapes (lobehub color artwork ships
    the occasional <circle>). Stroke width is added by the walker."""
    tag = el.tag.rsplit("}", 1)[-1]
    if tag == "path":
        d = el.get("d")
        if not d:
            raise LogoError("<path> without d")
        return _path_bounds(d)
    if tag == "rect":
        x, y = _num(el, "x"), _num(el, "y")
        w, h = _num(el, "width"), _num(el, "height")
        return (x, y, x + w, y + h)
    if tag == "circle":
        cx, cy, r = _num(el, "cx"), _num(el, "cy"), _num(el, "r")
        return (cx - r, cy - r, cx + r, cy + r)
    if tag == "ellipse":
        cx, cy = _num(el, "cx"), _num(el, "cy")
        rx, ry = _num(el, "rx"), _num(el, "ry")
        return (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag == "line":
        return (
            min(_num(el, "x1"), _num(el, "x2")), min(_num(el, "y1"), _num(el, "y2")),
            max(_num(el, "x1"), _num(el, "x2")), max(_num(el, "y1"), _num(el, "y2")),
        )
    if tag in ("polygon", "polyline"):
        pts = _points(el)
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


_DRAWABLE = {"path", "rect", "circle", "ellipse", "line", "polygon", "polyline"}


def ink_bounds(root: ET.Element) -> Bounds:
    """The painted-ink box of a normalized SVG document.

    `defs` subtrees are skipped (never rendered directly). An element only
    contributes when it actually paints: fill defaults to black, an explicit
    fill="none" needs a stroke. Stroke ink expands the box by half the
    (inherited) stroke width."""
    best: Bounds | None = None

    def walk(el: ET.Element, matrix: _Matrix, stroke: str | None, width: float) -> None:
        nonlocal best
        tag = el.tag.rsplit("}", 1)[-1]
        ns = el.tag[1:].split("}", 1)[0] if el.tag.startswith("{") else None
        if ns not in (None, SVG_NS):
            return
        if tag == "defs":
            return
        own_stroke = el.get("stroke")
        eff_stroke = own_stroke if own_stroke is not None else stroke
        eff_width = _num(el, "stroke-width", width)
        own_matrix_attr = el.get("transform")
        eff_matrix = matrix * (parse_transform(own_matrix_attr) if own_matrix_attr else _IDENTITY)

        if tag in _DRAWABLE:
            fill = el.get("fill")
            paints_fill = fill is None or fill.strip().lower() != "none"
            paints_stroke = eff_stroke is not None and eff_stroke.strip().lower() != "none"
            if paints_fill or paints_stroke:
                raw = _shape_bounds(el)
                if raw is not None:
                    box = eff_matrix.box(raw)
                    if paints_stroke:
                        pad = eff_width * abs(eff_matrix.a) / 2
                        box = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
                    if best is None:
                        best = box
                    else:
                        best = (
                            min(best[0], box[0]), min(best[1], box[1]),
                            max(best[2], box[2]), max(best[3], box[3]),
                        )
        for child in el:
            walk(child, eff_matrix, eff_stroke, eff_width)

    walk(root, _IDENTITY, None, 1.0)
    if best is None:
        raise LogoError("no painted ink found in source")
    return best


# ── layer lifting ────────────────────────────────────────────────────────────

_ROOT_DROP_ATTRS = {"viewBox", "width", "height"}


def lift_piece(data: bytes, paint_override: dict[str, str] | None = None) -> tuple[ET.Element, Bounds]:
    """Normalize one source piece and lift it into a <g> for the lockup.

    The source <svg> root's presentation attributes move onto the group (the
    wordmark's fill=currentColor lives there); `paint_override` rewrites
    currentColor paints (used by the tinted-mono color layer)."""
    from fetch_logos import _ns_local  # helper shared with the mono tool

    root = ET.fromstring(data)
    group = ET.Element("g")
    for name, value in root.attrib.items():
        ns, local = _ns_local(name)
        if ns is not None:
            continue  # namespace declarations / foreign bookkeeping never lift
        if local in _ROOT_DROP_ATTRS:
            continue
        if paint_override and value == "currentColor":
            value = paint_override["currentColor"]
        group.set(local, value)
    for child in list(root):
        group.append(child)
    return group, ink_bounds(root)


def flat_fill_counts(group: ET.Element) -> dict[str, int]:
    """Path-count per distinct FLAT fill in a lifted layer. currentColor and
    url() paints are excluded: currentColor already follows the theme, and
    gradients are kept verbatim by policy."""
    counts: dict[str, int] = {}
    for el in group.iter():
        if el.tag.rsplit("}", 1)[-1] not in _DRAWABLE:
            continue
        fill = el.get("fill")
        if fill is None:
            continue  # inherited (currentColor) or resolved upstream
        kind, _ = parse_paint(fill)
        if kind in ("hex", "named"):
            counts[fill] = counts.get(fill, 0) + 1
    return counts


def rewrite_fill(group: ET.Element, old: str, new: str) -> int:
    n = 0
    for el in group.iter():
        if el.get("fill") == old:
            el.set("fill", new)
            n += 1
    return n


def normalize_named_fills(group: ET.Element) -> int:
    """Canonicalize named-color fills to #rrggbb. The composed file's paint
    allowlist accepts hex/currentColor/gradient references only: a named
    color in the output would mean a paint the validator cannot spell."""
    n = 0
    for el in group.iter():
        fill = el.get("fill")
        if fill is None:
            continue
        # parse_paint resolves named colors to their hex KIND already, so the
        # check runs on the raw spelling, not the classification.
        if fill.strip().lower() in NAMED_COLORS:
            _, rgb = parse_paint(fill)
            el.set("fill", hex6(rgb or LIGHT_FABRIC))
            n += 1
    return n


def resolve_colorwork(group: ET.Element) -> str:
    """Apply the readability contract to a lifted color artwork layer, in
    place. Returns a one-line note for the run report.

    Main fill = the most-carried flat fill (ties break to the lexicographically
    smallest spelling — deterministic without artwork knowledge). Below
    ART_MIN_CONTRAST on the light fabric it becomes currentColor; everything else
    (gradients, knockout accents, other brand fills) is untouched."""
    counts = flat_fill_counts(group)
    if not counts:
        return "color artwork (gradients only, kept verbatim)"
    main = min(counts, key=lambda f: (-counts[f], f))
    kind, rgb = parse_paint(main)
    rgb_or_fabric = rgb if rgb is not None else LIGHT_FABRIC
    if contrast(rgb_or_fabric, LIGHT_FABRIC) >= ART_MIN_CONTRAST:
        return f"color artwork (main fill {main} clears contrast {ART_MIN_CONTRAST})"
    flipped = rewrite_fill(group, main, "currentColor")
    if flipped == 0:  # pragma: no cover — counts came from this very tree
        raise LogoError("main fill rewrite touched nothing")
    return (
        f"color artwork (main fill {main} below contrast {ART_MIN_CONTRAST}"
        f" on light fabric — {flipped} path(s) -> currentColor)"
    )


# ── composition ──────────────────────────────────────────────────────────────


def fmt(v: float) -> str:
    """Deterministic float spelling: 6 decimal places, trailing zeros and a
    trailing dot stripped, -0 normalized to 0."""
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        s = "0"
    return s


def _fit_layer(group: ET.Element, source_ink: Bounds,
               box: tuple[float, float, float, float]) -> None:
    """Map the source ink box INTO the placed box (x, y, w, h) — uniform
    scale (contain), centered in the box. The mark layers of one lockup can
    carry slightly different drawings (a -color artwork with extra shapes),
    so each is contain-fitted into the SAME mark box instead of stretched;
    when the inks agree the fit is exact."""
    bx, by, bw, bh = box
    ix0, iy0, ix1, iy1 = source_ink
    ink_w, ink_h = ix1 - ix0, iy1 - iy0
    if ink_w <= 0 or ink_h <= 0:
        raise LogoError("degenerate ink box")
    s = min(bw / ink_w, bh / ink_h)
    matrix = _Matrix(
        a=s, d=s,
        e=bx + (bw - ink_w * s) / 2 - ix0 * s,
        f=by + (bh - ink_h * s) / 2 - iy0 * s,
    )
    existing = group.get("transform")
    if existing:
        matrix = matrix * parse_transform(existing)
    group.set("transform", f"translate({fmt(matrix.e)} {fmt(matrix.f)}) scale({fmt(matrix.a)})")
    group.set(BOX_ATTR, f"{fmt(bx)} {fmt(by)} {fmt(bw)} {fmt(bh)}")


def _place_layer(group: ET.Element, source_ink: Bounds,
                 box: tuple[float, float, float, float]) -> None:
    """Map the source ink box ONTO the placed box exactly (uniform scale).
    Used for the text layer, whose box is derived from its own ink."""
    x, y, w, h = box
    ix0, iy0, ix1, iy1 = source_ink
    ink_w, ink_h = ix1 - ix0, iy1 - iy0
    if ink_w <= 0 or ink_h <= 0:
        raise LogoError("degenerate ink box")
    s = w / ink_w
    if abs(s - h / ink_h) > 1e-9:
        raise LogoError("non-uniform layer scaling is not part of the grammar")
    matrix = _Matrix(a=s, d=s, e=x - ix0 * s, f=y - iy0 * s)
    existing = group.get("transform")
    if existing:
        matrix = matrix * parse_transform(existing)
    group.set("transform", f"translate({fmt(matrix.e)} {fmt(matrix.f)}) scale({fmt(matrix.a)})")
    group.set(BOX_ATTR, f"{fmt(x)} {fmt(y)} {fmt(w)} {fmt(h)}")


def compose_lockup(word: str,
                   mark_mono_group: ET.Element, mark_mono_ink: Bounds,
                   mark_color_group: ET.Element | None, mark_color_ink: Bounds | None,
                   text_group: ET.Element, text_ink: Bounds) -> bytes:
    """Build the lockup document. Geometry: one lockup unit = one mark
    height — the mark box fills the full unit height [0, 1] (its shape
    derives from the UNION of the mark layers' ink boxes; the two mark
    drawings may differ slightly), the text ink band maps to
    1/MARK_HEIGHT, vertically centered, and the gap is MARK_GAP cap units
    (= MARK_GAP / MARK_HEIGHT here). Every layer box therefore stays inside
    the pinned `0 0 W 1` viewBox — validate_composed enforces it."""
    inks = [mark_mono_ink] if mark_color_ink is None else [mark_mono_ink, mark_color_ink]
    ux0 = min(b[0] for b in inks)
    uy0 = min(b[1] for b in inks)
    ux1 = max(b[2] for b in inks)
    uy1 = max(b[3] for b in inks)
    aspect = (ux1 - ux0) / (uy1 - uy0)
    if aspect <= 1.0:
        mark_w, mark_h = aspect, 1.0
    else:
        mark_w, mark_h = MAX_MARK_WIDTH, MAX_MARK_WIDTH / aspect
    gap = MARK_GAP / MARK_HEIGHT
    text_h = 1.0 / MARK_HEIGHT
    text_w = text_h * (text_ink[2] - text_ink[0]) / (text_ink[3] - text_ink[1])
    total_w = mark_w + gap + text_w
    if total_w > MAX_LOCKUP_WIDTH:
        raise LogoError(f"lockup width {total_w:.2f} over the {MAX_LOCKUP_WIDTH} sanity cap")

    _fit_layer(mark_mono_group, mark_mono_ink, (0.0, 0.0, mark_w, mark_h))
    if mark_color_group is not None and mark_color_ink is not None:
        _fit_layer(mark_color_group, mark_color_ink, (0.0, 0.0, mark_w, mark_h))
    _place_layer(text_group, text_ink, (mark_w + gap, (1.0 - text_h) / 2, text_w, text_h))
    text_group.set("class", TEXT_CLASS)
    text_group.set(WORD_ATTR, word)

    root = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": f"0 0 {fmt(total_w)} 1"})
    if mark_color_group is not None:
        mark_color_group.set("class", MARK_COLOR_CLASS)
        root.append(mark_color_group)
    mono_class = MARK_MONO_CLASS if mark_color_group is not None else f"{MARK_MONO_CLASS} {MARK_COLOR_CLASS}"
    mark_mono_group.set("class", mono_class)
    root.append(mark_mono_group)
    root.append(text_group)

    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


# ── composed-file validation ─────────────────────────────────────────────────


def validate_composed(data: bytes, expect_word: str | None = None) -> list[str]:
    """Mechanical invariants for a committed combine file (offline)."""
    problems: list[str] = []
    if len(data) > MAX_SVG_BYTES:
        problems.append(f"file is {len(data)} bytes, over the {MAX_SVG_BYTES} cap")
        return problems
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        problems.append(f"not well-formed XML: {exc}")
        return problems

    def ns_local(tag: str) -> tuple[str | None, str]:
        if tag.startswith("{"):
            ns, local = tag[1:].split("}", 1)
            return ns, local
        return None, tag

    ns, local = ns_local(root.tag)
    if ns not in (None, SVG_NS) or local != "svg":
        problems.append(f"root element is {local!r}, not svg")
        return problems
    viewBox = root.get("viewBox")
    if not viewBox or not re.fullmatch(r"0 0 [0-9.]+ 1", viewBox):
        problems.append(f"viewBox {viewBox!r} is not pinned to '0 0 W 1'")
        return problems
    total_w = float(viewBox.split()[2])

    layers: list[str] = []
    for el in root:
        cls = el.get("class", "")
        layers.extend(c for c in cls.split() if c.startswith("pr-combine-"))
        box = el.get(BOX_ATTR)
        if box is None:
            problems.append(f"layer {cls!r} carries no {BOX_ATTR}")
        else:
            # Containment pin: the composer's unit is the mark height, so a
            # box that escapes the pinned `0 0 W 1` canvas is a geometry
            # bug that would CLIP the mark at render time (the unit-mixing
            # defect this file shipped once — the pin keeps it dead).
            try:
                bx, by, bw, bh = (float(part) for part in box.split())
            except ValueError:
                problems.append(f"malformed {BOX_ATTR} {box!r}")
            else:
                eps = 1e-6
                if bx < -eps or by < -eps or bx + bw > total_w + eps or by + bh > 1.0 + eps:
                    problems.append(
                        f"layer {cls!r} box {box!r} escapes the {viewBox!r} canvas"
                    )
        for attr_name in el.attrib:
            if FORBIDDEN_ATTR.match(attr_name.rsplit("}", 1)[-1].lower()):
                problems.append(f"forbidden attribute {attr_name!r}")
        for child in el.iter():
            cns, ctag = ns_local(child.tag)
            if ctag.lower() in {
                "script", "image", "style", "foreignobject",
                "animate", "set", "animatetransform", "animatemotion", "discard", "handler",
            }:
                problems.append(f"forbidden element <{ctag}>")
            if cns not in (None, SVG_NS, XLINK_NS):
                problems.append(f"foreign-namespace element <{ctag}>")
            for attr_name, value in child.attrib.items():
                an = attr_name.rsplit("}", 1)[-1].lower()
                if FORBIDDEN_ATTR.match(an):
                    problems.append(f"forbidden attribute {attr_name!r}")
                if an in ("href", "src"):
                    probe = URL_PROBE_STRIP.sub("", value).lower()
                    if EXTERNAL_REF.search(probe) or probe.startswith(SCRIPT_SCHEMES):
                        problems.append(f"external reference in {attr_name!r}")
                if an in ("fill", "stroke", "stop-color"):
                    if not PAINT_ALLOWED.match(value.strip()):
                        problems.append(f"paint {value!r} is outside the allowlist")
                elif URL_CALL.search(value) and not FRAGMENT_URL.match(value.strip()):
                    problems.append(f"non-fragment url() in {attr_name!r}")
    if MARK_MONO_CLASS not in layers:
        problems.append(f"no {MARK_MONO_CLASS} layer")
    if MARK_COLOR_CLASS not in layers:
        problems.append(f"no {MARK_COLOR_CLASS} layer")
    if TEXT_CLASS not in layers:
        problems.append(f"no {TEXT_CLASS} layer")
    if expect_word is not None:
        worded = [el for el in root if el.get(WORD_ATTR) is not None]
        if len(worded) != 1 or worded[0].get(WORD_ATTR) != expect_word:
            problems.append(f"{WORD_ATTR} is not exactly {expect_word!r}")
    return problems


# ── fetching ─────────────────────────────────────────────────────────────────

_BUDGET_LOCK = threading.Lock()


def download_piece(url: str, budget: dict[str, int]) -> bytes | None:
    """Fetch one piece: transport failures retry, a 404 is a clean downgrade
    (the variant does not exist for this vendor), an oversized payload is a
    policy refusal. The byte budget is shared across the worker threads."""
    last: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            url, headers={"User-Agent": "provider-registry-logos-combines/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read(MAX_SVG_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last = exc
        except Exception as exc:  # noqa: BLE001 — retry transport failures
            last = exc
        else:
            if len(payload) > MAX_SVG_BYTES:
                raise LogoError(f"download exceeds {MAX_SVG_BYTES} bytes")
            with _BUDGET_LOCK:
                budget["total"] -= len(payload)
                if budget["total"] < 0:
                    raise LogoError("total download budget exhausted — refusing to continue")
            return payload
    raise LogoError(f"download failed after retries: {last}")


def piece_url(name: str) -> str:
    return LOBEHUB_URL.format(package=LOBEHUB_PACKAGE, pin=LOBEHUB_PIN, name=name)


def load_local(logos_dir: Path, relative: str) -> bytes:
    path = (logos_dir / relative).resolve()
    logos_root = logos_dir.resolve()
    if logos_root != path and logos_root not in path.parents:
        raise LogoError(f"local override {relative!r} escapes the logos/ tree")
    if not path.is_file():
        raise LogoError(f"local override {relative!r} is missing")
    return path.read_bytes()


# ── per-provider compose ─────────────────────────────────────────────────────


def load_combine_table(provider: str, entry: dict) -> dict:
    combine = entry.get("combine")
    if not isinstance(combine, dict):
        raise LogoError(f"[{provider}] has no [combine] table")
    if not isinstance(combine.get("word"), str) or not combine["word"]:
        raise LogoError(f"[{provider}.combine] is missing word")
    if not (combine.get("lobehub_mark") or combine.get("local_mark")):
        raise LogoError(f"[{provider}.combine] needs lobehub_mark or local_mark")
    if not (combine.get("lobehub_text") or combine.get("local_text")):
        raise LogoError(f"[{provider}.combine] needs lobehub_text or local_text")
    tint = combine.get("tint")
    if tint is not None and not re.fullmatch(r"[0-9a-fA-F]{6}", str(tint)):
        raise LogoError(f"[{provider}.combine] tint must be a 6-hex string without '#'")
    return combine


class ComposeResult:
    __slots__ = ("provider", "data", "note")

    def __init__(self, provider: str, data: bytes, note: str) -> None:
        self.provider = provider
        self.data = data
        self.note = note


def compose_provider(provider: str, entry: dict, logos_dir: Path,
                     budget: dict[str, int]) -> tuple[ComposeResult | None, str | None]:
    """Compose one provider. Returns (result, reason): a None result with a
    reason is either a documented skip (missing wordmark) or a failure — the
    caller separates them by the FAILED: prefix."""
    from fetch_logos import normalize_svg

    try:
        combine = load_combine_table(provider, entry)
        mark_name = combine.get("lobehub_mark") or ""
        text_name = combine.get("lobehub_text") or ""

        if combine.get("local_mark"):
            mark_raw = load_local(logos_dir, combine["local_mark"])
        else:
            mark_raw = download_piece(piece_url(mark_name), budget)
        if mark_raw is None:
            return None, f"FAILED: mono mark {piece_url(mark_name)} vanished — refusing to compose"

        if combine.get("local_color"):
            color_raw: bytes | None = load_local(logos_dir, combine["local_color"])
        elif mark_name:
            color_raw = download_piece(piece_url(f"{mark_name}-color"), budget)
        else:
            color_raw = None

        if combine.get("local_text"):
            text_raw: bytes | None = load_local(logos_dir, combine["local_text"])
        else:
            text_raw = download_piece(piece_url(f"{text_name}-text"), budget)
        if text_raw is None:
            return None, (
                f"no -text wordmark variant for {text_name!r} — skipped"
                " (no lockup synthesized, the gap stays visible)"
            )

        mono_svg = normalize_svg(mark_raw, "mono")
        text_svg = normalize_svg(text_raw, "brand")
        mono_group, mono_ink = lift_piece(mono_svg)
        text_group, text_ink = lift_piece(text_svg)

        tint_value = combine.get("tint") or entry.get("brand_color")
        color_group: ET.Element | None = None
        color_ink: Bounds | None = None
        if color_raw is not None:
            color_svg = normalize_svg(color_raw, "brand")
            color_group, color_ink = lift_piece(color_svg)
            named = normalize_named_fills(color_group)
            note = resolve_colorwork(color_group)
            if named:
                note += f"; {named} named color(s) canonicalized to hex"
        else:
            tint_rgb: tuple[int, int, int] | None = None
            if tint_value:
                _, tint_rgb = parse_paint(f"#{tint_value}")
            if tint_rgb is not None and contrast(tint_rgb, LIGHT_FABRIC) >= TINT_MIN_CONTRAST:
                # Tint the mono mark for the light layer; the dark layer stays
                # currentColor. Both layers carry their own geometry copies so
                # the consumer's theme switch is a pure layer toggle.
                color_group, color_ink = lift_piece(
                    mono_svg, paint_override={"currentColor": hex6(tint_rgb)}
                )
                note = f"no -color artwork; mono tinted {hex6(tint_rgb)} (tint clears contrast)"
            else:
                note = (
                    f"no -color artwork; tint {tint_value or '—'} below contrast"
                    f" {TINT_MIN_CONTRAST} on light fabric — color layer folded into mono"
                )

        data = compose_lockup(
            str(combine["word"]),
            mono_group, mono_ink,
            color_group, color_ink,
            text_group, text_ink,
        )
        problems = validate_composed(data, expect_word=str(combine["word"]))
        if problems:
            raise LogoError("; ".join(problems))
        return ComposeResult(provider, data, note), None
    except LogoError as exc:
        return None, f"FAILED: {exc}"
    except Exception as exc:  # noqa: BLE001 — a crash is a failure, not a skip
        return None, f"FAILED: {type(exc).__name__}: {exc}"


# ── manifest ─────────────────────────────────────────────────────────────────


def combine_path(logos_dir: Path, provider: str) -> Path:
    return logos_dir / COMBINE_TOP / f"{provider}.svg"


def cmd_fetch(logos_dir: Path, only: set[str] | None) -> int:
    entries = load_manifest(logos_dir)
    targets = [p for p in sorted(entries) if isinstance(entries[p].get("combine"), dict)]
    if only:
        missing_combine = sorted(only - set(targets))
        if missing_combine:
            print(
                f"[FAIL] --only names provider(s) without a [combine] table:"
                f" {missing_combine}",
                file=sys.stderr,
            )
            return 1
        targets = [p for p in targets if p in only]
    if not targets:
        print("no [*.combine] tables in the manifest — nothing to compose", file=sys.stderr)
        return 1

    budget = {"total": MAX_TOTAL_BYTES}
    results: list[ComposeResult] = []
    skips: list[str] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            provider: pool.submit(compose_provider, provider, entries[provider], logos_dir, budget)
            for provider in targets
        }
        for provider, future in futures.items():
            result, reason = future.result()
            if result is not None:
                results.append(result)
            elif reason is not None:
                (failures if reason.startswith("FAILED:") else skips).append(f"{provider}: {reason}")

    out_dir = logos_dir / COMBINE_TOP
    out_dir.mkdir(parents=True, exist_ok=True)
    refreshed = False
    for result in sorted(results, key=lambda r: r.provider):
        target = combine_path(logos_dir, result.provider)
        target.write_bytes(result.data)
        digest = hashlib.sha256(result.data).hexdigest()
        table = entries[result.provider]["combine"]
        if table.get("sha256") != digest or "fetched_at" not in table:
            table["sha256"] = digest
            table["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            refreshed = True
        print(f"[ok] {result.provider} <- combine ({len(result.data)} bytes): {result.note}")

    for skip in skips:
        print(f"[skip] {skip}", file=sys.stderr)
    for failure in failures:
        print(f"[FAIL] {failure}", file=sys.stderr)

    if failures:
        print(f"combine: {len(failures)} provider(s) failed", file=sys.stderr)
        return 1
    if refreshed:
        rewrite_manifest(logos_dir, entries)
        print("manifest refreshed (combine sha256/fetched_at)")
    print(f"combine: {len(results)} lockup(s) composed, {len(skips)} skip(s)")
    return 0


def cmd_check(logos_dir: Path) -> int:
    entries = load_manifest(logos_dir)
    problems: list[str] = []
    checked = 0
    for provider, entry in sorted(entries.items()):
        combine = entry.get("combine")
        if not isinstance(combine, dict):
            continue
        target = combine_path(logos_dir, provider)
        if not target.is_file():
            problems.append(f"{provider}: {target.name} is missing (run fetch_logos_combines.py)")
            continue
        digest = sha256_file(target)
        if combine.get("sha256") != digest:
            problems.append(
                f"{provider}: combine sha256 {combine.get('sha256')!r} != file {digest!r}"
                " — rerun scripts/fetch_logos_combines.py"
            )
            continue
        problems.extend(
            f"{provider}: combine invariant — {p}"
            for p in validate_composed(target.read_bytes(), expect_word=combine.get("word"))
        )
        checked += 1
    for problem in problems:
        print(f"[FAIL] {problem}", file=sys.stderr)
    if problems:
        print(f"combine check: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"combine check: {checked} lockup(s) verified")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent,
        help="registry root (default: the checkout this script lives in)",
    )
    parser.add_argument("--only", type=str, default="", help="comma-separated provider ids")
    parser.add_argument("--check", action="store_true", help="verify the committed tree, no network")
    args = parser.parse_args(argv)

    logos_dir = args.root / LOGOS_TOP
    if not logos_dir.is_dir():
        print(f"{logos_dir} is missing — this is not a registry root", file=sys.stderr)
        return 2
    only = {part.strip() for part in args.only.split(",") if part.strip()} or None
    if args.check:
        return cmd_check(logos_dir)
    return cmd_fetch(logos_dir, only)


if __name__ == "__main__":
    sys.exit(main())
