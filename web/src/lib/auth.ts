import type { Role } from "@/types/api";

const TOKEN_KEY = "finsight.token";

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function parseRole(token: string): Role | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const normalized = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const json = atob(padded);
    const payload = JSON.parse(json) as { role?: string };
    const role = payload.role;
    if (
      role === "researcher" ||
      role === "reviewer" ||
      role === "publisher" ||
      role === "admin"
    ) {
      return role;
    }
    return null;
  } catch {
    return null;
  }
}

export function parseUsername(token: string): string | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const normalized = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const payload = JSON.parse(atob(padded)) as { sub?: string; username?: string };
    return payload.username || payload.sub || null;
  } catch {
    return null;
  }
}
