<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  Registro compartido de configuración de proveedores y modelos LLM.<br>
  Utilizado por <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> y <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a>.
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.ar.md">العربية</a>
</p>

---

## Estructura

```
provider-registry/
├── entrypoint/          # Configuraciones API por proveedor (22 proveedores)
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # Metadatos por modelo (160+ modelos)
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # Catálogo de proveedores (agrupados por categoría)
├── scripts/
│   ├── update_models.py # Sincronizar listas de modelos desde APIs externas
│   └── utils/
│       └── cli_format.py
├── justfile             # Task runner para operaciones comunes
└── .github/workflows/   # CI: sincronización diaria de modelos
```

## Formato TOML de puntos de entrada

Cada proveedor tiene uno o más archivos TOML de punto de entrada:

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

## Formato TOML de modelos

Cada modelo tiene un archivo TOML individual:

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

## Uso

### Sincronizar listas de modelos

```bash
# Instalar just
cargo install just

# Sincronizar desde todas las fuentes (recomendado)
just sync-all

# Sincronizar desde una fuente específica
just sync-openrouter
just sync-modelsdev

# Sincronizar proveedores específicos
just sync all openai,anthropic
```

### Como submódulo de git

```bash
# En tu proyecto
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## CI

Un flujo de trabajo diario de GitHub Actions obtiene las últimas listas de modelos desde [OpenRouter](https://openrouter.ai/) y [models.dev](https://models.dev/) y confirma los cambios en la rama `dev`.

Se pueden realizar ejecuciones manuales desde la pestaña Actions con filtros configurables de fuente y proveedor.

## Licencia

[CC0 1.0 Universal](LICENSE) — dominio público, sin restricciones.
