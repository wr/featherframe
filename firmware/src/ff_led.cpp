#include "ff_led.h"

#include <Arduino.h>
#include <driver/gpio.h>
#include "ff_config.h"

#if FF_STATUS_LED_PIN >= 0

static volatile LedState g_led = LED_OFF;
static volatile uint32_t g_ledSince = 0;   // millis() the state began
static volatile bool     g_ledHalt = false;

struct Look { uint8_t r, g, b; uint8_t pattern; uint16_t period; };
enum { SOLID, BREATHE, BLINK };

// One colour per state, and a pattern that says whether it is waiting on you
// (a slow breath) or something is wrong (a blink).
static Look lookOf(LedState s) {
  switch (s) {
    case LED_BOOT:       return {255, 255, 255, BREATHE, 2000};
    case LED_WIFI_SETUP: return {  0,  60, 255, BREATHE, 3000};
    case LED_PAIRING:    return {255, 150,   0, BREATHE, 3000};
    case LED_UPDATING:   return {170,   0, 255, BREATHE,  800};
    case LED_NO_WIFI:    return {255,   0,   0, BLINK,   2000};
    case LED_NO_SERVER:  return {255,  50,   0, BLINK,   2000};
    case LED_PAIRED:     return {  0, 255,  40, SOLID,      0};
    default:             return {  0,   0,   0, SOLID,      0};
  }
}

// 0..255 of the state's colour at `t` ms into it.
static uint8_t levelAt(const Look& k, uint32_t t) {
  switch (k.pattern) {
    case BREATHE: {
      uint32_t p = t % k.period, half = k.period / 2;
      uint32_t tri = p < half ? p * 255 / half : (k.period - p) * 255 / half;
      return 20 + (uint8_t)(tri * tri / 255 * 235 / 255);   // never quite dark
    }
    case BLINK:
      return (t % k.period) < FF_LED_BLINK_ON_MS ? 255 : 0;
    default:
      return 255;
  }
}

static void ledTask(void*) {
  uint32_t last = 0xFFFFFFFF;
  for (;;) {
    if (g_ledHalt) { vTaskDelay(pdMS_TO_TICKS(100)); continue; }
    LedState s = g_led;
    uint32_t t = millis() - g_ledSince;
    // Paired is a moment of green that fades out, then the LED is simply off.
    if (s == LED_PAIRED && t >= FF_LED_PAIRED_MS + FF_LED_FADE_MS) { g_led = LED_OFF; s = LED_OFF; }
    Look k = lookOf(s);
    uint32_t lvl = levelAt(k, t);
    if (s == LED_PAIRED && t > FF_LED_PAIRED_MS) lvl = lvl * (FF_LED_FADE_MS - (t - FF_LED_PAIRED_MS)) / FF_LED_FADE_MS;
    uint32_t scale = lvl * FF_LED_MAX / 255;
    uint8_t r = k.r * scale / 255, g = k.g * scale / 255, b = k.b * scale / 255;
    uint32_t rgb = (uint32_t)r << 16 | (uint32_t)g << 8 | b;
    if (rgb != last) { rgbLedWrite(FF_STATUS_LED_PIN, r, g, b); last = rgb; }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void ledBegin() {
  gpio_hold_dis((gpio_num_t)FF_STATUS_LED_PIN);   // held low through the last deep sleep
  g_led = LED_BOOT;
  g_ledSince = millis();
  // Core 0 beside Improv and the push socket, away from the panel's loop().
  xTaskCreatePinnedToCore(ledTask, "ffled", 3072, nullptr, 1, nullptr, 0);
}

void ledSet(LedState s) {
  LedState cur = g_led;
  if (s == cur) return;
  // Green says "it worked": only after something else was showing.
  if (s == LED_PAIRED && cur == LED_OFF) return;
  g_led = s;
  g_ledSince = millis();
}

LedState ledState() { return g_led; }

void ledSleep() {
  g_ledHalt = true;
  delay(30);                                      // let a write in flight finish
  rgbLedWrite(FF_STATUS_LED_PIN, 0, 0, 0);
  delay(1);
  // A floating DIN through a long sleep can clock noise into the pixel.
  rmtDeinit(FF_STATUS_LED_PIN);
  pinMode(FF_STATUS_LED_PIN, OUTPUT);
  digitalWrite(FF_STATUS_LED_PIN, LOW);
  gpio_hold_en((gpio_num_t)FF_STATUS_LED_PIN);
  gpio_deep_sleep_hold_en();
}

#else

void ledBegin() {}
void ledSet(LedState) {}
LedState ledState() { return LED_OFF; }
void ledSleep() {}

#endif
