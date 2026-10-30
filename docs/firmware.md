# Firmware

How `firmware/src/main.cpp` and its helpers work on the two kits.
`AGENTS.md` has the commands; the code wins when this page and it
disagree.

## Building

The firmware builds on the pioarduino fork of the espressif32 platform
(Arduino-ESP32 v3, IDF 5). On the mainline platform (Arduino-ESP32 2.0.x)
the EE03's IT8951 T-CON does not configure the panel from cold: `update()`
completes and no pixels change. `main.cpp` includes `<FS.h>` and
`using namespace fs;` before `WebServer.h`, which uses an unqualified `FS`
on v3.

## The client

**Firmware (`firmware/src/main.cpp`).** Deep-sleep model: all logic in `setup()`,
`loop()` empty. Wake → Wi-Fi (WiFiManager captive portal on first boot / held
button) → `GET /api/frame` with stored ETag → 304 sleeps, else `pushImage` +
`update()` (full refresh) → deep sleep (timer + button ext1 wake). Uses Seeed's
`Seeed_GFX` via `TFT_eSPI.h`; `begin(1)` is the fast re-init after a sleep wake.
Panel/board selection is `lib/driver/driver.h` (combo 511). Battery voltage is
`analogReadMilliVolts(A0) * 2 * VBAT_TRIM` (10k/10k divider; raw counts flatten
near a full cell; the trim defaults to 1.0 so one binary ships to
every unit, and `FF_VBAT_TRIM=…` in the environment trims a bench build) and the button pins are known (KEY0=2, KEY1=3, KEY2=5,
active-low); the remaining on-hardware unknown is whether ext1 button wake
works from deep sleep at all — the keys read only while the panel's T-CON is
awake (see `PIN_PANEL_PWR` in `include/ff_config.h`). The power model is a
runtime setting: the server sends `X-Power-Mode` (awake|sleep) and
`X-Wake-Minutes` on every `/api/frame` response, the firmware stores both in
NVS, and `setup()` branches on `g_alwaysAwake`; a switch takes effect at the
end of the cycle that learned it (awake→sleep from `loop()`, sleep→awake by a
restart). `FF_DEFAULT_ALWAYS_AWAKE` is only the mode of a unit with no stored
value; `X-Poll-Seconds` sets the awake poll gap the same way (3 s default; the
page offers a minute to a day, and older firmware ignores anything over
`FF_POLL_MAX_S` = 60 s until it is updated). `X-FF-Rotation`
(the page's panel rotation) rides along too and is kept in NVS (dark mode is
gone: the server still says `X-FF-Invert: 0` so fielded firmware
clears the flag it stored, until every frame runs firmware without it):
the baked art is baked at one rotation (`FF_BAKED_ROTATION`), so when the
frame hangs the other way up the firmware turns every baked screen and tile
180° (`rotate180`, and `flipX`/`flipY` for a tile's window). The mat rides the same way: `X-FF-Mat` ("inset,x,y") on every response, kept in NVS `mat` and said back on every ask and over Improv, so a frame removed and paired again (`admit_frame`, a new row) starts with its mat; the rotation already did (`X-FF-Rotation`). The mat also places the toasts: every pill on the glass (a toast, an error, a footnote) is the one black pill on the footer line between the corner marks — at 34 px type on the glass (the footnote line's pill scaled up, standing on its bottom edge: the line is ~36 px over the mat's edge), the server's footnote the same pill, drawn that much larger on the sheet so the 4% mat brings it to 34 px (`system.PILL_*`) — baked where that line lands under a 4% reference mat, and `placeToast` moves a toast to where it lands under the frame's own mat (`FF_REF_INSET`, `FF_FOOT_CY`; `test_toast_place.py` holds the formula to the server's). The offline mark is the footnote-size pill as a circle in the plate number's place, moved the same way (`FF_FOOT_RX`). Every icon on a pill is `system.draw_icon`, drawn large and cut to two tones, which the bake calls too.
Always-awake is the well-tested mode; deep sleep is the
less-tested branch. An optional status LED (`ff_led.cpp`): one WS2812B pixel
with DIN on GPIO39, soldered to pad 1 of the unfitted font chip U6 (3V3 on
pads 7/8, GND on 4, both kits; U6 is turned 180° on the EE02, so pad 1 is
found by the empty R39 beside it, never by where it sat on the EE03); `ledSet()` from the boot, portal, fetch
outcome (`noteLedOutcome`) and OTA paths, dark in deep sleep. Setup is one
colour a step: blue breathing slowly while the hotspot waits, steady once a
phone is on it (`onWifiLed`, Wi-Fi events), a quick pulse while it joins the
chosen network (WiFiManager's pre-save callback, or Improv), two red pulses
and back to blue if that fails (the station stops); green breathes from the join until the server
answers, then holds and fades. The EE02 build (`-e ee02`, `FF_PANEL_SPECTRA6`) is the same
app with a full-refresh equivalent for everything partial: the plate
is retained in PSRAM, and a toast or the corner mark is a baked black/white-ink
tile blitted into a copy of it, then one ~15 s repaint (`paintPlate`; cleared
by painting the plate again, 60 s later for a toast). A press fetches first
and paints one thing, so only the outcome pills exist (the in-progress ones
have no tile); out of deep sleep there is no retained plate, so "Already up-to-date"
refetches the plate with the pill armed and the corner mark becomes the full
error screen. A frame whose last picture was a pairing code (NVS `unpaired`) paints no
"Connecting" at all, so a restart or a Wi-Fi reset goes straight to the code.
The gray splash's version line is not baked: the bake ships its glyphs (`ff_ver_glyphs`) and `stampVersion` sets `FF_FW_VERSION` there at boot — "v 0.2.5" for a release, "dev 2026.09.24" for a dev build; `bake_screens.stamp_version` mirrors it and a test holds it to `draw_engraved`. Boot is one baked "Connecting" screen (every boot stage maps to
`FF_SCR_BOOT_WIFI`, painted while Wi-Fi joins underneath) that gives way to a
specific error screen only on failure; no loading sweep. Baked screens and
tiles live in `ff_screens_ee02.h`, from the same bake (the screens are the
colour art under black/white type, dithered as the server dithers a plate:
`bake_screens.on_color_art`; the stamp tiles stay black/white ink), and a 180 s floor sits
between resident repaints. Its refresh is `SpectraPaper::refresh()`, the library's sequence with every BUSY wait bounded (DRF 40 s, other steps 10 s): a stuck one resets the panel and powers it off, returns `FETCH_STUCK` (ETag unset, no error screen), and after three in a row the panel is tried once an hour; `X-Wake-Detail` carries `panel=stuckN[+held]`. One server serves as many frames as are added to it, and it knows them apart
(`service.admit_frame`): each frame names itself with `X-Device-Id` (its MAC).
Every kit gets a 403 and waits as "pending" — the first one on a fresh install
too — until the owner answers at the top of the Frames card: add it, or ignore
it (ignored frames fold at the bottom of that card, where they can be added or
forgotten; a frame removed from a row asks again the next time it checks in). Each frame is drawn for the panel it reports in its own `X-Panel`, so
there is no notice to answer when a different kit connects. Firmware without
the header is one frame called "legacy", which becomes its real ID in place
after an update. A parked frame
shows "Add this frame on the Featherframe webapp" (gray: error pill 3; EE02:
`FF_SCR_PENDING`) and keeps asking. Discovery prefers a server whose mDNS TXT
`panel` matches and otherwise takes any, and `X-Board` on the OTA request
keeps one board's image off the other. A
kit row's Advanced has "Reset to this panel's defaults" (a client-side fill
from that frame's own `Config.defaults_for(panel)`, applied only on Save). Low battery (`FF_LOW_BATT_V`: gray < 3.45 V, EE02 < 3.55 V) skips Wi-Fi and
sleeps 4 h at a time, saying "Low battery" on the glass once at
the crossing (`markLowBattery`: gray paints the baked `FF_TOAST_LOW_BATTERY`
pill over the plate, the EE02 the baked `FF_SCR_LOW_BATT` full screen, which
is why its hold starts 0.1 V earlier — a ~15 s full refresh needs the headroom;
"once" lives in NVS `lowmark`, written before the paint so a brownout can't
loop it; the always-awake loop enters the same hold after `FF_LOW_BATT_POLLS`
low polls), and the page puts a red *Battery low* badge on that frame's
own row at its `card.battery_critical` (≤ 10 % or ≤ the panel's hold,
`Panel.low_battery_volts`, which a test keeps equal to `FF_LOW_BATT_V`); OTA is refused under 3.70 V and a bad image rolls back.

## Porting a panel

The firmware side of a port is `-DFF_GENERIC_PANEL` (the `generic_bench`
env: the EE03's own glass under a label the server does not know): the panel
comes from build flags, its screens from
`bake_screens.py --size WxH --format gray16|spectra6 --rotation N --out …`
(baked at build time by `tools/bake_generic.py`, not committed), and it runs
the EE02's full-refresh path. The app branches on two facts, never on the
panel: `FF_FULL_REFRESH` (no windowed update) and `FF_FRAME_INKS` (nibbles are
ink codes); `fullPaint()` is the one driver call on that path.
`gray2`/`mono` have no baked screens or push path yet.
