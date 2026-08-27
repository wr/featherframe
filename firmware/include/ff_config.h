// Featherframe firmware — board pins & defaults.
//
// Pin assignments for the XIAO ESP32-S3 on the EE03 driver board. The display
// SPI pins are owned by Seeed_GFX (Setup511); these are the *app* pins: the
// three user buttons and the battery sense divider.
//
// NOTE: the button/ADC pins below come from Seeed's shared EE0x example code.
// EE03 uniquely adds an SHT40 on I2C for waveform temperature comp, which may
// share GPIO5/6 — verify KEY2 and the ADC-enable pin against the EE03 schematic
// on first bring-up if the buttons or battery reading misbehave.

#pragma once

// --- User buttons (active-low, RTC-capable for deep-sleep wake) ---
#define PIN_KEY0        GPIO_NUM_2
#define PIN_KEY1        GPIO_NUM_3
#define PIN_KEY2        GPIO_NUM_5
// Bitmask of the buttons we wake on (ext1, ANY_LOW).
#define BUTTON_WAKE_MASK ((1ULL << PIN_KEY0) | (1ULL << PIN_KEY1) | (1ULL << PIN_KEY2))
// Hold KEY2 during boot to clear WiFi/server settings and re-open the portal.
#define PIN_PORTAL_RESET PIN_KEY2

// --- Battery sense ---
#define PIN_BATTERY_ADC     1   // A0 / GPIO1
#define PIN_BATTERY_ENABLE  6   // GPIO6 — drive HIGH to enable the divider
// Volts-per-count multiplier: (ADC/4095) * VBAT_SCALE. Starts from Seeed's
// ~2:1 divider + Vref term; CALIBRATE against a meter on your unit.
#define VBAT_SCALE          7.16f

// --- Behaviour defaults (overridable via NVS / server) ---
#define DEFAULT_WAKE_MINUTES   15
// Overridable at build time so a deployment can bake its own server URL.
#ifndef DEFAULT_SERVER_URL
#define DEFAULT_SERVER_URL     "http://featherframe.local:8080"
#endif
#define FRAME_PATH             "/api/frame"

// Flash-time provisioning generation. Bump this (e.g. -DSERVER_URL_PROVISION=1)
// to force the stored server URL to DEFAULT_SERVER_URL exactly once on next boot
// — repoints an already-provisioned frame without opening the captive portal.
// It writes only when the stored generation is older, so later portal edits stick.
#ifndef SERVER_URL_PROVISION
#define SERVER_URL_PROVISION   0
#endif

// HTTP + wifi timeouts (ms)
#define WIFI_CONNECT_TIMEOUT_MS   20000
#define PORTAL_TIMEOUT_S          180
#define HTTP_TIMEOUT_MS           30000

// Panel geometry (portrait, as the frame hangs)
#define PANEL_W  1404
#define PANEL_H  1872
