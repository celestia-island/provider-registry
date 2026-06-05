<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  Общий реестр конфигураций LLM-провайдеров и моделей.<br>
  Используется в <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> и <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a>.
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ar.md">العربية</a>
</p>

---

## Структура

```
provider-registry/
├── entrypoint/          # Конфигурации API для каждого провайдера (22 провайдера)
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # Метаданные моделей (160+ моделей)
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # Каталог провайдеров (сгруппированных по категориям)
├── scripts/
│   ├── update_models.py # Синхронизация списков моделей из внешних API
│   └── utils/
│       └── cli_format.py
├── justfile             # Task runner для типичных операций
└── .github/workflows/   # CI: ежедневная синхронизация моделей
```

## Формат TOML точек входа

Каждый провайдер имеет один или несколько файлов TOML точек входа:

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

## Формат TOML моделей

Каждая модель имеет отдельный TOML-файл:

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

## Использование

### Синхронизация списков моделей

```bash
# Установка just
cargo install just

# Синхронизация из всех источников (рекомендуется)
just sync-all

# Синхронизация из конкретного источника
just sync-openrouter
just sync-modelsdev

# Синхронизация конкретных провайдеров
just sync all openai,anthropic
```

### Как git-подмодуль

```bash
# В вашем проекте
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## CI

Ежедневный рабочий процесс GitHub Actions получает последние списки моделей из [OpenRouter](https://openrouter.ai/) и [models.dev](https://models.dev/) и фиксирует изменения в ветке `dev`.

Ручной запуск доступен на вкладке Actions с настраиваемыми фильтрами по источнику и провайдеру.

## Лицензия

[CC0 1.0 Universal](LICENSE) — общественное достояние, без ограничений.
