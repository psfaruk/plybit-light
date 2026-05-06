import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { useStore, Signal } from "../store/useStore";
import styles from "./SignalPanel.module.css";

// ── Constants ────────────────────────────────────────────────

const GRADE_COLOR: Record<string, string> = {
  ELITE:    "var(--gold-neon)",
  HIGH:     "var(--blue-neon)",
  MODERATE: "var(--orange-neon)",
};

const GRADE_ICON: Record<string, string> = {
  ELITE: "★", HIGH: "✓", MODERATE: "•",
};

const MODEL_LABELS: Record<string, string> = {
  xgb:      "XGBoost",
  lgbm:     "LightGBM",
  catboost: "CatBoost",
  rf:       "Random Forest",
  extra:    "ExtraTrees",
  histgb:   "HistGB",
  lstm:     "LSTM+GRU",
  cnn:      "CNN-LSTM",
  attn:     "Attention",
  tcn:      "TCN",
  nbeats:   "N-BEATS",
  rule:     "Rule Engine",
  bayes:    "Bayesian NN",
};

const BLOCK_REASON_LABEL: Record<string, string> = {
  loading_history:  "Loading history (150+ candles needed)",
  market_closed:    "Market closed · Forex",
  news_window:      "High-impact news window",
  choppy_market:    "Choppy market · ADX low",
  volatility:       "Volatility spike",
  mtf_conflict:     "Multi-timeframe conflict",
  no_consensus:     "AI models disagree",
  ppo_skip:         "RL safety gate",
  meta_label_block: "Meta-label rejected",
  below_threshold:  "Confidence below threshold",
  low_score:        "Confidence below threshold",
  circuit_break:    "Circuit breaker · loss streak",
  circuit_breaker:  "Circuit breaker · loss streak",
  window_full:      "5M window full (3 signals)",
};

// ── Small components ─────────────────────────────────────────

function ConfidenceBar({ value, color }: { value: number; color: string }) {
  return (
    <div className={styles.confBar}>
      <div className={styles.confFill} style={{ width: `${value * 100}%`, background: color }} />
      <span className={styles.confLabel}>{Math.round(value * 100)}%</span>
    </div>
  );
}

function CountdownTimer({ expiryBars, color }: { expiryBars: number; color: string }) {
  return (
    <div className={styles.timer}>
      <svg width="44" height="44" viewBox="0 0 44 44">
        <circle cx="22" cy="22" r="18" fill="none" stroke="var(--bg-elevated)" strokeWidth="3" />
        <circle
          cx="22" cy="22" r="18"
          fill="none" stroke={color} strokeWidth="3" strokeLinecap="round"
          strokeDasharray="113"
          style={{
            animation: `count-down ${expiryBars * 60}s linear forwards`,
            transform: "rotate(-90deg)", transformOrigin: "center",
          }}
        />
      </svg>
      <span className={styles.timerLabel}>{expiryBars}M</span>
    </div>
  );
}

function ModelChips({
  models, isGreen, dim = false,
}: { models: Record<string, number>; isGreen: boolean; dim?: boolean }) {
  const entries = Object.entries(models)
    .slice()
    .sort((a, b) => Math.abs(b[1] - 0.5) - Math.abs(a[1] - 0.5));

  return (
    <div className={styles.modelList}>
      {entries.map(([key, prob]) => {
        const accepts = isGreen ? prob > 0.5 : prob < 0.5;
        const dirPct  = isGreen ? Math.round(prob * 100) : Math.round((1 - prob) * 100);
        return (
          <div key={key} className={`${styles.modelChip} ${dim ? styles.modelChipDim : ""}`}>
            <span
              className={styles.modelDot}
              style={{ background: dim ? "var(--muted)" : accepts ? "var(--green-neon)" : "var(--red-neon)" }}
            />
            <span className={styles.modelName}>{MODEL_LABELS[key] ?? key.toUpperCase()}</span>
            <span
              className={styles.modelPct}
              style={{ color: dim ? "var(--muted)" : accepts ? (isGreen ? "var(--green-neon)" : "var(--red-neon)") : "var(--muted)" }}
            >
              {dirPct}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ScanningModelChips({ count }: { count: number }) {
  return (
    <div className={styles.modelList}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className={`${styles.modelChip} ${styles.modelChipScanning}`}
          style={{ animationDelay: `${i * 80}ms` }}>
          <span className={`${styles.modelDot} ${styles.modelDotPulse}`} />
          <span className={styles.modelName} style={{ color: "var(--muted)" }}>——</span>
          <span className={styles.modelPct} style={{ color: "var(--muted)" }}>···</span>
        </div>
      ))}
    </div>
  );
}

function ScoringLayers({ layers }: {
  layers: { mtf_pts: number; smc_pts: number; react_pts: number; ai_pts: number };
}) {
  const items = [
    { label: "MTF",   pts: layers.mtf_pts,   max: 45 },
    { label: "SMC",   pts: layers.smc_pts,   max: 30 },
    { label: "React", pts: layers.react_pts, max: 25 },
    { label: "AI",    pts: layers.ai_pts,    max: 30 },
  ];
  return (
    <div className={styles.layersGrid}>
      {items.map(({ label, pts, max }) => {
        const pct   = Math.max(0, Math.min(pts / max, 1));
        const color = pct >= 0.70 ? "var(--green-neon)" : pct >= 0.40 ? "var(--orange-neon)" : "var(--red-neon)";
        return (
          <div key={label} className={styles.layerRow}>
            <span className={styles.layerLabel}>{label}</span>
            <div className={styles.layerBar}>
              <div style={{ width: `${pct * 100}%`, background: color, height: "100%", borderRadius: 2, transition: "width 0.5s" }} />
            </div>
            <span className={styles.layerPts} style={{ color }}>
              {Math.round(pts)}/{max}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function SmcSection({ smc, isGreen }: { smc: Record<string, unknown>; isGreen: boolean }) {
  const chips: { label: string; aligned: boolean }[] = [];

  if (isGreen && smc.price_at_bullish_ob)   chips.push({ label: "Bull OB ✓", aligned: true });
  else if (!isGreen && smc.price_at_bearish_ob) chips.push({ label: "Bear OB ✓", aligned: true });
  else if (isGreen && smc.price_at_bearish_ob)  chips.push({ label: "Bear OB ✗", aligned: false });
  else if (!isGreen && smc.price_at_bullish_ob) chips.push({ label: "Bull OB ✗", aligned: false });

  if (isGreen && smc.price_in_bullish_fvg)   chips.push({ label: "Bull FVG ✓", aligned: true });
  else if (!isGreen && smc.price_in_bearish_fvg) chips.push({ label: "Bear FVG ✓", aligned: true });
  else if (isGreen && smc.price_in_bearish_fvg)  chips.push({ label: "Bear FVG ✗", aligned: false });
  else if (!isGreen && smc.price_in_bullish_fvg) chips.push({ label: "Bull FVG ✗", aligned: false });

  if (isGreen && smc.bullish_bos)   chips.push({ label: "Bull BOS ✓", aligned: true });
  else if (!isGreen && smc.bearish_bos) chips.push({ label: "Bear BOS ✓", aligned: true });
  else if (isGreen && smc.bearish_bos)  chips.push({ label: "Bear BOS ✗", aligned: false });
  else if (!isGreen && smc.bullish_bos) chips.push({ label: "Bull BOS ✗", aligned: false });

  if (isGreen && smc.in_ote_zone_bull)   chips.push({ label: "OTE Bull ✓", aligned: true });
  else if (!isGreen && smc.in_ote_zone_bear) chips.push({ label: "OTE Bear ✓", aligned: true });
  else if (smc.in_ote_zone) chips.push({ label: "OTE Zone ✓", aligned: true });

  if (smc.liquidity_swept)                       chips.push({ label: "Liq Swept ✓", aligned: true });
  if (isGreen && smc.sell_side_liquidity)        chips.push({ label: "Sell Liq ✓", aligned: true });
  else if (!isGreen && smc.buy_side_liquidity)   chips.push({ label: "Buy Liq ✓", aligned: true });

  const net = typeof smc.smc_net_score === "number" ? smc.smc_net_score as number : null;

  return (
    <>
      {chips.length === 0
        ? <span className={styles.smcEmpty}>No zone confluence detected</span>
        : (
          <div className={styles.smcChips}>
            {chips.map((c) => (
              <span key={c.label} className={styles.smcChip}
                style={{
                  color:      c.aligned ? "var(--cyan-neon)" : "var(--muted)",
                  background: c.aligned ? "rgba(0,245,255,0.08)" : "rgba(255,255,255,0.04)",
                }}
              >{c.label}</span>
            ))}
          </div>
        )
      }
      {net !== null && (
        <div className={styles.smcNet}>
          Net: <span style={{ color: net > 0 ? "var(--green-neon)" : "var(--red-neon)" }}>
            {net > 0 ? "+" : ""}{Math.round(net)}
          </span>
        </div>
      )}
    </>
  );
}

// ── Waiting Panel ─────────────────────────────────────────────

function WaitingPanel({
  blockReason, modelStatus, lastSignal, justExpired,
}: {
  blockReason: string;
  modelStatus: { is_trained: boolean; accuracy: number; n_candles: number };
  lastSignal: Signal | null;
  justExpired: boolean;
}) {
  const blockLabel = blockReason ? (BLOCK_REASON_LABEL[blockReason] ?? blockReason) : "";
  const modelCount = lastSignal ? Object.keys(lastSignal.ai_models ?? {}).length || 13 : 13;

  return (
    <div className={`${styles.panel} ${styles.waitingPanel}`}>
      {/* Status heading */}
      <div className={styles.waitSection}>
        <div className={styles.scanRing} />
        <div className={styles.waitHeading}>
          {justExpired ? "Please wait for next signal" : "Scanning market…"}
        </div>
        {blockLabel && <div className={styles.blockBadge}>⚠ {blockLabel}</div>}
        {!modelStatus.is_trained && (
          <div className={styles.trainingBadge}>
            Training AI: {modelStatus.n_candles} candles
          </div>
        )}
      </div>

      {/* Live model analysis animation */}
      <div className={styles.section}>
        <div className={styles.sectionLabel}>AI Models Analyzing</div>
        <ScanningModelChips count={modelCount} />
      </div>

      {/* Last signal analysis (greyed) */}
      {lastSignal && lastSignal.signal !== "SKIP" && (
        <>
          <div className={styles.section}>
            <div className={styles.sectionLabel}>Last Analysis</div>
            {lastSignal.mtf_context && (
              <div className={styles.tfRow}>
                {Object.entries((lastSignal.mtf_context.tfs ?? {}) as Record<string, { bias: string }>).map(([tf, v]) => (
                  <div key={tf} className={styles.tfBadge} style={{ color: "var(--muted)", opacity: 0.55 }}>
                    {tf.toUpperCase()} {v.bias === "bull" ? "↑" : v.bias === "bear" ? "↓" : "→"}
                  </div>
                ))}
              </div>
            )}
          </div>
          {lastSignal.ai_models && Object.keys(lastSignal.ai_models).length > 0 && (
            <div className={styles.section}>
              <ModelChips
                models={lastSignal.ai_models}
                isGreen={lastSignal.signal === "GREEN"}
                dim
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Active Signal Panel ───────────────────────────────────────

function ActiveSignalPanel({ signal }: { signal: Signal }) {
  const isGreen = signal.signal === "GREEN";
  const color   = isGreen ? "var(--green-neon)" : "var(--red-neon)";
  const glow    = isGreen ? "var(--shadow-green)" : "var(--shadow-red)";
  const anim    = isGreen ? "anim-pulse-green" : "anim-pulse-red";

  const mtf   = signal.mtf_context ?? {};
  const react = signal.candle_reaction ?? {};
  const smc   = (signal.smc_context ?? {}) as Record<string, unknown>;
  const tfs   = (mtf.tfs ?? {}) as Record<string, { bias: string; strength: number }>;

  const models      = signal.ai_models ?? {};
  const entries     = Object.entries(models);
  const total       = entries.length;
  const accepting   = entries.filter(([, p]) => isGreen ? p > 0.5 : p < 0.5);
  const acceptCount = accepting.length;
  const acceptPct   = total > 0 ? (acceptCount / total) * 100 : 0;
  const avgConf     = accepting.length > 0
    ? accepting.reduce((s, [, p]) => s + (isGreen ? p : 1 - p), 0) / accepting.length
    : 0;

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={signal.timestamp ?? Date.now()}
        className={`${styles.panel} ${anim}`}
        style={{ border: `1px solid ${color}40`, boxShadow: glow }}
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -12 }}
        transition={{ duration: 0.3 }}
      >
        {/* ── Direction ── */}
        <div className={styles.direction} style={{ color }}>
          <span className={styles.directionArrow}>{isGreen ? "▲" : "▼"}</span>
          <span className={styles.directionLabel}>{signal.signal}</span>
          <span className={styles.directionSub}>{isGreen ? "CALL/BUY" : "PUT/SELL"}</span>
        </div>

        {/* ── Grade + timer ── */}
        <div className={styles.gradeRow}>
          <span className={styles.gradeIcon}>{GRADE_ICON[signal.grade]}</span>
          <span className={styles.gradeLabel} style={{ color: GRADE_COLOR[signal.grade] }}>
            {signal.grade}
          </span>
          <CountdownTimer expiryBars={signal.expiry_bars} color={color} />
        </div>
        <ConfidenceBar value={signal.confidence} color={color} />

        <div className={styles.tradeInfo}>
          <span>Trade: <b>Time Candle {signal.expiry_bars}M</b></span>
          <span className={styles.delay}>Max delay: {signal.max_delay_sec}s</span>
        </div>

        {/* ── AI Confirmation ── */}
        {total > 0 && (
          <div className={styles.section}>
            <div className={styles.sectionLabel}>AI Confirmation</div>
            <div className={styles.consensusRow}>
              <div className={styles.consensusBig} style={{ color }}>
                {acceptCount}/{total}
              </div>
              <div className={styles.consensusMeta}>
                <div className={styles.consensusPct} style={{ color }}>
                  {Math.round(acceptPct)}% models accepted
                </div>
                <div className={styles.consensusAvg}>
                  Avg confidence: {Math.round(avgConf * 100)}%
                </div>
              </div>
            </div>
            <ModelChips models={models} isGreen={isGreen} />
          </div>
        )}

        {/* ── Scoring Layers ── */}
        {signal.layers && (
          <div className={styles.section}>
            <div className={styles.sectionLabel}>Scoring Layers</div>
            <ScoringLayers layers={signal.layers} />
          </div>
        )}

        {/* ── Market Structure (MTF) ── */}
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Market Structure · MTF</div>
          <div className={styles.tfRow}>
            {Object.entries(tfs).map(([tf, v]) => (
              <div key={tf} className={styles.tfBadge}
                style={{ color: v.bias === "bull" ? "var(--green-neon)" : v.bias === "bear" ? "var(--red-neon)" : "var(--muted)" }}>
                {tf.toUpperCase()} {v.bias === "bull" ? "↑" : v.bias === "bear" ? "↓" : "→"}
              </div>
            ))}
          </div>
          {mtf.in_killzone && (
            <div className={styles.kzBadge}>{String(mtf.killzone).toUpperCase()} Killzone Active</div>
          )}
          {mtf.agreement !== undefined && (
            <div className={styles.mtfAgreement}>
              Agreement: {Math.round(Number(mtf.agreement) * 100)}%
            </div>
          )}
        </div>

        {/* ── SMC / ICT Context ── */}
        <div className={styles.section}>
          <div className={styles.sectionLabel}>SMC · ICT Context</div>
          <SmcSection smc={smc} isGreen={isGreen} />
        </div>

        {/* ── Candle Reaction ── */}
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Candle Reaction</div>
          <div className={styles.reactionRow}>
            <div>
              <span className={styles.reactionLabel}>Bull</span>
              <div className={styles.reactionBar}>
                <div style={{ width: `${react.bull_score ?? 0}%`, background: "var(--green-neon)" }} />
              </div>
              <span className={styles.reactionVal}>{Math.round(react.bull_score ?? 0)}</span>
            </div>
            <div>
              <span className={styles.reactionLabel}>Bear</span>
              <div className={styles.reactionBar}>
                <div style={{ width: `${react.bear_score ?? 0}%`, background: "var(--red-neon)" }} />
              </div>
              <span className={styles.reactionVal}>{Math.round(react.bear_score ?? 0)}</span>
            </div>
          </div>
          <div className={styles.reactionFlags}>
            {isGreen && react.pin_bull  ? <span className={styles.flag}>PIN BULL</span>   : null}
            {!isGreen && react.pin_bear ? <span className={styles.flag}>PIN BEAR</span>   : null}
            {isGreen && react.bull_engulf  ? <span className={styles.flag}>BULL ENGULF</span> : null}
            {!isGreen && react.bear_engulf ? <span className={styles.flag}>BEAR ENGULF</span> : null}
            {/* legacy fallback */}
            {!react.pin_bull && !react.pin_bear && react.pin_bar    ? <span className={styles.flag}>PIN</span>    : null}
            {!react.bull_engulf && !react.bear_engulf && react.engulfing ? <span className={styles.flag}>ENGULF</span> : null}
          </div>
        </div>

        {/* ── Patterns ── */}
        {(signal.patterns?.length ?? 0) > 0 && (
          <div className={styles.section}>
            <div className={styles.sectionLabel}>Patterns</div>
            <div className={styles.patternList}>
              {signal.patterns.slice(0, 5).map((p) => (
                <span key={p} className={styles.patternBadge}>{p.replace(/_/g, " ")}</span>
              ))}
            </div>
          </div>
        )}

        {/* ── Bayesian ── */}
        {signal.bayes_uncertainty && (
          <div className={styles.bayes}>
            Bayesian: {Math.round(signal.bayes_uncertainty.bayes_mean * 100)}%
            <span className={styles.bayesStd}>±{Math.round(signal.bayes_uncertainty.bayes_std * 100)}%</span>
          </div>
        )}
      </motion.div>
    </AnimatePresence>
  );
}

// ── Main export ───────────────────────────────────────────────

export function SignalPanel() {
  const { signal, prevSignals, modelStatus, blockReason } = useStore();
  const [expired, setExpired] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);

    if (!signal || signal.signal === "SKIP") {
      setExpired(false);
      return;
    }

    // candle_close_time = when the ANALYSIS candle closed = trade ENTRY time.
    // The trade itself is on the next 1M candle → add 60s for trade expiry.
    // Fallback: use signal.timestamp + 70s if candle_close_time is absent.
    const closeTime = signal.candle_close_time;
    const ts        = (signal as unknown as Record<string, unknown>).timestamp as number | undefined;
    const expiryMs  = closeTime
      ? (closeTime + 60) * 1000          // trade candle close
      : ts
        ? ts * 1000 + 70_000             // 70s from issue time
        : Date.now() + 60_000;           // 60s from now as last resort

    const msUntil = expiryMs - Date.now();
    if (msUntil <= 0) {
      setExpired(true);
    } else {
      setExpired(false);
      timerRef.current = setTimeout(() => setExpired(true), msUntil);
    }
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [signal]);

  // Reset expired when new signal arrives
  useEffect(() => {
    if (signal && signal.signal !== "SKIP") setExpired(false);
  }, [signal?.timestamp]);

  const lastSignal = prevSignals.length > 0 ? prevSignals[prevSignals.length - 1] : null;
  const isActive   = signal && signal.signal !== "SKIP" && !expired;

  if (!isActive) {
    return (
      <WaitingPanel
        blockReason={blockReason}
        modelStatus={modelStatus}
        lastSignal={lastSignal}
        justExpired={expired}
      />
    );
  }

  return <ActiveSignalPanel signal={signal} />;
}
