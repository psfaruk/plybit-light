import { useEffect, useRef } from "react";
import { createChart, IChartApi, ISeriesApi, CandlestickSeries, LineSeries, ColorType, CrosshairMode } from "lightweight-charts";
import { useStore } from "../store/useStore";

export function CandleChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema5Ref  = useRef<ISeriesApi<"Line"> | null>(null);
  const ema20Ref = useRef<ISeriesApi<"Line"> | null>(null);

  const { candles, activeTf, signal } = useStore();

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

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor:   "#00ff88",
      downColor: "#ff2d6b",
      borderUpColor:   "#00ff88",
      borderDownColor: "#ff2d6b",
      wickUpColor:   "#00cc66",
      wickDownColor: "#cc1144",
    });

    const ema5 = chart.addSeries(LineSeries, {
      color:       "#00b4ff",
      lineWidth:   1,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const ema20 = chart.addSeries(LineSeries, {
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

  // Update candles
  useEffect(() => {
    const cs = candles[activeTf] ?? [];
    if (!candleSeriesRef.current || cs.length === 0) return;

    const data = cs.map((c) => ({
      time:  c.epoch as unknown as import("lightweight-charts").UTCTimestamp,
      open:  c.open,
      high:  c.high,
      low:   c.low,
      close: c.close,
    }));

    candleSeriesRef.current.setData(data);

    // EMA lines
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

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
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
