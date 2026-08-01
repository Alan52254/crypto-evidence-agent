const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 600; // Allow up to 10 minutes for long-running analysis

export async function GET(request: Request): Promise<Response> {
  const requestUrl = new URL(request.url);
  const runId = requestUrl.searchParams.get("run_id")?.trim();
  if (!runId) {
    return Response.json({ error: "run_id is required" }, { status: 422 });
  }

  try {
    const upstreamUrl = new URL("/api/v1/stream_trace", BACKEND_URL);
    upstreamUrl.searchParams.set("run_id", runId);

    // Use a 10-minute timeout for long-running analysis streams
    const timeoutController = new AbortController();
    const timeoutId = setTimeout(() => timeoutController.abort(), 600_000);

    // Abort if the client disconnects OR our timeout fires
    request.signal.addEventListener("abort", () => {
      clearTimeout(timeoutId);
      timeoutController.abort();
    });

    const upstream = await fetch(upstreamUrl, {
      headers: { Accept: "text/event-stream" },
      cache: "no-store",
      signal: timeoutController.signal,
      // @ts-expect-error -- undici/Node.js specific option to disable body timeout
      keepalive: true,
    });

    if (!upstream.ok) {
      clearTimeout(timeoutId);
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

    // Wrap the upstream body to clear timeout when stream ends
    const wrappedBody = new ReadableStream({
      async start(controller) {
        const reader = upstream.body?.getReader();
        if (!reader) {
          clearTimeout(timeoutId);
          controller.close();
          return;
        }
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            controller.enqueue(value);
          }
        } catch {
          // Stream interrupted — client disconnected or timeout
        } finally {
          clearTimeout(timeoutId);
          controller.close();
          reader.releaseLock();
        }
      },
    });

    return new Response(wrappedBody, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (err) {
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
