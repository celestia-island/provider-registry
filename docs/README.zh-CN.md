<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  共享的提供商与模型配置注册表：LLM 对话 + 网页搜索。<br>
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
├── search/              # 各搜索协议的配置（首批 12 个提供商）
│   ├── seia/
│   │   └── default.toml #   自托管聚合网关
│   ├── deepseek/
│   │   └── default.toml
│   └── ...
├── registry.toml        # 提供商目录（按类别分组）
├── scripts/
│   ├── update_models.py # 从外部 API 同步模型列表
│   ├── validate_catalog.py     # entrypoint/ + search/ + models/ 的结构校验
│   ├── test_validate_search.py # search/ 校验规则的回归用例
│   └── utils/
│       └── cli_format.py
├── justfile             # 常用操作的 task runner
└── .github/workflows/   # CI：每日模型列表同步 + 目录校验
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

## 搜索提供商

`search/` 是目录的「搜索半边」：一个搜索提供商接收查询并返回排序后的网页结果。它是
**与 LLM 对话独立的能力类别**——同一家厂商可以同时出现在两棵树里（DeepSeek、百度即是），
但两者的线上契约完全不同，`entrypoint/` 里的对话协议在 `search/` 里永远不合法。

```
search/<provider_id>/<variant>.toml   # 首批 12 个提供商
```

每个文件沿用 `[entrypoint]` 表形：

```toml
[entrypoint]
id = "deepseek_search_default"
provider_id = "deepseek"
website_domain = "deepseek.com"

[entrypoint.api]
protocol = "deepseek_search_v1"   # 命名规则见下：始终以 _search_v1 结尾
base_url = "https://api.deepseek.com"
endpoint = "/v1/tools/search"     # 绝对路径，拼在 base_url 之后
method = "POST"                   # GET 或 POST
auth_type = "bearer"              # bearer | header | none
auth_header = "Authorization"
env_var = "DEEPSEEK_API_KEY"

[entrypoint.name]
en = "DeepSeek Search (Official)" # 与 entrypoint/ 相同的语种集合（en, zhs, zht, ja, ko, fr, es, ru）

[entrypoint.capabilities]
content = true                    # 能返回正文而不只是摘要
snippets = true
freshness = true                  # 有文档化的时间范围参数
site_filter = true                # 能限定到某个站点/域名
```

**协议命名规则**：搜索协议一律为 `<provider>_search_v1`，其中 `<provider>` 既是目录名也是
协议前缀；复合厂商名按厂商完整拼写而不是缩写（`baidu_ai_search_v1`、`duckduckgo_search_v1`、
`searxng_search_v1`），网关协议为 `seia_search_v1`。校验脚本会拒绝任何不以 `_search_v1`
结尾的协议，因此误把 LLM 对话协议（`openai_chat_v1` 等）贴进来会直接失败。

- `auth_type` 说明凭据放在哪里：`bearer`（`Authorization: Bearer`）、`header`（由
  `auth_header` 指定头名，例如 Brave 的 `X-Subscription-Token`、Bing 的
  `Ocp-Apim-Subscription-Key`）、`none`（无需凭据，此时 `auth_header` 与 `env_var` 留空）。
  这些差异是消费者必须知道的协议事实，本目录照实声明而不是抹平。
- 搜索条目**不使用** `[entrypoint.defaults]`：tier→model 的默认值描述的是对话模型。
  搜索端点需要的信息只在 `[entrypoint.api]` 与 `[entrypoint.capabilities]` 中。
- `capabilities` 是「断言清单」而非「穷举清单」：出现的键表示厂商有文档支持；缺失的键表示
  **未断言（未知）**，而不是「确定不支持」。每个文件的头部注释记录了依据来源。
- 消费者据此决定可选请求字段：`content` 对应 `need_content`（缺失则不要请求正文）、
  `freshness` 对应时间范围参数、`site_filter` 对应域名限定、`snippets` 对应结果摘要文本。
  因此省略某个键就是安全默认值：未知的提供商不会被索要它可能不支持的能力。
- 由运维方提供主机的条目 `base_url` 留空，并用 `base_url_env_var` 指明存 URL 的环境变量
  （目前只有 `searxng`）。GET 协议可声明 `query_param`（以及常量查询串 `fixed_params`），
  POST 协议全部走请求体。

### 目录分组

`registry.toml` 用五个分组登记搜索提供商，与 LLM 分组并列、互不混用：

| 分组 | 含义 |
| --- | --- |
| `[[search.aggregators]]` | 把一次查询扇出到提供商链的网关（`seia`） |
| `[[search.international.developers]]` | 非中国厂商官方发布的 API |
| `[[search.chinese.developers]]` | 中国厂商官方发布的 API |
| `[[search.operators]]` | 自托管或需运维指定主机的端点（`searxng`） |
| `[[search.open]]` | 无需 key 的公共端点（`duckduckgo`、`wikipedia`） |

每条含 `id`、`name`、`description`、`website_domain` 以及三个事实性标记：`requires_key`
（无凭据即可调用）、`is_official`（厂商有文档且支持该端点）、`verified`（只有在本工作区
真实调用过才为 `true`；`false` 只表示未验证，绝不表示不可用）。校验脚本双向强制一致：
每个登记 id 必须有 `search/<id>/` 目录，每个目录必须恰好被登记一次，因此提供商不会从
配置界面里悄悄消失。

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

### 推送前校验

```bash
just validate             # entrypoint/ + search/ + models/（外加 descriptors 解析）
just validate-descriptors # descriptors/ 的完整 MDD schema 校验
just test                 # 校验规则的回归用例（仅标准库）
```

`.github/workflows/catalog-validate.yml` 在每次 PR 与 master 上跑的就是上面两条 `validate`
命令：TOML 写错、模型的 `provider_id` 与目录不符、或 `registry.toml` 登记的搜索提供商没有
对应 `search/` 目录，都会在 CI 失败，而不是让消费者的目录悄悄变小。

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
