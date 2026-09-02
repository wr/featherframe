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

// --- Build identity ---
// Normally injected from git by tools/fw_version.py at compile time
// (e.g. "2026.09.01+a1b2c3d"), reported to the server as X-FF-Version so the
// config page shows which build is on the wall. This fallback only applies when
// git is unavailable at build time; a bare `pio run` is still stamped by the hook.
#ifndef FF_FW_VERSION
#define FF_FW_VERSION "dev"
#endif

// --- User buttons (active-low, RTC-capable for deep-sleep wake) ---
#define PIN_KEY0        GPIO_NUM_2
#define PIN_KEY1        GPIO_NUM_3
#define PIN_KEY2        GPIO_NUM_5
// Bitmask of the buttons we wake on (ext1, ANY_LOW).
#define BUTTON_WAKE_MASK ((1ULL << PIN_KEY0) | (1ULL << PIN_KEY1) | (1ULL << PIN_KEY2))
// Hold KEY2 during boot to clear WiFi/server settings and re-open the portal.
#define PIN_PORTAL_RESET PIN_KEY2

// Panel/board power-enable rail (Seeed_GFX TFT_ENABLE). Seeed_GFX drives it
// HIGH in init and never lowers it; what kills the buttons after a refresh is
// update() putting the IT8951 to SLEEP (the key pull-ups hang off the T-CON
// side), so presses only register while the panel side is powered and awake.
// Consequence for the deep-sleep model: ext1 button wake is UNVERIFIED on this
// board — with the T-CON asleep the keys may be electrically invisible.
#define PIN_PANEL_PWR   43

// --- Battery sense ---
#define PIN_BATTERY_ADC     1   // A0 / GPIO1
#define PIN_BATTERY_ENABLE  6   // GPIO6 — drive HIGH to enable the divider
// The EE03 divider is 10k/10k behind a TPS22916 load switch (schematic sheet
// 4, "BAT ADC DETE"), so the pin sees VBAT/2. The voltage is read with the
// eFuse-calibrated analogReadMilliVolts(): the raw analogRead() counts on the
// ESP32-S3 flatten near the top of the range — on this unit the same ~2360
// counts came back at 3.87 V and at 4.13 V (W-693), which is why a single
// point fit (the old VBAT_SCALE = 6.698) read a full cell as 3.83 V / 55%.
#define VBAT_DIVIDER        2.0f
// Residual trim after the calibrated read, from a meter on the JST leads:
// meter / (2 * mV). 2026-09-02: 4.13 V metered, 2036 mV at the pin -> 1.014.
#define VBAT_TRIM           1.014f

// --- Low battery ---
// Below FF_LOW_BATT_V (resting, read before the radio starts) the frame skips
// Wi-Fi entirely and sleeps FF_LOW_BATT_SLEEP_MIN at a time: a Wi-Fi burst on
// a nearly empty 1S cell sags the 3.3 V rail into the S3's brownout detector
// and the board reboots in a loop until the pack's protection IC cuts it off.
// Service resumes once the cell rests at FF_LOW_BATT_RESUME_V (hysteresis).
// Readings under FF_BATT_ABSENT_V mean no pack is fitted and are ignored.
#define FF_LOW_BATT_V          3.45f
#define FF_LOW_BATT_RESUME_V   3.60f
#define FF_BATT_ABSENT_V       2.50f
#define FF_LOW_BATT_SLEEP_MIN  240
// OTA is refused below this: a brownout mid-write leaves the spare slot half
// written (harmless — the boot slot is untouched) but burns the download.
#define FF_OTA_MIN_BATT_V      3.70f

// Deep-sleep timer bounds: a 0 in NVS would arm a zero-length timer (wake storm).
#define FF_MIN_SLEEP_MINUTES   1
#define FF_MAX_SLEEP_MINUTES   720

// --- Dev mode: stay awake instead of deep-sleeping between actions ---
// Keeps Wi-Fi up and polls the buttons in loop(), so a press is instant and the
// panel never re-inits. Set to 0 to restore the battery-saving deep-sleep model.
#define FF_NO_SLEEP  1

// How often loop() re-fetches the frame (always-awake auto-refresh), in ms.
#define FF_POLL_INTERVAL_MS  15000
// Always-awake build: how often the poll loop also asks for hosted firmware.
#define FF_OTA_CHECK_MS      (15UL * 60UL * 1000UL)

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
