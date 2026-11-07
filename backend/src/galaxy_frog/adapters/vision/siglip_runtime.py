"""Standalone offline child. Explicit device, single-frame batches, no model download.

Dynamic native objects stay at this optional dependency boundary. The parent validates all output.
"""

import json
import os
import sys
from collections.abc import Mapping
from contextlib import redirect_stdout
from importlib import import_module
from pathlib import Path
from typing import Any


def execute(payload: Mapping[str, Any]) -> dict[str, object]:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    torch: Any = import_module("torch")
    transformers: Any = import_module("transformers")
    images: Any = import_module("PIL.Image")
    if torch.__version__ != "2.11.0+cu128" or transformers.__version__ != "5.17.0":
        raise ValueError("unverified runtime")
    device = payload["device"]
    if device not in {"cpu", "cuda"} or (device == "cuda" and not torch.cuda.is_available()):
        raise ValueError("requested device unavailable")
    kwargs = {"revision": payload["revision"], "local_files_only": True, "trust_remote_code": False}
    processor = transformers.AutoProcessor.from_pretrained(
        payload["model"], backend="pil", **kwargs
    )
    model = (
        transformers.SiglipModel.from_pretrained(payload["model"], use_safetensors=True, **kwargs)
        .to(device)
        .eval()
    )
    vectors: list[list[float]] = []
    with torch.inference_mode():
        if "query" in payload:
            inputs = processor(
                text=[payload["query"]],
                padding="max_length",
                truncation=True,
                max_length=64,
                return_tensors="pt",
            ).to(device)
            features = model.get_text_features(**inputs, return_dict=True).pooler_output
            vectors.append(
                torch.nn.functional.normalize(features, dim=-1).float().cpu().tolist()[0]
            )
        else:
            for path in payload["paths"]:
                if Path(path).stat().st_size > 4_194_304:
                    raise ValueError("frame byte budget exceeded")
                with images.open(path) as image:
                    if image.width * image.height > 16_777_216:
                        raise ValueError("frame pixel budget exceeded")
                    inputs = processor(images=image.convert("RGB"), return_tensors="pt").to(device)
                    features = model.get_image_features(**inputs, return_dict=True).pooler_output
                vectors.append(
                    torch.nn.functional.normalize(features, dim=-1).float().cpu().tolist()[0]
                )
    return {
        "model": payload["model"],
        "revision": payload["revision"],
        "device": device,
        "vectors": vectors,
    }


def main() -> None:
    try:
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        with redirect_stdout(sys.stderr):
            result = execute(payload)
        print(json.dumps(result, allow_nan=False))
    except Exception:
        # No native/provider diagnostics (which may contain local paths) enter traces.
        print('{"error":"visual_provider_unavailable"}')
        raise SystemExit(1) from None


if __name__ == "__main__":  # pragma: no cover - subprocess entry
    main()
