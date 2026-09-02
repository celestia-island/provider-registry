#!/usr/bin/env python3
"""Mirror the `supports_vision` capability flag from model cards into entrypoint entries.

The per-model cards in ``models/<provider>/<id>.toml`` are the registry's source
of truth for model capabilities, including the boolean ``supports_vision``.
This script copies that value onto the matching ``[[entrypoint.models]]`` entry
in ``entrypoint/<provider>/*.toml``, so downstream consumers (e.g. plana's
provider config, which is gaining a ``supports_vision`` field on its model
entries) can read the capability without loading the card files.

* Idempotent: entries that already carry ``supports_vision`` are never touched;
  re-running the script reports zero changes.
* Text-level insertion only: the field is inserted right after the entry's
  ``id = "..."`` line (extended entries keep it after ``max_output_tokens``),
  matching the field order used by fully-expanded entries elsewhere in the
  registry. No file is re-serialized, so diffs stay minimal and formatting is
  preserved.
* Card lookup is by model ``id``, preferring the card in the matching provider
  directory (``models/<provider>/``) and falling back to any card with the same
  id. Entries whose id has no card are left without the field: downstream
  structs default the flag to ``false`` (non-vision), a safe default.

Usage:
    python3 scripts/sync_supports_vision.py            # apply changes
    python3 scripts/sync_supports_vision.py --check    # dry run (exit 1 if drift)
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Simple scalar fields that may precede `supports_vision` in an expanded entry.
PREAMBLE_FIELDS = {"id", "name", "context_window", "max_output_tokens"}

HEADER_RE = re.compile(r"^\[")  # any table/array-of-table header at column 0
ID_LINE_RE = re.compile(r'^id\s*=\s*"([^"]+)"\s*$')
PREAMBLE_LINE_RE = re.compile(r"^(name|context_window|max_output_tokens)\s*=")
FLAG_LINE_RE = re.compile(r"^supports_vision\s*=", re.MULTILINE)


def load_cards() -> dict[str, list[tuple[str, bool | None]]]:
    """Index model cards by id -> [(provider_dir, supports_vision), ...]."""
    cards: dict[str, list[tuple[str, bool | None]]] = {}
    missing_flag: list[str] = []
    for card in sorted((REPO_ROOT / "models").glob("*/*.toml")):
        with card.open("rb") as fh:
            data = tomllib.load(fh)
        model = data.get("model") or {}
        mid = model.get("id")
        if not mid:
            continue
        sv = model.get("supports_vision")
        if sv is None:
            missing_flag.append(str(card.relative_to(REPO_ROOT)))
        cards.setdefault(mid, []).append((card.parent.name, sv))
    return cards, missing_flag


def resolve_flag(cards: dict[str, list[tuple[str, bool | None]]],
                 mid: str, provider_dir: str) -> tuple[bool | None, str]:
    """Resolve supports_vision for an entry id, provider-scoped first."""
    cands = cards.get(mid)
    if not cands:
        return None, "no-card"
    scoped = [sv for p, sv in cands if p == provider_dir and sv is not None]
    if scoped:
        if len(set(scoped)) > 1:
            print(f"  WARN: provider-scoped cards disagree for {mid!r}: {scoped}")
        return scoped[0], "scoped"
    values = [sv for _, sv in cands if sv is not None]
    if not values:
        return None, "no-flag-in-card"
    if len(set(values)) > 1:
        print(f"  WARN: global cards disagree for {mid!r}: "
              f"{[(p, sv) for p, sv in cands]}; picked {values[0]}")
    return values[0], "global"


def plan_changes(cards: dict[str, list[tuple[str, bool | None]]]):
    """Return list of (path, line_index, text) insertions and skip stats."""
    insertions: list[tuple[Path, int, str]] = []
    already = 0
    skipped: list[tuple[str, str]] = []  # (path, id) reason "no-card"
    for toml_file in sorted((REPO_ROOT / "entrypoint").glob("*/*.toml")):
        provider_dir = toml_file.parent.name
        lines = toml_file.read_text(encoding="utf-8").splitlines(keepends=True)
        # Split into blocks on table/array-of-table headers at column 0.
        headers = [i for i, ln in enumerate(lines) if HEADER_RE.match(ln)]
        headers.append(len(lines))
        for bi in range(len(headers) - 1):
            start, end = headers[bi], headers[bi + 1]
            if not re.match(r"^\[\[entrypoint\.models\]\]\s*$", lines[start].strip()):
                continue
            block_text = "".join(lines[start:end])
            if FLAG_LINE_RE.search(block_text):
                already += 1
                continue
            id_m = next((ID_LINE_RE.match(ln) for ln in lines[start:end]
                         if ID_LINE_RE.match(ln)), None)
            if not id_m:
                continue
            mid = id_m.group(1)
            # Insertion anchor: after id + any preamble fields (name,
            # context_window, max_output_tokens), mirroring the field order of
            # fully-expanded entries.
            anchor = start + next(k for k, ln in enumerate(lines[start:end])
                                  if ID_LINE_RE.match(ln))
            j = anchor + 1
            while j < end and PREAMBLE_LINE_RE.match(lines[j].strip()):
                anchor = j
                j += 1
            flag, how = resolve_flag(cards, mid, provider_dir)
            if flag is None:
                skipped.append((str(toml_file.relative_to(REPO_ROOT)), mid))
                continue
            insertions.append((toml_file, anchor + 1,
                               f"supports_vision = {'true' if flag else 'false'}\n"))
    return insertions, already, skipped


def apply(insertions: list[tuple[Path, int, str]]) -> list[Path]:
    """Apply insertions grouped per file; re-validate with tomllib before write."""
    changed: list[Path] = []
    by_file: dict[Path, list[tuple[int, str]]] = {}
    for path, idx, text in insertions:
        by_file.setdefault(path, []).append((idx, text))
    for path, edits in sorted(by_file.items()):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for idx, text in sorted(edits, key=lambda e: -e[0]):
            lines.insert(idx, text)
        candidate = "".join(lines)
        tomllib.loads(candidate)  # must still parse, else abort this file
        path.write_text(candidate, encoding="utf-8")
        changed.append(path)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="dry run: report what would change, write nothing; "
                             "exit 1 when drift exists")
    args = parser.parse_args()

    cards, missing_flag = load_cards()
    if missing_flag:
        print(f"WARN: {len(missing_flag)} model cards lack supports_vision:")
        for c in missing_flag[:10]:
            print(f"  {c}")

    insertions, already, skipped = plan_changes(cards)
    n_files = len({p for p, _, _ in insertions})
    n_no_card = len(skipped)

    print(f"model cards indexed: {len(cards)}")
    print(f"entries already having supports_vision: {already}")
    print(f"entries to update: {len(insertions)} across {n_files} files")
    print(f"entries skipped (no card): {n_no_card}")
    for rel, mid in skipped[:10]:
        print(f"  no-card: {rel} -> {mid}")

    if args.check:
        print("\n--check: " + ("DRIFT: changes pending (exit 1)"
                               if insertions else "clean (exit 0)"))
        return 1 if insertions else 0

    if not insertions:
        print("\nNothing to do.")
        return 0

    changed = apply(insertions)
    print("\nApplied changes:")
    for p in changed:
        print(f"  + {p.relative_to(REPO_ROOT)}")
    print(f"\nDone. files changed: {len(changed)}, entries updated: {len(insertions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
