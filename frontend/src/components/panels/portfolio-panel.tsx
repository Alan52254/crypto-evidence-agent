"use client";

import { useState } from "react";
import {
  X,
  Wallet,
  Plus,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownRight,
  PieChart,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Asset } from "@/lib/contracts";

interface Holding {
  asset: Asset;
  amount: number;
  avgCost: number;
  currentPrice: number;
}

const DEMO_HOLDINGS: Holding[] = [
  { asset: "BTC", amount: 0.45, avgCost: 62000, currentPrice: 67234.18 },
  { asset: "ETH", amount: 5.2, avgCost: 3100, currentPrice: 3399.52 },
  { asset: "SOL", amount: 25, avgCost: 140, currentPrice: 156.40 },
  { asset: "BNB", amount: 3.0, avgCost: 580, currentPrice: 612.30 },
];

interface PortfolioPanelProps {
  open: boolean;
  onClose: () => void;
}

export function PortfolioPanel({ open, onClose }: PortfolioPanelProps) {
  const [holdings, setHoldings] = useState<Holding[]>(DEMO_HOLDINGS);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newAsset, setNewAsset] = useState<Asset>("BTC");
  const [newAmount, setNewAmount] = useState("");
  const [newCost, setNewCost] = useState("");

  if (!open) return null;

  const totalValue = holdings.reduce((sum, h) => sum + h.amount * h.currentPrice, 0);
  const totalCost = holdings.reduce((sum, h) => sum + h.amount * h.avgCost, 0);
  const totalPnl = totalValue - totalCost;
  const totalPnlPercent = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;

  function addHolding() {
    const amount = parseFloat(newAmount);
    const cost = parseFloat(newCost);
    if (isNaN(amount) || isNaN(cost) || amount <= 0 || cost <= 0) return;

    const priceMap: Record<Asset, number> = {
      BTC: 67234.18,
      ETH: 3399.52,
      SOL: 156.40,
      BNB: 612.30,
      XRP: 0.6312,
    };

    const existing = holdings.find((h) => h.asset === newAsset);
    if (existing) {
      setHoldings(
        holdings.map((h) =>
          h.asset === newAsset
            ? {
                ...h,
                amount: h.amount + amount,
                avgCost: (h.amount * h.avgCost + amount * cost) / (h.amount + amount),
              }
            : h,
        ),
      );
    } else {
      setHoldings([
        ...holdings,
        { asset: newAsset, amount, avgCost: cost, currentPrice: priceMap[newAsset] },
      ]);
    }

    setNewAmount("");
    setNewCost("");
    setShowAddForm(false);
  }

  function removeHolding(asset: Asset) {
    setHoldings(holdings.filter((h) => h.asset !== asset));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16 bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-xl rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 shadow-dropdown animate-fade-in max-h-[80vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-outline-variant pb-4 mb-4">
          <div className="flex items-center gap-2">
            <Wallet className="h-5 w-5 text-primary" />
            <h2 className="text-headline-md font-bold text-primary">Portfolio Tracker</h2>
          </div>
          <button onClick={onClose} className="rounded-md p-1.5 text-secondary hover:bg-surface-container-low">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Portfolio summary */}
        <div className="grid grid-cols-3 gap-3 mb-4">
          <div className="rounded-xl border border-outline-variant bg-surface-container-low p-3 text-center">
            <span className="text-[10px] text-secondary uppercase font-semibold block">總市值</span>
            <span className="font-mono text-[14px] font-bold text-primary">
              ${totalValue.toLocaleString("en-US", { maximumFractionDigits: 0 })}
            </span>
          </div>
          <div className="rounded-xl border border-outline-variant bg-surface-container-low p-3 text-center">
            <span className="text-[10px] text-secondary uppercase font-semibold block">總投入成本</span>
            <span className="font-mono text-[14px] font-bold text-primary">
              ${totalCost.toLocaleString("en-US", { maximumFractionDigits: 0 })}
            </span>
          </div>
          <div className={`rounded-xl border p-3 text-center ${totalPnl >= 0 ? "border-emerald-200 bg-emerald-50/50" : "border-red-200 bg-red-50/50"}`}>
            <span className="text-[10px] text-secondary uppercase font-semibold block">未實現損益</span>
            <span className={`font-mono text-[14px] font-bold ${totalPnl >= 0 ? "text-emerald-700" : "text-red-700"}`}>
              {totalPnl >= 0 ? "+" : ""}${Math.abs(totalPnl).toLocaleString("en-US", { maximumFractionDigits: 0 })}
              <span className="text-[10px] ml-1">({totalPnlPercent >= 0 ? "+" : ""}{totalPnlPercent.toFixed(1)}%)</span>
            </span>
          </div>
        </div>

        {/* Holdings list */}
        <div className="overflow-y-auto chat-scroll flex-1 space-y-2 mb-4">
          {holdings.map((holding) => {
            const value = holding.amount * holding.currentPrice;
            const pnl = (holding.currentPrice - holding.avgCost) * holding.amount;
            const pnlPercent = ((holding.currentPrice - holding.avgCost) / holding.avgCost) * 100;
            const allocation = totalValue > 0 ? (value / totalValue) * 100 : 0;

            return (
              <div
                key={holding.asset}
                className="rounded-xl border border-outline-variant bg-surface-container-low p-4 hover:border-primary/30 transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-primary px-2.5 py-0.5 font-mono text-[11px] font-bold text-on-primary">
                      {holding.asset}
                    </span>
                    <span className="text-[11px] text-secondary font-mono">
                      {holding.amount} 枚
                    </span>
                  </div>
                  <button
                    onClick={() => removeHolding(holding.asset)}
                    className="text-[10px] text-red-500 hover:text-red-700 transition-colors"
                  >
                    移除
                  </button>
                </div>
                <div className="grid grid-cols-4 gap-2 text-[11px]">
                  <div>
                    <span className="text-secondary block">現價</span>
                    <span className="font-mono font-semibold text-primary">${holding.currentPrice.toLocaleString()}</span>
                  </div>
                  <div>
                    <span className="text-secondary block">均價</span>
                    <span className="font-mono font-semibold text-primary">${holding.avgCost.toLocaleString()}</span>
                  </div>
                  <div>
                    <span className="text-secondary block">損益</span>
                    <span className={`font-mono font-semibold ${pnl >= 0 ? "text-emerald-700" : "text-red-700"}`}>
                      {pnl >= 0 ? "+" : ""}{pnlPercent.toFixed(1)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-secondary block">佔比</span>
                    <span className="font-mono font-semibold text-primary">{allocation.toFixed(1)}%</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Add form */}
        {showAddForm && (
          <div className="rounded-xl border border-primary/30 bg-surface-container-low p-4 mb-3 animate-fade-in">
            <div className="grid grid-cols-3 gap-2 mb-3">
              <div>
                <label className="text-[10px] text-secondary uppercase font-semibold block mb-1">幣種</label>
                <select
                  value={newAsset}
                  onChange={(e) => setNewAsset(e.target.value as Asset)}
                  className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-2 py-1.5 font-mono text-[12px] text-primary"
                >
                  {(["BTC", "ETH", "SOL", "BNB", "XRP"] as Asset[]).map((a) => (
                    <option key={a} value={a}>{a}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-[10px] text-secondary uppercase font-semibold block mb-1">數量</label>
                <input
                  type="number"
                  step="any"
                  value={newAmount}
                  onChange={(e) => setNewAmount(e.target.value)}
                  placeholder="0.5"
                  className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-2 py-1.5 font-mono text-[12px] text-primary placeholder:text-secondary"
                />
              </div>
              <div>
                <label className="text-[10px] text-secondary uppercase font-semibold block mb-1">均價 (USD)</label>
                <input
                  type="number"
                  step="any"
                  value={newCost}
                  onChange={(e) => setNewCost(e.target.value)}
                  placeholder="65000"
                  className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-2 py-1.5 font-mono text-[12px] text-primary placeholder:text-secondary"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" className="text-[11px] min-h-[32px] px-3" onClick={() => setShowAddForm(false)}>取消</Button>
              <Button className="text-[11px] min-h-[32px] px-3" onClick={addHolding}>確認新增</Button>
            </div>
          </div>
        )}

        {/* Add button */}
        {!showAddForm && (
          <Button variant="secondary" className="w-full gap-2 text-[12px]" onClick={() => setShowAddForm(true)}>
            <Plus className="h-4 w-4" /> 新增持倉
          </Button>
        )}

        {/* Disclaimer */}
        <p className="text-[10px] text-secondary text-center mt-3">
          投資組合追蹤僅供本地記錄，數據不會上傳。HOYA BIT 提供 100% 法幣信託隔離。
        </p>
      </div>
    </div>
  );
}
