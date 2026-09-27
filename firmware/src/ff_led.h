// Featherframe status LED — one addressable RGB pixel (WS2812B/SK6812) seen
// through the mat, for what the glass cannot say quickly: a restart, setup, an
// update in progress, a lost connection. The glass still says all of it; the
// LED is the fast, glanceable half. Off in normal running.
//
// The pixel is optional and hand-wired: DIN on FF_STATUS_LED_PIN (GPIO39, the
// empty font-chip footprint U6 on both the EE02 and EE03). A board without
// one loses nothing: the pin is otherwise unconnected.

#pragma once
#include <stdint.h>

enum LedState : uint8_t {
  LED_OFF,         // normal running: nothing to say
  LED_BOOT,        // power-on / restarting, until the first answer
  LED_WIFI_SETUP,  // the setup portal is open (Wi-Fi pairing)
  LED_PAIRING,     // waiting to be added: a pairing code, or "Add this frame"
  LED_UPDATING,    // a firmware image is being written
  LED_NO_WIFI,     // the saved network cannot be joined
  LED_NO_SERVER,   // on Wi-Fi, but the server does not answer
  LED_PAIRED,      // connected: a short green, then off
};

void ledBegin();              // first thing in setup(): starts the animation task
void ledSet(LedState s);      // from any task
LedState ledState();
void ledSleep();              // before deep sleep: dark, and the pin held low
