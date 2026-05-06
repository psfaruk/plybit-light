import { useState } from "react";
import { useStore } from "../store/useStore";
import { CandleTimer } from "./CandleTimer";
import styles from "./TimeframeSelector.module.css";

const PRIMARY_TFS:   [number, string][] = [[60,"1M"],[300,"5M"],[900,"15M"]];
const SECONDARY_TFS: [number, string][] = [[120,"2M"],[3600,"1H"],[14400,"4H"]];

export function TimeframeSelector() {
  const { activeTf, setTf, triggerRefresh, triggerSignalRefresh, clearSignal } = useStore();
  const [spin, setSpin] = useState<"chart" | "signal" | "full" | null>(null);

  const refreshChart = () => {
    triggerRefresh();
    setSpin("chart");
    setTimeout(() => setSpin(null), 600);
  };
  const refreshSignal = () => {
    clearSignal();
    triggerSignalRefresh();
    setSpin("signal");
    setTimeout(() => setSpin(null), 600);
  };
  const refreshFull = () => {
    setSpin("full");
    setTimeout(() => window.location.reload(), 250);
  };

  return (
    <div className={styles.row}>
      {PRIMARY_TFS.map(([tf, label]) => (
        <button
          type="button"
          key={tf}
          className={`${styles.btn} ${activeTf === tf ? styles.active : ""}`}
          onClick={() => setTf(tf)}
        >
          {label}
        </button>
      ))}
      <span className={styles.divider} />
      {SECONDARY_TFS.map(([tf, label]) => (
        <button
          type="button"
          key={tf}
          className={`${styles.btn} ${styles.secondary} ${activeTf === tf ? styles.active : ""}`}
          onClick={() => setTf(tf)}
        >
          {label}
        </button>
      ))}

      <div className={styles.spacer} />

      <CandleTimer />

      <div className={styles.refreshGroup}>
        <button
          type="button"
          className={`${styles.refresh} ${spin === "chart" ? styles.spinning : ""}`}
          onClick={refreshChart}
          title="Refresh chart history"
          aria-label="Refresh chart"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="23 4 23 10 17 10" />
            <polyline points="1 20 1 14 7 14" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
          <span className={styles.refreshLabel}>Chart</span>
        </button>
        <button
          type="button"
          className={`${styles.refresh} ${spin === "signal" ? styles.spinning : ""}`}
          onClick={refreshSignal}
          title="Refresh signal panel"
          aria-label="Refresh signal"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2 L3 14 H12 L11 22 L21 10 H12 Z" />
          </svg>
          <span className={styles.refreshLabel}>Signal</span>
        </button>
        <button
          type="button"
          className={`${styles.refresh} ${styles.refreshFull} ${spin === "full" ? styles.spinning : ""}`}
          onClick={refreshFull}
          title="Reload entire app"
          aria-label="Reload app"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="16 8 12 12 16 16" />
            <polyline points="8 8 12 12 8 16" />
          </svg>
          <span className={styles.refreshLabel}>App</span>
        </button>
      </div>
    </div>
  );
}
