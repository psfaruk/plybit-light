import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";

const WS_BASE = import.meta.env.VITE_WS_URL ??
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

const API_BASE = import.meta.env.VITE_API_URL ??
  `${location.protocol}//${location.host}`;

export function useWebSocket() {
  const {
    activePair, activeTf, refreshTick, signalRefreshTick,
    setConnected, addCandle, setHistory,
    setSignal, setModelStatus, setBlockReason, addMarker,
  } = useStore();
  const ws = useRef<WebSocket | null>(null);

  // Fetch history via REST whenever pair, timeframe, or refresh tick changes
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/candles/${activePair}?granularity=${activeTf}&count=300`);
        if (!r.ok) return;
        const data = await r.json() as { candles?: Array<{ epoch: number; open: number; high: number; low: number; close: number }> };
        if (cancelled) return;
        setHistory(activeTf, data.candles ?? []);
      } catch {/* ignore */}
    })();
    return () => { cancelled = true; };
  }, [activePair, activeTf, refreshTick]);

  // Fetch the last cached signal whenever signal-refresh is triggered or pair changes
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/signal/${activePair}`);
        if (!r.ok) return;
        const data = await r.json() as { signal?: Record<string, unknown> | null };
        if (cancelled || !data.signal) return;
        setSignal(data.signal as unknown as Parameters<typeof setSignal>[0]);
        setBlockReason("");
      } catch {/* ignore */}
    })();
    return () => { cancelled = true; };
  }, [activePair, signalRefreshTick]);

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
      const msgPair = msg.pair as string | undefined;
      const msgType = msg.type as string;

      // ── Signals: capture markers for ALL pairs (Telegram parity) ──
      // Backend broadcasts signals for every pair; we want chart arrows
      // to appear when the user later switches to that pair.
      if (msgType === "signal" && msgPair) {
        const sig = msg as unknown as { signal?: string; grade?: string; confidence?: number; pair?: string };
        if (sig.signal === "GREEN" || sig.signal === "RED") {
          // Use the most-recent candle epoch for this pair to anchor the arrow
          // (we only have access to active-pair candles here; backend includes
          //  candle_open_time in the payload for cross-pair anchoring).
          const epoch = (msg.candle_open_time as number | undefined) ??
                        (msg.epoch as number | undefined) ??
                        Math.floor(Date.now() / 1000 / 60) * 60;
          addMarker({
            pair:       sig.pair ?? msgPair,
            tf:         60,
            epoch,
            direction:  sig.signal,
            grade:      sig.grade ?? "MODERATE",
            confidence: sig.confidence ?? 0,
            createdAt:  Date.now(),
          });
        }
      }

      // Pair-scoped messages — only for the chart we're viewing
      const pairScoped = msgType === "history" || msgType === "candle_update"
        || msgType === "candle_closed" || msgType === "signal"
        || msgType === "signal_blocked"
        || msgType === "model_status" || msgType === "model_retrained";
      if (pairScoped && msgPair && msgPair !== activePair) return;

      switch (msgType) {
        case "history": {
          const gran = (msg.granularity as number) ?? 60;
          setHistory(gran, (msg.candles as Parameters<typeof setHistory>[1]) ?? []);
          break;
        }

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
          setBlockReason("");
          break;

        case "signal_blocked":
          setBlockReason((msg.reason as string) ?? "blocked");
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
        if (current.readyState === WebSocket.CONNECTING) {
          current.onopen = () => current.close();
        } else {
          current.close();
        }
      }
    };
  }, [activePair]);
}
