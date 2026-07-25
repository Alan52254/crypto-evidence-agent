"use client";

import { X, PieChart, TrendingUp, TrendingDown, ArrowUpRight, ArrowDownRight } from "lucide-react";

interface Sector {
  name: string;
  nameZh: string;
  change24h: number;
  marketCap: string;
  topCoins: string[];
  color: string;
}

const SECTORS: Sector[] = [
  {
    name: "Layer 1",
    nameZh: "公鏈基礎設施",
    change24h: 1.82,
    marketCap: "$1.89T",
    topCoins: ["BTC", "ETH", "SOL", "ADA"],
    color: "bg-blue-500",
  },
  {
    name: "DeFi",
    nameZh: "去中心化金融",
    change24h: 3.14,
    marketCap: "$98.2B",
    topCoins: ["UNI", "AAVE", "MKR", "LDO"],
    color: "bg-purple-500",
  },
  {
    name: "Exchange Tokens",
    nameZh: "交易所代幣",
    change24h: 0.45,
    marketCap: "$112B",
    topCoins: ["BNB", "OKB", "CRO", "GT"],
    color: "bg-amber-500",
  },
  {
    name: "Meme",
    nameZh: "迷因幣",
    change24h: -2.31,
    marketCap: "$52.8B",
    topCoins: ["DOGE", "SHIB", "PEPE", "WIF"],
    color: "bg-pink-500",
  },
  {
    name: "AI & Big Data",
    nameZh: "人工智慧與大數據",
    change24h: 4.67,
    marketCap: "$38.5B",
    topCoins: ["FET", "RNDR", "AGIX", "OCEAN"],
    color: "bg-emerald-500",
  },
  {
    name: "RWA",
    nameZh: "真實世界資產",
    change24h: 1.23,
    marketCap: "$12.1B",
    topCoins: ["ONDO", "MKR", "COMP", "SNX"],
    color: "bg-cyan-500",
  },
  {
    name: "Gaming & Metaverse",
    nameZh: "遊戲與元宇宙",
    change24h: -0.95,
    marketCap: "$18.7B",
    topCoins: ["AXS", "SAND", "MANA", "IMX"],
    color: "bg-orange-500",
  },
  {
    name: "Infrastructure",
    nameZh: "基礎建設 / 預言機",
    change24h: 0.88,
    marketCap: "$24.3B",
    topCoins: ["LINK", "DOT", "ATOM", "GRT"],
    color: "bg-indigo-500",
  },
];

interface SectorsPanelProps {
  open: boolean;
  onClose: () => void;
}

export function SectorsPanel({ open, onClose }: SectorsPanelProps) {
  if (!open) return null;

  const sortedSectors = [...SECTORS].sort((a, b) => b.change24h - a.change24h);
  const totalMarket = "$2.54T";

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16 bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-2xl rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 shadow-dropdown animate-fade-in max-h-[80vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-outline-variant pb-4 mb-4">
          <div className="flex items-center gap-2">
            <PieChart className="h-5 w-5 text-primary" />
            <h2 className="text-headline-md font-bold text-primary">Sector Analysis</h2>
          </div>
          <button onClick={onClose} className="rounded-md p-1.5 text-secondary hover:bg-surface-container-low">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Sector heatmap summary */}
        <div className="flex flex-wrap gap-2 mb-4">
          {sortedSectors.map((sector) => (
            <div
              key={sector.name}
              className={`rounded-lg px-3 py-1.5 text-[11px] font-mono font-bold text-white ${
                sector.change24h >= 2
                  ? "bg-emerald-600"
                  : sector.change24h >= 0
                  ? "bg-emerald-400"
                  : sector.change24h >= -2
                  ? "bg-red-400"
                  : "bg-red-600"
              }`}
            >
              {sector.name} {sector.change24h >= 0 ? "+" : ""}{sector.change24h.toFixed(1)}%
            </div>
          ))}
        </div>

        {/* Sector details */}
        <div className="overflow-y-auto chat-scroll flex-1 space-y-3">
          {sortedSectors.map((sector) => (
            <div
              key={sector.name}
              className="rounded-xl border border-outline-variant bg-surface-container-low p-4 hover:border-primary/30 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <div className={`h-3 w-3 rounded-full ${sector.color}`} />
                  <div>
                    <span className="text-[13px] font-bold text-primary">{sector.name}</span>
                    <span className="text-[11px] text-secondary ml-2">{sector.nameZh}</span>
                  </div>
                </div>
                <span className={`inline-flex items-center gap-0.5 rounded-md px-2 py-0.5 font-mono text-[11px] font-bold ${
                  sector.change24h >= 0
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-red-50 text-red-700"
                }`}>
                  {sector.change24h >= 0 ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
                  {sector.change24h >= 0 ? "+" : ""}{sector.change24h.toFixed(2)}%
                </span>
              </div>
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-secondary font-mono">市值: <strong className="text-primary">{sector.marketCap}</strong></span>
                <div className="flex items-center gap-1">
                  {sector.topCoins.map((coin) => (
                    <span key={coin} className="rounded bg-surface-container-high px-1.5 py-0.5 font-mono text-[10px] font-semibold text-primary">
                      {coin}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-outline-variant mt-3">
          <p className="text-[10px] text-secondary text-center">
            板塊分類依 CoinGecko Categories，24H 漲跌幅為加權平均 | 全球加密市場總市值: {totalMarket}
          </p>
        </div>
      </div>
    </div>
  );
}
