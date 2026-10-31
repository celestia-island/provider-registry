#!/usr/bin/env python3
"""Structural validator for the provider-registry data files.

The catalog is data, not code: 60+ entrypoint configs and 1100+ model rows that
consumers (plana / shittim-chest / entelecheia) read directly. A typo in a key
name or a missing `provider_id` does not fail loudly anywhere today — the
consumer just silently sees a smaller catalog. This script is the gate.

Checks (stdlib only, `tomllib`):

1. every `*.toml` under `entrypoint/`, `models/`, `descriptors/` and `search/`
   parses;
2. every model declares `[model]` with `id`, `name`, `provider_id`, integer
   `context_window` / `max_output_tokens`, and — when present — a `pricing`
   table with numeric `input_per_million` / `output_per_million`;
3. every entrypoint declares `[entrypoint]` with `id`, `provider_id`, an `api`
   table carrying `protocol` and `base_url`, and a `name` table with an `en`
   label;
4. the `provider_id` of a file equals the provider directory it lives in
   (`models/<provider>/…`, `entrypoint/<provider>/…`, `search/<provider>/…`) —
   the catalog is grouped by provider and consumers index it by that grouping;
5. entrypoint ids are unique across `entrypoint/`; model ids are unique inside a
   provider directory. (A model id MAY repeat across provider directories — the
   same model is genuinely served by several providers with different limits,
   e.g. `glm-5` under `qwen/` and `zhipu_glm/`.)
6. every search entrypoint additionally declares the full search transport —
   `protocol`, `base_url`, `endpoint`, `method`, `auth_type`, `auth_header`,
   `env_var` — with `protocol` ending in `_search_v1` (an LLM chat protocol such
   as `openai_chat_v1` is a defect here), `endpoint` an absolute path, `method`
   `GET`/`POST`, and an auth contract that matches the credential requirement
   (`bearer`/`header` need a header and an env var, `none` needs neither);
7. `[entrypoint.capabilities]`, when present, only carries the four known
   capability flags (`content`, `snippets`, `freshness`, `site_filter`) as
   booleans — an unknown key would be silently ignored by consumers;
8. `registry.toml`'s search groups and the `search/` tree agree in both
   directions: every listed id has a `search/<id>/` directory, every directory
   is listed exactly once, and group spellings are from the known set.

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

SEARCH_TOP = "search"
SEARCH_PROTOCOL_SUFFIX = "_search_v1"
SEARCH_API_KEYS = (
    "protocol",
    "base_url",
    "endpoint",
    "method",
    "auth_type",
    "auth_header",
    "env_var",
)
SEARCH_METHODS = ("GET", "POST")
SEARCH_AUTH_TYPES = ("bearer", "header", "none")
SEARCH_CAPABILITY_KEYS = ("content", "snippets", "freshness", "site_filter")
# The one search protocol whose host is supplied by the operator at runtime, so
# a blank base_url is the correct declaration rather than a defect.
SEARCH_OPERATOR_HOSTS = frozenset({"searxng"})
SEARCH_REGISTRY_GROUPS = (
    "search.aggregators",
    "search.international.developers",
    "search.chinese.developers",
    "search.operators",
    "search.open",
)
SEARCH_REGISTRY_ROW_STRINGS = ("id", "name", "description")
SEARCH_REGISTRY_ROW_BOOLS = ("is_official", "requires_key", "verified")


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


def _check_search_api(path: Path, entry: dict, provider: str | None, problems: list[str]) -> None:
    """(6) + (7): the search-specific transport contract."""
    api = entry.get("api")
    if not isinstance(api, dict):
        if "api" in entry:
            problems.append(f"{path}: [entrypoint.api] must be a table")
        return

    # Absent keys are reported once, here; the per-field checks below only run
    # for keys that are present, so one defect does not produce two messages.
    for key in SEARCH_API_KEYS:
        if key not in api:
            problems.append(
                f"{path}: [entrypoint.api].{key} is missing "
                "(search entrypoints must declare the full transport)"
            )

    if "protocol" in api:
        protocol = api["protocol"]
        if not isinstance(protocol, str) or not protocol.strip():
            problems.append(f"{path}: [entrypoint.api].protocol is blank")
        elif not protocol.endswith(SEARCH_PROTOCOL_SUFFIX):
            problems.append(
                f"{path}: [entrypoint.api].protocol {protocol!r} must end with "
                f"{SEARCH_PROTOCOL_SUFFIX!r} (search protocols are named "
                f"<engine>{SEARCH_PROTOCOL_SUFFIX}; the LLM chat protocols used under "
                "entrypoint/ are not valid here)"
            )

    if "base_url" in api:
        base_url = api["base_url"]
        if not isinstance(base_url, str):
            problems.append(f"{path}: [entrypoint.api].base_url must be a string")
        elif not base_url.strip() and provider not in SEARCH_OPERATOR_HOSTS:
            problems.append(
                f"{path}: [entrypoint.api].base_url is blank for a provider with a fixed host "
                f"(only {', '.join(sorted(SEARCH_OPERATOR_HOSTS))} may ship an empty base_url)"
            )

    if "endpoint" in api:
        endpoint = api["endpoint"]
        if not isinstance(endpoint, str) or not endpoint.strip():
            problems.append(f"{path}: [entrypoint.api].endpoint is blank")
        elif not endpoint.startswith("/"):
            problems.append(
                f"{path}: [entrypoint.api].endpoint {endpoint!r} must start with '/' "
                "(it is appended to base_url)"
            )

    if "method" in api:
        method = api["method"]
        if not isinstance(method, str) or not method.strip():
            problems.append(f"{path}: [entrypoint.api].method is blank")
        elif method not in SEARCH_METHODS:
            problems.append(
                f"{path}: [entrypoint.api].method {method!r} must be one of "
                f"{', '.join(SEARCH_METHODS)} (uppercase)"
            )

    auth_type = api.get("auth_type")
    auth_header = api.get("auth_header")
    env_var = api.get("env_var")
    if "auth_type" in api:
        if not isinstance(auth_type, str) or not auth_type.strip():
            problems.append(f"{path}: [entrypoint.api].auth_type is blank")
        elif auth_type not in SEARCH_AUTH_TYPES:
            problems.append(
                f"{path}: [entrypoint.api].auth_type {auth_type!r} must be one of "
                f"{', '.join(SEARCH_AUTH_TYPES)}"
            )
        elif auth_type == "none":
            if isinstance(auth_header, str) and auth_header.strip():
                problems.append(
                    f"{path}: [entrypoint.api].auth_header is {auth_header!r} "
                    "but auth_type is 'none'"
                )
            if isinstance(env_var, str) and env_var.strip():
                problems.append(
                    f"{path}: [entrypoint.api].env_var is {env_var!r} "
                    "but auth_type is 'none'"
                )
        else:
            if not isinstance(auth_header, str) or not auth_header.strip():
                problems.append(
                    f"{path}: [entrypoint.api].auth_header is blank "
                    f"(auth_type {auth_type!r} carries the credential in a header)"
                )
            if not isinstance(env_var, str) or not env_var.strip():
                problems.append(
                    f"{path}: [entrypoint.api].env_var is blank "
                    f"(auth_type {auth_type!r} needs the env var holding the credential)"
                )

    capabilities = entry.get("capabilities")
    if capabilities is None:
        return
    if not isinstance(capabilities, dict):
        problems.append(f"{path}: [entrypoint.capabilities] must be a table")
        return
    for key, value in capabilities.items():
        if key not in SEARCH_CAPABILITY_KEYS:
            problems.append(
                f"{path}: [entrypoint.capabilities].{key} is not a known capability "
                f"({', '.join(SEARCH_CAPABILITY_KEYS)})"
            )
        elif not isinstance(value, bool):
            problems.append(f"{path}: [entrypoint.capabilities].{key} must be a boolean")


def _search_registry_rows(registry: dict | None, problems: list[str]) -> dict[str, list]:
    """Collect the `[[search.*]]` group tables, rejecting unknown spellings."""
    rows: dict[str, list] = {}
    if registry is None:
        return rows
    search = registry.get("search")
    if search is None:
        return rows
    if not isinstance(search, dict):
        problems.append("registry.toml: [search] must hold the search provider groups")
        return rows

    pairs: list[tuple[str, object]] = []
    for key, value in search.items():
        if key in ("aggregators", "operators", "open"):
            pairs.append((f"search.{key}", value))
        elif key in ("international", "chinese"):
            if not isinstance(value, dict):
                problems.append(
                    f"registry.toml: [search.{key}] must contain provider groups "
                    f"(e.g. [[search.{key}.developers]])"
                )
                continue
            for sub, sub_value in value.items():
                pairs.append((f"search.{key}.{sub}", sub_value))
        else:
            problems.append(
                f"registry.toml: unknown search group 'search.{key}' "
                f"(allowed: {', '.join(SEARCH_REGISTRY_GROUPS)})"
            )

    for group, value in pairs:
        if group not in SEARCH_REGISTRY_GROUPS:
            problems.append(
                f"registry.toml: unknown search group {group!r} "
                f"(allowed: {', '.join(SEARCH_REGISTRY_GROUPS)})"
            )
            continue
        if not isinstance(value, list):
            problems.append(f"registry.toml: {group} must be an array of tables ([[{group}]])")
            continue
        rows[group] = value
    return rows


def _check_search_registry(root: Path, registry: dict | None, problems: list[str]) -> int:
    """(8): registry.toml search groups <-> search/ directories, both ways."""
    rows = _search_registry_rows(registry, problems)
    declared: dict[str, str] = {}
    for group in SEARCH_REGISTRY_GROUPS:
        for index, row in enumerate(rows.get(group, [])):
            where = f"registry.toml: {group}[{index}]"
            if not isinstance(row, dict):
                problems.append(f"{where}: must be a table")
                continue
            for key in SEARCH_REGISTRY_ROW_STRINGS:
                if not isinstance(row.get(key), str) or not row[key].strip():
                    problems.append(f"{where}: {key} is missing or blank")
            if "website_domain" in row and not isinstance(row["website_domain"], str):
                problems.append(f"{where}: website_domain must be a string")
            for key in SEARCH_REGISTRY_ROW_BOOLS:
                if key in row and not isinstance(row[key], bool):
                    problems.append(f"{where}: {key} must be a boolean")
            provider_id = row.get("id")
            if not isinstance(provider_id, str) or not provider_id.strip():
                continue
            previous = declared.get(provider_id)
            if previous is not None:
                problems.append(
                    f"{where}: duplicate search provider id {provider_id!r} (also listed in {previous})"
                )
            else:
                declared[provider_id] = group

    search_root = root / SEARCH_TOP
    directories = (
        {p.name: p for p in search_root.iterdir() if p.is_dir() and not p.name.startswith(".")}
        if search_root.is_dir()
        else {}
    )
    for provider_id in sorted(declared):
        if provider_id not in directories:
            problems.append(
                f"registry.toml: search provider {provider_id!r} (in {declared[provider_id]}) "
                f"has no {SEARCH_TOP}/{provider_id}/ directory"
            )
    for name in sorted(directories):
        if name not in declared:
            problems.append(
                f"{directories[name]}: no registry.toml entry for search provider {name!r} "
                f"(add it to one of: {', '.join(SEARCH_REGISTRY_GROUPS)})"
            )
        if not any(directories[name].rglob("*.toml")):
            problems.append(
                f"{directories[name]}: search provider directory holds no *.toml entrypoint"
            )
    return len(declared)


def validate(root: Path) -> list[str]:
    problems: list[str] = []

    # 1) everything parses (including descriptors, which get the schema check
    #    in their own validator).
    parsed: dict[str, dict] = {}
    for top in ("entrypoint", "models", "descriptors", SEARCH_TOP):
        if not (root / top).is_dir():
            continue
        for path in sorted((root / top).rglob("*.toml")):
            data = _load(path, problems)
            if data is not None:
                parsed[str(path)] = data

    registry_path = root / "registry.toml"
    if registry_path.is_file():
        registry = _load(registry_path, problems)
    else:
        problems.append(
            f"{registry_path}: missing (the provider catalog — including the search "
            "groups — ships with the data)"
        )
        registry = None

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

    # 6) + 7) search entrypoints
    search_root = root / SEARCH_TOP
    search_paths = sorted(search_root.rglob("*.toml")) if search_root.is_dir() else []
    search_ids: dict[str, str] = {}
    for path in search_paths:
        data = parsed.get(str(path))
        if data is None:
            continue
        entry = data.get("entrypoint")
        if not isinstance(entry, dict):
            problems.append(f"{path}: missing the [entrypoint] table")
            continue
        provider = _provider_dir(path, root, SEARCH_TOP)
        if provider is None:
            problems.append(
                f"{path}: search entrypoints live in {SEARCH_TOP}/<provider>/<variant>.toml, "
                "not directly under the tree"
            )
        for key in REQUIRED_ENTRYPOINT_KEYS:
            if key not in entry:
                problems.append(f"{path}: [entrypoint].{key} is missing")
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
            if entry_id in search_ids:
                problems.append(f"{path}: duplicate search entrypoint id {entry_id!r} (also {search_ids[entry_id]})")
            else:
                search_ids[entry_id] = str(path)
        _check_search_api(path, entry, provider, problems)

    # 8) registry.toml <-> search/ agreement
    _check_search_registry(root, registry, problems)

    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent
    if (
        not (root / "models").is_dir()
        or not (root / "entrypoint").is_dir()
        or not (root / SEARCH_TOP).is_dir()
        or not (root / "registry.toml").is_file()
    ):
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
    searches = len(list((root / SEARCH_TOP).rglob("*.toml")))
    print(
        f"validate-catalog: clean ({models} model file(s), {entrypoints} entrypoint file(s), "
        f"{searches} search file(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
