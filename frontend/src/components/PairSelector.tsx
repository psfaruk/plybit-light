import { useStore } from "../store/useStore";
import styles from "./PairSelector.module.css";

const FOREX_PAIRS = [
  // Majors
  "frxEURUSD","frxGBPUSD","frxUSDJPY","frxUSDCHF",
  "frxUSDCAD","frxAUDUSD","frxNZDUSD",
  // EUR crosses
  "frxEURGBP","frxEURJPY","frxEURCAD","frxEURAUD",
  "frxEURCHF","frxEURNZD",
  // GBP crosses
  "frxGBPJPY","frxGBPCAD","frxGBPAUD",
  "frxGBPCHF","frxGBPNZD",
  // Other crosses
  "frxAUDJPY","frxAUDCAD","frxAUDNZD",
  "frxNZDJPY",
  // Synthetics
  "R_10","R_25","R_50","R_75","R_100",
  // Gold
  "frxXAUUSD",
];

const CRYPTO_PAIRS = [
  "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT",
  "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","MATICUSDT",
];

function formatPair(p: string): string {
  if (p.startsWith("R_")) return p;
  const clean = p.replace("frx", "");
  if (clean.length === 6) return `${clean.slice(0, 3)}/${clean.slice(3)}`;
  return clean;
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
