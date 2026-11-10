#!/usr/bin/env python3
"""Fetch, normalize and verify the provider logo assets in `logos/`.

The logo tree is the upstream CACHE for vendor marks: official SVGs (vendor
press kits) first, simple-icons (CC0, the official mark as a single path)
second, and providers with no usable vector source keep the consumer's
letter fallback. Consumers vendor a snapshot of this tree (e.g. shittim-chest
generates a TS module from it via its own export script), so the committed
bytes must be deterministic — no timestamps inside the SVG files, stable
serialization, hashes recorded in the manifest.

The manifest `logos/manifest.toml` is the hand-authored SOURCING decision per
provider:

    [deepseek]
    source = "simple-icons"          # "official" | "simple-icons"
    source_url = "https://raw.githubusercontent.com/simple-icons/simple-icons/master/icons/deepseek.svg"
    mode = "mono"                    # "mono" (fill=currentColor, theme-adaptive)
                                     # | "brand" (original fills kept)
    license = "CC0-1.0 (path data); the mark itself is DeepSeek's trademark"
    brand_color = "4D6BFE"           # vendor brand hex, no '#', drives the chip tint

The tool owns the DERIVED fields (`sha256`, `fetched_at`): a fetch refreshes
them, a check verifies them.

Usage:
    python3 scripts/fetch_logos.py                # fetch every manifest entry (network)
    python3 scripts/fetch_logos.py --only deepseek,zhipu_glm
    python3 scripts/fetch_logos.py --check        # no network: verify the committed tree

Exit codes: 0 ok / 1 a fetch or a check failed / 2 the tree layout is wrong.

Normalization (both modes; the committed file IS the normalized form):
  * XML-parsed and re-serialized deterministically: comments, processing
    instructions, `<title>`/`<desc>`/`<metadata>` and every editor-namespace
    element (inkscape/sodipodi/dc/rdf) are dropped — the vendor name rides the
    consumer chip's `aria-label`, never an SVG `<title>` that would
    double-announce next to it;
  * `style` ATTRIBUTES are dropped everywhere (a CSS smuggling vector, and
    `enable-background`-style legacy decorations carry no mark information);
  * `viewBox` required (derived from numeric width/height when absent), the
    fixed `width`/`height` dropped so the consumer scales it;
  * safety: `<script>`, `<image>`, `<style>`, `<foreignObject>`, `on*`
    attributes and external `href`/`src` references are REJECTED — a logo is
    data, never active content;
  * mono mode additionally sets `fill="currentColor"` on the root so the mark
    follows the consumer's theme text color;
  * byte cap: a source above MAX_SVG_BYTES is refused (press kits sometimes
    ship multi-megabyte artwork; the chip needs a compact mark).

`pre_clean = "inline-css"` (manifest, optional) resolves `<style>` CLASS rules
into presentation attributes BEFORE that walk — official Illustrator exports
(zhipu/Z.ai's logo.svg, stepfun's favicon) put every fill on `.stN` classes,
and stripping the block would leave unpainted shapes. Only plain `.class`
selectors are accepted; anything structural (`@media`, element/descendant
selectors) fails loudly instead of half-converting.

Downloads carry a total byte budget and a per-source retry cap, exactly like
the other registry sync scripts.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

LOGOS_TOP = "logos"
MANIFEST_NAME = "manifest.toml"

MAX_SVG_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_RETRIES = 3

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Elements that make an SVG active content or dependent on external state —
# rejected in both modes, whatever namespace they appear in. Compared against
# the LOWERCASED local name: XML is case-sensitive and a `<SCRIPT>` element
# survives ElementTree verbatim, but the browser's HTML/SVG parser folds case
# when the SVG is inlined — an uppercase gate check is a silent XSS bypass.
#
# SMIL animation elements are forbidden wholesale: a logo never needs
# animation, and `<animate attributeName="href" to="javascript:…"/>` /
# `<set attributeName="onmouseover" to="…"/>` are documented click-triggered
# XSS payloads that carry NO href/src attribute for the scheme gate to see.
FORBIDDEN_ELEMENTS = frozenset({
    "script", "image", "style", "foreignobject",
    "animate", "set", "animatetransform", "animatemotion", "discard", "handler",
})
# Metadata elements dropped from the committed form (lowercase compare, same
# case-folding rationale).
METADATA_ELEMENTS = frozenset({"title", "desc", "metadata"})
# Attributes never allowed: event handlers, matched case-insensitively (an
# `ONLOAD` attribute is as executable as `onload` once the browser folds it).
FORBIDDEN_ATTR = re.compile(r"^on[a-z]+$", re.IGNORECASE)
# href/src (any namespace) must not reference anything external OR carry a
# script scheme: `//` covers protocol-relative and absolute URLs, the scheme
# prefixes cover `javascript:` / `data:` / `vbscript:` payloads (a chip can
# sit inside a clickable surface). The probe is checked on a copy of the
# value with ALL ASCII C0 controls and spaces removed — browsers strip
# TAB/LF/CR anywhere inside a URL, so `jav&#09;ascript:` (an XML entity that
# decodes to an embedded TAB) would otherwise sail past an anchored,
# literal `javascript:` match.
EXTERNAL_REF = re.compile(r"(?:https?:)?//")
SCRIPT_SCHEMES = ("javascript:", "vbscript:", "data:")
URL_PROBE_STRIP = re.compile(r"[\x00-\x20]")
# Any url() reference outside inline-css must target a fragment (`#id`):
# internal gradients/clipPaths are fine, `url(https://…)` in a paint or
# clip-path attribute is a rendering-time beacon / remote-content channel.
FRAGMENT_URL = re.compile(r"^url\(#[^)]*\)$", re.IGNORECASE)
URL_CALL = re.compile(r"url\(", re.IGNORECASE)
# Numbers in a viewBox: negative components are legal SVG.
NUMERIC = re.compile(r"^-?[0-9]*\.?[0-9]+$")


class LogoError(Exception):
    """A fetch or normalization failure with a human-readable reason."""


# ── normalization ────────────────────────────────────────────────────────────


def _ns_local(tag: str) -> tuple[str | None, str]:
    """`{ns}local` -> (ns, local); plain `local` -> (None, local)."""
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return ns, local
    return None, tag


def _derive_viewbox(root: ET.Element) -> str:
    existing = root.get("viewBox")
    if existing:
        parts = existing.replace(",", " ").split()
        if len(parts) != 4 or any(not NUMERIC.match(p) for p in parts):
            raise LogoError(f"viewBox {existing!r} is not four numbers")
        return " ".join(parts)
    width, height = root.get("width"), root.get("height")
    if width and height and NUMERIC.match(width) and NUMERIC.match(height):
        return f"0 0 {float(width):g} {float(height):g}"
    raise LogoError("no viewBox and no numeric width/height to derive one from")


# A CSS property name we accept as an SVG presentation attribute, and a value
# that carries nothing active (internal `url(#id)` gradients are fine; anything
# else callable is not).
CSS_PROP = re.compile(r"^-?[a-z][a-z0-9-]*$")
CSS_VALUE_SAFE = re.compile(r"^url\(#[^)]*\)$|^#[0-9a-fA-F]{3,8}$|^[^;:(){}@]*$")
CSS_CLASS_RULE = re.compile(r"\.(-?[A-Za-z_][\w-]*)\s*\{([^}]*)\}")


def inline_css_classes(data: bytes) -> bytes:
    """Resolve `<style>` class rules into presentation attributes.

    Illustrator-exported official logos put every fill on `.stN` classes; the
    block cannot simply be stripped (shapes would lose their paint). This
    pass inlines the declarations onto the elements carrying the class —
    stylesheet order wins per PROPERTY (later rules overwrite earlier ones
    property by property, they never erase a sibling rule's other
    declarations) — then removes the block and the class attributes.
    Anything but plain class selectors (@media, element or descendant
    selectors, keyframes) fails loudly: a silent half-conversion would ship
    a broken mark.
    """
    root = ET.fromstring(data)
    style_texts = [
        (el.text or "") + "".join(child.text or "" for child in el)
        for el in root.iter()
        if _ns_local(el.tag)[1] == "style"
    ]
    has_class_attrs = any("class" in el.attrib for el in root.iter())
    if not style_texts and not has_class_attrs:
        # Already resolved (the committed form) — the pass is idempotent.
        return data
    joined = "\n".join(style_texts)
    rules: dict[str, dict[str, str]] = {}
    remainder = CSS_CLASS_RULE.sub("", joined)
    residue = re.sub(r"\s+", "", remainder).strip("/;")
    if residue:
        raise LogoError(
            f"<style> carries non-class CSS ({residue[:60]!r}…) — pre_clean=inline-css refuses it"
        )
    for name, body in CSS_CLASS_RULE.findall(joined):
        props = rules.setdefault(name, {})
        for declaration in body.split(";"):
            if not declaration.strip():
                continue
            prop, sep, value = declaration.partition(":")
            prop, value = prop.strip(), value.strip()
            if not sep or not CSS_PROP.match(prop) or not CSS_VALUE_SAFE.match(value):
                raise LogoError(
                    f"unsafe CSS declaration {declaration!r} in .{name} — refusing"
                )
            props[prop] = value
        rules[name] = props

    if not rules:
        raise LogoError("pre_clean=inline-css found no class rules to inline")

    for el in root.iter():
        class_attr = el.get("class")
        if not class_attr:
            continue
        # Stylesheet order decides; element attribute order is irrelevant.
        for name in class_attr.split():
            for prop, value in rules.get(name, {}).items():
                el.set(prop, value)
        del el.attrib["class"]

    for parent in root.iter():
        for child in list(parent):
            if _ns_local(child.tag)[1] == "style":
                parent.remove(child)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


def normalize_svg(data: bytes, mode: str, pre_clean: str = "") -> bytes:
    """Parse, clean and deterministically re-serialize one logo SVG."""
    if mode not in ("mono", "brand"):
        raise LogoError(f"mode must be mono|brand, got {mode!r}")
    if len(data) > MAX_SVG_BYTES:
        raise LogoError(f"source is {len(data)} bytes, over the {MAX_SVG_BYTES} cap")
    if pre_clean:
        if pre_clean != "inline-css":
            raise LogoError(f"unknown pre_clean {pre_clean!r} (supported: inline-css)")
        data = inline_css_classes(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LogoError(f"not well-formed XML: {exc}") from exc

    ns, local = _ns_local(root.tag)
    if ns not in (None, SVG_NS) or local != "svg":
        raise LogoError(f"root element is {local!r}, not svg")

    root.set("viewBox", _derive_viewbox(root))
    for attr in ("width", "height"):
        root.attrib.pop(attr, None)
    # One validating walk: reject active content, collect the nodes the
    # committed form must not carry (metadata, editor namespaces). The root's
    # own attributes go through the same reject/strip pass as every child's.
    # Element/attribute local names compare LOWERCASED — XML preserves case,
    # the browser that later inlines this SVG does not (see FORBIDDEN_ELEMENTS).
    def screen_attributes(el: ET.Element) -> None:
        for attr_name in list(el.attrib):
            ans, alocal = _ns_local(attr_name)
            check_local = alocal.lower()
            if check_local == "style":
                # CSS-in-attribute is a smuggling vector and never carries
                # mark information we keep (`enable-background`-style legacy
                # decorations are dropped with it).
                del el.attrib[attr_name]
                continue
            if FORBIDDEN_ATTR.match(check_local):
                raise LogoError(f"forbidden attribute {attr_name!r}")
            value = el.attrib[attr_name]
            if check_local in ("href", "src"):
                # WHATWG URL parsing strips TAB/LF/CR (and leading C0) inside
                # the value — probe a control-stripped, lowercased copy.
                probe = URL_PROBE_STRIP.sub("", value).lower()
                if EXTERNAL_REF.search(probe) or probe.startswith(SCRIPT_SCHEMES):
                    raise LogoError(f"unsafe reference in {attr_name!r}")
            if URL_CALL.search(value) and not FRAGMENT_URL.match(value.strip()):
                raise LogoError(f"non-fragment url() reference in {attr_name!r}")
            if ans is not None and ans not in (SVG_NS, XLINK_NS):
                del el.attrib[attr_name]  # inkscape/sodipodi bookkeeping

    screen_attributes(root)
    parents: dict[int, ET.Element] = {}
    to_remove: list[ET.Element] = []
    for parent in root.iter():
        for child in list(parent):
            parents[id(child)] = parent
            cns, clocal = _ns_local(child.tag)
            check_local = clocal.lower()
            if check_local in FORBIDDEN_ELEMENTS:
                raise LogoError(f"forbidden element <{clocal}>")
            if check_local in METADATA_ELEMENTS and cns in (None, SVG_NS):
                to_remove.append(child)
                continue
            if cns is not None and cns != SVG_NS:
                to_remove.append(child)  # editor/metadata namespace
            screen_attributes(child)

    for child in to_remove:
        parents.get(id(child), root).remove(child)

    if mode == "mono":
        # Mono = ONE color that follows the consumer theme. Child paint colors
        # would override an inherited root fill, so every non-"none"
        # fill/stroke color is stripped (fill="none"/stroke="none" are kept —
        # they are transparency, not a color).
        #
        # The root then carries fill=currentColor ONLY when a shape actually
        # depends on inheritance: a mark drawn purely in strokes keeps its
        # root fill="none" (painting it would turn outlined shapes into
        # solid blobs), while a mark whose children carried explicit fills
        # (now stripped) needs the inherited color or it disappears.
        stroked = False
        stripped_fills = 0

        def strip_paint(el: ET.Element, is_root: bool) -> None:
            nonlocal stroked, stripped_fills
            for attr_name in ("fill", "stroke"):
                value = el.get(attr_name)
                if value is None:
                    continue
                if value.strip().lower() == "none":
                    continue
                if attr_name == "stroke":
                    stroked = True
                elif not is_root:
                    stripped_fills += 1
                del el.attrib[attr_name]

        strip_paint(root, True)
        for el in root.iter():
            if el is root:
                continue
            strip_paint(el, False)
        if root.get("fill") != "none" or stripped_fills:
            root.set("fill", "currentColor")
        if stroked:
            root.set("stroke", "currentColor")

    # Deterministic serialization: pin the prefixes so the bytes are stable
    # across Python versions, then write compact UTF-8 without a declaration.
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


# ── manifest ─────────────────────────────────────────────────────────────────


def load_manifest(logos_dir: Path) -> dict[str, dict]:
    manifest_path = logos_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise LogoError(f"{manifest_path} is missing")
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    entries: dict[str, dict] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue  # stray scalars are not provider sections; a future
            # dict-typed SECTION must still satisfy the per-entry checks below
        for field in ("source", "source_url", "mode", "license"):
            if not isinstance(value.get(field), str) or not value[field]:
                raise LogoError(f"[{key}] is missing {field}")
        if value["source"] not in ("official", "simple-icons"):
            raise LogoError(f"[{key}] source must be official|simple-icons")
        if value["mode"] not in ("mono", "brand"):
            raise LogoError(f"[{key}] mode must be mono|brand")
        pre_clean = value.get("pre_clean", "")
        if pre_clean not in ("", "inline-css"):
            raise LogoError(f"[{key}] pre_clean must be empty or inline-css")
        if value.get("brand_color") and not re.fullmatch(r"[0-9a-fA-F]{6}", str(value["brand_color"])):
            raise LogoError(f"[{key}] brand_color must be a 6-hex string without '#'")
        entries[key] = value
    if not entries:
        raise LogoError(f"{manifest_path} carries no provider entries")
    return entries


def svg_path(logos_dir: Path, provider: str) -> Path:
    return logos_dir / f"{provider}.svg"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── fetching ─────────────────────────────────────────────────────────────────


def download(url: str, budget: dict[str, int]) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "provider-registry-logos/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read(MAX_SVG_BYTES + 1)
            if len(payload) > MAX_SVG_BYTES:
                # A data-sized refusal is not a transport failure: raise the
                # policy error so the LogoError handler below re-raises it
                # without consuming the retry budget (retrying an oversized
                # source three times only slows the failure).
                raise LogoError(f"download exceeds {MAX_SVG_BYTES} bytes")
            budget["total"] -= len(payload)
            if budget["total"] < 0:
                raise LogoError("total download budget exhausted — refusing to continue")
            return payload
        except LogoError:
            raise  # policy refusals are final; only transport errors retry
        except Exception as exc:  # noqa: BLE001 — retry transport failures
            last_error = exc
            if attempt < MAX_RETRIES:
                continue
    raise LogoError(f"download failed after {MAX_RETRIES} attempts: {last_error}")


def cmd_fetch(logos_dir: Path, only: set[str] | None) -> int:
    entries = load_manifest(logos_dir)
    if only:
        unknown = only - entries.keys()
        if unknown:
            print(f"[FAIL] --only names unknown providers: {sorted(unknown)}", file=sys.stderr)
            return 1
    budget = {"total": MAX_TOTAL_BYTES}
    failures = 0
    refreshed = False
    for provider, entry in sorted(entries.items()):
        if only and provider not in only:
            continue
        try:
            payload = download(entry["source_url"], budget)
            normalized = normalize_svg(payload, entry["mode"], entry.get("pre_clean", ""))
        except LogoError as exc:
            print(f"[FAIL] {provider}: {exc}", file=sys.stderr)
            failures += 1
            continue
        target = svg_path(logos_dir, provider)
        target.write_bytes(normalized)
        digest = hashlib.sha256(normalized).hexdigest()
        if entry.get("sha256") != digest or "fetched_at" not in entry:
            entry["sha256"] = digest
            entry["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            refreshed = True
        print(f"[ok] {provider} <- {entry['source']} ({len(normalized)} bytes)")
    if failures:
        print(f"fetch: {failures} provider(s) failed", file=sys.stderr)
        return 1
    if refreshed:
        rewrite_manifest(logos_dir, entries)
        print("manifest refreshed (sha256/fetched_at)")
    return 0


DEFAULT_MANIFEST_HEADER = [
    "# Provider logo manifest — the hand-authored SOURCING decision per provider.",
    "# `source`/`source_url`/`mode`/`pre_clean`/`license`/`brand_color` are hand-",
    "# maintained; `sha256`/`fetched_at` are owned by scripts/fetch_logos.py (do",
    "# not hand-edit).",
    "#",
    '# mode = "mono"   -> the SVG carries fill=currentColor, follows the theme text.',
    '# mode = "brand"  -> original vendor fills kept (multi-color marks).',
    "#",
    "# A provider WITHOUT an entry here has no usable vector source yet and keeps",
    "# the consumer's letter fallback (see validate_logos.py's allowlist).",
]

# bare-safe by full match (`match` + `$` still matches before a trailing
# newline: the key "a\n" would pass and be written bare, splitting the line).
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_string(value: str) -> str:
    """A TOML basic string that is VALID for ANY input: backslash, quote and
    every control character (including newlines and DEL) escaped — a raw
    newline inside a quoted single-line string is illegal TOML, and browsers
    are not the only consumers that fold what XML preserves."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = "".join(
        ch if ord(ch) >= 0x20 and ord(ch) != 0x7F else f"\\u{ord(ch):04X}"
        for ch in escaped
    )
    return f'"{escaped}"'


def _toml_key(key: str) -> str:
    """A TOML key: bare when the spelling allows it, else a quoted string.
    Quoting is not cosmetic — a bare `my.note` parses as the DOTTED key
    my.note (silently reshaping a `my.note` annotation into a `my` table),
    and an embedded quote produces illegal bytes."""
    if _BARE_KEY.fullmatch(key):
        return key
    return _toml_string(key)


def _toml_inline(field: str, value: object) -> str:
    """Render one unknown manifest value as a TOML literal.

    Scalars natively; arrays as TOML arrays; tables as TOML inline tables
    (`{ key = value }` — JSON's colon syntax is NOT valid TOML and would
    poison the next tomllib parse). `None` has no TOML spelling at all, so
    anything carrying one is refused; the caller drops it loudly."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_inline(field, item) for item in value) + "]"
    if isinstance(value, dict):
        body = ", ".join(
            _toml_key(key) + " = " + _toml_inline(field, item)
            for key, item in value.items()
        )
        return "{" + body + "}"
    raise LogoError(f"field {field!r}: {type(value).__name__} has no TOML spelling")


def _toml_unknown(field: str, value: object, problems: list[str]) -> str | None:
    """TOML spelling for an unknown field, or None (dropped, loudly) when the
    value cannot be expressed — a `None` anywhere in a list/table, or a
    non-JSON type. Silent loss on a hash refresh would hide the annotation."""
    try:
        return _toml_inline(field, value)
    except LogoError as exc:
        problems.append(f"[?] {exc} and was DROPPED from the rewrite")
        return None


def rewrite_manifest(logos_dir: Path, entries: dict[str, dict]) -> None:
    """Rewrite manifest.toml in a stable field order (deterministic bytes).

    The leading hand-written comment block is preserved across the rewrite —
    the seed date, the pinned simple-icons version and the ZIP-reseed notes
    live there, and a hash refresh must not erase them. (A fresh tree with no
    comment block gets the tool's default header.)"""
    existing = logos_dir / MANIFEST_NAME
    header: list[str] | None = None
    if existing.is_file():
        candidate: list[str] = []
        for line in existing.read_text(encoding="utf-8").splitlines():
            if line.startswith("["):
                break
            candidate.append(line)
        if any(line.strip().startswith("#") for line in candidate):
            # Trailing blanks are normalized away — re-appending exactly one
            # separator below keeps repeated rewrites byte-identical instead
            # of growing a blank line per refresh.
            while candidate and not candidate[-1].strip():
                candidate.pop()
            header = candidate
    lines = list(header if header is not None else DEFAULT_MANIFEST_HEADER)
    lines.append("")
    problems: list[str] = []
    for provider, entry in sorted(entries.items()):
        # Table headers are keys too: a quoted provider id (or one carrying
        # control characters) must come out as ['prov"two'], not [prov"two].
        lines.append(f"[{_toml_key(provider)}]")
        emitted = set()
        for field in (
            "source", "source_url", "mode", "pre_clean", "license",
            "brand_color", "sha256", "fetched_at",
        ):
            value = entry.get(field)
            if value is None or value == "":
                continue
            # The shared emitter, not a hand-rolled escape: a license note
            # carrying a newline (or any control character) must serialize
            # as a legal single-line basic string, same as unknown fields.
            lines.append(f"{_toml_key(field)} = {_toml_string(str(value))}")
            emitted.add(field)
        # Fields this tool does not know (a reviewer's `note =`, future
        # metadata) survive the rewrite AFTER the known block — dropping
        # hand-written annotations on a hash refresh would be silent loss.
        for field in sorted(k for k in entry if k not in emitted):
            rendered = _toml_unknown(field, entry[field], problems)
            if rendered is not None:
                # The KEY needs the same treatment as its value: a bare
                # `my.note` parses as a dotted key (silently reshaping the
                # annotation into a `my` table) and an embedded quote is
                # illegal bytes — quote+escape unless bare-safe.
                lines.append(f"{_toml_key(field)} = {rendered}")
        lines.append("")
    (logos_dir / MANIFEST_NAME).write_text("\n".join(lines), encoding="utf-8")
    for problem in problems:
        print(f"[WARN] {problem}", file=sys.stderr)


# ── checking (no network) ────────────────────────────────────────────────────


def file_check_problems(logos_dir: Path, entries: dict[str, dict]) -> list[str]:
    """Offline per-file verification: presence, manifest hash, normalization
    idempotency. Shared by `fetch_logos.py --check` and validate_logos."""
    problems: list[str] = []
    for provider, entry in sorted(entries.items()):
        target = svg_path(logos_dir, provider)
        if not target.is_file():
            problems.append(f"{provider}: {target.name} is missing")
            continue
        raw = target.read_bytes()
        digest = sha256_file(target)
        if entry.get("sha256") != digest:
            problems.append(
                f"{provider}: manifest sha256 {entry.get('sha256')!r} != file {digest!r}"
                " — rerun scripts/fetch_logos.py after re-sourcing"
            )
            continue
        try:
            renormalized = normalize_svg(raw, entry["mode"], entry.get("pre_clean", ""))
        except LogoError as exc:
            problems.append(f"{provider}: committed file breaks invariants: {exc}")
            continue
        if renormalized != raw:
            problems.append(
                f"{provider}: committed file is not the normalized form"
                f" (hand-edited? rerun scripts/fetch_logos.py --only {provider})"
            )
    return problems


def cmd_check(logos_dir: Path) -> int:
    entries = load_manifest(logos_dir)
    problems = file_check_problems(logos_dir, entries)
    for problem in problems:
        print(f"[FAIL] {problem}", file=sys.stderr)
    if problems:
        print(f"check: {len(problems)} provider(s) failed", file=sys.stderr)
        return 1
    print(f"check: {len(entries)} logo(s) verified")
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
