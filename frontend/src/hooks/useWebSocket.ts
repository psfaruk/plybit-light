import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";

const WS_BASE = import.meta.env.VITE_WS_URL ??
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

export function useWebSocket() {
  const { activePair, setConnected, addCandle, setHistory, setSignal, setModelStatus } = useStore();
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    let active = true;
    let retryTimer: ReturnType<typeof setTimeout>;

    function connect() {
      if (!active) return;

      const url = `${WS_BASE}/${activePair}`;
      const socket = new WebSocket(url);
      ws.current = socket;

      socket.onopen = () => {
        if (!active) { socket.close(); return; }
        setConnected(true);
      };

      socket.onclose = () => {
        setConnected(false);
        if (active) retryTimer = setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        socket.close();
      };

      socket.onmessage = (ev: MessageEvent<string>) => {
        if (!active) return;
        try {
          const msg = JSON.parse(ev.data) as Record<string, unknown>;
          handleMessage(msg);
        } catch {/* ignore */}
      };
    }

    function handleMessage(msg: Record<string, unknown>) {
      // Backend broadcasts updates for ALL pairs to every client.
      // Filter so this chart only renders the symbol the user is viewing.
      const msgPair = msg.pair as string | undefined;
      const pairScoped = msg.type === "history" || msg.type === "candle_update"
        || msg.type === "candle_closed" || msg.type === "signal"
        || msg.type === "model_status" || msg.type === "model_retrained";
      if (pairScoped && msgPair && msgPair !== activePair) return;

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
      active = false;
      clearTimeout(retryTimer);
      const current = ws.current;
      if (current) {
        // Defer close if still CONNECTING to avoid the browser error
        if (current.readyState === WebSocket.CONNECTING) {
          current.onopen = () => current.close();
        } else {
          current.close();
        }
      }
    };
  }, [activePair]);
}
