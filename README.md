<p align="center">
  <img src="docs/logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  Shared provider and model configuration registry for LLM chat and web search.<br>
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
├── search/              # Search protocol configurations (12 providers)
│   ├── seia/
│   │   └── default.toml #   self-hosted aggregator
│   ├── deepseek/
│   │   └── default.toml
│   └── ...
├── descriptors/         # Model Deployment Descriptors (MDD, schema v1)
│   ├── README.md        #   TOML format reference
│   └── example.toml     #   annotated example (fake values)
├── registry.toml        # Provider catalog (grouped by category)
├── scripts/
│   ├── update_models.py # Sync model lists from external APIs
│   ├── validate_catalog.py      # Structural validator for entrypoint/ + search/ + models/
│   ├── validate_descriptors.py  # Structural validator for descriptors/
│   ├── test_update_models.py    # Regression pins for the model sync merge rules
│   ├── test_validate_search.py  # Regression pins for the search/ audit
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

## Search Providers

`search/` is the search half of the catalog: a provider that takes a query and
returns ranked web results. It is a **separate capability class from LLM chat** —
one vendor can appear in both trees (DeepSeek and Baidu do), but the two wire
contracts have nothing in common, so the chat protocols used under `entrypoint/`
are never valid here.

```
search/<provider_id>/<variant>.toml   # 12 providers in the first batch
```

Each file reuses the `[entrypoint]` table shape:

```toml
[entrypoint]
id = "deepseek_search_default"
provider_id = "deepseek"
website_domain = "deepseek.com"

[entrypoint.api]
protocol = "deepseek_search_v1"   # "<provider>_search_v1", always that suffix
base_url = "https://api.deepseek.com"
endpoint = "/v1/tools/search"     # absolute path, appended to base_url
method = "POST"                   # GET or POST
auth_type = "bearer"              # bearer | header | none
auth_header = "Authorization"
env_var = "DEEPSEEK_API_KEY"

[entrypoint.name]
en = "DeepSeek Search (Official)" # same locale set as entrypoint/ (en, zhs, zht, ja, ko, fr, es, ru)

[entrypoint.capabilities]
content = true                    # can return page text, not just a snippet
snippets = true
freshness = true                  # has a documented recency control
site_filter = true                # can restrict results to a site/domain
```

The naming rule: a search protocol is `<provider>_search_v1`, where `<provider>`
is the directory id and the protocol prefix. Compound vendor names spell the
vendor out rather than abbreviating (`baidu_ai_search_v1`, `duckduckgo_search_v1`,
`searxng_search_v1`); the gateway protocol is `seia_search_v1`. The validator
rejects anything that does not end in `_search_v1`, so an LLM chat protocol
(`openai_chat_v1`, …) cannot be dropped in by accident.

- `auth_type` says where the credential goes: `bearer` (`Authorization: Bearer`),
  `header` (the named `auth_header`, e.g. Brave's `X-Subscription-Token` or
  Bing's `Ocp-Apim-Subscription-Key`), or `none` (keyless — then `auth_header`
  and `env_var` are blank). These differences are the protocol facts a consumer
  needs; they are declared, not normalized away.
- `[entrypoint.defaults]` is **not** used by search entries: tier→model defaults
  describe chat models. Anything a consumer needs to know about a search
  endpoint lives in `[entrypoint.api]` and `[entrypoint.capabilities]`.
- `capabilities` is an assertion list, not an enumeration. A key that is present
  is documented by the vendor; an absent key means "not asserted" (unknown),
  never "known to be missing", and every file's header comment records where the
  claim comes from.
- Consumers gate optional request fields on those keys: `content` is the signal
  for `need_content` (absent → do not ask for page bodies), `freshness` for a
  recency/time-range parameter, `site_filter` for a domain restriction, and
  `snippets` for the presence of result text. Omitting a key is therefore the
  fail-safe default: an unknown provider is never asked for a field it may not
  accept.
- Operator-supplied hosts ship an empty `base_url` and name the variable that
  holds the URL (`base_url_env_var`); today that is only `searxng`. GET
  protocols may declare `query_param` (plus `fixed_params` for a constant query
  string); POST protocols carry everything in the body.

### Registry grouping

`registry.toml` lists search providers in five groups, parallel to the LLM
groups and never mixed with them:

| Group | Meaning |
| --- | --- |
| `[[search.aggregators]]` | gateways that fan one query out to a provider chain (`seia`) |
| `[[search.international.developers]]` | vendor-published APIs from non-Chinese vendors |
| `[[search.chinese.developers]]` | vendor-published APIs from Chinese vendors |
| `[[search.operators]]` | self-hosted or operated endpoints the caller must point at a host (`searxng`) |
| `[[search.open]]` | keyless public endpoints (`duckduckgo`, `wikipedia`) |

Rows carry `id`, `name`, `description`, `website_domain` and three factual flags:
`requires_key` (callable without a credential), `is_official` (the vendor
documents and supports the endpoint) and `verified` (`true` only once the
endpoint has been exercised from this workspace; `false` means unexercised, never
unavailable). The validator enforces agreement in both directions — every listed
id needs a `search/<id>/` directory and every directory must be listed exactly
once — so a provider cannot silently disappear from a setup UI.

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
just validate             # entrypoint/ + search/ + models/ (+ descriptors parse)
just validate-descriptors # full MDD schema check for descriptors/
just test                 # validator regression tests (stdlib only)
```

The same two `validate` commands are what `.github/workflows/catalog-validate.yml`
runs on every PR and on master, so a malformed TOML (or a model whose
`provider_id` does not match its directory, or a search provider that is listed
in `registry.toml` without a matching `search/` directory) fails CI instead of
quietly shrinking a consumer's catalog.

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
- `search/<provider>/<variant>.toml` — one `[entrypoint]` per search protocol
  (`api.protocol` ends in `_search_v1`, plus `api.endpoint`, `api.method` and the
  `api.auth_type`/`auth_header`/`env_var` triple; `capabilities` declares the
  optional request features), and the `search.*` groups in `registry.toml` index
  the same provider ids one-to-one;
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
