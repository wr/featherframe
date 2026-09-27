// featherframe.app's Worker: www goes to the apex; everything else is dist/.
import HELP from './help.json' with { type: 'json' };

interface Env { ASSETS: { fetch(request: Request): Promise<Response> } }

/** GitHub's anchor for a heading: lower case, punctuation dropped, spaces
 * to hyphens. check_copy.py --wiki holds the headings to the wiki. */
export function slug(heading: string): string {
  return heading.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').trim().replace(/\s/g, '-');
}

/** Where featherframe.app/help/<topic> sends an owner (site/src/help.json):
 * the card in the box and the webapp print these, so the pages behind them
 * can move. Null when the path is not under /help. */
export function helpTarget(pathname: string): string | null {
  const m = pathname.match(/^\/help(?:\/([^/]*))?\/?$/i);
  if (!m) return null;
  const topics: Record<string, { page: string; heading?: string }> = HELP.topics;
  const t = topics[(m[1] || '').toLowerCase()] ?? topics[''];
  return `${HELP.wiki}/${t.page}${t.heading ? `#${slug(t.heading)}` : ''}`;
}

export default {
  fetch(request: Request, env: Env): Promise<Response> | Response {
    const url = new URL(request.url);
    // plain http: the same page over https (a link pasted as http:// unfurls from there)
    if (url.protocol === 'http:' && (url.hostname === 'featherframe.app' || url.hostname.endsWith('.featherframe.app'))) {
      // (www too, straight to the apex: one hop)
      if (url.hostname === 'www.featherframe.app') url.hostname = 'featherframe.app';
      url.protocol = 'https:';
      return Response.redirect(url.toString(), 301);
    }
    if (url.hostname === 'www.featherframe.app') {
      url.hostname = 'featherframe.app';
      return Response.redirect(url.toString(), 301);
    }
    const help = helpTarget(url.pathname);
    if (help) return Response.redirect(help, 302);
    // the icon crawlers ask for by convention
    if (url.pathname === '/favicon.ico') {
      url.pathname = '/favicon.png';
      return env.ASSETS.fetch(new Request(url.toString(), request));
    }
    return env.ASSETS.fetch(request);
  },
};
