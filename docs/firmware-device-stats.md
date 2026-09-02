# Firmware spec — accurate device stats

The config page's **Frame** card is only as honest as what the device reports on
each wake. Today several rows are placeholders, hardcoded, or derived from a
value the firmware never calibrates. This spec lists the firmware (and matching
server) changes needed so every stat on that card is real.

Scope is the request path only: the device already `GET`s `/api/frame` on every
wake, so each stat travels as a request header. Nothing here changes the frame
wire format (FFF) or adds a round trip.

## The contract today

`firmware/src/main.cpp` (`fetchFrame`, ~line 665) sends on every frame fetch:

| Header | Source | Notes |
|---|---|---|
| `If-None-Match` | `g_etag` | unchanged |
| `X-Battery-Voltage` | `readBatteryVoltage()` | volts, 3 dp |
| `X-Battery-Percent` | `batteryPercent(v)` | see Battery below |
| `X-Wifi-RSSI` | `WiFi.RSSI()` | dBm |
| `X-Wake` | `g_wakeInfo` | `"cause=N keys=0xM"` debug string |
| `User-Agent` | literal `"Featherframe-ESP32/1.0"` | never changes across builds |

`X-Firmware-MD5` (`ESP.getSketchMD5()`) is sent **only** on the OTA check
(`/api/firmware`), not on frame fetch. The server reads these in
`app.py` (`get_frame`, ~line 86) into `DeviceStatus` (`service.py`) and the IP is
taken from the connection.

Card rows and their real backing today:

| Row | Backed by | Status |
|---|---|---|
| Last seen | server clock at checkin | ✅ accurate |
| Battery | `X-Battery-Percent` | ⚠️ uncalibrated (see below) |
| Wi-Fi | `X-Wifi-RSSI` | ✅ accurate |
| IP address | connection peer | ✅ accurate |
| Firmware | `User-Agent` | ❌ constant string, tells you nothing |
| ~~Panel~~ / ~~Board~~ | hardcoded in the template | removed — never came from the device |

---

## 1. Firmware version (highest value)

**Problem.** The `Firmware` row prints `User-Agent`, which is the literal
`"Featherframe-ESP32/1.0"` for every build ever flashed. You cannot tell whether
the frame on the wall is running last week's sketch or today's.

**Firmware change.**
- Add a build identity that changes when the code changes. Prefer a compile-time
  define fed from git:
  ```ini
  ; platformio.ini
  build_flags =
    -D FF_FW_VERSION='"${sysenv.FF_FW_VERSION}"'   ; e.g. "2026.09.01+a1b2c3d"
  ```
  with a fallback `#ifndef FF_FW_VERSION` in `ff_config.h` so a bare `pio run`
  still builds (e.g. `"dev"`).
- Send it as a dedicated header on the frame fetch, and keep the User-Agent for
  the product name:
  ```cpp
  http.addHeader("X-FF-Version", FF_FW_VERSION);
  http.addHeader("X-FF-Sketch-MD5", ESP.getSketchMD5());  // opaque but exact
  ```
  MD5 is the unambiguous "is this the same binary" check; the version string is
  the human-readable one.

**Server change.** Read `x-ff-version` in `get_frame`/`record_view_checkin`,
store `fw_version` (and optionally `sketch_md5`) on `DeviceStatus`, and render
`fw_version` in the `Firmware` row, falling back to the User-Agent when absent
(older firmware). Show the short MD5 on hover/title.

**Result.** The row reads e.g. `2026.09.01+a1b2c3d` and moves when you reflash.

---

## 2. Battery percent — calibration

**Status (2026-09-02, W-693): done.** The EE03 divider is 10 kΩ/10 kΩ behind a
TPS22916 load switch (EN = GPIO6, ADC = GPIO1; schematic sheet 4 "BAT ADC
DETE"), so the pin sees VBAT/2. The firmware reads it with the eFuse-calibrated
`analogReadMilliVolts()` and multiplies by `VBAT_DIVIDER` (2.0) and a residual
`VBAT_TRIM` from a meter on the JST leads.

Why not raw counts: the original `(ADC/4095) * VBAT_SCALE` fit was calibrated
at one point (3.865 V ↔ 2363 counts) and read a full 4.13 V cell as 3.83 V /
55%, because raw `analogRead()` counts on this ESP32-S3 flatten near the top
of the range — the same ~2360 counts came back at 3.87 V and at 4.13 V, while
the calibrated path reported 2036 mV at the pin (4.07 V) for the same cell.

**Re-calibrating.** Every check-in carries `X-Wake-Detail: … adc=<counts>
first=<n> last=<n> mv=<pin mV>`, so no serial cable is needed: meter the leads,
read `mv` from the config page's Frame card tooltip (or `/api/status →
device.wake_detail`), and set `VBAT_TRIM = V_meter / (2 * mV / 1000)`.

**Optional accuracy add.** If a charge line is sensed (USB present / `CHG` pin),
send `X-Battery-State: charging|discharging|full` so the card can stop showing a
misleading "74%, dropping" while it's actually on USB. The server already
special-cases "USB power" when no percent is sent; a state header lets it say
"charging" instead.

**Server change.** None required for calibration. If `X-Battery-State` is added,
store it and show a small charging glyph next to the cell.

---

## 3. Wake reason (surfacing what's already sent)

**Problem.** `X-Wake` already arrives as `cause=N keys=0xM` but is only logged,
never shown. Knowing *why* the frame last woke (timer vs button vs first boot)
is useful health info.

**Firmware change.** None strictly needed — the data is already sent. Optionally
replace the raw debug string with a stable token set so the server doesn't parse
ESP wakeup enums:
```cpp
// "timer" | "button" | "coldboot" | "lowbatt"
http.addHeader("X-Wake", wakeReasonToken());
```
Keep the detailed `cause=N keys=0xM` under a separate `X-Wake-Detail` header if
you still want it for debugging.

**Server change.** Store `last_wake`, add a `Last wake` row (or fold into
`Last seen`: "just now · timer").

---

## 4. Ambient temperature / humidity (new stat)

**Opportunity.** The EE03 carries an **SHT40** on its own I2C bus (noted in
`ff_config.h`). A wall frame that also reports room temp/humidity is a nice,
free stat and a canary for a cold/damp mounting spot (e-paper ghosts in the
cold).

**Firmware change.**
- Add the SHT40 read (Adafruit_SHT4x or a 6-byte raw I2C transaction) during the
  wake, *after* the panel push so it doesn't delay the draw.
- Send:
  ```cpp
  http.addHeader("X-Env-TempC", String(tempC, 1));
  http.addHeader("X-Env-RH", String(rh, 0));
  ```
- Gate it behind a `#ifdef FF_HAS_SHT40` so boards without it still build.

**Server change.** Store `temp_c` / `humidity`, add an `Ambient` row
(`21.4 °C · 43%`). Omit the row when the headers are absent.

---

## 5. Longevity counters (new stat, RTC-backed)

**Opportunity.** E-paper has a finite full-refresh budget and the deep-sleep
model makes boot/refresh counts cheap to keep. The firmware already uses
`RTC_DATA_ATTR` for `g_failCount` / `g_failMinutes`, so add two persistent
counters:

**Firmware change.**
```cpp
RTC_DATA_ATTR uint32_t g_bootCount    = 0;  // ++ every wake
RTC_DATA_ATTR uint32_t g_refreshCount = 0;  // ++ only when the panel is redrawn
```
Send on frame fetch:
```cpp
http.addHeader("X-Boot-Count", String(g_bootCount));
http.addHeader("X-Refresh-Count", String(g_refreshCount));
```
(Both reset to 0 only on power loss / battery pull — acceptable; note it in the
UI copy as "since last power-up" if you want to be precise.)

**Server change.** Store both; show `Refreshes` on the card and/or use the count
to estimate panel wear. Low priority relative to 1–2.

---

## 6. Panel & board identity (optional — re-derive what was removed)

The `Panel` (`10.3″ · 1404×1872 · grayscale`) and `Board`
(`XIAO ESP32-S3 · EE03`) rows were removed from the card because they were
template constants, not device facts — they'd stay wrong if you ever swapped
hardware. They're fixed by `firmware/lib/driver/driver.h` (combo 511) at compile
time, so the *firmware* does know them.

If you want them back **honestly**, have the firmware report its own hardware
from compile-time defines:
```cpp
http.addHeader("X-Panel", "ED103TC2 1404x1872 gray16");
http.addHeader("X-Board", "XIAO-ESP32S3 EE03");
```
Then the server can show them and they'll track the actual flashed firmware. This
is cosmetic — skip unless you expect more than one hardware variant.

---

## Priority

1. **Firmware version** (§1) — the card currently lies about what's running.
2. **Battery calibration** (§2) — a real bring-up task; the low-battery warning
   depends on it.
3. Wake reason (§3) — nearly free, data already sent.
4. Ambient temp/humidity (§4) — new hardware read, genuinely useful.
5. Longevity counters (§5), panel/board identity (§6) — nice-to-have.

## Header summary (target state)

| Header | Row | Section |
|---|---|---|
| `X-FF-Version` | Firmware | §1 |
| `X-FF-Sketch-MD5` | Firmware (title) | §1 |
| `X-Battery-Voltage` / `X-Battery-Percent` | Battery | §2 (calibrate) |
| `X-Battery-State` | Battery (charging) | §2 (optional) |
| `X-Wifi-RSSI` | Wi-Fi | — (already accurate) |
| `X-Wake` | Last wake | §3 |
| `X-Env-TempC` / `X-Env-RH` | Ambient | §4 |
| `X-Boot-Count` / `X-Refresh-Count` | Refreshes | §5 |
| `X-Panel` / `X-Board` | Panel / Board | §6 (optional) |

Every header is optional on the wire: the server renders a row only when its
backing header is present, so old firmware degrades to the current card and new
firmware lights up the new rows without a coordinated deploy.
