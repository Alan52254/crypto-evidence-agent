const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  try {
    const body = await request.json();
    const upstream = await fetch(`${BACKEND_URL}/api/v1/export-artifacts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: request.signal,
    });

    if (!upstream.ok) {
      const errorData = await upstream.json().catch(() => ({ error: "Export failed" }));
      return Response.json(errorData, { status: upstream.status });
    }

    // Stream the ZIP binary response
    const zipBuffer = await upstream.arrayBuffer();
    const contentDisposition = upstream.headers.get("Content-Disposition")
      ?? 'attachment; filename="HoyaBit_Analysis.zip"';

    return new Response(zipBuffer, {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": contentDisposition,
        "Content-Length": String(zipBuffer.byteLength),
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 },
    );
  }
}
