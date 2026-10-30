// Shows how pairing went. The extension's bridge script reads the code from this page's
// address and reports back; this page never talks to the API itself.

const NO_ANSWER_MS = 2500;
const sections = ["working", "done", "missing", "failed", "nolink"];

function show(id) {
  for (const s of sections) document.getElementById(s).hidden = s !== id;
}

let answered = false;

window.addEventListener("message", (e) => {
  if (e.source !== window || e.origin !== location.origin) return;
  if (e.data?.source !== "motwi-extension") return;
  answered = true;
  const { result } = e.data;
  if (result.ok === "here") return;
  if (result.ok) {
    history.replaceState(null, "", location.pathname); // the code is used up
    if (result.channel) {
      const link = document.getElementById("chat-link");
      link.href = `https://www.twitch.tv/popout/${encodeURIComponent(result.channel)}/chat`;
      document.getElementById("chat").hidden = false;
    }
    show("done");
  } else {
    document.getElementById("reason").textContent = result.error;
    show("failed");
  }
});

if (!/[#&]c=/.test(location.hash)) {
  show("nolink");
} else {
  setTimeout(() => {
    if (!answered) show("missing");
  }, NO_ANSWER_MS);
}
