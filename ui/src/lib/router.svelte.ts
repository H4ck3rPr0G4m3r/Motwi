// Hash routes, so the core serves a single index.html.

export const ROUTES = ["overview", "rules", "history", "people", "stats", "mods", "settings"] as const;
export type Route = (typeof ROUTES)[number];

function parse(): { route: Route; query: URLSearchParams } {
  const [path, qs] = location.hash.replace(/^#\/?/, "").split("?");
  const route = (ROUTES as readonly string[]).includes(path) ? (path as Route) : "overview";
  return { route, query: new URLSearchParams(qs ?? "") };
}

export const nav = $state(parse());

window.addEventListener("hashchange", () => Object.assign(nav, parse()));

export function go(route: Route, query?: Record<string, string>): void {
  const qs = query ? `?${new URLSearchParams(query)}` : "";
  location.hash = `#/${route}${qs}`;
}
