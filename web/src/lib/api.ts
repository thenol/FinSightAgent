import { clearToken, getToken } from "@/lib/auth";
import type { Envelope } from "@/types/api";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type RequestOptions = RequestInit & {
  token?: string | null;
};

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  const token = options.token === undefined ? getToken() : options.token;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...options, headers });
  let body: Envelope<T> | null = null;
  try {
    body = (await response.json()) as Envelope<T>;
  } catch {
    body = null;
  }

  if (response.status === 401) {
    clearToken();
    throw new ApiError("登录已过期，请重新登录", 401, "AUTH_REQUIRED");
  }
  if (!response.ok) {
    const code = body?.error?.code || String(body?.error?.message || `HTTP_${response.status}`);
    const message = body?.error?.message || code;
    throw new ApiError(message, response.status, code);
  }
  if (!body || !("data" in body)) {
    throw new ApiError("API_RESPONSE_INVALID", response.status, "API_RESPONSE_INVALID");
  }
  return body.data;
}

export async function apiGetWithStatus<T>(path: string): Promise<{ data: T; status: number }> {
  const response = await fetch(path, {
    headers: { Accept: "application/json", Authorization: `Bearer ${getToken() || ""}` },
  });
  const body = (await response.json().catch(() => null)) as Envelope<T> | null;
  if (!response.ok && response.status !== 202) {
    const code = body?.error?.code || `HTTP_${response.status}`;
    const message = body?.error?.message || code;
    throw new ApiError(message, response.status, code);
  }
  if (!body || !("data" in body)) {
    throw new ApiError("API_RESPONSE_INVALID", response.status, "API_RESPONSE_INVALID");
  }
  return { data: body.data, status: response.status };
}

export function apiGet<T>(path: string): Promise<T> {
  return apiRequest<T>(path);
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

export function apiDelete<T>(path: string): Promise<T> {
  return apiRequest<T>(path, { method: "DELETE" });
}
