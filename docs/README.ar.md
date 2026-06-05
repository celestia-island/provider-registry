<div dir="rtl" align="center">
  <img src="logo.webp" alt="Provider Registry" width="120">
</div>

<h1 dir="rtl" align="center">Provider Registry</h1>

<p dir="rtl" align="center">
  سجل مشترك لإعدادات مزوّدي وموديلات LLM.<br>
  يُستخدم من قبل <a href="https://github.com/celestia-island/entelecheia">Entelecheia</a> و <a href="https://github.com/celestia-island/shittim-chest">Shittim Chest</a>.
</p>

<p dir="rtl" align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a>
</p>

---

## الهيكل

```
provider-registry/
├── entrypoint/          # إعدادات API لكل مزوّد (22 مزوّدًا)
│   ├── anthropic/
│   │   └── default.toml
│   ├── openai/
│   │   ├── default.toml
│   │   └── generation.toml
│   └── ...
├── models/              # بيانات وصفية لكل موديل (160+ موديل)
│   ├── anthropic/
│   │   ├── claude-opus-4.toml
│   │   └── ...
│   └── ...
├── registry.toml        # فهرس المزوّدين (مُجمَّع حسب الفئة)
├── scripts/
│   ├── update_models.py # مزامنة قوائم الموديلات من واجهات برمجة خارجية
│   └── utils/
│       └── cli_format.py
├── justfile             # مشغّل مهام للعمليات الشائعة
└── .github/workflows/   # CI: مزامنة يومية لقوائم الموديلات
```

## تنسيق TOML لنقاط الدخول

يملك كل مزوّد ملف TOML واحد أو أكثر لنقطة الدخول:

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

## تنسيق TOML للموديلات

يملك كل موديل ملف TOML منفصل:

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

## الاستخدام

### مزامنة قوائم الموديلات

```bash
# تثبيت just
cargo install just

# المزامنة من جميع المصادر (مُوصى به)
just sync-all

# المزامنة من مصدر محدد
just sync-openrouter
just sync-modelsdev

# مزامنة مزوّدين محددين
just sync all openai,anthropic
```

### كوحدة فرعية في git

```bash
# في مشروعك
git submodule add https://github.com/celestia-island/provider-registry.git provider-registry
```

## التكامل المستمر (CI)

يقوم سير عمل GitHub Actions اليومي بجلب أحدث قوائم الموديلات من [OpenRouter](https://openrouter.ai/) و [models.dev](https://models.dev/) وإيداع التغييرات في فرع `dev`.

يتوفر التشغيل اليدوي من تبويب Actions مع فلاتر قابلة للتهيئة للمصدر والمزوّد.

## الترخيص

[CC0 1.0 Universal](LICENSE) — الملكية العامة، بدون قيود.
