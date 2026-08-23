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
#include <driver/rtc_io.h>

#include "ff_config.h"

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
char     g_wakeInfo[64] = "";   // "cause=N keys=0xM" — sent to the server for debugging
bool     g_gray = false;        // is the sprite currently in 4-bit gray mode?

// ---------------------------------------------------------------- battery
float readBatteryVoltage() {
  pinMode(PIN_BATTERY_ENABLE, OUTPUT);
  digitalWrite(PIN_BATTERY_ENABLE, HIGH);   // enable divider
  delay(10);
  analogReadResolution(12);
  // median-of-several to reject ADC noise
  uint32_t acc = 0;
  const int N = 16;
  for (int i = 0; i < N; i++) { acc += analogRead(PIN_BATTERY_ADC); delay(2); }
  digitalWrite(PIN_BATTERY_ENABLE, LOW);    // save idle current
  float counts = acc / (float)N;
  return (counts / 4095.0f) * VBAT_SCALE;
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

// ---------------------------------------------------------------- sleep
void goToSleep(uint32_t minutes) {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  // update() already put the T-CON to sleep; e-paper holds its image with the
  // rails off, so there's nothing else to power down.

  // Wake on the user buttons (active-low) and on a timer.
  for (gpio_num_t p : {PIN_KEY0, PIN_KEY1, PIN_KEY2}) rtc_gpio_pullup_en(p);
  esp_sleep_enable_ext1_wakeup(BUTTON_WAKE_MASK, ESP_EXT1_WAKEUP_ANY_LOW);
  esp_sleep_enable_timer_wakeup((uint64_t)minutes * 60ULL * 1000000ULL);

  Serial.printf("Sleeping for %u min (or button)\n", minutes);
  Serial.flush();
  esp_deep_sleep_start();
}

// ---------------------------------------------------------------- wifi
// Panel screens for the Wi-Fi flow (defined later, near the splash).
void showPortalInstructions(const char* apName);
void showWifiStatus(const char* title, const char* detail);

// Paper/ink restyle for the WiFiManager captive portal — injected into <head>,
// overrides the stock blue theme. Kept in PROGMEM to save RAM.
static const char PORTAL_CSS[] PROGMEM = R"CSS(<style>
:root{--bg:#efeae0;--card:#fbf9f4;--ink:#20201d;--muted:#6f685c;--accent:#3f5e46;--line:#ddd6c8}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:ui-serif,Georgia,'Times New Roman',serif;margin:0;padding:26px 16px 60px;line-height:1.5}
h1,h2,h3{font-weight:600;letter-spacing:.01em}
h1{font-size:1.7rem;text-align:center;margin:.1em 0 .6em}
h3{color:var(--muted);font-weight:500}
div,dt,dd{color:var(--ink)}
button{background:var(--accent);color:#fff;border:0;border-radius:12px;padding:13px 16px;font-size:1.02rem;width:100%;margin-top:12px;cursor:pointer}
button:hover{filter:brightness(1.07)}
input,select{background:var(--card);border:1px solid var(--line);border-radius:12px;color:var(--ink);padding:12px;width:100%;font-size:1rem}
a,a:visited{color:var(--accent);text-decoration:none}
.msg{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;color:var(--muted)}
.q{filter:grayscale(1) opacity(.7)}
</style>)CSS";

bool ensureWifi(bool openPortal) {
  WiFi.mode(WIFI_STA);
  wm.setTitle("Featherframe");
  wm.setCustomHeadElement(PORTAL_CSS);
  WiFiManagerParameter serverParam("server", "Featherframe server URL",
                                   g_serverUrl, sizeof(g_serverUrl));
  wm.addParameter(&serverParam);
  wm.setConfigPortalTimeout(PORTAL_TIMEOUT_S);
  wm.setConnectTimeout(WIFI_CONNECT_TIMEOUT_MS / 1000);

  // Panel: show setup steps when the captive portal (AP) opens, and "Connecting"
  // the moment the user saves their network from it.
  wm.setAPCallback([](WiFiManager*) { showPortalInstructions("Featherframe-Setup"); });
  wm.setSaveConfigCallback([]() { showWifiStatus("Connecting", ""); });

  bool ok;
  if (openPortal) {
    ok = wm.startConfigPortal("Featherframe-Setup");
  } else {
    String ss = wm.getWiFiSSID();                 // stored network, if any
    showWifiStatus("Connecting", ss.length() ? ss.c_str() : "");
    ok = wm.autoConnect("Featherframe-Setup");    // portal (AP callback) only if it fails
  }
  if (ok) {
    showWifiStatus("Connected", WiFi.localIP().toString().c_str());
    delay(1500);                                  // let it read before the plate paints
    // Persist the (possibly updated) server URL.
    strlcpy(g_serverUrl, serverParam.getValue(), sizeof(g_serverUrl));
    prefs.putString("server", g_serverUrl);
  }
  return ok && WiFi.status() == WL_CONNECTED;
}

// ---------------------------------------------------------------- display
// The server sends the framebuffer already in the panel's NATIVE landscape
// orientation (1872x1404) and in the panel's exact packing:
//   4bpp: 2 px/byte, high nibble = left/even pixel, value 0..15, 0=black 15=white
//   1bpp: 8 px/byte, MSB = left pixel, bit set = white
// EPaper is a TFT_eSprite whose buffer uses that same layout, so we can push the
// whole thing with pushImage() (a per-row memcpy at rotation 0 / even width) and
// then update() for a full refresh — no per-pixel work.
void displayFrame(const uint8_t* data, size_t len) {
  if (len < FFF_HEADER_SIZE) return;
  FFFHeader h;
  memcpy(&h, data, FFF_HEADER_SIZE);
  if (memcmp(h.magic, "FFF1", 4) != 0) { Serial.println("bad frame magic"); return; }

  const uint16_t* body = (const uint16_t*)(data + FFF_HEADER_SIZE);
  const int w = h.width, hh = h.height;   // native: 1872 x 1404
  Serial.printf("frame %dx%d bpp=%d\n", w, hh, h.bpp);

  if (h.bpp == 4) {
    epaper.initGrayMode(GRAY_LEVEL16);      // reallocates the 4bpp gray sprite
    g_gray = true;
    epaper.fillSprite(TFT_GRAY_15);         // white ground (buffer was realloc'd)
    epaper.pushImage(0, 0, w, hh, (uint16_t*)body);
  } else {                                  // 1-bit fallback (default sprite depth)
    g_gray = false;
    epaper.fillScreen(TFT_WHITE);
    epaper.pushImage(0, 0, w, hh, (uint16_t*)body);
  }
  epaper.update();                          // full refresh; brackets its own power
  Serial.println("panel updated");
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

// A dark rounded pill with white text at the bottom of the frame, pushed via a
// fast 1-bit partial refresh so only the pill's own box is touched — no full-width
// band. The panel is fully inited once at boot (no per-toast begin), so this is
// sub-second and never hangs. Change TOAST_ROT if the pill lands wrong-way-up.
#define TOAST_ROT   3
struct ToastBox { bool active; uint32_t shownAt; int x, y, w, h; };
static ToastBox g_toast = {false, 0, 0, 0, 0};

// updataPartial reads the sprite as 1-bit; the plate is drawn in 4-bit gray, so
// drop back to 1-bit before drawing a pill. The next displayFrame re-inits gray.
static void ensure1bit() {
#ifdef USE_MUTIGRAY_EPAPER
  if (g_gray) { epaper.deinitGrayMode(); g_gray = false; }
#endif
}

void showToast(const char* text) {
  ensure1bit();
  const uint8_t savedRot = epaper.getRotation();
  epaper.setRotation(TOAST_ROT);
  const int W = epaper.width();       // 1404 portrait

  epaper.setTextDatum(MC_DATUM);
  epaper.setTextFont(4);
  epaper.setTextSize(2);
  const int tw = epaper.textWidth(text);
  const int pillW = tw + 96;
  const int pillH = 96;
  const int pillX = (W - pillW) / 2;
  const int pillY = epaper.height() - pillH - 54;   // sit in the bottom margin

  // Only the pill's box is refreshed. Its corners go white; they land in the
  // plate's light bottom margin, so they blend rather than reading as a box.
  epaper.fillRect(pillX, pillY, pillW, pillH, TFT_WHITE);
  epaper.fillRoundRect(pillX, pillY, pillW, pillH, pillH / 2, TFT_BLACK);
  epaper.setTextColor(TFT_WHITE, TFT_BLACK);
  epaper.drawString(text, W / 2, pillY + pillH / 2);
  epaper.updataPartial(pillX, pillY, pillW, pillH);
  epaper.setRotation(savedRot);

  g_toast = {true, millis(), pillX, pillY, pillW, pillH};
  Serial.printf("toast: %s\n", text);
}

// Wipe the pill back to white (restores the bottom margin) and forget it.
void clearToast() {
  if (!g_toast.active) return;
  ensure1bit();
  const uint8_t savedRot = epaper.getRotation();
  epaper.setRotation(TOAST_ROT);
  epaper.fillRect(g_toast.x, g_toast.y, g_toast.w, g_toast.h, TFT_WHITE);
  epaper.updataPartial(g_toast.x, g_toast.y, g_toast.w, g_toast.h);
  epaper.setRotation(savedRot);
  g_toast.active = false;
  Serial.println("toast cleared");
}

// ---------------------------------------------------------------- splash
// Shown as early as possible on boot so the device visibly reacts. Uses the same
// proven path as the toast: draw 1-bit at rotation TOAST_ROT and push with a
// full-screen partial refresh (full 1-bit update() doesn't render on this panel,
// and rotating the gray sprite crashes). The plate paint replaces it soon after.
void showSplash(const char* line2, int pct) {
  // 1-bit so it can be drawn upright (gray-mode drawPixel ignores rotation and
  // writes out of bounds); full 1-bit update() is Seeed's own HelloWorld path.
  ensure1bit();
  const uint8_t savedRot = epaper.getRotation();
  epaper.setRotation(TOAST_ROT);       // upright portrait
  const int W = epaper.width();        // 1404
  const int H = epaper.height();       // 1872
  const int cx = W / 2, cy = H / 2;
  epaper.fillScreen(TFT_WHITE);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextDatum(MC_DATUM);

  epaper.setTextFont(4);
  epaper.setTextSize(5);
  epaper.drawString("Featherframe", cx, cy - 150);          // wordmark
  epaper.fillRect(cx - 280, cy - 55, 560, 3, TFT_BLACK);    // hairline rule
  epaper.setTextSize(2);
  epaper.drawString("the birds you heard, as plates", cx, cy + 5);

  epaper.setTextSize(3);
  epaper.drawString(line2, cx, cy + 170);                   // status

  char b[64];
  snprintf(b, sizeof(b), "build %s   -   battery %d%%",
           ESP.getSketchMD5().substring(0, 8).c_str(), pct);
  epaper.setTextSize(2);
  epaper.drawString(b, cx, H - 150);                        // footer

  epaper.update();
  epaper.setRotation(savedRot);
  Serial.printf("splash: %s / %s\n", line2, b);
}

// A big centered status word with an optional detail line (Connecting / Connected).
void showWifiStatus(const char* title, const char* detail) {
  ensure1bit();
  const uint8_t savedRot = epaper.getRotation();
  epaper.setRotation(TOAST_ROT);
  const int cx = epaper.width() / 2, cy = epaper.height() / 2;
  epaper.fillScreen(TFT_WHITE);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextDatum(MC_DATUM);
  epaper.setTextFont(4);
  epaper.setTextSize(5);
  epaper.drawString(title, cx, detail && detail[0] ? cy - 50 : cy);
  if (detail && detail[0]) {
    epaper.setTextSize(3);
    epaper.drawString(detail, cx, cy + 90);
  }
  epaper.update();
  epaper.setRotation(savedRot);
  Serial.printf("panel: %s %s\n", title, detail ? detail : "");
}

// Step-by-step instructions shown on the glass while the setup portal is open.
void showPortalInstructions(const char* apName) {
  ensure1bit();
  const uint8_t savedRot = epaper.getRotation();
  epaper.setRotation(TOAST_ROT);
  const int W = epaper.width(), cx = W / 2;
  epaper.fillScreen(TFT_WHITE);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextDatum(MC_DATUM);

  epaper.setTextFont(4);
  epaper.setTextSize(4);
  epaper.drawString("Set up Wi-Fi", cx, 320);
  epaper.fillRect(cx - 250, 400, 500, 3, TFT_BLACK);

  epaper.setTextSize(2);
  epaper.drawString("On your phone or laptop,", cx, 560);
  epaper.drawString("join this Wi-Fi network:", cx, 630);
  epaper.setTextSize(4);
  epaper.drawString(apName, cx, 760);

  epaper.setTextSize(2);
  epaper.drawString("A setup page opens automatically.", cx, 950);
  epaper.drawString("If not, visit  192.168.4.1", cx, 1020);
  epaper.drawString("Then pick your home network.", cx, 1160);

  epaper.update();
  epaper.setRotation(savedRot);
  Serial.printf("panel: portal instructions (%s)\n", apName);
}

// ---------------------------------------------------------------- fetch
enum FetchResult { FETCH_UPDATED, FETCH_NOCHANGE, FETCH_NOTFOUND, FETCH_ERROR };

// Fetch a frame. `path`: endpoint under the server URL. `resident`: true for
// the normal current-bird frame (ETag conditional GET + store the new ETag);
// false for transient button views (no conditional, and the stored ETag is
// CLEARED so the next timer wake re-fetches the resident bird over the view).
FetchResult fetchAndRender(const char* path, bool resident, float vbat, int pct) {
  HTTPClient http;
  String url = String(g_serverUrl) + path;
  if (!http.begin(url)) return FETCH_ERROR;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setUserAgent("Featherframe-ESP32/1.0");
  if (resident && strlen(g_etag)) http.addHeader("If-None-Match", String("\"") + g_etag + "\"");
  http.addHeader("X-Battery-Voltage", String(vbat, 3));
  http.addHeader("X-Battery-Percent", String(pct));
  http.addHeader("X-Wifi-RSSI", String(WiFi.RSSI()));
  http.addHeader("X-Wake", g_wakeInfo);
  const char* collect[] = {"ETag"};
  http.collectHeaders(collect, 1);

  int code = http.GET();
  Serial.printf("GET %s -> %d\n", url.c_str(), code);
  if (code == HTTP_CODE_NOT_MODIFIED) { http.end(); return FETCH_NOCHANGE; }
  if (code == HTTP_CODE_NOT_FOUND) { http.end(); return FETCH_NOTFOUND; }
  if (code != HTTP_CODE_OK) { http.end(); return FETCH_ERROR; }

  int len = http.getSize();
  if (len <= (int)FFF_HEADER_SIZE) { http.end(); return FETCH_ERROR; }

  uint8_t* buf = (uint8_t*)ps_malloc(len);        // frame lives in PSRAM
  if (!buf) { Serial.println("ps_malloc failed"); http.end(); return FETCH_ERROR; }

  WiFiClient* stream = http.getStreamPtr();
  int got = 0;
  uint32_t t0 = millis();
  while (got < len && (millis() - t0) < HTTP_TIMEOUT_MS) {
    if (stream->available()) {
      got += stream->readBytes(buf + got, len - got);
    } else {
      delay(2);
    }
  }
  String newEtag = http.header("ETag");
  http.end();

  if (got != len) { Serial.printf("short read %d/%d\n", got, len); free(buf); return FETCH_ERROR; }

  displayFrame(buf, len);
  free(buf);

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

// ---------------------------------------------------------------- ota
// Pull-based OTA on every wake: offer the running sketch's MD5; the server
// answers 304 (same build hosted) or 200 with a new firmware.bin, which we
// stream into the spare OTA slot and reboot into. No USB, no user.
void maybeOTA() {
  HTTPClient http;
  String url = String(g_serverUrl) + FIRMWARE_PATH;
  if (!http.begin(url)) return;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setUserAgent("Featherframe-ESP32/1.0");
  http.addHeader("X-Firmware-MD5", ESP.getSketchMD5());
  int code = http.GET();
  Serial.printf("OTA check -> %d\n", code);
  if (code != HTTP_CODE_OK) { http.end(); return; }

  int len = http.getSize();
  if (len <= 0 || !Update.begin(len)) { http.end(); return; }
  Serial.printf("OTA: flashing %d bytes\n", len);
  size_t written = Update.writeStream(*http.getStreamPtr());
  http.end();
  if (written == (size_t)len && Update.end()) {
    Serial.println("OTA ok — rebooting into new firmware");
    Serial.flush();
    ESP.restart();
  }
  Serial.printf("OTA failed: %s\n", Update.errorString());
  Update.abort();
}

// ---------------------------------------------------------------- setup
void setup() {
  Serial.begin(115200);
  delay(50);
  // TEMP boot-ping: 6s of prints after USB settles, so a late reader confirms the
  // app is actually running and where setup gets to. Remove once serial is trusted.
  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
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
    char buf[32];
    snprintf(buf, sizeof(buf), "Up to date  %d", n);
    Serial.printf("[%d] showToast start\n", n);
    uint32_t t0 = millis();
    showToast(buf);
    Serial.printf("[%d] showToast done in %lu ms\n", n, (unsigned long)(millis() - t0));
    delay(3000);
  }
#endif

  prefs.begin("featherframe", false);
  prefs.getString("server", DEFAULT_SERVER_URL).toCharArray(g_serverUrl, sizeof(g_serverUrl));
  prefs.getString("etag", "").toCharArray(g_etag, sizeof(g_etag));
  g_wakeMinutes = prefs.getUInt("wake_min", DEFAULT_WAKE_MINUTES);

  // NOTE: the panel (Seeed_GFX) owns GPIO43 during init/refresh, so do NOT force it
  // here — that breaks the power sequencing and updates stop reaching the glass. We
  // re-assert it HIGH only in the idle loop, after rendering, to keep buttons alive.

  // Release the button pins from any lingering RTC-IO / hold state left by a prior
  // deep-sleep (ext1 wake config), then set them up as digital inputs with pullups.
  for (gpio_num_t p : {PIN_KEY0, PIN_KEY1, PIN_KEY2}) {
    rtc_gpio_hold_dis(p);
    rtc_gpio_deinit(p);
    pinMode(p, INPUT_PULLUP);
  }

#if FF_NO_SLEEP
  // --- Always-awake dev model: splash now, then Wi-Fi, then poll buttons in loop().
  epaper.begin(0);                          // full init once; the panel stays warm

  float vbat = readBatteryVoltage();
  int pct = batteryPercent(vbat);
  Serial.printf("battery: %.3f V (%d%%)\n", vbat, pct);
  showSplash(buttonWake ? "button wake" : "booting", pct);

  // Hold KEY2 at boot -> wipe Wi-Fi/server settings and open the setup portal.
  bool forcePortal = (digitalRead(PIN_PORTAL_RESET) == LOW);
  if (forcePortal) { Serial.println("portal reset requested"); wm.resetSettings(); }

  snprintf(g_wakeInfo, sizeof(g_wakeInfo), "cause=%d nosleep", (int)cause);
  if (ensureWifi(forcePortal)) {
    g_etag[0] = 0;   // force a fresh paint so the plate replaces the splash (not a 304)
    fetchAndRender(FRAME_PATH, true, vbat, pct);   // paint the current bird
    maybeOTA();
  } else {
    showToast("No Wi-Fi");
  }
  Serial.println("ready — polling buttons");
#else
  // --- Deep-sleep model: decode the waking button, act once, sleep.
  esp_task_wdt_init(WDT_TIMEOUT_S, true);   // reboot if a wake cycle hangs
  esp_task_wdt_add(NULL);

  uint64_t keyBits = buttonWake ? esp_sleep_get_ext1_wakeup_status() : 0;
  bool keyCheck   = keyBits & (1ULL << PIN_KEY0);
  bool keyCollage = keyBits & (1ULL << PIN_KEY1);
  bool keyStatus  = keyBits & (1ULL << PIN_KEY2);
  snprintf(g_wakeInfo, sizeof(g_wakeInfo), "cause=%d keys=0x%llx", (int)cause,
           (unsigned long long)keyBits);

  bool forcePortal = (!buttonWake && digitalRead(PIN_PORTAL_RESET) == LOW);
  if (keyStatus) {
    uint32_t t0 = millis();
    while (digitalRead(PIN_PORTAL_RESET) == LOW && millis() - t0 < PORTAL_HOLD_MS) delay(20);
    if (millis() - t0 >= PORTAL_HOLD_MS) { forcePortal = true; keyStatus = false; }
  }
  if (forcePortal) { Serial.println("portal reset requested"); wm.resetSettings(); }

  epaper.begin(fromDeepSleep ? 1 : 0);
  float vbat = readBatteryVoltage();
  int pct = batteryPercent(vbat);
  Serial.printf("battery: %.3f V (%d%%)\n", vbat, pct);

  if (!ensureWifi(forcePortal)) {
    Serial.println("no wifi — sleeping");
    if (buttonWake) ackBlink(4);
    goToSleep(g_wakeMinutes);
    return;
  }

  FetchResult r;
  if (keyCollage) {
    r = fetchAndRender(VIEW_COLLAGE_PATH, false, vbat, pct);
    if (r == FETCH_NOTFOUND) ackBlink(4);
  } else if (keyStatus) {
    r = fetchAndRender(VIEW_STATUS_PATH, false, vbat, pct);
  } else {
    r = fetchAndRender(FRAME_PATH, true, vbat, pct);
    if (keyCheck && r == FETCH_NOCHANGE) showToast("Up to date");
  }
  if (buttonWake && r == FETCH_ERROR) ackBlink(4);
  maybeOTA();
  goToSleep(g_wakeMinutes);
#endif
}

#if FF_NO_SLEEP
// Run a button's action: an instant pill for feedback, then fetch + paint. A new
// plate paints over the pill; on a no-change check the pill becomes "Up to date".
void doButton(int key) {
  float vbat = readBatteryVoltage();
  int pct = batteryPercent(vbat);
  if (key == 0) {                             // KEY0: check now
    showToast("Checking");
    FetchResult r = fetchAndRender(FRAME_PATH, true, vbat, pct);
    if (r == FETCH_NOCHANGE)      showToast("Up to date");
    else if (r == FETCH_UPDATED)  g_toast.active = false;   // new plate replaced it
    else                          showToast("Check failed");
  } else if (key == 1) {                      // KEY1: collage
    showToast("Collage");
    FetchResult r = fetchAndRender(VIEW_COLLAGE_PATH, false, vbat, pct);
    if (r == FETCH_NOTFOUND)      showToast("No collage yet");
    else if (r == FETCH_UPDATED)  g_toast.active = false;
    else                          showToast("Collage failed");
  } else {                                    // KEY2 tap: status
    showToast("Status");
    FetchResult r = fetchAndRender(VIEW_STATUS_PATH, false, vbat, pct);
    if (r == FETCH_UPDATED)       g_toast.active = false;
    else                          showToast("Status failed");
  }
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
  int k = pollButton();
  if (k == 2) {
    // KEY2: hold PORTAL_HOLD_MS -> setup portal; a quick tap -> status view.
    uint32_t t0 = millis();
    while (digitalRead(PIN_KEY2) == LOW && millis() - t0 < PORTAL_HOLD_MS) delay(20);
    if (millis() - t0 >= PORTAL_HOLD_MS) {
      showToast("Setup portal");
      wm.resetSettings();
      if (ensureWifi(true)) {
        fetchAndRender(FRAME_PATH, true, readBatteryVoltage(), batteryPercent(readBatteryVoltage()));
      }
    } else {
      doButton(2);
    }
  } else if (k >= 0) {
    doButton(k);
  }
  if (g_toast.active && millis() - g_toast.shownAt >= TOAST_HOLD_MS) clearToast();

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
