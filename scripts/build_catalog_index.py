#!/usr/bin/env python3
"""Flatten the TOML catalog into the one JSON artifact consumers sync.

The TOML tree is the source of truth, but a consumer should not re-implement the
parse (nor the `kind` derivation) on every release: it downloads one file. This
script builds that file, `dist/provider-registry-catalog.json`, and the publish
workflow attaches it to the rolling `catalog-latest` release. The delivery chain
is:

    push master -> .github/workflows/publish-catalog.yml
                -> dist/provider-registry-catalog.json
                -> release asset `provider-registry-catalog.json` of tag `catalog-latest`
                -> gateway.celestia.world/downloads/provider-registry/latest (302)
                -> arona server sync (system_config.pricing.registry.url may override;
                   a compiled-in copy of the same bytes is the offline fallback).

The format is a cross-repository contract, so the field set below is closed —
add a field only when the consumer side is changed in the same wave, and bump
`schema` for anything a consumer cannot ignore:

    {
      "schema": 1,
      "generated_at": "2026-11-04T12:34:56Z",
      "digest": "sha256:<hex>",
      "providers": [{"id", "name", "category", "base_hosts"}],
      "models":    [{"provider", "id", "name", "kind", "input_per_million",
                     "output_per_million", "cached_per_million",
                     "supports_vision", "supports_reasoning",
                     "context_window", "max_output_tokens"}]
    }

Rules that are load-bearing for the consumer side:

* `digest` covers `providers` + `models` only (not `generated_at`), so two builds
  of the same tree produce the same digest. It is the sha256 of the canonical
  UTF-8 JSON (`sort_keys`, no spaces).
* A numeric field the data does not carry is OMITTED, never null: the consumer
  reads "absent" as "no upstream value, follow the fallback". An explicit `0.0`
  in the TOML is a value the data declares (e.g. a $0 output price) and is
  emitted as `0.0`.
* `supports_vision` / `supports_reasoning` are ALWAYS emitted as booleans (a card
  without the key reads as `false`): the consumer merges these flags against its
  own defaults, so "absent" and "false" must never be distinguishable. There is
  no `multimodal` kind — multimodal capability lives in `supports_vision`.
* `NaN` / `Infinity` are REFUSED (TOML accepts them, JSON does not): one such
  price would make the whole published artifact unparseable for every consumer
  while the tree still validates, so the build fails and names the field.
* Negative numbers are REFUSED (a price or a limit cannot be negative; the
  artifact would publish the sign verbatim and the consumer would treat it as a
  credit).
* Floats outside `[1e-4, 1e15)` (and non-zero below it) are REFUSED too. The
  digest is recomputed by consumers, and the two spellings have to agree byte for
  byte: Python writes `1e-05` where `ryu`/`serde_json` writes `0.00001`, so any
  value that Python renders in exponent form is a cross-language digest mismatch
  waiting to happen — which would reject the *whole* document at sync time. Zero
  and ordinary prices are unaffected; the smallest non-zero price in the catalog
  is 0.002.
* `kind` ∈ llm | embedding | image | video | audio | 3d | other, derived in this
  order: `[model].category.generation` (case-insensitive), then `tags`, then the
  id (`embed`), then `llm`.
* `providers[].base_hosts` are the hostnames of `entrypoint/<provider>/*.toml`
  `api.base_url` values, so the consumer can match a configured endpoint URL to
  a registry provider. An operator/custom endpoint ships `base_url = ""` and
  therefore has no host.
* `providers[].category` is the `registry.toml` group (`international`,
  `chinese`, `compatible`, …). A provider that has a directory under `models/`
  or `entrypoint/` but no `registry.toml` row is emitted with the id as its
  name and the category `unregistered` (and reported as a warning): every
  `models[].provider` must resolve to a `providers[]` entry, and a real data gap
  must be visible rather than silently dropped.

Ordering is deterministic (providers by id, models by provider then id) so the
artifact diff is useful, and a duplicate `(provider, model id)` fails the build
instead of silently picking one row.

Usage:
    python3 scripts/build_catalog_index.py                     # write dist/
    python3 scripts/build_catalog_index.py --check             # build only, no write
    python3 scripts/build_catalog_index.py --source . --out /tmp/catalog.json

Exit codes: 0 built (or buildable, with --check) / 1 the index cannot be built /
2 the source directory is not a registry root.

TOML is read with the standard library (`tomllib`, Python 3.11+), exactly like
`validate_catalog.py`; nothing here needs a third-party package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
INDEX_FILENAME = "provider-registry-catalog.json"
DEFAULT_OUT = Path("dist") / INDEX_FILENAME

ENTRYPOINT_TOP = "entrypoint"
MODELS_TOP = "models"
REGISTRY_FILE = "registry.toml"
# `search/` is a different capability class (web search, not model serving) and
# has its own registry groups; the model catalog consumers do not sync it.
REGISTRY_EXCLUDED_GROUPS = frozenset({"search"})
UNREGISTERED_CATEGORY = "unregistered"

# `category.generation` -> artifact `kind`. Anything else falls through to the
# tag heuristics below.
GENERATION_KINDS = {
    "chat": "llm",
    "image": "image",
    "video": "video",
    "audio": "audio",
    "model3d": "3d",
}

# Ordered: the first group carrying a matching tag wins. Order mirrors the
# contract (embedding, image, 3d) with video ahead of audio so a model tagged
# both (`video_generation` + `audio_video`, MiniMax-H3) presents as video.
TAG_KINDS = (
    ("image", ("image", "image_generation", "image_edit", "text_to_image")),
    ("3d", ("3d", "3d_generation", "model3d", "text_to_3d")),
    ("video", ("video", "video_generation", "text_to_video", "audio_video")),
    ("audio", ("audio", "audio_generation", "text_to_audio", "tts", "speech", "asr")),
)
EMBEDDING_TAG_PREFIX = "embedding"
EMBEDDING_ID_HINT = "embed"
DEFAULT_KIND = "llm"

# The range in which Python's `json` and other strict serializers (ryu /
# `serde_json`) spell a float identically. Outside it Python switches to exponent
# notation (`1e-05`, `1e+15`) and the digest a consumer recomputes no longer
# matches, so the whole document would be rejected at sync time.
MIN_PLAIN_FLOAT = 1e-4
MAX_PLAIN_FLOAT = 1e15

# Integers above this are refused as well: JSON consumers cannot all represent
# them (JavaScript loses precision beyond 2**53, `i64` consumers reject the
# document), so a huge `context_window` would break parsing, not just the digest.
MAX_PLAIN_INT = 2**53

PROVIDER_REQUIRED = ("id",)
MODEL_REQUIRED = ("id",)


class CatalogIndexError(RuntimeError):
    """The tree cannot be flattened into an artifact (reported, not raised to the user)."""


# ---------------------------------------------------------------------------
# small typed readers: the TOML tree is hand-maintained data, so every field is
# read defensively and a wrong type degrades to "absent" instead of a traceback
# ---------------------------------------------------------------------------


def _table(value) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value, where: str = ""):
    """A finite JSON number, or None when the field is absent (bool is not a number).

    `nan` / `inf` are legal TOML scalars and illegal JSON, so they are refused
    here instead of being serialised: emitting one makes the published artifact
    unparseable for every strict JSON consumer.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise CatalogIndexError(
            f"{where or 'value'}: {value!r} is not a finite number (TOML accepts nan/inf, JSON does not)"
        )
    if value < 0:
        raise CatalogIndexError(
            f"{where or 'value'}: {value!r} is negative — prices and limits cannot be negative, "
            "and the artifact would publish the sign verbatim"
        )
    if isinstance(value, int) and abs(value) >= MAX_PLAIN_INT:
        raise CatalogIndexError(
            f"{where or 'value'}: {value!r} is above 2**53 — JSON consumers cannot represent it "
            "(JavaScript loses precision, i64 readers reject the document)"
        )
    if isinstance(value, float):
        magnitude = abs(value)
        if 0 < magnitude < MIN_PLAIN_FLOAT or magnitude >= MAX_PLAIN_FLOAT:
            raise CatalogIndexError(
                f"{where or 'value'}: {value!r} is outside the range every JSON serializer spells the same way "
                f"(a non-zero float must be within [{MIN_PLAIN_FLOAT:g}, {MAX_PLAIN_FLOAT:g})); Python writes it in "
                "exponent form, other serializers do not, and a consumer recomputing the digest would reject "
                "the whole document at sync time"
            )
    return value


def _boolean(value):
    return value if isinstance(value, bool) else None


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogIndexError(f"{path}: not valid TOML ({exc})") from exc
    except UnicodeDecodeError as exc:
        raise CatalogIndexError(f"{path}: not valid UTF-8 ({exc})") from exc
    except OSError as exc:
        # A missing/unreadable file is a tree defect, not a crash: the caller
        # reports it as "the index cannot be generated".
        raise CatalogIndexError(f"{path}: cannot be read ({exc})") from exc


def _provider_of(path: Path, top: Path) -> str | None:
    """`models/<provider>/x.toml` -> `<provider>`; None when not nested under one."""
    parts = path.relative_to(top).parts
    return parts[0] if len(parts) > 1 else None


def base_host(base_url, where: str = "") -> str | None:
    """`https://api.openai.com/v1` -> `api.openai.com`; blank/relative -> None.

    Operator-supplied endpoints declare `base_url = ""` (their host is chosen at
    setup time), so "no fixed host" is a legitimate state, not a defect.
    """
    text = _text(base_url)
    if text is None:
        return None
    try:
        host = urlsplit(text).hostname
    except ValueError as exc:
        # `urlsplit` raises on some malformed authorities (e.g. `http://[::1/v1`);
        # that is a data defect in the entrypoint, so name it instead of crashing.
        raise CatalogIndexError(f"{where or 'base_url'}: {text!r} is not a parseable URL ({exc})") from exc
    if host is None:
        # A scheme-less string (``api.example.com/v1``) parses to no hostname. Blank
        # is the legitimate "operator supplies the host" state; a non-blank value
        # without a host can only be a typo, and silently shipping an empty
        # ``base_hosts`` would remove the consumer's first lookup key.
        raise CatalogIndexError(
            f"{where or 'base_url'}: {text!r} has no parseable host — write a full URL such as "
            '"https://api.example.com/v1" (a blank base_url is the operator-supplied form)'
        )
    return host


# ---------------------------------------------------------------------------
# kind derivation
# ---------------------------------------------------------------------------


def kind_for(model: dict) -> str:
    """Contract §2: category first, then tags, then the id, then `llm`."""
    category = model.get("category")
    generation = None
    if isinstance(category, dict):
        generation = category.get("generation")
    elif isinstance(category, str):
        generation = category
    kind = GENERATION_KINDS.get((_text(generation) or "").lower())
    if kind is not None:
        return kind

    tags = [tag.strip().lower() for tag in model.get("tags") or [] if isinstance(tag, str)]
    for tag in tags:
        if tag.startswith(EMBEDDING_TAG_PREFIX):
            return "embedding"
    for kind, vocabulary in TAG_KINDS:
        if any(tag in vocabulary for tag in tags):
            return kind

    model_id = (_text(model.get("id")) or "").lower()
    if EMBEDDING_ID_HINT in model_id:
        return "embedding"
    return DEFAULT_KIND


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


def _walk_registry_groups(node, path: tuple[str, ...]):
    """Yield `(group path, rows)` for every array-of-tables in registry.toml.

    The file groups providers by nesting (`international.developers`,
    `chinese.operators`, …) and one top-level array (`compatible`), so the walk
    is structural rather than a hardcoded group list: a new group appears in the
    artifact without touching this script.
    """
    if isinstance(node, list):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_registry_groups(value, path + (key,))


def registered_providers(registry: dict) -> tuple[dict[str, dict], list[str]]:
    """`registry.toml` -> `{id: {name, category}}` plus warnings for unusable rows."""
    providers: dict[str, dict] = {}
    warnings: list[str] = []
    for group_path, rows in _walk_registry_groups(registry, ()):
        if not group_path or group_path[0] in REGISTRY_EXCLUDED_GROUPS:
            continue
        category = group_path[0]
        group = ".".join(group_path)
        for index, row in enumerate(rows):
            where = f"{REGISTRY_FILE}: {group}[{index}]"
            if not isinstance(row, dict):
                warnings.append(f"{where}: not a table, skipped")
                continue
            provider_id = _text(row.get("id"))
            if provider_id is None:
                warnings.append(f"{where}: no provider id, skipped")
                continue
            if provider_id in providers:
                raise CatalogIndexError(
                    f"{where}: duplicate provider id {provider_id!r} (also listed in "
                    f"{providers[provider_id]['category']!r}); the artifact keys providers by id"
                )
            providers[provider_id] = {
                "id": provider_id,
                "name": _text(row.get("name")) or provider_id,
                "category": category,
            }
    if not providers:
        # A zero-yield walk means the registry shape changed under us — never
        # publish an artifact whose provider list silently emptied.
        raise CatalogIndexError(f"{REGISTRY_FILE}: no provider rows found (registry shape changed?)")
    return providers, warnings


def entrypoint_hosts(source: Path) -> tuple[dict[str, set[str]], list[str]]:
    """`entrypoint/<provider>/*.toml` -> `{provider: {hostname, …}}`."""
    hosts: dict[str, set[str]] = {}
    warnings: list[str] = []
    top = source / ENTRYPOINT_TOP
    for path in sorted(top.rglob("*.toml")):
        data = _load_toml(path)
        entry = _table(data.get("entrypoint"))
        provider = _provider_of(path, top) or _text(entry.get("provider_id"))
        if provider is None:
            warnings.append(f"{path}: not inside an entrypoint/<provider>/ directory, skipped")
            continue
        host = base_host(_table(entry.get("api")).get("base_url"), where=f"{path}: [entrypoint.api].base_url")
        if host is not None:
            hosts.setdefault(provider, set()).add(host)
        else:
            # Register the provider even when it has no fixed host, so a
            # models-less operator entry still resolves.
            hosts.setdefault(provider, set())
    return hosts, warnings


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def collect_models(source: Path) -> tuple[list[dict], dict[str, int], list[str]]:
    """`models/<provider>/*.toml` -> rows, kind histogram, warnings."""
    rows: list[dict] = []
    kinds: dict[str, int] = {}
    warnings: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    top = source / MODELS_TOP
    for path in sorted(top.rglob("*.toml")):
        data = _load_toml(path)
        model = data.get("model")
        if not isinstance(model, dict):
            raise CatalogIndexError(f"{path}: missing the [model] table")
        model_id = _text(model.get("id"))
        if model_id is None:
            raise CatalogIndexError(f"{path}: [model].id is missing or blank")
        provider = _provider_of(path, top) or _text(model.get("provider_id"))
        if provider is None:
            raise CatalogIndexError(
                f"{path}: not inside a models/<provider>/ directory and [model].provider_id is blank"
            )
        key = (provider, model_id)
        if key in seen:
            # Two rows for one key would make the artifact depend on file order.
            raise CatalogIndexError(
                f"{path}: duplicate model id {model_id!r} inside provider {provider!r} (also {seen[key]})"
            )
        seen[key] = str(path)

        kind = kind_for(model)
        kinds[kind] = kinds.get(kind, 0) + 1
        row: dict = {"provider": provider, "id": model_id, "name": _text(model.get("name")) or model_id, "kind": kind}

        pricing = _table(model.get("pricing"))
        for field in ("input_per_million", "output_per_million", "cached_per_million"):
            value = _number(pricing.get(field), where=f"{path}: [model.pricing].{field}")
            if value is not None:
                row[field] = value
        # Always emitted, never omitted: a consumer merging these flags against
        # its own defaults must not be able to tell "absent" from "false".
        row["supports_vision"] = _boolean(model.get("supports_vision")) is True
        row["supports_reasoning"] = _boolean(model.get("supports_reasoning")) is True
        for field in ("context_window", "max_output_tokens"):
            value = model.get(field)
            if value is None:
                continue
            # The consumer's field is i64: a float would make it reject the
            # WHOLE document, and this builder is the cross-repo import path
            # (the structural validator is not on it).
            if isinstance(value, bool) or not isinstance(value, int):
                raise CatalogIndexError(
                    f"{path}: [model].{field} is {value!r} — a limit must be an integer "
                    "(a float makes every typed consumer reject the whole document)"
                )
            checked = _number(value, where=f"{path}: [model].{field}")
            if checked is not None:
                row[field] = checked
        rows.append(row)

    if not rows:
        raise CatalogIndexError(f"{MODELS_TOP}/: no model rows found (did the tree move?)")
    rows.sort(key=lambda item: (item["provider"], item["id"]))
    return rows, kinds, warnings


# ---------------------------------------------------------------------------
# artifact
# ---------------------------------------------------------------------------


def index_digest(providers: list[dict], models: list[dict]) -> str:
    """Contract §2: sha256 over the canonical `{providers, models}` JSON.

    `allow_nan=False` is the last line of defence behind `_number`: a document
    that cannot be spelled as strict JSON must not get a digest either.

    Cross-repo note: this digest is recomputed by consumers, so the numbers must
    keep their TOML type (integers stay integers; do not widen them to f64) and
    prices must stay in plain decimal range — Python renders values below 1e-4 in
    exponent form (`1e-05`), which other serializers may spell differently.
    """
    try:
        canonical = json.dumps(
            {"providers": providers, "models": models},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:
        raise CatalogIndexError(f"the catalog cannot be spelled as strict JSON ({exc})") from exc
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generated_at() -> str:
    """`SOURCE_DATE_EPOCH` makes the artifact byte-reproducible when asked."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch:
        try:
            stamp = datetime.fromtimestamp(int(epoch), timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            raise CatalogIndexError(f"SOURCE_DATE_EPOCH={epoch!r} is not a unix timestamp ({exc})") from exc
    else:
        stamp = datetime.now(timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(source: Path) -> tuple[dict, dict]:
    """Build the artifact document plus the statistics its summary needs."""
    registry = _load_toml(source / REGISTRY_FILE)
    providers, warnings = registered_providers(registry)
    hosts, host_warnings = entrypoint_hosts(source)
    models, kinds, model_warnings = collect_models(source)
    warnings += host_warnings + model_warnings

    # Referential integrity: every provider the model/entrypoint trees talk
    # about gets an entry, so `models[].provider` always resolves. Rows the
    # registry has not learned about yet are named after their id and marked
    # `unregistered` instead of being dropped.
    referenced = {row["provider"] for row in models} | set(hosts)
    missing = sorted(referenced - set(providers))
    for provider_id in missing:
        providers[provider_id] = {
            "id": provider_id,
            "name": provider_id,
            "category": UNREGISTERED_CATEGORY,
        }
        warnings.append(
            f"{provider_id}: has a directory under {MODELS_TOP}/ or {ENTRYPOINT_TOP}/ but no {REGISTRY_FILE} row "
            f"(published with category {UNREGISTERED_CATEGORY!r})"
        )

    provider_rows = []
    for provider_id in sorted(providers):
        entry = dict(providers[provider_id])
        entry["base_hosts"] = sorted(hosts.get(provider_id, set()))
        provider_rows.append(entry)

    document = {
        "schema": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "digest": index_digest(provider_rows, models),
        "providers": provider_rows,
        "models": models,
    }
    stats = {
        "models": len(models),
        "providers": len(provider_rows),
        "kinds": kinds,
        "unregistered": missing,
        "warnings": warnings,
    }
    return document, stats


def summarize(document: dict, stats: dict) -> str:
    """One-line summary shared by the human run and `validate_catalog.py`."""
    kinds = ", ".join(f"{kind}={count}" for kind, count in sorted(stats["kinds"].items()))
    return (
        f"{stats['models']} model(s) across {stats['providers']} provider(s); "
        f"kinds: {kinds or 'none'}; digest {document['digest']}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_source() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_registry_root(source: Path) -> bool:
    return (source / MODELS_TOP).is_dir() and (source / ENTRYPOINT_TOP).is_dir() and (source / REGISTRY_FILE).is_file()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the provider-registry catalog index (the artifact arona syncs).",
    )
    parser.add_argument("--source", default=None, help="registry root to read (default: this repository)")
    parser.add_argument("--out", default=None, help=f"artifact path (default: <source>/{DEFAULT_OUT})")
    parser.add_argument(
        "--check",
        action="store_true",
        help="build in memory and report; write nothing (CI/test mode)",
    )
    args = parser.parse_args(argv)

    source = Path(args.source).resolve() if args.source else _default_source()
    if not _is_registry_root(source):
        print(f"catalog-index: {source} does not look like the registry root", file=sys.stderr)
        return 2

    try:
        document, stats = build(source)
    except CatalogIndexError as exc:
        print(f"catalog-index: cannot build the index: {exc}", file=sys.stderr)
        return 1

    for warning in stats["warnings"]:
        print(f"catalog-index: warning: {warning}", file=sys.stderr)
    if args.check:
        print(f"catalog-index: index is buildable — {summarize(document, stats)}")
        return 0

    out = Path(args.out) if args.out else source / DEFAULT_OUT
    try:
        payload = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    except ValueError as exc:
        print(f"catalog-index: cannot serialise the index: {exc}", file=sys.stderr)
        return 1
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    except OSError as exc:
        print(f"catalog-index: cannot write {out}: {exc}", file=sys.stderr)
        return 1
    print(f"catalog-index: wrote {out} ({len(payload.encode('utf-8'))} bytes)")
    print(f"catalog-index: generated_at {document['generated_at']} — {summarize(document, stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
