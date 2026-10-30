// The bridge on Motwi's pair.html: hands the one-time code to the worker and tells the page
// how it went. The page knows nothing else about the extension, and never sees the token.

import type { PairResult, Request } from "../shared/protocol";

function tell(result: PairResult | { ok: "here" }): void {
  window.postMessage({ source: "motwi-extension", result }, location.origin);
}

const match = /[#&]c=([^&]+)/.exec(location.hash);
tell({ ok: "here" });
if (match) {
  const request: Request = { type: "pair", code: decodeURIComponent(match[1]) };
  chrome.runtime
    .sendMessage(request)
    .then((result: PairResult) => tell(result))
    .catch(() => tell({ ok: false, error: "The extension didn't answer. Reload this page." }));
}
