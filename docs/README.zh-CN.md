<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  共享的大语言模型（LLM）提供商与模型配置注册表。<br>
  供 <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> 和 <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a> 使用。
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.ar.md">العربية</a>
</p>

---

## 目录结构

```
provider-registry/
├── entrypoint/          # 各提供商的 API 配置（22 个提供商）
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # 各模型的元数据（160+ 个模型）
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # 提供商目录（按类别分组）
├── scripts/
│   ├── update_models.py # 从外部 API 同步模型列表
│   └── utils/
│       └── cli_format.py
├── justfile             # 常用操作的 task runner
└── .github/workflows/   # CI：每日模型列表同步
```

## Entrypoint TOML 格式

每个提供商拥有一个或多个 entrypoint TOML 文件：

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

每个模型都有一个独立的 TOML 文件：

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

## 使用方法

### 同步模型列表

```bash
# 安装 just
cargo install just

# 从所有来源同步（推荐）
just sync-all

# 从指定来源同步
just sync-openrouter
just sync-modelsdev

# 同步指定提供商
just sync all openai,anthropic
```

### 作为 git 子模块使用

```bash
# 在你的项目中
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## 持续集成（CI）

每日 GitHub Actions 工作流会从 [OpenRouter](https://openrouter.ai/) 和 [models.dev](https://models.dev/) 获取最新的模型列表，并将变更提交到 `dev` 分支。

可在 Actions 标签页中通过可配置的来源和提供商过滤器手动触发。

## 许可证

[CC0 1.0 Universal](LICENSE) — 公共领域，无任何限制。
