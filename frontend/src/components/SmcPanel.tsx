import { useStore } from "../store/useStore";
import styles from "./SmcPanel.module.css";

export function SmcPanel() {
  const { signal } = useStore();
  if (!signal || signal.signal === "SKIP") return null;

  const isGreen = signal.signal === "GREEN";
  const mtf     = signal.mtf_context ?? { tfs: {}, direction: "neutral", agreement: 0, killzone: "", in_killzone: false };
  const smc     = (signal.smc_context ?? {}) as Record<string, unknown>;
  const inst    = (signal.institutional ?? {}) as Record<string, unknown>;
  const tfs     = (mtf.tfs ?? {}) as Record<string, { bias: string; strength: number }>;

  return (
    <div className={styles.panel}>
      {/* ── MTF Analysis ─────────────────── */}
      <div className={styles.sectionHeader}>MTF Analysis</div>
      <div className={styles.tfGrid}>
        {Object.entries(tfs).map(([tf, v]) => (
          <div key={tf} className={styles.tfCell} data-bias={v.bias}>
            <span className={styles.tfLabel}>{tf.toUpperCase()}</span>
            <span className={styles.tfBias}>
              {v.bias === "bull" ? "▲" : v.bias === "bear" ? "▼" : "—"}
            </span>
          </div>
        ))}
      </div>
      {mtf.agreement !== undefined && (
        <div className={styles.agreementRow}>
          <span className={styles.agreementLabel}>Agreement</span>
          <div className={styles.agreementBar}>
            <div
              className={styles.agreementFill}
              data-dir={isGreen ? "green" : "red"}
              style={{ "--agree-pct": `${Math.round(Number(mtf.agreement) * 100)}%` } as React.CSSProperties}
            />
          </div>
          <span className={styles.agreementPct}>{Math.round(Number(mtf.agreement) * 100)}%</span>
        </div>
      )}
      {mtf.in_killzone && (
        <div className={styles.kzBadge}>{String(mtf.killzone).toUpperCase()} Killzone</div>
      )}

      {/* ── SMC Concepts ─────────────────── */}
      <div className={styles.sectionHeader}>SMC Concepts</div>
      <div className={styles.chipGrid}>
        {[
          { key: isGreen ? "price_at_bullish_ob" : "price_at_bearish_ob", label: isGreen ? "Bull OB" : "Bear OB" },
          { key: isGreen ? "price_in_bullish_fvg" : "price_in_bearish_fvg", label: isGreen ? "Bull FVG" : "Bear FVG" },
          { key: isGreen ? "bullish_bos" : "bearish_bos", label: "BOS" },
          { key: isGreen ? "bullish_choch" : "bearish_choch", label: "ChoCh" },
          { key: isGreen ? "in_ote_zone_bull" : "in_ote_zone_bear", label: "OTE Zone" },
          { key: "liquidity_swept", label: "Liq Swept" },
          { key: isGreen ? "liq_sweep_bull" : "liq_sweep_bear", label: isGreen ? "SSL Swept" : "BSL Swept" },
          { key: isGreen ? "sell_side_liquidity" : "buy_side_liquidity", label: isGreen ? "Sell Liq" : "Buy Liq" },
        ].map(({ key, label }) => (
          <span key={key} className={styles.chip} data-active={smc[key] ? "true" : "false"}>
            {label}
          </span>
        ))}
      </div>

      {/* ── ICT Concepts ─────────────────── */}
      <div className={styles.sectionHeader}>ICT Concepts</div>
      <div className={styles.chipGrid}>
        {[
          { key: isGreen ? "judas_swing_bull" : "judas_swing_bear", label: "Judas Swing" },
          { key: isGreen ? "mm_bull_cycle" : "mm_bear_cycle", label: "MM Cycle" },
          { key: isGreen ? "displacement_bull" : "displacement_bear", label: "Displacement" },
          { key: isGreen ? "bullish_liquidity_sweep" : "bearish_liquidity_sweep", label: "Inst Sweep" },
        ].map(({ key, label }) => (
          <span key={key} className={styles.chip} data-active={inst[key] ? "true" : "false"}>
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
