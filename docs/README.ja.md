<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  共有の LLM プロバイダーおよびモデル設定レジストリ。<br>
  <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> および <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a> で利用されています。
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.ar.md">العربية</a>
</p>

---

## ディレクトリ構成

```
provider-registry/
├── entrypoint/          # プロバイダーごとの API 設定（22 プロバイダー）
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # モデルごとのメタデータ（160+ モデル）
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # プロバイダーカタログ（カテゴリ別）
├── scripts/
│   ├── update_models.py # 外部 API からモデルリストを同期
│   └── utils/
│       └── cli_format.py
├── justfile             # 一般的な操作用タスクランナー
└── .github/workflows/   # CI：毎日のモデルリスト同期
```

## Entrypoint TOML フォーマット

各プロバイダーには1つ以上の entrypoint TOML ファイルがあります：

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

## モデル TOML フォーマット

各モデルには個別の TOML ファイルがあります：

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

## 使い方

### モデルリストの同期

```bash
# just のインストール
cargo install just

# すべてのソースから同期（推奨）
just sync-all

# 特定のソースから同期
just sync-openrouter
just sync-modelsdev

# 特定のプロバイダーを同期
just sync all openai,anthropic
```

### git サブモジュールとして使用

```bash
# プロジェクト内で
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## CI

毎日実行される GitHub Actions ワークフローが [OpenRouter](https://openrouter.ai/) と [models.dev](https://models.dev/) から最新のモデルリストを取得し、変更を `dev` ブランチにコミットします。

Actions タブからソースおよびプロバイダーフィルターを設定して手動でトリガーすることも可能です。

## ライセンス

[CC0 1.0 Universal](LICENSE) — パブリックドメイン、制限なし。
