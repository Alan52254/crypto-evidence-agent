import { NextResponse } from "next/server";

const SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD"];
const DISPLAY_NAMES: Record<string, string> = {
  "BTC-USD": "BTC",
  "ETH-USD": "ETH",
  "SOL-USD": "SOL",
  "BNB-USD": "BNB",
  "XRP-USD": "XRP",
};

interface TickerData {
  symbol: string;
  price: number;
  change: number;
}

export async function GET() {
  try {
    const results = await Promise.allSettled(
      SYMBOLS.map(async (sym) => {
        const url = `https://query1.finance.yahoo.com/v8/finance/chart/${sym}?interval=1d&range=1d`;
        const res = await fetch(url, {
          headers: { "User-Agent": "Mozilla/5.0" },
          next: { revalidate: 0 },
        });
        if (!res.ok) throw new Error(`Yahoo API ${res.status}`);
        const json = await res.json();
        const meta = json.chart.result[0].meta;
        const price: number = meta.regularMarketPrice;
        const prevClose: number = meta.chartPreviousClose;
        const change = ((price - prevClose) / prevClose) * 100;
        return {
          symbol: DISPLAY_NAMES[sym],
          price,
          change: Math.round(change * 100) / 100,
        } as TickerData;
      })
    );

    const tickers: TickerData[] = results
      .filter((r): r is PromiseFulfilledResult<TickerData> => r.status === "fulfilled")
      .map((r) => r.value);

    return NextResponse.json({ tickers, ts: Date.now() });
  } catch {
    return NextResponse.json(
      { error: "Failed to fetch crypto prices" },
      { status: 502 }
    );
  }
}
