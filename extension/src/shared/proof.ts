// How a Motwi proves it's the one a mod paired with, before the mod's token goes to it. Anyone
// can get a trycloudflare.com address, so a "new link" could be a trap. Only the real Motwi has
// the token's hash, and the address is in the answer, so another site can't relay it.
// The other half is proof_for in core/src/motwi/api/mods.py.

const enc = new TextEncoder();

function hex(bytes: ArrayBuffer | Uint8Array): string {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function newNonce(): string {
  return hex(crypto.getRandomValues(new Uint8Array(16)));
}

export async function expectedProof(token: string, origin: string, nonce: string): Promise<string> {
  const tokenHash = hex(await crypto.subtle.digest("SHA-256", enc.encode(token)));
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(tokenHash),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, enc.encode(`${origin}|${nonce}`)));
}
