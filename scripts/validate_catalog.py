#!/usr/bin/env python3
"""Structural validator for the provider-registry data files.

The catalog is data, not code: 60+ entrypoint configs and 1100+ model rows that
consumers (plana / shittim-chest / entelecheia) read directly. A typo in a key
name or a missing `provider_id` does not fail loudly anywhere today — the
consumer just silently sees a smaller catalog. This script is the gate.

Checks (stdlib only, `tomllib`):

1. every `*.toml` under `entrypoint/`, `models/` and `descriptors/` parses;
2. every model declares `[model]` with `id`, `name`, `provider_id`, integer
   `context_window` / `max_output_tokens`, and — when present — a `pricing`
   table with numeric `input_per_million` / `output_per_million`;
3. every entrypoint declares `[entrypoint]` with `id`, `provider_id`, an `api`
   table carrying `protocol` and `base_url`, and a `name` table with an `en`
   label;
4. the `provider_id` of a file equals the provider directory it lives in
   (`models/<provider>/…`, `entrypoint/<provider>/…`) — the catalog is grouped
   by provider and consumers index it by that grouping;
5. entrypoint ids are unique across `entrypoint/`; model ids are unique inside a
   provider directory. (A model id MAY repeat across provider directories — the
   same model is genuinely served by several providers with different limits,
   e.g. `glm-5` under `qwen/` and `zhipu_glm/`.)

`descriptors/` gets the full MDD schema check from `scripts/validate_descriptors.py`;
here it only has to parse, so one missing validator cannot hide a broken file.

Usage: python3 scripts/validate_catalog.py [root]   (default: repository root)
Exit codes: 0 clean / 1 violations (each printed as `path: reason`).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REQUIRED_MODEL_KEYS = ("id", "name", "provider_id")
REQUIRED_MODEL_INTS = ("context_window", "max_output_tokens")
REQUIRED_PRICING_KEYS = ("input_per_million", "output_per_million")
REQUIRED_ENTRYPOINT_KEYS = ("id", "provider_id", "api", "name")


def _load(path: Path, problems: list[str]) -> dict | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        problems.append(f"{path}: not valid TOML ({exc})")
    except UnicodeDecodeError as exc:
        problems.append(f"{path}: not valid UTF-8 ({exc})")
    return None


def _provider_dir(path: Path, root: Path, top: str) -> str | None:
    """`models/<provider>/x.toml` -> `<provider>`; None when the file is not
    nested under a provider directory."""
    rel = path.relative_to(root / top)
    return rel.parts[0] if len(rel.parts) > 1 else None


def validate(root: Path) -> list[str]:
    problems: list[str] = []

    # 1) everything parses (including descriptors, which get the schema check
    #    in their own validator).
    parsed: dict[str, dict] = {}
    for top in ("entrypoint", "models", "descriptors"):
        for path in sorted((root / top).rglob("*.toml")):
            data = _load(path, problems)
            if data is not None:
                parsed[str(path)] = data

    # 2) + 4) + 5) models
    model_ids: dict[tuple[str, str], str] = {}
    for path in sorted((root / "models").rglob("*.toml")):
        data = parsed.get(str(path))
        if data is None:
            continue
        model = data.get("model")
        if not isinstance(model, dict):
            problems.append(f"{path}: missing the [model] table")
            continue
        for key in REQUIRED_MODEL_KEYS:
            if not isinstance(model.get(key), str) or not model[key].strip():
                problems.append(f"{path}: [model].{key} is missing or blank")
        for key in REQUIRED_MODEL_INTS:
            if not isinstance(model.get(key), int) or isinstance(model.get(key), bool):
                problems.append(f"{path}: [model].{key} must be an integer")
        pricing = model.get("pricing")
        if pricing is not None:
            if not isinstance(pricing, dict):
                problems.append(f"{path}: [model.pricing] must be a table")
            else:
                for key in REQUIRED_PRICING_KEYS:
                    if pricing.get(key) is not None and not isinstance(pricing[key], (int, float)):
                        problems.append(f"{path}: [model.pricing].{key} must be a number")
        provider = _provider_dir(path, root, "models")
        declared = model.get("provider_id")
        if provider is not None and declared is not None and declared != provider:
            problems.append(
                f"{path}: [model].provider_id {declared!r} does not match directory {provider!r}"
            )
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id:
            key = (provider or "", model_id)
            if key in model_ids:
                problems.append(
                    f"{path}: duplicate model id {model_id!r} inside provider {(provider or '-')!r} "
                    f"(also {model_ids[key]})"
                )
            else:
                model_ids[key] = str(path)

    # 3) + 4) + 5) entrypoints
    entrypoint_ids: dict[str, str] = {}
    for path in sorted((root / "entrypoint").rglob("*.toml")):
        data = parsed.get(str(path))
        if data is None:
            continue
        entry = data.get("entrypoint")
        if not isinstance(entry, dict):
            problems.append(f"{path}: missing the [entrypoint] table")
            continue
        provider = _provider_dir(path, root, "entrypoint")
        for key in REQUIRED_ENTRYPOINT_KEYS:
            if key not in entry:
                problems.append(f"{path}: [entrypoint].{key} is missing")
        api = entry.get("api")
        if isinstance(api, dict):
            protocol = api.get("protocol")
            if not isinstance(protocol, str) or not protocol.strip():
                problems.append(f"{path}: [entrypoint.api].protocol is missing or blank")
            base_url = api.get("base_url")
            if not isinstance(base_url, str):
                problems.append(f"{path}: [entrypoint.api].base_url is missing (use \"\" for custom endpoints)")
            elif not base_url.strip() and provider and not provider.endswith("_compatible"):
                # Custom/operator endpoints are declared blank on purpose; a
                # concrete provider with no base_url is a defect.
                problems.append(f"{path}: [entrypoint.api].base_url is blank for a concrete provider")
        elif api is not None:
            problems.append(f"{path}: [entrypoint.api] must be a table")
        name = entry.get("name")
        if isinstance(name, dict) and not name.get("en"):
            problems.append(f"{path}: [entrypoint.name].en is missing (the un-localized label)")
        declared = entry.get("provider_id")
        if provider is not None and isinstance(declared, str) and declared != provider:
            problems.append(
                f"{path}: [entrypoint].provider_id {declared!r} does not match directory {provider!r}"
            )
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and entry_id:
            if entry_id in entrypoint_ids:
                problems.append(f"{path}: duplicate entrypoint id {entry_id!r} (also {entrypoint_ids[entry_id]})")
            else:
                entrypoint_ids[entry_id] = str(path)

    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent
    if not (root / "models").is_dir() or not (root / "entrypoint").is_dir():
        print(f"validate-catalog: {root} does not look like the registry root", file=sys.stderr)
        return 2
    problems = validate(root)
    if problems:
        for line in problems:
            print(f"validate-catalog: {line}", file=sys.stderr)
        print(f"validate-catalog: {len(problems)} violation(s)", file=sys.stderr)
        return 1
    models = len(list((root / "models").rglob("*.toml")))
    entrypoints = len(list((root / "entrypoint").rglob("*.toml")))
    print(f"validate-catalog: clean ({models} model file(s), {entrypoints} entrypoint file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
