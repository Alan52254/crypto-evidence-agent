"use client";

import { useState, useEffect, useCallback } from "react";
import {
  X,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
  BarChart2,
  Activity,
} from "lucide-react";

interface CoinData {
  symbol: string;
  name: string;
  price: string;
  change24h: number;
  volume24h: string;
  marketCap: string;
  sparkline: number[];
}

const INITIAL_DATA: CoinData[] = [
  { symbol: "BTC", name: "Bitcoin", price: "67,234.18", change24h: 2.45, volume24h: "28.4B", marketCap: "1.32T", sparkline: [64, 65, 63, 66, 67, 66, 67] },
  { symbol: "ETH", name: "Ethereum", price: "3,399.52", change24h: 1.62, volume24h: "14.2B", marketCap: "408B", sparkline: [3300, 3350, 3320, 3380, 3400, 3390, 3399] },
  { symbol: "SOL", name: "Solana", price: "156.40", change24h: -0.82, volume24h: "3.8B", marketCap: "72.1B", sparkline: [158, 157, 159, 156, 155, 157, 156] },
  { symbol: "BNB", name: "BNB", price: "612.30", change24h: 0.34, volume24h: "1.9B", marketCap: "91.2B", sparkline: [608, 610, 609, 611, 612, 610, 612] },
  { symbol: "XRP", name: "XRP", price: "0.6312", change24h: -1.15, volume24h: "2.1B", marketCap: "34.8B", sparkline: [0.64, 0.635, 0.638, 0.632, 0.630, 0.633, 0.631] },
];

interface MarketsPanelProps {
  open: boolean;
  onClose: () => void;
}

export function MarketsPanel({ open, onClose }: MarketsPanelProps) {
  const [coins, setCoins] = useState<CoinData[]>(INITIAL_DATA);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(new Date());

  const refresh = useCallback(async () => {
    setLoading(true);
    // Simulate live data refresh with slight price variations
    await new Promise((r) => setTimeout(r, 800));
    setCoins((prev) =>
      prev.map((coin) => {
        const variation = (Math.random() - 0.5) * 0.02;
        const basePrice = parseFloat(coin.price.replace(/,/g, ""));
        const newPrice = basePrice * (1 + variation);
        return {
          ...coin,
          price: newPrice > 1000
            ? newPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : newPrice.toFixed(4),
          change24h: coin.change24h + (Math.random() - 0.5) * 0.3,
        };
      }),
    );
    setLastUpdated(new Date());
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    const interval = setInterval(refresh, 30000); // auto refresh every 30s
    return () => clearInterval(interval);
  }, [open, refresh]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16 bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-2xl rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 shadow-dropdown animate-fade-in max-h-[80vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-outline-variant pb-4 mb-4">
          <div className="flex items-center gap-2">
            <BarChart2 className="h-5 w-5 text-primary" />
            <h2 className="text-headline-md font-bold text-primary">Markets Overview</h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refresh}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-outline-variant px-2.5 py-1.5 text-[11px] text-secondary hover:bg-surface-container-low transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
            <button onClick={onClose} className="rounded-md p-1.5 text-secondary hover:bg-surface-container-low">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Market summary */}
        <div className="grid grid-cols-3 gap-3 mb-4">
          <div className="rounded-xl border border-outline-variant bg-surface-container-low p-3 text-center">
            <span className="text-[10px] text-secondary uppercase font-semibold block">全球市值</span>
            <span className="font-mono text-[14px] font-bold text-primary">$2.54T</span>
          </div>
          <div className="rounded-xl border border-outline-variant bg-surface-container-low p-3 text-center">
            <span className="text-[10px] text-secondary uppercase font-semibold block">24H 交易量</span>
            <span className="font-mono text-[14px] font-bold text-primary">$89.2B</span>
          </div>
          <div className="rounded-xl border border-outline-variant bg-surface-container-low p-3 text-center">
            <span className="text-[10px] text-secondary uppercase font-semibold block">BTC 主導率</span>
            <span className="font-mono text-[14px] font-bold text-primary">52.1%</span>
          </div>
        </div>

        {/* Coin list */}
        <div className="overflow-y-auto chat-scroll flex-1">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-outline-variant text-[10px] text-secondary uppercase">
                <th className="text-left py-2 font-semibold">幣種</th>
                <th className="text-right py-2 font-semibold">價格 (USD)</th>
                <th className="text-right py-2 font-semibold">24H 變化</th>
                <th className="text-right py-2 font-semibold hidden sm:table-cell">24H 交易量</th>
                <th className="text-right py-2 font-semibold hidden md:table-cell">市值</th>
                <th className="text-right py-2 font-semibold">趨勢</th>
              </tr>
            </thead>
            <tbody>
              {coins.map((coin) => (
                <tr key={coin.symbol} className="border-b border-outline-variant/50 hover:bg-surface-container-low transition-colors">
                  <td className="py-3">
                    <div className="flex items-center gap-2">
                      <span className="rounded-md bg-primary/10 px-2 py-0.5 font-mono text-[11px] font-bold text-primary">
                        {coin.symbol}
                      </span>
                      <span className="text-secondary hidden sm:inline">{coin.name}</span>
                    </div>
                  </td>
                  <td className="text-right font-mono font-semibold text-primary py-3">
                    ${coin.price}
                  </td>
                  <td className="text-right py-3">
                    <span className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 font-mono text-[11px] font-bold ${
                      coin.change24h >= 0
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-red-50 text-red-700"
                    }`}>
                      {coin.change24h >= 0 ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
                      {coin.change24h >= 0 ? "+" : ""}{coin.change24h.toFixed(2)}%
                    </span>
                  </td>
                  <td className="text-right font-mono text-secondary py-3 hidden sm:table-cell">${coin.volume24h}</td>
                  <td className="text-right font-mono text-secondary py-3 hidden md:table-cell">${coin.marketCap}</td>
                  <td className="text-right py-3">
                    {coin.change24h >= 0
                      ? <TrendingUp className="h-4 w-4 text-emerald-600 inline" />
                      : <TrendingDown className="h-4 w-4 text-red-500 inline" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-outline-variant mt-3 flex items-center justify-between">
          <span className="text-[10px] text-secondary font-mono">
            最後更新: {lastUpdated.toLocaleTimeString("zh-TW")}
          </span>
          <span className="text-[10px] text-secondary">
            數據來源: CoinGecko / Binance (競賽展示數據)
          </span>
        </div>
      </div>
    </div>
  );
}
