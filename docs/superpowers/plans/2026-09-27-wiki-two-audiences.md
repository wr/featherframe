# The wiki for owners and makers: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give owners of a ready-made frame their own way into the wiki (set up, the frame, troubleshooting by symptom), reached through `featherframe.app/help/<topic>`, without duplicating what makers read.

**Architecture:** One wiki, every fact on one page. A JSON table (`site/src/help.json`) maps help topics to wiki pages and headings; the site's Worker redirects `/help/*` with it, and `check_copy.py --wiki` holds the wiki to it. Removing a frame on Featherframe Cloud frees its kit, so a frame can be handed on.

**Tech Stack:** Cloudflare Worker (TypeScript, `node --test`), the hosted Worker (TypeScript, vitest on node:sqlite), Python 3.9+ (`check_copy.py`, pytest), GitHub wiki (Markdown).

**Spec:** `docs/superpowers/specs/2026-09-27-wiki-two-audiences-design.md`

## Global constraints

- Owner-facing words follow docs/STYLE.md: Featherframe Cloud and self-hosted, never "hosted"; illustration, not plate; plain headings, never questions; American spelling (color, gray).
- Wiki pages: no emoji (Yes/No in tables), numbered steps, bold UI names, "In your Featherframe webapp, go to **X**", multi-line code blocks, "Next: [Page](Page)." footers, under about 1000 words.
- Owner pages say "a USB-C cable and power adapter", never that one is included. Maker pages assume nothing was bought or given.
- No setup codes anywhere (W-892).
- Help redirects are 302s.
- The help table's headings are the exact wiki headings below; changing one means changing both.

## The wiki's headings the table names

| Page | Headings (exact) |
|---|---|
| Set-up-your-frame | `## You need`, `## 1. Plug it in`, `## 2. Connect it to Wi-Fi`, `## 3. Scan the code`, `## 4. Fill in the setup page`, `## 5. Check your email`, `## 6. Hang it` |
| Your-frame | `## Buttons`, `## The light`, `## What the screen shows`, `## When the picture changes` |
| Troubleshooting | `## The frame` (`### Nothing on the screen`, `### The Wi-Fi setup page doesn't open`, `### The frame can't join your Wi-Fi`, `### Can't reach server`, `### The code expired`, `### The picture doesn't change`), `## Your account` (`### The sign-in email doesn't arrive`, `### You set up with the wrong email`), `## Detection sources` (as today), `## Move, give away, or reset a frame` (`### Change Wi-Fi`, `### Give the frame away`, `### Forget Wi-Fi`, `### Reset everything`), `## Your own server`, `## Get help` |
| Settings | `## Frames`, `## Settings` (`### General`, `### Detection source`, `### Illustrations`, `### Collage`, `### AI image generation`, `### Generated illustrations and collages`) |

---

### Task 1: The help table and the site's redirects

**Files:**
- Create: `site/src/help.json`
- Modify: `site/src/worker.ts`
- Test: `site/test/worker.test.ts`

**Interfaces:**
- Produces: `site/src/help.json` shaped `{"wiki": string, "topics": {[topic: string]: {"page": string, "heading"?: string}}}`; the empty topic `""` is `/help` itself. `helpTarget(pathname: string): string | null` exported from `worker.ts` (null when the path is not under `/help`). `slug(heading: string): string`, GitHub's anchor for a heading.

- [ ] **Step 1: Write the table**

```json
{
  "wiki": "https://github.com/wr/featherframe/wiki",
  "topics": {
    "": { "page": "Home" },
    "setup": { "page": "Set-up-your-frame" },
    "frame": { "page": "Your-frame" },
    "buttons": { "page": "Your-frame", "heading": "Buttons" },
    "light": { "page": "Your-frame", "heading": "The light" },
    "quiet-hours": { "page": "Your-frame", "heading": "When the picture changes" },
    "troubleshooting": { "page": "Troubleshooting" },
    "wifi": { "page": "Troubleshooting", "heading": "The frame can't join your Wi-Fi" },
    "account": { "page": "Troubleshooting", "heading": "Your account" },
    "move": { "page": "Troubleshooting", "heading": "Move, give away, or reset a frame" },
    "source-problems": { "page": "Troubleshooting", "heading": "Detection sources" },
    "settings": { "page": "Settings" },
    "collage": { "page": "Settings", "heading": "Collage" },
    "add": { "page": "Add-a-frame" },
    "detection-sources": { "page": "Detection-sources" },
    "birdweather": { "page": "Detection-sources", "heading": "BirdWeather" },
    "birdnet-pi": { "page": "Detection-sources", "heading": "BirdNET-Pi" },
    "birdnet-go": { "page": "Detection-sources", "heading": "BirdNET-Go" },
    "screens": { "page": "Other-screens" },
    "ai": { "page": "AI-illustrations" },
    "build": { "page": "Quickstart" }
  }
}
```

- [ ] **Step 2: Write the failing tests** (append to `site/test/worker.test.ts`)

```ts
import { helpTarget, slug } from '../src/worker.ts';

const WIKI = 'https://github.com/wr/featherframe/wiki';

test('/help goes to the wiki, a topic to its page and heading', async () => {
  for (const [path, to] of [
    ['/help', `${WIKI}/Home`],
    ['/help/', `${WIKI}/Home`],
    ['/help/setup', `${WIKI}/Set-up-your-frame`],
    ['/HELP/Setup/', `${WIKI}/Set-up-your-frame`],
    ['/help/wifi', `${WIKI}/Troubleshooting#the-frame-cant-join-your-wi-fi`],
    ['/help/move', `${WIKI}/Troubleshooting#move-give-away-or-reset-a-frame`],
    ['/help/birdnet-go', `${WIKI}/Detection-sources#birdnet-go`],
  ]) {
    const res = await worker.fetch(new Request(`https://featherframe.app${path}`), env);
    assert.equal(res.status, 302, path);
    assert.equal(res.headers.get('Location'), to, path);
  }
});

test('an unknown topic goes to the wiki\'s home, never a 404', async () => {
  assert.equal(helpTarget('/help/no-such-thing'), `${WIKI}/Home`);
  assert.equal(helpTarget('/helpful'), null);
  assert.equal(helpTarget('/'), null);
});

test('anchors are GitHub\'s', () => {
  assert.equal(slug("The frame can't join your Wi-Fi"), 'the-frame-cant-join-your-wi-fi');
  assert.equal(slug('Move, give away, or reset a frame'), 'move-give-away-or-reset-a-frame');
  assert.equal(slug('BirdNET-Pi'), 'birdnet-pi');
});
```

- [ ] **Step 3: Run them and see them fail**

Run: `cd site && node --test test/worker.test.ts`
Expected: FAIL (`helpTarget` is not exported).

- [ ] **Step 4: Implement** (in `site/src/worker.ts`, above the default export, and a branch in `fetch` after the www redirect)

```ts
import HELP from './help.json' with { type: 'json' };

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
```

and in `fetch`, before the favicon branch:

```ts
    const help = helpTarget(url.pathname);
    if (help) return Response.redirect(help, 302);
```

- [ ] **Step 5: Run the unit tests and the build**

Run: `cd site && npm run build && npm run test:unit`
Expected: PASS, and the build bundles the JSON (esbuild reads import attributes).

- [ ] **Step 6: Commit**

```bash
git add site/src/help.json site/src/worker.ts site/test/worker.test.ts
git commit -m "featherframe.app/help: a table of topics that redirects into the wiki"
```

### Task 2: check_copy holds the wiki to the table, and retires "hosted"

**Files:**
- Modify: `server/scripts/check_copy.py`
- Test: `server/tests/test_copy_style.py`

**Interfaces:**
- Consumes: `site/src/help.json` (Task 1's shape).
- Produces: `check_help_table(wiki: Path, table: Path = HELP_TABLE) -> list[str]`; findings are `help.json: <topic>: <why>`.

- [ ] **Step 1: Write the failing tests** (append to `server/tests/test_copy_style.py`)

```python
import json


def test_hosted_is_retired_but_self_hosted_is_not():
    assert rules("Use the hosted server.", webapp=False) == ["retired"]
    assert rules("Featherframe Cloud, or self-hosted.", webapp=False) == []


def test_the_help_table_names_only_pages_and_headings_the_wiki_has(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "Home.md").write_text("Hello\n")
    (wiki / "Troubleshooting.md").write_text("## The frame\n\n### The frame can't join your Wi-Fi\n")
    table = tmp_path / "help.json"
    table.write_text(json.dumps({"wiki": "https://example.org", "topics": {
        "": {"page": "Home"},
        "wifi": {"page": "Troubleshooting", "heading": "The frame can't join your Wi-Fi"},
        "gone": {"page": "Nowhere"},
        "moved": {"page": "Troubleshooting", "heading": "Wi-Fi"},
    }}))
    found = check_copy.check_help_table(wiki, table)
    assert [f.split(":")[1].strip() for f in found] == ["gone", "moved"]


def test_the_real_help_table_is_well_formed():
    table = json.loads(check_copy.HELP_TABLE.read_text())
    assert "" in table["topics"]
    assert all("page" in t for t in table["topics"].values())
```

- [ ] **Step 2: Run them and see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_copy_style.py -q`
Expected: FAIL (`check_help_table` and `HELP_TABLE` missing; "hosted" not caught).

- [ ] **Step 3: Implement**

In `RETIRED`, add:

```python
    r"(?<!self-)\bhosted\b": "Featherframe Cloud (or self-hosted)",
```

Below `TEMPLATES`:

```python
# featherframe.app/help/<topic>'s table (site/src/help.json): the card in the
# box and the webapp print these, so every page and heading it names must be
# on the wiki.
HELP_TABLE = SERVER.parent / "site" / "src" / "help.json"
```

Beside `check_wiki_page`:

```python
def check_help_table(wiki: Path, table: Path = HELP_TABLE) -> list[str]:
    out = []
    for topic, t in json.loads(table.read_text())["topics"].items():
        page = wiki / f"{t['page']}.md"
        if not page.exists():
            out.append(f"help.json: {topic or '(help)'}: no wiki page {t['page']}")
            continue
        heading = t.get("heading")
        if heading and not re.search(rf"^#{{1,6}} {re.escape(heading)}\s*$", page.read_text(), re.M):
            out.append(f"help.json: {topic}: no heading {heading!r} on {t['page']}")
    return out
```

(`import json` at the top.) In `main`, inside `if args.wiki:`, after the page loop:

```python
        findings += check_help_table(args.wiki)
```

- [ ] **Step 4: Run the copy tests**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_copy_style.py -q`
Expected: PASS (the webapp's own uses of "hosted" are template and script comments, which `visible_lines` blanks).

- [ ] **Step 5: Commit**

```bash
git add server/scripts/check_copy.py server/tests/test_copy_style.py
git commit -m "check_copy: hold the wiki to the help table, and retire \"hosted\""
```

### Task 3: A removed frame frees its kit

**Files:**
- Modify: `hosted/src/setup.ts` (a `releaseFrame` beside `claim`), `hosted/src/household.ts:262-268` (`unpair` calls it)
- Test: `hosted/test/setup.test.ts`

**Interfaces:**
- Produces: `export async function releaseFrame(env: Env, deviceId: string, hid: string): Promise<void>`: deletes the `frames` row for `(deviceId, hid)` and sets the kit's `used_at` and `household_id` to NULL when its `household_id` is `hid`.

- [ ] **Step 1: Write the failing test** (in `describe("setting up", …)`)

```ts
  it("lets a removed kit be set up again by someone new", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    expect((await post(f, { email: "giver@example.com" }, "198.51.100.10")).status).toBe(303);
    const giver = one("SELECT household_id FROM users WHERE email = 'giver@example.com'").household_id;
    await releaseFrame(env, f.device, giver);
    expect(one("SELECT count(*) AS n FROM frames").n).toBe(0);
    // The frame shows a new code; the new owner has no invitation of their own.
    const g = await frameShowing("GHJKMN", f.device);
    expect((await post(g, { email: "friend@example.com" }, "198.51.100.11")).status).toBe(303);
    const friend = one("SELECT household_id FROM users WHERE email = 'friend@example.com'").household_id;
    expect(one("SELECT household_id FROM frames WHERE device_id = ?", f.device).household_id).toBe(friend);
    expect(one("SELECT household_id FROM kits").household_id).toBe(friend);
  });

  it("frees only a kit that was that household's", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at, used_at, household_id) VALUES (?, ?, 'ee03', ?, ?, 'h1')")
      .run(f.device, f.keyHash, NOW, NOW);
    await releaseFrame(env, f.device, "h2");
    expect(one("SELECT household_id FROM kits").household_id).toBe("h1");
  });
```

Add `releaseFrame` to the `../src/setup` import.

- [ ] **Step 2: Run it and see it fail**

Run: `cd hosted && npx vitest run test/setup.test.ts`
Expected: FAIL (`releaseFrame` is not exported).

- [ ] **Step 3: Implement** (in `hosted/src/setup.ts`, after `claim`)

```ts
/** The inverse of claim: a frame removed on the page leaves household `hid`,
 * and a kit it was set up with is an invitation again, so the frame can be
 * handed on and set up by someone new. */
export async function releaseFrame(env: Env, deviceId: string, hid: string): Promise<void> {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM frames WHERE device_id = ? AND household_id = ?").bind(deviceId, hid),
    env.DB.prepare("UPDATE kits SET used_at = NULL, household_id = NULL WHERE device_id = ? AND household_id = ?")
      .bind(deviceId, hid),
  ]);
}
```

In `hosted/src/household.ts`, `unpair` becomes:

```ts
  /** A frame removed on the page is no longer this household's: the registry
   * lets it go, so its next ask is shown a new pairing code rather than kept
   * waiting here for an add that will not come, and its kit can be set up
   * again by whoever has it next. */
  async unpair(deviceId: string): Promise<void> {
    await releaseFrame(this.env, deviceId, this.meta("hid")!);
  }
```

with `import { releaseFrame } from "./setup";` (check the file's existing import style and cycles: `setup.ts` must not import `household.ts`).

- [ ] **Step 4: Run the hosted tests and the type check**

Run: `cd hosted && npx vitest run && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hosted/src/setup.ts hosted/src/household.ts hosted/test/setup.test.ts
git commit -m "Cloud: a removed frame frees its kit, so it can be given away"
```

### Task 4: The webapp links owners through /help; STYLE and AGENTS say so

**Files:**
- Modify: `server/templates/index.html:1318, 1793, 1976, 3837`, `docs/STYLE.md` (README and wiki section), `AGENTS.md` (Writing section)
- Test: `server/tests/test_frames_page.py:470`, `server/tests/test_no_key_page.py:37`, `server/tests/test_settings_cull.py:86`

- [ ] **Step 1: Update the three tests first**

- `test_frames_page.py:470`: `'href="https://featherframe.app/help/build"' in head and ">DIY instructions<" in head`
- `test_no_key_page.py:37` and `test_settings_cull.py:86`: `"featherframe.app/help/ai" in html and ">Learn</a>" in html`

- [ ] **Step 2: Run them and see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_frames_page.py tests/test_no_key_page.py tests/test_settings_cull.py -q`
Expected: FAIL.

- [ ] **Step 3: Change the links**

- 1318 *DIY instructions*: `https://featherframe.app/help/build`
- 1793 *Learn*: `https://featherframe.app/help/ai`
- 1976 footer: the link text *Hardware guide* becomes *Help*, to `https://featherframe.app/help` (Home has both front doors).
- 3837 `TROUBLE`: `https://featherframe.app/help/source-problems`

- [ ] **Step 4: STYLE.md and AGENTS.md** (verbatim)

docs/STYLE.md, in *README and wiki*, a new bullet:

> - Link an owner to help through `https://featherframe.app/help/<topic>` (the table in `site/src/help.json`), never to a wiki page directly: the card in the box and the webapp print these, and the pages behind them can move. A new topic is a line in that table; `check_copy.py --wiki` checks every page and heading it names. The README links the wiki directly: its readers are makers on GitHub.

AGENTS.md, at the end of *Writing*:

> Link owners to help through `featherframe.app/help/<topic>` (`site/src/help.json`), never to the wiki directly (docs/STYLE.md).

- [ ] **Step 5: Run the server suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/templates/index.html server/tests docs/STYLE.md AGENTS.md
git commit -m "Webapp: help links go through featherframe.app/help"
```

### Task 5: The wiki

**Files (wiki checkout):** create `Set-up-your-frame.md`, `Your-frame.md`; rewrite `Troubleshooting.md`, `_Sidebar.md`, `Home.md`; edit `Settings.md`, `Add-a-frame.md`, `Detection-sources.md`, `Other-screens.md`, `Quickstart.md`, `Build-the-frame.md`, `Status-LED.md`, `Flash-the-frame.md`, `Install-the-server.md`, `Hardware-frames.md`.

Headings exactly as in the table above. Facts, from the code:

- **Set up your frame** (first frame and the account only). You need: Wi-Fi on 2.4 GHz, a phone, a USB-C cable and power adapter. (1) Plug it in; the screen shows the Wi-Fi setup steps and the light breathes blue. (2) On the phone, join **Featherframe-Setup**; a page opens; choose your Wi-Fi and enter its password; if no page opens, open `http://192.168.4.1`; the frame says **Connecting**. (3) The frame shows a six-letter code beside a QR code; scan it with the phone's camera, or go to cloud.featherframe.app and enter the code under **Set up a new frame**. (4) Enter your email; choose a detection source: **A BirdWeather station near me** (the page finds stations near the phone's location, or a ZIP code or town you type), **My BirdNET-Pi**, or **My BirdNET-Go** (connected afterwards: [Detection sources](Detection-sources)); click **Set up frame**. You're signed in on the phone. (5) Two emails: **Welcome to Featherframe!**, and one asking you to confirm your email; the confirm link works for 7 days, and confirming lets you sign in on other devices. (6) Hang it in portrait; with a BirdWeather station the first picture appears within about a minute; the EE02 takes about 15 seconds to change. A note: a gift needs nothing from the person who gave it. Already have an account: see [Add a frame](Add-a-frame). Next: [Your frame](Your-frame).
- **Your frame.** Buttons table (from Build-the-frame; KEY2 held while plugging in "forgets Wi-Fi", not "erase all settings"; the switch only matters with a battery, so it is left off here). The light: the Status-LED colours table, amber = "Showing its setup code (Featherframe Cloud), or waiting to be added (self-hosted)". What the screen shows, a table in the order an owner meets them: the Wi-Fi setup steps (the third step, the server address, is for a self-hosted server; on Featherframe Cloud there is nothing to fill in), **Connecting**, the setup code, **No detections yet** with *Listening since…*, **Detection source unreachable**, **Can't reach Wi-Fi** / **Can't reach server** with *Trying again in 1 minute* (then 5, then 15), a small mark in the bottom corner (no check-in for 30 minutes; it clears itself), the button messages **Loading image**, **Loading collage**, **Already up-to-date**, **Join Featherframe-Setup**, and, self-hosted only, **Add this frame on the Featherframe webapp**. When the picture changes: the moved "doesn't show a new picture" list (quiet hours show the collage; a new species waits for a second detection; the first species of the day is held a while), the Refresh / Manual override tip, and the EE02's 15-second change with its flashing.
- **Troubleshooting.** As the headings list. *Nothing on the screen*: check the power; the light is off with no power, white while starting; e-paper keeps its last picture with the power off, so an unchanged picture is not proof of power. *The Wi-Fi setup page doesn't open*: stay on Featherframe-Setup even if the phone says it has no internet, then open `http://192.168.4.1`. *The frame can't join your Wi-Fi*: 2.4 GHz only (turn it on in the router, or a separate 2.4 GHz network), check the password; hold **KEY2** for 3 seconds and join Featherframe-Setup again. *Can't reach server*: the frame retries by itself; check the internet on that network. *The code expired*: a new one appears within seconds. *The picture doesn't change*: a line to [Your frame](Your-frame#when-the-picture-changes). *The sign-in email doesn't arrive*: it comes only to an account's email, or an invited one (the page says the same either way); check spam; links work once, for 15 minutes. *You set up with the wrong email*: while the phone is still signed in (30 days), go to **Settings → General** and change it; a link goes to the new address; otherwise email help. Detection sources: today's section. *Change Wi-Fi*: hold **KEY2** for 3 seconds, join Featherframe-Setup, choose the new network, leave **Server URL** as it is; the setup network closes after 10 minutes if unused. *Give the frame away*: in your Featherframe webapp, click the frame, then **Remove**; it shows a new code; the new owner follows [Set up your frame](Set-up-your-frame). *Forget Wi-Fi*: hold **KEY2** while plugging it in. *Reset everything*: the USB installer's **Erase** ([Flash the frame](Flash-the-frame)). *Your own server*: today's frame items that are self-hosted (mDNS, another server, missing illustrations) and the local database. *Get help*: owners email help@featherframe.app with what the screen shows; makers [open an issue](https://github.com/wr/featherframe/issues/new).
- **Settings.** The *Settings* bullets become `###` sections (headings above). Its line "When a new screen asks to connect…" stays.
- **Add a frame.** Headings *Featherframe Cloud* and *Self-hosted*. Cloud: the frame shows a code; signed in on your phone, scan it and click **Add this frame to your account**; or in the webapp's **Frames** section click **⋯**, then **Pair a frame**, and enter the code; signed out, scanning it and entering your email sends a link that adds it. Makers on Cloud need an email invitation (the waitlist at [featherframe.app](https://featherframe.app)). Self-hosted: as today.
- **Home and _Sidebar:** Your Featherframe (Set up your frame, Your frame, Troubleshooting) · Use (Settings, Add a frame, Detection sources, Other screens, Species and illustrations, AI illustrations, How AI illustrations are made) · Build your own (Quickstart, Kits and screens → `Hardware-frames`, BirdNET hardware → `Hardware-BirdNET`, Build the frame, Status LED, Battery, Flash the frame, Install the server) · About (Why Featherframe, About BirdNET, Sibling projects) · Develop (as today). Home's first line stays; one line under each page.
- **Moves and trims:** Build-the-frame's buttons table → a line to [Your frame](Your-frame#buttons). Status-LED's colours table → a line to [Your frame](Your-frame#the-light). Flash-the-frame's *Change Wi-Fi* → a line to Troubleshooting; its pre-flashed tip → [Set up your frame](Set-up-your-frame); "On hosted" → "On Featherframe Cloud". Install-the-server's *Hosted* section → *Featherframe Cloud*: one line (a ready-made frame: [Set up your frame](Set-up-your-frame); your own kit: join the waitlist). Detection-sources' table column *Hosted* → *Featherframe Cloud*. Quickstart: "Hosted" → Featherframe Cloud, the Wells Workshop line → [Set up your frame](Set-up-your-frame), nothing assumes a bought kit. Hardware-frames: keep; its "buy one ready-made" line stays. Other-screens: add a Featherframe Cloud line (the tablet page at `https://cloud.featherframe.app/view`, TRMNL server `https://cloud.featherframe.app`; each shows a code to pair, as a frame does), verified against `hosted/src/viewers.ts` before writing.

- [ ] **Step 1: Write the pages** in the scratchpad wiki checkout.
- [ ] **Step 2: Check them**

Run: `cd server && ./.venv/bin/python scripts/check_copy.py --wiki <wiki checkout>`
Expected: no findings (Task 2 must be in place). Also: `wc -w` each page (under about 1000), and every `](Page)` and `](Page#anchor)` link resolves to a page and heading in the checkout.

- [ ] **Step 3: Commit in the wiki checkout** (not pushed yet).

### Task 6: Ship

- [ ] **Step 1:** Push the wiki (`git push` in the checkout; the wiki has no PR flow).
- [ ] **Step 2:** Deploy the site from the branch: `cd site && npm test && npm run deploy`. Check `curl -sI https://featherframe.app/help/wifi` answers 302 to the wiki's anchor, and `/help` to Home.
- [ ] **Step 3:** Push the branch, open the PR (`Refs W-614`), attach it to W-614, wait for CI, squash-merge.
- [ ] **Step 4:** Deploy Featherframe Cloud from an up-to-date main: `cd hosted && npx wrangler deploy` (Docker running). No migration.
- [ ] **Step 5:** Linear: a comment on W-614 with the pages and the PR; move to Done once merged and deployed.
