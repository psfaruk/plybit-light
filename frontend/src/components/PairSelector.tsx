import { useStore } from "../store/useStore";
import styles from "./PairSelector.module.css";

const FOREX_PAIRS = [
  "XAUUSD",   // XAU/USD (Gold)
  "EURUSD",   // EUR/USD
  "GBPUSD",   // GBP/USD
  "USDJPY",   // USD/JPY
  "AUDUSD",   // AUD/USD
  "USDCAD",   // USD/CAD
  "NZDUSD",   // NZD/USD
  "USDCHF",   // USD/CHF
];

export function formatPair(p: string): string {
  // Handle OTC pairs: EURUSD_otc → EUR/USD (OTC)
  const base = p.replace("_otc", "");
  const isOtc = p.endsWith("_otc");
  const label = base.length === 6
    ? `${base.slice(0, 3)}/${base.slice(3)}`
    : base;
  return isOtc ? `${label} (OTC)` : label;
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
