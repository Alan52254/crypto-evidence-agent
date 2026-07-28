"use client";

import { useState } from "react";
import { X, TrendingUp, BarChart2, Activity, Maximize2, Minimize2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface CandlestickPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  rsi?: number;
}

export interface FloatingCandlestickModalProps {
  isOpen: boolean;
  onClose: () => void;
  asset: string;
  data?: CandlestickPoint[];
}

export function FloatingCandlestickModal({
  isOpen,
  onClose,
  asset = "BTC",
  data = [],
}: FloatingCandlestickModalProps) {
  const [hoveredCandle, setHoveredCandle] = useState<CandlestickPoint | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);

  if (!isOpen) return null;

  // Generate sample points if data is empty for demonstration
  const points: CandlestickPoint[] =
    data.length > 0
      ? data
      : Array.from({ length: 24 }).map((_, i) => {
          const basePrice = asset === "BTC" ? 67000 : asset === "ETH" ? 3400 : 150;
          const randomDelta = (Math.random() - 0.48) * (basePrice * 0.015);
          const close = basePrice + randomDelta;
          const open = close + (Math.random() - 0.5) * (basePrice * 0.01);
          const high = Math.max(open, close) + Math.random() * (basePrice * 0.008);
          const low = Math.min(open, close) - Math.random() * (basePrice * 0.008);
          return {
            date: `07-${20 + Math.floor(i / 6)} ${String((i % 6) * 4).padStart(2, "0")}:00`,
            open,
            high,
            low,
            close,
            volume: Math.floor(Math.random() * 5000 + 1000),
            rsi: 45 + Math.random() * 25,
          };
        });

  const latestCandle = points[points.length - 1];
  const activeCandle = hoveredCandle || latestCandle;
  const isUp = activeCandle ? activeCandle.close >= activeCandle.open : true;

  const minPrice = Math.min(...points.map((p) => p.low)) * 0.995;
  const maxPrice = Math.max(...points.map((p) => p.high)) * 1.005;
  const priceRange = maxPrice - minPrice || 1;

  const width = isExpanded ? 900 : 640;
  const height = isExpanded ? 460 : 320;
  const candleW = Math.max(3, (width - 100) / points.length * 0.7);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200">
      <div
        className={`relative flex flex-col rounded-3xl border border-outline-variant bg-surface-container-lowest p-6 shadow-2xl transition-all duration-300 ${
          isExpanded ? "w-full max-w-5xl" : "w-full max-w-2xl"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-outline-variant/60 pb-4">
          <div className="flex items-center gap-3">
            <span className="rounded-full bg-primary/10 px-3.5 py-1 text-label-md font-bold text-primary">
              {asset} / USDT
            </span>
            <div className="flex items-center gap-2 font-mono text-body-sm text-secondary">
              <Activity className="h-4 w-4 text-emerald-500" />
              <span>時框: 4H 浮動 K 線圖</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              onClick={() => setIsExpanded(!isExpanded)}
              className="h-8 w-8 min-h-0 p-0 text-secondary hover:text-primary"
            >
              {isExpanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </Button>
            <Button
              variant="ghost"
              onClick={onClose}
              className="h-8 w-8 min-h-0 p-0 text-secondary hover:text-primary"
            >
              <X className="h-4.5 w-4.5" />
            </Button>
          </div>
        </div>

        {/* OHLCV Active Reader */}
        {activeCandle && (
          <div className="my-3 flex flex-wrap items-center justify-between rounded-xl bg-surface-container-low px-4 py-2.5 font-mono text-body-xs">
            <div className="flex items-center gap-4">
              <span>時間: <strong>{activeCandle.date}</strong></span>
              <span>開: <strong className={isUp ? "text-emerald-600" : "text-rose-600"}>${activeCandle.open.toFixed(2)}</strong></span>
              <span>高: <strong className="text-emerald-600">${activeCandle.high.toFixed(2)}</strong></span>
              <span>低: <strong className="text-rose-600">${activeCandle.low.toFixed(2)}</strong></span>
              <span>收: <strong className={isUp ? "text-emerald-600" : "text-rose-600"}>${activeCandle.close.toFixed(2)}</strong></span>
            </div>
            {activeCandle.rsi !== undefined && (
              <span className="text-secondary">RSI(14): <strong className="text-primary">{activeCandle.rsi.toFixed(1)}</strong></span>
            )}
          </div>
        )}

        {/* Interactive SVG K-line rendering */}
        <div className="relative flex justify-center overflow-x-auto py-2">
          <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto max-h-[400px]">
            {/* Grid background */}
            {[0, 1, 2, 3, 4].map((i) => {
              const y = 30 + (i / 4) * (height - 60);
              const p = maxPrice - (i / 4) * priceRange;
              return (
                <g key={i}>
                  <line x1="60" y1={y} x2={width - 20} y2={y} stroke="#e2e8f0" strokeWidth="0.5" strokeDasharray="3,3" />
                  <text x="52" y={y + 3} textAnchor="end" fontSize="10" fill="#94a3b8" fontFamily="monospace">
                    ${p.toFixed(0)}
                  </text>
                </g>
              );
            })}

            {/* Candlesticks */}
            {points.map((p, i) => {
              const cx = 65 + (i / (points.length - 1)) * (width - 90);
              const isGreen = p.close >= p.open;
              const color = isGreen ? "#10b981" : "#f43f5e";
              const yHigh = 30 + (1 - (p.high - minPrice) / priceRange) * (height - 60);
              const yLow = 30 + (1 - (p.low - minPrice) / priceRange) * (height - 60);
              const yOpen = 30 + (1 - (p.open - minPrice) / priceRange) * (height - 60);
              const yClose = 30 + (1 - (p.close - minPrice) / priceRange) * (height - 60);
              const bodyY = Math.min(yOpen, yClose);
              const bodyH = Math.max(2, Math.abs(yOpen - yClose));

              return (
                <g
                  key={i}
                  onMouseEnter={() => setHoveredCandle(p)}
                  onMouseLeave={() => setHoveredCandle(null)}
                  className="cursor-pointer transition-opacity hover:opacity-80"
                >
                  <line x1={cx} y1={yHigh} x2={cx} y2={yLow} stroke={color} strokeWidth="1.2" />
                  <rect
                    x={cx - candleW / 2}
                    y={bodyY}
                    width={candleW}
                    height={bodyH}
                    fill={color}
                    rx="1"
                  />
                </g>
              );
            })}
          </svg>
        </div>

        {/* Footer */}
        <div className="mt-3 flex items-center justify-between border-t border-outline-variant/60 pt-3 text-mono-label text-secondary">
          <span className="flex items-center gap-1.5">
            <BarChart2 className="h-3.5 w-3.5 text-primary" />
            滑鼠懸浮可互動讀取每根 K 棒高低點與成交量
          </span>
          <Button variant="secondary" onClick={onClose} className="min-h-0 py-1.5 px-3 rounded-xl text-xs">
            關閉視窗
          </Button>
        </div>
      </div>
    </div>
  );
}
