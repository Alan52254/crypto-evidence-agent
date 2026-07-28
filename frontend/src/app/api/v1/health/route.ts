const BACKEND_URL = process.env.HOYABIT_BACKEND_URL ?? "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(): Promise<Response> {
  const status: {
    frontend: string;
    backend: string;
    backend_url: string;
    timestamp: string;
  } = {
    frontend: "ok",
    backend: "unreachable",
    backend_url: BACKEND_URL,
    timestamp: new Date().toISOString(),
  };

  try {
    const res = await fetch(`${BACKEND_URL}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    status.backend = res.ok ? "ok" : `error (HTTP ${res.status})`;
  } catch {
    status.backend = "unreachable";
  }

  const httpStatus = status.backend === "ok" ? 200 : 503;
  return Response.json(status, { status: httpStatus });
}
