<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  Registre partagé de configurations de fournisseurs et de modèles LLM.<br>
  Utilisé par <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> et <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a>.
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.ar.md">العربية</a>
</p>

---

## Structure

```
provider-registry/
├── entrypoint/          # Configurations API par fournisseur (22 fournisseurs)
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # Métadonnées par modèle (160+ modèles)
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # Catalogue des fournisseurs (groupés par catégorie)
├── scripts/
│   ├── update_models.py # Synchroniser les listes de modèles depuis des API externes
│   └── utils/
│       └── cli_format.py
├── justfile             # Task runner pour les opérations courantes
└── .github/workflows/   # CI : synchronisation quotidienne des modèles
```

## Format TOML des points d'entrée

Chaque fournisseur possède un ou plusieurs fichiers TOML de point d'entrée :

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

## Format TOML des modèles

Chaque modèle possède un fichier TOML individuel :

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

## Utilisation

### Synchroniser les listes de modèles

```bash
# Installer just
cargo install just

# Synchroniser depuis toutes les sources (recommandé)
just sync-all

# Synchroniser depuis une source spécifique
just sync-openrouter
just sync-modelsdev

# Synchroniser des fournisseurs spécifiques
just sync all openai,anthropic
```

### Comme sous-module git

```bash
# Dans votre projet
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## CI

Un workflow GitHub Actions quotidien récupère les dernières listes de modèles depuis [OpenRouter](https://openrouter.ai/) et [models.dev](https://models.dev/) et valide les modifications sur la branche `dev`.

Des déclenchements manuels sont disponibles via l'onglet Actions avec des filtres configurables sur la source et le fournisseur.

## Licence

[CC0 1.0 Universal](LICENSE) — domaine public, sans restrictions.
