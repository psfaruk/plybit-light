import "./design/tokens.css";
import "./design/animations.css";
import "./design/glass.css";
import styles from "./App.module.css";

import { useStore } from "./store/useStore";
import { useWebSocket } from "./hooks/useWebSocket";
import { CandleChart }              from "./components/CandleChart";
import { SignalPanel }              from "./components/SignalPanel";
import { ModelGrid }                from "./components/ModelGrid";
import { PairSelector, formatPair } from "./components/PairSelector";
import { TimeframeSelector }        from "./components/TimeframeSelector";
import { WindowPanel }              from "./components/WindowPanel";
import { SmcPanel }                 from "./components/SmcPanel";
import { ReactionPanel }            from "./components/ReactionPanel";
import { EnginePanel }              from "./components/EnginePanel";
import { TickerBar }                from "./components/TickerBar";

export function App() {
  const { connected, activePair, modelStatus, liveEngine } = useStore();
  useWebSocket();

  const pairDisplay = formatPair(activePair);

  // Connection-quality badge: green LIVE if last tick < 5s, yellow LAGGY 5-15s,
  // red RECONNECTING > 15s. `tick_age` arrives via engine_update every ~500ms.
  const tickAge = liveEngine[activePair]?.tick_age ?? 999;
  let dataQuality: "live" | "laggy" | "reconnecting";
  let dataLabel:   string;
  if (tickAge < 5)        { dataQuality = "live";         dataLabel = "LIVE"; }
  else if (tickAge < 15)  { dataQuality = "laggy";        dataLabel = `LAGGY ${Math.round(tickAge)}s`; }
  else                    { dataQuality = "reconnecting"; dataLabel = "RECONNECTING…"; }

  return (
    <div className={styles.root}>
      {/* ── Header ── */}
      <header className={styles.header}>
        <div className={styles.logo}>
          <span className={styles.logoIcon}>⚡</span>
          <span className={styles.logoText}>Plybit Ai Signals</span>
        </div>

        <div className={styles.headerCenter}>
          <PairSelector />
          <span className={styles.pairLabel}>{pairDisplay}</span>
        </div>

        <div className={styles.headerRight}>
          <div className={`${styles.liveDot} ${connected ? styles[dataQuality] : styles.offline}`} />
          <span className={styles.liveLabel} data-quality={dataQuality}>
            {connected ? dataLabel : "CONNECTING…"}
          </span>
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
          <div className={styles.smcWrap}>
            <SmcPanel />
          </div>
          <div className={styles.engineWrap}>
            <EnginePanel />
          </div>
          <div className={styles.reactionWrap}>
            <ReactionPanel />
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
