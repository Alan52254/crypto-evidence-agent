const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ run_id: string }> },
): Promise<Response> {
  const { run_id } = await params;
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/runs/${run_id}/logs`, { cache: "no-store" });
    if (!res.ok) return Response.json({ error: "not found" }, { status: res.status });
    const text = await res.text();
    return new Response(text, { headers: { "Content-Type": "application/x-ndjson" } });
  } catch {
    return Response.json({ error: "Backend unreachable" }, { status: 502 });
  }
}
