import { useStore } from "../store/useStore";
import { formatPair } from "./PairSelector";
import styles from "./TickerBar.module.css";

function pricePrecision(pair: string): number {
  if (pair.includes("XAU")) return 2;                       // Gold
  if (pair.startsWith("OTC_") || pair.includes("OIL")) return 2;
  if (pair.endsWith("USDT")) return 2;                       // BTC etc.
  if (pair.includes("JPY")) return 3;                        // FX with JPY
  return 5;                                                  // FX majors
}

export function TickerBar() {
  const { livePrice, activePair } = useStore();
  const pair     = formatPair(activePair);
  const priceStr = livePrice > 0 ? livePrice.toFixed(pricePrecision(activePair)) : "—";

  return (
    <div className={styles.bar}>
      <div className={styles.ticker} aria-label="live ticker">
        <span className={styles.item}>
          <span className={styles.sym}>{pair}</span>
          <span className={styles.price}>{priceStr}</span>
        </span>
      </div>
    </div>
  );
}
