// @ts-nocheck: node:fs has no types under the Worker's tsconfig.
// The loading page takes the webapp's theme and colours (W-1075), so nothing
// changes when the webapp replaces it: held to server/templates/index.html.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { loadingPage } from "../src/loading";

const INDEX = readFileSync(new URL("../../server/templates/index.html", import.meta.url), "utf8");

// The value of a token in the first rule of index.html's that `opening` starts.
function token(opening: string, name: string): string {
  const at = INDEX.indexOf(opening);
  expect(at).toBeGreaterThan(-1);
  const m = new RegExp(`--${name}:(#[0-9a-f]{6})`).exec(INDEX.slice(at));
  return m![1];
}

describe("the loading page", () => {
  it("reads the theme the webapp's footer keeps, before it paints", async () => {
    const html = await loadingPage().text();
    const pick = /<script>\(function\(\)\{try\{var t=localStorage\.getItem\('ff-theme'\)[^<]*<\/script>/.exec(INDEX)![0];
    expect(html).toContain(pick);
    expect(html.indexOf(pick)).toBeLessThan(html.indexOf("<style>"));
    expect(html).toContain(':root:not([data-theme="light"])');
    expect(html).toContain(':root[data-theme="dark"]');
  });

  it("is drawn in the webapp's own field and card colours, light and dark", async () => {
    const html = await loadingPage().text();
    const light = { bg: token(":root {", "bg"), surface: token(":root {", "surface") };
    const dark = { bg: token(':root[data-theme="dark"] {', "bg"), surface: token(':root[data-theme="dark"] {', "surface") };
    expect(html).toContain(`:root { --bg:${light.bg}; --block:${light.surface};`);
    expect(html).toContain(`:root[data-theme="dark"] { --bg:${dark.bg}; --block:${dark.surface};`);
    expect(html).toContain(`:root:not([data-theme="light"]) { --bg:${dark.bg}; --block:${dark.surface};`);
  });
});
