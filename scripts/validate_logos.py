#!/usr/bin/env python3
"""Structural validation of the `logos/` tree (the upstream vendor-mark cache).

Checks (all offline, stdlib only):

1. Every `logos/manifest.toml` entry has its `logos/<id>.svg`, the committed
   bytes match the manifest `sha256`, and the file IS the normalized form
   (re-normalizing it is a no-op — hand-edits fail here, not in a consumer
   build).
2. Manifest provider ids resolve to a `registry.toml` row (any model-provider
   group; the `compatible` protocol placeholders and the `search` groups are
   not logo carriers).
3. Coverage: every provider that ships an `entrypoint/<id>/` or a
   `models/<id>/` directory has a manifest entry OR is on the LETTER_FALLBACK
   allowlist below — a provider with no usable vector source must be listed
   there DELIBERATELY, not silently absent.
4. `brand_prefixes` on registry rows (the consumer-facing brand words used to
   split a display label into vendor prefix + model version) are well-formed:
   a list of distinct strings, no empties, no slash/pound characters (they are
   matched against label/id stems, never paths).

Usage:
    python3 scripts/validate_logos.py [--root <registry root>]

Exit codes: 0 clean / 1 problems found / 2 layout error.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

from fetch_logos import LOGOS_TOP, MANIFEST_NAME, file_check_problems, load_manifest

ENTRYPOINT_TOP = "entrypoint"
MODELS_TOP = "models"
REGISTRY_FILE = "registry.toml"

# `compatible` rows are protocol placeholders (custom OpenAI/Anthropic/Gemini
# endpoints), not vendors; `search.*` rows are a different capability class
# with no model rosters to mark. Neither carries a logo.
COMPATIBLE_PREFIX = "compatible"

# Providers with an entrypoint/ or models/ dir but NO usable vector source yet
# (verified against the official sites and simple-icons master, 2026-11-10).
# Each keeps the consumer's letter fallback. When a source appears, move the
# provider into logos/manifest.toml and drop it here — this list shrinking is
# the direction of travel.
LETTER_FALLBACK: dict[str, str] = {
    # fireworks.ai has no press kit (/brand and /press are 404); the site
    # favicon is a two-tone mark that is not theme-safe in either mode.
    "fireworks": "no theme-safe vector source; letter fallback",
    # siliconflow.cn's logo-new.svg is a horizontal lockup with a black
    # wordmark; no glyph-only variant is published.
    "silicon": "official asset is a lockup with black wordmark; needs a glyph variant",
    # stepfun's official favicon is a white circle + black grid dual-tone;
    # neither mono (one blob) nor brand mode (mark mutates per theme) is safe.
    "stepfun": "official mark is dual-tone, not theme-safe; needs light/dark variants",
    # stability.ai publishes a white-only horizontal wordmark; illegible at
    # the chip size and unusable on light surfaces without hand-recoloring.
    "stability": "official asset is a white-only wordmark; needs a glyph variant",
    # x.ai's asset ZIP (data.x.ai) is unreachable from this network and the
    # site-embedded wordmark is a wide text mark, illegible at chip size.
    "xai": "official assets unreachable here; wordmark illegible at chip size",
    # tripo3d.ai was unreachable from this node during the 2026-11-10 sweep.
    "tripo": "not researched yet (site unreachable from this node)",
}

# Brand-prefix words are matched against label/id STARTS (case-insensitive),
# so slashes and pounds can never be part of one.
PREFIX_FORBIDDEN = re.compile(r"[#/\\]")


def registry_providers(root: Path) -> dict[str, dict]:
    """All model-provider rows from registry.toml, keyed by id."""
    data = tomllib.loads((root / REGISTRY_FILE).read_text(encoding="utf-8"))
    providers: dict[str, dict] = {}
    for group_name, group in data.items():
        if group_name == COMPATIBLE_PREFIX or group_name == "search":
            continue
        if not isinstance(group, dict):
            continue
        for sub in group.values():
            if not isinstance(sub, list):
                continue
            for row in sub:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    providers[row["id"]] = row
    return providers


def dir_providers(root: Path) -> set[str]:
    """Provider ids that ship an entrypoint/ or models/ directory.

    The `*_compatible` entrypoint directories are protocol placeholders
    (custom OpenAI/Anthropic/Gemini endpoints), not vendors — they never
    carry a logo, same rule as the registry's `compatible` rows.
    """
    ids: set[str] = set()
    for top in (ENTRYPOINT_TOP, MODELS_TOP):
        base = root / top
        if base.is_dir():
            ids.update(
                p.name
                for p in base.iterdir()
                if p.is_dir() and not p.name.startswith(".") and not p.name.endswith("_compatible")
            )
    return ids
    return ids


def validate(root: Path) -> list[str]:
    problems: list[str] = []
    logos_dir = root / LOGOS_TOP
    if not (logos_dir / MANIFEST_NAME).is_file():
        return [f"{logos_dir / MANIFEST_NAME} is missing"]

    entries = load_manifest(logos_dir)
    # 1: file-level audit (presence, manifest hashes, normalization
    # idempotency — a hand-edited SVG fails HERE, not in a consumer build).
    problems.extend(file_check_problems(logos_dir, entries))

    providers = registry_providers(root)
    if not providers:
        problems.append("registry.toml: no model-provider rows parsed")

    # 2: manifest ids resolve to a real provider — a registry.toml row OR a
    # models/-only directory (meshy / meta / runway ship model dirs without a
    # registry row and are rendered by consumers all the same). Anything else
    # is a typo.
    dir_ids = dir_providers(root)
    for provider, entry in entries.items():
        if provider not in providers and provider not in dir_ids:
            problems.append(
                f"manifest [{provider}]: id has no registry.toml row and no"
                " entrypoint//models directory (compatible/search ids carry no logo)"
            )
        if entry.get("mode") == "mono" and not entry.get("brand_color"):
            problems.append(
                f"manifest [{provider}]: mono mode should record brand_color"
                " (it drives the consumer chip tint)"
            )

    # 3: coverage — every directory-provider is deliberate (logo or fallback).
    unlisted = sorted(dir_ids.difference(entries.keys(), LETTER_FALLBACK.keys()))
    for provider in unlisted:
        problems.append(
            f"provider '{provider}' has no logos/manifest.toml entry and is not"
            " on validate_logos.py's LETTER_FALLBACK allowlist — pick one"
        )
    stale = sorted(set(LETTER_FALLBACK).difference(dir_providers(root)))
    for provider in stale:
        problems.append(
            f"LETTER_FALLBACK names '{provider}', which ships no entrypoint/ or"
            " models/ directory anymore — drop the row"
        )

    # 4: brand_prefixes shape (registry rows are the consumer-facing source).
    for provider, row in providers.items():
        prefixes = row.get("brand_prefixes")
        if prefixes is None:
            continue
        if not isinstance(prefixes, list) or not all(isinstance(p, str) for p in prefixes):
            problems.append(f"registry.toml [{provider}]: brand_prefixes must be a list of strings")
            continue
        if any(not p.strip() or PREFIX_FORBIDDEN.search(p) for p in prefixes):
            problems.append(
                f"registry.toml [{provider}]: brand_prefixes carry empty or path-like words"
            )
        lowered = [p.lower() for p in prefixes]
        if len(set(lowered)) != len(lowered):
            problems.append(f"registry.toml [{provider}]: brand_prefixes contain duplicates")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent,
        help="registry root (default: the checkout this script lives in)",
    )
    args = parser.parse_args(argv)

    if not (args.root / REGISTRY_FILE).is_file():
        print(f"{args.root / REGISTRY_FILE} is missing — not a registry root", file=sys.stderr)
        return 2

    problems = validate(args.root)
    for problem in problems:
        print(f"[FAIL] {problem}", file=sys.stderr)
    if problems:
        print(f"validate_logos: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("validate_logos: logos tree is consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
