import { useEffect, useRef } from "react";
import { createChart, IChartApi, ISeriesApi, ColorType, CrosshairMode, SeriesMarker, Time } from "lightweight-charts";
import { useStore } from "../store/useStore";
import styles from "./CandleChart.module.css";

function isForexMarketClosed(pair: string): boolean {
  if (pair.startsWith("BTC") || pair.startsWith("ETH") || pair.endsWith("USDT")) return false;
  const now  = new Date();
  const dow  = now.getUTCDay();
  const h    = now.getUTCHours();
  if (dow === 5 && h >= 21) return true;
  if (dow === 6) return true;
  if (dow === 0 && h < 21) return true;
  return false;
}

export function CandleChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema5Ref  = useRef<ISeriesApi<"Line"> | null>(null);
  const ema20Ref = useRef<ISeriesApi<"Line"> | null>(null);

  const lastFullKeyRef = useRef<string>("");

  const { candles, activeTf, activePair, signal, markers, addMarker, pruneMarkers } = useStore();
  const isLoading = (candles[activeTf] ?? []).length === 0;

  // Init chart
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#080d14" },
        textColor:  "#7a8fa6",
        fontFamily: "'Inter', system-ui, sans-serif",
        fontSize:   11,
      },
      grid: {
        vertLines:   { color: "#0d1520" },
        horzLines:   { color: "#0d1520" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#1a2a40" },
      timeScale: {
        borderColor:    "#1a2a40",
        timeVisible:    true,
        secondsVisible: activeTf === 60,
      },
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor:   "#00ff88",
      downColor: "#ff2d6b",
      borderUpColor:   "#00ff88",
      borderDownColor: "#ff2d6b",
      wickUpColor:   "#00cc66",
      wickDownColor: "#cc1144",
    });

    const ema5 = chart.addLineSeries({
      color:       "#00b4ff",
      lineWidth:   1,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const ema20 = chart.addLineSeries({
      color:       "#b347ff",
      lineWidth:   1,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    chartRef.current        = chart;
    candleSeriesRef.current = candleSeries;
    ema5Ref.current         = ema5;
    ema20Ref.current        = ema20;

    const observer = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.resize(containerRef.current.clientWidth, containerRef.current.clientHeight);
      }
    });
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, []);

  // Apply markers — filtered to current pair + tf, deduped, expired removed
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    const filtered = markers.filter((m) => m.pair === activePair && m.tf === activeTf);
    const sorted = filtered.slice().sort((a, b) => a.epoch - b.epoch);
    const seriesMarkers: SeriesMarker<Time>[] = sorted.map((m) => ({
      time:     m.epoch as unknown as Time,
      position: m.direction === "GREEN" ? "belowBar" : "aboveBar",
      color:    m.direction === "GREEN" ? "#00ff88" : "#ff2d6b",
      shape:    m.direction === "GREEN" ? "arrowUp" : "arrowDown",
      text:     `${m.grade} ${Math.round(m.confidence * 100)}%`,
    }));
    try { series.setMarkers(seriesMarkers); } catch { /* ignore */ }
  }, [markers, activePair, activeTf]);

  // Update candles — incremental
  useEffect(() => {
    const raw = candles[activeTf] ?? [];
    if (!candleSeriesRef.current || raw.length === 0) return;

    const byEpoch = new Map<number, typeof raw[number]>();
    for (const c of raw) byEpoch.set(c.epoch, c);
    const cs = Array.from(byEpoch.values()).sort((a, b) => a.epoch - b.epoch);

    const last     = cs[cs.length - 1];
    const fullKey  = `${activePair}:${activeTf}:${cs.length}`;
    const isNewSet = fullKey !== lastFullKeyRef.current;

    if (isNewSet) {
      const data = cs.map((c) => ({
        time:  c.epoch as unknown as Time,
        open:  c.open,
        high:  c.high,
        low:   c.low,
        close: c.close,
      }));
      try { candleSeriesRef.current.setData(data); }
      catch (e) { console.warn("Chart setData failed:", e); return; }
      lastFullKeyRef.current = fullKey;

      if (cs.length >= 20) {
        const closes = cs.map((c) => c.close);
        const ema5d  = calcEMA(closes, 5);
        const ema20d = calcEMA(closes, 20);
        ema5Ref.current?.setData(
          cs.map((c, i) => ({
            time:  c.epoch as unknown as Time,
            value: ema5d[i] ?? 0,
          })).filter((d) => d.value !== 0)
        );
        ema20Ref.current?.setData(
          cs.map((c, i) => ({
            time:  c.epoch as unknown as Time,
            value: ema20d[i] ?? 0,
          })).filter((d) => d.value !== 0)
        );
      }
    } else {
      try {
        candleSeriesRef.current.update({
          time:  last.epoch as unknown as Time,
          open:  last.open,
          high:  last.high,
          low:   last.low,
          close: last.close,
        });
      } catch {/* ignore */}
    }

  }, [candles, activeTf, activePair]);

  // Auto-scroll on pair/tf change
  useEffect(() => {
    chartRef.current?.timeScale().scrollToRealTime();
  }, [activePair, activeTf]);

  // When a signal arrives, persist a marker (also handled in useWebSocket
  // for other pairs; this is the active-pair fallback)
  useEffect(() => {
    if (!signal || signal.signal === "SKIP") return;
    const cs = candles[activeTf] ?? [];
    if (!cs.length) return;
    const last = cs[cs.length - 1];
    addMarker({
      pair:       activePair,
      tf:         activeTf,
      epoch:      last.epoch,
      direction:  signal.signal,
      grade:      signal.grade,
      confidence: signal.confidence,
      createdAt:  Date.now(),
    });
  }, [signal]);

  // Periodic cleanup of expired markers
  useEffect(() => {
    const id = setInterval(pruneMarkers, 60 * 1000);
    return () => clearInterval(id);
  }, [pruneMarkers]);

  const marketClosed = isForexMarketClosed(activePair);

  return (
    <div className={styles.wrapper}>
      <div ref={containerRef} className={styles.canvas} />

      {isLoading && (
        <div className={styles.loadingOverlay}>
          <div className={styles.spinner} />
          <span className={styles.loadingText}>Loading chart…</span>
        </div>
      )}

      {marketClosed && !isLoading && (
        <div className={styles.marketClosedBanner}>
          FOREX CLOSED · Opens Sunday 21:00 UTC
        </div>
      )}
    </div>
  );
}

function calcEMA(values: number[], period: number): number[] {
  const result: number[] = new Array(values.length).fill(0);
  const k = 2 / (period + 1);
  let ema = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  result[period - 1] = ema;
  for (let i = period; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
    result[i] = ema;
  }
  return result;
}
