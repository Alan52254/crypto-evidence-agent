import { NextResponse } from "next/server";

/**
 * Binance Futures USDT-M 24hr ticker API — 作為 WebSocket 的 fallback/初始資料源。
 * 前端主要透過 WebSocket 直連 Binance 即時更新，這個 route 用於初始載入。
 */

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
const DISPLAY_NAMES: Record<string, string> = {
  BTCUSDT: "BTC",
  ETHUSDT: "ETH",
  SOLUSDT: "SOL",
  BNBUSDT: "BNB",
  XRPUSDT: "XRP",
};

interface TickerData {
  symbol: string;
  price: number;
  change: number;
}

export async function GET() {
  try {
    // Binance Futures 24hr ticker — 公開 API 不需要 key
    const url = `https://fapi.binance.com/fapi/v1/ticker/24hr?symbols=${JSON.stringify(SYMBOLS)}`;
    const res = await fetch(url, { next: { revalidate: 0 } });

    if (!res.ok) throw new Error(`Binance API ${res.status}`);

    const data: Array<{
      symbol: string;
      lastPrice: string;
      priceChangePercent: string;
    }> = await res.json();

    const tickers: TickerData[] = data
      .filter((d) => DISPLAY_NAMES[d.symbol])
      .map((d) => ({
        symbol: DISPLAY_NAMES[d.symbol],
        price: parseFloat(d.lastPrice),
        change: parseFloat(d.priceChangePercent),
      }));

    // 保持幣種順序一致
    const order = ["BTC", "ETH", "SOL", "BNB", "XRP"];
    tickers.sort((a, b) => order.indexOf(a.symbol) - order.indexOf(b.symbol));

    return NextResponse.json({ tickers, ts: Date.now() });
  } catch {
    return NextResponse.json(
      { error: "Failed to fetch crypto prices from Binance" },
      { status: 502 }
    );
  }
}
