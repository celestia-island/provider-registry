<p align="center">
  <img src="docs/logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  Shared LLM provider and model configuration registry.<br>
  Consumed by <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a>,
  <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a> and
  <a href="https://github.com/celestia-island/plana">plana</a> — each one vendors a snapshot
  of this data into its own build (see <a href="#consumers">Consumers</a>).
</p>

<p align="center">
  <a href="docs/README.zh-CN.md">简体中文</a> ·
  <a href="docs/README.zh-TW.md">繁體中文</a> ·
  <a href="docs/README.ja.md">日本語</a> ·
  <a href="docs/README.ko.md">한국어</a> ·
  <a href="docs/README.fr.md">Français</a> ·
  <a href="docs/README.es.md">Español</a> ·
  <a href="docs/README.ru.md">Русский</a> ·
  <a href="docs/README.ar.md">العربية</a>
</p>

---

## Structure

```
provider-registry/
├── entrypoint/          # Per-provider API configurations (22 providers)
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # Per-model metadata (160+ models)
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── descriptors/         # Model Deployment Descriptors (MDD, schema v1)
│   ├── README.md        #   TOML format reference
│   └── example.toml     #   annotated example (fake values)
├── registry.toml        # Provider catalog (grouped by category)
├── scripts/
│   ├── update_models.py # Sync model lists from external APIs
│   ├── validate_catalog.py      # Structural validator for entrypoint/ + models/
│   ├── validate_descriptors.py  # Structural validator for descriptors/
│   └── utils/
│       └── cli_format.py
├── justfile             # Task runner for common operations (shared devtools recipes via .just/)
└── .github/workflows/   # CI: daily model sync + catalog validation
```

## Entrypoint TOML Format

Each provider has one or more entrypoint TOML files:

```toml
[entrypoint]
id = "anthropic_default"
provider_id = "anthropic"

[entrypoint.api]
protocol = "anthropic_messages_v1"
base_url = "https://api.anthropic.com/v1"
env_var = "ANTHROPIC_API_KEY"

[entrypoint.name]
en = "Anthropic (Official)"
zhs = "Anthropic（官方）"
zht = "Anthropic（官方）"
ja = "Anthropic（公式）"
ko = "Anthropic（공식）"
fr = "Anthropic (Officiel)"
es = "Anthropic (Oficial)"
ru = "Anthropic (Официальный)"
```

## Model TOML Format

Each model has an individual TOML file:

```toml
[model]
id = "claude-opus-4"
name = "Claude Opus 4"
context_window = 200000
max_output_tokens = 16384
supports_vision = true
supports_streaming = true

[model.pricing]
input_per_million = 15.0
output_per_million = 75.0
```

## Usage

### Sync model lists

```bash
# Install just
cargo install just

# Sync from all sources (recommended)
just sync-all

# Sync from specific source
just sync-openrouter
just sync

# Sync specific providers
just sync all openai,anthropic
```

### Validate before pushing

```bash
just validate             # entrypoint/ + models/ (+ descriptors parse)
just validate-descriptors # full MDD schema check for descriptors/
```

The same two commands are what `.github/workflows/catalog-validate.yml` runs on
every PR and on master, so a malformed TOML (or a model whose `provider_id` does
not match its directory) fails CI instead of quietly shrinking a consumer's
catalog.

## Consumers

<a id="consumers"></a>

| Consumer | How it reads this registry |
| --- | --- |
| [entelecheia](https://github.com/celestia-island/entelecheia) | `scripts/fetch_provider_registry.py` downloads the tree into its build output; scepter/chest read the parsed data (see `packages/scepter/src/app/setup.rs`) |
| [shittim-chest](https://github.com/celestia-island/shittim-chest) | `scripts/fetch_provider_registry.py` (mock-server cache) + `scripts/export_vendors_snapshot.py`; the webui types live in `packages/webui/src/types/provider.ts` |
| [plana](https://github.com/celestia-island/plana) | `scripts/fetch_provider_registry.py` at build time; `packages/celestia-types` mirrors the shapes (`src/http.rs`, `packages/res/build.rs`) |

The consumer-side fetch scripts are build glue and are copies at the moment —
keeping them byte-identical is a follow-up; this registry only guarantees the
data contract below:

- `entrypoint/<provider>/<name>.toml` — one `[entrypoint]` per provider endpoint
  (`id`, `provider_id`, `api.protocol`, `api.base_url`; custom/operator endpoints
  ship `base_url = ""` and are filled at runtime);
- `models/<provider>/<model>.toml` — one `[model]` per model row, where
  `provider_id` must equal the directory name (a model id may repeat across
  providers: the same model is served by several with different limits);
- `descriptors/<provider>/<mdd>.toml` — Model Deployment Descriptors, schema v1,
  validated against the shapes plana's `MddDescriptor` defines.

`arona` does **not** read this registry.

### As a git submodule

```bash
# In your project
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## Model Deployment Descriptors (MDD)

Descriptors in `descriptors/` answer how a model is deployed and at what scale, for
every model class (LLM / diffusion / audio / encoders). The schema v1 lives in the
`plana-celestia-types` crate (`MddDescriptor`); the TOML data format mirrors it one-to-one.

See [`descriptors/README.md`](descriptors/README.md) for the format reference and
[`descriptors/example.toml`](descriptors/example.toml) for a complete annotated example.

```bash
just validate-descriptors   # structural validation of descriptors/
```

## CI

A daily GitHub Actions workflow fetches the latest model lists from [OpenRouter](https://openrouter.ai/) and [models.dev](https://models.dev/) and opens an auto-merge pull request against `master` (squash-only; a validation step gates the PR).

Manual triggers are available via the Actions tab with configurable source and provider filters.

## License

[CC0 1.0 Universal](LICENSE) — public domain, no restrictions.
