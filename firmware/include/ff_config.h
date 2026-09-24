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
// Residual trim after the calibrated read. One binary ships to every unit
// (W-768), so the default is none: the eFuse-calibrated read plus 1 % divider
// resistors lands within about 2 % (one bench unit metered 1.4 % low, 4.13 V
// on the JST leads against 2036 mV at the pin). That is a few points of charge
// mid-curve, and it errs toward reading low, so the low-battery hold and the
// OTA floor trip slightly early rather than late. Most frames run on USB and
// never read a pack at all. To trim one unit anyway, build with
//   FF_VBAT_TRIM=1.014 pio run        (meter volts / the untrimmed volts the
//                                      serial log prints at boot)
#ifndef VBAT_TRIM
#define VBAT_TRIM           1.000f
#endif

// --- Panel ---
// Set ahead of everything that branches on it: an undefined macro in an #if is
// silently 0 (the EE02 shipped with the gray low-battery threshold that way).
#if defined(FF_BOARD_EE02)
#define FF_PANEL_SPECTRA6 1
#else
#define FF_PANEL_SPECTRA6 0
#endif
// A panel this firmware was not written for (W-819, -DFF_GENERIC_PANEL): it
// describes itself from build flags (see "Panel" below) and brings its own
// baked screens, from `bake_screens.py --size`.
#if defined(FF_GENERIC_PANEL)
#define FF_PANEL_GENERIC 1
#else
#define FF_PANEL_GENERIC 0
#endif
// What the app branches on is not the panel but these two facts about it.
// FF_FULL_REFRESH: no windowed update, so everything the gray build does with
// one (the loading sweep, toasts, the corner mark, boot-stage screens) has a
// full-refresh equivalent (W-817). True of the Spectra 6, and assumed of a
// panel we know nothing about. FF_FRAME_INKS: the 4bpp nibbles are Seeed
// colour-sprite ink codes (FFF_FLAG_INKS), not gray levels.
#define FF_FULL_REFRESH (FF_PANEL_SPECTRA6 || FF_PANEL_GENERIC)
#if FF_PANEL_SPECTRA6 || defined(FF_GENERIC_INKS)
#define FF_FRAME_INKS 1
#else
#define FF_FRAME_INKS 0
#endif

// --- Low battery ---
// Below FF_LOW_BATT_V (resting, read before the radio starts) the frame skips
// Wi-Fi entirely and sleeps FF_LOW_BATT_SLEEP_MIN at a time: a Wi-Fi burst on
// a nearly empty 1S cell sags the 3.3 V rail into the S3's brownout detector
// and the board reboots in a loop until the pack's protection IC cuts it off.
// Service resumes once the cell rests at FF_LOW_BATT_RESUME_V (hysteresis).
// Readings under FF_BATT_ABSENT_V mean no pack is fitted and are ignored.
// The hold announces itself on the glass, once ("Battery low, charge me"). On
// the gray panel that is a sub-second windowed pill; on the Spectra it is a
// ~30 s six-ink full refresh, so that panel holds 0.1 V earlier, while the
// cell still has the headroom to paint it. The server's Panel.low_battery_volts
// mirrors these (a test keeps them equal): it is where the page's banner goes up.
#if FF_FULL_REFRESH
#define FF_LOW_BATT_V          3.55f
#define FF_LOW_BATT_RESUME_V   3.70f
#else
#define FF_LOW_BATT_V          3.45f
#define FF_LOW_BATT_RESUME_V   3.60f
#endif
#define FF_BATT_ABSENT_V       2.50f
#define FF_LOW_BATT_SLEEP_MIN  240
#define FF_LOW_BATT_POLLS      4     // always awake: consecutive low polls before the hold
// OTA is refused below this: a brownout mid-write leaves the spare slot half
// written (harmless — the boot slot is untouched) but burns the download.
#define FF_OTA_MIN_BATT_V      3.70f

// Deep-sleep timer bounds: a 0 in NVS would arm a zero-length timer (wake storm).
#define FF_MIN_SLEEP_MINUTES   1
#define FF_MAX_SLEEP_MINUTES   1440

// --- Power model: the mode a fresh unit boots in until the server says ---
// The server sends X-Power-Mode (awake|sleep) and X-Wake-Minutes on every
// /api/frame response; the frame stores both in NVS, so the config page is
// where the model is chosen (W-736/W-456). "Always awake" keeps Wi-Fi up and
// polls the buttons in loop(), so a press is instant and the panel never
// re-inits (USB). "Deep sleep" acts once per wake and sleeps (battery).
// 1 = a unit with no stored mode starts always-awake.
#define FF_DEFAULT_ALWAYS_AWAKE  1

// How often loop() re-fetches the frame (always-awake auto-refresh), in ms.
// The server serves the live value (X-Poll-Seconds, NVS "poll_s"); this is
// only what a unit with no stored value starts with.
#define FF_POLL_INTERVAL_MS  3000
#define FF_POLL_MIN_S        2
#define FF_POLL_MAX_S        86400
// Always-awake build: how often the poll loop also asks for hosted firmware.
#define FF_OTA_CHECK_MS      (15UL * 60UL * 1000UL)

// Push (W-841): on USB the frame holds a WebSocket to /api/frame/push and the
// server says when its next GET /api/frame would answer differently. While the
// socket is up the timed poll is only this heartbeat (telemetry, the OTA
// check); it is sent to the server as X-FF-Push so "overdue" is measured
// against it. With the socket down the frame polls at X-Poll-Seconds as before.
#define FF_PUSH_HEARTBEAT_MS (15UL * 60UL * 1000UL)
#define FF_PUSH_RETRY_MS     30000UL          // reconnect gap, for as long as it takes…
#define FF_PUSH_JITTER_MS    10000UL          // …plus up to this, so a restarted server is not stampeded
#define FF_PUSH_PATH         "/api/frame/push"
// The push task has not turned for this long: count the socket as down and
// poll (a TLS connect it is stuck in gives up by itself, W-853).
#define FF_PUSH_STALL_MS     60000UL
// A LAN server is looked for again over mDNS only after this many failed
// fetches in a row (W-853): one failure is a hiccup, not a move.
#define FF_REDISCOVER_FAILS  3

// How long the "Up to date" pill stays on the glass before it clears (ms).
// On the Spectra a pill costs a ~30 s refresh to put up and another to take
// down, so it stays long enough to be seen by someone who looked away.
#if FF_FULL_REFRESH
#define TOAST_HOLD_MS  60000
#else
#define TOAST_HOLD_MS  10000
#endif

// How long a button-requested view (collage / status) holds the glass before
// the always-awake poll may repaint the resident bird over it.
#define FF_VIEW_HOLD_MS  300000

// Step period of the loading mark's diamond sweep (ms). Each step is a tiny DU
// partial (~200ms of panel time) pushed from its own task; keep this above that
// so the panel and SPI bus spend most of their time free.
#define FF_LOADER_STEP_MS  400

// --- Behaviour defaults (overridable via NVS / server) ---
#define DEFAULT_WAKE_MINUTES   15
// No server is baked in: the frame finds it by mDNS (_featherframe._tcp, which
// the server advertises) on first boot and again whenever the stored URL
// stops answering. A URL typed into the portal is used as-is until it fails.
// A LAN that blocks multicast needs the typed URL.
#define DEFAULT_SERVER_URL     ""
#define FF_MDNS_SERVICE        "featherframe"
#define FF_MDNS_PROTO          "tcp"
#define FF_MDNS_RETRY_MS       60000     // don't re-query mDNS more often than this while awake
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
// Streaming a frame body once the headers are in. The link is slow for the
// first minute after association (41-85 KB/s measured 3 Sep 2026, 15-32 s for
// a frame) and far slower after an OTA reboot (~15 KB/s, ~90 s, 4 Sep, W-718),
// so any fixed total deadline eventually discards a nearly complete body and
// retries from scratch — and while a boot screen holds the glass every retry
// is another full fetch, a loop that ran 6.5 h. Give up only when the bytes
// stop (no progress for FF_BODY_STALL_MS: a dead link fails over as fast as
// before), with a hard cap that keeps a crawling link inside the watchdog.
#define FF_BODY_STALL_MS          15000
#define FF_BODY_MAX_MS            300000
#define HTTP_CONNECT_TIMEOUT_MS   10000

// Error-state thresholds. Over a painted plate the corner mark appears only
// after this many consecutive failed cycles AND this many minutes since the
// last success — a router blip never marks the art.
#define FF_MARK_FAILS     4
#define FF_MARK_MINUTES   30
// Always-awake model: failed polls back off to this interval after
// FF_MARK_FAILS consecutive failures.
#define FF_POLL_BACKOFF_MS  60000

// --- Panel ---
// -DFF_BOARD_EE02 (the ee02 envs) builds for the EE02 board's 13.3" Spectra 6
// colour panel instead of the EE03's 10.3" gray one. Spectra has no partial
// refresh and a full one takes ~30 s, so everything the gray build does with
// windowed updates (the loading sweep, toasts, the corner mark, boot-stage
// screens) has a full-refresh-or-nothing fallback under FF_FULL_REFRESH.
// The ID strings ride X-Panel / X-Board as labels. The server draws for a
// name it knows (featherframe/panels.py from_report) and, for any other, from
// the facts sent beside it (W-813): the native canvas (X-Panel-Width/-Height,
// FF_NATIVE_W/H), FF_PANEL_FORMAT (what the nibbles/bits mean: gray16 | gray2
// | mono | spectra6) and FF_PANEL_ROTATIONS (the server rotations whose frame
// displayFrame() accepts, default first). A port to another panel sets these
// five and its driver; the server needs no change.
//
// -DFF_GENERIC_PANEL is that port without touching this file (W-819). Its
// build flags say what the panel is:
//   -DPANEL_W=480 -DPANEL_H=800          as it hangs
//   -DFF_PANEL_ID=\"my-panel\"           the label (X-Panel); no name the server knows
//   -DFF_BOARD_ID=\"my-board\"           X-Board, and what keeps OTA images apart
//   -DFF_PANEL_ROTATIONS=\"90,270\"      what displayFrame() accepts, default first
//   -DFF_BAKED_ROTATION=90               the first of those: what the screens are baked at
//   -DFF_SCREENS_HEADER=\"ff_screens_mine.h\"   from bake_screens.py --size ... --out
//   -DFF_GENERIC_INKS                    only for a Spectra 6: frames are ink codes
// The format is gray16 unless FF_GENERIC_INKS; gray2 and mono have no push
// path here yet. lib/driver/driver.h still picks the Seeed_GFX setup, and
// fullPaint() in main.cpp is the one call into it.
#if FF_PANEL_GENERIC
#if !defined(PANEL_W) || !defined(PANEL_H) || !defined(FF_PANEL_ID) || !defined(FF_BOARD_ID) \
    || !defined(FF_PANEL_ROTATIONS) || !defined(FF_BAKED_ROTATION) || !defined(FF_SCREENS_HEADER)
#error "FF_GENERIC_PANEL needs PANEL_W, PANEL_H, FF_PANEL_ID, FF_BOARD_ID, FF_PANEL_ROTATIONS, FF_BAKED_ROTATION and FF_SCREENS_HEADER (see ff_config.h, Panel)"
#endif
#if FF_FRAME_INKS
#define FF_PANEL_FORMAT "spectra6"
#else
#define FF_PANEL_FORMAT "gray16"
#endif
// The server keys a reported panel by its facts (panels.custom), and
// advertises that key in its mDNS TXT "panel". FF_NATIVE_W/H come from the
// screens header, which is included before this is used.
#define FF_STR_(x) #x
#define FF_STR(x)  FF_STR_(x)
#define FF_PANEL_KEY "custom:" FF_STR(FF_NATIVE_W) "x" FF_STR(FF_NATIVE_H) ":" FF_PANEL_FORMAT ":" FF_PANEL_ROTATIONS
#elif defined(FF_BOARD_EE02)
#define FF_PANEL_ID  "T133A01 1200x1600 spectra6"
#define FF_PANEL_KEY "ee02"              // the server's mDNS TXT "panel" for us
#define FF_BOARD_ID  "XIAO-ESP32S3 EE02"
#define FF_PANEL_FORMAT    "spectra6"
#define FF_PANEL_ROTATIONS "0,180"
#define PANEL_W  1200
#define PANEL_H  1600
#else
#define FF_PANEL_ID  "ED103TC2 1404x1872 gray16"
#define FF_PANEL_KEY "ee03"
#define FF_BOARD_ID  "XIAO-ESP32S3 EE03"
#define FF_PANEL_FORMAT    "gray16"
#define FF_PANEL_ROTATIONS "90,270"
#define PANEL_W  1404
#define PANEL_H  1872
#endif
// The rotation the baked art (boot/setup/error screens, pills, toasts, the
// loading mark) is baked at: the server's default panel_rotation for the panel.
// The server announces the rotation in use (X-FF-Rotation); when it is the
// other one, everything baked is turned 180 degrees to match the plates.
#if FF_PANEL_GENERIC
// from the build flags, checked against the screens header in main.cpp
#elif FF_PANEL_SPECTRA6
#define FF_BAKED_ROTATION 0
#else
#define FF_BAKED_ROTATION 90
#endif
// Full-refresh panels: the floor between two resident repaints from the poll
// loop (the Spectra maker's guidance is >= 180 s between refreshes, and a
// panel we know nothing about gets the same care). Button presses and error
// screens are exempt — they are rare and deliberate.
#ifndef FF_MIN_REPAINT_MS
#define FF_MIN_REPAINT_MS  180000UL
#endif
// FFF header flags bit 0: the 4bpp nibbles are Spectra ink codes, not grays.
#define FFF_FLAG_INKS  0x01
