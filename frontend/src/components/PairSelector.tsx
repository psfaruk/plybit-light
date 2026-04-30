import { useStore } from "../store/useStore";
import styles from "./PairSelector.module.css";

const FOREX_PAIRS = [
  "frxXAUUSD",   // XAU/USD (Gold)
  "frxEURUSD",   // EUR/USD
  "frxGBPUSD",   // GBP/USD
  "frxUSDJPY",   // USD/JPY
  "frxAUDUSD",   // AUD/USD
  "frxUSDCAD",   // USD/CAD
  "frxNZDUSD",   // NZD/USD
  "frxUSDCHF",   // USD/CHF
];

export function formatPair(p: string): string {
  // frxEURUSD → EUR/USD, frxXAUUSD → XAU/USD
  if (p.startsWith("frx")) {
    const clean = p.replace("frx", "");
    if (clean.length === 6) return `${clean.slice(0, 3)}/${clean.slice(3)}`;
    return clean;
  }
  return p;
}

export function PairSelector() {
  const { activePair, setPair } = useStore();

  return (
    <div className={styles.wrapper}>
      <select
        className={styles.select}
        value={activePair}
        onChange={(e) => setPair(e.target.value)}
        aria-label="Trading pair"
        title="Trading pair"
      >
        <optgroup label="Forex / Gold">
          {FOREX_PAIRS.map((p) => (
            <option key={p} value={p}>{formatPair(p)}</option>
          ))}
        </optgroup>
      </select>
    </div>
  );
}
