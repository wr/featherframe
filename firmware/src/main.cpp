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
#include <WebSocketsClient.h>
#include <ESPmDNS.h>
#include <FS.h>            // WebServer.h (via WiFiManager) uses unqualified FS on
using namespace fs;        // arduino-esp32 v3, so pull fs:: into scope before it
#include <WebServer.h>
#include <WiFiManager.h>
#include "ff_improv.h"
#include <Preferences.h>
#include <Update.h>
#include <esp_sleep.h>
#include <esp_task_wdt.h>
#include <esp_ota_ops.h>
#include <driver/rtc_io.h>

#include "ff_config.h"
#if FF_PANEL_GENERIC
#include FF_SCREENS_HEADER     // bake_screens.py --size: this panel's own full-refresh set
static_assert(FF_SCREENS_ROTATION == FF_BAKED_ROTATION,
              "FF_BAKED_ROTATION is not the rotation FF_SCREENS_HEADER was baked at");
#elif FF_PANEL_SPECTRA6
#include "ff_screens_ee02.h"   // baked black/white-ink screens (1200x1600): setup + errors
#else
#include "ff_screens.h"    // baked 1-bit boot/setup panel screens (1404x1872)
#endif

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

#if FF_PANEL_SPECTRA6
// EPaper::update() with every BUSY wait bounded (W-820). The driver's EPD_*
// macros expand only inside the display class (they call writecommanddata and
// name the SPI handle `spi`), hence a subclass; CHECK_BUSY is redefined around
// them so the init and push steps are bounded too. Same sequence as the
// library's: INIT (hardware reset) -> push -> PON -> DRF -> POF -> sleep.
class SpectraPaper : public EPaper {
 public:
  // One full refresh of the sprite. False if the controller held BUSY past
  // its cap; it is then reset and powered off before this returns.
  bool refresh() {
    SPIClass& spi = getSPIinstance();
    _stuck = false;
#pragma push_macro("CHECK_BUSY")
#undef CHECK_BUSY
#define CHECK_BUSY() waitBusy(FF_BUSY_STEP_MS, "INIT/push/sleep")
    EPD_INIT();
    if (!_stuck) EPD_PUSH_NEW_COLORS(_width, _height, _img8);
    if (!_stuck) { both(R04_PON, nullptr, 0); waitBusy(FF_BUSY_STEP_MS, "PON"); delay(30); }
    if (!_stuck) { both(R12_DRF, DRF_V, sizeof(DRF_V)); waitBusy(FF_BUSY_DRF_MS, "DRF"); delay(30); }
    const bool stuck = _stuck;
    // A controller still holding BUSY may still be driving the glass: a
    // hardware reset stops it before POF (the bench's recovery, PR #135).
    if (stuck) EPD_INIT();
    both(R02_POF, POF_V, sizeof(POF_V));
    waitBusy(FF_BUSY_STEP_MS, "POF");
    delay(30);
    EPD_SLEEP();
#pragma pop_macro("CHECK_BUSY")
    return !stuck;
  }

 private:
  bool _stuck = false;

  // CS1 low while writecommanddata drives CS: the command reaches both chips.
  void both(uint8_t cmd, const uint8_t* data, uint16_t n) {
    digitalWrite(TFT_CS1, LOW);
    if (n) writecommanddata(cmd, data, n); else writecommand(cmd);
    digitalWrite(TFT_CS1, HIGH);
  }

  void waitBusy(uint32_t capMs, const char* step) {
    const uint32_t t0 = millis();
    do {
      delay(10);
      if (digitalRead(TFT_BUSY)) return;
    } while (millis() - t0 < capMs);
    _stuck = true;
    Serial.printf("panel: BUSY stuck after %s (%lu ms)\n", step, (unsigned long)capMs);
  }
};
SpectraPaper epaper;           // Seeed_GFX display object (combo 510)
#else
EPaper epaper;                 // Seeed_GFX display object (combo 511, or a generic panel)
#endif
Preferences prefs;             // NVS: server URL, wake minutes, last ETag
WiFiManager wm;

char     g_serverUrl[128];
char     g_etag[40];
uint32_t g_wakeMinutes = DEFAULT_WAKE_MINUTES;
bool     g_alwaysAwake = FF_DEFAULT_ALWAYS_AWAKE;   // power model, served by the server (NVS "awake")
uint32_t g_pollMs = FF_POLL_INTERVAL_MS;            // always-awake poll gap, served by the server (NVS "poll_s")
char     g_wakeInfo[64] = "";   // "cause=N keys=0xM" — sent as X-Wake-Detail (debug)
char     g_battRaw[48] = "";    // last ADC read: raw counts, first/last sample, eFuse mV
char     g_lastXfer[40] = "";   // last frame body transfer: "xfer=got/lenB ms [stall|cap|hangup]"
char     g_wakeToken[16] = "";  // stable token ("timer"|"button"|"coldboot") — X-Wake
char     g_mdnsNote[12] = "";   // last discovery: "mdns=new|same|miss" — rides X-Wake-Detail
bool     g_viaPortal = false;   // did this boot go through the setup portal?
// The last picture this frame was sent is its pairing code (NVS "unpaired"):
// no one has claimed it yet. Kept apart from the ETag, which a baked screen
// clears, so a restart or a Wi-Fi reset still knows it.
bool     g_unpaired = false;
char     g_redirect[128] = "";  // a 403's X-FF-Server: the instance that draws for our panel

// Push (W-841): the socket the server says "something changed" over, on USB.
WebSocketsClient g_ws;
volatile bool g_pushUp = false;   // the socket is open (set by the push task)
volatile bool g_pushWake = false; // the server said something changed: fetch now
volatile bool g_pushOta = false;  // …and a firmware update is waiting for us
static bool pushLive();         // the socket is up and its task is turning (W-853)

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

// Ask the LAN where the server is: one PTR query for _featherframe._tcp
// (blocks ~3 s). Fills url ("http://ip:port") and returns true on a hit.
// `exclude`: a server that has just told us it serves another frame (403) —
// look past it, or a parked frame would never find the instance meant for it.
static bool discoverServer(char* url, size_t n, const char* exclude = nullptr) {
  if (!MDNS.begin("featherframe-frame")) { Serial.println("mDNS: begin failed"); return false; }
  int found = MDNS.queryService(FF_MDNS_SERVICE, FF_MDNS_PROTO);
  bool ok = false;
  for (int i = 0; i < found; i++)
    Serial.printf("mDNS[%d]: %s:%u panel=%s\n", i, MDNS.address(i).toString().c_str(),
                  (unsigned)MDNS.port(i), MDNS.txt(i, "panel").c_str());
  // Two instances can share a LAN (one per panel), so prefer the one whose
  // TXT "panel" names ours. Failing that, take any: a server follows the panel
  // of the frame that checks in, which is how a frame swapped for one with a
  // different panel finds the household's one server (it answers 409 instead
  // if another frame is still live on it).
  for (int pass = 0; pass < 2 && !ok; pass++) {
    for (int i = 0; i < found && !ok; i++) {
      if (pass == 0 && MDNS.txt(i, "panel") != FF_PANEL_KEY) continue;
      IPAddress ip = MDNS.address(i);
      uint16_t port = MDNS.port(i);
      if (ip == IPAddress((uint32_t)0) || !port) continue;
      char cand[128];
      snprintf(cand, sizeof(cand), "http://%s:%u", ip.toString().c_str(), (unsigned)port);
      if (exclude && strcmp(cand, exclude) == 0) continue;
      strlcpy(url, cand, n);
      ok = true;
    }
  }
  MDNS.end();
  Serial.printf("mDNS: %d service(s)%s%s\n", found, ok ? " -> " : "", ok ? url : "");
  return ok;
}

// Discover and, if it differs from what we have, adopt the server URL. Called
// with no URL yet (a fresh unit) and after a connect failure (the box moved).
// Rate-limited so an outage in the always-awake poll loop doesn't spend 3 s
// on every poll.
static bool adoptDiscoveredServer(bool lookPastCurrent = false) {
  static bool tried = false;
  static uint32_t lastTry = 0;
  if (tried && millis() - lastTry < FF_MDNS_RETRY_MS) return false;
  tried = true; lastTry = millis();
  char found[sizeof(g_serverUrl)];
  if (!discoverServer(found, sizeof(found), lookPastCurrent ? g_serverUrl : nullptr)) { strlcpy(g_mdnsNote, "mdns=miss", sizeof(g_mdnsNote)); return false; }
  if (strcmp(found, g_serverUrl) == 0)       { strlcpy(g_mdnsNote, "mdns=same", sizeof(g_mdnsNote)); return false; }
  strlcpy(g_mdnsNote, "mdns=new", sizeof(g_mdnsNote));
  Serial.printf("server: %s -> %s\n", g_serverUrl[0] ? g_serverUrl : "(none)", found);
  strlcpy(g_serverUrl, found, sizeof(g_serverUrl));
  prefs.putString("server", g_serverUrl);
  return true;
}

// This frame's name to the server: its Wi-Fi MAC, bare hex. The server serves
// one frame and asks its owner before switching to another (X-Device-Id).
static String frameId() {
  String id = WiFi.macAddress();
  id.replace(":", "");
  return id;
}

// A secret of the frame's own (W-845), made once from the hardware RNG and
// kept in NVS: sent as X-FF-Key with its ID, so a hosted server knows this is
// the frame it paired and not something on the Internet using its MAC. A
// self-hosted server ignores it. Survives OTA; a full erase makes a new one,
// and the frame asks to be paired again.
static String frameKey() {
  static String key;
  if (key.length()) return key;
  key = prefs.getString("ffkey", "");
  if (key.length() != 32) {
    char hex[33];
    for (int i = 0; i < 4; i++) snprintf(hex + i * 8, 9, "%08lx", (unsigned long)esp_random());
    key = String(hex);
    prefs.putString("ffkey", key);
  }
  return key;
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
#if !FF_FULL_REFRESH    // no partial refresh on Spectra: no sweep, no tiles
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
#endif

// ---------------------------------------------------------------- error states
// Failure presentation (design: Linear W-587). On a boot pill screen the pill
// band is swapped in place — outlined pill + slashed icon for real errors, the
// solid pill for "waiting for the first bird" — with a "Trying again …" line
// beneath. Over a painted plate only a small slashed glyph appears in the
// margin corner, and only past the FF_MARK_* thresholds. All tiles are baked
// pure black/white and pushed as windowed DU partials (no flash). The state
// survives deep sleep in RTC memory.
enum ErrKind { ERRK_WIFI = 0, ERRK_SERVER = 1, ERRK_NOFRAME = 2, ERRK_PENDING = 3 };
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

// The frame hangs the other way up (config.panel_rotation is not the one the
// art was baked at): the server rotates the plates, and announces the rotation
// (X-FF-Rotation, kept in NVS) so everything baked follows —
// a 4bpp buffer turned 180 degrees is its bytes reversed with the nibbles
// swapped, and a tile's window mirrors to the opposite corner.
bool g_flip = false;
// The mat it hangs with, as the server last said it (X-FF-Mat, "inset,x,y",
// kept in NVS). Nothing here draws with it: it is said back on every ask, so
// a frame removed and added again starts with the mat it had.
char g_mat[32] = "";
static bool validMat(const String& m) {
  if (!m.length() || m.length() >= sizeof(g_mat)) return false;
  int commas = 0;
  for (char c : m) {
    if (c == ',') commas++;
    else if (!isdigit((unsigned char)c) && c != '.' && c != '-') return false;
  }
  return commas == 2;
}
static inline uint8_t swapNibbles(uint8_t b) { return (uint8_t)((b << 4) | (b >> 4)); }
static void rotate180(uint8_t* buf, size_t n) {
  for (size_t i = 0, j = n - 1; i < j; i++, j--) {
    uint8_t a = swapNibbles(buf[i]);
    buf[i] = swapNibbles(buf[j]);
    buf[j] = a;
  }
  if (n & 1) buf[n / 2] = swapNibbles(buf[n / 2]);
}
static inline int flipX(int x, int w) { return g_flip ? FF_NATIVE_W - x - w : x; }
static inline int flipY(int y, int h) { return g_flip ? FF_NATIVE_H - y - h : y; }
#if FF_FULL_REFRESH
// ff_config.h must know the panel before it picks the hold voltage: the full
// "Battery low" screen needs the headroom the pill does not.
static_assert(FF_LOW_BATT_V > 3.5f, "EE02 built with the gray panel's low-battery threshold");
// Spectra error presentation: the same states as the gray frame, each one a
// ~30 s full refresh instead of a windowed update (W-817). While a baked
// screen holds the glass (boot, setup, an earlier error) the error takes it as
// a baked full screen. Over a painted plate the corner mark appears at the
// gray frame's own thresholds, stamped into the retained plate and the whole
// glass repainted (repaintPlate); a wake out of deep sleep has no retained
// plate, so there the same thresholds bring up the full error screen.
void showScreen(int idx);
static bool repaintPlate();
void showErrorState(int kind) {
  int scr = kind == ERRK_WIFI ? FF_SCR_ERR_WIFI : kind == ERRK_SERVER ? FF_SCR_ERR_SERVER
          : kind == ERRK_PENDING ? FF_SCR_PENDING : FF_SCR_WAITING;
  if (g_glassScreen >= 0) {
    if (g_glassScreen != scr) showScreen(scr);
    return;
  }
  if (g_failCount < FF_MARK_FAILS || g_failMinutes < FF_MARK_MINUTES) return;
  uint8_t mark = (kind == ERRK_WIFI) ? 1 : 2;
  if (g_cornerMark == mark) return;
  g_cornerMark = mark;
  if (!repaintPlate()) { g_cornerMark = 0; showScreen(scr); }
}
uint32_t retryDelayMinutes() {
  return g_failCount <= 1 ? 1 : g_failCount == 2 ? 5 : 15;
}
// A cycle succeeded: a 304 keeps the plate, so a corner mark needs its own
// clean repaint (a new plate has already painted over it, see plateArrived).
void noteSuccess() {
  if (g_cornerMark) {
    g_cornerMark = 0;
    if (!repaintPlate()) {            // no retained plate: have the next fetch repaint
      g_etag[0] = 0;
      prefs.putString("etag", "");
    }
  }
  g_failCount = 0;
  g_failMinutes = 0;
  g_lastSuccessMs = millis();
}
#else
static uint8_t g_tileBuf[FF_MAX_TILE_BYTES];

// A baked tile as the glass needs it: turned for a frame hung the other way
// up. Only call while holding the panel mutex — g_tileBuf is shared.
static const uint8_t* turnedTile(const uint8_t* t, size_t n) {
  if (!g_flip) return t;
  memcpy(g_tileBuf, t, n);
  rotate180(g_tileBuf, n);
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
  tile = turnedTile(tile, (size_t)(w / 2) * h);
  x = flipX(x, w); y = flipY(y, h);             // the window mirrors with the art
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
    int stage = kind == ERRK_PENDING ? 4        // waiting on a person, not a retry: no line
              : g_alwaysAwake ? 3               // polls retry in seconds: "shortly"
              : g_failCount <= 1 ? 0 : g_failCount == 2 ? 1 : 2;
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
#endif  // !FF_FULL_REFRESH

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
void clearToast();   // defined with the toasts, below
void goToSleep(uint32_t minutes) {
  g_loaderAnim.on = false;
  clearToast();              // a pill must not sit on the plate for a whole wake interval
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
// sheet still supplies the signal-strength sprite). The "Featherframe"
// wordmark is the plates' script: a WOFF subset of the bundled face
// embedded as a data URI by tools/portal_font.py into ff_portal_font.h — the
// captive portal has no internet, so the face must travel with the page.
// Without that header the @font-face is empty and the h1 falls back to
// Georgia italic. Kept in PROGMEM.
#if __has_include("ff_portal_font.h")
#include "ff_portal_font.h"
#else
#define FF_PORTAL_FONT_FACE ""
#endif
static const char PORTAL_CSS[] PROGMEM = R"CSS(<style>)CSS" FF_PORTAL_FONT_FACE R"CSS(
:root{--bg:#efeae0;--card:#fbf9f4;--ink:#20201d;--muted:#6f685c;--accent:#3f5e46;--err:#8a4a3a;--line:#ddd6c8}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;margin:0;padding:28px 18px 60px;line-height:1.5;text-align:center}
.wrap{text-align:left;display:inline-block;width:100%;min-width:260px;max-width:430px}
h1{font-family:'FFScript',Georgia,serif;font-style:italic;font-weight:500;font-size:2.6rem;text-align:center;margin:.4em 0 0}
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

// The captive portal is open (Improv, W-839: Wi-Fi set over USB closes it).
static volatile bool g_portalOpen = false;
// NVS is open: Improv (its own task, started first thing) may read it.
static volatile bool g_prefsReady = false;

// Improv over USB (ff_improv.h): the flasher asks what the frame is and
// whether it is on Wi-Fi, and can give it a network. Credentials joined
// while the portal is open close the portal and boot carries on; joined
// while running, the frame restarts onto them.
static void startImprov() {
  ImprovHooks hooks;
  hooks.serverUrl = []() -> const char* { return g_serverUrl; };
  hooks.hasSavedWifi = []() { return wm.getWiFiIsSaved(); };
  hooks.onJoined = []() {
    if (!g_portalOpen) return false;
    wm.stopConfigPortal();   // blocking portal: sets its abort flag
    return true;
  };
  // A signed-in hosted page pairs the frame over USB (W-848).
  hooks.identity = [](const char** out, size_t max) -> size_t {
    if (!g_prefsReady || max < 10) return 0;
    static String id, key, w, h, rot;
    id = frameId(); key = frameKey();
    w = String(FF_NATIVE_W); h = String(FF_NATIVE_H);
    rot = String(g_flip ? (FF_BAKED_ROTATION + 180) % 360 : FF_BAKED_ROTATION);
    const char* v[] = {id.c_str(), key.c_str(), FF_PANEL_ID, w.c_str(), h.c_str(),
                       FF_PANEL_FORMAT, FF_PANEL_ROTATIONS, rot.c_str(), FF_BOARD_ID, g_mat};
    for (size_t i = 0; i < 10; i++) out[i] = v[i];
    return 10;
  };
  hooks.setServer = [](const char* url) -> bool {
    char next[sizeof(g_serverUrl)];
    strlcpy(next, url, sizeof(next));
    normalizeServerUrl(next, sizeof(next), g_serverUrl);
    if (strcmp(next, g_serverUrl) == 0) return false;
    strlcpy(g_serverUrl, next, sizeof(g_serverUrl));
    prefs.putString("server", g_serverUrl);
    return true;
  };
  improvBegin("Featherframe", FF_FW_VERSION, FF_BOARD_ID, hooks);
}

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
  static WiFiManagerParameter serverParam("server",
                                          "Featherframe server URL (leave blank to find it automatically)",
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
    g_portalOpen = true;
#if FF_FULL_REFRESH
    // The pill is stamped on the retained plate; with none to stamp on (a
    // wake out of deep sleep) the steps take the glass.
    if (g_glassScreen < 0 && g_lastFrame) showToast(FF_TOAST_PORTAL);
    else showScreen(FF_SCR_SETUP);
#else
    if (g_glassScreen < 0) showToast(FF_TOAST_PORTAL);
    else showScreen(FF_SCR_SETUP);
#endif
  });
  wm.setSaveConfigCallback([]() { showScreenFull(FF_SCR_BOOT_WIFI); });

  // Join the strongest AP carrying the SSID, not the first one to answer. The
  // core default (WIFI_FAST_SCAN) takes whichever AP replies first, so on a
  // multi-AP network the frame can land on a weak one for the whole session;
  // every transfer is RTT-bound (5760-byte window, see fetchFrame), so the
  // AP choice sets the fetch time. Costs one all-channel scan (~1-2 s) per
  // join. Both settings feed WiFi.begin(), which WiFiManager calls for us.
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);

  bool ok;
  if (openPortal) {
    ok = wm.startConfigPortal("Featherframe-Setup");
  } else {
    // Deep-sleep wakes connect silently (showBoot false): the resident plate
    // stays on the glass and a 304 wake never repaints anything.
#if FF_FULL_REFRESH
    // One boot screen, ~30 s to paint: start joining the saved network first so
    // the two overlap (autoConnect picks up the connection already under way).
    // With no network saved the setup steps are about to take the glass; a
    // "Connecting" screen ahead of them would only be a second refresh.
    if (showBoot && wm.getWiFiIsSaved()) {
      WiFi.mode(WIFI_STA);
      WiFi.begin();
      showScreen(FF_SCR_BOOT_WIFI);               // "Connecting"
    }
#else
    if (showBoot) showScreen(FF_SCR_BOOT_WIFI);   // "Connecting to Wi-Fi"
#endif
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
  g_portalOpen = false;
  // Wi-Fi given over USB (Improv) closes the portal as an abort; the frame
  // is on the network all the same.
  if (!ok && WiFi.status() == WL_CONNECTED) ok = true;
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
static uint32_t g_lastPaintMs = 0;   // Spectra: last full refresh, for the repaint floor
// Spectra: the refresh is SpectraPaper's, bounded (W-820). A stuck one leaves
// the glass as it was and fullPaint() says false; after FF_STUCK_MAX in a row
// the panel is held off (panelHeldOff) and nothing is sent to it until
// FF_STUCK_RETRY_S has passed. System time runs through deep sleep; a restart
// zeroes it, which counts as the wait being over.
RTC_DATA_ATTR uint8_t g_stuckCount = 0;   // stuck refreshes in a row
RTC_DATA_ATTR time_t  g_stuckAt = 0;      // when the last one was
RTC_DATA_ATTR bool    g_paintOwed = false; // the glass is behind the stored ETag
static bool g_paintOk = true;              // the last fullPaint() reached the glass
static bool panelHeldOff() {
#if FF_PANEL_SPECTRA6
  if (g_stuckCount < FF_STUCK_MAX) return false;
  const time_t now = time(nullptr);
  return now >= g_stuckAt && now - g_stuckAt < FF_STUCK_RETRY_S;
#else
  return false;
#endif
}
#if FF_FULL_REFRESH
static void plateArrived();          // defined with the toasts, below
static bool paintPlate();
#endif
#if FF_FULL_REFRESH
// The one call into the panel driver on the full-refresh path: a whole 4bpp
// body, then a full refresh. A port to another driver replaces this.
// Inks: the colour sprite is 4bpp from begin() and the nibbles are already
// Seeed's ink codes, so a body lands verbatim; refresh() is the ~30 s one.
// Gray: the 16-level sprite is allocated by initGrayMode(), as the gray build
// does it.
static bool fullPaint(const uint8_t* body) {
  if (panelHeldOff()) {
    Serial.println("panel: held off after stuck refreshes");
    return g_paintOk = false;
  }
  panelLock();
#if !FF_FRAME_INKS
  epaper.initGrayMode(GRAY_LEVEL16);
  epaper.fillSprite(TFT_GRAY_15);
#endif
  epaper.pushImage(0, 0, FF_NATIVE_W, FF_NATIVE_H, (uint16_t*)body);
  uint32_t t0 = millis();
#if FF_PANEL_SPECTRA6
  const bool ok = epaper.refresh();
  if (ok) {
    g_stuckCount = 0;
  } else {
    if (g_stuckCount < 255) g_stuckCount++;
    g_stuckAt = time(nullptr);
  }
#else
  epaper.update();
  const bool ok = true;
#endif
  Serial.printf("refresh %lu ms%s\n", (unsigned long)(millis() - t0), ok ? "" : " (stuck, panel reset)");
  g_refreshCount++;
  g_lastPaintMs = millis();                 // a stuck refresh drove the glass too: the floor holds
  panelUnlock();
  return g_paintOk = ok;
}
#endif
bool displayFrame(const uint8_t* data, size_t len, bool retain = false) {
  g_paintOk = true;
  if (len < FFF_HEADER_SIZE) return false;
  FFFHeader h;
  memcpy(&h, data, FFF_HEADER_SIZE);
  if (memcmp(h.magic, "FFF1", 4) != 0) { Serial.println("bad frame magic"); return false; }
#if FF_FRAME_INKS
  // Only an ink frame (4bpp + FFF_FLAG_INKS): gray levels pushed as ink codes
  // would paint noise. The server switches its render on our X-Panel, so a
  // gray frame here means it has not seen this device yet — the retry gets it.
  if (h.version != 1 || h.bpp != 4 || !(h.flags & FFF_FLAG_INKS)) {
    Serial.printf("bad frame: version=%d bpp=%d flags=0x%02x (want 4bpp inks)\n",
                  h.version, h.bpp, h.flags);
    return false;
  }
#elif FF_FULL_REFRESH
  // A gray panel on the full-refresh path: 16 levels only (the retained plate,
  // the stamp tiles and the baked screens are all 4bpp).
  if (h.version != 1 || h.bpp != 4 || (h.flags & FFF_FLAG_INKS)) {
    Serial.printf("bad frame: version=%d bpp=%d flags=0x%02x (want 4bpp gray)\n",
                  h.version, h.bpp, h.flags);
    return false;
  }
#else
  if (h.version != 1 || (h.bpp != 4 && h.bpp != 1) || (h.flags & FFF_FLAG_INKS)) {
    Serial.printf("bad frame: version=%d bpp=%d flags=0x%02x\n", h.version, h.bpp, h.flags);
    return false;
  }
#endif
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
  const int w = h.width, hh = h.height;   // native: 1872 x 1404 (EE02: 1200 x 1600)
  Serial.printf("frame %dx%d bpp=%d\n", w, hh, h.bpp);

#if FF_FULL_REFRESH
  // A plate is kept (960 KB of PSRAM): toasts and the corner mark are stamped
  // into a copy of it, and cleared by painting it again. A baked screen is not.
  if (retain) {
    if (!g_lastFrame) g_lastFrame = (uint8_t*)ps_malloc(FF_SCREEN_BYTES);
    if (g_lastFrame) memcpy(g_lastFrame, body, FF_SCREEN_BYTES);
  }
  g_glassScreen = -1;
  bool painted = false;
  if (retain && g_lastFrame) {
    plateArrived();
    painted = paintPlate();             // the plate, plus a toast armed for it
  }
  if (!painted) fullPaint((const uint8_t*)body);
  panelUnlock();
  // A stuck refresh (W-820): no ETag, so the next poll retries — unless the
  // panel is now held off, when the ETag is kept (no refetch every poll) and
  // the paint is owed until the hold-off ends (fetchFrame).
  g_paintOwed = !g_paintOk;
  if (!g_paintOk && !panelHeldOff()) return false;
  Serial.println(g_paintOk ? "panel updated" : "panel held off: frame kept for later");
  return true;
#else
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
#endif
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

#if FF_FULL_REFRESH
// No windowed update, so a pill is a stamped repaint: the baked tile is blitted
// into a copy of the retained plate and the whole glass refreshed (~30 s);
// clearing it paints the plate again, TOAST_HOLD_MS later. The in-progress
// pills have no tile here — the fetch answers in seconds, so the press is
// acknowledged by its outcome (a new plate, a view, or an outcome pill) and
// exactly one refresh. The corner mark rides the same repaint.
static int  g_toastId = -1;
static bool g_toastArmed = false;     // put the pill on the next plate to arrive

static void stampTile(uint8_t* body, const FfScreenAsset& t, int x, int y, int w, int h) {
  const size_t stride = w / 2, n = stride * h;
  uint8_t* tile = (uint8_t*)ps_malloc(n);
  if (!tile || ff_unpack_n(t.data, t.len, tile, n) != n) {
    Serial.println("stamp: bad tile");
    free(tile);
    return;
  }
  // A frame hung the other way up mirrors the window.
  if (g_flip) { rotate180(tile, n); x = flipX(x, w); y = flipY(y, h); }
  for (int r = 0; r < h; r++)
    memcpy(body + (size_t)(y + r) * (FF_NATIVE_W / 2) + x / 2, tile + r * stride, stride);
  free(tile);
}

// Paint the retained plate with whatever is standing on it.
static bool paintPlate() {
  if (!g_lastFrame) return false;
  const bool toast = g_toast.active && g_toastId >= 0 && ff_toast_tiles[g_toastId].data;
  if (!toast && !g_cornerMark) { fullPaint(g_lastFrame); return true; }
  uint8_t* body = (uint8_t*)ps_malloc(FF_SCREEN_BYTES);
  if (!body) { Serial.println("stamp: no buffer"); return false; }
  memcpy(body, g_lastFrame, FF_SCREEN_BYTES);
  if (g_cornerMark)
    stampTile(body, ff_corner_tiles[g_cornerMark - 1], FF_CORNER_X, FF_CORNER_Y, FF_CORNER_W, FF_CORNER_H);
  if (toast)
    stampTile(body, ff_toast_tiles[g_toastId], FF_TOAST_X, FF_TOAST_Y, FF_TOAST_W, FF_TOAST_H);
  fullPaint(body);
  free(body);
  return true;
}

// Only a plate can be stamped on: a baked screen carries its own state.
static bool repaintPlate() {
  if (g_glassScreen >= 0) return false;
  return paintPlate();
}

// A new plate replaces whatever stood on the old one — contact is back, and
// the press that raised a pill has its answer — unless a pill was armed for it.
static void plateArrived() {
  g_cornerMark = 0;
  g_toast.active = g_toastArmed;
  g_toastArmed = false;
}

void showToast(int t) {
  if (t < 0 || t >= FF_TOAST_COUNT) return;
  if (!ff_toast_tiles[t].data) { Serial.printf("toast %d: not drawn on this panel\n", t); return; }
  if (g_glassScreen >= 0) return;
  g_toastId = t;
  g_toast.active = true;
  if (!repaintPlate()) { g_toast.active = false; return; }
  g_toast.shownAt = millis();          // the hold starts once it is on the glass
  if (!g_alwaysAwake) {
    // No loop() ever clears this toast; drop the ETag so the next wake's fetch
    // returns 200 and repaints the plate over it.
    g_etag[0] = 0;
    prefs.putString("etag", "");
  }
  Serial.printf("toast: %d\n", t);
}

void clearToast() {
  if (!g_toast.active) return;
  g_toast.active = false;
  if (g_glassScreen >= 0) return;       // a baked paint already covered the toast
  if (!g_alwaysAwake) {
    // On the way into deep sleep: a second refresh now would wipe the pill
    // before anyone read it. The next wake repaints the plate instead.
    g_etag[0] = 0;
    prefs.putString("etag", "");
    return;
  }
  repaintPlate();
  Serial.println("toast cleared");
}

// A button wake out of deep sleep has no retained plate to stamp on: fetch the
// plate again (no ETag) with the pill armed, so it arrives stamped — one paint.
static bool toastOnFreshPlate(int t, float vbat, int pct);
#else
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
  if (!g_alwaysAwake) {
    // No loop() ever clears this toast; drop the ETag so the next wake's fetch
    // returns 200 and repaints the plate over it.
    g_etag[0] = 0;
    prefs.putString("etag", "");
  }
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
    // The pill sits where pushTile put it (mirrored when the frame is hung
    // the other way up); the retained frame is already in glass orientation.
    const int tx = flipX(FF_TOAST_X, FF_TOAST_W), ty = flipY(FF_TOAST_Y, FF_TOAST_H);
    const int nx = FF_NATIVE_W - tx - FF_TOAST_W;
    for (int r = 0; r < FF_TOAST_H; r++)
      memcpy(g_tileBuf + r * (FF_TOAST_W / 2),
             g_lastFrame + (uint32_t)(ty + r) * (FF_NATIVE_W / 2) + nx / 2,
             FF_TOAST_W / 2);
    pushTileRaw(g_tileBuf, tx, ty, FF_TOAST_W, FF_TOAST_H);
    panelUnlock();
  } else {
    pushTile(ff_toast_tiles[FF_TOAST_BLANK], FF_TOAST_X, FF_TOAST_Y, FF_TOAST_W, FF_TOAST_H);
  }
  g_toast.active = false;
  Serial.println("toast cleared");
}
#endif

// The hold says so on the glass, once: a baked "Battery low, charge me" pill
// over the plate's bottom margin, left up through the hold. "Once" is kept in
// NVS, and written BEFORE the paint: a cell too flat to survive the paint
// browns out, loses g_lowBatt with the rest of RTC memory, and must not try
// again on every reboot. The ETag goes with it, so the first fetch after the
// charge repaints the whole plate over the pill. `beginMode` is the panel
// init the caller still owes (0 cold, 1 out of deep sleep), -1 if it is up.
static void markLowBattery(int beginMode) {
  if (prefs.getBool("lowmark", false)) return;
  prefs.putBool("lowmark", true);
  g_etag[0] = 0;
  prefs.putString("etag", "");
#if FF_FULL_REFRESH
  // No windowed update to put a pill down with: the same words as a baked
  // full screen, one ~30 s refresh. FF_LOW_BATT_V is higher on this panel so
  // the cell can carry it. (showScreen drops the ETag and sleeps the panel.)
  if (beginMode >= 0) epaper.begin(beginMode);
  showScreen(FF_SCR_LOW_BATT);
  Serial.println("low-battery screen painted");
#else
  if (beginMode >= 0) epaper.begin(beginMode);
  g_loaderAnim.on = false;
  g_toast.active = false;          // goToSleep must not "clear" this one off the glass
  pushTile(ff_toast_tiles[FF_TOAST_LOW_BATTERY], FF_TOAST_X, FF_TOAST_Y, FF_TOAST_W, FF_TOAST_H);
  g_bandKind = g_bandStage = -1;   // over a baked screen the pill took the error band's place
  epaper.sleep();                  // pushTile leaves the T-CON awake; the hold is four hours
  Serial.println("low-battery mark painted");
#endif
}
static void clearLowBatteryMark() {
  if (prefs.getBool("lowmark", false)) prefs.putBool("lowmark", false);
}


// The always-awake model never passes through setup()'s resting read, so it
// watches its own polls: FF_LOW_BATT_POLLS readings in a row under the
// threshold (one sagging sample under a Wi-Fi burst is not an empty cell)
// start the same hold, with the mark, instead of waiting for the brownout.
static bool lowBatteryWhileAwake(float vbat) {
  static uint8_t lows = 0;
  if (vbat < FF_BATT_ABSENT_V || vbat >= FF_LOW_BATT_V) { lows = 0; return false; }
  if (++lows < FF_LOW_BATT_POLLS) return false;
  g_lowBatt = true;
  Serial.printf("battery low (%.2f V, %d polls): holding\n", vbat, (int)lows);
  return true;
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
#if FF_FULL_REFRESH
// Spectra: every screen is a ~30 s full refresh, so the boot sequence is one
// screen (ff_screens_ee02.h): "Connecting" while things proceed normally, and
// a specific error screen only if Wi-Fi or the server fails (W-817). Each
// stage the gray frame names would cost a refresh longer than the stage.
// Painting a baked screen drops the ETag, so the next good fetch repaints the
// plate over it instead of 304-ing.
static uint8_t* g_scrBuf = nullptr;

void freeScreenBuffers() { free(g_scrBuf); g_scrBuf = nullptr; }

void showScreen(int idx) {
  if (idx == FF_SCR_SPLASH || idx == FF_SCR_BOOT_BIRDNET || idx == FF_SCR_BOOT_DOWNLOAD)
    idx = FF_SCR_BOOT_WIFI;                           // every boot stage is "Connecting"
  // A frame no one has claimed goes straight to its pairing code, which is
  // the boot screen with the code on it: a "Connecting" ahead of it would be
  // a second 30 s paint of the same picture.
  if (idx == FF_SCR_BOOT_WIFI && g_unpaired) return;
  if (idx < 0 || idx >= FF_SCR_COUNT || !ff_screens[idx].data) return;
  if (g_glassScreen == idx) return;                 // already on the glass
  const size_t total = FFF_HEADER_SIZE + FF_SCREEN_BYTES;
  if (!g_scrBuf) g_scrBuf = (uint8_t*)ps_malloc(total);
  if (!g_scrBuf) { Serial.println("screen: no buffer"); return; }
  FFFHeader h = {};
  memcpy(h.magic, "FFF1", 4);
  h.version = 1; h.bpp = 4; h.width = FF_NATIVE_W; h.height = FF_NATIVE_H;
  h.flags = FF_FRAME_INKS ? FFF_FLAG_INKS : 0;
  memcpy(g_scrBuf, &h, FFF_HEADER_SIZE);
  uint8_t* body = g_scrBuf + FFF_HEADER_SIZE;
  ff_unpack(ff_screens[idx].data, ff_screens[idx].len, body);
  if (g_flip) rotate180(body, FF_SCREEN_BYTES);
  if (displayFrame(g_scrBuf, total)) {
    g_glassScreen = (int8_t)idx;
    g_etag[0] = 0;
    prefs.putString("etag", "");
    Serial.printf("screen %d full\n", idx);
  }
  freeScreenBuffers();                              // 960 KB back before any fetch
}

void showScreenFull(int idx) { showScreen(idx); }
void showSplash(const char*, int) {}
#else
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

// The splash's version line is this build's own: "v 0.2.4" for a release,
// "dev 2026.09.24" for a dev build (FF_FW_VERSION "2026.09.24+sha"), "dev"
// with no git. The same rule as version_line() in bake_screens.py.
static void splashVersion(char* out, size_t n) {
  const char* v = FF_FW_VERSION;
  const char* plus = strchr(v, '+');
  bool release = !plus && v[0];
  for (const char* c = v; *c && release; c++) release = isdigit((unsigned char)*c) || *c == '.';
  if (plus) snprintf(out, n, "dev %.*s", (int)(plus - v), v);
  else snprintf(out, n, release ? "v %s" : "dev", v);
}

static const FfGlyph* versionGlyph(char ch) {
  for (int i = 0; i < FF_VER_GLYPHS; i++)
    if (ff_ver_glyphs[i].ch == ch) return &ff_ver_glyphs[i];
  return nullptr;
}

// Sets the version line into a decoded (unflipped) splash body, centred as
// draw_engraved centres it. Darkest wins, so the glyphs' edges may overlap.
static void stampVersion(uint8_t* body) {
  char text[32];
  splashVersion(text, sizeof(text));
  int32_t total16 = 0; int count = 0;
  for (const char* c = text; *c; c++) {
    const FfGlyph* g = versionGlyph(*c);
    if (!g) continue;
    total16 += g->adv16 + (count++ ? FF_VER_TRACK16 : 0);
  }
  int32_t pen16 = FF_VER_CX * 16 - total16 / 2;
  const int bytes = FF_VER_BH / 2;
  for (const char* c = text; *c; c++) {
    const FfGlyph* g = versionGlyph(*c);
    if (!g) continue;
    // Portrait columns [px, px+w) are native rows [FF_NATIVE_H - px - w, FF_NATIVE_H - px).
    const int px = (pen16 + 8) / 16 + g->dx;
    const int row0 = FF_NATIVE_H - px - g->w;
    for (int r = 0; g->data && r < g->w; r++) {
      if (row0 + r < 0 || row0 + r >= FF_NATIVE_H) continue;
      uint8_t* d = body + (size_t)(row0 + r) * FF_GRAY_STRIDE + FF_VER_Y0 / 2;
      const uint8_t* s = g->data + (size_t)r * bytes;
      for (int b = 0; b < bytes; b++) {
        const uint8_t hi = min(d[b] >> 4, s[b] >> 4), lo = min(d[b] & 15, s[b] & 15);
        d[b] = (uint8_t)((hi << 4) | lo);
      }
    }
    pen16 += g->adv16 + FF_VER_TRACK16;
  }
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
  if (idx == FF_SCR_SPLASH) stampVersion(body);
  if (g_flip) rotate180(body, FF_SCREEN_BYTES);   // the partial windows below derive from this body

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
#endif  // !FF_FULL_REFRESH

// ---------------------------------------------------------------- fetch
// FETCH_REJECTED: the server answered 200 but the container failed displayFrame's
// checks (wrong size/version). It is an error for the glass and the backoff, but
// the server IS reachable — so OTA still runs, because a format mismatch is
// exactly the thing only a firmware update can fix.
// FETCH_PENDING: the server answered 403 — it serves another frame until its
// owner switches it to this one. Reachable (so OTA runs), nothing to paint.
// FETCH_STUCK: a good frame the panel could not paint (W-820). The ETag stays
// unset so the next poll retries, but it is not the server's fault: no error
// screen, which would only be another refresh of the same stuck panel.
enum FetchResult { FETCH_UPDATED, FETCH_NOCHANGE, FETCH_NOTFOUND, FETCH_NOFRAME, FETCH_ERROR, FETCH_REJECTED, FETCH_PENDING, FETCH_STUCK };

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

// X-Power-Mode / X-Wake-Minutes from the server -> NVS + globals. Absent or
// malformed headers change nothing (an older server, a proxy).
static void applyServedPower(const String& mode, const String& minutes, const String& pollSecs) {
  if (pollSecs.length()) {
    long p = pollSecs.toInt();
    if (p >= FF_POLL_MIN_S && p <= FF_POLL_MAX_S && (uint32_t)p * 1000UL != g_pollMs) {
      g_pollMs = (uint32_t)p * 1000UL;
      prefs.putUInt("poll_s", (uint32_t)p);
      Serial.printf("power: poll every %ld s\n", p);
    }
  }
  if (mode == "awake" || mode == "sleep") {
    bool awake = (mode == "awake");
    if (awake != g_alwaysAwake) {
      g_alwaysAwake = awake;
      prefs.putBool("awake", awake);
      Serial.printf("power: server says %s\n", awake ? "always awake" : "deep sleep");
    }
  }
  if (minutes.length()) {
    long m = minutes.toInt();
    if (m >= FF_MIN_SLEEP_MINUTES && m <= FF_MAX_SLEEP_MINUTES && (uint32_t)m != g_wakeMinutes) {
      g_wakeMinutes = (uint32_t)m;
      prefs.putUInt("wake_min", g_wakeMinutes);
      Serial.printf("power: wake interval now %ld min\n", m);
    }
  }
}

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
  // A plate the panel could not paint (W-820): ask for it whole once the
  // hold-off is over.
  if (g_paintOwed && !panelHeldOff()) g_etag[0] = 0;
  if (resident && strlen(g_etag)) http.addHeader("If-None-Match", String("\"") + g_etag + "\"");
  http.addHeader("X-Battery-Voltage", String(vbat, 3));
  http.addHeader("X-Battery-Percent", String(pct));
  http.addHeader("X-Wifi-RSSI", String(WiFi.RSSI()));
  http.addHeader("X-Wake", g_wakeToken);            // stable token (spec §3)
  // cause=N keys=0xM + ADC diag + last transfer + last mDNS + stuck refreshes (debug)
  http.addHeader("X-Wake-Detail", String(g_wakeInfo) + " " + g_battRaw + " " + g_lastXfer
                                  + (g_mdnsNote[0] ? String(" ") + g_mdnsNote : String(""))
                                  + (g_stuckCount ? String(" panel=stuck") + String((int)g_stuckCount)
                                                    + (panelHeldOff() ? "+held" : "") : String("")));
  http.addHeader("X-FF-Version", FF_FW_VERSION);    // human build id (spec §1)
  http.addHeader("X-FF-Sketch-MD5", ESP.getSketchMD5());  // exact binary id
  http.addHeader("X-Boot-Count", String(g_bootCount));    // spec §5
  http.addHeader("X-Refresh-Count", String(g_refreshCount));
  http.addHeader("X-Device-Id", frameId());         // who we are: one server serves one frame
  http.addHeader("X-FF-Key", frameKey());           // …and that it is really us (W-845)
  http.addHeader("X-Panel", FF_PANEL_ID);           // spec §6; the server renders for it
  http.addHeader("X-Board", FF_BOARD_ID);
  // The panel as facts (W-813): a server that has never heard of FF_PANEL_ID
  // still draws exactly what displayFrame() accepts.
  http.addHeader("X-Panel-Width", String(FF_NATIVE_W));
  http.addHeader("X-Panel-Height", String(FF_NATIVE_H));
  http.addHeader("X-Panel-Format", FF_PANEL_FORMAT);
  http.addHeader("X-Panel-Rotations", FF_PANEL_ROTATIONS);
  // Which way up it hangs now (W-851): the rotation it was last told, which
  // a server that has never seen it starts from — a pairing code, a new row.
  http.addHeader("X-FF-Rotation", String(g_flip ? (FF_BAKED_ROTATION + 180) % 360 : FF_BAKED_ROTATION));
  if (g_mat[0]) http.addHeader("X-FF-Mat", g_mat);   // …and the mat it hangs with
  // We speak push (W-841): "0" = no socket right now, polling as told; N = a
  // socket is open and the next plain check-in is a heartbeat N s away.
  if (g_alwaysAwake)
    http.addHeader("X-FF-Push", pushLive() ? String(FF_PUSH_HEARTBEAT_MS / 1000) : String("0"));
  const char* collect[] = {"ETag", "X-Power-Mode", "X-Wake-Minutes", "X-Poll-Seconds", "X-FF-Frame", "X-FF-Server", "X-FF-Rotation", "X-FF-Mat"};
  http.collectHeaders(collect, 8);

  int code = http.GET();
  Serial.printf("GET %s -> %d\n", url.c_str(), code);
  // The server announces which way up the frame hangs on every response;
  // remember it for the baked screens/tiles (the plates arrive already turned).
  String rot = http.header("X-FF-Rotation");
  if (rot.length()) {
    bool f = (rot.toInt() != FF_BAKED_ROTATION);
    if (f != g_flip) { g_flip = f; prefs.putBool("flip", f); }
  }
  String mat = http.header("X-FF-Mat");
  if (validMat(mat) && mat != g_mat) {
    strlcpy(g_mat, mat.c_str(), sizeof(g_mat));
    prefs.putString("mat", g_mat);
  }
  // The power model and wake interval are set on the config page and ride
  // every response (a 304 too). Stored in NVS; the callers act on the new
  // values at the end of this cycle (W-736/W-456).
  applyServedPower(http.header("X-Power-Mode"), http.header("X-Wake-Minutes"),
                   http.header("X-Poll-Seconds"));
  if (code == HTTP_CODE_NOT_MODIFIED) { http.end(); return FETCH_NOCHANGE; }
  if (code == HTTP_CODE_NOT_FOUND) { http.end(); return FETCH_NOTFOUND; }
  if (code == HTTP_CODE_SERVICE_UNAVAILABLE) { http.end(); return FETCH_NOFRAME; }  // server up, no bird yet
  if (code == HTTP_CODE_FORBIDDEN) {
    // The server is serving another frame and its owner has not switched it
    // to us (yet, or ever: "ignored"). Keep the URL and keep asking — the
    // answer changes the moment they press Switch on the page.
    Serial.printf("not the active frame (%s)\n", http.header("X-FF-Frame").c_str());
    // The server may know the instance that draws for our panel (X-FF-Server);
    // our own one-shot mDNS query does not always see both on one host.
    String other = http.header("X-FF-Server");
    http.end();
    if (other.length() && other.length() < sizeof(g_serverUrl) && other != g_serverUrl) {
      strlcpy(g_redirect, other.c_str(), sizeof(g_redirect));
    }
    return FETCH_PENDING;
  }
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
  // Block copies, never Stream::readBytes: that walks the body a byte at a
  // time through timedRead()/read() (~4 s of CPU for a frame — 20 s at boot
  // with the loader task sharing the core) and blocks inside itself until the
  // whole remainder arrives, so the deadline check below never ran on a
  // slow-but-steady link. read(buf, n) is a memcpy out of the socket buffer and
  // returns as soon as what is there is copied, so the loop bounds the transfer.
  // The deadline is a stall, not a total: a body that is still arriving is
  // never abandoned short of FF_BODY_MAX_MS, because the retry starts over
  // from byte zero (see FF_BODY_STALL_MS in ff_config.h).
  uint32_t lastProgress = t0;
  const char* why = nullptr;
  while (got < len) {
    uint32_t now = millis();
    if (now - lastProgress >= FF_BODY_STALL_MS) { why = "stall"; break; }
    if (now - t0 >= FF_BODY_MAX_MS)           { why = "cap";   break; }
    int avail = stream->available();
    if (avail > 0) {
      int n = stream->read(buf + got, (size_t)min(avail, len - got));
      if (n > 0) { got += n; lastProgress = now; }
    } else if (!stream->connected()) {
      why = "hangup"; break;                // server hung up mid-body: don't sit out the timeout
    } else {
      delay(2);
    }
  }
  uint32_t ms = millis() - t0;
  String newEtag = http.header("ETag");
  http.end();

  // Transfer stats: on serial now, and on the next request's X-Wake-Detail so
  // the server journal shows the rate without a USB attach (which reboots the board).
  snprintf(g_lastXfer, sizeof(g_lastXfer), "xfer=%d/%dB %lums%s%s",
           got, len, (unsigned long)ms, why ? " " : "", why ? why : "");
  Serial.printf("body %d/%d bytes in %lu ms (%.1f KB/s)%s%s\n", got, len, (unsigned long)ms,
                ms ? got / 1.024f / ms : 0.0f, why ? " " : "", why ? why : "");
  if (got != len) { free(buf); return FETCH_ERROR; }

  bool painted = displayFrame(buf, len, true);   // retain: the toast band restores from it
  free(buf);
  if (!painted) return g_paintOk ? FETCH_REJECTED : FETCH_STUCK;   // keep the ETag unset so we retry

  if (resident) {
    // Store the new ETag (strip quotes/W-prefix).
    newEtag.replace("W/", ""); newEtag.replace("\"", ""); newEtag.trim();
    if (newEtag.length()) { strlcpy(g_etag, newEtag.c_str(), sizeof(g_etag)); prefs.putString("etag", g_etag); }
    bool unpaired = strncmp(g_etag, "pair-", 5) == 0;
    if (unpaired != g_unpaired) { g_unpaired = unpaired; prefs.putBool("unpaired", unpaired); }
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
// A hosted server (W-845): no box on the LAN answers https, and a hosted
// frame's hiccup — a redeploy dropping it for a minute — is not its server
// moving. Trading it for whatever answers mDNS put the EE02 back on the box
// two minutes after a redeploy (W-853).
static bool hostedServer() { return strncmp(g_serverUrl, "https://", 8) == 0; }

// A pairing code is on the glass (its ETag is the lobby's, "pair-…"): the
// owner is about to type it, and the next picture is the one they are waiting
// for. Until it comes, "not added yet" is expected, not a failure: keep asking
// at the served pace (10 s), paint no error over the code, and do not let the
// Spectra's repaint floor hold back the picture that replaces it.
static bool pairingOnGlass() { return strncmp(g_etag, "pair-", 5) == 0; }

// Look for the server on the LAN only once it has really stopped answering:
// FF_REDISCOVER_FAILS failed fetches in a row (this one included), and never
// away from a hosted one.
static bool mayRediscover() {
  return !hostedServer() && g_failCount + 1 >= FF_REDISCOVER_FAILS;
}

FetchResult fetchAndRender(const char* path, bool resident, float vbat, int pct) {
  // A resident fetch while a baked screen (or its error band) holds the glass
  // must actually paint: drop the ETag so a healthy server answers 200, not a
  // 304 that would strand the boot art until the bird changes.
  if (resident && g_glassScreen >= 0) g_etag[0] = 0;
  // No URL yet (fresh unit, blank portal field): find the server first.
  if (!g_serverUrl[0] && WiFi.status() == WL_CONNECTED) adoptDiscoveredServer();
  FetchResult r = g_serverUrl[0] ? fetchFrame(path, resident, vbat, pct) : FETCH_ERROR;
  // Couldn't reach it: maybe the box got a new address. One mDNS query, and a
  // single retry if it names somewhere new. A typed URL that still works is
  // never replaced; one that has stopped answering is (mayRediscover).
  if (r == FETCH_ERROR && WiFi.status() == WL_CONNECTED && mayRediscover() && adoptDiscoveredServer())
    r = fetchFrame(path, resident, vbat, pct);
  // This server serves another frame. If the LAN has a second instance, it is
  // the one meant for us; with only this one, we stay and wait to be added.
  if (r == FETCH_PENDING && g_redirect[0]) {
    Serial.printf("server: %s -> %s (its panel's instance)\n", g_serverUrl, g_redirect);
    strlcpy(g_serverUrl, g_redirect, sizeof(g_serverUrl));
    normalizeServerUrl(g_serverUrl, sizeof(g_serverUrl), g_serverUrl);
    prefs.putString("server", g_serverUrl);
    g_redirect[0] = 0;
    r = fetchFrame(path, resident, vbat, pct);
  } else if (r == FETCH_PENDING && !hostedServer() && adoptDiscoveredServer(true)) {
    r = fetchFrame(path, resident, vbat, pct);
  }
  g_loaderAnim.on = false;
  return r;
}

#if FF_FULL_REFRESH
static bool toastOnFreshPlate(int t, float vbat, int pct) {
  g_toastId = t;
  g_toastArmed = true;
  g_etag[0] = 0;
  bool ok = fetchAndRender(FRAME_PATH, true, vbat, pct) == FETCH_UPDATED;
  g_toastArmed = false;
  if (ok) g_toast.shownAt = millis();
  // The pill goes into deep sleep on the glass: the next wake must repaint.
  g_etag[0] = 0;
  prefs.putString("etag", "");
  return ok;
}
#endif

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
  if (r == FETCH_NOFRAME && pairingOnGlass()) return;   // paired; its picture is being drawn
  if (r == FETCH_STUCK) return;                         // the panel's, not the server's (X-Wake-Detail)
  bumpFail();
  int kind = (WiFi.status() != WL_CONNECTED) ? ERRK_WIFI
           : r == FETCH_NOFRAME ? ERRK_NOFRAME
           : r == FETCH_PENDING ? ERRK_PENDING : ERRK_SERVER;
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
  RadioAwake radio;     // a 2.5 MB image is RTT-bound like the frame: 30+ s with modem sleep on
  HTTPClient http;
  String url = String(g_serverUrl) + FIRMWARE_PATH;
  if (!http.begin(url)) return;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_CONNECT_TIMEOUT_MS);
  http.setUserAgent("Featherframe-ESP32/1.0");
  http.addHeader("X-Firmware-MD5", ESP.getSketchMD5());
  http.addHeader("X-Board", FF_BOARD_ID);   // the server never hands over another board's image
  http.addHeader("X-Device-Id", frameId());
  http.addHeader("X-FF-Key", frameKey());
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

// A restart that finds its plate on the glass leaves it there (full-refresh
// panels): no boot screen, and the plate's ETag is kept, so an unchanged
// picture is a 304 and nothing repaints. A stored ETag means exactly that:
// every baked screen and one-off view clears it. On the Spectra each paint is
// ~30 s, and a USB session — the installer, Wi-Fi over Improv, pairing, the
// move to a new server — restarts the frame several times: two paints each,
// ten in a row, some cut short (Wells, 23 Sep 2026). The setup portal and a
// blank board still show their screens.
static bool resumeGlass(bool forcePortal) {
  return FF_FULL_REFRESH && g_etag[0] && !forcePortal && !g_viaPortal;
}

// ---------------------------------------------------------------- setup
void setup() {
  Serial.begin(115200);
  delay(50);
  startImprov();   // answers the USB flasher from the first moment (W-839)
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
  frameKey();              // made (once) before anything asks for it: Improv runs beside setup
  g_prefsReady = true;
  prefs.getString("server", DEFAULT_SERVER_URL).toCharArray(g_serverUrl, sizeof(g_serverUrl));
  normalizeServerUrl(g_serverUrl, sizeof(g_serverUrl), DEFAULT_SERVER_URL);   // older saves may carry a trailing '/'
  prefs.getString("etag", "").toCharArray(g_etag, sizeof(g_etag));
  g_unpaired = prefs.getBool("unpaired", false);
  g_wakeMinutes = prefs.getUInt("wake_min", DEFAULT_WAKE_MINUTES);
  g_alwaysAwake = prefs.getBool("awake", FF_DEFAULT_ALWAYS_AWAKE);
  g_pollMs = prefs.getUInt("poll_s", FF_POLL_INTERVAL_MS / 1000) * 1000UL;
  g_flip = prefs.getBool("flip", false);
  prefs.getString("mat", "").toCharArray(g_mat, sizeof(g_mat));
  Serial.printf("power: %s, wake %u min\n", g_alwaysAwake ? "always awake" : "deep sleep",
                (unsigned)g_wakeMinutes);

  // NOTE: the panel (Seeed_GFX) owns GPIO43 during init/refresh, so do NOT force it
  // here — that breaks the power sequencing and updates stop reaching the glass. We
  // re-assert it HIGH only in the idle loop, after rendering, to keep buttons alive.

  // Panel arbitration + the loading-mark sweep task. Created before any screen
  // shows so the mark animates through Wi-Fi connect, server connect, and the
  // download alike; it idles (no panel traffic) whenever no loader is armed.
  g_panelMutex = xSemaphoreCreateRecursiveMutex();
#if !FF_FULL_REFRESH
  xTaskCreatePinnedToCore(loaderTask, "ffloader", 4096, nullptr, 1, nullptr, 1);
#endif

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
  if (lowBatteryHold(vbat)) {
    markLowBattery(fromDeepSleep ? 1 : 0);
    goToSleep(FF_LOW_BATT_SLEEP_MIN);
    return;
  }
  clearLowBatteryMark();

  if (g_alwaysAwake) {
  // --- Always-awake model: splash now, then Wi-Fi, then poll buttons in loop().
  epaper.begin(0);                          // full init once; the panel stays warm
  armWatchdog();

  // Hold KEY2 at boot -> wipe Wi-Fi/server settings and open the setup portal.
  // Read it here, right after begin(): the splash's update() puts the T-CON
  // to sleep, and the keys don't register while it is (see PIN_PANEL_PWR).
  bool forcePortal = (digitalRead(PIN_PORTAL_RESET) == LOW);
  showSplash(buttonWake ? "button wake" : "booting", pct);

  if (forcePortal) { Serial.println("portal reset requested"); wm.resetSettings(); }

  snprintf(g_wakeInfo, sizeof(g_wakeInfo), "cause=%d nosleep", (int)cause);
  bool resume = resumeGlass(forcePortal);
  ensureWifi(forcePortal, !resume);   // loops the portal itself until first-run setup
  g_lastSuccessMs = millis();
  if (WiFi.status() == WL_CONNECTED) {
    if (!resume) {
      g_etag[0] = 0;   // force a fresh paint so the plate replaces the splash (not a 304)
      showScreen(FF_SCR_BOOT_BIRDNET);          // reaching the server
      showScreen(FF_SCR_BOOT_DOWNLOAD);         // fetching the image
    }
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
  return;                                   // loop() takes over
  }

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

  if (!ensureWifi(forcePortal, !fromDeepSleep && !resumeGlass(forcePortal))) {
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
    if ((!fromDeepSleep || g_viaPortal) && !resumeGlass(false)) {
      showScreen(FF_SCR_BOOT_BIRDNET);
      showScreen(FF_SCR_BOOT_DOWNLOAD);
    }
    r = fetchAndRender(FRAME_PATH, true, vbat, pct);
#if FF_FULL_REFRESH
    if (keyCheck && r == FETCH_NOCHANGE) toastOnFreshPlate(FF_TOAST_UP_TO_DATE, vbat, pct);
#else
    if (keyCheck && r == FETCH_NOCHANGE) showToast(FF_TOAST_UP_TO_DATE);
#endif
  }
  if (buttonWake && (r == FETCH_ERROR || r == FETCH_REJECTED || r == FETCH_NOFRAME || r == FETCH_STUCK)) ackBlink(4);
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
  if (g_alwaysAwake) {
    // The server switched us to always-awake during this wake. The awake
    // model wants its own panel init (begin(0)) and boot flow; a restart is
    // the clean way there and costs one splash.
    Serial.println("power: switching to always awake — restarting");
    Serial.flush();
    ESP.restart();
  }
  goToSleep(g_wakeMinutes);
}

// The always-awake poll clock and the button-view hold. A transient view's
// fetch clears the resident ETag, so without the hold the very next poll
// would repaint the bird over the collage the user just asked for.
static uint32_t g_lastPoll = 0;
static uint32_t g_lastOta = 0;    // last hosted-firmware check from the poll loop
static uint32_t g_viewHoldUntil = 0;

// ---- Push (W-841) ----
// On USB the frame holds one WebSocket to the server it fetches from. Every
// message means "your next GET /api/frame would answer differently" (a new
// plate, a new rotation, a new power model); the GET itself is unchanged, so
// the socket carries no pixels and no settings. Battery frames never open it:
// an open socket keeps Wi-Fi associated. A socket that closes is a check-in at
// once (the server may have let the frame go: its answer is a pairing code, or
// "add this frame"), and the socket is tried again every ~30 s for as long as
// it takes — a frame on USB has nothing better to do, and one that quietly
// fell back to a long timer looked broken. Meanwhile it polls at the served
// interval, which the server keeps short for a frame that speaks push.
static char     g_pushUrl[128] = "";   // the server the socket was opened to ("" = closed)
static String   g_pushHeaders;
static bool     g_pushTried = false;   // the current open has not connected yet

// The next reconnect: FF_PUSH_RETRY_MS and a few seconds of chance, so every
// frame of a server that just restarted does not knock at the same moment.
static uint32_t pushRetryMs() { return FF_PUSH_RETRY_MS + (esp_random() % FF_PUSH_JITTER_MS); }

static void onPush(WStype_t type, uint8_t* payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED:
      g_pushUp = true; g_pushTried = false;
      Serial.println("push: connected");
      break;
    case WStype_DISCONNECTED:
      if (g_pushUp) {
        // An open socket closed: ask now what the server makes of us, rather
        // than wait out the timer (removed → a pairing code; down → the first
        // of the failed checks that lead to the offline mark).
        Serial.println("push: closed — checking in");
        g_pushWake = true;
      }
      g_ws.setReconnectInterval(pushRetryMs());
      g_pushUp = false; g_pushTried = true;
      break;
    case WStype_TEXT: {
      // {"etag":…,"rotation":…,"power":…,"ota":bool}. Any message is a wake;
      // only "ota" is read out of it.
      String m((const char*)payload, len);
      m.replace(" ", "");
      g_pushWake = true;
      if (m.indexOf("\"ota\":true") >= 0) g_pushOta = true;
      break;
    }
    default:
      break;
  }
}

// The push socket runs in a task of its own (W-853). Its reconnect is a
// blocking TLS connect: in loop() a stalled one froze everything — the
// heartbeat, the buttons, the next paint — until the watchdog rebooted the
// frame (seen on both kits after a hosted redeploy dropped their sockets).
// loop() only reads these flags; it asks for a reopen, it never touches g_ws.
static volatile bool     g_pushReopen = false;   // loop(): the server stopped answering
static volatile uint32_t g_pushAliveMs = 0;      // the push task's last turn

// On a socket the timed fetch is only a heartbeat — but only while the push
// task is really turning; a stalled one counts as no socket, and loop() polls.
static bool pushLive() {
  return g_pushUp && millis() - g_pushAliveMs < FF_PUSH_STALL_MS;
}

static void pushClose() {
  if (!g_pushUrl[0]) return;
  g_ws.disconnect();
  g_pushUrl[0] = 0;
  g_pushUp = false;
}

// Keep the socket open to the server we fetch from, and service it. Called
// from loop() on every pass.
static void pushService() {
  if (!g_alwaysAwake || WiFi.status() != WL_CONNECTED || !g_serverUrl[0]) { pushClose(); return; }
  if (g_pushUrl[0] && strcmp(g_pushUrl, g_serverUrl) != 0) pushClose();   // rediscovered: follow it
  if (!g_pushUrl[0]) {
    // "http[s]://host[:port][/prefix]" -> host, port, prefix + FF_PUSH_PATH.
    const char* u = g_serverUrl;
    bool tls = strncmp(u, "https://", 8) == 0;
    u += tls ? 8 : 7;
    const char* slash = strchr(u, '/');
    String hostPort = slash ? String(u).substring(0, slash - u) : String(u);
    String path = String(slash ? slash : "") + FF_PUSH_PATH;
    uint16_t port = tls ? 443 : 80;
    int colon = hostPort.lastIndexOf(':');
    if (colon > 0) { port = hostPort.substring(colon + 1).toInt(); hostPort = hostPort.substring(0, colon); }
    // Who we are, as on every GET: the server keeps only a frame that is on.
    g_pushHeaders = String("X-Device-Id: ") + frameId() + "\r\nX-FF-Key: " + frameKey() +
                    "\r\nX-Panel: " + FF_PANEL_ID +
                    "\r\nX-Board: " + FF_BOARD_ID + "\r\nX-FF-Version: " + FF_FW_VERSION;
    g_ws.setExtraHeaders(g_pushHeaders.c_str());
    g_ws.onEvent(onPush);
    g_ws.setReconnectInterval(pushRetryMs());
    // Pings keep a NAT or proxy from dropping an idle socket. A missed pong
    // never closes it by itself: a ~30 s colour paint holds the loop up, and
    // a dead server shows up as a failed heartbeat GET instead (see loop()).
    g_ws.enableHeartbeat(30000, 10000, 0);
    if (tls) g_ws.beginSSL(hostPort.c_str(), port, path.c_str());
    else     g_ws.begin(hostPort.c_str(), port, path.c_str());
    strlcpy(g_pushUrl, g_serverUrl, sizeof(g_pushUrl));
    g_pushTried = true;
    Serial.printf("push: opening %s:%u%s\n", hostPort.c_str(), (unsigned)port, path.c_str());
  }
  g_ws.loop();
}

static void pushTask(void*) {
  for (;;) {
    g_pushAliveMs = millis();
    if (g_pushReopen) { g_pushReopen = false; pushClose(); }
    pushService();
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

static void startPushTask() {
  static bool started = false;
  if (started) return;
  started = true;
  // TLS on its stack; core 0, beside Improv, away from the panel's loop().
  xTaskCreatePinnedToCore(pushTask, "ffpush", 16384, nullptr, 1, nullptr, 0);
}

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
  if (!g_alwaysAwake) {
    // The server switched us to deep sleep (the poll that learned it has
    // finished). goToSleep clears any pill first.
    Serial.println("power: switching to deep sleep");
    goToSleep(g_wakeMinutes);
  }
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

  // Re-fetch every g_pollMs (served by the page). fetchAndRender sends the stored ETag, so
  // an unchanged frame returns 304 and the panel is not repainted. Failed polls
  // back off to FF_POLL_BACKOFF_MS and keep the error state current. A button-
  // requested view holds the glass for FF_VIEW_HOLD_MS first — the view fetch
  // clears the ETag, so an eager poll would repaint the bird within seconds of
  // the press that asked for the collage.
  startPushTask();
  // On a push socket the timed fetch is only a heartbeat: a change arrives
  // as a message (g_pushWake) and is fetched at once, below.
  uint32_t interval = pairingOnGlass() ? g_pollMs
                    : (g_failCount >= FF_MARK_FAILS) ? FF_POLL_BACKOFF_MS
                    : pushLive() ? FF_PUSH_HEARTBEAT_MS : g_pollMs;
  if (g_viewHoldUntil && (int32_t)(millis() - g_viewHoldUntil) < 0) {
    // transient view on the glass
#if FF_FULL_REFRESH
  } else if (g_glassScreen < 0 && g_lastPaintMs && !pairingOnGlass() &&
             millis() - g_lastPaintMs < FF_MIN_REPAINT_MS) {
    // a plate was painted moments ago: let the panel rest before the next one
#endif
  } else if (g_pushWake || millis() - g_lastPoll >= interval) {
    g_lastPoll = millis();
    g_pushWake = false;
    float vb = readBatteryVoltage();
    if (lowBatteryWhileAwake(vb)) {
      markLowBattery(-1);
      goToSleep(FF_LOW_BATT_SLEEP_MIN);
    }
    FetchResult r = fetchAndRender(FRAME_PATH, true, vb, batteryPercent(vb));
    uint32_t mins = (millis() - g_lastSuccessMs) / 60000UL;
    g_failMinutes = mins > 65535 ? 65535 : (uint16_t)mins;
    noteFetchOutcome(r);
    // A socket to a server that no longer answers is not to be trusted: open
    // it again (or poll, if it will not open).
    if (r == FETCH_ERROR) g_pushReopen = true;
    // The boot-time OTA check is skipped when the server is unreachable; the
    // always-awake build never reboots on its own, so re-check from here —
    // at once when the server has said an update is waiting.
    if (r != FETCH_ERROR && (g_pushOta || millis() - g_lastOta >= FF_OTA_CHECK_MS)) {
      g_lastOta = millis();
      g_pushOta = false;
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
