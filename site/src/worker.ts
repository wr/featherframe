// featherframe.app's Worker: www goes to the apex; everything else is dist/.
interface Env { ASSETS: { fetch(request: Request): Promise<Response> } }

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
    // the icon crawlers ask for by convention
    if (url.pathname === '/favicon.ico') {
      url.pathname = '/favicon.png';
      return env.ASSETS.fetch(new Request(url.toString(), request));
    }
    return env.ASSETS.fetch(request);
  },
};
