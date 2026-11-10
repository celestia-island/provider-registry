#!/usr/bin/env python3
"""Behavioral pins for the `logos/` audit in scripts/validate_logos.py.

Run: python3 scripts/test_validate_logos.py  (exit 0 = all green)

The logo tree is vendored into consumers (shittim-chest generates a TS module
from it), so a hand-edited SVG or a manifest hash drift must fail HERE instead
of shipping a quietly broken vendor mark. Every check gets a positive pin (the
fixture or real tree passes) and a negative one (a deliberately broken fixture
fails with a message that names the defect).
"""
import shutil
from collections.abc import Iterator
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import fetch_logos as fl  # noqa: E402
import validate_logos as vl  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The real allowlist names real-world providers; fixtures run isolated.
REAL_FALLBACK = dict(vl.LETTER_FALLBACK)

# A minimal well-formed provider SVG: fixed width/height, a <title>, editor
# metadata and two paths — everything normalization must strip or keep.
SAMPLE_SVG = b"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="24" height="24" viewBox="0 0 24 24" inkscape:version="1.3">
  <title>Test Vendor</title>
  <metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></metadata>
  <path d="M2 2h8v8H2z" fill="#4D6BFE"/>
  <inkscape:label>layer one</inkscape:label>
</svg>
"""


class TmpTree:
    """A disposable registry root with the pieces the validator reads."""

    def __init__(self, providers: str, dirs: list[str] | None = None) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="registry-logos-test-"))
        (self.root / "registry.toml").write_text(providers, encoding="utf-8")
        (self.root / "logos").mkdir()
        for name in dirs or []:
            (self.root / "entrypoint" / name).mkdir(parents=True)

    def __enter__(self) -> Path:
        vl.LETTER_FALLBACK = {}
        return self.root

    def __exit__(self, *exc) -> None:
        vl.LETTER_FALLBACK = dict(REAL_FALLBACK)
        shutil.rmtree(self.root, ignore_errors=True)


def manifest(text: str, root: Path) -> None:
    (root / "logos" / "manifest.toml").write_text(text, encoding="utf-8")


def write_logo(root: Path, provider: str, data: bytes, mode: str = "mono") -> str:
    normalized = fl.normalize_svg(data, mode)
    (root / "logos" / f"{provider}.svg").write_bytes(normalized)
    import hashlib

    return hashlib.sha256(normalized).hexdigest()


MANIFEST_ENTRY = """
[provone]
source = "simple-icons"
source_url = "https://example.invalid/icons/provone.svg"
mode = "mono"
license = "CC0-1.0"
brand_color = "4D6BFE"
sha256 = "{sha}"
fetched_at = "2026-11-10"
"""

REGISTRY = """
[[international.developers]]
id = "provone"
name = "Prov One"

[[chinese.developers]]
id = "provtwo"
name = "Prov Two"
brand_prefixes = ["Provtwo", "PW"]
"""


def expect_fail(name: str, problems: list[str], needle: str) -> None:
    if not problems:
        raise AssertionError(f"{name}: expected a failure, got none")
    if not any(needle in p for p in problems):
        raise AssertionError(f"{name}: failure does not mention {needle!r}: {problems}")


def test_normalization() -> None:
    # mono: fill forced to currentColor, metadata stripped, viewBox kept.
    out = fl.normalize_svg(SAMPLE_SVG, "mono")
    text = out.decode("utf-8")
    assert 'fill="currentColor"' in text, "mono mode must set fill=currentColor"
    assert "<title>" not in text, "title must be stripped"
    assert "rdf" not in text and "inkscape" not in text, "editor namespaces must be stripped"
    assert 'viewBox="0 0 24 24"' in text, "viewBox must survive"
    assert "width=" not in text and "height=" not in text, "fixed size must be dropped"
    assert fl.normalize_svg(out, "mono") == out, "normalization must be idempotent"

    # brand: original fills kept.
    brand = fl.normalize_svg(SAMPLE_SVG, "brand").decode("utf-8")
    assert 'fill="#4D6BFE"' in brand, "brand mode must keep original fills"
    assert "currentColor" not in brand, "brand mode must not recolor"

    # viewBox derivation from numeric width/height.
    bare = SAMPLE_SVG.replace(b' viewBox="0 0 24 24"', b"")
    derived = fl.normalize_svg(bare, "mono").decode("utf-8")
    assert 'viewBox="0 0 24 24"' in derived, "viewBox must be derived from width/height"

    # Active content is rejected outright — including CASE VARIANTS: XML
    # keeps `<SCRIPT>` verbatim and the browser that later inlines the SVG
    # folds case, so an uppercase-sensitive gate is a silent XSS bypass
    # (R2 S0). Attributes likewise: `ONLOAD` == `onload` to the browser.
    for evil in (b"<script/>", b"<SCRIPT/>", b"<ScRiPt/>", b"<image/>",
                 b"<style/>", b"<foreignObject/>", b"<FOREIGNOBJECT/>"):
        payload = SAMPLE_SVG.replace(b"<path", evil + b"<path")
        try:
            fl.normalize_svg(payload, "mono")
        except fl.LogoError:
            continue
        raise AssertionError(f"forbidden element {evil!r} was accepted")
    for attr in (b'onload="x()"', b'ONLOAD="x()"', b'OnClick="x()"'):
        payload = SAMPLE_SVG.replace(b"<path d=", b"<path " + attr + b" d=")
        try:
            fl.normalize_svg(payload, "mono")
        except fl.LogoError:
            continue
        raise AssertionError(f"event attribute {attr!r} was accepted")
    # href/src never carry script schemes or external targets, and url()
    # references anywhere must be fragments (internal defs only).
    for bad in (b'<path href="javascript:alert(1)" d="M0 0"/>',
                b'<path xlink:href="data:text/html,x" d="M0 0"/>',
                b'<path fill="url(https://evil.example/x#p)" d="M0 0"/>',
                b'<path clip-path="url(http://evil.example/y)" d="M0 0"/>'):
        payload = SAMPLE_SVG.replace(b'</svg>', bad + b'</svg>')
        try:
            fl.normalize_svg(payload, "mono")
        except fl.LogoError:
            continue
        raise AssertionError(f"unsafe reference {bad[:40]!r} was accepted")
    # SMIL animation is forbidden wholesale — a logo never animates, and
    # `<animate attributeName="href" to="javascript:…"/>` /
    # `<set attributeName="onmouseover" to="…"/>` are documented
    # click-triggered XSS payloads with NO href/src for the scheme gate.
    for smil in (b'<animate attributeName="href" to="javascript:alert(1)"/>',
                 b'<set attributeName="onmouseover" to="alert(1)"/>',
                 b'<animateTransform attributeName="transform" type="rotate"/>',
                 b'<discard begin="0s"/>'):
        payload = SAMPLE_SVG.replace(b'</svg>', smil + b'</svg>')
        try:
            fl.normalize_svg(payload, "mono")
        except fl.LogoError:
            continue
        raise AssertionError(f"SMIL element {smil[:44]!r} was accepted")
    # Scheme detection must survive WHATWG URL control-stripping: the browser
    # removes TAB/LF/CR anywhere inside a URL, so entity-encoded `jav&#09;ascript:`
    # is a live javascript: URL even though the literal never anchors the scheme.
    for sneaky in (b'<path xlink:href="jav&#09;ascript:alert(1)" d="M0 0"/>',
                   b'<path xlink:href="jav&#x0D;ascript:alert(1)" d="M0 0"/>',
                   b'<path href=" &#10;javascript:alert(1)" d="M0 0"/>'):
        payload = SAMPLE_SVG.replace(b'</svg>', sneaky + b'</svg>')
        try:
            fl.normalize_svg(payload, "mono")
        except fl.LogoError:
            continue
        raise AssertionError(f"control-stripped scheme {sneaky[:44]!r} was accepted")
    # …while a plain internal fragment stays legal.
    ok_frag = SAMPLE_SVG.replace(b'</svg>', b'<path href="#only-a-fragment"/></svg>')
    fl.normalize_svg(ok_frag, "mono")
    try:
        fl.normalize_svg(b"x" * (fl.MAX_SVG_BYTES + 1), "mono")
    except fl.LogoError:
        pass
    else:
        raise AssertionError("oversized source was accepted")

    # A mark drawn purely in strokes keeps fill="none" (painting it would
    # turn outlined shapes into solid blobs) but gains the themed stroke.
    stroke_only = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" fill="none">'
                   b'<path d="M1 1h8v8H1z" stroke="#123456" stroke-width="1"/></svg>')
    out = fl.normalize_svg(stroke_only, "mono").decode()
    assert 'fill="none"' in out, "stroke-only mark must keep fill=none"
    assert 'stroke="currentColor"' in out, "stroke must become currentColor"

    # Negative viewBox components are legal SVG and must be accepted.
    negative = fl.normalize_svg(
        SAMPLE_SVG.replace(b'viewBox="0 0 24 24"', b'viewBox="0 -2 24 28"'), "mono"
    )
    assert b'viewBox="0 -2 24 28"' in negative

    # inline-css merges same-name rules per PROPERTY: a later rule adds its
    # declarations without erasing the earlier rule's fill.
    two_rules = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><style>'
                 b'.st0{fill:#ff0000;}.st0{stroke-width:2;}</style>'
                 b'<path class="st0" d="M1 1h8v8"/></svg>')
    merged = fl.normalize_svg(two_rules, "brand", "inline-css").decode()
    assert 'fill="#ff0000"' in merged, "first rule's fill must survive the merge"
    assert 'stroke-width="2"' in merged, "second rule's property must land"


def test_real_tree_validates() -> None:
    # Positive pin: the committed logos tree (manifest + files + coverage +
    # brand_prefixes) validates clean. This is the pin a future edit loosens.
    problems = vl.validate(ROOT)
    assert not problems, f"real tree has problems: {problems}"


def test_manifest_file_drift() -> None:
    with TmpTree(REGISTRY) as root:
        sha = write_logo(root, "provone", SAMPLE_SVG)
        manifest(MANIFEST_ENTRY.format(sha=sha), root)
        assert vl.validate(root) == [], f"clean fixture failed: {vl.validate(root)}"

        # Hand-edit the SVG: the check must name the defect — the hash gate
        # fires first; the normalization gate covers hash-undetected edits.
        target = root / "logos" / "provone.svg"
        target.write_bytes(target.read_bytes().replace(b'M2 2h8', b'M3 3h7'))
        problems = vl.validate(root)
        if not any(("sha256" in p) or ("not the normalized form" in p) for p in problems):
            raise AssertionError(f"hand-edited svg: undetected: {problems}")

    with TmpTree(REGISTRY) as root:
        sha = write_logo(root, "provone", SAMPLE_SVG)
        manifest(MANIFEST_ENTRY.format(sha="0" * 64), root)
        expect_fail("sha drift", vl.validate(root), "sha256")


def test_provider_resolution_and_coverage() -> None:
    with TmpTree(REGISTRY) as root:
        sha = write_logo(root, "ghostvendor", SAMPLE_SVG)
        manifest(MANIFEST_ENTRY.format(sha=sha).replace("[provone]", "[ghostvendor]"), root)
        expect_fail("unknown vendor", vl.validate(root), "no registry.toml row")

    with TmpTree(REGISTRY, dirs=["provone", "provtwo", "acme_compatible"]) as root:
        # provone carries a logo; provtwo has no manifest entry and no
        # fallback row -> named; the compatible placeholder dir must NOT
        # require coverage.
        sha = write_logo(root, "provone", SAMPLE_SVG)
        manifest(MANIFEST_ENTRY.format(sha=sha), root)
        expect_fail("missing coverage", vl.validate(root), "'provtwo' has no")

    with TmpTree(REGISTRY, dirs=["provtwo"]) as root:
        # provtwo on the fallback allowlist: no logo required. provone (a
        # registry row without a dir) carries the tree's one logo entry.
        sha = write_logo(root, "provone", SAMPLE_SVG)
        manifest(MANIFEST_ENTRY.format(sha=sha), root)
        vl.LETTER_FALLBACK["provtwo"] = "test fixture"
        assert vl.validate(root) == [], f"fallback fixture failed: {vl.validate(root)}"
        # A stale fallback row (provider without a dir) is named too.
        vl.LETTER_FALLBACK["ghostdir"] = "stale row"
        expect_fail("stale fallback", vl.validate(root), "no entrypoint/ or")
        del vl.LETTER_FALLBACK["ghostdir"]
        del vl.LETTER_FALLBACK["provtwo"]


def seeded_tree(registry_text: str) -> Iterator[Path]:
    """A registry tree whose logos tree is already valid (manifest + file)."""
    with TmpTree(registry_text) as root:
        sha = write_logo(root, "provone", SAMPLE_SVG)
        manifest(MANIFEST_ENTRY.format(sha=sha), root)
        yield root


def test_brand_prefixes_shape() -> None:
    for root in seeded_tree(REGISTRY + '\n[[chinese.developers]]\nid = "provthree"\nname = "P3"\nbrand_prefixes = ["A", "a"]\n'):
        expect_fail("duplicate prefixes", vl.validate(root), "duplicates")
    for root in seeded_tree(REGISTRY + '\n[[chinese.developers]]\nid = "provthree"\nname = "P3"\nbrand_prefixes = "GLM"\n'):
        expect_fail("non-list prefixes", vl.validate(root), "list of strings")
    for root in seeded_tree(REGISTRY + '\n[[chinese.developers]]\nid = "provthree"\nname = "P3"\nbrand_prefixes = ["GL#/M"]\n'):
        expect_fail("path-like prefix", vl.validate(root), "path-like")


def test_mono_needs_brand_color() -> None:
    # Negative pin for the mono-tint rule: a mono record without brand_color
    # loses the chip tint that keeps the theme-safe mark readable, so the
    # audit must name it (mutation E proved this check had zero coverage).
    with TmpTree(REGISTRY) as root:
        sha = write_logo(root, "provone", SAMPLE_SVG)
        manifest(
            MANIFEST_ENTRY.format(sha=sha).replace('brand_color = "4D6BFE"\n', ""),
            root,
        )
        expect_fail("mono without brand_color", vl.validate(root), "mono mode should record brand_color")


def test_rewrite_roundtrip() -> None:
    # The rewrite owns the DERIVED manifest fields; the hand-written header
    # and unknown fields must survive it in TOML-valid spelling, and two
    # rewrites in a row must be byte-identical (no blank-line inflation).
    import tomllib

    with TmpTree(REGISTRY) as root:
        sha = write_logo(root, "provone", SAMPLE_SVG)
        (root / "logos" / "manifest.toml").write_text(
            "# hand note 2026-01-01\n"
            "# pinned simple-icons 16.37.0\n"
            "\n"
            + MANIFEST_ENTRY.format(sha=sha)
            + 'note = "keep me"\nflag = true\ntags = ["a", "b"]\nmeta = { x = 1, y = "z" }\n',
            encoding="utf-8",
        )
        fl.rewrite_manifest(root / "logos", fl.load_manifest(root / "logos"))
        text = (root / "logos" / "manifest.toml").read_text(encoding="utf-8")
        # Header preserved verbatim, trailing blanks normalized (no growth).
        assert text.startswith("# hand note 2026-01-01\n# pinned simple-icons 16.37.0\n"), text[:200]
        # Re-parseable: unknown fields round-trip with their values intact.
        reparsed = tomllib.loads(text)
        assert reparsed["provone"]["note"] == "keep me"
        assert reparsed["provone"]["flag"] is True
        assert reparsed["provone"]["tags"] == ["a", "b"]
        assert reparsed["provone"]["meta"] == {"x": 1, "y": "z"}
        assert "note = " in text and "'keep me'" not in text, "must be TOML-quoted, not Python repr"
        # Idempotent: a second rewrite changes nothing.
        fl.rewrite_manifest(root / "logos", fl.load_manifest(root / "logos"))
        assert (root / "logos" / "manifest.toml").read_text(encoding="utf-8") == text

    # A None anywhere in an unknown value has no TOML spelling: dropped with
    # a loud warning, never written as `null` (which would poison tomllib).
    problems: list[str] = []
    assert fl._toml_unknown("meta", {"n": None}, problems) is None
    assert problems and "DROPPED" in problems[0]
    # A table renders as an inline table (= syntax), not JSON colons.
    assert fl._toml_unknown("meta", {"x": 1, "y": "z"}, []) == '{x = 1, y = "z"}'

    # Escaping corner cases (R3 round: legal-but-tricky TOML inputs must
    # re-serialize to VALID bytes, never silently reshape): quoted keys,
    # dotted keys, quoted nested keys, control characters in values.
    with TmpTree(REGISTRY) as root:
        sha = write_logo(root, "provone", SAMPLE_SVG)
        (root / "logos" / "manifest.toml").write_text(
            MANIFEST_ENTRY.format(sha=sha)
            + "'a\"b' = 1\n"
            + "'my.note' = \"hello\"\n"
            + "tricky = \"line one\\nline two\\u007f\"\n"
            + "meta2 = { inner = { 'x\"y' = 1 } }\n",
            encoding="utf-8",
        )
        fl.rewrite_manifest(root / "logos", fl.load_manifest(root / "logos"))
        text = (root / "logos" / "manifest.toml").read_text(encoding="utf-8")
        # Re-parseable, and the KEY SHAPES survive: `a"b` stays one key,
        # `my.note` stays ONE key (a bare rewrite would parse it as the
        # dotted key my.note and silently reshape it into a `my` table).
        reparsed = tomllib.loads(text)
        assert reparsed["provone"].get('a"b') == 1
        assert reparsed["provone"].get("my.note") == "hello"
        assert "my" not in reparsed["provone"], "dotted key must stay quoted, not reshape"
        assert reparsed["provone"]["tricky"] == "line one\nline two\x7f"
        assert reparsed["provone"]["meta2"] == {"inner": {'x"y': 1}}
        # Idempotent.
        fl.rewrite_manifest(root / "logos", fl.load_manifest(root / "logos"))
        assert (root / "logos" / "manifest.toml").read_text(encoding="utf-8") == text


def test_download_policy_refusal_does_not_retry() -> None:
    # An oversized source is a POLICY refusal, not a transport failure: it
    # must raise immediately (one attempt), not after MAX_RETRIES network
    # round-trips with a RuntimeError masking the real reason.
    import urllib.request

    calls: list[int] = []

    class FakeOversized:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, limit: int) -> bytes:
            return b"x" * limit

    def fake_urlopen(request, timeout=None):
        calls.append(1)
        return FakeOversized()

    original = fl.urllib.request.urlopen
    fl.urllib.request.urlopen = fake_urlopen
    try:
        try:
            fl.download("https://example.invalid/logo.svg", {"total": fl.MAX_TOTAL_BYTES})
        except fl.LogoError as exc:
            assert "exceeds" in str(exc), f"wrong refusal: {exc}"
        else:
            raise AssertionError("oversized download must fail with LogoError")
    finally:
        fl.urllib.request.urlopen = original
    assert len(calls) == 1, f"policy refusal must not retry (attempted {len(calls)}x)"


def main() -> int:
    test_normalization()
    test_real_tree_validates()
    test_manifest_file_drift()
    test_provider_resolution_and_coverage()
    test_brand_prefixes_shape()
    test_mono_needs_brand_color()
    test_rewrite_roundtrip()
    test_download_policy_refusal_does_not_retry()
    print("test_validate_logos: all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
