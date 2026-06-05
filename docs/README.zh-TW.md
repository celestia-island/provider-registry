<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  共用的大語言模型（LLM）供應商與模型組態登錄檔。<br>
  供 <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> 與 <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a> 使用。
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.ar.md">العربية</a>
</p>

---

## 目錄結構

```
provider-registry/
├── entrypoint/          # 各供應商的 API 組態（22 個供應商）
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # 各模型的中繼資料（160+ 個模型）
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # 供應商目錄（按類別分組）
├── scripts/
│   ├── update_models.py # 從外部 API 同步模型列表
│   └── utils/
│       └── cli_format.py
├── justfile             # 常用操作的 task runner
└── .github/workflows/   # CI：每日模型列表同步
```

## Entrypoint TOML 格式

每個供應商擁有一個或多個 entrypoint TOML 檔案：

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

## 模型 TOML 格式

每個模型都有一個獨立的 TOML 檔案：

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

## 使用方式

### 同步模型列表

```bash
# 安裝 just
cargo install just

# 從所有來源同步（推薦）
just sync-all

# 從指定來源同步
just sync-openrouter
just sync-modelsdev

# 同步指定供應商
just sync all openai,anthropic
```

### 作為 git 子模組使用

```bash
# 在你的專案中
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## 持續整合（CI）

每日 GitHub Actions 工作流程會從 [OpenRouter](https://openrouter.ai/) 與 [models.dev](https://models.dev/) 取得最新的模型列表，並將變更提交到 `dev` 分支。

可在 Actions 標籤頁中透過可設定的來源與供應商篩選器手動觸發。

## 授權條款

[CC0 1.0 Universal](LICENSE) — 公共領域，無任何限制。
