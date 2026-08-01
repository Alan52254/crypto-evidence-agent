import http from "node:http";

const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Next.js 15 route segment config: max duration 15 minutes
export const maxDuration = 900;

export async function GET(request: Request): Promise<Response> {
  const requestUrl = new URL(request.url);
  const runId = requestUrl.searchParams.get("run_id")?.trim();
  if (!runId) {
    return Response.json({ error: "run_id is required" }, { status: 422 });
  }

  // Use Node.js native http to avoid undici's hardcoded 300s bodyTimeout.
  // undici's fetch cannot be configured to allow > 300s idle body,
  // which causes BodyTimeoutError on long-running SSE streams.
  const upstreamUrl = new URL("/api/v1/stream_trace", BACKEND_URL);
  upstreamUrl.searchParams.set("run_id", runId);

  const stream = new ReadableStream({
    start(controller) {
      const req = http.get(
        upstreamUrl.toString(),
        { headers: { Accept: "text/event-stream" }, timeout: 0 },
        (res) => {
          if (res.statusCode !== 200) {
            const errSSE = `event: error\ndata: ${JSON.stringify({ run_id: runId, error: `Backend HTTP ${res.statusCode}` })}\n\n`;
            controller.enqueue(new TextEncoder().encode(errSSE));
            controller.close();
            return;
          }
          res.on("data", (chunk: Buffer) => {
            controller.enqueue(chunk);
          });
          res.on("end", () => {
            controller.close();
          });
          res.on("error", (err) => {
            const errSSE = `event: error\ndata: ${JSON.stringify({ run_id: runId, error: err.message })}\n\n`;
            controller.enqueue(new TextEncoder().encode(errSSE));
            controller.close();
          });
        },
      );

      req.on("error", (err) => {
        const errSSE = `event: error\ndata: ${JSON.stringify({ run_id: runId, error: `Backend unreachable: ${err.message}` })}\n\n`;
        controller.enqueue(new TextEncoder().encode(errSSE));
        controller.close();
      });

      // Abort upstream request if client disconnects
      request.signal.addEventListener("abort", () => {
        req.destroy();
        controller.close();
      });
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
