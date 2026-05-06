import { useStore } from "../store/useStore";
import styles from "./ModelGrid.module.css";

const TABULAR: Record<string, string> = {
  xgb:      "XGBoost",
  lgbm:     "LightGBM",
  catboost: "CatBoost",
  rf:       "Rand Forest",
  extra:    "ExtraTrees",
  histgb:   "HistGB",
};

const DEEP: Record<string, string> = {
  lstm:  "LSTM+GRU",
  cnn:   "CNN-LSTM",
  attn:  "Attention",
  tcn:   "TCN",
  nbeats:"N-BEATS",
};

const ADVANCED: Record<string, string> = {
  rule:     "Rule Engine",
  bayes:    "Bayesian NN",
  stacking: "Stacking",
};

const CATEGORIES = [
  { label: "Tabular Models", keys: TABULAR },
  { label: "Deep Learning",  keys: DEEP },
  { label: "Advanced",       keys: ADVANCED },
];

function ModelRow({
  name, prob, isGreen,
}: { name: string; prob: number; isGreen: boolean }) {
  const dirPct = isGreen ? Math.round(prob * 100) : Math.round((1 - prob) * 100);
  const agrees = isGreen ? prob > 0.5 : prob < 0.5;

  return (
    <div
      className={styles.row}
      data-agrees={agrees ? "true" : "false"}
      data-dir={isGreen ? "green" : "red"}
      style={{ "--bar-pct": `${dirPct}%` } as React.CSSProperties}
    >
      <span className={styles.dot} />
      <span className={styles.name}>{name}</span>
      <div className={styles.bar}>
        <div className={styles.fill} />
      </div>
      <span className={styles.pct}>{dirPct}%</span>
    </div>
  );
}

export function ModelGrid() {
  const { signal } = useStore();
  const models  = signal?.ai_models ?? {};
  const isGreen = signal?.signal === "GREEN";

  if (Object.keys(models).length === 0) {
    return (
      <div className={styles.grid}>
        <div className={styles.header}>AI MODELS</div>
        <div className={styles.empty}>Models training…</div>
      </div>
    );
  }

  return (
    <div className={styles.grid}>
      <div className={styles.header}>AI MODELS · {Object.keys(models).length} ACTIVE</div>
      {CATEGORIES.map(({ label, keys }) => {
        const entries = Object.entries(keys).filter(([k]) => models[k] !== undefined);
        if (entries.length === 0) return null;
        return (
          <div key={label} className={styles.category}>
            <div className={styles.catLabel}>{label}</div>
            {entries.map(([k, name]) => (
              <ModelRow key={k} name={name} prob={models[k]} isGreen={isGreen} />
            ))}
          </div>
        );
      })}
      {(() => {
        const known = new Set(CATEGORIES.flatMap(c => Object.keys(c.keys)));
        const extras = Object.entries(models).filter(([k]) => !known.has(k));
        if (extras.length === 0) return null;
        return (
          <div className={styles.category}>
            <div className={styles.catLabel}>Other</div>
            {extras.map(([k, prob]) => (
              <ModelRow key={k} name={k.toUpperCase()} prob={prob} isGreen={isGreen} />
            ))}
          </div>
        );
      })()}
    </div>
  );
}
