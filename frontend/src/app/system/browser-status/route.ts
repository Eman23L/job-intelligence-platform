import { NextResponse } from "next/server";

const PRODUCTION_API_BASE_URL = "https://job-intelligence-ai-63rj.onrender.com";

export async function GET() {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? PRODUCTION_API_BASE_URL;
  const response = await fetch(`${apiBaseUrl}/system/browser-status`, { cache: "no-store" });
  const body = await response.json();

  return NextResponse.json(body, { status: response.status });
}
