# Model Deployment Descriptors (MDD)

This directory hosts **Model Deployment Descriptor (MDD)** data files. A descriptor
answers two questions for every model class (LLM / diffusion / audio / encoders):

1. **How is this model deployed?** — component DAG, runtime engine matrix, task API contract.
2. **At what performance scale is it best deployed?** — hardware tiers, quantization, context/throughput profiles.

The schema v1 is defined in the `plana-celestia-types` crate
(`MddDescriptor` et al., exported as `@celestia-island/plana-celestia-types`).
The TOML format below mirrors that schema one-to-one. Data files must pass
`scripts/validate_descriptors.py` before merge.

## File Layout

```
descriptors/
├── README.md                 # This file
├── example.toml              # Annotated example (fake values, never deployed)
└── <provider>/<model>.toml   # One file per model, e.g. minimax/h3.toml
```

## TOML Format (schema v1)

Enum values use the snake_case wire format. `[[...]]` is TOML's array-of-tables
syntax, mirroring the schema's `Vec<...>` fields.

```toml
schema_version = 1

[model]
id = "example-model"
name = "Example Model"
family = "example-family"      # optional
architecture = "dit"           # free-form: "llm", "dit", "encoder", ...
description = "..."            # optional

# Component DAG: encoder -> dit -> vae (edges via `dependencies`)
[[components]]
id = "encoder"
kind = "encoder"               # encoder | dit | vae | decoder | tokenizer | other
arch = "example-vl"            # optional
dependencies = []              # component ids this one consumes
inputs = [{ name = "text", dtype = "text" }]
outputs = [{ name = "hidden_states", dtype = "bf16" }]

[[components.runtimes]]
engine = "llama_cpp"           # llama_cpp | vllm | sglang | candle | ollama | cloud | external_api | native
status = "ready"               # ready | planned | unavailable
features = ["hidden-states-export"]
quantizations = [{ id = "q4_k_m", bits = 4, size_multiplier = 0.25 }]

[components.runtimes.entry]
kind = "gguf"                  # file | url | registry | gguf
path = "models/example-encoder.Q4_K_M.gguf"
sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # optional
size_bytes = 4294967296        # optional

[components.runtimes.hardware] # optional
min_vram_mb = 16384
min_gpus = 1

[[components]]
id = "dit"
kind = "dit"
dependencies = ["encoder"]
outputs = [{ name = "latents", dtype = "bf16" }]

[[components.runtimes]]
engine = "cloud"
status = "ready"
entry = { kind = "url", path = "https://api.example.invalid/v1/video" }

[[components]]
id = "vae"
kind = "vae"
dependencies = ["dit"]
outputs = [{ name = "frames", dtype = "u8" }]

[[components.runtimes]]
engine = "native"
status = "unavailable"         # marks the not-yet-ready tier
entry = { kind = "file", path = "runtimes/native-vae" }


[[deploy.pipeline]]
id = "encode"
phase = "pre"                  # pre | iterative | post
cache_key = "text_encoder:prompt_hash"  # optional

[[deploy.pipeline]]
id = "denoise"
phase = "iterative"

[[deploy.pipeline]]
id = "decode"
phase = "post"

[deploy.api]
task = "video_generation"      # text_generation | embedding | image_generation | video_generation | audio_generation | other

[deploy.api.submit]
name = "video_generation"
[[deploy.api.submit.params]]
name = "prompt"
dtype = "string"
required = true

[deploy.api.result]
name = "video_result"
[[deploy.api.result.params]]
name = "url"
dtype = "string"
required = true

[scale]
total_parameters_b = 30.0      # optional
[scale.weights]
size_gb = 62.0
format = "safetensors"         # optional
[[scale.quantization]]
id = "fp16"
bits = 16
size_multiplier = 1.0
[[scale.quantization]]
id = "int8"
bits = 8
size_multiplier = 0.5
[scale.activation]
peak_gb = 12.0
[scale.context]
max_tokens = 8192
[scale.throughput]
tokens_per_second = 40.0

[[scale.tiers]]
id = "cloud-2k"
engines = ["cloud"]
quantizations = ["fp16"]
placement = "cloud"            # single_card | multi_card | cloud | external
notes = "..."

[[scale.tiers]]
id = "48gb-native"
engines = ["native"]
quantizations = ["int8"]
placement = "single_card"
min_vram_gb = 46.0
min_gpus = 1
```

See `example.toml` for a complete, valid file that passes the validator.
