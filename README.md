<p align="center">
  <img src="docs/logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  Shared LLM provider and model configuration registry.<br>
  Used by <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> and <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a>.
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
├── registry.toml        # Provider catalog (grouped by category)
├── scripts/
│   ├── update_models.py # Sync model lists from external APIs
│   └── utils/
│       └── cli_format.py
├── justfile             # Task runner for common operations
└── .github/workflows/   # CI: daily model list sync
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
just sync-modelsdev

# Sync specific providers
just sync all openai,anthropic
```

### As a git submodule

```bash
# In your project
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## CI

A daily GitHub Actions workflow fetches the latest model lists from [OpenRouter](https://openrouter.ai/) and [models.dev](https://models.dev/) and commits changes to the `dev` branch.

Manual triggers are available via the Actions tab with configurable source and provider filters.

## License

[CC0 1.0 Universal](LICENSE) — public domain, no restrictions.
