# whisper.cpp Q8_0 試作

`whisper-rs 0.16.0` / `whisper.cpp 1.8.3` で、GGML の FP16 / Q8_0 モデルを実行する WAV 用 CLI です。
推論に Python、PyTorch、ONNX Runtime は使いません。既存 UI、マイク取得、Silero、モデル管理 API への接続はまだありません。
faster-whisper 用の CTranslate2 モデルは読み込めません。

## ビルドと実行

リポジトリのルートで実行します。Rust に加えて CMake、C/C++ compiler、libclang が必要です。
Ubuntu では `sudo apt-get install cmake g++ libclang-dev` で準備できます。

```bash
cargo build --release --locked --manifest-path test/rust-whisper-backend/Cargo.toml
```

モデル取得だけ Hugging Face CLI を利用します。`uv tool run` が用意する Python 環境は取得ツール用で、
認識プロセスには入りません。保存先は Hugging Face 標準キャッシュで、`HF_HUB_CACHE` / `HF_HOME` などの指定にも従います。
既に GGML モデルがあれば、この取得操作は不要です。

```bash
whisper_model="$(uv tool run --from huggingface-hub==1.11.0 hf download \
  ggerganov/whisper.cpp ggml-large-v3-turbo-q8_0.bin \
  --revision 5359861c739e955e79d9a303bcbc70fb988958b1 --format quiet)"

test/rust-whisper-backend/target/release/meeting-whisper-probe \
  --model "$whisper_model" --wav /path/to/sample.wav \
  --language ja --device cpu --threads 4 --runs 3
```

入力は **16 kHz・mono・PCM16・30 秒以内**の空でない WAV です。形式違い・長すぎる音声はモデルをロードする前に拒否します。
変換が必要なら、手元の音声を `ffmpeg -i input.wav -t 30 -ar 16000 -ac 1 -c:a pcm_s16le sample.wav` で変換できます。
`--language` は `ja` / `en` / `auto`、`--runs` は同じモデルを保持して行う独立した認識の回数です。

モデル候補は次のとおりです。いずれも上記の固定 revision にある多言語モデルです。

| ファイル | 容量（10進 MB） | SHA-256 |
|---|---:|---|
| `ggml-tiny-q8_0.bin` | 43.5 | `c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca` |
| `ggml-base-q8_0.bin` | 81.8 | `c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9` |
| `ggml-small-q8_0.bin` | 264.5 | `49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f` |
| `ggml-large-v3-turbo-q8_0.bin` | 874.2 | `317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1` |
| `ggml-tiny.bin`（FP16比較用） | 77.7 | `be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21` |

取得後は `sha256sum "$whisper_model"` で照合できます。CLI 自体は任意のモデルファイルを受け取り、ダウンロードやハッシュ照合は行いません。
モデルの配布元・利用条件は [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) を参照してください。

## 出力と計測

標準出力は JSON Lines、whisper.cpp の診断は標準エラーです。認識文も出力されるため、実会議の結果をリポジトリへ保存しないでください。

- `prepared`: `load_ms`、モデル種別、`weight_type`（Q8_0 は `7`、FP16 は `1`）、要求したデバイス。
- `transcript`: 区間ごとの認識文と時刻、`inference_ms`、音声の秒数、`real_time_factor`。
- RTF は認識時間 / 音声時間です。1 未満は音声の実時間より速いことを示し、ストリーミング認識への対応を意味しません。

読み込み時間にプロセス起動・WAV 読み込みは含みません。認識時間には各回の decoder state 作成を含みます。
Greedy / best-of 1、temperature 0、過去の認識文を引き継がない設定です。VAD や誤認識フィルタはまだ適用しません。

2026-09-30 の CPU smoke 実測（Linux x86_64 / AMD EPYC VM、4 threads、release、約 5.46 秒の合成英語音声）:

| Tiny モデル | 読み込み | 初回認識 | 2 / 3 回目 | 2 / 3 回目の RTF |
|---|---:|---:|---:|---:|
| Q8_0 | 57 ms | 898 ms | 704 / 692 ms | 0.129 / 0.127 |
| FP16 | 135 ms | 831 ms | 861 / 794 ms | 0.158 / 0.145 |

両者の認識結果は一致しましたが、合成音声の一語をどちらも誤認識しました。
短い合成音声 1 件の動作確認であり、日本語の認識精度や実会議での性能、faster-whisper に対する優位性は未評価です。
同時にビルド処理も動いていたため、上記は厳密な比較ベンチマークではありません。

## Large-v3-turbo Q8_0 の実測

同じ固定 revision の `ggml-large-v3-turbo-q8_0.bin` を取得し、SHA-256 を照合して実行しました。
実行条件は Linux x86_64 / AMD EPYC VM、CPU 4 threads、release、Greedy / best-of 1 です。
追加のコンパイル処理を並行実行せず計測しています。

| 入力 | モデル読み込み | 認識時間 | RTF | プロセスの最大 RSS |
|---|---:|---:|---:|---:|
| 合成英語 5.46 秒 | 1.45 秒 | 初回 19.74 秒 / 2 回目 20.68 秒 | 3.62 / 3.79 | 約 1,239 MiB |
| 合成日本語 7.25 秒 | 1.25 秒 | 20.61 秒 | 2.84 | 約 1,236 MiB |

英語は用意した文と一致しました。日本語は「音声認識」を誤認識し、後半の予定確認の文は認識できました。
eSpeak NG の合成音声各 1 件による動作確認であり、実会議や自然な日本語の認識精度の評価ではありません。
この CPU 条件では音声の実時間より処理が遅いため、リアルタイム利用に向けた GPU 測定が必要です。
Turbo でも読み込まれたモデル種別は CLI 上 `large` と表示されます。Q8_0 は `weight_type: 7` で確認しました。

Large-v3（非 Turbo）の Q8_0 はこの固定 revision の配布一覧にはありません。
Large-v2 Q8_0 の取得は途中で停止し、推論していません。

## faster-whisper との CPU 比較

`faster_whisper_probe.py` は既存 `python/.venv` の faster-whisper 1.2.1 / CTranslate2 4.7.1 を使う比較用 CLI です。
モデルパスを明示するローカル実行専用で、ダウンロードは行いません。
両実装を CPU 4 threads、Greedy / best-of 1、temperature 0、VAD なし、過去の認識文を引き継がない設定に揃えました。
faster-whisper は `compute_type="int8"` を要求し、実際には `int8_float32` で動作しています。
Q8_0 と CTranslate2 INT8 は同一の量子化方式ではなく、decoder の細部や既定フィルタも異なるため、実用構成同士の比較です。

| 指標 | whisper.cpp Q8_0 | faster-whisper INT8 |
|---|---:|---:|
| 合成英語 5.46 秒：初回 / 2 回目 | 19.74 / 20.68 秒 | 10.48 / 10.35 秒 |
| 合成日本語 7.25 秒：初回 / 2 回目 | 20.61 秒 / 未計測 | 11.30 / 10.55 秒 |
| モデル読み込み（各言語の別プロセス） | 1.25〜1.45 秒 | 2.93〜4.34 秒 |
| 最大 RSS（読み込み・推論を含む） | 約 1,239 MiB | 約 1,639 MiB |
| モデル本体の取得サイズ | 約 874 MB | 約 1,618 MB |

英語・日本語とも認識文は区間の分け方を除いて一致し、日本語の「音声認識」の誤認識も同じでした。
faster-whisper の Python / ライブラリ import は別途約 0.21〜0.23 秒でした。
認識時間は遅延評価される segments を最後まで取り出した時間で、音声特徴量の生成を含みます。
モデル読み込み時間は OS のファイルキャッシュ条件を統制していないため、コールド起動の厳密な比較ではありません。
各言語の試験は独立したプロセスで、同時推論・同時コンパイル・同時ダウンロードは行っていません。

この短い CPU 試験では faster-whisper が約 1.8〜2 倍速く、読み込み時間と最大 RSS は whisper.cpp が少ない結果でした。
どちらも音声の実時間より遅く、GPU 性能・自然な音声での精度・長時間運用は未評価です。

使用したモデルは [mobiuslabsgmbh/faster-whisper-large-v3-turbo](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo)、
revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` です。
`model.bin` の SHA-256 は `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da` を照合しました。
この配布モデルは取得後、ロード時に INT8 へ変換されます。GGML のモデルファイルは流用できません。

取得が必要な場合だけ、以下を実行します。`HF_HUB_DISABLE_XET=1` はこの検証環境で転送が停滞した高速転送経路を避ける指定です。

```bash
faster_model="$(HF_HUB_DISABLE_XET=1 uv tool run --from huggingface-hub==1.11.0 hf download \
  mobiuslabsgmbh/faster-whisper-large-v3-turbo \
  model.bin config.json preprocessor_config.json tokenizer.json vocabulary.json \
  --revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf --format quiet)"

OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 HF_HUB_OFFLINE=1 \
  python/.venv/bin/python test/rust-whisper-backend/faster_whisper_probe.py \
  --model "$faster_model" --wav /path/to/sample.wav \
  --language ja --threads 4 --runs 2
```

モデルが既にある場合は `faster_model` にその snapshot ディレクトリを指定してください。
実測では一時ディレクトリの HF キャッシュを使い、ユーザーの録音・設定・認証情報は使用していません。

## GPU ビルド

デバイスに応じて feature を一つ選んでビルドし、実行時に `--device gpu` を指定します。

```bash
cargo build --release --locked --manifest-path test/rust-whisper-backend/Cargo.toml --features vulkan
# NVIDIA CUDA: --features cuda
# macOS Metal: --features metal
```

CUDA Toolkit、Vulkan SDK（shader compiler を含む）、macOS SDK など対応するビルド環境が必要です。
GPU feature のないバイナリへの `--device gpu` はエラーになります。
GPU feature があっても whisper.cpp 側で CPU にフォールバックする場合があり、実デバイスは標準エラーの初期化ログで確認してください。
GPU 実行は未検証です。配布時の CPU 命令セット・GPU runtime の同梱・署名は別途対応が必要です。

## 検証

```bash
cargo test --locked --manifest-path test/rust-whisper-backend/Cargo.toml
cargo clippy --locked --manifest-path test/rust-whisper-backend/Cargo.toml --all-targets -- -D warnings
```

自動テストは合成 WAV の形式・長さ制限と GPU feature の拒否を確認し、モデルを取得しません。
実モデル smoke は一時ディレクトリに取得した Tiny Q8_0 / FP16、Large-v3-turbo Q8_0 と合成音声で実施しました。
