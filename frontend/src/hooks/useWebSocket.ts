import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";

const WS_BASE = import.meta.env.VITE_WS_URL ?? `ws://${location.host}/ws`;

export function useWebSocket() {
  const { activePair, setConnected, addCandle, setHistory, setSignal, setModelStatus } = useStore();
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    let retryTimer: ReturnType<typeof setTimeout>;

    function connect() {
      const url = `${WS_BASE}/${activePair}`;
      const socket = new WebSocket(url);
      ws.current = socket;

      socket.onopen = () => {
        setConnected(true);
        console.log(`WS connected: ${url}`);
      };

      socket.onclose = () => {
        setConnected(false);
        retryTimer = setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        socket.close();
      };

      socket.onmessage = (ev: MessageEvent<string>) => {
        try {
          const msg = JSON.parse(ev.data) as Record<string, unknown>;
          handleMessage(msg);
        } catch {/* ignore */}
      };
    }

    function handleMessage(msg: Record<string, unknown>) {
      switch (msg.type) {
        case "history":
          setHistory(60, (msg.candles as Parameters<typeof setHistory>[1]) ?? []);
          break;

        case "candle_update": {
          const c = msg.candle as { epoch: number; open: number; high: number; low: number; close: number };
          const gran = (msg.granularity as number) ?? 60;
          addCandle(gran, c, false);
          break;
        }

        case "candle_closed": {
          const c = msg.candle as { epoch: number; open: number; high: number; low: number; close: number };
          const gran = (msg.granularity as number) ?? 60;
          addCandle(gran, c, true);
          break;
        }

        case "signal":
          setSignal(msg as unknown as Parameters<typeof setSignal>[0]);
          break;

        case "model_status":
        case "model_retrained":
          setModelStatus({
            is_trained: (msg.is_trained as boolean) ?? false,
            accuracy:   (msg.accuracy   as number)  ?? 0,
            n_candles:  (msg.n_candles  as number)  ?? 0,
          });
          break;
      }
    }

    connect();

    return () => {
      clearTimeout(retryTimer);
      ws.current?.close();
    };
  }, [activePair]);
}
