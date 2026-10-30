// Which channel a Twitch URL shows chat for. Twitch logins are lowercase in comparisons.

const LOGIN = /^[a-z0-9_]{1,25}$/;

export function channelFromUrl(href: string): string | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  const parts = url.pathname.split("/").filter(Boolean).map((p) => p.toLowerCase());
  let login: string | undefined;
  if (url.hostname === "dashboard.twitch.tv") {
    login = parts[0] === "u" ? parts[1] : undefined;
  } else if (url.hostname === "www.twitch.tv") {
    if (parts[0] === "popout" || parts[0] === "moderator" || parts[0] === "embed") {
      login = parts[1];
    } else {
      login = parts[0];
    }
  }
  return login && LOGIN.test(login) ? login : null;
}
