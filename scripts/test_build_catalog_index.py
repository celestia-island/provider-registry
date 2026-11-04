#!/usr/bin/env python3
"""Contract pins for scripts/build_catalog_index.py.

Run: python3 scripts/test_build_catalog_index.py   (exit 0 = all green)
     python3 -m unittest discover -s scripts -p 'test_*.py'

The generated `provider-registry-catalog.json` is a cross-repository artifact:
arona re-runs this generator (its `scripts/gen_registry_catalog.py` imports
`build()` and calls `index_digest()`) and the publish workflow ships the same
file as a release asset, so its shape, its `kind` derivation, the digest
definition and the "absent vs zero" rules are all load-bearing (shared contract
§2). Every pin here is either a fixture with a deliberately awkward tree, or the
real catalog:

* the digest is recomputed independently from the emitted `providers`/`models`
  (a test that called the same helper twice would still pass if the definition
  silently changed), and it is rebuilt in subprocesses with different
  `PYTHONHASHSEED`s — that is what catches ordering that leaks set/dict
  iteration order instead of an explicit sort;
* "field absent" vs "field is 0.0" is pinned separately, because the consumer
  reads the first as "no upstream value" and the second as "$0";
* `supports_vision` / `supports_reasoning` must be present on every row;
* `nan` / `inf` prices must fail the build instead of shipping invalid JSON, and
  so must floats outside the range every JSON serializer spells the same way
  (`1e-05` vs `0.00001` — the digest is recomputed by consumers, so a mismatch
  rejects the whole document);
* the endpoint side (`entrypoint/`, i.e. the artifact's `base_hosts`) has its own
  floors, because it is the consumer's first lookup key — and a non-blank
  `base_url` that carries no hostname counts as a defect, not as a host;
* negative prices and limits are refused on both layers (a sign would be
  published verbatim and read as a credit);
* the publish workflow may only touch the release from `refs/heads/master`;
* a duplicate `(provider, model id)` must fail the build rather than let file
  order decide which row wins.
"""

import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import build_catalog_index as bci  # noqa: E402
import validate_catalog as vc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(__file__).resolve().parent / "build_catalog_index.py"

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "publish-catalog.yml"
VALIDATE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "catalog-validate.yml"

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
    # Three entrypoints for one provider: the artifact must sort the hostnames
    # (file order is zzz-backup -> mirror -> api, sorted is api -> mirror -> zzz).
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
    "entrypoint/openai/a-backup.toml": """\
[entrypoint]
id = "openai_backup"
provider_id = "openai"

[entrypoint.api]
protocol = "openai_chat_v1"
base_url = "https://zzz-backup.openai.example/v1"

[entrypoint.name]
en = "OpenAI (backup)"
""",
    "entrypoint/openai/b-alt.toml": """\
[entrypoint]
id = "openai_alt"
provider_id = "openai"

[entrypoint.api]
protocol = "openai_chat_v1"
base_url = "https://mirror.openai.example/v1"

[entrypoint.name]
en = "OpenAI (alt)"
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
    # No pricing table, no capability keys, no category, empty tags: the id is
    # the only type signal (`embed`), everything else must be omitted — except
    # the two capability booleans, which are always present.
    "models/openai/text-embedding-3-small.toml": """\
[model]
id = "text-embedding-3-small"
name = "Text Embedding 3 Small"
provider_id = "openai"
context_window = 8191
max_output_tokens = 0
tags = []
""",
    # Carries `embed` but NOT `embedding`: pins the id hint, not a spelling.
    "models/openai/mxbai-embed-large.toml": """\
[model]
id = "mxbai-embed-large"
name = "mxbai embed large"
provider_id = "openai"
context_window = 512
max_output_tokens = 0
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
    # `model3d` must map to `3d`, not to image/video.
    "models/stability/mesh-1.toml": """\
[model]
id = "mesh-1"
name = "Mesh 1"
provider_id = "stability"
context_window = 0
max_output_tokens = 0
category = { generation = "model3d" }
tags = ["3d_generation"]
""",
    # The category value is free-form data; case must not change the kind.
    "models/stability/plate-upper.toml": """\
[model]
id = "plate-upper"
name = "Plate Upper"
provider_id = "stability"
context_window = 0
max_output_tokens = 0
category = { generation = "Image" }
""",
}

PROVIDER_IDS = ["openai", "stability", "zhipu_glm"]
MODEL_ORDER = [
    ("openai", "gpt-5.4-nano"),
    ("openai", "mxbai-embed-large"),
    ("openai", "text-embedding-3-small"),
    ("openai", "tts-1"),
    ("stability", "flux-2"),
    ("stability", "mesh-1"),
    ("stability", "plate-upper"),
    ("zhipu_glm", "cogview-4"),
    ("zhipu_glm", "glm-5.3"),
]
KIND_COUNTS = {"llm": 2, "embedding": 2, "audio": 1, "image": 3, "3d": 1}
OPENAI_HOSTS = ["api.openai.com", "mirror.openai.example", "zzz-backup.openai.example"]


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


def workflow_step_block(text: str, name: str) -> str:
    """The YAML text of one workflow step, from its `- name:` to the next step."""
    marker = f"- name: {name}"
    start = text.index(marker)
    rest = text[start + len(marker) :]
    end = rest.find("\n      - ")
    return rest if end == -1 else rest[:end]


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
        self.assertEqual(stats["models"], len(MODEL_ORDER))
        # Two registered providers plus the models-only `stability`.
        self.assertEqual(stats["providers"], 3)
        # Deterministic order: providers by id, models by (provider, id).
        self.assertEqual([p["id"] for p in document["providers"]], PROVIDER_IDS)
        self.assertEqual([(m["provider"], m["id"]) for m in document["models"]], MODEL_ORDER)
        self.assertEqual(stats["kinds"], KIND_COUNTS)

    def test_digest_matches_the_contract_definition(self):
        # Recomputed here from the emitted arrays, not from the generator's own
        # helper: this is the "consumer re-validates the digest" contract.
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

    def test_digest_is_hash_seed_independent(self):
        # Different `PYTHONHASHSEED`s change set/dict iteration order. If any
        # emitted array (providers, models, base_hosts) took its order from a set
        # instead of an explicit sort, the two subprocesses disagree here.
        digests = []
        for seed in ("0", "1234"):
            out = Path(self._tmp.name) / f"artifact-{seed}.json"
            environment = dict(os.environ, PYTHONHASHSEED=seed)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--source", str(self.root), "--out", str(out)],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(out.read_text(encoding="utf-8"))
            digests.append(document["digest"])
            self.assertEqual(document["providers"][0]["base_hosts"], OPENAI_HOSTS)
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(digests[0], self.build()[0]["digest"])

    def test_kind_derivation(self):
        document, _ = self.build()
        self.assertEqual(rows_by_id(document, "openai", "gpt-5.4-nano")["kind"], "llm")
        self.assertEqual(rows_by_id(document, "zhipu_glm", "glm-5.3")["kind"], "llm")
        # id hints: `embed` (and NOT the longer spelling `embedding`) is the signal
        self.assertEqual(rows_by_id(document, "openai", "text-embedding-3-small")["kind"], "embedding")
        self.assertEqual(rows_by_id(document, "openai", "mxbai-embed-large")["kind"], "embedding")
        # tags: the audio family
        self.assertEqual(rows_by_id(document, "openai", "tts-1")["kind"], "audio")
        # category wins over a contradicting tag
        self.assertEqual(rows_by_id(document, "zhipu_glm", "cogview-4")["kind"], "image")
        self.assertEqual(rows_by_id(document, "stability", "flux-2")["kind"], "image")
        # `model3d` -> `3d` (not image, not video)
        self.assertEqual(rows_by_id(document, "stability", "mesh-1")["kind"], "3d")
        # the category value is folded case-insensitively
        self.assertEqual(rows_by_id(document, "stability", "plate-upper")["kind"], "image")

    def test_every_row_carries_boolean_capabilities(self):
        # Always emitted, never omitted: the consumer merges these against its own
        # defaults, so "absent" and "false" must not be distinguishable.
        document, _ = self.build()
        for row in document["models"]:
            for field in ("supports_vision", "supports_reasoning"):
                self.assertIn(field, row, row)
                self.assertIsInstance(row[field], bool, row)
        self.assertIs(rows_by_id(document, "openai", "gpt-5.4-nano")["supports_vision"], True)
        self.assertIs(rows_by_id(document, "openai", "mxbai-embed-large")["supports_vision"], False)
        self.assertIs(rows_by_id(document, "zhipu_glm", "glm-5.3")["supports_reasoning"], True)

    def test_absent_numbers_are_omitted_and_explicit_zeros_are_kept(self):
        document, _ = self.build()
        # No [model.pricing] at all -> every price key is absent, never null.
        embedding = rows_by_id(document, "openai", "text-embedding-3-small")
        for field in ("input_per_million", "output_per_million", "cached_per_million"):
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

    def test_providers_carry_registry_metadata_and_entrypoint_hosts(self):
        document, _ = self.build()
        providers = {row["id"]: row for row in document["providers"]}
        self.assertEqual(
            providers["openai"],
            {"id": "openai", "name": "OpenAI", "category": "international", "base_hosts": OPENAI_HOSTS},
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

    def test_non_finite_prices_are_refused(self):
        # TOML accepts nan/inf; JSON does not. Shipping one would make the whole
        # artifact unparseable for every consumer, so the build must fail loudly
        # and name the field.
        card = self.root / "models" / "openai" / "nan-price.toml"
        card.write_text(
            "[model]\n"
            'id = "nan-price"\n'
            'name = "NaN Price"\n'
            'provider_id = "openai"\n'
            "context_window = 1000\n"
            "max_output_tokens = 10\n"
            "\n[model.pricing]\n"
            "input_per_million = nan\n"
            "output_per_million = inf\n",
            encoding="utf-8",
        )
        with self.assertRaises(bci.CatalogIndexError) as caught:
            self.build()
        message = str(caught.exception)
        self.assertIn("not a finite number", message)
        self.assertIn("[model.pricing]", message)
        # …and the validator says the same thing, so CI catches it before publish.
        code, _, err = run_main(["--source", str(self.root), "--check"])
        self.assertEqual(code, 1)
        self.assertIn("not a finite number", err)

    def test_prices_outside_the_cross_language_range_are_refused(self):
        # The digest is recomputed by consumers, so the two serializations must
        # agree byte for byte: Python writes `1e-05`, `ryu`/`serde_json` writes
        # `0.00001`. Any value Python renders in exponent form is refused.
        card = self.root / "models" / "openai" / "tiny-price.toml"
        template = (
            "[model]\n"
            'id = "tiny-price"\n'
            'name = "Tiny Price"\n'
            'provider_id = "openai"\n'
            "context_window = 1000\n"
            "max_output_tokens = 10\n"
            "\n[model.pricing]\n"
            "input_per_million = {value}\n"
        )
        for value, allowed in (("1e-05", False), ("5e-05", False), ("1e15", False), ("0.0001", True), ("0.002", True), ("0", True)):
            card.write_text(template.format(value=value), encoding="utf-8")
            if allowed:
                document, _ = self.build()
                self.assertEqual(rows_by_id(document, "openai", "tiny-price")["input_per_million"], float(value), value)
                continue
            with self.assertRaises(bci.CatalogIndexError, msg=value) as caught:
                self.build()
            message = str(caught.exception)
            self.assertIn("[model.pricing].input_per_million", message, value)
            self.assertIn("outside the range", message, value)
        # …and zero keeps flowing through as an emitted value, never a rejection.
        card.write_text(template.format(value="0.0"), encoding="utf-8")
        self.assertEqual(rows_by_id(self.build()[0], "openai", "tiny-price")["input_per_million"], 0.0)

    def test_negative_prices_and_limits_are_refused(self):
        # A negative price is not a "small" price: the artifact would publish the
        # sign verbatim and a consumer would read it as a credit.
        card = self.root / "models" / "openai" / "negative.toml"
        for field, literal, where in (
            ("input_per_million", "-0.2", "[model.pricing].input_per_million"),
            ("input_per_million", "-1", "[model.pricing].input_per_million"),
        ):
            card.write_text(
                "[model]\n"
                'id = "negative"\n'
                'name = "Negative"\n'
                'provider_id = "openai"\n'
                "context_window = 100\n"
                "max_output_tokens = 10\n"
                "\n[model.pricing]\n"
                f"{field} = {literal}\n",
                encoding="utf-8",
            )
            with self.assertRaises(bci.CatalogIndexError, msg=literal) as caught:
                self.build()
            self.assertIn(where, str(caught.exception))
            self.assertIn("negative", str(caught.exception))
        # … and a negative limit takes the same path.
        card.write_text(
            "[model]\n"
            'id = "negative-window"\n'
            'name = "Negative Window"\n'
            'provider_id = "openai"\n'
            "context_window = -5\n"
            "max_output_tokens = 10\n",
            encoding="utf-8",
        )
        with self.assertRaises(bci.CatalogIndexError) as caught:
            self.build()
        self.assertIn("[model].context_window", str(caught.exception))

    def test_negative_prices_are_reported_by_the_structural_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_tree(root, GATE_FIXTURE)
            (root / "search").mkdir()
            card = root / "models" / "openai" / "gpt-5.4-nano.toml"
            card.write_text(
                card.read_text(encoding="utf-8").replace("input_per_million = 0.2", "input_per_million = -0.2"),
                encoding="utf-8",
            )
            problems = vc.validate(root)
            self.assertTrue(any("cannot be negative" in line for line in problems), problems)
            # A negative limit is caught by the structural pass as well.
            card.write_text(
                card.read_text(encoding="utf-8")
                .replace("input_per_million = -0.2", "input_per_million = 0.2")
                .replace("context_window = 400000", "context_window = -5"),
                encoding="utf-8",
            )
            problems = vc.validate(root)
            self.assertTrue(any("a limit cannot be negative" in line for line in problems), problems)

    def test_base_url_without_a_host_is_refused(self):
        # Non-blank but not a URL: it parses to no hostname, so the provider's
        # only lookup key would vanish from the artifact while the tree validated.
        broken = self.root / "entrypoint" / "openai" / "scheme-less.toml"
        broken.write_text(
            "[entrypoint]\n"
            'id = "openai_scheme_less"\n'
            'provider_id = "openai"\n'
            "\n[entrypoint.api]\n"
            'protocol = "openai_chat_v1"\n'
            'base_url = "api.openai.example/v1"\n'
            "\n[entrypoint.name]\n"
            'en = "OpenAI (scheme-less)"\n',
            encoding="utf-8",
        )
        with self.assertRaises(bci.CatalogIndexError) as caught:
            self.build()
        self.assertIn("has no parseable host", str(caught.exception))
        # A blank base_url stays the legitimate operator-supplied form.
        self.assertIsNone(bci.base_host(""))
        self.assertEqual(bci.base_host("https://api.openai.com/v1"), "api.openai.com")

    def test_malformed_base_url_is_reported(self):
        # `urlsplit` raises on some malformed authorities; that is a data defect in
        # the entrypoint, not a crash.
        broken = self.root / "entrypoint" / "openai" / "broken.toml"
        broken.write_text(
            "[entrypoint]\n"
            'id = "openai_broken"\n'
            'provider_id = "openai"\n'
            "\n[entrypoint.api]\n"
            'protocol = "openai_chat_v1"\n'
            'base_url = "http://[::1/v1"\n'
            "\n[entrypoint.name]\n"
            'en = "OpenAI (broken)"\n',
            encoding="utf-8",
        )
        with self.assertRaises(bci.CatalogIndexError) as caught:
            self.build()
        self.assertIn("not a parseable URL", str(caught.exception))

    def test_digest_is_stable_and_price_sensitive(self):
        first, _ = self.build()
        second, _ = self.build()
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["providers"], second["providers"])
        self.assertEqual(first["models"], second["models"])

        # A one-cent change to one model must move the digest.
        card = self.root / "models" / "zhipu_glm" / "glm-5.3.toml"
        card.write_text(
            card.read_text(encoding="utf-8").replace("input_per_million = 1.23", "input_per_million = 1.24"),
            encoding="utf-8",
        )
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
        self.assertIn(f"{len(MODEL_ORDER)} model(s) across 3 provider(s)", out)
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
        self.assertEqual(len(document["models"]), len(MODEL_ORDER))

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


# A tree with one endpoint-bearing provider, one operator-supplied provider (no
# models, blank base_url) and the models/ side both rules look at.
ENDPOINT_FIXTURE = {
    "registry.toml": """
[[compatible]]
id = "openai_compatible"
name = "OpenAI Compatible"
description = "Custom OpenAI compatible API"
is_operator = true

[[international.developers]]
id = "openai"
name = "OpenAI"
description = "GPT models from OpenAI"
""",
    "entrypoint/openai/default.toml": """
[entrypoint]
id = "openai_default"
provider_id = "openai"

[entrypoint.api]
protocol = "openai_chat_v1"
base_url = "https://api.openai.com/v1"

[entrypoint.name]
en = "OpenAI"
""",
    "entrypoint/openai_compatible/default.toml": """
[entrypoint]
id = "openai_compatible_default"
provider_id = "openai_compatible"

[entrypoint.api]
protocol = "openai_chat_v1"
base_url = ""

[entrypoint.name]
en = "OpenAI Compatible"
""",
    "models/openai/gpt-5.4-nano.toml": """
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


class EntrypointGateTest(unittest.TestCase):
    """The endpoint side of the gate (`base_hosts` is the consumer's first key)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        write_tree(self.root, ENDPOINT_FIXTURE)
        (self.root / "search").mkdir()

    def test_a_registered_provider_losing_its_entrypoint_is_named(self):
        shutil.rmtree(self.root / "entrypoint" / "openai")
        problems = vc.entrypoint_problems(self.root, floor=1)
        # Two independent rules fire here: the named coverage rule and the count
        # floor (the deleted directory was the only fixed-host entrypoint left).
        named = [line for line in problems if "no entrypoint/openai/*.toml" in line]
        self.assertEqual(len(named), 1, problems)
        self.assertIn("base_hosts", named[0])
        self.assertTrue(any("fixed api.base_url" in line for line in problems), problems)
        # …and the validator CLI is actually wired to it.
        code, _, err = run_validator(self.root)
        self.assertEqual(code, 1, err)
        self.assertIn("no entrypoint/openai/*.toml", err)

    def test_operator_provider_may_ship_without_models_or_a_host(self):
        # `*_compatible` carries no host and no model cards: losing it is not a
        # model-catalog defect, so the gate must stay green (floor injected so a
        # three-file fixture is not judged against the real tree's 59).
        shutil.rmtree(self.root / "entrypoint" / "openai_compatible")
        self.assertEqual(vc.entrypoint_problems(self.root, floor=1), [])

    def test_a_scheme_less_base_url_does_not_count_as_a_fixed_host(self):
        # The floor counts hosts the artifact would actually carry; a URL that
        # parses to no hostname contributes none, so it must not satisfy the floor.
        entry = self.root / "entrypoint" / "openai" / "default.toml"
        entry.write_text(
            entry.read_text(encoding="utf-8").replace("https://api.openai.com/v1", "api.openai.com/v1"),
            encoding="utf-8",
        )
        problems = vc.entrypoint_problems(self.root, floor=1)
        self.assertTrue(any("fixed api.base_url" in line for line in problems), problems)
        # … the structural pass names the file itself (pinned on `validate()`, not on
        # the CLI: the CLI would still say "has no parseable host" through the
        # generator's own refusal, and this layer must have its own pin) …
        structural = vc.validate(self.root)
        self.assertTrue(any("has no parseable host" in line for line in structural), structural)
        # … and the CLI as a whole is red.
        code, _, err = run_validator(self.root)
        self.assertEqual(code, 1, err)

    def test_whole_entrypoint_tree_loss_is_caught(self):
        shutil.rmtree(self.root / "entrypoint")
        problems = vc.entrypoint_problems(self.root, floor=1)
        self.assertTrue(any("openai" in line for line in problems), problems)
        self.assertTrue(any("fixed api.base_url" in line for line in problems), problems)

    def test_count_floor_ignores_operator_endpoints(self):
        census = {
            "openai": {"files": 3, "fixed_host_files": 3},
            "openai_compatible": {"files": 2, "fixed_host_files": 0},
        }
        self.assertEqual(vc.entrypoint_count_problems(census, floor=3), [])
        # dropping a fixed-host file is a loss …
        shrunk = dict(census, openai={"files": 2, "fixed_host_files": 2})
        self.assertEqual(len(vc.entrypoint_count_problems(shrunk, floor=3)), 1)
        # … while operator files never counted towards the floor in the first place
        without_operator = dict(census, openai_compatible={"files": 0, "fixed_host_files": 0})
        self.assertEqual(vc.entrypoint_count_problems(without_operator, floor=3), [])

    def test_coverage_scope_is_registered_model_providers(self):
        census = {"openai": {"files": 1, "fixed_host_files": 1}}
        # models-only providers (meshy/meta/runway/stability/tripo) are out of scope
        self.assertEqual(vc.entrypoint_coverage_problems(set(), census), [])
        named = vc.entrypoint_coverage_problems({"openai", "silicon"}, census)
        self.assertEqual(len(named), 1, named)
        self.assertIn("silicon", named[0])


class PublishWorkflowGuardTest(unittest.TestCase):
    """The release steps must be unreachable from a non-master dispatch.

    Text assertions rather than YAML parsing: this suite runs on the farm with the
    standard library only (no PyYAML), and what matters is that the guard line is
    attached to the two steps that mutate the release.
    """

    PROTECTED = (
        "Create or update the rolling catalog-latest release",
        "Verify the release serves this build",
    )

    def test_release_steps_require_master_and_a_real_run(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        for name in self.PROTECTED:
            block = workflow_step_block(text, name)
            self.assertIn("if: github.ref == 'refs/heads/master'", block, name)
            self.assertIn("inputs.dry_run != true", block, name)

    def test_step_splitter_is_not_matching_the_whole_file(self):
        # Negative control for the assertions above: an unprotected step must not
        # contain the guard, otherwise `assertIn` would pass on any file.
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        block = workflow_step_block(text, "Gate the data and build the index")
        self.assertNotIn("refs/heads/master", block)
        self.assertIn("validate_catalog.py", block)

    def test_workflow_publishes_only_from_master_pushes(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("branches: [master]", text)
        self.assertIn("contents: write", text)
        self.assertIn("--clobber", text)
        self.assertIn("dry_run:", text)
        # `gh` must fail the job rather than skip the upload silently.
        self.assertIn("gh is not installed on this runner", text)


class CatalogValidateWorkflowTest(unittest.TestCase):
    """The model-sync suite needs the `toml` wheel; the gate must provision it or fail loudly.

    #32 started running `scripts/test_update_models.py` in this job and the Debian 12
    runners ship no `toml` (their system interpreter is PEP 668 EXTERNALLY-MANAGED, so a
    plain `pip install` cannot fix it either), which turned master red. Skipping the
    suite silently would be worse than red — the pin is that the provision step exists,
    is bounded, and that the suites run the interpreter which actually has the wheel.
    """

    def test_the_toml_dependency_is_provisioned_bounded(self):
        # Scoped to the step block on purpose: a file-wide `assertIn` would still
        # pass after the step was renamed, emptied or moved to another job.
        text = VALIDATE_WORKFLOW_PATH.read_text(encoding="utf-8")
        block = workflow_step_block(text, "Ensure the `toml` wheel for the model-sync suite")
        self.assertIn("python3 -c 'import toml'", block)
        self.assertIn("python3 -m venv .venv", block)
        self.assertIn('pip install --quiet --disable-pip-version-check "toml>=0.10,<1.0"', block)
        self.assertIn("::error::", block)
        self.assertIn("exit 1", block)
        # The suites must not run before the dependency is there.
        self.assertLess(text.index("Ensure the `toml` wheel"), text.index("Run the catalog test suites"))

    def test_the_suites_run_the_interpreter_that_has_toml(self):
        text = VALIDATE_WORKFLOW_PATH.read_text(encoding="utf-8")
        block = workflow_step_block(text, "Run the catalog test suites")
        self.assertIn("if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi", block)
        self.assertIn('"$PY" scripts/test_update_models.py', block)
        self.assertIn('"$PY" scripts/test_build_catalog_index.py', block)


class IndexGateWiringTest(unittest.TestCase):
    """`validate_catalog.py` must actually fail when the index gate fails.

    A gate nobody calls is not a gate: without these pins, deleting the
    `check_index` call from `main()` (or the per-provider floors from it) would
    leave every other check green while the published artifact could ship
    hundreds of models short.
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
            # …including the per-provider floor, not just the global one.
            self.assertIn("provider 'openai'", err)

    def test_provider_floors_name_the_lost_directory(self):
        healthy = dict(vc.MIN_ROWS_PER_PROVIDER)
        self.assertEqual(vc.provider_floor_problems(healthy), [])
        # Every pinned provider missing = every floor reported, and the message
        # says the directory is gone (not just "fewer rows").
        gone = vc.provider_floor_problems({})
        self.assertEqual(len(gone), len(vc.MIN_ROWS_PER_PROVIDER))
        self.assertTrue(all("is gone" in line for line in gone), gone[:2])
        # One provider short by a single row is still a loss.
        shrunk = vc.provider_floor_problems(dict(healthy, silicon=105))
        self.assertEqual(len(shrunk), 1, shrunk)
        self.assertIn("rows went missing", shrunk[0])
        self.assertIn("silicon", shrunk[0])
        # A provider the catalog adds beyond the pin is never reported.
        self.assertEqual(vc.provider_floor_problems(dict(healthy, brand_new=0)), [])

    def test_validator_flags_non_finite_prices_structurally(self):
        # The structural pass (not just the index gate) must name a nan/inf price:
        # it is the layer that reports `path: reason` for a single bad card.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_tree(root, GATE_FIXTURE)
            (root / "search").mkdir()
            card = root / "models" / "openai" / "gpt-5.4-nano.toml"
            card.write_text(
                card.read_text(encoding="utf-8").replace("input_per_million = 0.2", "input_per_million = nan"),
                encoding="utf-8",
            )
            problems = vc.validate(root)
            self.assertTrue(any("not a finite number" in line for line in problems), problems)
            self.assertTrue(any("[model.pricing].input_per_million" in line for line in problems), problems)
            # …and the same for the range that breaks cross-language digests.
            card.write_text(
                card.read_text(encoding="utf-8").replace("input_per_million = nan", "input_per_million = 1e-05"),
                encoding="utf-8",
            )
            problems = vc.validate(root)
            self.assertTrue(any("outside the range" in line for line in problems), problems)

    def test_validate_catalog_passes_the_real_tree(self):
        code, out, _ = run_validator(ROOT)
        self.assertEqual(code, 0, out)
        self.assertIn("index ", out)


class RealCatalogTest(unittest.TestCase):
    """The shipped tree must produce a publishable artifact — this is the gate
    `scripts/validate_catalog.py` calls, pinned from the data side too."""

    def setUp(self):
        self.document, self.stats = bci.build(ROOT)

    def test_real_catalog_is_publishable(self):
        self.assertGreaterEqual(self.stats["models"], 1000, self.stats)
        self.assertGreaterEqual(self.stats["providers"], 20, self.stats)
        self.assertEqual(self.document["schema"], 1)
        self.assertRegex(self.document["generated_at"], ISO_UTC)

        providers = {row["id"]: row for row in self.document["providers"]}
        self.assertEqual({m["provider"] for m in self.document["models"]} - set(providers), set())
        self.assertEqual(
            len(self.document["models"]),
            len({(m["provider"], m["id"]) for m in self.document["models"]}),
        )
        # The per-provider floors the validator enforces agree with the tree.
        counts: dict[str, int] = {}
        for row in self.document["models"]:
            counts[row["provider"]] = counts.get(row["provider"], 0) + 1
        self.assertEqual(vc.provider_floor_problems(counts), [])
        # Endpoint host -> provider mapping the consumer resolves against.
        self.assertIn("api.openai.com", providers["openai"]["base_hosts"])
        self.assertEqual(providers["openai_compatible"]["base_hosts"], [])

    def test_real_catalog_representative_rows(self):
        expected = {
            ("openai", "gpt-5.4-nano"): (0.2, 1.25, 0.02),
            ("anthropic", "claude-sonnet-5.5"): None,
            ("zhipu_glm", "glm-5.3"): None,
            ("google", "gemini-3.1-flash-lite"): (0.25, 1.5, 0.025),
        }
        for (provider, model_id), prices in expected.items():
            row = rows_by_id(self.document, provider, model_id)
            self.assertEqual(row["kind"], "llm", row)
            if prices is not None:
                self.assertEqual(
                    (row["input_per_million"], row["output_per_million"], row["cached_per_million"]),
                    prices,
                    row,
                )

    def test_real_catalog_kind_spot_checks(self):
        # The three kind paths the contract names, on live rows rather than
        # fixtures: category (`model3d`), tags (`tts`), id (`embed`).
        self.assertEqual(rows_by_id(self.document, "meshy", "meshy-1")["kind"], "3d")
        self.assertEqual(rows_by_id(self.document, "tripo", "tripo-1")["kind"], "3d")
        self.assertEqual(rows_by_id(self.document, "mistral", "mistral-embed")["kind"], "embedding")
        self.assertEqual(rows_by_id(self.document, "openai", "tts-1")["kind"], "audio")
        self.assertEqual(rows_by_id(self.document, "openai", "dall-e-3")["kind"], "image")
        self.assertEqual(rows_by_id(self.document, "minimax", "MiniMax-H3")["kind"], "video")

    def test_real_catalog_rows_are_json_legal(self):
        # Every emitted number is finite and every row carries the two capability
        # booleans: this is what makes the file parseable by a strict consumer.
        for row in self.document["models"]:
            for field in ("supports_vision", "supports_reasoning"):
                self.assertIsInstance(row[field], bool, row)
            for field, value in row.items():
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value), (field, value, row))
            for field in ("input_per_million", "output_per_million", "cached_per_million"):
                if field in row:
                    self.assertIsInstance(row[field], (int, float), row)
                    self.assertNotIsInstance(row[field], bool, row)
                    self.assertGreaterEqual(row[field], 0, row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
