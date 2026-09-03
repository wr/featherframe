// Featherframe firmware — deliberately dumb e-paper client.
//
// Wake (timer or button) -> connect Wi-Fi -> GET /api/frame with the stored
// ETag -> 304 means nothing changed, go back to sleep; otherwise receive a
// packed framebuffer (the server did ALL image processing) -> push it to the
// panel with a full refresh -> store the new ETag -> deep sleep.
//
// The device knows only Wi-Fi creds + the server URL, provisioned once via a
// captive portal (WiFiManager) and kept in NVS. Everything else is server-side.

#include "driver.h"        // Seeed_GFX board/panel selection — include FIRST
#include "TFT_eSPI.h"      // Seeed_GFX is a TFT_eSPI fork; EPaper lives here

#include <WiFi.h>
#include <HTTPClient.h>
#include <FS.h>            // WebServer.h (via WiFiManager) uses unqualified FS on
using namespace fs;        // arduino-esp32 v3, so pull fs:: into scope before it
#include <WebServer.h>
#include <WiFiManager.h>
#include <Preferences.h>
#include <Update.h>
#include <esp_sleep.h>
#include <esp_task_wdt.h>
#include <esp_ota_ops.h>
#include <driver/rtc_io.h>

#include "ff_config.h"
#include "ff_screens.h"    // baked 1-bit boot/setup panel screens (1404x1872)

// ---- Featherframe Frame (FFF) wire format ----
struct FFFHeader {
  char     magic[4];   // "FFF1"
  uint8_t  version;
  uint8_t  bpp;        // 4 = 16-gray, 1 = 1-bit
  uint16_t width;
  uint16_t height;
  uint8_t  flags;
  uint8_t  reserved[5];
} __attribute__((packed));
static const size_t FFF_HEADER_SIZE = 16;

EPaper epaper;                 // Seeed_GFX display object (combo 511)
Preferences prefs;             // NVS: server URL, wake minutes, last ETag
WiFiManager wm;

char     g_serverUrl[128];
char     g_etag[40];
uint32_t g_wakeMinutes = DEFAULT_WAKE_MINUTES;
char     g_wakeInfo[64] = "";   // "cause=N keys=0xM" — sent as X-Wake-Detail (debug)
char     g_battRaw[48] = "";    // last ADC read: raw counts, first/last sample, eFuse mV
char     g_wakeToken[16] = "";  // stable token ("timer"|"button"|"coldboot") — X-Wake
bool     g_viaPortal = false;   // did this boot go through the setup portal?

// The server URL as typed into the portal is user input: trim it, give it a
// scheme (HTTPClient::begin() rejects a bare host:port), and drop trailing
// slashes — "http://pi:8081/" + "/api/frame" is "//api/frame", which the
// server routes as 404 and the glass reports as "Can't reach server". An
// empty field keeps whatever was there before.
static void normalizeServerUrl(char* url, size_t n, const char* fallback) {
  char tmp[128];
  strlcpy(tmp, url, sizeof(tmp));
  char* s = tmp;
  while (*s == ' ' || *s == '\t') s++;
  size_t len = strlen(s);
  while (len && (s[len - 1] == ' ' || s[len - 1] == '\t' || s[len - 1] == '/')) s[--len] = 0;
  if (!len) { strlcpy(url, fallback, n); return; }
  if (strncmp(s, "http://", 7) != 0 && strncmp(s, "https://", 8) != 0)
    snprintf(url, n, "http://%s", s);
  else
    strlcpy(url, s, n);
}

// Wake cause -> a stable token the server can show without parsing ESP enums.
static const char* wakeToken(esp_sleep_wakeup_cause_t cause) {
  switch (cause) {
    case ESP_SLEEP_WAKEUP_TIMER: return "timer";
    case ESP_SLEEP_WAKEUP_EXT1:  return "button";
    default:                     return "coldboot";   // power-on / reset
  }
}

// ---------------------------------------------------------------- battery
float readBatteryVoltage() {
  pinMode(PIN_BATTERY_ENABLE, OUTPUT);
  digitalWrite(PIN_BATTERY_ENABLE, HIGH);   // enable divider
  delay(10);
  analogReadResolution(12);
  // median-of-several to reject ADC noise
  uint32_t acc = 0, accMv = 0, first = 0, last = 0;
  const int N = 16;
  for (int i = 0; i < N; i++) {
    uint32_t c = analogRead(PIN_BATTERY_ADC);
    if (i == 0) first = c;
    last = c;
    acc += c;
    accMv += analogReadMilliVolts(PIN_BATTERY_ADC);   // eFuse-calibrated; the measurement
    delay(2);
  }
  digitalWrite(PIN_BATTERY_ENABLE, LOW);    // save idle current
  float counts = acc / (float)N;
  float mv = accMv / (float)N;
  // The calibrated millivolt path is the measurement; raw counts are kept in
  // the diagnostic only (see VBAT_DIVIDER in ff_config.h for why).
  float v = (mv / 1000.0f) * VBAT_DIVIDER * VBAT_TRIM;
  snprintf(g_battRaw, sizeof(g_battRaw), "adc=%u first=%u last=%u mv=%u",
           (unsigned)(counts + 0.5f), (unsigned)first, (unsigned)last, (unsigned)(mv + 0.5f));
  // Calibration aid: put a meter on the JST, read this line, then set
  // VBAT_TRIM = V_meter / the printed untrimmed volts.
  Serial.printf("battery ADC: counts=%.1f mv=%.0f -> %.3f V untrimmed, %.3f V | "
                "VBAT_TRIM = V_meter / %.3f\n",
                counts, mv, (mv / 1000.0f) * VBAT_DIVIDER, v, (mv / 1000.0f) * VBAT_DIVIDER);
  return v;
}

int batteryPercent(float v) {
  // Rough 1S Li-ion curve; good enough for a status readout.
  struct { float v; int p; } pts[] = {
    {4.20, 100}, {4.10, 90}, {4.00, 80}, {3.90, 65}, {3.80, 50},
    {3.70, 35}, {3.60, 22}, {3.50, 12}, {3.40, 5}, {3.30, 0}};
  if (v >= pts[0].v) return 100;
  for (int i = 1; i < 10; i++) {
    if (v >= pts[i].v) {
      float span = pts[i-1].v - pts[i].v;
      float frac = (v - pts[i].v) / span;
      return (int)(pts[i].p + frac * (pts[i-1].p - pts[i].p));
    }
  }
  return 0;
}

// ---------------------------------------------------------------- loader anim
// The loading mark on the boot pills / onboarding checklist: three diamonds,
// the solid one sweeping left to right. The connect/download steps run
// blocking on the main task, so a FreeRTOS task pushes the baked frames
// (ff_loader[], tiny pure-black/white tiles) as windowed DU partials — real
// motion through the whole boot, no flash, no main-path changes. g_panelMutex
// serializes every panel touch between this task and the rest of the app.
static SemaphoreHandle_t g_panelMutex;
struct LoaderAnim { volatile bool on; int16_t x, y; const uint8_t* const* frames; };
static LoaderAnim g_loaderAnim = {false, 0, 0, nullptr};

static void panelLock()   { if (g_panelMutex) xSemaphoreTakeRecursive(g_panelMutex, portMAX_DELAY); }
static void panelUnlock() { if (g_panelMutex) xSemaphoreGiveRecursive(g_panelMutex); }
static void pushTile(const uint8_t* tile, int x, int y, int w, int h);

static void loaderTask(void*) {
  int frame = 0;
  for (;;) {
    if (g_loaderAnim.on) {
      panelLock();
      if (g_loaderAnim.on) {     // re-check: a full refresh may have landed
        pushTile(g_loaderAnim.frames[frame], g_loaderAnim.x, g_loaderAnim.y,
                 FF_LOADER_NW, FF_LOADER_NH);
        frame = (frame + 1) % FF_LOADER_FRAMES;
      }
      panelUnlock();
    } else {
      frame = 0;   // next sweep starts from the baked resting state
    }
    vTaskDelay(pdMS_TO_TICKS(FF_LOADER_STEP_MS));
  }
}

// ---------------------------------------------------------------- error states
// Failure presentation (design: Linear W-587). On a boot pill screen the pill
// band is swapped in place — outlined pill + slashed icon for real errors, the
// solid pill for "waiting for the first bird" — with a "Trying again …" line
// beneath. Over a painted plate only a small slashed glyph appears in the
// margin corner, and only past the FF_MARK_* thresholds. All tiles are baked
// pure black/white and pushed as windowed DU partials (no flash). The state
// survives deep sleep in RTC memory.
enum ErrKind { ERRK_WIFI = 0, ERRK_SERVER = 1, ERRK_NOFRAME = 2 };
RTC_DATA_ATTR int16_t  g_failCount = 0;     // consecutive failed cycles
RTC_DATA_ATTR uint16_t g_failMinutes = 0;   // ~minutes since the last success
// Longevity counters (spec §5). RTC-backed: survive deep sleep, reset only on a
// power pull. bootCount ++ every wake; refreshCount ++ on every full panel redraw.
RTC_DATA_ATTR uint32_t g_bootCount = 0;
RTC_DATA_ATTR uint32_t g_refreshCount = 0;
RTC_DATA_ATTR int8_t   g_glassScreen = -1;  // baked screen on the glass; -1 = a plate
RTC_DATA_ATTR uint8_t  g_cornerMark = 0;    // 0 none, 1 wifi, 2 server
RTC_DATA_ATTR int8_t   g_bandKind = -1;     // error band on the glass (-1 none)
RTC_DATA_ATTR int8_t   g_bandStage = -1;    // its retry-line stage
static uint32_t g_lastSuccessMs = 0;        // always-awake model: for g_failMinutes

static void bumpFail() { if (g_failCount < 30000) g_failCount++; }

// The last painted frame body, kept so a cleared toast can restore the band
// it covered byte-for-byte (a windowed DU, no flash, no white scar). Lost
// across deep sleep — the sleep model repaints via an ETag drop instead.
static uint8_t* g_lastFrame = nullptr;

// Dark mode: the server inverts the plates it serves and announces the mode
// via X-FF-Invert; the firmware mirrors it onto everything baked (screens and
// tiles) by flipping every 4bpp nibble (v -> 15-v == byte ^ 0xFF).
bool g_invert = false;
static uint8_t g_tileBuf[FF_MAX_TILE_BYTES];

// Only call while holding the panel mutex — g_tileBuf is shared.
static const uint8_t* maybeInvert(const uint8_t* t, size_t n) {
  if (!g_invert) return t;
  for (size_t i = 0; i < n; i++) g_tileBuf[i] = t[i] ^ 0xFF;
  return g_tileBuf;
}

static void pushTileRaw(const uint8_t* tile, int x, int y, int w, int h) {
  panelLock();
  epaper.wake();
  epaper.tconLoadImage((uint8_t*)tile, x, y, w, h, false);
  epaper.tconDisplayArea(x, y, w, h, 1);        // DU: no flash
  epaper.tconWaitForDisplayReady();
  panelUnlock();
}

static void pushTile(const uint8_t* tile, int x, int y, int w, int h) {
  panelLock();
  epaper.wake();
  tile = maybeInvert(tile, (size_t)(w / 2) * h);
  epaper.tconLoadImage((uint8_t*)tile, x, y, w, h, false);
  epaper.tconDisplayArea(x, y, w, h, 1);        // DU: no flash
  epaper.tconWaitForDisplayReady();
  panelUnlock();
}

// Backoff: 1 -> 5 -> 15 min, capped — deliberately decoupled from the wake
// interval so the baked "Trying again in N minutes" line is always true.
uint32_t retryDelayMinutes() {
  return g_failCount <= 1 ? 1 : g_failCount == 2 ? 5 : 15;
}

void showErrorState(int kind) {
  bool bootPill = (g_glassScreen == FF_SCR_BOOT_WIFI ||
                   g_glassScreen == FF_SCR_BOOT_BIRDNET ||
                   g_glassScreen == FF_SCR_BOOT_DOWNLOAD);
  if (bootPill) {
#if FF_NO_SLEEP
    int stage = 3;                              // polls retry in seconds: "shortly"
#else
    int stage = g_failCount <= 1 ? 0 : g_failCount == 2 ? 1 : 2;
#endif
    if (g_bandKind != kind || g_bandStage != stage) {   // repeated fails: no re-push
      pushTile(ff_err_tiles[kind], FF_ERR_X, FF_ERR_Y, FF_ERR_W, FF_ERR_H);
      pushTile(ff_retry_tiles[stage], FF_RETRY_X, FF_RETRY_Y, FF_RETRY_W, FF_RETRY_H);
      g_bandKind = (int8_t)kind; g_bandStage = (int8_t)stage;
    }
  } else if (g_glassScreen < 0 &&
             g_failCount >= FF_MARK_FAILS && g_failMinutes >= FF_MARK_MINUTES) {
    uint8_t mark = (kind == ERRK_WIFI) ? 1 : 2;
    if (g_cornerMark != mark) {
      pushTile(ff_corner_tiles[mark - 1], FF_CORNER_X, FF_CORNER_Y, FF_CORNER_W, FF_CORNER_H);
      g_cornerMark = mark;
    }
  }
}

// A cycle succeeded: erase the corner mark if one is up (a 304 keeps the
// plate, so the mark needs an explicit wipe) and reset the accounting.
void noteSuccess() {
  if (g_cornerMark) {
    pushTile(ff_corner_tiles[2], FF_CORNER_X, FF_CORNER_Y, FF_CORNER_W, FF_CORNER_H);
    g_cornerMark = 0;
    // The mark's box white-washed a corner of the plate; drop the ETag so the
    // next fetch repaints the whole glass instead of 304-ing over the scar.
    g_etag[0] = 0;
    prefs.putString("etag", "");
  }
  g_failCount = 0;
  g_failMinutes = 0;
  g_lastSuccessMs = millis();
}

// ---------------------------------------------------------------- watchdog
// Whole-cycle watchdog in BOTH power models: a wedged panel busy-wait or a
// stuck socket reboots the board instead of stranding the frame.
static void armWatchdog() {
  esp_task_wdt_config_t cfg = {};
  cfg.timeout_ms = WDT_TIMEOUT_S * 1000;
  cfg.idle_core_mask = 0;
  cfg.trigger_panic = true;
  if (esp_task_wdt_init(&cfg) == ESP_ERR_INVALID_STATE)
    esp_task_wdt_reconfigure(&cfg);             // the Arduino core may arm it first
  esp_task_wdt_add(NULL);
}

// ---------------------------------------------------------------- sleep
void goToSleep(uint32_t minutes) {
  g_loaderAnim.on = false;
  panelLock();               // let an in-flight loader step finish first
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  // update() already put the T-CON to sleep; e-paper holds its image with the
  // rails off, so there's nothing else to power down.

  // Wake on the user buttons (active-low) and on a timer. The internal RTC
  // pull-ups only hold the keys high in deep sleep while the RTC peripheral
  // domain stays powered (esp_sleep.h: "internal pullups don't work when RTC
  // peripherals are powered off") — without it the pins float and ext1
  // ANY_LOW either never fires or fires at once.
  for (gpio_num_t p : {PIN_KEY0, PIN_KEY1, PIN_KEY2}) {
    rtc_gpio_pulldown_dis(p);
    rtc_gpio_pullup_en(p);
  }
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
  esp_sleep_enable_ext1_wakeup(BUTTON_WAKE_MASK, ESP_EXT1_WAKEUP_ANY_LOW);
  if (minutes < FF_MIN_SLEEP_MINUTES) minutes = FF_MIN_SLEEP_MINUTES;   // 0 => wake storm
  if (minutes > FF_MAX_SLEEP_MINUTES) minutes = FF_MAX_SLEEP_MINUTES;
  esp_sleep_enable_timer_wakeup((uint64_t)minutes * 60ULL * 1000000ULL);

  Serial.printf("Sleeping for %u min (or button)\n", minutes);
  Serial.flush();
  esp_deep_sleep_start();
}

// ---------------------------------------------------------------- low battery
// A nearly empty 1S cell can't take a Wi-Fi burst without sagging the 3.3 V
// rail into the brownout detector; the reboot loop that follows runs the pack
// down to its protection cutoff. Decide before the radio starts, with
// hysteresis so a cell resting back up to 3.5 V doesn't flap the frame on and
// off. RTC-backed so the decision survives the long sleep it triggers.
RTC_DATA_ATTR bool g_lowBatt = false;

static bool lowBatteryHold(float vbat) {
  if (vbat < FF_BATT_ABSENT_V) { g_lowBatt = false; return false; }   // no pack (USB-only)
  if (g_lowBatt) { if (vbat >= FF_LOW_BATT_RESUME_V) g_lowBatt = false; }
  else if (vbat < FF_LOW_BATT_V) g_lowBatt = true;
  if (g_lowBatt) Serial.printf("battery low (%.2f V): skipping Wi-Fi, sleeping %d min\n",
                               vbat, FF_LOW_BATT_SLEEP_MIN);
  return g_lowBatt;
}

// ---------------------------------------------------------------- wifi
// Baked panel screens (defined later, near the splash).
void showScreen(int idx);
void showScreenFull(int idx);
void showToast(int t);
void markFirmwareGood();

// Paper/ink restyle for the WiFiManager captive portal — injected into
// <head> after the stock style, so these rules win the cascade (the stock
// sheet still supplies the signal-strength sprite). The swash "Featherframe"
// wordmark is a 3.4 KB WOFF subset of EB Garamond italic (glyphs of the name
// + the hedera, swsh retained) embedded as a data URI — the captive portal
// has no internet, so the face must travel with the page. Kept in PROGMEM.
static const char PORTAL_CSS[] PROGMEM = R"CSS(<style>
@font-face{font-family:'FFSwash';src:url(data:font/woff;base64,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) format('woff');font-style:italic;font-weight:500}
:root{--bg:#efeae0;--card:#fbf9f4;--ink:#20201d;--muted:#6f685c;--accent:#3f5e46;--err:#8a4a3a;--line:#ddd6c8}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;margin:0;padding:28px 18px 60px;line-height:1.5;text-align:center}
.wrap{text-align:left;display:inline-block;width:100%;min-width:260px;max-width:430px}
h1{font-family:'FFSwash',Georgia,serif;font-style:italic;font-weight:500;font-size:2.6rem;text-align:center;margin:.4em 0 0;letter-spacing:.01em;font-feature-settings:'swsh' 1}
h1:after{content:'\2767';display:block;font-size:1.1rem;color:var(--muted);margin-top:10px}
h3{display:none}
h2,label{font-size:.72rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
label{display:block;margin:16px 2px 6px}
div,input,select{box-sizing:border-box}
input,select{background:var(--card);border:1px solid var(--line);border-radius:14px;color:var(--ink);padding:13px 14px;width:100%;font-size:1rem;margin:2px 0}
input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
input[type=radio],input[type=checkbox]{width:auto;accent-color:var(--accent);margin-right:6px}
button,input[type='button'],input[type='submit']{cursor:pointer;border:0;border-radius:999px;background:var(--ink);color:var(--bg);line-height:2.9rem;font-size:1.02rem;font-weight:600;width:100%;margin:6px 0}
button:hover{filter:brightness(1.25)}
button:active{opacity:.5}
button.D{background:transparent;color:var(--err);border:2px solid var(--err);line-height:2.65rem}
form{margin:0}
a{color:var(--ink);font-weight:600;text-decoration:none}
a:hover{color:var(--accent)}
/* network list rows */
.wrap>div>div,.ffnet{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 16px;margin:8px 0}
.wrap>div>div a{font-family:Georgia,'Times New Roman',serif;font-size:1.08rem;display:inline-block;max-width:75%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}
.q{height:16px;margin:2px 0 0;padding:0 5px;text-align:right;min-width:38px;float:right;opacity:.75}
.msg{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--muted);border-radius:0 12px 12px 0;padding:16px 18px;margin:18px 0;color:var(--ink)}
.msg.P{border-left-color:var(--accent)}
.msg.D{border-left-color:var(--err)}
.msg.S{border-left-color:var(--accent)}
dt{font-weight:600}dd{margin:0;padding:0 0 .5em;min-height:12px;color:var(--muted)}
td{vertical-align:top}
hr{border:0;border-top:1px solid var(--line);margin:22px 0}
small{color:var(--muted)}
.h{display:none}:disabled{opacity:.5}
</style>)CSS";

bool ensureWifi(bool openPortal, bool showBoot) {
  // The portal blocks here for its whole session, and WiFiManager extends
  // its own timeout on every captive-portal probe — a phone parked on the
  // hotspot keeps it alive indefinitely, far past WDT_TIMEOUT_S. Setup is
  // user-driven; stand the watchdog down for it and re-arm on the way out.
  esp_task_wdt_delete(NULL);
  WiFi.mode(WIFI_STA);
  wm.setTitle("Featherframe");
  wm.setCustomHeadElement(PORTAL_CSS);
  // WiFiManager keeps the registered pointer forever and never dedupes, and
  // ensureWifi is re-entered from loop()'s KEY2 handler — so the parameter
  // lives in static storage and registers exactly once.
  static WiFiManagerParameter serverParam("server", "Featherframe server URL",
                                          g_serverUrl, sizeof(g_serverUrl));
  static bool paramRegistered = false;
  if (!paramRegistered) {
    wm.addParameter(&serverParam);
    paramRegistered = true;
  } else {
    serverParam.setValue(g_serverUrl, sizeof(g_serverUrl));
  }
  wm.setConfigPortalTimeout(PORTAL_TIMEOUT_S);
  wm.setConnectTimeout(WIFI_CONNECT_TIMEOUT_MS / 1000);

  // With saved credentials a failed connect must NOT fall into the portal — a
  // router blip would otherwise swap the glass to setup steps and burn ten
  // minutes of AP mode per attempt. The portal is for first run (no saved
  // network) and the explicit KEY2-hold only.
  wm.setEnableConfigPortal(openPortal || !wm.getWiFiIsSaved());

  // Panel: the setup steps appear when the captive portal (AP) opens; the
  // moment the user's network is saved and connected, hand straight over to
  // the normal boot flow (one full repaint — the setup layout shares nothing
  // with the boot screens). There is no separate onboarding checklist.
  g_viaPortal = openPortal;
  // With a plate on the glass the portal announces itself as a pill (the art
  // stays; a timed-out portal just clears it). With nothing painted yet, the
  // full setup instructions take the glass.
  wm.setAPCallback([](WiFiManager*) {
    g_viaPortal = true;
    if (g_glassScreen < 0) showToast(FF_TOAST_PORTAL);
    else showScreen(FF_SCR_SETUP);
  });
  wm.setSaveConfigCallback([]() { showScreenFull(FF_SCR_BOOT_WIFI); });

  bool ok;
  if (openPortal) {
    ok = wm.startConfigPortal("Featherframe-Setup");
  } else {
    // Deep-sleep wakes connect silently (showBoot false): the resident plate
    // stays on the glass and a 304 wake never repaints anything.
    if (showBoot) showScreen(FF_SCR_BOOT_WIFI);   // "Connecting to Wi-Fi"
    ok = wm.autoConnect("Featherframe-Setup");
  }
  // First run: no network saved yet — keep the portal open until one is. The
  // loop lives HERE because serverParam is stack-allocated and WiFiManager
  // keeps the registered pointer: ensureWifi must not be re-entered for
  // retries. The watchdog outlasts one portal round and is fed between.
  while (!ok && !wm.getWiFiIsSaved()) {
    esp_task_wdt_reset();
    g_viaPortal = true;
    ok = wm.startConfigPortal("Featherframe-Setup");
  }
  if (ok) {
    // Wi-Fi up: the caller drives the "Connecting to BirdNET…"/"Downloading…"
    // steps next. Persist the (possibly updated, user-typed) server URL.
    char prev[sizeof(g_serverUrl)];
    strlcpy(prev, g_serverUrl, sizeof(prev));
    strlcpy(g_serverUrl, serverParam.getValue(), sizeof(g_serverUrl));
    normalizeServerUrl(g_serverUrl, sizeof(g_serverUrl), prev);
    prefs.putString("server", g_serverUrl);
  }
  bool connected = ok && WiFi.status() == WL_CONNECTED;
  if (connected) markFirmwareGood();   // a build that gets this far is not a brick
  // A dead end (portal timeout, connect failure) can leave a boot screen
  // armed via the save callback; stop the sweep — there is no progress to show.
  if (!connected) g_loaderAnim.on = false;
  esp_task_wdt_add(NULL);
  return connected;
}

// ---------------------------------------------------------------- display
// The server sends the framebuffer already in the panel's NATIVE landscape
// orientation (1872x1404) and in the panel's exact packing:
//   4bpp: 2 px/byte, high nibble = left/even pixel, value 0..15, 0=black 15=white
//   1bpp: 8 px/byte, MSB = left pixel, bit set = white
// EPaper is a TFT_eSprite whose buffer uses that same layout, so we can push the
// whole thing with pushImage() (a per-row memcpy at rotation 0 / even width) and
// then update() for a full refresh — no per-pixel work.
// `retain` keeps a copy for toast-band restore. Only the FETCH path may set
// it: it runs after freeScreenBuffers(), where PSRAM has room. Retaining on a
// baked entry screen (splash/setup) adds 1.3 MB while the three boot buffers
// are still resident, and the IT8951 full write then can't find a contiguous
// mirror block — it drops the frame SILENTLY (gotcha #2 in the handoff).
// Returns false (and leaves the glass untouched) if the container isn't a
// frame this panel can take: wrong magic/version/bpp, not the native
// 1872x1404 (e.g. the server's panel_rotation set to 0/180, which emits
// portrait), or a body that doesn't match the header. pushImage would clip a
// wrong-sized image into garbage rather than fault, so the check lives here.
bool displayFrame(const uint8_t* data, size_t len, bool retain = false) {
  if (len < FFF_HEADER_SIZE) return false;
  FFFHeader h;
  memcpy(&h, data, FFF_HEADER_SIZE);
  if (memcmp(h.magic, "FFF1", 4) != 0) { Serial.println("bad frame magic"); return false; }
  if (h.version != 1 || (h.bpp != 4 && h.bpp != 1)) {
    Serial.printf("bad frame: version=%d bpp=%d\n", h.version, h.bpp);
    return false;
  }
  if (h.width != FF_NATIVE_W || h.height != FF_NATIVE_H) {
    Serial.printf("bad frame: %dx%d, panel is %dx%d native (server panel_rotation?)\n",
                  h.width, h.height, FF_NATIVE_W, FF_NATIVE_H);
    return false;
  }
  const size_t stride = (h.bpp == 4) ? (h.width + 1) / 2 : (h.width + 7) / 8;
  if (len - FFF_HEADER_SIZE < stride * h.height) {
    Serial.printf("bad frame: body %u < %u\n", (unsigned)(len - FFF_HEADER_SIZE),
                  (unsigned)(stride * h.height));
    return false;
  }
  g_loaderAnim.on = false;      // a full refresh replaces any loading screen
  panelLock();

  const uint16_t* body = (const uint16_t*)(data + FFF_HEADER_SIZE);
  const int w = h.width, hh = h.height;   // native: 1872 x 1404
  Serial.printf("frame %dx%d bpp=%d\n", w, hh, h.bpp);

  if (h.bpp == 4) {
    epaper.initGrayMode(GRAY_LEVEL16);       // reallocates the 4bpp gray sprite
    epaper.fillSprite(TFT_GRAY_15);          // white ground (buffer was realloc'd)
    epaper.pushImage(0, 0, w, hh, (uint16_t*)body);
  } else {                                  // 1-bit fallback (default sprite depth)
    epaper.fillScreen(TFT_WHITE);
    epaper.pushImage(0, 0, w, hh, (uint16_t*)body);
  }
  // The mirror-buffer failure mode is silent; make the headroom visible.
  Serial.printf("psram largest free %u\n",
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM));
  epaper.update();                          // full refresh; brackets its own power
  g_refreshCount++;                         // panel-wear tally (spec §5)
  if (retain && h.bpp == 4) {               // a 1-bit body is smaller than this copy
    if (!g_lastFrame) g_lastFrame = (uint8_t*)ps_malloc(FF_SCREEN_BYTES);
    if (g_lastFrame) memcpy(g_lastFrame, body, FF_SCREEN_BYTES);
  }
  panelUnlock();
  g_glassScreen = -1;                       // a full paint owns the whole glass
  g_cornerMark = 0;
  g_bandKind = g_bandStage = -1;
  Serial.println("panel updated");
  return true;
}

// ---------------------------------------------------------------- press ack
// Button-press acknowledgement when the glass doesn't change. The XIAO's user
// LED sits on the driver board, hidden behind a wall-mounted frame — useless as
// feedback. Instead we drop a small pill toast at the bottom of the frame with a
// fast partial refresh, so a press always shows a visible result.
// 2 blinks = checked, nothing new. 4 blinks = couldn't do it (no Wi-Fi/server).
void ackBlink(int blinks) {
  pinMode(LED_BUILTIN, OUTPUT);
  for (int i = 0; i < blinks; i++) {
    digitalWrite(LED_BUILTIN, LOW);   // active low on the XIAO ESP32-S3
    delay(140);
    digitalWrite(LED_BUILTIN, HIGH);
    delay(140);
  }
}

// Button toasts are baked pills (ff_toast_tiles, same design language as the
// boot pills) pushed as windowed DU partials over the plate's bottom margin.
// In-progress toasts carry the loading mark and the sweep task animates them;
// FF_TOAST_BLANK wipes the band (its white box lands in the plate's light
// margin, where it blends — same trade the old GFX toast made).
struct ToastState { bool active; uint32_t shownAt; };
static ToastState g_toast = {false, 0};

void showToast(int t) {
  if (t < 0 || t >= FF_TOAST_COUNT) return;
  // A baked screen's own pills carry its state; a toast there would sit on
  // the error band's columns and its later blank-clear would punch a white
  // hole the band-dedup latch never repairs.
  if (g_glassScreen >= 0) return;
  g_loaderAnim.on = false;
  pushTile(ff_toast_tiles[t], FF_TOAST_X, FF_TOAST_Y, FF_TOAST_W, FF_TOAST_H);
  if (t < (int)(sizeof(ff_toast_loader) / sizeof(ff_toast_loader[0]))) {
    const FfLoader& ld = ff_toast_loader[t];
    if (ld.x >= 0) {
      g_loaderAnim.x = ld.x; g_loaderAnim.y = ld.y; g_loaderAnim.frames = ld.frames;
      g_loaderAnim.on = true;
    }
  }
  g_toast = {true, millis()};
#if !FF_NO_SLEEP
  // No loop() ever clears this toast; drop the ETag so the next wake's fetch
  // returns 200 and repaints the plate over it.
  g_etag[0] = 0;
  prefs.putString("etag", "");
#endif
  Serial.printf("toast: %d\n", t);
}

// Clear the toast: restore the band the pill covered from the retained frame
// (byte-for-byte — the plate's caption comes back intact). The white blank is
// only the fallback when no frame copy exists.
void clearToast() {
  if (!g_toast.active) return;
  if (g_glassScreen >= 0) {         // a baked paint already covered the toast
    g_toast.active = false;
    return;
  }
  g_loaderAnim.on = false;
  if (g_lastFrame && g_glassScreen < 0) {
    panelLock();                              // g_tileBuf is shared under the lock
    const int nx = FF_NATIVE_W - FF_TOAST_X - FF_TOAST_W;
    for (int r = 0; r < FF_TOAST_H; r++)
      memcpy(g_tileBuf + r * (FF_TOAST_W / 2),
             g_lastFrame + (uint32_t)(FF_TOAST_Y + r) * (FF_NATIVE_W / 2) + nx / 2,
             FF_TOAST_W / 2);
    pushTileRaw(g_tileBuf, FF_TOAST_X, FF_TOAST_Y, FF_TOAST_W, FF_TOAST_H);
    panelUnlock();
  } else {
    pushTile(ff_toast_tiles[FF_TOAST_BLANK], FF_TOAST_X, FF_TOAST_Y, FF_TOAST_W, FF_TOAST_H);
  }
  g_toast.active = false;
  Serial.println("toast cleared");
}

// ---------------------------------------------------------------- screens
// The boot + first-time-setup art (splash, "Connecting…", setup steps) is baked
// at full 1404x1872 into ff_screens.h and drawn here.
// Screens are baked as 16-level gray in native 1872x1404 (the panel's 1-bit path
// can't address the full width — the bottom quarter comes up black — but the gray
// load path can). An entry screen (splash/setup) does a full gray refresh via
// displayFrame; every following screen repaints ONLY the region that changed, as a
// windowed gray update, so the birdhouse never flashes. Native 4bpp: 2 px/byte,
// stride 936, so a byte column = 2 px.
#define FF_GRAY_STRIDE (FF_NATIVE_W / 2)           // 936 bytes/row

// The three ~1.3 MB screen buffers live at file scope so they can be released before
// the plate loads: the IT8951 full-image write mallocs its own 1.31 MB mirror buffer,
// and if PSRAM is too fragmented for it, it silently drops the frame (the plate never
// appears). freeScreenBuffers() hands that space back once the boot art is done.
static uint8_t* g_scrBuf  = nullptr;               // FFF header + decoded 4bpp body
static uint8_t* g_scrPrev = nullptr;               // last screen's body, for diffing
static uint8_t* g_scrWin  = nullptr;               // extracted window for a partial
static bool     g_scrHavePrev = false;

void freeScreenBuffers() {
  free(g_scrBuf); free(g_scrPrev); free(g_scrWin);
  g_scrBuf = g_scrPrev = g_scrWin = nullptr;
  g_scrHavePrev = false;
}

void showScreen(int idx) {
  if (idx < 0 || idx >= FF_SCR_COUNT) return;
  uint8_t*& buf  = g_scrBuf;                        // aliases onto the file-scope bufs
  uint8_t*& prev = g_scrPrev;
  uint8_t*& win  = g_scrWin;
  bool&     havePrev = g_scrHavePrev;
  const size_t total = FFF_HEADER_SIZE + FF_SCREEN_BYTES;
  if (!buf) {
    // The retained frame copy exists only to restore toast bands over a
    // plate; once boot screens own the glass it is stale, and its 1.3 MB
    // would push the entry screen's full write past the PSRAM mirror cliff
    // (handoff gotcha #2) exactly as splash-time retention once did.
    free(g_lastFrame);
    g_lastFrame = nullptr;
    buf  = (uint8_t*)ps_malloc(total);             // ~1.3 MB each, PSRAM
    prev = (uint8_t*)ps_malloc(FF_SCREEN_BYTES);
    win  = (uint8_t*)ps_malloc(FF_SCREEN_BYTES);
    if (!buf || !prev || !win) { Serial.println("screen: no buffer"); return; }
    FFFHeader h = {};
    memcpy(h.magic, "FFF1", 4);
    h.version = 1; h.bpp = 4; h.width = FF_NATIVE_W; h.height = FF_NATIVE_H;
    memcpy(buf, &h, FFF_HEADER_SIZE);              // header is constant; write once
  }
  uint8_t* body = buf + FFF_HEADER_SIZE;
  ff_unpack(ff_screens[idx].data, ff_screens[idx].len, body);
  if (g_invert)
    for (uint32_t i = 0; i < FF_SCREEN_BYTES; i++) body[i] ^= 0xFF;

  g_loaderAnim.on = false;        // pause the sweep while the glass changes
  panelLock();
  const bool entry = (idx == FF_SCR_SPLASH || idx == FF_SCR_SETUP);
  if (entry || !havePrev) {
    displayFrame(buf, total);                      // full gray refresh (== bird plates)
    Serial.printf("screen %d full\n", idx);
  } else {
    // Repaint each changed region as its own tight window. The birdhouse is static
    // (identical between screens), so a transition only touches the bird box and the
    // pill box — and in native orientation those are separated along X (byte columns),
    // so we band by column: a run of changed columns is one window. DU waveform =
    // non-flashing (the content is high-contrast line art / pills). Byte column = 2 px.
    const int GAP = 24;   // small: keep art contiguous but don't merge the pill with
                          // the erasing footer into one oversized window
    int nwin = 0, c = 0;
    int wmx[8], wny[8], wnw[8], wnh[8], wmode[8];   // deferred display areas
    epaper.wake();
    while (c < FF_GRAY_STRIDE && nwin < 8) {
      bool dirty = false;
      for (int r = 0; r < FF_NATIVE_H && !dirty; r++)
        if (prev[r * FF_GRAY_STRIDE + c] != body[r * FF_GRAY_STRIDE + c]) dirty = true;
      if (!dirty) { c++; continue; }
      int c0 = c, c1 = c, gap = 0, rmin = FF_NATIVE_H, rmax = -1;
      for (int cc = c; cc < FF_GRAY_STRIDE; cc++) {
        bool cd = false;
        for (int r = 0; r < FF_NATIVE_H; r++)
          if (prev[r * FF_GRAY_STRIDE + cc] != body[r * FF_GRAY_STRIDE + cc]) {
            cd = true; if (r < rmin) rmin = r; if (r > rmax) rmax = r;
          }
        if (cd) { c1 = cc; gap = 0; }
        else if (++gap > GAP) break;
      }
      c0 &= ~3; c1 |= 3;                            // align X to 8 px (4 bytes)
      int nx = c0 * 2, nw = (c1 - c0 + 1) * 2, ny = rmin, nh = rmax - rmin + 1;
      for (int r = 0; r < nh; r++)
        memcpy(win + r * (nw / 2), body + (ny + r) * FF_GRAY_STRIDE + c0, nw / 2);
      // Pick the waveform by content. A window that's essentially pure black/white —
      // the pill, the dithered wren-in-hole — refreshes with DU (mode 1), which is fast
      // and does NOT flash. Gray line art (the fly-in bird) needs GC16 (mode 2); DU
      // renders gray too faint. Count mid-gray nibbles; <5% => treat as 1-bit => DU.
      // (GL16/mode 3 isn't in this panel's waveform table — it paints flat gray blocks.)
      long nonbin = 0; const long totpx = (long)nw * nh;
      for (int i = 0, nb = (nw / 2) * nh; i < nb; i++) {
        uint8_t hi = win[i] >> 4, lo = win[i] & 0xF;
        if (hi != 0 && hi != 15) nonbin++;
        if (lo != 0 && lo != 15) nonbin++;
      }
      // <20% mid-gray => 1-bit content => DU. Generous so a small box's ~6px byte-align
      // border of gray house doesn't tip it into GC16 (that kept the tiny wren box on
      // GC16 while the larger bird box went DU). Real gray art is >50% mid-gray.
      int mode = (nonbin * 5 < totpx) ? 1 : 2;
      // The display mirrors X (invisible at full width, where the full refresh runs);
      // place the window at the mirrored X so it lands where the full render put it.
      int mx = FF_NATIVE_W - nx - nw;
      // Load each window into the controller now, but DEFER the display trigger. Firing
      // them one-at-a-time (load, display, wait) made the bird box and the pill box
      // repaint a couple seconds apart. Loading all first, then firing every display
      // area back-to-back, lets the IT8951 refresh the (non-overlapping) regions together.
      epaper.tconLoadImage(win, mx, ny, nw, nh, false);   // gray load — no 1bpp flip bug
      wmx[nwin] = mx; wny[nwin] = ny; wnw[nwin] = nw; wnh[nwin] = nh; wmode[nwin] = mode;
      Serial.printf("  win x=%d y=%d w=%d h=%d mode=%d\n", nx, ny, nw, nh, mode);
      nwin++;
      c = c1 + 1;
    }
    for (int i = 0; i < nwin; i++)                  // all changes appear at once
      epaper.tconDisplayArea(wmx[i], wny[i], wnw[i], wnh[i], wmode[i]);
    epaper.tconWaitForDisplayReady();
    // NOTE: do NOT sleep() here. The panel stays awake through the boot so the plate's
    // full update() later refreshes from a live gray state (sleeping mid-boot left the
    // T-CON in a state where the plate's GC16 re-showed the old screen).
    Serial.printf("screen %d: %d partial window(s)\n", idx, nwin);
  }
  memcpy(prev, body, FF_SCREEN_BYTES);
  havePrev = true;
  // Arm the loading-mark sweep if this screen carries one (see bake_screens.py).
  const FfLoader& ld = ff_loader[idx];
  if (ld.x >= 0) {
    g_loaderAnim.x = ld.x; g_loaderAnim.y = ld.y; g_loaderAnim.frames = ld.frames;
    g_loaderAnim.on = true;
  }
  g_glassScreen = (int8_t)idx;
  g_bandKind = g_bandStage = -1;            // fresh screen: no error band on it
  panelUnlock();
}

// Force a full gray repaint on the next screen — used when the glass doesn't
// share the boot layout (leaving the setup portal).
void showScreenFull(int idx) {
  g_scrHavePrev = false;
  showScreen(idx);
}

// Kept for the boot call site; the battery/build args are now baked in the art.
void showSplash(const char*, int) { showScreen(FF_SCR_SPLASH); }

// ---------------------------------------------------------------- fetch
// FETCH_REJECTED: the server answered 200 but the container failed displayFrame's
// checks (wrong size/version). It is an error for the glass and the backoff, but
// the server IS reachable — so OTA still runs, because a format mismatch is
// exactly the thing only a firmware update can fix.
enum FetchResult { FETCH_UPDATED, FETCH_NOCHANGE, FETCH_NOTFOUND, FETCH_NOFRAME, FETCH_ERROR, FETCH_REJECTED };

// Fetch a frame. `path`: endpoint under the server URL. `resident`: true for
// the normal current-bird frame (ETag conditional GET + store the new ETag);
// false for transient button views (no conditional, and the stored ETag is
// CLEARED so the next timer wake re-fetches the resident bird over the view).
// Wi-Fi modem sleep (the Arduino default) parks the radio between beacons, so
// every TCP round trip waits for a wake: ~80 ms on a quiet AP, several hundred
// on a busy one. lwIP's receive window is a fixed 5760 bytes with no scaling,
// so a 1.3 MB frame is ~230 round trips — 15 s at best and 1–2 min of
// "Downloading image" at worst (measured 3 Sep 2026). With power save off the
// round trip is ~2 ms and the same body arrives in a couple of seconds. The
// guard restores modem sleep on every exit so the always-awake build keeps its
// idle current between polls.
struct RadioAwake {
  RadioAwake()  { WiFi.setSleep(false); }
  ~RadioAwake() { WiFi.setSleep(true); }
};

static FetchResult fetchFrame(const char* path, bool resident, float vbat, int pct) {
  // Release the boot-art buffers first: the IT8951 full-image write needs ~1.31 MB of
  // contiguous PSRAM for its mirror buffer, and if the boot buffers still hold it the
  // plate silently fails to load. (Safe here — this path renders network data, not the
  // boot art; the splash uses displayFrame directly without going through here.)
  freeScreenBuffers();
  RadioAwake radio;                          // full-power Wi-Fi for the transfer
  HTTPClient http;
  String url = String(g_serverUrl) + path;
  if (!http.begin(url)) return FETCH_ERROR;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_CONNECT_TIMEOUT_MS);
  http.setUserAgent("Featherframe-ESP32/1.0");
  if (resident && strlen(g_etag)) http.addHeader("If-None-Match", String("\"") + g_etag + "\"");
  http.addHeader("X-Battery-Voltage", String(vbat, 3));
  http.addHeader("X-Battery-Percent", String(pct));
  http.addHeader("X-Wifi-RSSI", String(WiFi.RSSI()));
  http.addHeader("X-Wake", g_wakeToken);            // stable token (spec §3)
  http.addHeader("X-Wake-Detail", String(g_wakeInfo) + " " + g_battRaw);   // cause=N keys=0xM + ADC diag (debug)
  http.addHeader("X-FF-Version", FF_FW_VERSION);    // human build id (spec §1)
  http.addHeader("X-FF-Sketch-MD5", ESP.getSketchMD5());  // exact binary id
  http.addHeader("X-Boot-Count", String(g_bootCount));    // spec §5
  http.addHeader("X-Refresh-Count", String(g_refreshCount));
  http.addHeader("X-Panel", "ED103TC2 1404x1872 gray16"); // spec §6
  http.addHeader("X-Board", "XIAO-ESP32S3 EE03");
  const char* collect[] = {"ETag", "X-FF-Invert"};
  http.collectHeaders(collect, 2);

  int code = http.GET();
  Serial.printf("GET %s -> %d\n", url.c_str(), code);
  // The server announces dark mode on every response; remember it for the
  // baked screens/tiles (the plates arrive already inverted).
  String inv = http.header("X-FF-Invert");
  if (inv.length()) {
    bool v = (inv == "1");
    if (v != g_invert) { g_invert = v; prefs.putBool("invert", v); }
  }
  if (code == HTTP_CODE_NOT_MODIFIED) { http.end(); return FETCH_NOCHANGE; }
  if (code == HTTP_CODE_NOT_FOUND) { http.end(); return FETCH_NOTFOUND; }
  if (code == HTTP_CODE_SERVICE_UNAVAILABLE) { http.end(); return FETCH_NOFRAME; }  // server up, no bird yet
  if (code != HTTP_CODE_OK) { http.end(); return FETCH_ERROR; }

  int len = http.getSize();                 // -1 = chunked/no Content-Length: refuse
  if (len <= (int)FFF_HEADER_SIZE) { http.end(); return FETCH_ERROR; }
  // A frame is at most header + one native 4bpp body; anything bigger is not
  // ours and must not be handed to ps_malloc on a 512 KB-headroom heap.
  if (len > (int)(FFF_HEADER_SIZE + FF_SCREEN_BYTES)) {
    Serial.printf("frame too big: %d\n", len); http.end(); return FETCH_ERROR;
  }

  uint8_t* buf = (uint8_t*)ps_malloc(len);        // frame lives in PSRAM
  if (!buf) { Serial.println("ps_malloc failed"); http.end(); return FETCH_ERROR; }

  WiFiClient* stream = http.getStreamPtr();
  int got = 0;
  uint32_t t0 = millis();
  while (got < len && (millis() - t0) < HTTP_TIMEOUT_MS) {
    if (stream->available()) {
      got += stream->readBytes(buf + got, len - got);
    } else if (!stream->connected()) {
      break;                                // server hung up mid-body: don't sit out the timeout
    } else {
      delay(2);
    }
  }
  String newEtag = http.header("ETag");
  http.end();

  if (got != len) { Serial.printf("short read %d/%d\n", got, len); free(buf); return FETCH_ERROR; }

  bool painted = displayFrame(buf, len, true);   // retain: the toast band restores from it
  free(buf);
  if (!painted) return FETCH_REJECTED;       // rejected container: keep the ETag unset so we retry

  if (resident) {
    // Store the new ETag (strip quotes/W-prefix).
    newEtag.replace("W/", ""); newEtag.replace("\"", ""); newEtag.trim();
    if (newEtag.length()) { strlcpy(g_etag, newEtag.c_str(), sizeof(g_etag)); prefs.putString("etag", g_etag); }
  } else {
    // A transient view is on the glass; forget the resident ETag so the next
    // timer wake redraws the current bird instead of 304-ing forever.
    g_etag[0] = 0;
    prefs.putString("etag", "");
  }
  return FETCH_UPDATED;
}

// The fetch attempt is the loading mark's last leg: on success displayFrame has
// already replaced the loading screen (and disarmed the sweep); on a 304 or any
// failure the attempt is over, so stop the sweep rather than keep implying
// progress on a stale screen — in the always-awake model nothing else would,
// and it would burn ~200ms DU partials every FF_LOADER_STEP_MS forever.
FetchResult fetchAndRender(const char* path, bool resident, float vbat, int pct) {
  // A resident fetch while a baked screen (or its error band) holds the glass
  // must actually paint: drop the ETag so a healthy server answers 200, not a
  // 304 that would strand the boot art until the bird changes.
  if (resident && g_glassScreen >= 0) g_etag[0] = 0;
  FetchResult r = fetchFrame(path, resident, vbat, pct);
  g_loaderAnim.on = false;
  return r;
}

// Resident-fetch accounting: success clears the error state, failure advances
// it and updates the glass. Transient button views don't count — they are user
// actions, not frame health. Callers keep g_failMinutes current beforehand.
void noteFetchOutcome(FetchResult r) {
  // Any answer from the server proves the build can boot, join Wi-Fi and talk
  // HTTP: that is the rollback bar. ensureWifi() marks it too, but the
  // always-awake build only calls ensureWifi once; a slow router on the first
  // post-OTA boot would otherwise leave the image PENDING_VERIFY for its whole
  // uptime and roll it back on the next hard reset.
  if (r != FETCH_ERROR) markFirmwareGood();
  if (r == FETCH_UPDATED || r == FETCH_NOCHANGE) { noteSuccess(); return; }
  bumpFail();
  int kind = (WiFi.status() != WL_CONNECTED) ? ERRK_WIFI
           : (r == FETCH_NOFRAME ? ERRK_NOFRAME : ERRK_SERVER);
  showErrorState(kind);
}

// ---------------------------------------------------------------- ota
// Pull-based OTA on every wake: offer the running sketch's MD5; the server
// answers 304 (same build hosted) or 200 with a new firmware.bin, which we
// stream into the spare OTA slot and reboot into. No USB, no user.
//
// Rollback: the build has CONFIG_APP_ROLLBACK_ENABLE, but the Arduino core
// marks every new image valid during init unless verifyRollbackLater() says
// otherwise — so by default a build that hangs in setup() is never rolled
// back. Defer it: the image is marked good only once it has joined Wi-Fi
// (markFirmwareGood). A build that never gets there sleeps or watchdogs, the
// bootloader sees PENDING_VERIFY on the next boot, and boots the previous slot.
extern "C" bool verifyRollbackLater() { return true; }

void markFirmwareGood() {
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t st;
  if (running && esp_ota_get_state_partition(running, &st) == ESP_OK &&
      st == ESP_OTA_IMG_PENDING_VERIFY) {
    esp_ota_mark_app_valid_cancel_rollback();
    Serial.println("OTA: new build verified, rollback cancelled");
  }
}

void maybeOTA(float vbat) {
  // A flash write is the one thing worth refusing on a weak cell.
  if (vbat > FF_BATT_ABSENT_V && vbat < FF_OTA_MIN_BATT_V) {
    Serial.printf("OTA skipped: battery %.2f V\n", vbat);
    return;
  }
  HTTPClient http;
  String url = String(g_serverUrl) + FIRMWARE_PATH;
  if (!http.begin(url)) return;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_CONNECT_TIMEOUT_MS);
  http.setUserAgent("Featherframe-ESP32/1.0");
  http.addHeader("X-Firmware-MD5", ESP.getSketchMD5());
  const char* collect[] = {"X-MD5"};
  http.collectHeaders(collect, 1);
  int code = http.GET();
  Serial.printf("OTA check -> %d\n", code);
  if (code != HTTP_CODE_OK) { http.end(); return; }

  // The server names the image; an image that already failed to flash is not
  // downloaded again every wake (1.5 MB per cycle, forever, on battery).
  String md5 = http.header("X-MD5");
  md5.toLowerCase();
  if (md5.length() && md5 == prefs.getString("ota_bad", "")) {
    Serial.println("OTA: hosted image previously failed, skipping");
    http.end();
    return;
  }

  int len = http.getSize();
  if (len <= 0 || !Update.begin(len)) { http.end(); return; }
  if (md5.length() == 32) Update.setMD5(md5.c_str());   // end() then verifies the stream
  Serial.printf("OTA: flashing %d bytes\n", len);
  size_t written = Update.writeStream(*http.getStreamPtr());
  http.end();
  if (written == (size_t)len && Update.end()) {
    Serial.println("OTA ok — rebooting into new firmware");
    Serial.flush();
    ESP.restart();
  }
  Serial.printf("OTA failed: %s\n", Update.errorString());
  uint8_t err = Update.getError();
  Update.abort();
  // Only a COMPLETE, checksum-matching stream that still fails is a bad
  // image; a short read or an MD5 mismatch is the network (or a torn copy on
  // the server) and must be retried, not remembered forever.
  if (md5.length() && written == (size_t)len && err != UPDATE_ERROR_MD5)
    prefs.putString("ota_bad", md5);
}

// ---------------------------------------------------------------- setup
void setup() {
  Serial.begin(115200);
  delay(50);
  // TEMP boot-ping: 6s of prints after USB settles, so a late reader confirms the
  // app is actually running and where setup gets to. Remove once serial is trusted.
  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  g_bootCount++;                                    // wake tally (spec §5)
  strlcpy(g_wakeToken, wakeToken(cause), sizeof(g_wakeToken));
  bool buttonWake = (cause == ESP_SLEEP_WAKEUP_EXT1);
  bool fromDeepSleep = (cause == ESP_SLEEP_WAKEUP_TIMER || cause == ESP_SLEEP_WAKEUP_EXT1);
  Serial.printf("\nFeatherframe wake: cause=%d (%s) fw=%s\n",
                cause, buttonWake ? "button" : "timer/boot",
                ESP.getSketchMD5().substring(0, 8).c_str());

#ifdef FF_DEBUG_TOAST
  // On-bench toast harness: no Wi-Fi, no sleep, no watchdog. Loops the partial-
  // refresh toast so we can confirm it completes (prints an elapsed-ms line) or
  // hangs (no line). Flash with `pio run -e toastdebug -t upload`.
  Serial.println("DEBUG_TOAST: awake, looping toast every 3s");
  epaper.begin(0);
  for (int n = 0; ; n++) {
    Serial.printf("[%d] showToast start\n", n);
    uint32_t t0 = millis();
    showToast(n % 2 ? FF_TOAST_UP_TO_DATE : FF_TOAST_CHECKING);
    Serial.printf("[%d] showToast done in %lu ms\n", n, (unsigned long)(millis() - t0));
    delay(3000);
  }
#endif

  prefs.begin("featherframe", false);
  prefs.getString("server", DEFAULT_SERVER_URL).toCharArray(g_serverUrl, sizeof(g_serverUrl));
  normalizeServerUrl(g_serverUrl, sizeof(g_serverUrl), DEFAULT_SERVER_URL);   // older saves may carry a trailing '/'
  prefs.getString("etag", "").toCharArray(g_etag, sizeof(g_etag));
  g_wakeMinutes = prefs.getUInt("wake_min", DEFAULT_WAKE_MINUTES);
  g_invert = prefs.getBool("invert", false);

  // NOTE: the panel (Seeed_GFX) owns GPIO43 during init/refresh, so do NOT force it
  // here — that breaks the power sequencing and updates stop reaching the glass. We
  // re-assert it HIGH only in the idle loop, after rendering, to keep buttons alive.

  // Panel arbitration + the loading-mark sweep task. Created before any screen
  // shows so the mark animates through Wi-Fi connect, server connect, and the
  // download alike; it idles (no panel traffic) whenever no loader is armed.
  g_panelMutex = xSemaphoreCreateRecursiveMutex();
  xTaskCreatePinnedToCore(loaderTask, "ffloader", 4096, nullptr, 1, nullptr, 1);

  // Release the button pins from any lingering RTC-IO / hold state left by a prior
  // deep-sleep (ext1 wake config), then set them up as digital inputs with pullups.
  for (gpio_num_t p : {PIN_KEY0, PIN_KEY1, PIN_KEY2}) {
    rtc_gpio_hold_dis(p);
    rtc_gpio_deinit(p);
    pinMode(p, INPUT_PULLUP);
  }

  // Battery first, BEFORE the panel: the ADC needs only its own two pins, and
  // a low-battery hold must not leave the IT8951 awake (begin() wakes it and
  // only update() puts it back to sleep) through a four-hour deep sleep. This
  // also samples the cell at rest, not under the panel-init load. The cost:
  // a power-on KEY2 hold on a flat cell is not honoured until it is charged.
  float vbat = readBatteryVoltage();
  int pct = batteryPercent(vbat);
  Serial.printf("battery: %.3f V (%d%%)\n", vbat, pct);
  // Even the always-awake build sleeps on an empty cell — the alternative is
  // a brownout loop. It comes back on its own once the pack is charged.
  if (lowBatteryHold(vbat)) { goToSleep(FF_LOW_BATT_SLEEP_MIN); return; }

#if FF_NO_SLEEP
  // --- Always-awake dev model: splash now, then Wi-Fi, then poll buttons in loop().
  epaper.begin(0);                          // full init once; the panel stays warm
  armWatchdog();

  // Hold KEY2 at boot -> wipe Wi-Fi/server settings and open the setup portal.
  // Read it here, right after begin(): the splash's update() puts the T-CON
  // to sleep, and the keys don't register while it is (see PIN_PANEL_PWR).
  bool forcePortal = (digitalRead(PIN_PORTAL_RESET) == LOW);
  showSplash(buttonWake ? "button wake" : "booting", pct);

  if (forcePortal) { Serial.println("portal reset requested"); wm.resetSettings(); }

  snprintf(g_wakeInfo, sizeof(g_wakeInfo), "cause=%d nosleep", (int)cause);
  ensureWifi(forcePortal, true);   // loops the portal itself until first-run setup
  g_lastSuccessMs = millis();
  if (WiFi.status() == WL_CONNECTED) {
    g_etag[0] = 0;   // force a fresh paint so the plate replaces the splash (not a 304)
    showScreen(FF_SCR_BOOT_BIRDNET);          // reaching the server
    showScreen(FF_SCR_BOOT_DOWNLOAD);         // fetching the image
    FetchResult r = fetchAndRender(FRAME_PATH, true, vbat, pct);
    noteFetchOutcome(r);
    if (r != FETCH_ERROR) maybeOTA(vbat);     // unreachable server: don't burn a second connect timeout
  } else {
    // Saved network unreachable right now: say so on the glass and let the
    // poll loop retry.
    bumpFail();
    showErrorState(ERRK_WIFI);
  }
  Serial.println("ready — polling buttons");
#else
  // --- Deep-sleep model: decode the waking button, act once, sleep.
  armWatchdog();                            // reboot if a wake cycle hangs

  uint64_t keyBits = buttonWake ? esp_sleep_get_ext1_wakeup_status() : 0;
  bool keyCheck   = keyBits & (1ULL << PIN_KEY0);
  bool keyCollage = keyBits & (1ULL << PIN_KEY1);
  bool keyStatus  = keyBits & (1ULL << PIN_KEY2);
  snprintf(g_wakeInfo, sizeof(g_wakeInfo), "cause=%d keys=0x%llx", (int)cause,
           (unsigned long long)keyBits);

  // Panel next (battery was read above, before it): the keys only read while
  // the panel side is powered and the T-CON awake (see PIN_PANEL_PWR), so the
  // power-on hold and the 3 s KEY2 hold below can't be sampled before begin().
  epaper.begin(fromDeepSleep ? 1 : 0);

  bool forcePortal = (!buttonWake && digitalRead(PIN_PORTAL_RESET) == LOW);
  if (keyStatus) {
    uint32_t t0 = millis();
    while (digitalRead(PIN_PORTAL_RESET) == LOW && millis() - t0 < PORTAL_HOLD_MS) delay(20);
    if (millis() - t0 >= PORTAL_HOLD_MS) { forcePortal = true; keyStatus = false; }
  }
  if (forcePortal && !fromDeepSleep) {
    Serial.println("factory reset requested");   // power-on + held KEY2 only
    wm.resetSettings();
  }

  if (!ensureWifi(forcePortal, !fromDeepSleep)) {
    if (buttonWake) ackBlink(4);
    bumpFail();
    uint32_t mins = retryDelayMinutes();
    showErrorState(ERRK_WIFI);
    uint32_t nm = (uint32_t)g_failMinutes + mins;
    g_failMinutes = nm > 65535 ? 65535 : (uint16_t)nm;
    Serial.printf("no wifi — retrying in %u min\n", mins);
    goToSleep(mins);
    return;
  }

  FetchResult r;
  bool residentFetch = !keyCollage && !keyStatus;
  if (keyCollage) {
    r = fetchAndRender(VIEW_COLLAGE_PATH, false, vbat, pct);
    if (r == FETCH_NOTFOUND) ackBlink(4);
  } else if (keyStatus) {
    r = fetchAndRender(VIEW_STATUS_PATH, false, vbat, pct);
  } else {
    // Boot screens only on a true cold boot or straight out of setup — a
    // deep-sleep wake leaves the resident plate alone and fetches silently.
    if (!fromDeepSleep || g_viaPortal) {
      showScreen(FF_SCR_BOOT_BIRDNET);
      showScreen(FF_SCR_BOOT_DOWNLOAD);
    }
    r = fetchAndRender(FRAME_PATH, true, vbat, pct);
    if (keyCheck && r == FETCH_NOCHANGE) showToast(FF_TOAST_UP_TO_DATE);
  }
  if (buttonWake && (r == FETCH_ERROR || r == FETCH_REJECTED || r == FETCH_NOFRAME)) ackBlink(4);
  if (residentFetch) {
    noteFetchOutcome(r);
    if (r != FETCH_UPDATED && r != FETCH_NOCHANGE) {
      uint32_t mins = retryDelayMinutes();
      uint32_t nm = (uint32_t)g_failMinutes + mins;
      g_failMinutes = nm > 65535 ? 65535 : (uint16_t)nm;
      if (r != FETCH_ERROR) maybeOTA(vbat);   // the server answered (404/503): worth the check
      goToSleep(mins);
      return;
    }
  }
  maybeOTA(vbat);
  goToSleep(g_wakeMinutes);
#endif
}

#if FF_NO_SLEEP
// The always-awake poll clock and the button-view hold. A transient view's
// fetch clears the resident ETag, so without the hold the very next poll
// would repaint the bird over the collage the user just asked for.
static uint32_t g_lastPoll = 0;
static uint32_t g_lastOta = 0;    // last hosted-firmware check from the poll loop
static uint32_t g_viewHoldUntil = 0;

// Run a button's action: an instant pill for feedback, then fetch + paint. A new
// plate paints over the pill; on a no-change check the pill becomes "Up to date".
void doButton(int key) {
  float vbat = readBatteryVoltage();
  int pct = batteryPercent(vbat);
  if (key == 0) {                             // KEY0: check now
    g_viewHoldUntil = 0;                      // asking for the bird ends a view hold
    showToast(FF_TOAST_CHECKING);
    FetchResult r = fetchAndRender(FRAME_PATH, true, vbat, pct);
    if (r == FETCH_NOCHANGE)      showToast(FF_TOAST_UP_TO_DATE);
    else if (r == FETCH_UPDATED)  g_toast.active = false;   // new plate replaced it
    else                          showToast(FF_TOAST_CHECK_FAILED);
  } else if (key == 1) {                      // KEY1: collage
    showToast(FF_TOAST_COLLAGE);
    FetchResult r = fetchAndRender(VIEW_COLLAGE_PATH, false, vbat, pct);
    if (r == FETCH_NOTFOUND)      showToast(FF_TOAST_NO_COLLAGE);
    else if (r == FETCH_UPDATED)  { g_toast.active = false; g_viewHoldUntil = millis() + FF_VIEW_HOLD_MS; }
    else                          showToast(FF_TOAST_COLLAGE_FAILED);
  } else {                                    // KEY2 tap: status
    showToast(FF_TOAST_STATUS);
    FetchResult r = fetchAndRender(VIEW_STATUS_PATH, false, vbat, pct);
    if (r == FETCH_UPDATED)       { g_toast.active = false; g_viewHoldUntil = millis() + FF_VIEW_HOLD_MS; }
    else                          showToast(FF_TOAST_STATUS_FAILED);
  }
  g_lastPoll = millis();                      // the render leg may exceed the poll gap
  Serial.printf("button %d handled\n", key);
}

// Debounced active-low edge detect. Returns 0/1/2 on a fresh press, else -1.
int pollButton() {
  static uint8_t prev[3] = {HIGH, HIGH, HIGH};
  const int pins[3] = {PIN_KEY0, PIN_KEY1, PIN_KEY2};
  for (int i = 0; i < 3; i++) {
    int v = digitalRead(pins[i]);
    if (v == LOW && prev[i] == HIGH) {
      delay(15);
      if (digitalRead(pins[i]) == LOW) { prev[i] = LOW; return i; }
    } else if (v == HIGH) {
      prev[i] = HIGH;
    }
  }
  return -1;
}

void loop() {
  esp_task_wdt_reset();
  int k = pollButton();
  if (k == 2) {
    // KEY2: hold PORTAL_HOLD_MS -> setup portal; a quick tap -> status view.
    uint32_t t0 = millis();
    while (digitalRead(PIN_KEY2) == LOW && millis() - t0 < PORTAL_HOLD_MS) delay(20);
    if (millis() - t0 >= PORTAL_HOLD_MS) {
      // The runtime hold does NOT wipe credentials — the portal itself can
      // change networks, and an accidental three-second press must not orphan
      // a wall-mounted frame. The destructive wipe lives only on the power-on
      // hold (the factory-reset gesture in setup()).
      bool ok = ensureWifi(true, false);
      if (!ok && wm.getWiFiIsSaved())
        ok = ensureWifi(false, false);    // portal timed out: rejoin the saved network
      if (ok) {
        if (g_glassScreen >= 0) {
          // The portal saved a network (the save callback repainted the boot
          // screen): run the normal flow through to a fresh plate.
          showScreen(FF_SCR_BOOT_BIRDNET);
          showScreen(FF_SCR_BOOT_DOWNLOAD);
          g_etag[0] = 0;
          float vb = readBatteryVoltage();
          noteFetchOutcome(fetchAndRender(FRAME_PATH, true, vb, batteryPercent(vb)));
          g_toast.active = false;         // the full repaint took the pill with it
        } else {
          clearToast();                   // peeked and left: the plate stays put
        }
      }
    } else {
      doButton(2);
    }
  } else if (k >= 0) {
    doButton(k);
  }
  if (g_toast.active && millis() - g_toast.shownAt >= TOAST_HOLD_MS) clearToast();

  // Re-fetch every FF_POLL_INTERVAL_MS. fetchAndRender sends the stored ETag, so
  // an unchanged frame returns 304 and the panel is not repainted. Failed polls
  // back off to FF_POLL_BACKOFF_MS and keep the error state current. A button-
  // requested view holds the glass for FF_VIEW_HOLD_MS first — the view fetch
  // clears the ETag, so an eager poll would repaint the bird within seconds of
  // the press that asked for the collage.
  uint32_t interval = (g_failCount >= FF_MARK_FAILS) ? FF_POLL_BACKOFF_MS
                                                     : FF_POLL_INTERVAL_MS;
  if (g_viewHoldUntil && (int32_t)(millis() - g_viewHoldUntil) < 0) {
    // transient view on the glass
  } else if (millis() - g_lastPoll >= interval) {
    g_lastPoll = millis();
    float vb = readBatteryVoltage();
    FetchResult r = fetchAndRender(FRAME_PATH, true, vb, batteryPercent(vb));
    uint32_t mins = (millis() - g_lastSuccessMs) / 60000UL;
    g_failMinutes = mins > 65535 ? 65535 : (uint16_t)mins;
    noteFetchOutcome(r);
    // The boot-time OTA check is skipped when the server is unreachable; the
    // always-awake build never reboots on its own, so re-check from here.
    if (r != FETCH_ERROR && millis() - g_lastOta >= FF_OTA_CHECK_MS) {
      g_lastOta = millis();
      maybeOTA(vb);
    }
  }

  // The buttons' pull-up rail is powered by the panel's enable line (GPIO43). The
  // panel's update() drops it to sleep the T-CON, which also kills the buttons, so
  // re-assert it HIGH here (in the idle loop, after any render) to keep presses alive.
  static bool pwrInit = false;
  if (!pwrInit) { pinMode(PIN_PANEL_PWR, OUTPUT); pwrInit = true; }
  digitalWrite(PIN_PANEL_PWR, HIGH);
  delay(10);
}
#else
void loop() {}   // never reached — deep sleep model
#endif
