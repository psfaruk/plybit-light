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
    bull_score:   number;
    bear_score:   number;
    net_score:    number;
    pin_bar?:     number;
    pin_bull?:    number;
    pin_bear?:    number;
    engulfing?:   number;
    bull_engulf?: number;
    bear_engulf?: number;
    atr_score?:   number;
    close_pos?:   number;
  };
  candle_open_time?:  number;
  candle_close_time?: number;
  mtf_context: {
    direction:  string;
    agreement:  number;
    killzone:   string;
    in_killzone:boolean;
    tfs:        Record<string, { bias: string; strength: number }>;
  };
  smc_context:        Record<string, unknown>;
  institutional?:     Record<string, unknown>;
  wyckoff?:           Record<string, unknown>;
  bayes_uncertainty?: { bayes_mean: number; bayes_std: number };
  layers?: { mtf_pts: number; smc_pts: number; react_pts: number; ai_pts: number };
  window_plan?: Array<{ epoch: number; confidence: number; direction: string; grade: string }>;
  timestamp?: number;

  /* ── New Engines (Rejection Zone, Volume Profile, Sentiment, Synthetic) ── */
  rejection_zone?: {
    rz_score:    number;
    rz_grade:    "S" | "A" | "B" | "C" | "D";
    rz_signal:   "GREEN" | "RED" | "NONE";
    rz_touches:  number;
    rz_growing:  boolean;
    rz_side:     "BUYER" | "SELLER" | "NONE";
  };
  volume_profile?: {
    vp_poc:             number;
    vp_vah:             number;
    vp_val:             number;
    vp_zone:            "AT_POC" | "AT_RESISTANCE" | "AT_SUPPORT"
                      | "LVN_FAST_MOVE" | "IN_VALUE_AREA"
                      | "ABOVE_VALUE_AREA" | "BELOW_VALUE_AREA" | "NEUTRAL";
    vp_hvn_resistance:  number;
    vp_hvn_support:     number;
    vp_at_poc:          boolean;
  };
  sentiment?: {
    sent_mult:    number;
    sent_align:   string;
    buyer_ratio?: number;
    extreme?:     string;
    velocity?:    number;
    reason?:      string;
  };
  order_flow?: {
    os_total:     number;
    os_buy_pct:   number;
    os_imbalance: number;
  };
  synthetic_sentiment?: {
    synth_buyer_ratio:   number;
    synth_net_sentiment: number;
    synth_exhaustion:    number;
    synth_divergence:    "BULL" | "BEAR" | "NONE";
    synth_seller_wick:   number;
    synth_buyer_wick:    number;
    synth_effort_imb:    number;
    synth_signal_bias:   "GREEN" | "RED" | "NEUTRAL";
    synth_mult:          number;
    synth_align:         string;
  };

  /* ── Multi-Agent Consensus Engine ── */
  multi_agent?: {
    regime:        string;
    conflict_score: number;
    agreement:     number;
    dir_changed:   boolean;
    critique:      string[];
    green_weight:  number;
    red_weight:    number;
    agent_report:  Record<string, {
      direction:  string;
      confidence: number;
      score:      number;
      reasons:    string[];
      weight:     number;
      win_rate:   number;
      trades:     number;
      data:       Record<string, unknown>;
    }>;
  };
}

export interface ModelStatus {
  is_trained: boolean;
  accuracy:   number;
  n_candles:  number;
}

export interface EngineSnapshot {
  rejection_zone?:      Signal["rejection_zone"];
  volume_profile?:      Signal["volume_profile"];
  synthetic_sentiment?: Signal["synthetic_sentiment"];
  sentiment?: {
    buyer_ratio: number;
    extreme:     string;
    fresh:       boolean;
  };
  order_flow?:    Signal["order_flow"];
  current_price?: number;
  tick_age?:      number;   // seconds since last raw tick from Quotex
  ts?:            number;
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

  /* Live engine state (per pair) — updated ~2 Hz from WebSocket */
  liveEngine:    Record<string, EngineSnapshot>;
  setLiveEngine: (pair: string, snap: EngineSnapshot) => void;

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

  activePair: "EURUSD",
  activeTf:   60,
  setPair:    (p) => set({ activePair: p, candles: {}, signal: null, isLoading: true }),
  setTf:      (tf) => set({ activeTf: tf, isLoading: true }),

  isLoading:  false,
  setLoading: (v) => set({ isLoading: v }),

  candles:    {},
  addCandle:  (tf, c, closed) => set((state) => {
    const prev = state.candles[tf] ?? [];
    const idx  = prev.findIndex((x) => x.epoch === c.epoch);

    let next: Candle[];
    if (idx >= 0) {
      // Mutate in place — same array reference if only the last candle changed.
      // This prevents non-active-TF updates from triggering CandleChart's effect.
      const updated = [...prev];
      updated[idx]  = c;
      next = updated;
    } else {
      next = [...prev, c];
    }
    if (next.length > 5000) next = next.slice(-5000);

    return { candles: { ...state.candles, [tf]: next }, livePrice: c.close };
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

  liveEngine: {},
  setLiveEngine: (pair, snap) => set((state) => ({
    liveEngine: { ...state.liveEngine, [pair]: snap },
  })),

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
