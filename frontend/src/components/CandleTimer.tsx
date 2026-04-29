import { useEffect, useState } from "react";
import { useStore } from "../store/useStore";
import styles from "./CandleTimer.module.css";

function formatRemaining(ms: number): string {
  if (ms < 0) ms = 0;
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function CandleTimer() {
  const { activeTf, candles } = useStore();
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, []);

  const cs = candles[activeTf] ?? [];
  if (cs.length === 0) return null;

  const last = cs[cs.length - 1];
  const closeAt   = (last.epoch + activeTf) * 1000;
  const remaining = closeAt - now;
  const total     = activeTf * 1000;
  const elapsed   = total - remaining;
  const pct       = Math.max(0, Math.min(1, elapsed / total));
  const urgent    = remaining < 5000;

  return (
    <div className={`${styles.timer} ${urgent ? styles.urgent : ""}`}>
      <div className={styles.label}>NEXT</div>
      <div className={styles.value}>{formatRemaining(remaining)}</div>
      <div className={styles.bar}>
        <div className={styles.fill} style={{ width: `${pct * 100}%` }} />
      </div>
    </div>
  );
}
