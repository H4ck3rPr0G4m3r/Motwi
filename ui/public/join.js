// With the extension installed, its bridge moves this tab to the extension's own page within a
// moment. Still being here after that means it isn't installed.

const NO_ANSWER_MS = 2500;

setTimeout(() => {
  document.getElementById("working").hidden = true;
  document.getElementById("missing").hidden = false;
}, NO_ANSWER_MS);
