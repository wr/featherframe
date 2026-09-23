// Improv Wi-Fi Serial v1 (see ff_improv.h). One task, no allocation after
// start: packets are small (an SSID and a password at most).
#include "ff_improv.h"

#include <Arduino.h>
#include <WiFi.h>

namespace {

// Message types.
constexpr uint8_t TYPE_CURRENT_STATE = 0x01;
constexpr uint8_t TYPE_ERROR_STATE   = 0x02;
constexpr uint8_t TYPE_RPC           = 0x03;
constexpr uint8_t TYPE_RPC_RESULT    = 0x04;
// States.
constexpr uint8_t STATE_READY        = 0x02;
constexpr uint8_t STATE_PROVISIONING = 0x03;
constexpr uint8_t STATE_PROVISIONED  = 0x04;
// Errors.
constexpr uint8_t ERR_NONE           = 0x00;
constexpr uint8_t ERR_INVALID_RPC    = 0x01;
constexpr uint8_t ERR_UNKNOWN_RPC    = 0x02;
constexpr uint8_t ERR_UNABLE_TO_CONNECT = 0x03;
// RPC commands.
constexpr uint8_t CMD_WIFI_SETTINGS  = 0x01;
constexpr uint8_t CMD_CURRENT_STATE  = 0x02;
constexpr uint8_t CMD_INFO           = 0x03;
constexpr uint8_t CMD_WIFI_NETWORKS  = 0x04;
// Featherframe's own (W-848), outside the range Improv defines: [url] ->
// the frame's identity; a new url is kept and the frame restarts onto it.
constexpr uint8_t CMD_FF_SERVER      = 0xF0;

constexpr char HEADER[] = "IMPROV";
constexpr uint8_t VERSION = 1;
constexpr uint32_t JOIN_TIMEOUT_MS = 20000;
// A saved network is still being joined for this long after boot: say
// PROVISIONING, not READY, so the flasher does not ask for Wi-Fi the frame
// already has.
constexpr uint32_t BOOT_JOIN_MS = 30000;

const char* g_name = "";
const char* g_version = "";
const char* g_board = "";
ImprovHooks g_hooks = {};
// The last state we told the host was not PROVISIONED: say so when it changes.
bool g_owesProvisioned = false;

void sendPacket(uint8_t type, const uint8_t* data, uint8_t len) {
  uint8_t out[6 + 3 + 255 + 2];
  size_t n = 0;
  memcpy(out, HEADER, 6); n = 6;
  out[n++] = VERSION;
  out[n++] = type;
  out[n++] = len;
  memcpy(out + n, data, len); n += len;
  uint8_t sum = 0;
  for (size_t i = 0; i < n; i++) sum += out[i];
  out[n++] = sum;
  out[n++] = '\n';
  Serial.write(out, n);   // one write: log lines never land inside a packet
  Serial.flush();
}

void sendState(uint8_t state) {
  sendPacket(TYPE_CURRENT_STATE, &state, 1);
  g_owesProvisioned = (state != STATE_PROVISIONED);
}

void sendError(uint8_t err) { sendPacket(TYPE_ERROR_STATE, &err, 1); }

// An RPC result: the command, then length-prefixed strings.
void sendResult(uint8_t cmd, const char* const* strs, size_t count) {
  uint8_t data[255];
  size_t n = 2;
  for (size_t i = 0; i < count; i++) {
    size_t l = strnlen(strs[i], 64);
    if (n + 1 + l > sizeof(data)) break;
    data[n++] = (uint8_t)l;
    memcpy(data + n, strs[i], l); n += l;
  }
  data[0] = cmd;
  data[1] = (uint8_t)(n - 2);
  sendPacket(TYPE_RPC_RESULT, data, (uint8_t)n);
}

// Where the flasher sends the owner next: the dashboard, once it is known.
void sendNextUrl(uint8_t cmd) {
  const char* url = g_hooks.serverUrl ? g_hooks.serverUrl() : "";
  if (url && url[0]) {
    const char* strs[] = {url};
    sendResult(cmd, strs, 1);
  } else {
    sendResult(cmd, nullptr, 0);
  }
}

bool connected() { return WiFi.status() == WL_CONNECTED; }

void answerState() {
  if (connected()) {
    sendState(STATE_PROVISIONED);
    sendNextUrl(CMD_CURRENT_STATE);
  } else if (g_hooks.hasSavedWifi && g_hooks.hasSavedWifi() && millis() < BOOT_JOIN_MS) {
    sendState(STATE_PROVISIONING);
  } else {
    sendState(STATE_READY);
  }
}

void join(const char* ssid, const char* pass) {
  sendState(STATE_PROVISIONING);
  Serial.printf("Improv: joining \"%s\"\n", ssid);
  WiFi.persistent(true);                      // kept in NVS, as the portal keeps it
  if (!(WiFi.getMode() & WIFI_STA)) WiFi.mode(WIFI_AP_STA);   // the portal's AP stays up
  WiFi.begin(ssid, pass);
  uint32_t t0 = millis();
  while (!connected() && millis() - t0 < JOIN_TIMEOUT_MS) vTaskDelay(pdMS_TO_TICKS(100));
  if (!connected()) {
    Serial.println("Improv: could not join");
    sendError(ERR_UNABLE_TO_CONNECT);
    sendState(STATE_READY);
    return;
  }
  sendError(ERR_NONE);
  sendState(STATE_PROVISIONED);
  sendNextUrl(CMD_WIFI_SETTINGS);
  bool handled = g_hooks.onJoined && g_hooks.onJoined();
  if (!handled) {
    // Already running on another network: start again on this one, after the
    // host has read the answer.
    vTaskDelay(pdMS_TO_TICKS(1500));
    ESP.restart();
  }
}

void scan() {
  int n = WiFi.scanNetworks(false, false);
  for (int i = 0; i < n && i < 30; i++) {
    String ssid = WiFi.SSID(i);
    if (!ssid.length()) continue;
    char rssi[8];
    snprintf(rssi, sizeof(rssi), "%d", (int)WiFi.RSSI(i));
    const char* strs[] = {ssid.c_str(), rssi,
                          WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "NO" : "YES"};
    sendResult(CMD_WIFI_NETWORKS, strs, 3);
  }
  WiFi.scanDelete();
  sendResult(CMD_WIFI_NETWORKS, nullptr, 0);   // the end of the list
}

// One RPC packet's data: [command, length, payload...].
void handleRpc(const uint8_t* d, uint8_t len) {
  if (len < 2 || d[1] != len - 2) { sendError(ERR_INVALID_RPC); return; }
  const uint8_t* p = d + 2;
  switch (d[0]) {
    case CMD_WIFI_SETTINGS: {
      uint8_t sl = p[0];
      if (2 + 1 + sl + 1 > len) { sendError(ERR_INVALID_RPC); return; }
      uint8_t pl = p[1 + sl];
      if (2 + 1 + sl + 1 + pl > len || sl == 0 || sl > 32 || pl > 64) { sendError(ERR_INVALID_RPC); return; }
      char ssid[33], pass[65];
      memcpy(ssid, p + 1, sl); ssid[sl] = 0;
      memcpy(pass, p + 2 + sl, pl); pass[pl] = 0;
      join(ssid, pass);
      break;
    }
    case CMD_CURRENT_STATE:
      answerState();
      break;
    case CMD_INFO: {
      const char* strs[] = {g_name, g_version, "ESP32-S3", g_board};
      sendResult(CMD_INFO, strs, 4);
      break;
    }
    case CMD_WIFI_NETWORKS:
      scan();
      break;
    case CMD_FF_SERVER: {
      char url[128] = "";
      if (len > 2) {
        uint8_t ul = p[0];
        if (1 + ul > (uint8_t)(len - 2) || ul >= sizeof(url)) { sendError(ERR_INVALID_RPC); return; }
        memcpy(url, p + 1, ul); url[ul] = 0;
      }
      const char* strs[10];
      size_t n = g_hooks.identity ? g_hooks.identity(strs, 10) : 0;
      sendResult(CMD_FF_SERVER, strs, n);
      if (n && url[0] && g_hooks.setServer && g_hooks.setServer(url)) {
        Serial.printf("Improv: server is now %s; restarting onto it\n", url);
        vTaskDelay(pdMS_TO_TICKS(1500));   // after the host has read the answer
        ESP.restart();
      }
      break;
    }
    default:
      sendError(ERR_UNKNOWN_RPC);
  }
}

// A byte at a time: the header, version, type, length, data, checksum.
struct Parser {
  uint8_t buf[6 + 3 + 255 + 1];
  size_t n = 0;

  void feed(uint8_t b) {
    if (n < 6) {
      if (b == (uint8_t)HEADER[n]) { buf[n++] = b; return; }
      n = (b == (uint8_t)HEADER[0]) ? 1 : 0;
      if (n) buf[0] = b;
      return;
    }
    buf[n++] = b;
    if (n == 7 && b != VERSION) { n = 0; return; }
    if (n < 9) return;
    size_t total = 9 + buf[8] + 1;
    if (n < total) return;
    uint8_t sum = 0;
    for (size_t i = 0; i < total - 1; i++) sum += buf[i];
    uint8_t type = buf[7], len = buf[8];
    n = 0;
    if (sum != buf[total - 1]) { sendError(ERR_INVALID_RPC); return; }
    if (type == TYPE_RPC) handleRpc(buf + 9, len);
  }
};

void improvTask(void*) {
  Parser parser;
  for (;;) {
    while (Serial.available() > 0) parser.feed((uint8_t)Serial.read());
    // Joined after we said otherwise (a saved network came up at boot, or
    // the owner used the captive portal): tell the flasher.
    if (g_owesProvisioned && connected()) sendState(STATE_PROVISIONED);
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

}  // namespace

void improvBegin(const char* name, const char* version, const char* board,
                 const ImprovHooks& hooks) {
  g_name = name;
  g_version = version;
  g_board = board;
  g_hooks = hooks;
  xTaskCreatePinnedToCore(improvTask, "ffimprov", 6144, nullptr, 1, nullptr, 0);
}
