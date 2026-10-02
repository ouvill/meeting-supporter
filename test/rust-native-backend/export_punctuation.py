"""Developer-only, pinned Japanese punctuation ONNX export. See README for CPU dependencies."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import tempfile
from pathlib import Path

from prepare_assets import HERE, download, matches

EXPORT_VERSION = 1
VERSIONS = {
    "torch": "2.8.0+cpu",
    "transformers": "4.57.6",
    "onnx": "1.20.1",
    "onnxruntime": "1.24.4",
}
EXAMPLES = [
    (
        "明日の会議は何時からですか資料は私が用意しておくので先に始めていてください",
        "明日の会議は何時からですか。資料は私が用意しておくので先に始めていてください。",
    ),
    (
        "これは音声認識の動作確認です明日の会議は午前十時に始まります",
        "これは音声認識の動作確認です。明日の会議は午前十時に始まります。",
    ),
    ("今日は晴れです明日は雨です", "今日は晴れです。明日は雨です。"),
    (
        "承知しましたそれでは来週また相談しましょう",
        "承知しました。それでは来週また相談しましょう。",
    ),
]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(source: Path, staging: Path) -> None:
    # Never import or execute the model repository's Python code.
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import BertConfig, BertModel

    torch.set_num_threads(1)

    class Predictor(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            config = BertConfig.from_json_file(source / "config.json")
            config._attn_implementation = "eager"
            self.base_model = BertModel(config)
            self.linear = torch.nn.Linear(config.hidden_size, 2)

        def forward(self, input_ids, attention_mask):
            return self.linear(
                self.base_model(
                    input_ids=input_ids, attention_mask=attention_mask
                ).last_hidden_state
            )

    model = Predictor()
    # This pinned file is a state_dict. Do not enable arbitrary pickle deserialization.
    model.load_state_dict(
        torch.load(source / "weights.pth", map_location="cpu", weights_only=True),
        strict=True,
    )
    model.eval()
    vocabulary = {
        word: index
        for index, word in enumerate((source / "vocab.txt").read_text().splitlines())
    }

    def encode(text: str):
        return np.array(
            [
                [vocabulary["[CLS]"]]
                + [vocabulary.get(c, vocabulary["[UNK]"]) for c in text]
                + [vocabulary["[SEP]"]]
            ],
            dtype=np.int64,
        )

    def labels(logits):
        threshold = np.log(np.float32(0.1) / np.float32(0.9))
        return np.where(
            logits[..., 1] > threshold, 2, np.where(logits[..., 0] > threshold, 1, 0)
        )

    ids = torch.from_numpy(encode(EXAMPLES[0][0]))
    fp32 = staging / "punctuation.fp32.onnx"
    quantized = staging / "punctuation.onnx"
    torch.onnx.export(
        model,
        (ids, torch.ones_like(ids)),
        str(fp32),
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_axes={
            name: {1: "tokens"} for name in ("input_ids", "attention_mask", "logits")
        },
        opset_version=17,
        dynamo=False,
    )
    onnx.checker.check_model(str(fp32))
    quantize_dynamic(
        fp32,
        quantized,
        weight_type=QuantType.QInt8,
        per_channel=True,
        reduce_range=True,
        op_types_to_quantize=["MatMul"],
    )
    onnx.checker.check_model(str(quantized))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    sessions = [
        ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        for path in (fp32, quantized)
    ]
    # Numerical parity for FP32, punctuation decisions for INT8.
    # This is a synthetic regression suite, not an accuracy benchmark.
    for text, expected in EXAMPLES:
        ids = encode(text)
        mask = np.ones_like(ids)
        with torch.inference_mode():
            reference = model(torch.from_numpy(ids), torch.from_numpy(mask)).numpy()
        outputs = [
            s.run(["logits"], {"input_ids": ids, "attention_mask": mask})[0]
            for s in sessions
        ]
        np.testing.assert_allclose(outputs[0], reference, rtol=1e-3, atol=1e-4)
        np.testing.assert_array_equal(labels(outputs[1]), labels(reference))
        actual = "".join(
            c + ("", "、", "。")[label]
            for c, label in zip(text, labels(outputs[1])[0, 1:-1])
        )
        if actual != expected:
            raise RuntimeError("Synthetic punctuation regression failed")
    # Dynamic sequence axes must work at both ends of the runtime window size.
    for length in (1, 128, 256):
        ids = encode("あ" * length)
        output = sessions[1].run(
            ["logits"], {"input_ids": ids, "attention_mask": np.ones_like(ids)}
        )[0]
        if output.shape != (1, length + 2, 2) or not np.isfinite(output).all():
            raise RuntimeError("Invalid ONNX tensor contract")
    fp32.unlink()
    for name in ("vocab.txt", "README.md", "BASE_README.md"):
        shutil.copyfile(source / name, staging / name)
    (staging / "punctuation.json").write_text(
        json.dumps({"format": "bert_japanese_punctuation_v1"}) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=HERE / "target/models/punctuation-bert"
    )
    args = parser.parse_args()
    sources = json.loads((HERE / "assets.json").read_text())["punctuation_sources"]
    identity = {
        "export_version": EXPORT_VERSION,
        "sources": sources,
        "versions": VERSIONS,
    }
    output = args.output
    record = output / "export.json"
    if record.is_file():
        existing = json.loads(record.read_text())
        files = existing.get("files", {})
        required = {
            "punctuation.onnx",
            "punctuation.json",
            "vocab.txt",
            "README.md",
            "BASE_README.md",
        }
        if (
            existing.get("identity") == identity
            and set(files) == required
            and all(matches(output / name, value) for name, value in files.items())
        ):
            print("Verified cached Japanese punctuation ONNX bundle.")
            return
    for package, expected in VERSIONS.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            raise SystemExit(
                "Install the pinned CPU export environment described in README."
            ) from None
        if installed != expected:
            raise SystemExit(f"Export requires {package}=={expected}; see README.")
    source = HERE / "target/punctuation-source"
    for artifact in sources:
        for name, file in artifact["files"].items():
            download(
                f"https://huggingface.co/{artifact['repository']}/resolve/{artifact['revision']}/{name}",
                source / file["destination"],
                file["sha256"],
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary)
        export(source, staging)
        files = {path.name: digest(path) for path in staging.iterdir()}
        (staging / "export.json").write_text(
            json.dumps({"identity": identity, "files": files}, indent=2) + "\n",
            encoding="utf-8",
        )
        output.mkdir(parents=True, exist_ok=True)
        for name in files:
            (staging / name).replace(output / name)
        (staging / "export.json").replace(record)
    print("Exported INT8 ONNX; PyTorch/FP32/INT8 synthetic regressions passed.")


if __name__ == "__main__":
    main()
