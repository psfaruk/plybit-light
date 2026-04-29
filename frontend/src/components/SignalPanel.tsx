import { motion, AnimatePresence } from "framer-motion";
import { useStore } from "../store/useStore";
import styles from "./SignalPanel.module.css";

const GRADE_COLOR: Record<string, string> = {
  ELITE:    "var(--gold-neon)",
  HIGH:     "var(--green-neon)",
  MODERATE: "var(--orange-neon)",
};

const GRADE_ICON: Record<string, string> = {
  ELITE: "★", HIGH: "✓", MODERATE: "•",
};

const MODEL_LABELS: Record<string, string> = {
  xgb:     "XGBoost",
  lgbm:    "LightGBM",
  catboost:"CatBoost",
  rf:      "Random Forest",
  extra:   "ExtraTrees",
  histgb:  "HistGB",
  lstm:    "LSTM+GRU",
  cnn:     "CNN-LSTM",
  attn:    "Attention",
  tcn:     "TCN",
  nbeats:  "N-BEATS",
  rule:    "Rule Engine",
  bayes:   "Bayesian NN",
};

function ConfidenceBar({ value, color }: { value: number; color: string }) {
  return (
    <div className={styles.confBar}>
      <div
        className={styles.confFill}
        style={{ width: `${value * 100}%`, background: color }}
      />
      <span className={styles.confLabel}>{Math.round(value * 100)}%</span>
    </div>
  );
}

function CountdownTimer({ expiryBars }: { expiryBars: number }) {
  return (
    <div className={styles.timer}>
      <svg width="44" height="44" viewBox="0 0 44 44">
        <circle cx="22" cy="22" r="18" fill="none" stroke="var(--bg-elevated)" strokeWidth="3" />
        <circle
          cx="22" cy="22" r="18"
          fill="none"
          stroke="var(--green-neon)"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray="113"
          style={{ animation: `count-down ${expiryBars * 60}s linear forwards`, transform: "rotate(-90deg)", transformOrigin: "center" }}
        />
      </svg>
      <span className={styles.timerLabel}>{expiryBars}M</span>
    </div>
  );
}

const BLOCK_REASON_LABEL: Record<string, string> = {
  market_closed:     "Market closed (forex)",
  news_window:       "High-impact news window",
  choppy_market:     "Choppy market (ADX low)",
  mtf_conflict:      "Multi-timeframe conflict",
  no_consensus:      "AI models disagree",
  ppo_skip:          "RL safety gate",
  meta_label_block:  "Meta-label rejected",
  below_threshold:   "Confidence below threshold",
  circuit_breaker:   "Circuit breaker (loss streak)",
};

export function SignalPanel() {
  const { signal, modelStatus, blockReason } = useStore();

  if (!signal || signal.signal === "SKIP") {
    const blockLabel = blockReason ? (BLOCK_REASON_LABEL[blockReason] ?? blockReason) : "";
    return (
      <div className={`${styles.panel} ${styles.waiting}`}>
        <div className={styles.waitingDot} />
        <div className={styles.waitingText}>
          {blockLabel ? `Waiting: ${blockLabel}` : "Scanning market…"}
        </div>
        {!modelStatus.is_trained && (
          <div className={styles.trainingBadge}>
            Training AI: {modelStatus.n_candles} candles
          </div>
        )}
      </div>
    );
  }

  const isGreen = signal.signal === "GREEN";
  const color   = isGreen ? "var(--green-neon)" : "var(--red-neon)";
  const glow    = isGreen ? "var(--shadow-green)" : "var(--shadow-red)";
  const anim    = isGreen ? "anim-pulse-green" : "anim-pulse-red";

  const mtf   = signal.mtf_context ?? {};
  const react = signal.candle_reaction ?? {};
  const tfs   = (mtf.tfs ?? {}) as Record<string, { bias: string; strength: number }>;

  // Model consensus calculations
  const models = signal.ai_models ?? {};
  const modelEntries = Object.entries(models);
  const totalModels = modelEntries.length;
  const acceptingModels = modelEntries.filter(([, p]) =>
    isGreen ? p > 0.5 : p < 0.5,
  );
  const acceptCount = acceptingModels.length;
  const acceptPct = totalModels > 0 ? (acceptCount / totalModels) * 100 : 0;
  const avgAcceptConf =
    acceptingModels.length > 0
      ? acceptingModels.reduce((sum, [, p]) => sum + (isGreen ? p : 1 - p), 0) /
        acceptingModels.length
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
        {/* Signal direction */}
        <div className={styles.direction} style={{ color }}>
          <span className={styles.directionArrow}>{isGreen ? "▲" : "▼"}</span>
          <span className={styles.directionLabel}>{signal.signal}</span>
          <span className={styles.directionSub}>{isGreen ? "CALL/BUY" : "PUT/SELL"}</span>
        </div>

        {/* Grade + confidence */}
        <div className={styles.gradeRow}>
          <span className={styles.gradeIcon}>{GRADE_ICON[signal.grade]}</span>
          <span className={styles.gradeLabel} style={{ color: GRADE_COLOR[signal.grade] }}>
            {signal.grade}
          </span>
          <CountdownTimer expiryBars={signal.expiry_bars} />
        </div>
        <ConfidenceBar value={signal.confidence} color={color} />

        <div className={styles.tradeInfo}>
          <span>Trade: <b>Time Candle {signal.expiry_bars}M</b></span>
          <span className={styles.delay}>Max delay: {signal.max_delay_sec}s</span>
        </div>

        {/* AI Confirmation summary */}
        {totalModels > 0 && (
          <div className={styles.section}>
            <div className={styles.sectionLabel}>AI Confirmation</div>
            <div className={styles.consensusRow}>
              <div className={styles.consensusBig} style={{ color }}>
                {acceptCount}/{totalModels}
              </div>
              <div className={styles.consensusMeta}>
                <div className={styles.consensusPct} style={{ color }}>
                  {Math.round(acceptPct)}% models accepted
                </div>
                <div className={styles.consensusAvg}>
                  Avg confidence: {Math.round(avgAcceptConf * 100)}%
                </div>
              </div>
            </div>
            <div className={styles.modelList}>
              {modelEntries
                .slice()
                .sort((a, b) => Math.abs(b[1] - 0.5) - Math.abs(a[1] - 0.5))
                .map(([key, prob]) => {
                  const accepts = isGreen ? prob > 0.5 : prob < 0.5;
                  const dirPct = isGreen
                    ? Math.round(prob * 100)
                    : Math.round((1 - prob) * 100);
                  return (
                    <div key={key} className={styles.modelChip}>
                      <span
                        className={styles.modelDot}
                        style={{ background: accepts ? "var(--green-neon)" : "var(--red-neon)" }}
                      />
                      <span className={styles.modelName}>
                        {MODEL_LABELS[key] ?? key.toUpperCase()}
                      </span>
                      <span
                        className={styles.modelPct}
                        style={{ color: accepts ? color : "var(--muted)" }}
                      >
                        {dirPct}%
                      </span>
                    </div>
                  );
                })}
            </div>
          </div>
        )}

        {/* MTF */}
        <div className={styles.section}>
          <div className={styles.sectionLabel}>MTF Alignment</div>
          <div className={styles.tfRow}>
            {Object.entries(tfs).map(([tf, v]) => (
              <div
                key={tf}
                className={styles.tfBadge}
                style={{ color: v.bias === "bull" ? "var(--green-neon)" : v.bias === "bear" ? "var(--red-neon)" : "var(--muted)" }}
              >
                {tf.toUpperCase()} {v.bias === "bull" ? "↑" : v.bias === "bear" ? "↓" : "→"}
              </div>
            ))}
          </div>
          {mtf.in_killzone && (
            <div className={styles.kzBadge}>{String(mtf.killzone).toUpperCase()} Killzone Active</div>
          )}
        </div>

        {/* Candle Reaction */}
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
            {react.pin_bar ? <span className={styles.flag}>PIN</span> : null}
            {react.engulfing ? <span className={styles.flag}>ENGULF</span> : null}
          </div>
        </div>

        {/* Patterns */}
        {signal.patterns?.length > 0 && (
          <div className={styles.section}>
            <div className={styles.sectionLabel}>Patterns</div>
            <div className={styles.patternList}>
              {signal.patterns.slice(0, 4).map((p) => (
                <span key={p} className={styles.patternBadge}>{p.replace(/_/g, " ")}</span>
              ))}
            </div>
          </div>
        )}

        {/* Uncertainty (Bayesian) */}
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
