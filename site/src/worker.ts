// featherframe.app's Worker: www goes to the apex; everything else is dist/.
interface Env { ASSETS: { fetch(request: Request): Promise<Response> } }

export default {
  fetch(request: Request, env: Env): Promise<Response> | Response {
    const url = new URL(request.url);
    if (url.hostname === 'www.featherframe.app') {
      url.hostname = 'featherframe.app';
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
