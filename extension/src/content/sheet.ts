// One stylesheet object shared by the bar's and every mark's shadow root.

import css from "./overlay.css?inline";

let sheet: CSSStyleSheet | null = null;

export function attachSheet(root: ShadowRoot): void {
  if (!sheet) {
    sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
  }
  root.adoptedStyleSheets = [sheet];
}
