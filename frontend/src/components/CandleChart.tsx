import { useEffect, useRef } from "react";
import { createChart, IChartApi, ISeriesApi, ColorType, CrosshairMode, SeriesMarker, Time } from "lightweight-charts";
import { useStore } from "../store/useStore";
import styles from "./CandleChart.module.css";

const ANIM_DURATION_MS = 80;

function isForexMarketClosed(pair: string): boolean {
  if (pair.startsWith("BTC") || pair.startsWith("ETH") || pair.endsWith("USDT")) return false;
  const now = new Date();
  const dow = now.getUTCDay();
  const h   = now.getUTCHours();
  if (dow === 5 && h >= 21) return true;
  if (dow === 6) return true;
  if (dow === 0 && h < 21) return true;
  return false;
}

export function CandleChart() {
  const containerRef    = useRef<HTMLDivElement>(null);
  const chartRef        = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema5Ref         = useRef<ISeriesApi<"Line"> | null>(null);
  const ema20Ref        = useRef<ISeriesApi<"Line"> | null>(null);
  const lastDatasetKeyRef = useRef<string>("");
  const prevCandleLenRef  = useRef<number>(0);

  const animationRef = useRef<{
    startTime:    number;
    startCandle:  { open: number; high: number; low: number; close: number };
    targetCandle: { open: number; high: number; low: number; close: number };
    epoch:        number;
    rafId:        number | null;
  } | null>(null);

  const { candles, activeTf, activePair, signal, markers, addMarker, pruneMarkers } = useStore();
  const isLoading = (candles[activeTf] ?? []).length === 0;

  const animateToTarget = (target: { epoch: number; open: number; high: number; low: number; close: number }) => {
    if (!candleSeriesRef.current) return;
    if (animationRef.current?.rafId != null) cancelAnimationFrame(animationRef.current.rafId);
    const current = animationRef.current?.targetCandle ?? { open: target.open, high: target.high, low: target.low, close: target.close };
    animationRef.current = {
      startTime: performance.now(),
      startCandle: { ...current },
      targetCandle: { open: target.open, high: target.high, low: target.low, close: target.close },
      epoch: target.epoch,
      rafId: null,
    };
    const animate = (now: number) => {
      const anim = animationRef.current;
      if (!anim || !candleSeriesRef.current) return;
      const t     = Math.min((now - anim.startTime) / ANIM_DURATION_MS, 1);
      const eased = 1 - (1 - t) * (1 - t);
      try {
        candleSeriesRef.current.update({
          time:  anim.epoch as unknown as Time,
          open:  anim.startCandle.open  + (anim.targetCandle.open  - anim.startCandle.open)  * eased,
          high:  anim.startCandle.high  + (anim.targetCandle.high  - anim.startCandle.high)  * eased,
          low:   anim.startCandle.low   + (anim.targetCandle.low   - anim.startCandle.low)   * eased,
          close: anim.startCandle.close + (anim.targetCandle.close - anim.startCandle.close) * eased,
        });
      } catch { /* ignore */ }
      if (t < 1) { anim.rafId = requestAnimationFrame(animate); } else { anim.rafId = null; }
    };
    animationRef.current.rafId = requestAnimationFrame(animate);
  };

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: "#080d14" }, textColor: "#7a8fa6", fontFamily: "'JetBrains Mono', 'Inter', monospace", fontSize: 11 },
      grid: { vertLines: { color: "rgba(255,255,255,0.03)" }, horzLines: { color: "rgba(255,255,255,0.03)" } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { width: 1, color: "rgba(0,180,255,0.4)", labelBackgroundColor: "#121d2e" }, horzLine: { width: 1, color: "rgba(0,180,255,0.4)", labelBackgroundColor: "#121d2e" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.05)", scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderColor: "rgba(255,255,255,0.05)", timeVisible: true, secondsVisible: activeTf === 60, rightOffset: 5, barSpacing: 8, minBarSpacing: 2 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale:  { mouseWheel: true, pinch: true },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });
    const candleSeries = chart.addCandlestickSeries({ upColor: "#00ff88", downColor: "#ff2d6b", borderUpColor: "#00ff88", borderDownColor: "#ff2d6b", wickUpColor: "#00ff8880", wickDownColor: "#ff2d6b80", priceLineVisible: false, lastValueVisible: true });
    const ema5  = chart.addLineSeries({ color: "#00b4ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema20 = chart.addLineSeries({ color: "#b347ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    chartRef.current = chart; candleSeriesRef.current = candleSeries; ema5Ref.current = ema5; ema20Ref.current = ema20;
    const observer = new ResizeObserver(() => { if (containerRef.current) chart.resize(containerRef.current.clientWidth, containerRef.current.clientHeight); });
    if (containerRef.current) observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      if (animationRef.current?.rafId != null) cancelAnimationFrame(animationRef.current.rafId);
      animationRef.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    const filtered = markers.filter((m) => m.pair === activePair && m.tf === activeTf);
    const seriesMarkers: SeriesMarker<Time>[] = filtered.slice().sort((a, b) => a.epoch - b.epoch).map((m) => ({
      time: m.epoch as unknown as Time,
      position: m.direction === "GREEN" ? "belowBar" : "aboveBar",
      color:    m.direction === "GREEN" ? "#00ff88" : "#ff2d6b",
      shape:    m.direction === "GREEN" ? "arrowUp" : "arrowDown",
      text:     `${m.grade} ${Math.round(m.confidence * 100)}%`,
    }));
    try { series.setMarkers(seriesMarkers); } catch { /* ignore */ }
  }, [markers, activePair, activeTf]);

  useEffect(() => {
    const raw = candles[activeTf] ?? [];
    if (!candleSeriesRef.current || raw.length === 0) return;
    const byEpoch = new Map<number, typeof raw[number]>();
    for (const c of raw) byEpoch.set(c.epoch, c);
    const cs = Array.from(byEpoch.values()).sort((a, b) => a.epoch - b.epoch);
    const last = cs[cs.length - 1];
    const datasetKey  = `${activePair}:${activeTf}:${cs[0].epoch}`;
    const isNewDataset = datasetKey !== lastDatasetKeyRef.current;
    const isNewCandle  = cs.length > prevCandleLenRef.current;
    prevCandleLenRef.current = cs.length;
    const updateEMAs = () => {
      if (cs.length < 20) return;
      const closes = cs.map((c) => c.close);
      const ema5d  = calcEMA(closes, 5);
      const ema20d = calcEMA(closes, 20);
      ema5Ref.current?.setData(cs.map((c, i) => ({ time: c.epoch as unknown as Time, value: ema5d[i] ?? 0 })).filter((d) => d.value !== 0));
      ema20Ref.current?.setData(cs.map((c, i) => ({ time: c.epoch as unknown as Time, value: ema20d[i] ?? 0 })).filter((d) => d.value !== 0));
    };
    if (isNewDataset) {
      if (animationRef.current?.rafId != null) cancelAnimationFrame(animationRef.current.rafId);
      animationRef.current = null;
      try { candleSeriesRef.current.setData(cs.map((c) => ({ time: c.epoch as unknown as Time, open: c.open, high: c.high, low: c.low, close: c.close }))); }
      catch (e) { console.warn("Chart setData failed:", e); return; }
      lastDatasetKeyRef.current = datasetKey;
      updateEMAs();
    } else {
      animateToTarget({ epoch: last.epoch, open: last.open, high: last.high, low: last.low, close: last.close });
      if (isNewCandle) updateEMAs();
    }
  }, [candles, activeTf, activePair]);

  useEffect(() => { chartRef.current?.timeScale().scrollToRealTime(); }, [activePair, activeTf]);

  useEffect(() => {
    if (!signal || signal.signal === "SKIP") return;
    const openTime = (signal as unknown as Record<string, unknown>).candle_open_time as number | undefined;
    if (!openTime) return;
    const epoch = activeTf === 60 ? openTime + 60 : Math.floor(openTime / activeTf) * activeTf;
    addMarker({ pair: activePair, tf: activeTf, epoch, direction: signal.signal, grade: signal.grade, confidence: signal.confidence, createdAt: Date.now() });
  }, [signal]);

  useEffect(() => { const id = setInterval(pruneMarkers, 60_000); return () => clearInterval(id); }, [pruneMarkers]);

  return (
    <div className={styles.wrapper}>
      <div ref={containerRef} className={styles.canvas} />
      {isLoading && (<div className={styles.loadingOverlay}><div className={styles.spinner} /><span className={styles.loadingText}>Loading chart…</span></div>)}
      {isForexMarketClosed(activePair) && !isLoading && (<div className={styles.marketClosedBanner}>FOREX CLOSED · Opens Sunday 21:00 UTC</div>)}
    </div>
  );
}

function calcEMA(values: number[], period: number): number[] {
  const result: number[] = new Array(values.length).fill(0);
  const k = 2 / (period + 1);
  let ema = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  result[period - 1] = ema;
  for (let i = period; i < values.length; i++) { ema = values[i] * k + ema * (1 - k); result[i] = ema; }
  return result;
}
