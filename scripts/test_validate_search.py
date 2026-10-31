#!/usr/bin/env python3
"""Behavioral pins for the `search/` audit in scripts/validate_catalog.py.

Run: python3 scripts/test_validate_search.py  (exit 0 = all green)

The search tree is a cross-repository contract: the protocol names below are the
strings the search gateway and the LLM gateway match on, and the registry <-> tree
id agreement is what keeps a provider from silently disappearing from a setup UI.
Every check gets a positive pin (the real catalog passes) and a negative one (a
deliberately broken fixture fails with a message that names the defect), so a
future edit that loosens the validator fails here instead of shipping a quietly
smaller catalog.
"""
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import validate_catalog as vc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The public search protocol names. Changing one is a cross-repo change, not a
# local rename: consumers dispatch on these strings.
EXPECTED_PROTOCOLS = {
    "seia": "seia_search_v1",
    "deepseek": "deepseek_search_v1",
    "baidu": "baidu_ai_search_v1",
    "tavily": "tavily_search_v1",
    "brave": "brave_search_v1",
    "bing": "bing_search_v1",
    "bocha": "bocha_search_v1",
    "metaso": "metaso_search_v1",
    "zhipu": "zhipu_search_v1",
    "searxng": "searxng_search_v1",
    "duckduckgo": "duckduckgo_search_v1",
    "wikipedia": "wikipedia_search_v1",
}


def search_entry(
    provider="tavily",
    entry_id=None,
    protocol="tavily_search_v1",
    base_url="https://api.tavily.com",
    endpoint="/search",
    method="POST",
    auth_type="bearer",
    auth_header="Authorization",
    env_var="TAVILY_API_KEY",
    capabilities="snippets = true",
):
    return f"""\
[entrypoint]
id = "{entry_id or provider + '_search_default'}"
provider_id = "{provider}"

[entrypoint.api]
protocol = "{protocol}"
base_url = "{base_url}"
endpoint = "{endpoint}"
method = "{method}"
auth_type = "{auth_type}"
auth_header = "{auth_header}"
env_var = "{env_var}"

[entrypoint.name]
en = "Test Search"

[entrypoint.capabilities]
{capabilities}
"""


def registry_row(provider="tavily", group="search.international.developers", **overrides):
    rows = {
        "id": provider,
        "name": "Test Search",
        "description": "Test search provider",
    }
    rows.update(overrides)
    lines = [f"[[{group}]]"]
    for key, value in rows.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        else:
            lines.append(f'{key} = "{value}"')
    return "\n".join(lines) + "\n"


def problems(registry_text=None, files=None):
    """Validate a throwaway catalog and return the violation list."""
    files = files if files is not None else {"search/tavily/default.toml": search_entry()}
    registry_text = registry_text if registry_text is not None else registry_row()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "models").mkdir()
        (root / "entrypoint").mkdir()
        (root / "registry.toml").write_text(registry_text, encoding="utf-8")
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return vc.validate(root)


def has(problems_, needle):
    return any(needle in line for line in problems_), problems_


def test_real_catalog_is_clean():
    assert vc.validate(ROOT) == [], vc.validate(ROOT)


def test_protocol_names_are_the_cross_repo_contract():
    seen = {}
    for provider in EXPECTED_PROTOCOLS:
        path = ROOT / "search" / provider / "default.toml"
        assert path.is_file(), f"missing {path}"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["entrypoint"]["provider_id"] == provider, path
        seen[provider] = data["entrypoint"]["api"]["protocol"]
    assert seen == EXPECTED_PROTOCOLS, seen


def test_registry_groups_cover_every_directory():
    registry = tomllib.loads((ROOT / "registry.toml").read_text(encoding="utf-8"))
    listed = set()
    for flat in ("aggregators", "operators", "open"):
        for row in registry["search"].get(flat, []):
            listed.add(row["id"])
    for region in ("international", "chinese"):
        for group in registry["search"][region].values():
            for row in group:
                listed.add(row["id"])
    directories = {p.name for p in (ROOT / "search").iterdir() if p.is_dir()}
    assert listed == directories, (listed ^ directories)
    assert set(EXPECTED_PROTOCOLS) == directories, directories - set(EXPECTED_PROTOCOLS)


def test_missing_endpoint_is_reported():
    broken = search_entry().replace('endpoint = "/search"\n', "")
    files = {"search/tavily/default.toml": broken}
    ok, out = has(problems(files=files), "endpoint is missing")
    assert ok, out
    files = {"search/tavily/default.toml": search_entry(endpoint="")}
    ok, out = has(problems(files=files), "endpoint is blank")
    assert ok, out


def test_endpoint_must_be_an_absolute_path():
    files = {"search/tavily/default.toml": search_entry(endpoint="search")}
    ok, out = has(problems(files=files), "must start with '/'")
    assert ok, out


def test_method_must_be_uppercase():
    files = {"search/tavily/default.toml": search_entry(method="post")}
    ok, out = has(problems(files=files), "must be one of GET, POST (uppercase)")
    assert ok, out


def test_llm_chat_protocol_is_rejected():
    # The most likely copy-paste defect: an entrypoint/ protocol dropped into
    # search/. It must fail loudly, not be accepted as some chat endpoint.
    files = {"search/tavily/default.toml": search_entry(protocol="openai_chat_v1")}
    ok, out = has(problems(files=files), "must end with '_search_v1'")
    assert ok, out


def test_bearer_auth_needs_an_env_var():
    broken = search_entry().replace('env_var = "TAVILY_API_KEY"\n', "")
    files = {"search/tavily/default.toml": broken}
    ok, out = has(problems(files=files), "env_var is missing")
    assert ok, out
    files = {"search/tavily/default.toml": search_entry(env_var="")}
    ok, out = has(problems(files=files), "env_var is blank")
    assert ok, out


def test_header_auth_needs_a_header_name():
    files = {
        "search/tavily/default.toml": search_entry(auth_type="header", auth_header="")
    }
    ok, out = has(problems(files=files), "auth_header is blank")
    assert ok, out


def test_keyless_provider_must_not_declare_credentials():
    files = {
        "search/tavily/default.toml": search_entry(
            auth_type="none", auth_header="", env_var="TAVILY_API_KEY"
        )
    }
    ok, out = has(problems(files=files), "auth_type is 'none'")
    assert ok, out
    # ... and the keyless shape itself is clean.
    files = {
        "search/tavily/default.toml": search_entry(
            auth_type="none", auth_header="", env_var=""
        )
    }
    assert problems(files=files) == [], problems(files=files)


def test_blank_base_url_is_only_for_operator_supplied_hosts():
    files = {"search/searxng/default.toml": search_entry(
        provider="searxng", entry_id="searxng_search_default",
        protocol="searxng_search_v1", base_url="", endpoint="/search",
        method="GET", auth_type="none", auth_header="", env_var="",
    )}
    registry = registry_row(provider="searxng")
    assert problems(registry, files) == [], problems(registry, files)

    files = {"search/tavily/default.toml": search_entry(base_url="")}
    ok, out = has(problems(files=files), "base_url is blank for a provider with a fixed host")
    assert ok, out


def test_provider_id_must_match_its_directory():
    files = {"search/tavily/default.toml": search_entry(provider="brave")}
    ok, out = has(problems(files=files), "does not match directory 'tavily'")
    assert ok, out


def test_search_entrypoint_must_live_in_a_provider_directory():
    files = {"search/stray.toml": search_entry()}
    ok, out = has(problems(files=files), "not directly under the tree")
    assert ok, out


def test_unknown_capability_key_is_reported():
    files = {
        "search/tavily/default.toml": search_entry(capabilities="snippets = true\nboost = true")
    }
    ok, out = has(problems(files=files), "is not a known capability")
    assert ok, out


def test_capability_values_must_be_boolean():
    files = {"search/tavily/default.toml": search_entry(capabilities='content = "yes"')}
    ok, out = has(problems(files=files), "must be a boolean")
    assert ok, out


def test_registry_id_without_a_directory_is_reported():
    registry = registry_row(provider="ghost")
    ok, out = has(problems(registry), "has no search/ghost/ directory")
    assert ok, out


def test_directory_without_a_registry_entry_is_reported():
    files = {
        "search/tavily/default.toml": search_entry(),
        "search/brave/default.toml": search_entry(
            provider="brave", entry_id="brave_search_default", protocol="brave_search_v1"
        ),
    }
    ok, out = has(problems(files=files), "no registry.toml entry for search provider 'brave'")
    assert ok, out


def test_registry_id_may_not_appear_in_two_groups():
    registry = registry_row() + registry_row(group="search.open")
    ok, out = has(problems(registry), "duplicate search provider id 'tavily'")
    assert ok, out


def test_unknown_search_group_is_reported():
    # A plausible typo: the region sub-table is missing.
    registry = registry_row(group="search.developers")
    ok, out = has(problems(registry), "unknown search group 'search.developers'")
    assert ok, out


def test_registry_row_needs_name_and_description():
    registry = registry_row(name="")
    ok, out = has(problems(registry), "name is missing or blank")
    assert ok, out


def test_registry_flags_must_be_boolean():
    registry = registry_row(verified="yes")
    ok, out = has(problems(registry), "verified must be a boolean")
    assert ok, out


def test_empty_provider_directory_is_reported():
    registry = registry_row() + registry_row(provider="brave")
    files = {"search/tavily/default.toml": search_entry()}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "models").mkdir()
        (root / "entrypoint").mkdir()
        (root / "registry.toml").write_text(registry, encoding="utf-8")
        path = root / "search" / "tavily" / "default.toml"
        path.parent.mkdir(parents=True)
        path.write_text(files["search/tavily/default.toml"], encoding="utf-8")
        (root / "search" / "brave").mkdir()
        out = vc.validate(root)
    assert any("holds no *.toml entrypoint" in line for line in out), out


def test_catalog_without_registry_toml_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "models").mkdir()
        (root / "entrypoint").mkdir()
        path = root / "search" / "tavily" / "default.toml"
        path.parent.mkdir(parents=True)
        path.write_text(search_entry(), encoding="utf-8")
        out = vc.validate(root)
    assert any("registry.toml: missing" in line for line in out), out


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    assert len(tests) >= 15, f"expected >=15 tests, found {len(tests)}"
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Exception as e:  # a crashing test must fail the run, not abort it
            failed += 1
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
