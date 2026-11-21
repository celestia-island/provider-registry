#!/usr/bin/env python3
"""Behavioral pins for the combine lockup composer (fetch_logos_combines.py).

Run: python3 scripts/test_fetch_logos_combines.py  (exit 0 = all green)

The combine tree is vendored into consumers exactly like the mono tree, so
every contract below gets a positive pin (the fixture composes / validates)
and a negative one (the deliberate defect fails with a message that names
it). All fixtures are LOCAL (local_mark / local_color / local_text) — the
suite never touches the network.
"""
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fetch_logos import LogoError, load_manifest, rewrite_manifest  # noqa: E402
import fetch_logos_combines as fc  # noqa: E402

import xml.etree.ElementTree as ET  # noqa: E402

PASS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS
    if not condition:
        print(f"[FAIL] {name}: {detail}", file=sys.stderr)
        raise SystemExit(1)
    PASS += 1
    print(f"[ok] {name}")


def expect_error(name: str, fn, needle: str) -> None:
    global PASS
    try:
        fn()
    except LogoError as exc:
        if needle.lower() not in str(exc).lower():
            print(f"[FAIL] {name}: error {exc!r} lacks {needle!r}", file=sys.stderr)
            raise SystemExit(1)
        PASS += 1
        print(f"[ok] {name}")
        return
    except SystemExit as exc:  # require_fonttools exits the process
        if exc.code != 2 or needle.lower() not in "fonttools":
            raise
        PASS += 1
        print(f"[ok] {name}")
        return
    print(f"[FAIL] {name}: no LogoError raised", file=sys.stderr)
    raise SystemExit(1)


MARK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><title>T</title><path d="M2 2h20v20H2z" fill="currentColor"/></svg>"""
TEXT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 24"><title>T</title><path d="M3 5h44v14H3z" fill="currentColor"/></svg>"""
# Color artwork: the main (most-carried) flat fill is pale and must flip to
# currentColor; a white knockout and an internal-gradient paint stay.
COLOR_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><title>T</title><path d="M2 2h9v9H2z" fill="#F6D85A"/><path d="M13 2h9v9h-9z" fill="#F6D85A"/><path d="M2 13h9v9H2z" fill="#ffffff"/><path d="M13 13h9v9h-9z" fill="url(#g1)"/><defs><linearGradient id="g1"><stop stop-color="#123456"/></linearGradient></defs></svg>"""

ROOT_MARK = "M4 4h16v16H4z"  # square-ish mark for geometry pins
WIDE_MARK = "M2 6h20v5H2z"  # aspect > 1 — the over-width clamp pin


def svg(body: str, viewbox: str = "0 0 24 24") -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
        f'fill="currentColor">{body}</svg>'
    ).encode()


# ── colors ───────────────────────────────────────────────────────────────────


def test_colors() -> None:
    check("contrast black/white = 21", abs(fc.contrast((0, 0, 0), (255, 255, 255)) - 21.0) < 1e-9)
    check("contrast white/white = 1", abs(fc.contrast((255, 255, 255), (255, 255, 255)) - 1.0) < 1e-9)
    claude = fc.parse_paint("#D97757")[1]
    check("claude coral clears 3.0 on white", fc.contrast(claude, fc.LIGHT_FABRIC) >= 3.0,
          f"{fc.contrast(claude, fc.LIGHT_FABRIC):.2f}")
    teal = fc.parse_paint("#22B8CD")[1]
    check("perplexity teal fails 3.0 on white", fc.contrast(teal, fc.LIGHT_FABRIC) < 3.0)
    check("hex3 expands", fc.parse_paint("#fff") == ("hex", (255, 255, 255)))
    check("named gold resolves", fc.parse_paint("gold") == ("hex", (255, 215, 0)))
    check("currentColor kind", fc.parse_paint("currentColor")[0] == "currentColor")
    check("url kind", fc.parse_paint("url(#a)")[0] == "url")
    expect_error("unknown named color refused", lambda: fc.parse_paint("plum"), "unsupported color")


# ── ink measurement ──────────────────────────────────────────────────────────


def test_ink_bounds() -> None:
    root = ET.fromstring(svg(f'<path d="{ROOT_MARK}"/>'))
    box = fc.ink_bounds(root)
    check("path ink box", box == (4.0, 4.0, 20.0, 20.0), str(box))
    root = ET.fromstring(svg('<rect x="1" y="2" width="6" height="3"/><circle cx="18" cy="18" r="2"/>'))
    box = fc.ink_bounds(root)
    check("rect+circle union", box == (1.0, 2.0, 20.0, 20.0), str(box))
    root = ET.fromstring(svg('<g transform="translate(1 2) scale(2)"><path d="M2 3h3v3H2z"/></g>'))
    box = fc.ink_bounds(root)
    check("translate+scale applied", box == (5.0, 8.0, 11.0, 14.0), str(box))
    root = ET.fromstring(svg('<defs><path d="M0 0h30v30H0z"/></defs><path d="M4 4h2v2H4z"/>'))
    box = fc.ink_bounds(root)
    check("defs never counts as ink", box == (4.0, 4.0, 6.0, 6.0), str(box))
    root = ET.fromstring(svg('<path d="M2 2h4v4H2z" fill="none"/>'))
    expect_error("unpainted tree has no ink", lambda: fc.ink_bounds(root), "no painted ink")
    root = ET.fromstring(svg('<path d="M2 2h4v4H2z" fill="none" stroke="currentColor" stroke-width="2"/>'))
    box = fc.ink_bounds(root)
    check("stroke widens the box", box == (1.0, 1.0, 7.0, 7.0), str(box))
    expect_error("rotation refused", lambda: fc.parse_transform("rotate(45)"), "rotate")


# ── layer policy ─────────────────────────────────────────────────────────────


def test_layers() -> None:
    group, _ = fc.lift_piece(COLOR_SVG)
    note = fc.resolve_colorwork(group)
    fills = [el.get("fill") for el in group.iter() if el.get("fill")]
    check("main pale fill flipped to currentColor", "currentColor" in fills, str(fills))
    check("white knockout kept", "#ffffff" in fills, str(fills))
    check("gradient paint kept", any(str(f).startswith("url(#") for f in fills), str(fills))
    check("note names the flip", "below contrast" in note, note)

    group, _ = fc.lift_piece(COLOR_SVG)
    check("named colors canonicalized", fc.normalize_named_fills(group) >= 0)
    gold_group, _ = fc.lift_piece(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h4v4H2z" fill="gold"/></svg>'
    )
    n = fc.normalize_named_fills(gold_group)
    fill = [el.get("fill") for el in gold_group.iter() if el.get("fill")][0]
    check("gold -> #ffd700", n == 1 and fill == "#ffd700", f"{n} {fill}")


# ── composition ──────────────────────────────────────────────────────────────


def _compose(mark: bytes = MARK_SVG, color: bytes | None = COLOR_SVG, text: bytes = TEXT_SVG,
             tint: str | None = None) -> bytes:
    # Mirror compose_provider's normalization so the pins cover the real
    # pipeline (mono mark -> currentColor, artwork/text keep their fills).
    from fetch_logos import normalize_svg

    mono_group, mono_ink = fc.lift_piece(normalize_svg(mark, "mono"))
    text_group, text_ink = fc.lift_piece(normalize_svg(text, "brand"))
    color_group = color_ink = None
    if color is not None:
        color_group, color_ink = fc.lift_piece(normalize_svg(color, "brand"))
        fc.normalize_named_fills(color_group)
        fc.resolve_colorwork(color_group)
    elif tint:
        color_group, color_ink = fc.lift_piece(
            normalize_svg(mark, "mono"), paint_override={"currentColor": tint}
        )
    return fc.compose_lockup("T", mono_group, mono_ink, color_group, color_ink, text_group, text_ink)


def test_compose() -> None:
    data = _compose()
    again = _compose()
    check("compose is deterministic", data == again)
    root = ET.fromstring(data)
    check("viewBox pinned 0 0 W 1", root.get("viewBox").startswith("0 0 ") and root.get("viewBox").endswith(" 1"),
          root.get("viewBox"))
    classes = [" ".join(el.get("class", "").split()) for el in root]
    check("three layers present", classes == [fc.MARK_COLOR_CLASS, fc.MARK_MONO_CLASS, fc.TEXT_CLASS], str(classes))
    text_layer = [el for el in root if el.get(fc.WORD_ATTR)]
    check("word attr on the text layer", len(text_layer) == 1 and text_layer[0].get(fc.WORD_ATTR) == "T")
    box = text_layer[0].get(fc.BOX_ATTR).split()
    check("text band is 1/1.45 tall and centered", abs(float(box[3]) - 1 / fc.MARK_HEIGHT) < 1e-6
          and abs(float(box[1]) - (1 - 1 / fc.MARK_HEIGHT) / 2) < 1e-6, text_layer[0].get(fc.BOX_ATTR))
    # Unit contract: the mark box fills the full unit height and NEVER
    # escapes the pinned `0 0 W 1` canvas (the unit-mixing defect this
    # composer once shipped — validate_composed's containment pin is what
    # keeps it dead, exercised directly below).
    mark_layer = [el for el in root if fc.MARK_MONO_CLASS in el.get("class", "").split()][0]
    mx, my, mw, mh = (float(v) for v in mark_layer.get(fc.BOX_ATTR).split())
    vw = float(root.get("viewBox").split()[2])
    check("mark box fills the unit height", abs(mh - 1.0) < 1e-6 or abs(mw - 1.0) < 1e-6,
          mark_layer.get(fc.BOX_ATTR))
    check("mark box inside the canvas", mx >= -1e-6 and my >= -1e-6
          and mx + mw <= vw + 1e-6 and my + mh <= 1.0 + 1e-6, mark_layer.get(fc.BOX_ATTR))
    # This fixture's mark is tall (aspect ≤ 1), so it fills the unit height
    # and stands the skins' 1.45:1 proportion (mark = MARK_HEIGHT word
    # bands) — pin it, not just the geometry. A wide mark clamps at the
    # unit width instead and rides proportionally lower (see the clamp
    # block below).
    check("mark stands MARK_HEIGHT cap-bands tall",
          abs(mh / (float(box[3])) - fc.MARK_HEIGHT) < 1e-6,
          f"mark {mh} vs band {box[3]}")
    check("validate_composed green on real output", fc.validate_composed(data, expect_word="T") == [])
    # Negative control for the containment pin: a box pushed past the
    # canvas must be flagged (else the pin above is decoration).
    escaped = data.replace(
        mark_layer.get(fc.BOX_ATTR).encode(), b"0 0 1.45 1.45", 1
    )
    flagged = [p for p in fc.validate_composed(escaped) if "escapes" in p]
    check("escaping layer box flagged", bool(flagged), str(fc.validate_composed(escaped)[:2]))
    check("byte cap respected", len(data) <= fc.MAX_SVG_BYTES)

    # Over-width clamp: a 4:1 mark keeps its aspect inside a unit-wide box.
    wide = svg(f'<path d="{WIDE_MARK}"/>')
    data_wide = _compose(mark=wide, color=None)
    root = ET.fromstring(data_wide)
    mono = [el for el in root if fc.MARK_MONO_CLASS in el.get("class", "").split()][0]
    bw, bh = [float(v) for v in mono.get(fc.BOX_ATTR).split()[2:]]
    check("over-wide mark clamps at MAX_MARK_WIDTH", abs(bw - fc.MAX_MARK_WIDTH) < 1e-6,
          mono.get(fc.BOX_ATTR))
    check("over-wide mark keeps its aspect", abs(bh - fc.MAX_MARK_WIDTH / 4.0) < 1e-3, f"{bw}x{bh}")

    # Folded mono (tint refused): the mono layer doubles as the color layer.
    folded = _compose(color=None)
    root = ET.fromstring(folded)
    classes = [el.get("class", "") for el in root]
    check("folded mono carries both mark classes",
          any(fc.MARK_MONO_CLASS in c and fc.MARK_COLOR_CLASS in c for c in classes), str(classes))


def test_validate_negative() -> None:
    data = _compose(color=None)
    bad = data.replace(b"<svg", b"<svg", 1).replace(
        b'viewBox="0 0', b'viewBox="9 9', 1
    )
    check("unpinned viewBox flagged", any("viewBox" in p for p in fc.validate_composed(bad)))
    bad = data.replace(b"<path", b"<script/><path", 1)
    check("script element flagged", any("script" in p for p in fc.validate_composed(bad)))
    bad = data.replace(b'fill="currentColor"', b'fill="https://x.example/a"', 1)
    check("external paint flagged", any("external" in p or "allowlist" in p for p in fc.validate_composed(bad)))
    bad = data.replace(b"fill=\"currentColor\"", b"fill=\"plum\"", 1)
    check("unspellable paint flagged", any("allowlist" in p for p in fc.validate_composed(bad)))
    check("wrong word flagged", any("data-combine-word" in p for p in fc.validate_composed(data, expect_word="X")))


# ── end-to-end with local fixtures ───────────────────────────────────────────


def _fixture_logos(tmp: Path) -> Path:
    logos = tmp / "logos"
    (logos / "combine-src").mkdir(parents=True)
    (logos / "combine-src" / "mark.svg").write_bytes(MARK_SVG)
    (logos / "combine-src" / "color.svg").write_bytes(COLOR_SVG)
    (logos / "combine-src" / "text.svg").write_bytes(TEXT_SVG)
    entry = {
        "source": "lobehub", "source_url": "https://example.invalid/x.svg", "mode": "mono",
        "license": "test", "brand_color": "10A37F",
        "combine": {
            "word": "T", "license": "test",
            "local_mark": "combine-src/mark.svg",
            "local_color": "combine-src/color.svg",
            "local_text": "combine-src/text.svg",
        },
    }
    manifest = {"fixture": entry}
    rewrite_manifest(logos, manifest)
    return logos


def test_end_to_end() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="combine-test-"))
    try:
        logos = _fixture_logos(tmp)
        entries = load_manifest(logos)
        budget = {"total": 1 << 20}
        result, reason = fc.compose_provider("fixture", entries["fixture"], logos, budget)
        check("e2e composes", result is not None and reason is None, str(reason))
        data = result.data
        check("e2e output validates", fc.validate_composed(data, expect_word="T") == [])
        # cmd_fetch creates the output dir; the direct compose call does not.
        (logos / "combine").mkdir(exist_ok=True)
        target = fc.combine_path(logos, "fixture")
        target.write_bytes(data)
        entries["fixture"]["combine"]["sha256"] = fc.sha256_file(target)
        entries["fixture"]["combine"]["fetched_at"] = "2026-01-01"
        rewrite_manifest(logos, entries)
        reparsed = tomllib.loads((logos / "manifest.toml").read_text())
        check("combine table survives the rewrite as a sub-table",
              reparsed["fixture"]["combine"]["word"] == "T"
              and reparsed["fixture"]["combine"]["sha256"])
        check("load_manifest accepts the rewritten manifest", "fixture" in load_manifest(logos))
        code = fc.cmd_check(logos)
        check("cmd_check green", code == 0, str(code))
        entries["fixture"]["combine"]["sha256"] = "0" * 64
        rewrite_manifest(logos, entries)
        code = fc.cmd_check(logos)
        check("cmd_check flags drift", code == 1, str(code))

        # A missing LOCAL piece is a broken override: a loud failure, never a
        # silent skip.
        entries = load_manifest(logos)
        entries["fixture"]["combine"]["local_text"] = "combine-src/absent.svg"
        result, reason = fc.compose_provider("fixture", entries["fixture"], logos, {"total": 1 << 20})
        check("missing local text fails loudly", result is None and reason is not None
              and "FAILED" in reason, str(reason))
        entries["fixture"]["combine"]["local_mark"] = "combine-src/absent.svg"
        entries["fixture"]["combine"]["local_text"] = "combine-src/text.svg"
        result, reason = fc.compose_provider("fixture", entries["fixture"], logos, {"total": 1 << 20})
        check("missing local mark fails loudly", result is None and reason is not None
              and "FAILED" in reason, str(reason))

        # The network 404 path: a missing -text variant is a documented SKIP
        # (the mono tree stays untouched; the gap stays visible in the run
        # report). download_piece is stubbed, so no network is touched.
        entries["fixture"]["combine"].pop("local_mark")
        entries["fixture"]["combine"].pop("local_text")
        entries["fixture"]["combine"]["lobehub_mark"] = "stubmark"
        entries["fixture"]["combine"]["lobehub_text"] = "stubtext"

        def fake_download(url: str, budget: dict) -> bytes | None:
            if "-text" in url:
                return None
            return MARK_SVG

        real_download = fc.download_piece
        fc.download_piece = fake_download
        try:
            result, reason = fc.compose_provider(
                "fixture", entries["fixture"], logos, {"total": 1 << 20}
            )
        finally:
            fc.download_piece = real_download
        check("missing -text variant skips (not fails)", result is None and reason is not None
              and "FAILED" not in reason and "skipped" in reason, str(reason))

        # Tint refused (FF6900 is below 3.0 on white): the color layer folds
        # into the mono layer, which then carries both classes.
        entry_tint = {
            "source": "lobehub", "source_url": "https://example.invalid/x.svg", "mode": "mono",
            "license": "test", "brand_color": "FF6900",
            "combine": {
                "word": "T", "license": "test",
                "local_mark": "combine-src/mark.svg",
                "local_text": "combine-src/text.svg",
            },
        }
        result, reason = fc.compose_provider("fixture", entry_tint, logos, {"total": 1 << 20})
        check("refused tint composes folded", result is not None and "folded into mono" in result.note,
              str(reason))
        root = ET.fromstring(result.data)
        classes = [el.get("class", "") for el in root]
        check("folded classes on the mono layer",
              any(fc.MARK_MONO_CLASS in c and fc.MARK_COLOR_CLASS in c for c in classes), str(classes))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_readability_floors() -> None:
    """S2 floor pins: the two contrast constants must each do their own job,
    proven through the FULL compose pipeline (compose_provider), not by
    poking resolve_colorwork directly. The pipeline fixtures run BEFORE the
    constants-equality pin so a constant mutation dies here on the
    pipeline-level preserve/flip assertions (the exact gap the review
    found: the old suite survived MIN_CONTRAST→99 with 48/48 green).
    The preserve side is the mutation evidence: with the old single 3.0
    floor (or any floor ≥ 2.39) the teal fixture silently lost its brand
    color and every pin stayed green."""
    coral = fc.contrast(fc.parse_paint("#D97757")[1], fc.LIGHT_FABRIC)  # ≈ 3.12
    teal = fc.contrast(fc.parse_paint("#22B8CD")[1], fc.LIGHT_FABRIC)  # ≈ 2.38
    check("fixture brackets the art floor", 1.5 < teal < 3.0 <= coral,
          f"teal {teal:.2f} coral {coral:.2f}")

    def colorwork(main_fill: str) -> bytes:
        return (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            b'<path d="M2 2h9v9H2z" fill="' + main_fill.encode() + b'"/>'
            b'<path d="M13 2h9v9h-9z" fill="' + main_fill.encode() + b'"/>'
            b'<path d="M2 13h9v9H2z" fill="#1783FF"/></svg>'
        )

    tmp = Path(tempfile.mkdtemp(prefix="combine-floors-"))
    try:
        logos = tmp / "logos"
        (logos / "combine-src").mkdir(parents=True)
        (logos / "combine-src" / "mark.svg").write_bytes(MARK_SVG)
        (logos / "combine-src" / "text.svg").write_bytes(TEXT_SVG)

        def entry(main_fill: str) -> dict:
            color_path = f"combine-src/color-{main_fill.lstrip('#').lower()}.svg"
            (logos / color_path).write_bytes(colorwork(main_fill))
            return {
                "source": "lobehub", "source_url": "https://example.invalid/x.svg",
                "mode": "mono", "license": "test", "brand_color": "1783FF",
                "combine": {
                    "word": "T", "license": "test",
                    "local_mark": "combine-src/mark.svg",
                    "local_color": color_path,
                    "local_text": "combine-src/text.svg",
                },
            }

        for name, fill, ratio in (("claude coral", "#D97757", coral),
                                  ("perplexity teal", "#22B8CD", teal)):
            result, reason = fc.compose_provider(
                "fixture", entry(fill), logos, {"total": 1 << 20}
            )
            check(f"{name} composes", result is not None and reason is None, str(reason))
            check(f"{name} brand fill PRESERVED ({ratio:.2f} clears the art floor)",
                  fill.encode() in result.data,
                  f"{fill} was rewritten — the art floor moved above {ratio:.2f}")
            note = result.note
            check(f"{name} note reports a kept fill", "clears contrast" in note, note)

        # The flip side through the same pipeline: the pale fixture (#F6D85A,
        # 1.41 — BELOW the art floor) must still be rewritten.
        result, reason = fc.compose_provider(
            "fixture", entry("#F6D85A"), logos, {"total": 1 << 20}
        )
        check("pale fixture composes", result is not None and reason is None, str(reason))
        check("pale main fill flipped (below the art floor)",
              b"#F6D85A" not in result.data and b"currentColor" in result.data)

        # Tint floor, APPLIED side: a vendor without colorwork whose brand
        # hex clears TINT_MIN_CONTRAST ships a real tinted color layer.
        tint_entry = {
            "source": "lobehub", "source_url": "https://example.invalid/x.svg",
            "mode": "mono", "license": "test", "brand_color": "10A37F",
            "combine": {
                "word": "T", "license": "test",
                "local_mark": "combine-src/mark.svg",
                "local_text": "combine-src/text.svg",
            },
        }
        result, reason = fc.compose_provider("fixture", tint_entry, logos, {"total": 1 << 20})
        check("tint fixture composes", result is not None and reason is None, str(reason))
        root = ET.fromstring(result.data)
        color_layer = [el for el in root if fc.MARK_COLOR_CLASS in el.get("class", "").split()
                       and fc.MARK_MONO_CLASS not in el.get("class", "").split()]
        check("tint applied as its own color layer", len(color_layer) == 1
              and color_layer[0].get("fill") == "#10a37f",
              str(color_layer[0].get("fill") if color_layer else None))

        # Constants-equality pin LAST: the pipeline assertions above are the
        # semantic contract; this pin catches a drifted constant even when a
        # fixture's contrast value happens to sit on the surviving side.
        check("art floor is 1.5", fc.ART_MIN_CONTRAST == 1.5, str(fc.ART_MIN_CONTRAST))
        check("tint floor is 3.0", fc.TINT_MIN_CONTRAST == 3.0, str(fc.TINT_MIN_CONTRAST))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    test_colors()
    test_ink_bounds()
    test_layers()
    test_compose()
    test_validate_negative()
    test_end_to_end()
    test_readability_floors()
    print(f"test_fetch_logos_combines: all green ({PASS} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
