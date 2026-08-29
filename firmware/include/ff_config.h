// Featherframe firmware — board pins & defaults.
//
// Pin assignments for the XIAO ESP32-S3 on the EE03 driver board. The display
// SPI pins are owned by Seeed_GFX (Setup511); these are the *app* pins: the
// three user buttons and the battery sense divider.
//
// The button and ADC pins below are verified against the EE03 V1.0 schematic
// and confirmed on hardware (W-574 keyscan). The SHT40 sits on its own I2C bus
// at GPIO41/42, so it does not touch KEY2 (GPIO5) or the ADC-enable (GPIO6).

#pragma once

// --- User buttons (active-low, RTC-capable for deep-sleep wake) ---
#define PIN_KEY0        GPIO_NUM_2
#define PIN_KEY1        GPIO_NUM_3
#define PIN_KEY2        GPIO_NUM_5
// Bitmask of the buttons we wake on (ext1, ANY_LOW).
#define BUTTON_WAKE_MASK ((1ULL << PIN_KEY0) | (1ULL << PIN_KEY1) | (1ULL << PIN_KEY2))
// Hold KEY2 during boot to clear WiFi/server settings and re-open the portal.
#define PIN_PORTAL_RESET PIN_KEY2

// Panel/board power-enable rail (Seeed_GFX TFT_ENABLE). Also powers the button
// pull-up rail, so it must be HIGH for presses to register while awake.
#define PIN_PANEL_PWR   43

// --- Battery sense ---
#define PIN_BATTERY_ADC     1   // A0 / GPIO1
#define PIN_BATTERY_ENABLE  6   // GPIO6 — drive HIGH to enable the divider
// Volts-per-count multiplier: (ADC/4095) * VBAT_SCALE. Starts from Seeed's
// ~2:1 divider + Vref term; CALIBRATE against a meter on your unit.
#define VBAT_SCALE          7.16f

// --- Dev mode: stay awake instead of deep-sleeping between actions ---
// Keeps Wi-Fi up and polls the buttons in loop(), so a press is instant and the
// panel never re-inits. Set to 0 to restore the battery-saving deep-sleep model.
#define FF_NO_SLEEP  1

// How often loop() re-fetches the frame (always-awake auto-refresh), in ms.
#define FF_POLL_INTERVAL_MS  15000

// How long the "Up to date" pill stays on the glass before it clears (ms).
#define TOAST_HOLD_MS  10000

// How long a button-requested view (collage / status) holds the glass before
// the always-awake poll may repaint the resident bird over it.
#define FF_VIEW_HOLD_MS  300000

// Step period of the loading mark's diamond sweep (ms). Each step is a tiny DU
// partial (~200ms of panel time) pushed from its own task; keep this above that
// so the panel and SPI bus spend most of their time free.
#define FF_LOADER_STEP_MS  400

// --- Behaviour defaults (overridable via NVS / server) ---
#define DEFAULT_WAKE_MINUTES   15
#define DEFAULT_SERVER_URL     "http://10.0.1.73:8081"
#define FRAME_PATH             "/api/frame"
#define VIEW_COLLAGE_PATH      "/api/frame?view=collage"
#define VIEW_STATUS_PATH       "/api/frame?view=status"
#define FIRMWARE_PATH          "/api/firmware"

// Hold KEY2 this long after a button wake to open the settings portal instead
// of the status page.
#define PORTAL_HOLD_MS         3000

// Whole-cycle watchdog: any hang (panel busy-wait, network stall) reboots the
// board instead of stranding it. Deep sleep is the normal exit and disarms it.
// Must comfortably exceed portal timeout + a full OTA download.
#define WDT_TIMEOUT_S  (PORTAL_TIMEOUT_S + 120)

// HTTP + wifi timeouts (ms). The split connect timeout makes a dead host
// fail fast; the total still bounds a slow stream.
#define WIFI_CONNECT_TIMEOUT_MS   20000
#define PORTAL_TIMEOUT_S          600
#define HTTP_TIMEOUT_MS           30000
#define HTTP_CONNECT_TIMEOUT_MS   10000

// Error-state thresholds. Over a painted plate the corner mark appears only
// after this many consecutive failed cycles AND this many minutes since the
// last success — a router blip never marks the art.
#define FF_MARK_FAILS     4
#define FF_MARK_MINUTES   30
// Always-awake model: failed polls back off to this interval after
// FF_MARK_FAILS consecutive failures.
#define FF_POLL_BACKOFF_MS  60000

// Panel geometry (portrait, as the frame hangs)
#define PANEL_W  1404
#define PANEL_H  1872
