import { create } from "zustand";

export interface Candle {
  epoch:  number;
  open:   number;
  high:   number;
  low:    number;
  close:  number;
  volume?: number;
}

export interface Signal {
  signal:      "GREEN" | "RED" | "SKIP";
  confidence:  number;
  grade:       "ELITE" | "HIGH" | "MODERATE" | "SKIP";
  trade_type:  string;
  expiry_bars: number;
  max_delay_sec: number;
  patterns:    string[];
  ai_models:   Record<string, number>;
  candle_reaction: {
    bull_score: number;
    bear_score: number;
    net_score:  number;
    pin_bar?:   number;
    engulfing?: number;
  };
  mtf_context: {
    direction:  string;
    agreement:  number;
    killzone:   string;
    in_killzone:boolean;
    tfs:        Record<string, { bias: string; strength: number }>;
  };
  smc_context: Record<string, unknown>;
  bayes_uncertainty?: { bayes_mean: number; bayes_std: number };
  layers?: { mtf_pts: number; smc_pts: number; react_pts: number; ai_pts: number };
  window_plan?: Array<{ epoch: number; confidence: number; direction: string; grade: string }>;
  timestamp?: number;
}

export interface ModelStatus {
  is_trained: boolean;
  accuracy:   number;
  n_candles:  number;
}

export interface SignalMarker {
  pair:       string;
  tf:         number;
  epoch:      number;
  direction:  "GREEN" | "RED";
  grade:      string;
  confidence: number;
  createdAt:  number;
}

interface PlaybitState {
  /* Connection */
  connected:   boolean;
  setConnected:(v: boolean) => void;

  /* Pair / TF */
  activePair:  string;
  activeTf:    number;
  setPair:     (p: string) => void;
  setTf:       (tf: number) => void;

  /* Loading */
  isLoading:   boolean;
  setLoading:  (v: boolean) => void;

  /* Candles */
  candles:     Record<number, Candle[]>;  // granularity → candles
  addCandle:   (tf: number, c: Candle, closed?: boolean) => void;
  setHistory:  (tf: number, cs: Candle[]) => void;

  /* Signal */
  signal:      Signal | null;
  prevSignals: Signal[];
  setSignal:   (s: Signal) => void;

  /* Model */
  modelStatus: ModelStatus;
  setModelStatus: (m: ModelStatus) => void;

  /* Live price */
  livePrice:   number;
  setLivePrice:(p: number) => void;

  /* Block reason — last reason a signal was suppressed by the gate */
  blockReason: string;
  setBlockReason: (r: string) => void;

  /* Signal markers (all pairs/TFs, persisted 24h) */
  markers:        SignalMarker[];
  addMarker:      (m: SignalMarker) => void;
  pruneMarkers:   () => void;

  /* Refresh triggers */
  refreshTick:        number;
  triggerRefresh:     () => void;          // chart history refetch
  signalRefreshTick:  number;
  triggerSignalRefresh: () => void;        // refetch last signal from /api/signal
  clearSignal:        () => void;
}

export const useStore = create<PlaybitState>((set, get) => ({
  connected:  false,
  setConnected: (v) => set({ connected: v }),

  activePair: "frxEURUSD",
  activeTf:   60,
  setPair:    (p) => set({ activePair: p, candles: {}, signal: null, isLoading: true }),
  setTf:      (tf) => set({ activeTf: tf, isLoading: true }),

  isLoading:  false,
  setLoading: (v) => set({ isLoading: v }),

  candles:    {},
  addCandle:  (tf, c, closed) => set((state) => {
    const existing = [...(state.candles[tf] ?? [])];
    const idx      = existing.findIndex((x) => x.epoch === c.epoch);
    if (idx >= 0) existing[idx] = c;
    else          existing.push(c);
    if (existing.length > 5000) existing.splice(0, existing.length - 5000);
    return { candles: { ...state.candles, [tf]: existing }, livePrice: c.close };
  }),
  setHistory: (tf, cs) => set((state) => ({
    candles:   { ...state.candles, [tf]: cs },
    livePrice: cs.length > 0 ? cs[cs.length - 1].close : state.livePrice,
    isLoading: false,
  })),

  signal:     null,
  prevSignals:[],
  setSignal:  (s) => set((state) => ({
    signal:     s,
    prevSignals:[...state.prevSignals.slice(-9), s],
  })),

  modelStatus: { is_trained: false, accuracy: 0, n_candles: 0 },
  setModelStatus: (m) => set({ modelStatus: m }),

  livePrice:  0,
  setLivePrice:(p) => set({ livePrice: p }),

  blockReason: "",
  setBlockReason: (r) => set({ blockReason: r }),

  markers: loadMarkers(),
  addMarker: (m) => set((state) => {
    const exists = state.markers.some(
      (x) => x.pair === m.pair && x.tf === m.tf && x.epoch === m.epoch,
    );
    if (exists) return state;
    const next = [...state.markers, m];
    saveMarkers(next);
    return { markers: next };
  }),
  pruneMarkers: () => set((state) => {
    const now = Date.now();
    const ttl = 24 * 60 * 60 * 1000;
    const next = state.markers.filter((m) => now - m.createdAt < ttl);
    if (next.length === state.markers.length) return state;
    saveMarkers(next);
    return { markers: next };
  }),

  refreshTick:    0,
  triggerRefresh: () => set((state) => ({ refreshTick: state.refreshTick + 1 })),

  signalRefreshTick:    0,
  triggerSignalRefresh: () => set((state) => ({ signalRefreshTick: state.signalRefreshTick + 1 })),
  clearSignal: () => set({ signal: null, blockReason: "" }),
}));

const MARKER_KEY = "playbit_signal_markers_v2";

function loadMarkers(): SignalMarker[] {
  try {
    const raw = localStorage.getItem(MARKER_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SignalMarker[];
    const now = Date.now();
    const ttl = 24 * 60 * 60 * 1000;
    return parsed.filter((m) => now - m.createdAt < ttl);
  } catch { return []; }
}

function saveMarkers(list: SignalMarker[]) {
  try { localStorage.setItem(MARKER_KEY, JSON.stringify(list)); } catch { /* ignore */ }
}
