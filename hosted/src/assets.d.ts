// Files bundled into the Worker (wrangler.jsonc `rules`): the kiosk page and
// its icons (W-849), served with no Container to wake.
declare module "*.html" { const text: string; export default text; }
declare module "*.png" { const data: ArrayBuffer; export default data; }
