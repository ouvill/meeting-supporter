# Rust 音声バックエンド: Silero + ReazonSpeech

## 方針

通常起動と会議の実行経路を Rust に移し、Python を必須依存から外す方針です。音声系の native 化を優先し、モデルに適した実行 engine を選びます。ローカル LLM の実行基盤は後で検討します。React / TypeScript の画面は維持し、対象を現在 Python が担っているバックエンド全体とします。

目的は音声演算の高速化だけでなく、起動時の依存環境準備の除去、配布の再現性、状態・リソース・キャンセルの管理改善です。[音声コア単体の測定](../rust-audio-core/README.md)では扱わなかった起動経路を対象にしています。

このディレクトリは検証用です。製品のバックエンドはまだ Python であり、全面移行は完了していません。実装範囲は **Python なしの操作受付 → モデルの遅延初期化 → Silero VAD → 発話区間の切り出し → ReazonSpeech の文字起こし** です。マイク、16 kHz mono PCM16 の逐次入力、WAV ファイルを使えます。会議履歴・AI 通信・画面接続は未実装です。

## 起動経路で確認したこと

参照コードは `51b65e1` です。以下は静的なコード調査であり、製品の各工程を実機で計測した結果ではありません。

| 起動工程 | 現在の実装 | 移行先 |
|---|---|---|
| Python 環境の準備 | [Tauri 起動](../../src-tauri/src/lib.rs)から毎回 `ensure_python_environment` を呼びます。[uv 管理](../../src-tauri/src/paths/uv.rs)で `uv sync --locked --no-dev` を実行し、失敗時は再試行します。変更がなくても確認コストは残ります。 | 通常起動から uv / Python / pip 環境を除去し、署名済みアプリと native runtime を配布します。 |
| バックエンド起動 | [プロセス管理](../../src-tauri/src/process.rs)で `uv run --no-sync uvicorn main:app` を起動し、認証付き `/health` をポーリングします。 | Tauri 内の Rust サービスを起動し、画面操作の受付をモデル準備と分離します。 |
| モジュール読み込み | [main.py](../../python/main.py)から音声・STT・AI provider・API の依存を広く import します。設定、secret store、サービス、AI bundle も組み立てます。 | 純粋な設定・状態・UI 接続を先に用意し、デバイス・モデル・外部 provider は必要時に初期化します。 |
| lifespan | [lifespan.py](../../python/app/lifespan.py)で情報 AI runtime の enter、DB 初期化、文脈読み込みなどが完了してから受付可能になります。 | 必須のローカル状態だけを起動条件にし、外部 runtime の接続失敗が設定画面を塞がないようにします。 |
| 音声準備 | WebSocket 接続後のレベル監視と `init_stt` によるモデル準備は別経路です。現在もすべてのモデルが `/health` 前にロードされるわけではありません。 | `app_ready`、`audio_ready`、`stt_ready` を別々に計測・表示します。 |

Rust に移しても、デバイス列挙、DB migration、モデルのファイル I/O、推論 runtime の初期化時間は残ります。起動経路から不要な処理を外す設計と、言語の移行を組み合わせます。

## 移行先の設計

実行境界・状態の所有者・保存・UI 契約は、[ADR-016: Rust 移行時の実行境界と状態の所有者](../../doc/adr/016-rust-runtime-and-ownership-boundaries.md)にまとめています。現在は Proposed です。

推奨案は Tauri 本体で会議を制御し、ONNX / ローカル STT を必要時起動の Rust speech-worker に隔離する構成です。この実装は ONNX のロード・推論・区間処理・文字起こしを提供する Rust library / CLI であり、製品用 supervisor、会議世代、binary IPC、強制停止期限は未実装です。この試作は分割処理型の一例です。製品設計は SpeechSession ごとに処理 plan を持ち、VAD・ASR・話者推定をまとめて提供する engine / service も扱います。統合方式や話者推定を試作に実装済みという意味ではありません。以下は試作で検討したモデルの適合性と再現手順です。

## モデルごとの実行基盤

ONNX はモデル形式であり、トークナイザー、特徴量抽出、パディング、デコーダ、区間分割まで自動で提供するものではありません。

- **Silero VAD**: 既存の約 208 KB の int8 モデルをそのまま使えます。今回 Rust の `ort` から実際に推論しました。
- **ReazonSpeech K2-v2**: 既存の encoder / decoder / joiner はすでに ONNX です。現在使っている sherpa-onnx の native API を Rust から呼ぶ方式を第一候補とします。推論 runtime は ONNX Runtime、特徴量抽出と transducer decoding は sherpa-onnx に任せ、Python binding を外します。Rust binding `sherpa-onnx 1.13.8` で接続し、合成日本語音声の認識結果を既存 Python 関数と比較済みです。CPU、1 thread、80 次元の特徴量、greedy search、前後各 0.9 秒の無音追加を維持します。
- **Whisper**: ONNX への統一を要求せず、whisper-rs / whisper.cpp を第一候補とします。モデルの互換性、現行 faster-whisper との比較、代替案は [ADR-016](../../doc/adr/016-rust-runtime-and-ownership-boundaries.md)にまとめています。`SileroWhisper` plan を実装しています。実行方法とモデル管理は [Rust バックエンドの Whisper.cpp](../../doc/development/rust-desktop-backend.md#whispercpp-の実行)を参照してください。
- **クラウド音声認識**: クライアントは Rust にできますが、サービス内部のモデル形式はこのアプリから制御できません。
- **ローカル LLM**: 今回は対象外です。音声系の移行後に ONNX Runtime GenAI などの対応モデル・実行環境を評価します。

最初は CPU を基準に一致を確認し、その後に execution provider ごとの GPU 利用と fallback を評価します。`ort` と sherpa-onnx の要求する ONNX Runtime ABI を合わせ、異なるバージョンの runtime を偶然同居させないことが重要です。`ort 2.0.0-rc.12` は API 24 を使います。VAD 単体比較は ONNX Runtime `1.24.4`、今回の ReazonSpeech 版は公式 sherpa-onnx 配布物に含まれる `1.28.2` を使い、同じ共有 runtime で Silero も実行します。ReazonSpeech 版は実行ファイルに隣接する runtime を使い、`--ort-library` による別 runtime の指定を拒否します。

Python 専用モデルを実行する必要がある場合は、製品から呼び出すことも許容します。条件と実行境界は [ADR-016](../../doc/adr/016-rust-runtime-and-ownership-boundaries.md#python-が不可欠なモデルは専用-adapter-から実行できる)を参照してください。この Silero + ReazonSpeech 経路は Python を呼びません。

## 試作の実装

- [src/session.rs](src/session.rs): `SpeechPlan` と `SpeechSession<Unprepared/Prepared>`、入力元ごとの状態・sample clock・世代、終了とリセットです。
- [src/error.rs](src/error.rs): `thiserror` の error enum です。文字列で失敗理由を比較しません。
- [src/reazon.rs](src/reazon.rs): ReazonSpeech のモデル検証、ロード、padding、認識です。認識器は複数発話で再利用します。
- [src/microphone.rs](src/microphone.rs): `cpal` による取得、`ringbuf` による上限付き転送、`rubato` による 16 kHz 変換、終了処理です。
- [src/wav.rs](src/wav.rs): WAV を 480 sample ずつ読み、全体をメモリーに保持せず認識します。
- [src/main.rs](src/main.rs): 操作受付と音声ワーカーの分離、最大 8 件のキュー、起動・終了処理です。モデル未導入でも `ready` を返します。
- [src/vad.rs](src/vad.rs): 動的な ONNX Runtime のロード、既存モデルの実行、512 sample の入力窓、入力元ごとの h/c、4 窓の無音遷移です。閾値は試作では 0.5 固定です。
- [src/protocol.rs](src/protocol.rs): 型付き request / response、状態、入力拒否の境界です。音声は 16 kHz・mono・PCM16 の 480 sample に限定しています。
- [../rust-audio-core/src/lib.rs](../rust-audio-core/src/lib.rs): 発話区間の切り出しと入力判定に再利用しています。

不正な JSON、未知のフィールド、範囲外の PCM、フレーム長の不一致を拒否します。1 request は最大 16 KiB で、超過時はプロセスを終了します。推論に失敗した場合は部分更新された状態を再利用せず、モデルを failed にして再準備を要求します。モデル path や音声をエラー応答へ含めません。

Rust の所有権だけでは native library 内の障害や音声のリアルタイム性を保証できません。本番では VAD / STT のジョブ所有者、会議世代、欠落時の扱い、停止期限、デバイス切断を契約として実装します。試作の終了は受付済み最大 8 件を処理して worker を join する方式で、実行中の native 推論の強制キャンセルや watchdog はありません。JSON Lines と音声配列のシリアライズも本番の capture 経路には使いません。

## ReazonSpeech 版のビルドと実行

以下は検証済みの Linux x86_64 CPU 用です。リポジトリのルートで実行します。ビルドには Rust 1.88 以上と C リンカーが必要です。Linux では CPAL の ALSA backend 用に `pkg-config` と ALSA 開発ヘッダー（Debian / Ubuntu: `sudo apt-get install pkg-config libasound2-dev`）も必要です。開発用の取得スクリプトには Python 3.12 以上を使いますが、生成したバックエンドの実行には不要です。

```bash
python3 test/rust-native-backend/prepare_assets.py
export SHERPA_ONNX_LIB_DIR="$PWD/test/rust-native-backend/target/assets/sherpa-onnx-v1.13.8-linux-x64-shared-lib/lib"
cargo build --release --locked --features reazonspeech --manifest-path test/rust-native-backend/Cargo.toml
test/rust-native-backend/target/release/meeting-native-backend --reazon-model test/rust-native-backend/target/models/reazonspeech
```

[assets.json](assets.json) はモデル revision、各ファイルの SHA-256、Linux 用 native archive と展開後ライブラリの SHA-256 を固定しています。取得は `target/` に限定し、モデルファイルは一時ファイルで検証後に配置します。モデルは約 160 MB、native archive は約 9.8 MB です。既に同じモデルがある場合は `--reazon-model` でそのディレクトリを指定できます。実行時に自動ダウンロードはしません。

`SHERPA_ONNX_LIB_DIR` を指定しない場合、依存 crate の build script が native archive を取得します。再現検証では上記の検証済み artifact を明示してください。これは開発用セットアップであり、一般利用者向け installer はまだ作成していません。Windows / macOS の native 配布と実行は未検証です。

モデルの初期化前に protocol 2 の `ready` が返ります。次を 1 行ずつ標準入力へ送り、各応答を待って進められます。

```json
{"id":1,"command":{"op":"health"}}
{"id":2,"command":{"op":"prepare"}}
{"id":3,"command":{"op":"finish","role":"self"}}
{"id":4,"command":{"op":"reset","role":"self"}}
{"id":5,"command":{"op":"shutdown"}}
```

- `ready.transcription_available` は選択した plan の認識能力を示します。モデル準備の完了は `prepared` 応答、全モデルの状態は `health.models` で確認します。
- `audio` は `role`（`self` / `other`、旧試作の `user` も受理）と `pcm`（i16 配列、480 sample）を受け取り、VAD 確率と完成した区間を返します。入力元は個人単位の話者 ID ではありません。
- `segment.recognition` は `recognized`（`text` 付き）、`rejected`（音声 gate 不合格）、`not_requested`（VAD 単体 plan）の enum です。空の認識文字列も有効な結果であり、文字を補完しません。
- `start_sample` / `end_sample` は、その入力元の世代内の 16 kHz sample 位置です。終端は含みません。区間には preroll があり、隣接区間は重なることがあります。単語単位の時刻や話者識別は返しません。
- `finish` は残った区間を認識し、その入力元を終了します。再度の `finish` は区間を重複出力しません。終了後の `audio` は `source_finished` です。
- `reset` は未確定区間と VAD の状態を破棄し、sample 位置を 0 に戻して `generation` を増やします。モデルは再ロードしません。
- `shutdown` / EOF は受付済みの request を処理して終了しますが、未確定区間は破棄します。残りも認識したい場合は、両入力元の `finish` 応答を待ってから `shutdown` します。

応答順は操作によって前後するため、呼び出し側は一意な `id` で対応付けます。キューは最大 8 件で、満杯は `busy` です。検証クライアントは各音声応答を待って次を送ります。欠落した音声を飛ばして継続すると sample clock と実時間がずれるため、ライブ取得側は欠落を記録して `reset` し、新しい世代から開始する必要があります。推論中も health は別 thread で処理しますが、native 推論を強制停止する supervisor はまだありません。

通信は親子プロセスの標準入出力だけで、ネットワークの待受はありません。モデル・ライブラリの path は起動者が管理する信頼済みファイルに限定し、UI 入力には公開しません。native stderr には engine が path などを出す可能性があるため、製品ログへそのまま転送しません。

### マイクから使う

上記の ReazonSpeech 版を再ビルドし、`--mic` を付けて起動します。モデルの準備完了後に既定の入力デバイスで取得を開始し、認識結果を WAV モードと同じ JSON Lines で標準出力へ返します。準備・取得開始・終了の案内は標準エラーへ出します。

```bash
test/rust-native-backend/target/release/meeting-native-backend \
  --reazon-model test/rust-native-backend/target/models/reazonspeech \
  --mic
```

入力デバイスの一覧と番号指定も利用できます。一覧はデバイスを開いて録音しません。番号は列挙時の順序なので、接続状態が変わった場合は再確認してください。

```bash
test/rust-native-backend/target/release/meeting-native-backend --list-input-devices
test/rust-native-backend/target/release/meeting-native-backend \
  --reazon-model test/rust-native-backend/target/models/reazonspeech \
  --mic --input-device 0 --seconds 30
```

`--seconds` はモデル準備後の取得時間の目安です（デバイス開始処理とコールバック間隔分の誤差を含みます）。省略時は Ctrl+C まで取得します。Ctrl+C を 1 回押すと次の入力コールバックから音声の追加を止め、バッファ内の音声と最後の発話を処理して終了します。再度押すと強制終了します。推論の停止期限は未実装なので、native 推論が停止しない場合は 2 回目の割り込みを使います。

CPAL が返す既定の対応形式を使い、8–192 kHz・1–32 channel の整数 / 浮動小数点入力を受け付けます。コールバックでは正規化・channel 平均と事前確保したリングバッファへの書き込みだけを行います。別 thread の処理側でアンチエイリアス付きの 16 kHz 変換、480 sample 単位の組み立て、VAD と認識を行います。sinc 補間に必要な先読みは停止時のゼロ入力で排出し、出力の先頭を捨てずに元の sample 位置を保ちます（補間格子の位相差は 1 sample 未満）。最後のフレームだけ最大 479 sample をゼロ埋めします。

バッファは入力音声 5 秒分です。超過、デバイスエラー、不正な sample は明示的なエラーで停止し、未確定区間を破棄します。音声が欠落した前後を接続したり、勝手に別デバイスへ切り替えたりしません。出力先が詰まった場合も取得バッファが上限に達すれば同様に停止します。音声ファイルへの保存や外部送信は行いません。

この CLI は取得と推論を同じプロセスの別 thread で扱います。製品版での取得側と推論プロセスの分離は [ADR-016](../../doc/adr/016-rust-runtime-and-ownership-boundaries.md) に従って別途組み込みます。

この環境には実マイクがないため、実機での入力・権限・切断は未検証です。合成波形で変換・バッファ超過を検証し、合成日本語音声をコールバックと同じ処理から ReazonSpeech まで流す実モデルテストを用意しています。macOS のマイク権限設定と Windows / Linux の入力デバイス設定は配布時に別途確認します。システム音声のループバック取得は含みません。

### WAV から使う

```bash
test/rust-native-backend/target/release/meeting-native-backend \
  --reazon-model test/rust-native-backend/target/models/reazonspeech \
  --wav /path/to/synthetic-16k-mono.wav
```

16 kHz mono PCM16 WAV を受け取り、完成した区間を JSON Lines で標準出力へ返します。違う sample rate / channel / sample format は拒否し、暗黙の変換はしません。最後の 480 sample 未満はゼロ埋めし、`end_sample` に最大 479 sample の padding を含みます。全 WAV をロードせず、最大約 30 秒のモデル入力とフレーム単位のメモリーを使います。

### VAD 単体の比較用ビルド

従来の Silero 比較用に、ReazonSpeech の native link を含まない構成も残しています。出力先を分け、完全版を上書きしません。

```bash
cargo build --release --locked --manifest-path test/rust-native-backend/Cargo.toml \
  --target-dir test/rust-native-backend/target/vad-only
test/rust-native-backend/target/vad-only/release/meeting-native-backend \
  --ort-library /path/to/libonnxruntime.so.1.24.4
```

この構成では `transcription_available: false` です。ReazonSpeech 版と異なり、ORT 自体も `prepare` までロードしません。

## 検証と測定

### Silero 単体の比較

Python は比較用のテストドライバーだけに使います。以下は既存 lockfile に合わせた ONNX Runtime の Python 配布物を比較用環境へ導入する手順です。Rust 子プロセスはその中の native library を直接ロードしており、Python を実行しません。テストでは子プロセスの `PATH` を空にし、アプリの credential も渡していません。

```bash
uv venv --python 3.14 test/rust-native-backend/.venv
uv pip install --python test/rust-native-backend/.venv/bin/python numpy==2.4.4 pydantic==2.13.2 onnxruntime==1.24.4
test/rust-native-backend/.venv/bin/python test/rust-native-backend/verify.py \
  --binary test/rust-native-backend/target/vad-only/release/meeting-native-backend
cargo test --locked --manifest-path test/rust-native-backend/Cargo.toml
cargo clippy --locked --all-targets --manifest-path test/rust-native-backend/Cargo.toml -- -D warnings
```

Windows では `.venv/Scripts/python.exe` と実行ファイルの `.exe` を使い、`--binary` / `--ort-library` で必要な path を指定します。Windows / macOS の実行・配布は未検証です。

以下の数値は ReazonSpeech 接続前の VAD 単体試作の測定です。Linux x86_64、Rust 1.98.1 release、Python 比較環境 3.14.4、NumPy 2.4.4、ONNX Runtime 1.24.4 での結果です。15 回とも別プロセスを起動し、OS のファイルキャッシュはウォームです。コールドブートや製品全体の起動速度は測定していません。

| 測定対象 | 中央値 | 最大値 |
|---|---:|---:|
| 子プロセス起動から操作受付応答の受信まで | 1.03 ms | 1.15 ms |
| worker 内の ONNX Runtime + Silero session 作成 | 29.55 ms | 31.20 ms |

前者は親側のプロセス作成・標準出力受信・テストドライバーのスケジューリングを含みます。後者は Rust 内部の時計で、キュー待ちと IPC、初回推論を含みません。当時の VAD 単体バイナリは約 1.27 MB ですが、外部の ONNX Runtime 共有ライブラリや STT モデルはこのサイズに含みません。製品の Python バックエンドと同じ機能・条件での起動比較ではないため、製品の高速化倍率は算出していません。

比較は、数式で生成した母音状波形・ノイズ・無音を 2 話者に交互に渡した 360 フレームです。現行 Python の `SileroVadEngine` と発話判定が一致し、窓ごとの確率の最大絶対差は約 `2.98e-8` でした。認識対象に採用された 2 区間について、Python の `ReazonSpeechStage` と終端位置・サンプル数を比較しました。これはモデルの配線と状態管理の検証であり、自然な会話の VAD 精度や文字起こし精度の評価ではありません。

runtime 不在でも操作受付可能であること、ロード失敗と再試行、prepare と並行した health、準備前の音声拒否、不正入力、入力元の状態分離、reset、shutdown、過大入力の拒否も検証しました。従来の区間コアには別途 60 ケースの音声サンプル一致比較があります。

### ReazonSpeech の実モデル検証

[verify_reazon.py](verify_reazon.py) は Open JTalk で固定の日本語文を合成し、Rust に渡した区間を既存 Python の `transcribe_reazonspeech` にも渡して比較します。録音や会議データは使いません。テスト用の Open JTalk、48 kHz の voice と辞書を別途用意します。検証時は pyopenjtalk の `mei_normal.htsvoice`（SHA-256 `f3be49a6838904a6c218790b64e07c3e83c1886e995dca284b413caab19184de`）を使用しました。voice は製品に同梱しません。

```bash
uv pip install --python test/rust-native-backend/.venv/bin/python sherpa-onnx==1.12.31
test/rust-native-backend/.venv/bin/python test/rust-native-backend/verify_reazon.py \
  --model test/rust-native-backend/target/models/reazonspeech \
  --voice /path/to/mei_normal.htsvoice \
  --dictionary /path/to/open-jtalk-dictionary
cargo test --locked --features reazonspeech --manifest-path test/rust-native-backend/Cargo.toml
cargo clippy --locked --all-targets --features reazonspeech --manifest-path test/rust-native-backend/Cargo.toml -- -D warnings
```

比較側は製品 lockfile と同じ Python `sherpa-onnx 1.12.31`、Rust 側は `1.13.8` です。Linux x86_64 の実測では、2 発話の文字列が Python と一致し、WAV / PCM の区間・認識結果も一致しました。別入力元へ交互に無音を送った場合の分離、終了後の入力拒否、finish の冪等性、reset による破棄と世代更新、モデル不在時の再試行、不正な WAV、キュー満杯時の `busy` 応答も検証しています。typestate の準備前入力禁止は compile-fail doctest で確認します。

2 回の検証実行で、操作受付は約 3.4 ms と 70.8 ms、Silero + ReazonSpeech の準備は約 4.7 秒と 5.4 秒でした。native library のビルド・配置を挟んでおり、条件を揃えた起動ベンチマークではありません。準備後は同じ認識器を再利用します。この値は製品全体の起動速度や認識精度の保証ではなく、自然な会議音声・GPU・他 OS の比較も未実施です。

### マイク処理経路の合成音声テスト

通常の Rust テストで、sample の変換と channel 平均、バッファ超過後の停止、NaN の拒否、8 / 16 / 44.1 / 48 / 96 kHz の時刻・波形・終端 padding、高周波の折り返し抑制を確認します。実モデルを使うテストは、録音ではなく Open JTalk で作った 48 kHz mono PCM16 WAV（「これは音声認識の動作確認です。明日の会議は午前十時に始まります。」）を指定します。

```bash
export MEETING_TEST_SYNTHETIC_WAV=/path/to/synthetic-48k.wav
export MEETING_TEST_ORT_LIBRARY="$SHERPA_ONNX_LIB_DIR/libonnxruntime.so"
export MEETING_TEST_REAZON_MODEL="$PWD/test/rust-native-backend/target/models/reazonspeech"
cargo test --locked --features reazonspeech --manifest-path test/rust-native-backend/Cargo.toml \
  synthetic_capture_through_resampling_and_real_recognition -- --ignored
```

このテストは合成音声を stereo にして取得コールバックの処理へ渡し、リングバッファ・resampler・Silero・ReazonSpeech を通して日本語の認識を確認します。OS のマイクデバイスそのものの検証を代替するものではありません。

## 製品移行との関係

段階的な置換境界と採用前の検証条件は [ADR-016](../../doc/adr/016-rust-runtime-and-ownership-boundaries.md)を参照してください。この試作の起動時間は、製品の UI、DB、音声取得、文字起こしまで含めた時間ではありません。起動受付と最初に会議を開始できるまでの時間を分けて評価します。

## ライセンスと配布境界

製品の依存関係は変更していません。試作用 Cargo.lock と、[試作の第三者ライセンス通知](THIRD-PARTY-NOTICES.txt)を同じディレクトリに置いています。通知は既存のライセンス監査処理を利用して生成します。

```bash
python3 test/rust-native-backend/generate_notices.py
python3 test/rust-native-backend/generate_notices.py --check
```

この通知は ReazonSpeech feature を含む Rust 依存と埋め込み Silero の範囲です。取得した sherpa-onnx / ONNX Runtime の native library と ReazonSpeech モデルはリポジトリに同梱せず、この通知には含めていません。製品化時は公式 native runtime のバージョン・ABI・OS/architecture・ハッシュを固定し、その配布物の LICENSE / ThirdPartyNotices を含め、製品側の `npm run licenses:generate` に統合します。Python wheel の開発環境をユーザーに作らせる構成にはしません。

## 日本語の句読点復元

ReazonSpeech の確定発話に、任意の `--punctuation-model` で「、」「。」を
追加できます。Rust と既存の ONNX Runtime だけで推論し、実行時に
Python、PyTorch、ネットワーク接続は使いません。

モデルは [bobfromjapan/bert_japanese_punctuation](https://huggingface.co/bobfromjapan/bert_japanese_punctuation)
を ONNX に変換し、MatMul の重みを INT8 に量子化したものです。
[東北大学の文字単位 BERT](https://huggingface.co/tohoku-nlp/bert-base-japanese-char-v3)
が基になっています。独立した二つの sigmoid の閾値を作者と同じ 0.1 にし、
句点を優先します。「？」の推定には対応しません。

### モデルの準備

この作業は開発・配布物作成時に一度だけ行います。ユーザーへの配布物には、
変換済みモデルと語彙、モデル仕様、ライセンス通知を含めます。
以下は検証済みの Linux x86_64 / CPU 用の手順で、リポジトリルートから実行します。

```bash
uv venv --python 3.12 test/rust-native-backend/target/punctuation-export-env
uv pip install --python test/rust-native-backend/target/punctuation-export-env/bin/python \
  torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python test/rust-native-backend/target/punctuation-export-env/bin/python \
  transformers==4.57.6 onnx==1.20.1 onnxruntime==1.24.4
test/rust-native-backend/target/punctuation-export-env/bin/python \
  test/rust-native-backend/export_punctuation.py
```

[変換スクリプト](export_punctuation.py)は
[assets.json](assets.json) の revision と SHA-256 を検証して取得し、
`torch.load(weights_only=True)` で重みだけを読み込みます。
配布元の Python コードは実行しません。PyTorch と FP32 ONNX の数値比較、
INT8 化後の句読点位置の比較、可変長入力の検証を通過した場合だけ
`target/models/punctuation-bert/` に出力します。

出力モデルは約 109 MB（約 104 MiB）です。
`export.json` に出典、変換ツールのバージョン、生成ファイルのハッシュを記録します。
再実行時はその記録を検証し、正常な既存ファイルを再利用します。
ReazonSpeech と native runtime も同時に準備する場合は、上の変換環境の Python で
`prepare_assets.py --with-punctuation` を実行できます。

### マイクで使う

先に上記の ReazonSpeech feature 付き release ビルドを用意してください。

```bash
test/rust-native-backend/target/release/meeting-native-backend \
  --reazon-model test/rust-native-backend/target/models/reazonspeech \
  --punctuation-model test/rust-native-backend/target/models/punctuation-bert \
  --mic
```

`--mic` の代わりに `--wav synthetic.wav` を指定すると、
16 kHz mono PCM16 WAV に適用できます。JSONL の音声入力にも同じ設定が適用されます。
句読点機能を省略した場合はモデルをロードせず、従来の JSON 出力を変更しません。

出力例は次のとおりです。処理時間は説明用の値です。

```json
{
  "status": "recognized",
  "text": "これは音声認識の動作確認です",
  "punctuation": {
    "status": "applied",
    "text": "これは音声認識の動作確認です。",
    "processing_ms": 30.0
  }
}
```

`recognition.text` は常に認識原文です。後処理が失敗した場合は原文を返しつつ、
`punctuation` を `{"status":"failed","code":"inference_failed"}` などにします。
明示したモデルをロードできない場合は prepare 自体が
`punctuation_load_failed` で失敗します。

### 文字の保持と処理範囲

原文の文字は未知文字や絵文字を含めて削除・置換しません。
モデル入力では空白・制御文字・既存の句読点を除き、
ASCII 英字は小文字化しますが、出力では原文の文字と位置を保持します。
既存の句読点の直前・直後には追加しません。

長文は 128 文字ずつ、前後最大 64 文字の文脈を付けて処理します。
重複する文脈の予測は出力せず、各文字を一度だけ出力します。
文脈は発話内に限定し、別の入力元や過去の発話にはまたがりません。
採用した文字単位の入力処理は、日本語の句読点復元を対象とします。
一般的な多言語 BERT tokenizer の完全な代替ではありません。

認識直後の同期後処理なので、結果返却には句読点の推論時間が加わります。
原文の即時通知と同一発話への非同期更新は実装していません。
既に句読点を返す他の ASR へ自動適用する機能もありません。

### 検証と限界

```bash
export MEETING_TEST_ORT_LIBRARY="$SHERPA_ONNX_LIB_DIR/libonnxruntime.so"
export MEETING_TEST_PUNCTUATION_MODEL="$PWD/test/rust-native-backend/target/models/punctuation-bert"
cargo test --release --locked --features reazonspeech \
  --manifest-path test/rust-native-backend/Cargo.toml \
  punctuation::tests::synthetic_real_model -- --ignored --nocapture
```

実モデルのテストでは、次の合成文の句読点を期待値と比較します。

- 「今日は晴れです明日は雨です」
  →「今日は晴れです。明日は雨です。」
- 「私は資料を作りますので田中さんは会場を予約してください」
  →「私は資料を作りますので、田中さんは会場を予約してください。」

空文字列、未知文字、既存の句読点、740 文字の長文の原文保持も検証します。
合成 WAV を Silero、ReazonSpeech、句読点モデルへ通す経路を確認しています。
Linux x86_64、CPU 1 thread、ORT 1.28.2、release の単回測定では、
モデル準備（短い確認推論を含む）が約 372 ms、
37 文字が約 84 ms、740 文字が約 2.53 秒でした。
合成 WAV の 2 発話への追加処理は約 30 ms と 35 ms でした。
これらはキュー待ち・音声認識時間を含まず、コールド起動の保証でもありません。

モデルは小説『明暗』のテキストで学習されています。
合成文の回帰テストは会議での精度保証ではなく、専門用語や言いよどみを含む
自然な会話での評価は未実施です。音声の抑揚は入力しません。
VAD が文の途中で区切ると、不適切な句点が付く可能性があります。
物理マイクによる今回の検証は行っていません。

### 句読点モデルのライセンス

句読点モデルと元の BERT は、ともにモデルカードで Apache-2.0 を表明しています。
取得した二つのカードを変換済みモデルと一緒に保持します。
変換済みモデルはリポジトリに含めず、Rust 依存の第三者ライセンス通知の対象外です。
製品への同梱時は、モデルの出典、ONNX 変換・量子化の変更内容、
Apache-2.0 のライセンス本文と必要な通知を配布物へ統合してください。

## Tauri からの操作

画面との接続は [Rust ローカル文字起こし](../../doc/development/rust-local-speech.md)を参照してください。
`--mic --desktop` は desktop supervisor 専用の protocol 1 を使用します。
標準入力の stop または EOF で取得を止め、確定結果を flush して終了します。
通常の `--mic` と PCM JSONL protocol 2 の出力形式は変更しません。

## Whisper ワーカーの単体入力

既定の `whisper` feature で組み込みます。ReazonSpeech と併用したビルドでは共通の ONNX Runtime で Silero を実行します。
`--no-default-features` は Whisper を含めないビルドです。

```bash
test/rust-native-backend/target/release/meeting-native-backend \
  --whisper-model /path/to/ggml-large-v3-turbo-q8_0.bin \
  --inference-device cpu --language ja --wav /path/to/synthetic.wav
```

`--wav` を省略すると既存の protocol 2 の JSON Lines を受け付けます。
`prepare` 応答には `execution_device`（`cpu` / `gpu`）が追加されます。
`--inference-device` は `auto` / `cpu` / `gpu`、`--language` は `ja` / `en` / `auto` です。
ReazonSpeech・句読点モデルとの同時指定は拒否します。
