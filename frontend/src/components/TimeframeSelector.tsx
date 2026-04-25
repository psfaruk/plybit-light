import { useStore } from "../store/useStore";
import styles from "./TimeframeSelector.module.css";

const PRIMARY_TFS:   [number, string][] = [[60,"1M"],[300,"5M"],[900,"15M"]];
const SECONDARY_TFS: [number, string][] = [[120,"2M"],[3600,"1H"],[14400,"4H"]];

export function TimeframeSelector() {
  const { activeTf, setTf } = useStore();

  return (
    <div className={styles.row}>
      {PRIMARY_TFS.map(([tf, label]) => (
        <button
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
          key={tf}
          className={`${styles.btn} ${styles.secondary} ${activeTf === tf ? styles.active : ""}`}
          onClick={() => setTf(tf)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
