const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ run_id: string }> },
): Promise<Response> {
  const { run_id } = await params;
  try {
    const upstream = await fetch(`${BACKEND_URL}/api/v1/runs/${run_id}`, {
      cache: "no-store",
      signal: request.signal,
    });

    if (!upstream.ok) {
      return Response.json(
        { error: "not found", run_id },
        { status: upstream.status },
      );
    }

    const data = await upstream.json();
    return Response.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 },
    );
  }
}
