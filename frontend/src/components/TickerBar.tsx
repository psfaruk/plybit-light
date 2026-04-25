import { useStore } from "../store/useStore";
import styles from "./TickerBar.module.css";

export function TickerBar() {
  const { livePrice, activePair } = useStore();
  const pair = activePair.replace("frx","").replace("USDT","/USDT");
  const priceStr = livePrice > 0 ? livePrice.toFixed(activePair.includes("XAU") ? 2 : 5) : "—";

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
