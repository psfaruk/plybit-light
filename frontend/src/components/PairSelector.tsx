import { useStore } from "../store/useStore";
import styles from "./PairSelector.module.css";

const FOREX_PAIRS = [
  "frxEURUSD","frxGBPUSD","frxUSDJPY","frxUSDCHF",
  "frxUSDCAD","frxAUDUSD","frxNZDUSD",
  "frxEURGBP","frxEURJPY","frxGBPJPY","frxAUDJPY",
  "frxEURCAD","frxGBPCAD","frxEURAUD","frxGBPAUD",
  "R_10","R_25","R_50","R_75","R_100","frxXAUUSD",
];

const CRYPTO_PAIRS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"];

function formatPair(p: string): string {
  return p.replace("frx","").replace("USD","").replace("USDT","/USDT") || p;
}

export function PairSelector() {
  const { activePair, setPair } = useStore();

  return (
    <div className={styles.wrapper}>
      <select
        className={styles.select}
        value={activePair}
        onChange={(e) => setPair(e.target.value)}
      >
        <optgroup label="Forex">
          {FOREX_PAIRS.map((p) => (
            <option key={p} value={p}>{formatPair(p)}</option>
          ))}
        </optgroup>
        <optgroup label="Crypto">
          {CRYPTO_PAIRS.map((p) => (
            <option key={p} value={p}>{p.replace("USDT","").replace("USD","")}/USDT</option>
          ))}
        </optgroup>
      </select>
    </div>
  );
}
