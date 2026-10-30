// Calls to the local API. The window's token arrives once in the URL fragment.

const KEY = "motwi.token";
let token = "";

export function initToken(): void {
  const match = /[#&]k=([^&]+)/.exec(location.hash);
  if (match) {
    token = decodeURIComponent(match[1]);
    sessionStorage.setItem(KEY, token);
    history.replaceState(null, "", location.pathname + "#/");
  } else {
    token = sessionStorage.getItem(KEY) ?? "";
  }
}

export function getToken(): string {
  return token;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly field?: string,
  ) {
    super(message);
  }
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`/api${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Motwi's core isn't responding.", 0);
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ApiError(data.error ?? `Request failed (${resp.status})`, resp.status, data.field);
  }
  return data as T;
}

export const get = <T>(path: string) => api<T>("GET", path);
export const post = <T>(path: string, body: unknown = {}) => api<T>("POST", path, body);
export const patch = <T>(path: string, body: unknown) => api<T>("PATCH", path, body);
export const put = <T>(path: string, body: unknown) => api<T>("PUT", path, body);
export const del = <T>(path: string) => api<T>("DELETE", path);

export function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
