const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  const requestUrl = new URL(request.url);
  const runId = requestUrl.searchParams.get("run_id")?.trim();
  if (!runId) {
    return Response.json({ error: "run_id is required" }, { status: 422 });
  }

  try {
    const upstreamUrl = new URL("/api/v1/stream_trace", BACKEND_URL);
    upstreamUrl.searchParams.set("run_id", runId);
    const upstream = await fetch(upstreamUrl, {
      headers: { Accept: "text/event-stream" },
      cache: "no-store",
      signal: request.signal,
    });

    if (!upstream.ok) {
      // Return an SSE error event so the client handles it gracefully
      const errorSSE = `event: error\ndata: ${JSON.stringify({ run_id: runId, error: `Backend returned HTTP ${upstream.status}` })}\n\n`;
      return new Response(errorSSE, {
        status: 200,
        headers: {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
        },
      });
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") ?? "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (err) {
    // Backend unreachable — send an SSE error event instead of failing
    const message = err instanceof Error ? err.message : String(err);
    const errorSSE = `event: error\ndata: ${JSON.stringify({ run_id: runId, error: `Backend unreachable: ${message}` })}\n\n`;
    return new Response(errorSSE, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  }
}
