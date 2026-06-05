<p align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</p>

<h1 align="center">Provider Registry</h1>

<p align="center">
  공유 LLM 프로바이더 및 모델 구성 레지스트리입니다.<br>
  <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> 및 <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a>에서 사용됩니다.
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.ar.md">العربية</a>
</p>

---

## 디렉토리 구조

```
provider-registry/
├── entrypoint/          # 프로바이더별 API 구성 (22개 프로바이더)
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # 모델별 메타데이터 (160+ 모델)
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # 프로바이더 카탈로그 (카테고리별 분류)
├── scripts/
│   ├── update_models.py # 외부 API에서 모델 목록 동기화
│   └── utils/
│       └── cli_format.py
├── justfile             # 일반 작업용 태스크 러너
└── .github/workflows/   # CI: 매일 모델 목록 동기화
```

## Entrypoint TOML 형식

각 프로바이더는 하나 이상의 entrypoint TOML 파일을 가집니다:

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

## 모델 TOML 형식

각 모델은 개별 TOML 파일을 가집니다:

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

## 사용법

### 모델 목록 동기화

```bash
# just 설치
cargo install just

# 모든 소스에서 동기화 (권장)
just sync-all

# 특정 소스에서 동기화
just sync-openrouter
just sync-modelsdev

# 특정 프로바이더 동기화
just sync all openai,anthropic
```

### git 서브모듈로 사용

```bash
# 프로젝트 내에서
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## CI

매일 실행되는 GitHub Actions 워크플로우가 [OpenRouter](https://openrouter.ai/) 및 [models.dev](https://models.dev/)에서 최신 모델 목록을 가져와 변경 사항을 `dev` 브랜치에 커밋합니다.

Actions 탭에서 소스 및 프로바이더 필터를 구성하여 수동으로 트리거할 수 있습니다.

## 라이선스

[CC0 1.0 Universal](LICENSE) — 퍼블릭 도메인, 제한 없음.
