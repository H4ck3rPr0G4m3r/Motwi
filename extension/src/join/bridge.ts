// Runs on a Motwi join page: hands the link to the worker, which carries on in the extension's
// own page. The site never sees the Twitch sign-in or any token.

import type { Request } from "../shared/protocol";

const match = /[#&]c=([^&]+)/.exec(location.hash);
const request: Request = { type: "join-link", code: match ? decodeURIComponent(match[1]) : null };
void chrome.runtime.sendMessage(request);
