"use client";

import { useState, useEffect, useRef, useCallback } from "react";
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

interface TickerData {
  symbol: string;
  price: number;
  change: number;
}

const BINANCE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
const DISPLAY_MAP: Record<string, string> = {
  BTCUSDT: "BTC",
  ETHUSDT: "ETH",
  SOLUSDT: "SOL",
  BNBUSDT: "BNB",
  XRPUSDT: "XRP",
};
const SYMBOL_ORDER = ["BTC", "ETH", "SOL", "BNB", "XRP"];

function useCryptoTickers() {
  const [tickers, setTickers] = useState<TickerData[]>([]);
  const marqueeRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const tickerMapRef = useRef<Record<string, TickerData>>({});
  const shouldReconnectRef = useRef(true);

  // 初始載入 — 用 REST API fallback
  const fetchInitial = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/crypto-prices");
      if (!res.ok) return;
      const data = await res.json();
      if (data.tickers?.length) {
        setTickers(data.tickers);
        data.tickers.forEach((t: TickerData) => {
          tickerMapRef.current[t.symbol] = t;
        });
      }
    } catch { /* silent */ }
  }, []);

  // Binance WebSocket 即時連線（含自動重連、指數退避、心跳、24hr 重連）
  useEffect(() => {
    fetchInitial();
    shouldReconnectRef.current = true;
    let reconnectDelay = 5000; // 初始 5 秒，指數退避
    let refreshInterval: ReturnType<typeof setInterval> | null = null;
    let heartbeatInterval: ReturnType<typeof setInterval> | null = null;
    let maxLifetimeTimer: ReturnType<typeof setTimeout> | null = null;

    const clearTimers = () => {
      if (refreshInterval) { clearInterval(refreshInterval); refreshInterval = null; }
      if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
      if (maxLifetimeTimer) { clearTimeout(maxLifetimeTimer); maxLifetimeTimer = null; }
    };

    const connectWs = () => {
      if (!shouldReconnectRef.current) return;

      const ws = new WebSocket("wss://ws-fapi.binance.com/ws-fapi/v1");
      wsRef.current = ws;

      ws.onopen = () => {
        // 連線成功 → 重置退避延遲
        reconnectDelay = 5000;

        // 初始查詢五幣
        BINANCE_SYMBOLS.forEach((sym, idx) => {
          ws.send(JSON.stringify({
            id: `ticker-${idx}-${Date.now()}`,
            method: "ticker.24hr",
            params: { symbol: sym },
          }));
        });

        // 每 3 秒輪詢即時報價
        refreshInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            BINANCE_SYMBOLS.forEach((sym, idx) => {
              ws.send(JSON.stringify({
                id: `refresh-${idx}-${Date.now()}`,
                method: "ticker.24hr",
                params: { symbol: sym },
              }));
            });
          }
        }, 3000);

        // 心跳：每 3 分鐘發送 ping 維持連線
        heartbeatInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
              id: `ping-${Date.now()}`,
              method: "ping",
            }));
          }
        }, 180000); // 3 分鐘

        // 24 小時主動重連（連線最長 24 小時）
        maxLifetimeTimer = setTimeout(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.close(); // 觸發 onclose → 自動重連
          }
        }, 23 * 60 * 60 * 1000); // 23 小時（提前 1 小時重連）
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(typeof event.data === "string" ? event.data : "");
          if (msg.result && msg.result.symbol && DISPLAY_MAP[msg.result.symbol]) {
            const displayName = DISPLAY_MAP[msg.result.symbol];
            const updated: TickerData = {
              symbol: displayName,
              price: parseFloat(msg.result.lastPrice),
              change: parseFloat(msg.result.priceChangePercent),
            };
            tickerMapRef.current[displayName] = updated;

            const sorted = SYMBOL_ORDER
              .map((s) => tickerMapRef.current[s])
              .filter(Boolean) as TickerData[];
            if (sorted.length > 0) setTickers(sorted);
          }
        } catch { /* ignore parse errors */ }
      };

      ws.onerror = () => { /* silent — onclose will handle reconnect */ };

      ws.onclose = () => {
        clearTimers();
        // 指數退避重連（最長 60 秒）
        if (shouldReconnectRef.current) {
          setTimeout(connectWs, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, 60000);
        }
      };
    };

    connectWs();

    return () => {
      shouldReconnectRef.current = false;
      clearTimers();
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [fetchInitial]);

  return { tickers, marqueeRef };
}

export function TopBar({ onMenuToggle, remaining, running, onExport, onThemeToggle, darkMode }: TopBarProps) {
  const [marketsOpen, setMarketsOpen] = useState(false);
  const [sectorsOpen, setSectorsOpen] = useState(false);
  const [portfolioOpen, setPortfolioOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const { tickers, marqueeRef } = useCryptoTickers();

  const minutes = String(Math.floor(remaining / 60)).padStart(2, "0");
  const seconds = String(remaining % 60).padStart(2, "0");

  // Duplicate for seamless loop
  const items = tickers.length > 0 ? [...tickers, ...tickers] : [];

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

          {/* Desktop Market Tickers — Marquee */}
          <div className="hidden min-w-0 flex-1 items-center overflow-hidden md:flex">
            <TrendingUp className="h-4 w-4 flex-shrink-0 text-secondary mr-2" />
            <div className="relative flex-1 overflow-hidden">
              <div
                ref={marqueeRef}
                className="flex w-max items-center gap-6 animate-marquee"
              >
                {items.map(({ symbol, price, change }, i) => {
                  const up = change >= 0;
                  return (
                    <a
                      key={`${symbol}-${i}`}
                      href="https://tw.stock.yahoo.com/cryptocurrencies"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex flex-shrink-0 items-center gap-2"
                    >
                      <span className="text-label-caps font-bold text-primary font-mono">
                        {symbol}
                      </span>
                      <span className="font-mono text-mono-label font-medium text-on-surface">
                        ${price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </span>
                      <span
                        className={`flex items-center gap-0.5 rounded-md px-1.5 py-0.5 font-mono text-[11px] font-bold ${
                          up
                            ? "bg-red-50 text-red-700 border border-red-200/80"
                            : "bg-emerald-50 text-emerald-700 border border-emerald-200/80"
                        }`}
                      >
                        {up ? (
                          <ArrowUpRight className="h-3 w-3 text-red-600" />
                        ) : (
                          <ArrowDownRight className="h-3 w-3 text-emerald-600" />
                        )}
                        {up ? "+" : ""}{change.toFixed(2)}%
                      </span>
                    </a>
                  );
                })}
              </div>
            </div>
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
