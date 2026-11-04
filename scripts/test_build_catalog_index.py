#!/usr/bin/env python3
"""Contract pins for scripts/build_catalog_index.py.

Run: python3 scripts/test_build_catalog_index.py   (exit 0 = all green)
     python3 -m unittest discover -s scripts -p 'test_*.py'

The generated `provider-registry-catalog.json` is a cross-repository artifact:
arona downloads it from the rolling `catalog-latest` release and also vendors a
copy as its offline fallback, so its shape, its `kind` derivation and the exact
digest definition are all load-bearing (see the shared contract §2). Every pin
here is either a fixture with a deliberately awkward tree, or the real catalog:

* the digest is recomputed independently from the emitted `providers`/`models`
  (a test that called the same helper twice would still pass if the definition
  silently changed);
* "field absent" vs "field is 0.0" is pinned separately, because the consumer
  reads the first as "no upstream value" and the second as "$0";
* a duplicate `(provider, model id)` must fail the build rather than let file
  order decide which row wins.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import build_catalog_index as bci  # noqa: E402
import validate_catalog as vc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

FIXTURE = {
    "registry.toml": """\
[[international.developers]]
id = "openai"
website_domain = "openai.com"
name = "OpenAI"
description = "GPT models from OpenAI"

[[chinese.developers]]
id = "zhipu_glm"
website_domain = "zhipuai.cn"
name = "Zhipu GLM"
description = "Zhipu AI models"

# Search providers are a separate capability class: the index must not fold
# them into the model-provider list.
[[search.aggregators]]
id = "seia"
name = "Seia"
website_domain = ""
description = "Self-hosted search gateway"
""",
    "entrypoint/openai/default.toml": """\
[entrypoint]
id = "openai_default"
provider_id = "openai"

[entrypoint.api]
protocol = "openai_chat_v1"
base_url = "https://api.openai.com/v1"

[entrypoint.name]
en = "OpenAI"
""",
    "entrypoint/zhipu_glm/default.toml": """\
[entrypoint]
id = "zhipu_glm_default"
provider_id = "zhipu_glm"

[entrypoint.api]
protocol = "openai_chat_v1"
base_url = "https://open.bigmodel.cn/api/paas/v4"

[entrypoint.name]
en = "Zhipu GLM"
""",
    # Full row: every price, both capability flags, both limits.
    "models/openai/gpt-5.4-nano.toml": """\
[model]
id = "gpt-5.4-nano"
name = "GPT-5.4 Nano"
provider_id = "openai"
context_window = 400000
max_output_tokens = 16384
supports_vision = true
supports_reasoning = true
tags = ["flagship"]

[model.pricing]
input_per_million = 0.2
output_per_million = 1.25
cached_per_million = 0.02
""",
    # No pricing table, no capability flags, no category, empty tags: the id is
    # the only type signal (`embed`), everything else must be omitted.
    "models/openai/text-embedding-3-small.toml": """\
[model]
id = "text-embedding-3-small"
name = "Text Embedding 3 Small"
provider_id = "openai"
context_window = 8191
max_output_tokens = 0
tags = []
""",
    # `tts` is an explicit type tag in the audio family.
    "models/openai/tts-1.toml": """\
[model]
id = "tts-1"
name = "TTS 1"
provider_id = "openai"
context_window = 4096
max_output_tokens = 0
tags = ["tts"]

[model.pricing]
input_per_million = 15.0
output_per_million = 0.0
""",
    "models/zhipu_glm/glm-5.3.toml": """\
[model]
id = "glm-5.3"
name = "GLM-5.3"
provider_id = "zhipu_glm"
context_window = 204800
max_output_tokens = 131072
supports_vision = false
supports_reasoning = true
tags = ["flagship"]

[model.pricing]
input_per_million = 1.23
output_per_million = 4.31
cached_per_million = 0.31
""",
    # `category` wins over a contradicting tag (contract precedence rule).
    "models/zhipu_glm/cogview-4.toml": """\
[model]
id = "cogview-4"
name = "CogView 4"
provider_id = "zhipu_glm"
context_window = 0
max_output_tokens = 0
category = { generation = "image" }
tags = ["video_generation"]

[model.pricing]
input_per_million = 0.0
output_per_million = 0.0
""",
    # A models/ directory with no registry.toml row: published, but flagged.
    "models/stability/flux-2.toml": """\
[model]
id = "flux-2"
name = "FLUX 2"
provider_id = "stability"
context_window = 0
max_output_tokens = 0
tags = ["image_generation"]

[model.pricing]
input_per_million = 0.0
output_per_million = 2.5
""",
}


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run_main(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI with its output captured, so the suite output stays readable."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = bci.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def run_validator(root: Path) -> tuple[int, str, str]:
    """Run `validate_catalog.py`'s CLI on a root with its output captured."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = vc.main(["validate_catalog.py", str(root)])
    return code, stdout.getvalue(), stderr.getvalue()


def rows_by_id(document: dict, provider: str, model_id: str) -> dict:
    for row in document["models"]:
        if row["provider"] == provider and row["id"] == model_id:
            return row
    raise AssertionError(f"{provider}/{model_id} missing from the artifact")


class FixtureArtifactTest(unittest.TestCase):
    """One fixture tree per test, so a mutating pin cannot leak into another."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        write_tree(self.root, FIXTURE)

    def build(self):
        return bci.build(self.root)

    def test_artifact_shape(self):
        document, stats = self.build()
        self.assertEqual(list(document), ["schema", "generated_at", "digest", "providers", "models"])
        self.assertEqual(document["schema"], 1)
        self.assertRegex(document["generated_at"], ISO_UTC)
        self.assertTrue(document["digest"].startswith("sha256:"))
        self.assertEqual(len(document["digest"]), len("sha256:") + 64)
        self.assertEqual(stats["models"], 6)
        # Two registered providers plus the models-only `stability`.
        self.assertEqual(stats["providers"], 3)
        # Deterministic order: providers by id, models by (provider, id).
        self.assertEqual([p["id"] for p in document["providers"]], ["openai", "stability", "zhipu_glm"])
        self.assertEqual(
            [(m["provider"], m["id"]) for m in document["models"]],
            [
                ("openai", "gpt-5.4-nano"),
                ("openai", "text-embedding-3-small"),
                ("openai", "tts-1"),
                ("stability", "flux-2"),
                ("zhipu_glm", "cogview-4"),
                ("zhipu_glm", "glm-5.3"),
            ],
        )

    def test_digest_matches_the_contract_definition(self):
        # Recomputed here from the emitted arrays, not from the generator's own
        # helper: this is the "arona re-validates the digest" contract.
        document, _ = self.build()
        canonical = json.dumps(
            {"providers": document["providers"], "models": document["models"]},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(document["digest"], "sha256:" + hashlib.sha256(canonical).hexdigest())
        # And the digest ignores `generated_at`, so the same tree is stable.
        self.assertNotIn(document["generated_at"], canonical.decode("utf-8"))

    def test_kind_derivation(self):
        document, stats = self.build()
        self.assertEqual(rows_by_id(document, "openai", "gpt-5.4-nano")["kind"], "llm")
        self.assertEqual(rows_by_id(document, "openai", "text-embedding-3-small")["kind"], "embedding")
        self.assertEqual(rows_by_id(document, "openai", "tts-1")["kind"], "audio")
        self.assertEqual(rows_by_id(document, "zhipu_glm", "cogview-4")["kind"], "image")
        self.assertEqual(rows_by_id(document, "stability", "flux-2")["kind"], "image")
        self.assertEqual(rows_by_id(document, "zhipu_glm", "glm-5.3")["kind"], "llm")
        self.assertEqual(stats["kinds"], {"llm": 2, "embedding": 1, "audio": 1, "image": 2})

    def test_absent_numbers_are_omitted_and_explicit_zeros_are_kept(self):
        document, _ = self.build()
        # No [model.pricing] at all -> every price key is absent, never null.
        embedding = rows_by_id(document, "openai", "text-embedding-3-small")
        for field in ("input_per_million", "output_per_million", "cached_per_million"):
            self.assertNotIn(field, embedding)
        for field in ("supports_vision", "supports_reasoning"):
            self.assertNotIn(field, embedding)
        # An explicit 0.0 in the TOML is data (a $0 price), not a missing value.
        self.assertEqual(rows_by_id(document, "openai", "tts-1")["output_per_million"], 0.0)
        # A price the row does not carry is dropped even when its siblings exist.
        self.assertNotIn("cached_per_million", rows_by_id(document, "openai", "tts-1"))
        self.assertNotIn("cached_per_million", rows_by_id(document, "stability", "flux-2"))
        # Full row keeps every declared field.
        nano = rows_by_id(document, "openai", "gpt-5.4-nano")
        self.assertEqual(
            (nano["input_per_million"], nano["output_per_million"], nano["cached_per_million"]),
            (0.2, 1.25, 0.02),
        )
        self.assertEqual((nano["context_window"], nano["max_output_tokens"]), (400000, 16384))
        self.assertIs(nano["supports_vision"], True)
        self.assertIs(nano["supports_reasoning"], True)

    def test_providers_carry_registry_metadata_and_entrypoint_hosts(self):
        document, _ = self.build()
        providers = {row["id"]: row for row in document["providers"]}
        self.assertEqual(
            providers["openai"],
            {"id": "openai", "name": "OpenAI", "category": "international", "base_hosts": ["api.openai.com"]},
        )
        self.assertEqual(providers["zhipu_glm"]["category"], "chinese")
        self.assertEqual(providers["zhipu_glm"]["base_hosts"], ["open.bigmodel.cn"])
        # The search groups in registry.toml are not model providers.
        self.assertNotIn("seia", providers)

    def test_unregistered_provider_is_published_and_reported(self):
        document, stats = self.build()
        providers = {row["id"]: row for row in document["providers"]}
        self.assertEqual(providers["stability"]["category"], bci.UNREGISTERED_CATEGORY)
        self.assertEqual(providers["stability"]["name"], "stability")
        self.assertEqual(stats["unregistered"], ["stability"])
        # Referential integrity: every models[].provider resolves to a provider.
        self.assertEqual({m["provider"] for m in document["models"]} - set(providers), set())
        self.assertTrue(any("stability" in line for line in stats["warnings"]), stats["warnings"])

    def test_digest_is_stable_and_price_sensitive(self):
        first, _ = self.build()
        second, _ = self.build()
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["providers"], second["providers"])
        self.assertEqual(first["models"], second["models"])

        # A one-cent change to one model must move the digest.
        card = self.root / "models" / "zhipu_glm" / "glm-5.3.toml"
        card.write_text(card.read_text(encoding="utf-8").replace("input_per_million = 1.23", "input_per_million = 1.24"), encoding="utf-8")
        third, _ = self.build()
        self.assertNotEqual(first["digest"], third["digest"])
        self.assertEqual(rows_by_id(third, "zhipu_glm", "glm-5.3")["input_per_million"], 1.24)

    def test_duplicate_model_id_fails_loudly(self):
        duplicate = self.root / "models" / "openai" / "gpt-5.4-nano-copy.toml"
        duplicate.write_text(
            (self.root / "models" / "openai" / "gpt-5.4-nano.toml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        with self.assertRaises(bci.CatalogIndexError) as caught:
            self.build()
        self.assertIn("duplicate model id", str(caught.exception))

    def test_source_date_epoch_pins_generated_at(self):
        previous = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = "1793000000"
        self.addCleanup(
            lambda: os.environ.pop("SOURCE_DATE_EPOCH", None)
            if previous is None
            else os.environ.__setitem__("SOURCE_DATE_EPOCH", previous)
        )
        document, _ = self.build()
        # Cross-checked with `date -u -d @1793000000 +%Y-%m-%dT%H:%M:%SZ`.
        self.assertEqual(document["generated_at"], "2026-10-26T07:33:20Z")

    def test_main_check_mode_writes_nothing(self):
        code, out, _ = run_main(["--source", str(self.root), "--check"])
        self.assertEqual(code, 0)
        self.assertIn("index is buildable", out)
        self.assertIn("6 model(s) across 3 provider(s)", out)
        self.assertFalse((self.root / bci.DEFAULT_OUT).exists())

    def test_main_writes_the_artifact(self):
        out_path = Path(self._tmp.name) / "artifacts" / "catalog.json"
        code, out, _ = run_main(["--source", str(self.root), "--out", str(out_path)])
        self.assertEqual(code, 0)
        self.assertIn("wrote", out)
        payload = out_path.read_text(encoding="utf-8")
        self.assertTrue(payload.endswith("\n"))
        document = json.loads(payload)
        self.assertEqual(document["schema"], 1)
        self.assertEqual(len(document["models"]), 6)

    def test_main_rejects_a_directory_that_is_not_a_registry_root(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        code, _, err = run_main(["--source", str(empty), "--check"])
        self.assertEqual(code, 2)
        self.assertIn("does not look like the registry root", err)

    def test_main_reports_an_unbuildable_tree(self):
        (self.root / "registry.toml").unlink()
        self.assertEqual(run_main(["--source", str(self.root), "--check"])[0], 2)
        (self.root / "registry.toml").write_text("[international]\n", encoding="utf-8")
        code, _, err = run_main(["--source", str(self.root), "--check"])
        self.assertEqual(code, 1)
        self.assertIn("no provider rows found", err)


GATE_FIXTURE = {
    "registry.toml": """\
[[international.developers]]
id = "openai"
name = "OpenAI"
description = "GPT models from OpenAI"
""",
    "entrypoint/openai/default.toml": """\
[entrypoint]
id = "openai_default"
provider_id = "openai"

[entrypoint.api]
protocol = "openai_chat_v1"
base_url = "https://api.openai.com/v1"

[entrypoint.name]
en = "OpenAI"
""",
    "models/openai/gpt-5.4-nano.toml": """\
[model]
id = "gpt-5.4-nano"
name = "GPT-5.4 Nano"
provider_id = "openai"
context_window = 400000
max_output_tokens = 16384

[model.pricing]
input_per_million = 0.2
output_per_million = 1.25
""",
}


class IndexGateWiringTest(unittest.TestCase):
    """`validate_catalog.py` must actually fail when the index gate fails.

    A gate nobody calls is not a gate: without this pin, deleting the
    `check_index` call from `main()` would leave every other check green while
    the published artifact could ship hundreds of models short.
    """

    def test_validate_catalog_fails_a_structurally_clean_tree_too_small_to_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_tree(root, GATE_FIXTURE)
            (root / "search").mkdir()
            # The structural pass is happy: one well-formed provider, one model.
            self.assertEqual(vc.validate(root), [])
            code, _, err = run_validator(root)
            self.assertEqual(code, 1, err)
            self.assertIn("catalog index:", err)
            self.assertIn("model row(s) <", err)

    def test_validate_catalog_passes_the_real_tree(self):
        code, out, _ = run_validator(ROOT)
        self.assertEqual(code, 0, out)
        self.assertIn("index ", out)


class RealCatalogTest(unittest.TestCase):
    """The shipped tree must produce a publishable artifact — this is the gate
    `scripts/validate_catalog.py` calls, pinned from the data side too."""

    def test_real_catalog_is_publishable(self):
        document, stats = bci.build(ROOT)
        self.assertGreaterEqual(stats["models"], 1000, stats)
        self.assertGreaterEqual(stats["providers"], 20, stats)
        self.assertEqual(document["schema"], 1)
        self.assertRegex(document["generated_at"], ISO_UTC)

        providers = {row["id"]: row for row in document["providers"]}
        self.assertEqual({m["provider"] for m in document["models"]} - set(providers), set())
        self.assertEqual(len(document["models"]), len({(m["provider"], m["id"]) for m in document["models"]}))

        # Representative rows: the four providers the pricing page exercise uses.
        expected = {
            ("openai", "gpt-5.4-nano"): (0.2, 1.25, 0.02),
            ("anthropic", "claude-sonnet-5.5"): None,
            ("zhipu_glm", "glm-5.3"): None,
            ("google", "gemini-3.1-flash-lite"): (0.25, 1.5, 0.025),
        }
        for (provider, model_id), prices in expected.items():
            row = rows_by_id(document, provider, model_id)
            self.assertEqual(row["kind"], "llm", row)
            if prices is not None:
                self.assertEqual(
                    (row["input_per_million"], row["output_per_million"], row["cached_per_million"]),
                    prices,
                    row,
                )

        # Endpoint host -> provider mapping the consumer resolves against.
        self.assertIn("api.openai.com", providers["openai"]["base_hosts"])
        self.assertEqual(providers["openai_compatible"]["base_hosts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
