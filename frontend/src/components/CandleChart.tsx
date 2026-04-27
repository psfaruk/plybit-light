import { useEffect, useRef } from "react";
import { createChart, IChartApi, ISeriesApi, ColorType, CrosshairMode } from "lightweight-charts";
import { useStore } from "../store/useStore";
import styles from "./CandleChart.module.css";

function isForexMarketClosed(pair: string): boolean {
  if (pair.startsWith("BTC") || pair.startsWith("ETH") || pair.endsWith("USDT")) return false;
  const now  = new Date();
  const dow  = now.getUTCDay();    // 0=Sun, 6=Sat
  const h    = now.getUTCHours();
  if (dow === 5 && h >= 21) return true;  // Friday ≥ 21:00 UTC
  if (dow === 6) return true;              // Saturday
  if (dow === 0 && h < 21) return true;   // Sunday < 21:00 UTC
  return false;
}

export function CandleChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema5Ref  = useRef<ISeriesApi<"Line"> | null>(null);
  const ema20Ref = useRef<ISeriesApi<"Line"> | null>(null);

  const { candles, activeTf, activePair, signal } = useStore();
  const isLoading = (candles[activeTf] ?? []).length === 0;

  // Init chart
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#080d14" },
        textColor:  "#4a607a",
        fontFamily: "'JetBrains Mono', monospace",
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

    chartRef.current       = chart;
    candleSeriesRef.current = candleSeries;
    ema5Ref.current        = ema5;
    ema20Ref.current       = ema20;

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

  // Update candles — sort + dedupe by epoch to avoid lightweight-charts crashes
  useEffect(() => {
    const raw = candles[activeTf] ?? [];
    if (!candleSeriesRef.current || raw.length === 0) return;

    // Dedupe (keep latest per epoch) then sort ascending
    const byEpoch = new Map<number, typeof raw[number]>();
    for (const c of raw) byEpoch.set(c.epoch, c);
    const cs = Array.from(byEpoch.values()).sort((a, b) => a.epoch - b.epoch);

    const data = cs.map((c) => ({
      time:  c.epoch as unknown as import("lightweight-charts").UTCTimestamp,
      open:  c.open,
      high:  c.high,
      low:   c.low,
      close: c.close,
    }));

    try {
      candleSeriesRef.current.setData(data);
    } catch (e) {
      console.warn("Chart setData failed:", e);
      return;
    }

    if (cs.length >= 20) {
      const closes = cs.map((c) => c.close);
      const ema5d  = calcEMA(closes, 5);
      const ema20d = calcEMA(closes, 20);

      ema5Ref.current?.setData(
        cs.map((c, i) => ({
          time:  c.epoch as unknown as import("lightweight-charts").UTCTimestamp,
          value: ema5d[i] ?? 0,
        })).filter((d) => d.value !== 0)
      );
      ema20Ref.current?.setData(
        cs.map((c, i) => ({
          time:  c.epoch as unknown as import("lightweight-charts").UTCTimestamp,
          value: ema20d[i] ?? 0,
        })).filter((d) => d.value !== 0)
      );
    }

    chartRef.current?.timeScale().scrollToRealTime();
  }, [candles, activeTf]);

  // Signal arrow markers
  useEffect(() => {
    if (!signal || !candleSeriesRef.current || signal.signal === "SKIP") return;
    const cs = candles[activeTf] ?? [];
    if (!cs.length) return;
    const last = cs[cs.length - 1];
    candleSeriesRef.current.setMarkers([{
      time:     last.epoch as unknown as import("lightweight-charts").UTCTimestamp,
      position: signal.signal === "GREEN" ? "belowBar" : "aboveBar",
      color:    signal.signal === "GREEN" ? "#00ff88" : "#ff2d6b",
      shape:    signal.signal === "GREEN" ? "arrowUp"  : "arrowDown",
      text:     `${signal.grade} ${Math.round(signal.confidence * 100)}%`,
    }]);
  }, [signal]);

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
