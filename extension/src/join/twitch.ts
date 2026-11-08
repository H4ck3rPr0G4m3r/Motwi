// A mod's Twitch sign-in. It only has to prove who they are, so it asks for no permissions at
// all, and the token is revoked as soon as Motwi has read it. The extension runs this itself,
// so a fake invite can't ask for more than this.

const ID_BASE = "https://id.twitch.tv/oauth2";
// Motwi's public Twitch app. Not a secret; see core/src/motwi/twitch/__init__.py.
const CLIENT_ID = "fo0j62netc6u54f0wqf4khruex5exy";
const DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code";

export interface DeviceCode {
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}

function form(fields: Record<string, string>): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields).toString(),
  };
}

export async function startSignIn(): Promise<DeviceCode> {
  const resp = await fetch(`${ID_BASE}/device`, form({ client_id: CLIENT_ID, scopes: "" }));
  if (!resp.ok) throw new Error("Twitch refused the sign-in request. Try again.");
  return resp.json();
}

/** Waits for the mod to approve the code on Twitch, and returns the token. */
export async function waitForSignIn(code: DeviceCode, signal: AbortSignal): Promise<string> {
  const deadline = Date.now() + code.expires_in * 1000;
  let interval = Math.max(1, code.interval);
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, interval * 1000));
    if (signal.aborted) throw new DOMException("cancelled", "AbortError");
    const resp = await fetch(
      `${ID_BASE}/token`,
      form({
        client_id: CLIENT_ID,
        scopes: "",
        device_code: code.device_code,
        grant_type: DEVICE_GRANT,
      }),
    );
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) return data.access_token;
    if (data.message === "slow_down") interval += 5;
    else if (data.message !== "authorization_pending") break;
  }
  throw new Error("The sign-in code expired. Start again.");
}

export async function revoke(token: string): Promise<void> {
  await fetch(`${ID_BASE}/revoke`, form({ client_id: CLIENT_ID, token })).catch(() => {});
}
