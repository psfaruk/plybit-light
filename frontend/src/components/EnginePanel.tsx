import { useStore } from "../store/useStore";
import styles from "./EnginePanel.module.css";

/**
 * Engine Panel — shows the live output of:
 *   • Rejection Zone Engine
 *   • Volume Profile Engine
 *   • Quotex Sentiment (broker)
 *   • Synthetic Sentiment (price-action derived)
 *   • Order Flow
 * Helps the user understand WHY a signal was boosted or penalised.
 */
const AGENT_LABELS: Record<string, string> = {
  smc:            "SMC",
  ict:            "ICT",
  price_action:   "Price Action",
  mtf:            "Multi-TF",
  ml:             "ML Models",
  volume:         "Volume",
  sentiment:      "Sentiment",
  rejection_zone: "Reject Zone",
};

const DIR_COLOR: Record<string, string> = {
  GREEN:   "#00ff88",
  RED:     "#ff2d6b",
  NEUTRAL: "#7a8fa6",
};

export function EnginePanel() {
  const { signal, prevSignals, liveEngine, activePair } = useStore();
  const live = liveEngine[activePair];

  // Prefer LIVE engine data (updates every ~500ms); fall back to signal-bound data
  const rz    = live?.rejection_zone      ?? signal?.rejection_zone;
  const vp    = live?.volume_profile      ?? signal?.volume_profile;
  const synth = live?.synthetic_sentiment ?? signal?.synthetic_sentiment;
  const of    = live?.order_flow          ?? signal?.order_flow;
  const sent  = signal?.sentiment;
  const liveSent = live?.sentiment;

  const dir = signal?.signal ?? "GREEN";

  // Use current signal's agent data, fall back to last known signal
  const lastKnown = prevSignals.length > 0 ? prevSignals[prevSignals.length - 1] : null;
  const multi = signal?.multi_agent ?? lastKnown?.multi_agent ?? null;

  // If we have no live data AND no signal yet, hide panel
  if (!live && (!signal || signal.signal === "SKIP") && !lastKnown) return null;

  return (
    <div className={styles.panel}>
      {/* ── REJECTION ZONE ─────────────────────────────────── */}
      <div className={styles.engineBlock}>
        <div className={styles.engineHead}>
          <span className={styles.engineName}>REJECTION ZONE</span>
          {rz && rz.rz_grade && (
            <span className={styles.gradePill} data-grade={rz.rz_grade}>
              {rz.rz_grade}
            </span>
          )}
        </div>
        {rz && rz.rz_touches > 0 ? (
          <>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Side</span>
              <span className={styles.value} data-side={rz.rz_side}>
                {rz.rz_side}
              </span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Touches</span>
              <span className={styles.value}>{rz.rz_touches}</span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Score</span>
              <div className={styles.bar}>
                <div
                  className={styles.barFill}
                  data-grade={rz.rz_grade}
                  style={{ width: `${Math.min(rz.rz_score, 100)}%` }}
                />
              </div>
              <span className={styles.value}>{Math.round(rz.rz_score)}</span>
            </div>
            {rz.rz_growing && (
              <div className={styles.alertChip}>📈 Pressure growing</div>
            )}
            {rz.rz_signal !== "NONE" && (
              <div className={styles.rowFlex}>
                <span className={styles.label}>Says</span>
                <span className={styles.value} data-side={rz.rz_signal}>
                  {rz.rz_signal}{rz.rz_signal === dir ? " ✓ (confirms)" : " ✗ (opposes)"}
                </span>
              </div>
            )}
          </>
        ) : (
          <div className={styles.empty}>No active zone near price</div>
        )}
      </div>

      {/* ── VOLUME PROFILE ───────────────────────────────── */}
      <div className={styles.engineBlock}>
        <div className={styles.engineHead}>
          <span className={styles.engineName}>VOLUME PROFILE</span>
          {vp && (
            <span className={styles.zonePill} data-zone={vp.vp_zone}>
              {(vp.vp_zone || "").replace(/_/g, " ")}
            </span>
          )}
        </div>
        {vp ? (
          <>
            <div className={styles.rowFlex}>
              <span className={styles.label}>POC</span>
              <span className={styles.value}>{vp.vp_poc.toFixed(5)}</span>
              {vp.vp_at_poc && <span className={styles.alertChip}>AT POC</span>}
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>VAH</span>
              <span className={styles.value}>{vp.vp_vah.toFixed(5)}</span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>VAL</span>
              <span className={styles.value}>{vp.vp_val.toFixed(5)}</span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Resistance</span>
              <span className={styles.value}>{vp.vp_hvn_resistance.toFixed(5)}</span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Support</span>
              <span className={styles.value}>{vp.vp_hvn_support.toFixed(5)}</span>
            </div>
          </>
        ) : (
          <div className={styles.empty}>—</div>
        )}
      </div>

      {/* ── QUOTEX SENTIMENT ───────────────────────────── */}
      <div className={styles.engineBlock}>
        <div className={styles.engineHead}>
          <span className={styles.engineName}>QUOTEX SENTIMENT</span>
          <span className={styles.statusPill} data-status={liveSent?.fresh ? (sent?.sent_align || "ACTIVE") : "STALE"}>
            {liveSent?.fresh ? (sent?.sent_align || "ACTIVE") : "STALE"}
          </span>
        </div>
        {liveSent?.fresh ? (
          <>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Buyers</span>
              <div className={styles.bar}>
                <div
                  className={styles.barFill}
                  data-grade="A"
                  style={{ width: `${(liveSent.buyer_ratio ?? 0.5) * 100}%` }}
                />
              </div>
              <span className={styles.value}>
                {Math.round((liveSent.buyer_ratio ?? 0.5) * 100)}%
              </span>
            </div>
            {liveSent.extreme && liveSent.extreme !== "NEUTRAL" && (
              <div className={styles.alertChip}>{liveSent.extreme}</div>
            )}
            {sent && (
              <div className={styles.rowFlex}>
                <span className={styles.label}>Effect</span>
                <span className={styles.value} data-mult={sent.sent_mult >= 1 ? "boost" : "penalty"}>
                  ×{sent.sent_mult.toFixed(2)}
                </span>
              </div>
            )}
          </>
        ) : (
          <div className={styles.empty}>
            Broker data unavailable (account limitation)
          </div>
        )}
      </div>

      {/* ── SYNTHETIC SENTIMENT ───────────────────────── */}
      <div className={styles.engineBlock}>
        <div className={styles.engineHead}>
          <span className={styles.engineName}>SYNTHETIC SENTIMENT</span>
          {synth && (
            <span className={styles.zonePill} data-zone={synth.synth_signal_bias}>
              {synth.synth_signal_bias}
            </span>
          )}
        </div>
        {synth ? (
          <>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Net Sentiment</span>
              <div className={styles.netBar}>
                <div className={styles.netCenter} />
                <div
                  className={styles.netFill}
                  data-dir={synth.synth_net_sentiment >= 0 ? "green" : "red"}
                  style={{
                    width:  `${Math.min(Math.abs(synth.synth_net_sentiment) * 100, 50)}%`,
                    [synth.synth_net_sentiment >= 0 ? "left" : "right"]: "50%",
                  } as React.CSSProperties}
                />
              </div>
              <span className={styles.value}>
                {(synth.synth_net_sentiment * 100).toFixed(0)}
              </span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Buyer Wick</span>
              <div className={styles.bar}>
                <div
                  className={styles.barFill}
                  data-grade="A"
                  style={{ width: `${synth.synth_buyer_wick * 100}%` }}
                />
              </div>
              <span className={styles.value}>{Math.round(synth.synth_buyer_wick * 100)}</span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Seller Wick</span>
              <div className={styles.bar}>
                <div
                  className={styles.barFill}
                  data-grade="C"
                  style={{ width: `${synth.synth_seller_wick * 100}%` }}
                />
              </div>
              <span className={styles.value}>{Math.round(synth.synth_seller_wick * 100)}</span>
            </div>
            <div className={styles.rowFlex}>
              <span className={styles.label}>Exhaustion</span>
              <div className={styles.bar}>
                <div
                  className={styles.barFill}
                  data-grade={synth.synth_exhaustion > 0.5 ? "S" : "B"}
                  style={{ width: `${synth.synth_exhaustion * 100}%` }}
                />
              </div>
              <span className={styles.value}>{Math.round(synth.synth_exhaustion * 100)}</span>
            </div>
            {synth.synth_divergence !== "NONE" && (
              <div className={styles.alertChip}>
                {synth.synth_divergence} DIVERGENCE
              </div>
            )}
            {typeof synth.synth_mult === "number" && (
              <div className={styles.rowFlex}>
                <span className={styles.label}>Effect</span>
                <span className={styles.value} data-mult={synth.synth_mult >= 1 ? "boost" : "penalty"}>
                  ×{synth.synth_mult.toFixed(2)} ({synth.synth_align})
                </span>
              </div>
            )}
          </>
        ) : (
          <div className={styles.empty}>—</div>
        )}
      </div>

      {/* ── ORDER FLOW ──────────────────────────────────── */}
      {of && (of.os_total > 0) && (
        <div className={styles.engineBlock}>
          <div className={styles.engineHead}>
            <span className={styles.engineName}>ORDER FLOW</span>
          </div>
          <div className={styles.rowFlex}>
            <span className={styles.label}>Imbalance</span>
            <div className={styles.netBar}>
              <div className={styles.netCenter} />
              <div
                className={styles.netFill}
                data-dir={of.os_imbalance >= 0 ? "green" : "red"}
                style={{
                  width:  `${Math.min(Math.abs(of.os_imbalance) * 100, 50)}%`,
                  [of.os_imbalance >= 0 ? "left" : "right"]: "50%",
                } as React.CSSProperties}
              />
            </div>
            <span className={styles.value}>
              {(of.os_imbalance * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      )}

      {/* ── MULTI-AGENT CONSENSUS — always visible ─────────── */}
      <div className={styles.engineBlock}>
        <div className={styles.engineHead}>
          <span className={styles.engineName}>AGENT CONSENSUS</span>
          {multi ? (
            <span className={styles.regimePill} data-regime={multi.regime}>
              {(multi.regime ?? "unknown").toUpperCase()}
            </span>
          ) : (
            <span className={styles.regimePill} data-regime="quiet">SCANNING</span>
          )}
        </div>

        {multi && multi.agent_report && Object.keys(multi.agent_report).length > 0 ? (
          <>
            {/* Agreement bar */}
            <div className={styles.rowFlex}>
              <span className={styles.label}>Agreement</span>
              <div className={styles.bar}>
                <div
                  className={styles.barFill}
                  data-grade={multi.agreement >= 0.75 ? "S" : multi.agreement >= 0.60 ? "A" : "C"}
                  style={{ width: `${multi.agreement * 100}%` }}
                />
              </div>
              <span className={styles.value}>{Math.round(multi.agreement * 100)}%</span>
            </div>

            {/* Conflict */}
            {multi.conflict_score > 0.2 && (
              <div className={styles.rowFlex}>
                <span className={styles.label}>Conflict</span>
                <div className={styles.bar}>
                  <div
                    className={styles.barFill}
                    data-grade={multi.conflict_score > 0.5 ? "C" : "B"}
                    style={{ width: `${multi.conflict_score * 100}%` }}
                  />
                </div>
                <span className={styles.value}>{Math.round(multi.conflict_score * 100)}%</span>
              </div>
            )}

            {/* Direction changed warning */}
            {multi.dir_changed && (
              <div className={styles.alertChipOverride}>
                AGENT OVERRIDE — direction adjusted
              </div>
            )}

            {/* Per-agent rows */}
            <div className={styles.agentGrid}>
              {Object.entries(multi.agent_report).map(([key, ag]) => (
                <div key={key} className={styles.agentRow}>
                  <span className={styles.agentName}>{AGENT_LABELS[key] ?? key}</span>
                  <span className={styles.agentDir} data-dir={ag.direction}>
                    {ag.direction}
                  </span>
                  <div className={styles.agentBar}>
                    <div
                      className={styles.agentBarFill}
                      data-dir={ag.direction}
                      style={{ "--fill-pct": `${ag.confidence * 100}%` } as React.CSSProperties}
                    />
                  </div>
                  <span className={styles.agentConf}>{Math.round(ag.confidence * 100)}%</span>
                  <span className={styles.agentWeight}
                    title={`Win rate: ${Math.round(ag.win_rate * 100)}% (${ag.trades} trades)`}>
                    w={ag.weight.toFixed(1)}
                  </span>
                </div>
              ))}
            </div>

            {/* Critique messages */}
            {multi.critique.length > 0 && (
              <div className={styles.critiqueList}>
                {multi.critique.map((c, i) => (
                  <div key={i} className={styles.critiqueItem}>{c}</div>
                ))}
              </div>
            )}
          </>
        ) : (
          /* Scanning placeholders — shown before first signal */
          <div className={styles.agentGrid}>
            {Object.keys(AGENT_LABELS).map((key) => (
              <div key={key} className={styles.agentRow}>
                <span className={styles.agentName}>{AGENT_LABELS[key]}</span>
                <span className={styles.agentDir} data-dir="NEUTRAL">···</span>
                <div className={styles.agentBar}>
                  <div className={`${styles.agentBarFill} ${styles.agentBarEmpty}`} />
                </div>
                <span className={`${styles.agentConf} ${styles.agentConfMuted}`}>—</span>
                <span className={styles.agentWeight}>—</span>
              </div>
            ))}
            <div className={styles.scanningHint}>
              Waiting for 1M candle close…
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
