const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * GET /api/v1/runs — fetch list of recent runs from backend.
 * The backend doesn't have a dedicated list endpoint, so we:
 * 1. Try fetching the recent run IDs from the backend HTML index and parse them
 * 2. Then fetch each run's details
 *
 * In practice, we add a custom endpoint that aggregates this.
 * For now, we'll directly query available run IDs from the backend.
 */
export async function GET(request: Request): Promise<Response> {
  try {
    // 1. Try direct high-performance API endpoint from Python backend
    const apiRes = await fetch(`${BACKEND_URL}/api/v1/runs`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });

    if (apiRes.ok) {
      const data = await apiRes.json();
      return Response.json(data);
    }

    // 2. Fallback to HTML index scraping if legacy backend version is running
    const indexRes = await fetch(`${BACKEND_URL}/`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });

    if (!indexRes.ok) {
      return Response.json({ runs: [] });
    }

    const html = await indexRes.text();
    const matches = [...html.matchAll(/href="\/run\/([^"]+)"/g)];
    const runIds = matches.map((m) => m[1]);

    if (runIds.length === 0) {
      return Response.json({ runs: [] });
    }

    const limited = runIds.slice(0, 20);
    const details = await Promise.allSettled(
      limited.map(async (id) => {
        const res = await fetch(`${BACKEND_URL}/api/v1/runs/${id}`, {
          cache: "no-store",
          signal: AbortSignal.timeout(3000),
        });
        if (!res.ok) return null;
        return res.json();
      }),
    );

    const runs = details
      .map((result) => (result.status === "fulfilled" ? result.value : null))
      .filter(Boolean);

    return Response.json({ runs });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json(
      { error: `Backend unreachable: ${message}`, runs: [] },
      { status: 502 },
    );
  }
}
