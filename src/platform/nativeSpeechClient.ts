import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

const errorSchema = z.enum([
  "busy",
  "worker_unavailable",
  "model_unavailable",
  "microphone_unavailable",
  "capture_failed",
  "capture_overflow",
  "worker_failed",
  "invalid_worker_output",
  "prepare_timeout",
  "inference_timeout",
  "stop_timeout",
  "transcript_limit",
  "export_failed",
]);
const snapshotSchema = z.object({
  generation: z.number().int().nonnegative(),
  revision: z.number().int().nonnegative(),
  phase: z.enum([
    "idle",
    "starting",
    "listening",
    "stopping",
    "stopped",
    "failed",
  ]),
  error: errorSchema.nullable(),
  transcripts: z.array(
    z.object({
      generation: z.number().int().nonnegative(),
      sequence: z.number().int().nonnegative(),
      text: z.string(),
      raw_text: z.string(),
      start_sample: z.number().int().nonnegative(),
      end_sample: z.number().int().nonnegative(),
      punctuation_failed: z.boolean(),
    }),
  ),
});
export type NativeSnapshot = z.infer<typeof snapshotSchema>;
export type NativeSpeechError = z.infer<typeof errorSchema>;
export const errorMessages: Record<NativeSpeechError, string> = {
  export_failed:
    "テキストを保存できませんでした。保存先と空き容量を確認してください。",
  busy: "文字起こしの処理中です。停止が完了するまでお待ちください。",
  worker_unavailable: "音声処理プログラムが見つからないか、起動できません。",
  model_unavailable:
    "音声モデルを読み込めません。モデルのフォルダーを確認してください。",
  microphone_unavailable:
    "マイクが見つかりません。接続とアクセス権限を確認してください。",
  capture_failed:
    "マイクからの音声取得が停止しました。接続を確認して再開してください。",
  capture_overflow: "処理が音声に追いつかなかったため停止しました。",
  worker_failed:
    "音声処理が予期せず終了しました。表示済みの文字起こしは保持されています。",
  invalid_worker_output: "音声処理との通信に問題が発生したため停止しました。",
  prepare_timeout: "モデルの準備が時間内に完了しませんでした。",
  inference_timeout: "音声処理の応答が止まったため終了しました。",
  stop_timeout:
    "停止に時間がかかったため強制終了しました。末尾の音声は未確定の可能性があります。",
  transcript_limit:
    "この画面に保持できる上限に達しました。テキストを保存し、内容を消去して再開してください。",
};
export function speechErrorMessage(value: unknown): string {
  const result = errorSchema.safeParse(value);
  return result.success
    ? errorMessages[result.data]
    : "操作を完了できませんでした。もう一度お試しください。";
}
export async function runtimeMode() {
  return z
    .enum(["rust", "python", "rust-backend"])
    .parse(await invoke<unknown>("get_runtime_mode"));
}
export async function speechDefaults() {
  return z
    .object({
      model_directory: z.string(),
      punctuation_directory: z.string().nullable(),
    })
    .parse(await invoke<unknown>("native_speech_defaults"));
}
async function snapshotCommand(
  command: string,
  args?: Record<string, unknown>,
): Promise<NativeSnapshot> {
  return snapshotSchema.parse(await invoke<unknown>(command, args));
}
export const exportSpeech = async (path: string) =>
  z.null().parse(await invoke<unknown>("native_speech_export", { path }));
export const speechSnapshot = () => snapshotCommand("native_speech_snapshot");
export const stopSpeech = () => snapshotCommand("native_speech_stop");
export const clearSpeech = () => snapshotCommand("native_speech_clear");
export const startSpeech = (model: string, punctuation: string | null) =>
  snapshotCommand("native_speech_start", {
    options: {
      model_directory: model,
      punctuation_directory: punctuation,
      device: null,
    },
  });
