import "./design/tokens.css";
import "./design/animations.css";
import "./design/glass.css";
import styles from "./App.module.css";

import { useStore } from "./store/useStore";
import { useWebSocket } from "./hooks/useWebSocket";
import { CandleChart }       from "./components/CandleChart";
import { SignalPanel }        from "./components/SignalPanel";
import { ModelGrid }          from "./components/ModelGrid";
import { PairSelector }       from "./components/PairSelector";
import { TimeframeSelector }  from "./components/TimeframeSelector";
import { WindowPanel }        from "./components/WindowPanel";
import { TickerBar }          from "./components/TickerBar";

export function App() {
  const { connected, activePair, modelStatus } = useStore();
  useWebSocket();

  const pairDisplay = activePair.replace("frx", "").replace("USDT", "/USDT");

  return (
    <div className={styles.root}>
      {/* ── Header ── */}
      <header className={styles.header}>
        <div className={styles.logo}>
          <span className={styles.logoIcon}>⚡</span>
          <span className={styles.logoText}>PLAYBIT AI</span>
        </div>

        <div className={styles.headerCenter}>
          <PairSelector />
          <span className={styles.pairLabel}>{pairDisplay}</span>
        </div>

        <div className={styles.headerRight}>
          <div className={`${styles.liveDot} ${connected ? styles.live : styles.offline}`} />
          <span className={styles.liveLabel}>{connected ? "LIVE" : "CONNECTING…"}</span>
          {modelStatus.is_trained && (
            <span className={styles.accuracyBadge}>
              {Math.round(modelStatus.accuracy * 100)}% acc
            </span>
          )}
        </div>
      </header>

      {/* ── Timeframe bar ── */}
      <div className={styles.tfBar}>
        <TimeframeSelector />
      </div>

      {/* ── Main layout ── */}
      <main className={styles.main}>
        {/* Left: chart */}
        <section className={styles.chartSection}>
          <CandleChart />
        </section>

        {/* Right: signal sidebar */}
        <aside className={styles.sidebar}>
          <div className={styles.signalWrap}>
            <SignalPanel />
          </div>
          <div className={styles.windowWrap}>
            <WindowPanel />
          </div>
          <div className={styles.modelWrap}>
            <ModelGrid />
          </div>
        </aside>
      </main>

      {/* ── Ticker ── */}
      <TickerBar />
    </div>
  );
}
