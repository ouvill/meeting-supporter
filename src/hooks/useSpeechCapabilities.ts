import { useEffect, useState } from "react";
import { getSpeechCapabilities } from "../api/speechCapabilities";

type GpuSupport = "loading" | "supported" | "unsupported" | "unknown";

export function useSpeechCapabilities(enabled: boolean): GpuSupport {
  const [support, setSupport] = useState<GpuSupport>("loading");
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setSupport("loading");
    void getSpeechCapabilities(controller.signal)
      .then(({ whisper_gpu }) => {
        if (controller.signal.aborted) return;
        setSupport(
          whisper_gpu === null
            ? "unknown"
            : whisper_gpu
              ? "supported"
              : "unsupported",
        );
      })
      .catch(() => {
        if (!controller.signal.aborted) setSupport("unknown");
      });
    return () => controller.abort();
  }, [enabled]);
  return support;
}
