"use client";

import { useState, useEffect } from "react";
import {
  Menu,
  Bell,
  Moon,
  Sun,
  Download,
  ArrowUpRight,
  ArrowDownRight,
  TrendingUp,
  Timer,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { MarketsPanel } from "@/components/panels/markets-panel";
import { SectorsPanel } from "@/components/panels/sectors-panel";
import { PortfolioPanel } from "@/components/panels/portfolio-panel";
import { NotificationsPanel } from "@/components/panels/notifications-panel";

interface TopBarProps {
  onMenuToggle: () => void;
  remaining: number;
  running: boolean;
  onExport?: () => void;
  onThemeToggle?: () => void;
  darkMode?: boolean;
}

const TICKERS = [
  { symbol: "BTC", price: "67,234.18", change: "+2.45%", up: true },
  { symbol: "ETH", price: "3,399.52", change: "+1.62%", up: true },
  { symbol: "SOL", price: "156.40", change: "-0.82%", up: false },
];

export function TopBar({ onMenuToggle, remaining, running, onExport, onThemeToggle, darkMode }: TopBarProps) {
  const [marketsOpen, setMarketsOpen] = useState(false);
  const [sectorsOpen, setSectorsOpen] = useState(false);
  const [portfolioOpen, setPortfolioOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);

  const minutes = String(Math.floor(remaining / 60)).padStart(2, "0");
  const seconds = String(remaining % 60).padStart(2, "0");

  return (
    <>
      <header className="sticky top-0 z-10 flex h-14 flex-shrink-0 items-center justify-between border-b border-outline-variant bg-surface/95 px-4 backdrop-blur-sm lg:px-6">
        {/* Left side */}
        <div className="flex min-w-0 flex-1 items-center gap-4">
          {/* Mobile menu */}
          <button
            onClick={onMenuToggle}
            className="flex-shrink-0 rounded-md p-1.5 text-secondary hover:bg-surface-container-low hover:text-primary md:hidden"
            aria-label="Toggle menu"
          >
            <Menu className="h-5 w-5" />
          </button>

          {/* Mobile brand */}
          <span className="text-headline-md font-bold text-primary md:hidden">
            Alpha Intel AI
          </span>

          {/* Desktop Market Tickers with Red/Green indicators */}
          <div className="hidden min-w-0 items-center gap-3 md:flex">
            <TrendingUp className="h-4 w-4 flex-shrink-0 text-secondary" />
            {TICKERS.map(({ symbol, price, change, up }, i) => (
              <div key={symbol} className="flex flex-shrink-0 items-center gap-2">
                {i > 0 && (
                  <div className="mx-1 h-4 w-px bg-outline-variant" />
                )}
                <span className="text-label-caps font-bold text-primary font-mono">
                  {symbol}
                </span>
                <span className="font-mono text-mono-label font-medium text-on-surface">
                  ${price}
                </span>
                <span
                  className={`flex items-center gap-0.5 rounded-md px-1.5 py-0.5 font-mono text-[11px] font-bold ${
                    up
                      ? "bg-emerald-50 text-emerald-700 border border-emerald-200/80"
                      : "bg-red-50 text-red-700 border border-red-200/80"
                  }`}
                >
                  {up ? (
                    <ArrowUpRight className="h-3 w-3 text-emerald-600" />
                  ) : (
                    <ArrowDownRight className="h-3 w-3 text-red-600" />
                  )}
                  {change}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Right side */}
        <div className="flex flex-shrink-0 items-center gap-2">
          {/* Agent timer — only show when running */}
          {running && (
            <div className="mr-2 flex items-center gap-1.5 rounded-pill border border-emerald-300 bg-emerald-50 px-3 py-1 animate-pulse">
              <Timer className="h-3.5 w-3.5 text-emerald-700" />
              <span
                className={`tabular font-mono text-[13px] font-bold ${
                  remaining < 60 ? "text-red-700" : "text-emerald-800"
                }`}
              >
                {minutes}:{seconds}
              </span>
            </div>
          )}

          {/* Navigation links */}
          <div className="hidden items-center gap-3 border-r border-outline-variant pr-3 text-[13px] font-medium lg:flex">
            <button
              onClick={() => setMarketsOpen(true)}
              className="text-secondary transition-colors hover:text-primary"
            >
              Markets
            </button>
            <button
              onClick={() => setSectorsOpen(true)}
              className="text-secondary transition-colors hover:text-primary"
            >
              Sectors
            </button>
            <button
              onClick={() => setPortfolioOpen(true)}
              className="text-secondary transition-colors hover:text-primary"
            >
              Portfolio
            </button>
          </div>

          {/* Notifications */}
          <button
            onClick={() => setNotificationsOpen(true)}
            className="relative rounded-lg p-2 text-secondary transition-colors hover:bg-surface-container-low hover:text-primary"
            aria-label="Notifications"
          >
            <Bell className="h-4 w-4" />
            {/* Unread dot */}
            <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-red-500" />
          </button>

          {/* Dark mode toggle */}
          <button
            onClick={onThemeToggle}
            className="rounded-lg p-2 text-secondary transition-colors hover:bg-surface-container-low hover:text-primary"
            aria-label="Toggle dark mode"
          >
            {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>

          {/* Export button */}
          <Button
            variant="secondary"
            className="hidden min-h-[32px] px-3 text-[11px] lg:inline-flex"
            onClick={onExport}
          >
            <Download className="h-3.5 w-3.5" />
            Export
          </Button>

          {/* User avatar */}
          <div className="ml-1 h-8 w-8 flex-shrink-0 overflow-hidden rounded-full border border-outline-variant bg-surface-container-high">
            <div className="flex h-full w-full items-center justify-center font-mono text-[11px] font-bold text-primary">
              AL
            </div>
          </div>
        </div>
      </header>

      {/* Panels */}
      <MarketsPanel open={marketsOpen} onClose={() => setMarketsOpen(false)} />
      <SectorsPanel open={sectorsOpen} onClose={() => setSectorsOpen(false)} />
      <PortfolioPanel open={portfolioOpen} onClose={() => setPortfolioOpen(false)} />
      <NotificationsPanel open={notificationsOpen} onClose={() => setNotificationsOpen(false)} />
    </>
  );
}
