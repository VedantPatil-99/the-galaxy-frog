# P3.3 manual reranker preparation

P3.1 and P3.2 pass with the existing Python 3.14.7 runtime. A new Python installation is not
required for those checks. This procedure provisions a separate, ignored probe environment to
verify the next packet's CUDA runtime before changing the working backend dependencies.

Run each block separately in Git Bash and stop on the first failure. These are manual steps;
the agent has not installed packages, downloaded weights, changed drivers, or changed security
settings. Expect multi-gigabyte downloads and additional disk space for the environment/cache.

## Candidate pins and evidence

- `torch==2.11.0+cu128`: the [official CUDA 12.8 wheel index](https://download.pytorch.org/whl/cu128/torch/)
  publishes a CPython 3.14 Windows AMD64 wheel.
- `transformers==5.17.0`: the [published package metadata](https://pypi.org/pypi/transformers/5.17.0/json)
  supports Python >=3.10 and Hugging Face Hub >=1.5,<2. Pin the already-used Hub version `1.30.0`.
- `BAAI/bge-reranker-v2-m3`, immutable revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`,
  verified through the [model API](https://huggingface.co/api/models/BAAI/bge-reranker-v2-m3).
  The [model card](https://huggingface.co/BAAI/bge-reranker-v2-m3) documents the Transformers
  sequence-classification implementation and 512-token pair inputs.

Published artifacts do not prove this exact package/GPU combination works locally. The probe below
must pass before these pins are integrated into the application's optional dependency group/lock.
It records transitive versions for that integration; this temporary environment is not production.

## 1. Create an isolated environment with the existing interpreter

No PostgreSQL, API, web, worker, or Ollama service is required. Quit Ollama and other GPU-heavy apps
for this first isolated GPU check; later acceptance must test coexistence explicitly.

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
uv run --directory backend python --version
nvidia-smi
```

Expected Python is 3.14.7. The previously observed GPU is RTX 2050, 4096 MiB, driver 572.61.
Then run:

```bash
test ! -e tmp/reranker-probe/.venv && \
  uv venv --python backend/.venv/Scripts/python.exe --no-python-downloads tmp/reranker-probe/.venv
```

If the directory already exists, stop and report that fact instead of replacing it. `tmp/` is
already ignored by Git. This does not recreate `backend/.venv`.

```bash
probe_python='tmp/reranker-probe/.venv/Scripts/python.exe'
"$probe_python" --version
```

If Application Control blocks this interpreter, stop before installing anything and send the exact
error. Do not disable policy or change the working backend environment.

## 2. Install the pinned probe packages manually

```bash
uv pip install --python "$probe_python" --only-binary :all: \
  'torch==2.11.0+cu128' --index-url https://download.pytorch.org/whl/cu128
```

```bash
uv pip install --python "$probe_python" --only-binary :all: \
  'transformers==5.17.0' 'huggingface-hub==1.30.0'
```

```bash
uv pip check --python "$probe_python"
```

Do not replace a failed CUDA installation with CPU wheels or another model. A missing compatible
wheel or driver/runtime failure is a reported blocker; no compiler or driver installation is part
of these steps.

## 3. Prove CUDA execution before downloading the model

```bash
"$probe_python" - <<'PY'
import torch
print('torch:', torch.__version__, 'CUDA runtime:', torch.version.cuda)
assert torch.cuda.is_available(), 'CUDA unavailable; stop and report this output'
print('GPU:', torch.cuda.get_device_name(0))
x = torch.ones((32, 32), device='cuda', dtype=torch.float16)
y = x @ x
torch.cuda.synchronize()
assert torch.isfinite(y).all().item()
print('CUDA FP16 matrix multiplication passed')
PY
```

## 4. Download only the pinned model snapshot manually

```bash
"$probe_python" - <<'PY'
from huggingface_hub import snapshot_download
path = snapshot_download(
    repo_id='BAAI/bge-reranker-v2-m3',
    revision='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e',
    allow_patterns=[
        'config.json', 'model.safetensors', 'sentencepiece.bpe.model',
        'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json',
    ],
)
print('Model snapshot:', path)
PY
```

Record the printed snapshot path. It uses the standard Hugging Face cache outside source control.

## 5. Run the bounded, offline model probe

This uses CUDA FP16, batch size one, a 512-token limit, safetensors, and no remote model code.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "$probe_python" - <<'PY'
import json
import math
from time import perf_counter
import torch
import transformers
from transformers import AutoModelForSequenceClassification, AutoTokenizer

model_id = 'BAAI/bge-reranker-v2-m3'
revision = '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
assert torch.cuda.is_available(), 'CUDA is required'
tokenizer = AutoTokenizer.from_pretrained(
    model_id, revision=revision, local_files_only=True, trust_remote_code=False,
)
model = AutoModelForSequenceClassification.from_pretrained(
    model_id, revision=revision, local_files_only=True, trust_remote_code=False,
    use_safetensors=True, dtype=torch.float16,
).to('cuda').eval()
torch.cuda.reset_peak_memory_stats()
pairs = [
    ('Which animal eats bamboo?', 'Giant pandas eat bamboo.'),
    ('Which animal eats bamboo?', 'PostgreSQL stores searchable text.'),
]
scores = []
timings_ms = []
with torch.inference_mode():
    for question, passage in pairs:
        inputs = tokenizer(
            question, passage, padding=True, truncation=True,
            max_length=512, return_tensors='pt',
        ).to('cuda')
        torch.cuda.synchronize()
        started = perf_counter()
        score = model(**inputs, return_dict=True).logits.flatten()[0].float().item()
        torch.cuda.synchronize()
        timings_ms.append((perf_counter() - started) * 1000)
        assert math.isfinite(score), 'Non-finite score'
        scores.append(score)
assert scores[0] > scores[1], 'Sanity ranking failed'
print(json.dumps({
    'torch': torch.__version__, 'transformers': transformers.__version__,
    'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(0),
    'model': model_id, 'revision': revision, 'scores': scores,
    'inference_ms': timings_ms,
    'peak_allocated_mib': torch.cuda.max_memory_allocated() / 1024**2,
    'peak_reserved_mib': torch.cuda.max_memory_reserved() / 1024**2,
}, indent=2))
PY
```

The first inference includes warmup. These two pairs check basic execution, not retrieval quality
or coexistence with Ollama; the Phase 3 benchmark and live acceptance remain separate.

## 6. Capture versions and report

```bash
uv pip freeze --python "$probe_python" > tmp/reranker-probe/requirements-observed.txt
git status --short --branch
```

Send the CUDA check output, model snapshot path, model-probe JSON, and observed package versions.
If any step fails, send its command and error instead; stop subsequent steps. Never send tokens or
environment-file contents. Once verified, P3.3 can lock the proven dependencies, add the provider
adapter/runtime probe, and run its complete acceptance gate.
