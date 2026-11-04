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
   `env_var` — with `protocol` being one of the shared search vocabulary
   (`SEARCH_PROTOCOLS`, the verbatim seia/arona contract; a near-miss such as
   `baidu_search_v1` or an LLM chat protocol like `openai_chat_v1` is a defect
   here), `endpoint` an absolute path, `method` `GET`/`POST`, and an auth
   contract that matches the credential requirement (`bearer`/`header` need a
   header and an env var, `none` needs neither);
7. `[entrypoint.capabilities]`, when present, only carries the four known
   capability flags (`content`, `snippets`, `freshness`, `site_filter`) as
   booleans — an unknown key would be silently ignored by consumers;
8. `registry.toml`'s search groups and the `search/` tree agree in both
   directions: every listed id has a `search/<id>/` directory, every directory
   is listed exactly once, and group spellings are from the known set;
9. the published catalog index still builds: `scripts/build_catalog_index.py`
   flattens the tree into `dist/provider-registry-catalog.json` — the artifact
   the publish workflow attaches to the rolling `catalog-latest` release, which
   arona syncs and vendors as its offline fallback. "The tree parses" is not the
   same claim as "the artifact is publishable": the flattening has its own
   failure modes (a missing `[model]` table, a duplicate `(provider, id)`, a
   provider id listed twice), and a tree that silently lost a whole
   `models/<provider>/` directory would still validate clean while shipping a
   catalog with several hundred models missing. So the gate also asserts the
   row counts stay in the documented range — globally *and per provider*, which
   is the granularity that actually catches a lost directory (a 106-row subtree
   dropping out still clears a 1000-row global floor) — and that the digest is
   stable and the numeric values are JSON-legal.

   The gate has a second half aimed at the *endpoint* side, because
   `providers[].base_hosts` is the consumer's first lookup key (a provider is
   resolved from the configured endpoint URL before the model id is consulted):
   every registered provider that ships model cards must keep at least one
   `entrypoint/<provider>/*.toml`, and the number of entrypoints that declare a
   fixed `api.base_url` must not drop. Operator-supplied endpoints
   (`base_url = ""`, the `*_compatible` family) are exempt by design: they ship
   no host and no model cards.

`descriptors/` gets the full MDD schema check from `scripts/validate_descriptors.py`;
here it only has to parse, so one missing validator cannot hide a broken file.

Usage: python3 scripts/validate_catalog.py [root]   (default: repository root)
Exit codes: 0 clean / 1 violations (each printed as `path: reason`).
"""

from __future__ import annotations

import math
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The index gate reuses the generator itself rather than shelling out to
# `build_catalog_index.py --check`: one implementation means this gate cannot
# drift from the artifact the publish workflow actually ships.
import build_catalog_index as catalog_index  # noqa: E402


REQUIRED_MODEL_KEYS = ("id", "name", "provider_id")
REQUIRED_MODEL_INTS = ("context_window", "max_output_tokens")
REQUIRED_PRICING_KEYS = ("input_per_million", "output_per_million")
REQUIRED_ENTRYPOINT_KEYS = ("id", "provider_id", "api", "name")

SEARCH_TOP = "search"
ENTRYPOINT_TOP = "entrypoint"
SEARCH_PROTOCOL_SUFFIX = "_search_v1"
# The closed protocol vocabulary, pinned to arona's `SEARCH_PROTOCOLS`
# whitelist (scripts/test_validate_search.py keeps this set and the test's
# EXPECTED_PROTOCOLS in lockstep, so the two in-repo copies cannot drift;
# cross-repo parity with seia/arona is a review-time check, not CI — seia
# additionally serves academic engines that are deliberately out of this
# catalog's scope). A suffix-only check let a misspelling such as
# `baidu_search_v1` pass the catalog gate and explode only at arona runtime;
# membership is a data-contract fact, so the validator owns it here too.
SEARCH_PROTOCOLS = frozenset(
    (
        "seia_search_v1",
        "deepseek_search_v1",
        "baidu_ai_search_v1",
        "tavily_search_v1",
        "brave_search_v1",
        "bing_search_v1",
        "bocha_search_v1",
        "metaso_search_v1",
        "zhipu_search_v1",
        "searxng_search_v1",
        "duckduckgo_search_v1",
        "wikipedia_search_v1",
    )
)
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

# (9) Floor for the published index. The catalog is a data repository: the
# failure that matters is not a typo but a whole subtree going missing (a bad
# merge dropping a provider directory), which every other check here would call
# clean — the artifact would just quietly ship fewer models. The numbers are the
# documented order of magnitude (1100+ rows, 20+ providers), not a tight bound.
MIN_INDEX_MODELS = 1000
MIN_INDEX_PROVIDERS = 20
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

# (9) Per-provider row floors. This is the granularity that catches real data
# loss: dropping a whole `models/<provider>/` directory leaves every remaining
# file valid, so the structural checks stay clean and the global floor above only
# notices when the lost subtree is large (deleting `models/silicon/` = 106 rows
# still cleared a 1000-row floor). Rows are only ever ADDED by the sync, so these
# numbers do not need maintenance for growth; when a provider legitimately
# shrinks, lower its number in the same PR — that deliberate edit is the point.
# (9) Endpoint-side floors. `base_hosts` is the consumer's first lookup key, so
# `entrypoint/` losing a subtree damages resolution exactly like `models/` losing
# one — and no other check in this file looks at that tree's completeness.
# Counted over entrypoints that declare a fixed `api.base_url`; operator-supplied
# endpoints (`base_url = ""`, no host by design) are exempt. Entrypoints may be
# ADDED freely; a deliberate removal lowers this number in the same PR.
MIN_ENTRYPOINT_FILES = 59

MIN_ROWS_PER_PROVIDER = {
    "anthropic": 52,
    "cohere": 19,
    "deepseek": 20,
    "fireworks": 38,
    "google": 32,
    "meshy": 1,
    "meta": 12,
    "mimo": 1,
    "minimax": 12,
    "mistral": 60,
    "moonshot": 9,
    "openai": 91,
    "openrouter": 425,
    "qwen": 131,
    "runway": 1,
    "silicon": 106,
    "stability": 2,
    "stepfun": 8,
    "together": 42,
    "tripo": 1,
    "xai": 25,
    "zhipu_glm": 21,
}


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
        elif protocol not in SEARCH_PROTOCOLS:
            problems.append(
                f"{path}: [entrypoint.api].protocol {protocol!r} is not in the shared "
                f"search vocabulary (one of: {', '.join(sorted(SEARCH_PROTOCOLS))}); "
                "arona's gateway whitelist accepts nothing else (seia serves extra "
                "academic engines outside this catalog's scope), so a "
                "near-miss spelling must fail here rather than at runtime"
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
            elif model[key] < 0:
                problems.append(f"{path}: [model].{key} is {model[key]!r} — a limit cannot be negative")
        pricing = model.get("pricing")
        if pricing is not None:
            if not isinstance(pricing, dict):
                problems.append(f"{path}: [model.pricing] must be a table")
            else:
                for key in REQUIRED_PRICING_KEYS:
                    if pricing.get(key) is not None and not isinstance(pricing[key], (int, float)):
                        problems.append(f"{path}: [model.pricing].{key} must be a number")
                # TOML accepts `nan` / `inf` (and prices small enough that Python
                # spells them `1e-05`); the published catalog is strict JSON whose
                # digest every consumer recomputes, so one such price rejects the
                # whole document. Check 9 refuses them too — two independent layers.
                for key, value in pricing.items():
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    if value < 0:
                        problems.append(
                            f"{path}: [model.pricing].{key} is {value!r} — a price cannot be negative "
                            "(the artifact would publish the sign verbatim)"
                        )
                    elif isinstance(value, float):
                        reason = float_json_problem(value)
                        if reason is not None:
                            problems.append(f"{path}: [model.pricing].{key} {value!r} {reason}")
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
            elif base_url.strip() and not (provider or "").endswith("_compatible"):
                # A string that is not a URL (e.g. `api.example.com/v1`) parses to
                # no hostname, so the published providers[].base_hosts would lose
                # the provider's only lookup key while this validator stayed green.
                try:
                    host = catalog_index.base_host(base_url)
                except catalog_index.CatalogIndexError:
                    host = None
                if host is None:
                    problems.append(
                        f"{path}: [entrypoint.api].base_url {base_url!r} has no parseable host — "
                        "write a full URL such as \"https://api.example.com/v1\"; a scheme-less "
                        "string silently empties providers[].base_hosts in the published catalog"
                    )
        elif api is not None:
            problems.append(f"{path}: [entrypoint.api] must be a table")
        name = entry.get("name")
        if name is not None and not isinstance(name, dict):
            # Same silent-skip hole the search tree had: a bare string passed
            # the required-key loop and the dict guard skipped the `.en` check.
            problems.append(f"{path}: [entrypoint.name] must be a table with an `en` label")
        if isinstance(name, dict) and not name.get("en"):
            problems.append(f"{path}: [entrypoint.name].en is missing (the un-localized label)")
        declared = entry.get("provider_id")
        if provider is not None and isinstance(declared, str) and declared != provider:
            problems.append(
                f"{path}: [entrypoint].provider_id {declared!r} does not match directory {provider!r}"
            )
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and not entry_id.strip():
            problems.append(f"{path}: [entrypoint].id is blank")
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
        if name is not None and not isinstance(name, dict):
            # A bare string used to slip past: the required-key loop only saw
            # the key, and the dict guard below silently skipped the `.en`
            # check, so an unlocalized entry shipped green.
            problems.append(f"{path}: [entrypoint.name] must be a table with an `en` label")
        if isinstance(name, dict) and not name.get("en"):
            problems.append(f"{path}: [entrypoint.name].en is missing (the un-localized label)")
        declared = entry.get("provider_id")
        if provider is not None and isinstance(declared, str) and declared != provider:
            problems.append(
                f"{path}: [entrypoint].provider_id {declared!r} does not match directory {provider!r}"
            )
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and not entry_id.strip():
            problems.append(f"{path}: [entrypoint].id is blank")
        if isinstance(entry_id, str) and entry_id:
            if entry_id in search_ids:
                problems.append(f"{path}: duplicate search entrypoint id {entry_id!r} (also {search_ids[entry_id]})")
            else:
                search_ids[entry_id] = str(path)
        _check_search_api(path, entry, provider, problems)

    # 8) registry.toml <-> search/ agreement
    _check_search_registry(root, registry, problems)

    return problems


def float_json_problem(value: float) -> str | None:
    """Why this float cannot be spelled identically by every JSON serializer."""
    if not math.isfinite(value):
        return "is not a finite number (TOML accepts nan/inf, the published catalog is strict JSON)"
    magnitude = abs(value)
    if 0 < magnitude < catalog_index.MIN_PLAIN_FLOAT or magnitude >= catalog_index.MAX_PLAIN_FLOAT:
        return (
            "is outside the range every JSON serializer spells the same way "
            f"(a non-zero float must be within [{catalog_index.MIN_PLAIN_FLOAT:g}, "
            f"{catalog_index.MAX_PLAIN_FLOAT:g}]); Python writes it in exponent form, other "
            "serializers do not, and a consumer recomputing the digest would reject the document"
        )
    return None


def _entrypoint_census(root: Path) -> dict[str, dict[str, int]]:
    """`entrypoint/<provider>/*.toml` -> {provider: {files, fixed_host_files}}."""
    census: dict[str, dict[str, int]] = {}
    top = root / ENTRYPOINT_TOP
    if not top.is_dir():
        return census
    for path in sorted(top.rglob("*.toml")):
        parts = path.relative_to(top).parts
        if len(parts) < 2:
            continue
        entry = None
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
            # The structural pass already reports an unparseable file.
            continue
        if isinstance(parsed.get("entrypoint"), dict):
            entry = parsed["entrypoint"]
        api = entry.get("api") if entry else None
        base_url = api.get("base_url") if isinstance(api, dict) else None
        row = census.setdefault(parts[0], {"files": 0, "fixed_host_files": 0})
        row["files"] += 1
        # A base_url that does not parse to a hostname contributes no `base_hosts`
        # entry to the artifact, so it must not count towards the fixed-host floor
        # either — that was a hole: rewriting `https://api.x/v1` to `api.x/v1`
        # emptied the provider's hosts while the floor stayed satisfied.
        try:
            if catalog_index.base_host(base_url) is not None:
                row["fixed_host_files"] += 1
        except catalog_index.CatalogIndexError:
            # Reported by the structural pass; counting it here would hide the loss.
            pass
    return census


def entrypoint_coverage_problems(
    registered_model_providers: set[str], census: dict[str, dict[str, int]]
) -> list[str]:
    """A registered provider with model cards must keep an entrypoint directory.

    Models-only providers (media groups that this registry lists only as model
    cards) are out of scope: `registry.toml` is what makes a provider selectable
    in a setup UI, and only then does it need an endpoint to talk to.
    """
    problems: list[str] = []
    for provider in sorted(registered_model_providers):
        if census.get(provider, {}).get("files", 0) < 1:
            problems.append(
                f"catalog index: provider {provider!r} is registered and ships model cards but has no "
                f"{ENTRYPOINT_TOP}/{provider}/*.toml — its base_hosts would be empty, and the consumer "
                "resolves a provider by endpoint host before it looks at the model id "
                "(did the directory go missing?)"
            )
    return problems


def entrypoint_count_problems(
    census: dict[str, dict[str, int]], floor: int = MIN_ENTRYPOINT_FILES
) -> list[str]:
    """Guard partial loss inside a provider (coverage cannot see one file going)."""
    total = sum(row["fixed_host_files"] for row in census.values())
    if total >= floor:
        return []
    return [
        f"catalog index: {total} entrypoint file(s) declaring a fixed api.base_url < {floor} — "
        "endpoint hosts would silently disappear from the artifact (operator endpoints with "
        'base_url = "" are exempt); entrypoints may be added freely, a deliberate removal lowers '
        "the floor in scripts/validate_catalog.py"
    ]


def entrypoint_problems(root: Path, floor: int = MIN_ENTRYPOINT_FILES) -> list[str]:
    """Both endpoint-side rules over one tree (`floor` is injectable for fixtures)."""
    census = _entrypoint_census(root)
    try:
        registry = tomllib.loads((root / "registry.toml").read_text(encoding="utf-8"))
        registered = set(catalog_index.registered_providers(registry)[0])
    except (OSError, tomllib.TOMLDecodeError, catalog_index.CatalogIndexError) as exc:
        return [f"catalog index: cannot read registry.toml for the endpoint check ({exc})"]
    models_root = root / "models"
    model_providers = (
        {path.name for path in models_root.iterdir() if path.is_dir() and not path.name.startswith(".")}
        if models_root.is_dir()
        else set()
    )
    return entrypoint_coverage_problems(registered & model_providers, census) + entrypoint_count_problems(
        census, floor
    )


def provider_floor_problems(row_counts: dict[str, int]) -> list[str]:
    """(9): per-provider row floors, applied to the rows the artifact carries.

    Extracted from `check_index()` so the floor logic can be pinned with a dict
    instead of a 1000-file tree (see `scripts/test_build_catalog_index.py`).
    """
    problems: list[str] = []
    for provider, floor in sorted(MIN_ROWS_PER_PROVIDER.items()):
        rows = row_counts.get(provider, 0)
        if rows >= floor:
            continue
        hint = f"models/{provider}/ is gone" if rows == 0 else "rows went missing"
        problems.append(
            f"catalog index: provider {provider!r} carries {rows} model row(s) < {floor} ({hint}); "
            "rows may be added freely — a deliberate shrink lowers the floor in scripts/validate_catalog.py"
        )
    return problems


def check_index(root: Path) -> tuple[list[str], dict]:
    """(9): the publishable catalog index still builds, stays whole, stays stable.

    Kept out of `validate()` on purpose: `validate()` is also called on tiny
    fixtures (see `scripts/test_validate_search.py`), where a 1000-row floor is
    meaningless. Only the real tree is held to the artifact contract.

    Returns the violations plus the summary the clean message prints (empty dict
    when the index cannot be built at all).
    """
    try:
        document, stats = catalog_index.build(root)
    except catalog_index.CatalogIndexError as exc:
        return [f"catalog index: cannot be generated ({exc})"], {}

    problems: list[str] = []
    if stats["models"] < MIN_INDEX_MODELS:
        problems.append(
            f"catalog index: {stats['models']} model row(s) < {MIN_INDEX_MODELS} — the published artifact "
            "would ship a shrunken catalog (did a models/<provider>/ directory go missing?)"
        )
    if stats["providers"] < MIN_INDEX_PROVIDERS:
        problems.append(
            f"catalog index: {stats['providers']} provider row(s) < {MIN_INDEX_PROVIDERS} — the published "
            "artifact would drop providers that consumers resolve models and endpoint hosts against"
        )
    row_counts: dict[str, int] = {}
    for row in document["models"]:
        row_counts[row["provider"]] = row_counts.get(row["provider"], 0) + 1
    problems.extend(provider_floor_problems(row_counts))
    if not DIGEST_PATTERN.match(document["digest"]):
        problems.append(f"catalog index: digest {document['digest']!r} is not sha256:<64 hex>")
    else:
        rebuilt, _ = catalog_index.build(root)
        if rebuilt["digest"] != document["digest"]:
            # The digest is the consumer's staleness check: it must not depend on
            # run order, timestamps or dict iteration.
            problems.append("catalog index: digest is not stable across two builds of the same tree")

    summary = {"models": stats["models"], "providers": stats["providers"], "digest": document["digest"]}
    return problems, summary


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
    index_summary: dict = {}
    if not problems:
        # The structural pass owns the data defects; running the index gate on a
        # tree it already rejects only adds noise (and can abort mid-violation).
        index_problems, index_summary = check_index(root)
        problems.extend(index_problems)
        problems.extend(entrypoint_problems(root))
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
        f"{searches} search file(s); index {index_summary['models']} row(s) / "
        f"{index_summary['providers']} provider(s), digest {index_summary['digest']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
