import { useStore } from "../store/useStore";
import styles from "./ReactionPanel.module.css";

export function ReactionPanel() {
  const { signal } = useStore();
  if (!signal || signal.signal === "SKIP") return null;

  const isGreen = signal.signal === "GREEN";
  const react   = signal.candle_reaction ?? { bull_score: 0, bear_score: 0, net_score: 0 };
  const wyckoff = (signal.wyckoff ?? {}) as Record<string, unknown>;

  const bullPct = Math.min(react.bull_score ?? 0, 100);
  const bearPct = Math.min(react.bear_score ?? 0, 100);
  const net     = react.net_score ?? 0;

  const priceActionFlags: { label: string; active: boolean }[] = [
    { label: "Pin Bull",    active: !!(isGreen  && react.pin_bull) },
    { label: "Pin Bear",    active: !!(!isGreen && react.pin_bear) },
    { label: "Bull Engulf", active: !!(isGreen  && react.bull_engulf) },
    { label: "Bear Engulf", active: !!(!isGreen && react.bear_engulf) },
    { label: "Pin Bar",     active: !!(!react.pin_bull && !react.pin_bear && react.pin_bar) },
    { label: "Engulfing",   active: !!(!react.bull_engulf && !react.bear_engulf && react.engulfing) },
  ].filter((f) => f.active || !["Pin Bar", "Engulfing"].includes(f.label));

  const wyckoffFlags = [
    { key: "wyckoff_spring",    label: "Spring" },
    { key: "wyckoff_upthrust",  label: "Upthrust" },
    { key: "wyckoff_sos",       label: "SoS" },
    { key: "wyckoff_sow",       label: "SoW" },
    { key: "wyckoff_accumulation", label: "Accum" },
    { key: "wyckoff_distribution", label: "Distrib" },
    { key: "wyckoff_markup",    label: "Markup" },
    { key: "wyckoff_markdown",  label: "Markdown" },
  ].filter((f) => wyckoff[f.key]);

  return (
    <div className={styles.panel}>
      {/* ── Bull / Bear scores ── */}
      <div className={styles.sectionHeader}>Candle Reaction</div>
      <div className={styles.scoreRow}>
        <div className={styles.scoreItem}>
          <span className={styles.scoreLabel}>Bull</span>
          <div className={styles.scoreBar}>
            <div
              className={styles.scoreFill}
              data-side="bull"
              style={{ "--score-pct": `${bullPct}%` } as React.CSSProperties}
            />
          </div>
          <span className={styles.scoreVal} data-side="bull">{Math.round(bullPct)}</span>
        </div>
        <div className={styles.scoreItem}>
          <span className={styles.scoreLabel}>Bear</span>
          <div className={styles.scoreBar}>
            <div
              className={styles.scoreFill}
              data-side="bear"
              style={{ "--score-pct": `${bearPct}%` } as React.CSSProperties}
            />
          </div>
          <span className={styles.scoreVal} data-side="bear">{Math.round(bearPct)}</span>
        </div>
        <div className={styles.netBadge} data-positive={net > 0 ? "true" : "false"}>
          Net {net > 0 ? "+" : ""}{Math.round(net)}
        </div>
      </div>

      {/* ── Price Action patterns ── */}
      {priceActionFlags.some((f) => f.active) && (
        <>
          <div className={styles.sectionHeader}>Price Action</div>
          <div className={styles.chipRow}>
            {priceActionFlags.filter((f) => f.active).map((f) => (
              <span key={f.label} className={styles.chip} data-side={isGreen ? "bull" : "bear"}>
                {f.label}
              </span>
            ))}
          </div>
        </>
      )}

      {/* ── Wyckoff ── */}
      {wyckoffFlags.length > 0 && (
        <>
          <div className={styles.sectionHeader}>Wyckoff</div>
          <div className={styles.chipRow}>
            {wyckoffFlags.map((f) => (
              <span key={f.key} className={styles.chip} data-side="neutral">
                {f.label}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
