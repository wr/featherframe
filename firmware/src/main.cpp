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
#include <WiFiManager.h>
#include <Preferences.h>
#include <esp_sleep.h>
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
bool ensureWifi(bool openPortal) {
  WiFi.mode(WIFI_STA);
  WiFiManagerParameter serverParam("server", "Featherframe server URL",
                                   g_serverUrl, sizeof(g_serverUrl));
  wm.addParameter(&serverParam);
  wm.setConfigPortalTimeout(PORTAL_TIMEOUT_S);
  wm.setConnectTimeout(WIFI_CONNECT_TIMEOUT_MS / 1000);

  bool ok;
  if (openPortal) {
    ok = wm.startConfigPortal("Featherframe-Setup");
  } else {
    ok = wm.autoConnect("Featherframe-Setup");   // portal only if no creds/fail
  }
  if (ok) {
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
    epaper.fillSprite(TFT_GRAY_15);         // white ground (buffer was realloc'd)
    epaper.pushImage(0, 0, w, hh, (uint16_t*)body);
  } else {                                  // 1-bit fallback (default sprite depth)
    epaper.fillScreen(TFT_WHITE);
    epaper.pushImage(0, 0, w, hh, (uint16_t*)body);
  }
  epaper.update();                          // full refresh; brackets its own power
  Serial.println("panel updated");
}

// ---------------------------------------------------------------- fetch
enum FetchResult { FETCH_UPDATED, FETCH_NOCHANGE, FETCH_ERROR };

FetchResult fetchAndRender(float vbat, int pct) {
  HTTPClient http;
  String url = String(g_serverUrl) + FRAME_PATH;
  if (!http.begin(url)) return FETCH_ERROR;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setUserAgent("Featherframe-ESP32/1.0");
  if (strlen(g_etag)) http.addHeader("If-None-Match", String("\"") + g_etag + "\"");
  http.addHeader("X-Battery-Voltage", String(vbat, 3));
  http.addHeader("X-Battery-Percent", String(pct));
  const char* collect[] = {"ETag"};
  http.collectHeaders(collect, 1);

  int code = http.GET();
  Serial.printf("GET %s -> %d\n", url.c_str(), code);
  if (code == HTTP_CODE_NOT_MODIFIED) { http.end(); return FETCH_NOCHANGE; }
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

  // Store the new ETag (strip quotes/W-prefix).
  newEtag.replace("W/", ""); newEtag.replace("\"", ""); newEtag.trim();
  if (newEtag.length()) { strlcpy(g_etag, newEtag.c_str(), sizeof(g_etag)); prefs.putString("etag", g_etag); }
  return FETCH_UPDATED;
}

// ---------------------------------------------------------------- setup
void setup() {
  Serial.begin(115200);
  delay(50);
  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  bool buttonWake = (cause == ESP_SLEEP_WAKEUP_EXT1);
  bool fromDeepSleep = (cause == ESP_SLEEP_WAKEUP_TIMER || cause == ESP_SLEEP_WAKEUP_EXT1);
  Serial.printf("\nFeatherframe wake: cause=%d (%s)\n", cause, buttonWake ? "button" : "timer/boot");

  prefs.begin("featherframe", false);
  prefs.getString("server", DEFAULT_SERVER_URL).toCharArray(g_serverUrl, sizeof(g_serverUrl));
  prefs.getString("etag", "").toCharArray(g_etag, sizeof(g_etag));
  g_wakeMinutes = prefs.getUInt("wake_min", DEFAULT_WAKE_MINUTES);

  // Hold KEY2 at boot -> wipe settings and force the setup portal.
  pinMode(PIN_PORTAL_RESET, INPUT_PULLUP);
  bool forcePortal = (digitalRead(PIN_PORTAL_RESET) == LOW);
  if (forcePortal) { Serial.println("portal reset requested"); wm.resetSettings(); }

  // Fast re-init (ED103TC2_Init_Wake.h) after a deep-sleep wake; full init cold.
  epaper.begin(fromDeepSleep ? 1 : 0);

  float vbat = readBatteryVoltage();
  int pct = batteryPercent(vbat);
  Serial.printf("battery: %.3f V (%d%%)\n", vbat, pct);

  if (!ensureWifi(forcePortal)) {
    Serial.println("no wifi — sleeping");
    goToSleep(g_wakeMinutes);
    return;
  }

  FetchResult r = fetchAndRender(vbat, pct);
  Serial.printf("fetch: %d\n", r);

  goToSleep(g_wakeMinutes);
}

void loop() {}   // never reached — deep sleep model
