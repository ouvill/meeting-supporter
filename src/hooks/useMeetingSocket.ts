import { useCallback, useEffect, useRef } from "react";
import type { WsMessage } from "../types";
import { useMeetingStore } from "../store/meetingStore";
import { InboundMessageSchema } from "../types/wsMessages";

export function useMeetingSocket(
  apiPort: number | null,
  apiAuthToken: string | null,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const dispatch = useMeetingStore((s) => s.dispatch);
  const setConnected = useMeetingStore((s) => s.setConnected);
  const reset = useMeetingStore((s) => s.reset);

  useEffect(() => {
    if (!apiPort || !apiAuthToken) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;

    function connect() {
      if (!active) return;
      const ws = new WebSocket(`ws://127.0.0.1:${apiPort}/ws`, [
        `auth.${apiAuthToken}`,
      ]);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!active) return;
        setConnected(true);
      };

      ws.onclose = () => {
        if (!active) return;
        wsRef.current = null;
        setConnected(false);
        useMeetingStore.setState({
          statusText: "再接続中...",
          sttInitRequested: false,
        });
        timer = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        if (!active) return;
        useMeetingStore.setState({ statusText: "接続エラー" });
      };

      ws.onmessage = (e: MessageEvent) => {
        if (!active) return;
        try {
          const raw = JSON.parse(e.data as string) as unknown;
          const parsed = InboundMessageSchema.safeParse(raw);
          if (!parsed.success) {
            console.warn("[WS] Invalid message:", parsed.error);
            return;
          }
          dispatch(parsed.data);
        } catch {
          /* ignore malformed JSON */
        }
      };
    }

    connect();

    return () => {
      active = false;
      clearTimeout(timer);
      wsRef.current?.close();
      wsRef.current = null;
      reset();
    };
  }, [apiPort, apiAuthToken, dispatch, setConnected, reset]);

  const send = useCallback((msg: WsMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
      if (msg.type === "init_stt") {
        useMeetingStore.setState({ sttInitRequested: true });
      }
      if (msg.type === "shutdown_stt") {
        useMeetingStore.setState({ sttInitRequested: false });
      }
    }
  }, []);

  return { send };
}
