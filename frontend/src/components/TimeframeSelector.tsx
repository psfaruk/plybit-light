import { useState } from "react";
import { useStore } from "../store/useStore";
import styles from "./TimeframeSelector.module.css";

const PRIMARY_TFS:   [number, string][] = [[60,"1M"],[300,"5M"],[900,"15M"]];
const SECONDARY_TFS: [number, string][] = [[120,"2M"],[3600,"1H"],[14400,"4H"]];

export function TimeframeSelector() {
  const { activeTf, setTf, triggerRefresh } = useStore();
  const [spinning, setSpinning] = useState(false);

  const onRefresh = () => {
    triggerRefresh();
    setSpinning(true);
    setTimeout(() => setSpinning(false), 600);
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
      <button
        type="button"
        className={`${styles.refresh} ${spinning ? styles.spinning : ""}`}
        onClick={onRefresh}
        title="Refresh chart history"
        aria-label="Refresh"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="23 4 23 10 17 10" />
          <polyline points="1 20 1 14 7 14" />
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
        </svg>
      </button>
    </div>
  );
}
