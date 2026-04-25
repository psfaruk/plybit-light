import { useStore } from "../store/useStore";
import styles from "./WindowPanel.module.css";

export function WindowPanel() {
  const { signal } = useStore();
  const plan = signal?.window_plan ?? [];

  if (!plan.length) return null;

  return (
    <div className={styles.panel}>
      <div className={styles.header}>⏱ 5M Window</div>
      {plan.map((s, i) => {
        const dt  = new Date(Number(s.epoch) * 1000);
        const min = String(dt.getMinutes()).padStart(2, "0");
        const sec = String(dt.getSeconds()).padStart(2, "0");
        const isGreen = s.direction === "GREEN";
        return (
          <div key={i} className={styles.row}>
            <span className={styles.time}>:{min}:{sec}</span>
            <span
              className={styles.dot}
              style={{ background: isGreen ? "var(--green-neon)" : "var(--red-neon)" }}
            />
            <span
              className={styles.dir}
              style={{ color: isGreen ? "var(--green-neon)" : "var(--red-neon)" }}
            >
              {s.direction}
            </span>
            <span className={styles.conf}>{Math.round(s.confidence * 100)}%</span>
            <span className={styles.grade}>{s.grade}</span>
            {i === 0 && <span className={styles.nowBadge}>NOW</span>}
          </div>
        );
      })}
    </div>
  );
}
