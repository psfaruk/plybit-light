import { useStore } from "../store/useStore";
import styles from "./ModelGrid.module.css";

const MODEL_LABELS: Record<string, string> = {
  xgb:     "XGBoost",
  lgbm:    "LightGBM",
  catboost:"CatBoost",
  rf:      "Rand Forest",
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

export function ModelGrid() {
  const { signal } = useStore();
  const models = signal?.ai_models ?? {};

  return (
    <div className={styles.grid}>
      <div className={styles.header}>🤖 16 AI Models</div>
      {Object.entries(models).map(([key, prob]) => {
        const pct = Math.round(prob * 100);
        const isBull = prob > 0.5;
        return (
          <div key={key} className={styles.row}>
            <span className={styles.name}>{MODEL_LABELS[key] ?? key.toUpperCase()}</span>
            <div className={styles.bar}>
              <div
                className={styles.fill}
                style={{
                  width: `${pct}%`,
                  background: isBull ? "var(--green-neon)" : "var(--red-neon)",
                  opacity:    0.7,
                }}
              />
            </div>
            <span
              className={styles.pct}
              style={{ color: isBull ? "var(--green-neon)" : "var(--red-neon)" }}
            >
              {pct}%
            </span>
          </div>
        );
      })}
      {Object.keys(models).length === 0 && (
        <div className={styles.empty}>Models training…</div>
      )}
    </div>
  );
}
