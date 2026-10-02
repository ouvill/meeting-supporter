import { z } from "zod";
import { client } from "./generated/client.gen";

const capabilitiesSchema = z.object({ whisper_gpu: z.boolean().nullable() });

// Rust-only endpoint: reports build support, not physical GPU availability.
export async function getSpeechCapabilities(signal: AbortSignal) {
  const { data, error } = await client.get({
    url: "/api/stt/capabilities",
    signal,
  });
  if (error) throw new Error("Speech capabilities unavailable");
  return capabilitiesSchema.parse(data);
}
