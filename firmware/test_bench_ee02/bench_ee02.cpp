// Refresh-speed bench for the EE02 board + 13.3" Spectra 6 colour panel (combo
// 510). Three questions, answered on the glass and on serial:
//
//   1. How long is the stock refresh really? PON / DRF / POF timed by BUSY,
//      apart from the two ~480 KB SPI pushes.
//   2. Does a forced temperature pick a shorter waveform? CCSET 0x03 (the
//      driver's unused CCSET_V_LOCK) + TSSET, sent to both controllers after
//      init — the library's own EPD_SET_TEMP goes out *before* EPD_INIT's
//      hardware reset, to the master only, so it never reaches a refresh.
//   3. Does a refresh cut short by a hardware reset leave a usable black/white
//      image (the r/eink reTerminal E1002 trick: reset ~1 s into the waveform)?
//
// Nothing runs at boot — opening the Mac's serial port resets the board, and a
// reset must not cost a refresh. Everything is a serial command (115200):
//
//   b          stock full refresh of the colour card, timed
//   w          full refresh to white (the clean slate for an abort trial)
//   t <degC>   colour card with the temperature forced, timed
//   s          sweep t over 25..50 C, 60 s apart, and print the table
//   a <ms>     black/white card, refresh aborted by reset <ms> after DRF
//   r          read the temperature register (best effort, needs MISO)
//
// An aborted waveform is not DC-balanced and nobody has published what that
// costs a Spectra 6, so aborts are rationed: 3 between full refreshes, 12 per
// boot. Each card prints its own parameters, so a photo documents itself.
// Build: pio run -e ee02_bench -t upload
#include "driver.h"        // Seeed_GFX board/panel selection (-DFF_BOARD_EE02)
#include "TFT_eSPI.h"

static const uint8_t  ABORTS_PER_FULL   = 3;
static const uint8_t  ABORTS_PER_BOOT   = 12;
static const uint32_t BUSY_TIMEOUT_MS   = 90000;
static const uint32_t MAKER_MIN_GAP_MS  = 180000;  // the panel maker's guidance
static const uint32_t SWEEP_GAP_MS      = 60000;

struct Timing { uint32_t push, pon, drf, pof; bool timedOut; };

// The driver's EPD_* macros expect to expand inside the display class (they
// call writecommanddata and name the SPI handle `spi`), hence a subclass.
class BenchPaper : public EPaper {
 public:
  // One refresh of the sprite, bypassing EPaper::update() so each phase can be
  // timed, the temperature forced, or the waveform cut short.
  //   forceTemp < 0: leave the controllers on their own sensor.
  //   abortMs   > 0: hardware-reset that long after DRF instead of waiting.
  Timing refresh(int forceTemp, uint32_t abortMs) {
    SPIClass& spi = getSPIinstance();
    Timing t = {0, 0, 0, 0, false};
    EPD_INIT();

    uint32_t t0 = millis();
    EPD_PUSH_NEW_COLORS(_width, _height, _img8);
    t.push = millis() - t0;

    if (forceTemp >= 0) {
      uint8_t temp = (uint8_t)forceTemp;
      both(RE0_CCSET, CCSET_V_LOCK, sizeof(CCSET_V_LOCK));
      both(RE5_TSSET, &temp, 1);
    }

    both(R04_PON, nullptr, 0);
    t.pon = waitBusy(t.timedOut);
    delay(30);
    both(R12_DRF, DRF_V, sizeof(DRF_V));
    if (abortMs) {
      delay(abortMs);
      t.drf = abortMs;
      // The reset drops the rails without the POF sequence; re-init and send
      // POF anyway so the panel is never left to sit at drive voltage.
      EPD_INIT();
    } else {
      t.drf = waitBusy(t.timedOut);
      delay(30);
    }
    both(R02_POF, POF_V, sizeof(POF_V));
    t.pof = waitBusy(t.timedOut);
    delay(30);
    EPD_SLEEP();
    return t;
  }

  // TSC (0x40) on the master: two raw bytes, or 0xFFFF-ish noise if the board
  // has no MISO to the panel. Only a hint that TSSET "took".
  void readTemp(uint8_t out[2]) {
    SPIClass& spi = getSPIinstance();
    EPD_INIT();
    spi.beginTransaction(SPISettings(SPI_FREQUENCY, MSBFIRST, TFT_SPI_MODE));
    digitalWrite(TFT_CS, LOW);
    DC_C;
    spi.transfer(R40_TSC);
    DC_D;
    bool timedOut = false;
    waitBusy(timedOut);
    out[0] = spi.transfer(0x00);
    out[1] = spi.transfer(0x00);
    digitalWrite(TFT_CS, HIGH);
    spi.endTransaction();
    EPD_SLEEP();
  }

 private:
  // CS1 low while writecommanddata drives CS: the command reaches both chips.
  void both(uint8_t cmd, const uint8_t* data, uint16_t n) {
    digitalWrite(TFT_CS1, LOW);
    if (n) writecommanddata(cmd, data, n); else writecommand(cmd);
    digitalWrite(TFT_CS1, HIGH);
  }

  uint32_t waitBusy(bool& timedOut) {
    uint32_t t0 = millis();
    delay(10);
    while (!digitalRead(TFT_BUSY)) {
      if (millis() - t0 > BUSY_TIMEOUT_MS) { timedOut = true; break; }
      delay(5);
    }
    return millis() - t0;
  }
};

BenchPaper epaper;
static uint8_t  g_abortsSinceFull = 0, g_abortsThisBoot = 0;
static uint32_t g_lastRefreshMs = 0;

// The six inks as bands, with the trial's own parameters in the white band.
static void drawColourCard(const char* label) {
  struct Bar { uint8_t ink; uint8_t text; const char* name; };
  const Bar bars[6] = {
    {TFT_WHITE,  TFT_BLACK, "WHITE"},  {TFT_BLACK, TFT_WHITE, "BLACK"},
    {TFT_YELLOW, TFT_BLACK, "YELLOW"}, {TFT_RED,   TFT_WHITE, "RED"},
    {TFT_BLUE,   TFT_WHITE, "BLUE"},   {TFT_GREEN, TFT_WHITE, "GREEN"}};
  int16_t W = epaper.width(), H = epaper.height(), bandH = H / 6;
  epaper.fillScreen(TFT_WHITE);
  epaper.setTextSize(6);
  for (uint8_t i = 0; i < 6; i++) {
    int16_t y = i * bandH, h = (i == 5) ? (H - y) : bandH;
    epaper.fillRect(0, y, W, h, bars[i].ink);
    epaper.setTextColor(bars[i].text);
    epaper.drawString(bars[i].name, 40, y + 40);
  }
  epaper.setTextColor(TFT_BLACK);
  epaper.drawString(label, 40, 150);
  epaper.drawRect(0, 0, W, H, TFT_BLACK);
}

// What an aborted refresh would actually be asked to show: large type, a solid
// band, a checker and hairlines, all in the black and white inks only.
static void drawMonoCard(const char* label) {
  int16_t W = epaper.width(), H = epaper.height();
  epaper.fillScreen(TFT_WHITE);
  epaper.setTextColor(TFT_BLACK);
  epaper.setTextSize(8);
  epaper.drawString("Battery low,", 60, 120);
  epaper.drawString("charge me", 60, 220);
  epaper.setTextSize(5);
  epaper.drawString(label, 60, 380);

  epaper.fillRect(0, 520, W, 220, TFT_BLACK);
  epaper.setTextColor(TFT_WHITE);
  epaper.setTextSize(6);
  epaper.drawString("white on black", 60, 600);

  for (int16_t y = 0; y < 6; y++)            // 60 px checker
    for (int16_t x = 0; x < W / 60; x++)
      if ((x + y) & 1) epaper.fillRect(x * 60, 820 + y * 60, 60, 60, TFT_BLACK);
  for (int16_t i = 0; i < 40; i++)           // hairlines, 1..4 px, widening gaps
    epaper.fillRect(60 + i * 27, 1260, 1 + i / 10, 260, TFT_BLACK);
  epaper.drawRect(0, 0, W, H, TFT_BLACK);
}

static void report(const char* what, const Timing& t) {
  Serial.printf("%s: push %lu ms, PON %lu ms, DRF %lu ms, POF %lu ms%s\n", what,
                (unsigned long)t.push, (unsigned long)t.pon, (unsigned long)t.drf,
                (unsigned long)t.pof, t.timedOut ? "  ** BUSY TIMED OUT **" : "");
}

static void warnIfSoon() {
  if (g_lastRefreshMs && millis() - g_lastRefreshMs < MAKER_MIN_GAP_MS)
    Serial.printf("note: %lu s since the last refresh (maker's guidance: >= 180 s)\n",
                  (unsigned long)((millis() - g_lastRefreshMs) / 1000));
}

static Timing fullRefresh(int forceTemp) {
  warnIfSoon();
  Timing t = epaper.refresh(forceTemp, 0);
  g_lastRefreshMs = millis();
  g_abortsSinceFull = 0;
  return t;
}

static void cmdTemp(int degC) {
  char label[40];
  snprintf(label, sizeof(label), "TSSET %d C", degC);
  drawColourCard(label);
  report(label, fullRefresh(degC));
}

static void cmdSweep() {
  const int temps[] = {25, 30, 35, 40, 45, 50};
  const uint8_t n = sizeof(temps) / sizeof(temps[0]);
  uint32_t drf[n];
  for (uint8_t i = 0; i < n; i++) {
    if (i) { Serial.printf("sweep: waiting %lu s\n", (unsigned long)(SWEEP_GAP_MS / 1000)); delay(SWEEP_GAP_MS); }
    char label[40];
    snprintf(label, sizeof(label), "TSSET %d C", temps[i]);
    drawColourCard(label);
    Timing t = fullRefresh(temps[i]);
    report(label, t);
    drf[i] = t.drf;
  }
  Serial.println("sweep: degC -> DRF ms");
  for (uint8_t i = 0; i < n; i++) Serial.printf("  %d -> %lu\n", temps[i], (unsigned long)drf[i]);
}

static void cmdAbort(uint32_t ms) {
  if (ms < 100 || ms > 15000) { Serial.println("abort: 100..15000 ms"); return; }
  if (g_abortsThisBoot >= ABORTS_PER_BOOT) { Serial.println("abort: this boot's ration is spent"); return; }
  if (g_abortsSinceFull >= ABORTS_PER_FULL) { Serial.println("abort: run a full refresh (w or b) first"); return; }
  char label[40];
  snprintf(label, sizeof(label), "abort %lu ms", (unsigned long)ms);
  drawMonoCard(label);
  Timing t = epaper.refresh(-1, ms);
  g_lastRefreshMs = millis();
  g_abortsSinceFull++;
  g_abortsThisBoot++;
  report(label, t);
  Serial.printf("abort: %u since a full refresh, %u this boot\n", g_abortsSinceFull, g_abortsThisBoot);
}

static void help() {
  Serial.println("b | w | t <degC> | s | a <ms> | r   (see the file header)");
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  epaper.begin();
  Serial.printf("bench: %dx%d, psram free %u — idle until a command\n", epaper.width(),
                epaper.height(), (unsigned)ESP.getFreePsram());
  help();
}

void loop() {
  if (!Serial.available()) { delay(20); return; }
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (!line.length()) return;
  long arg = line.substring(1).toInt();
  switch (line[0]) {
    case 'b': drawColourCard("stock"); report("stock", fullRefresh(-1)); break;
    case 'w': epaper.fillScreen(TFT_WHITE); report("white", fullRefresh(-1)); break;
    case 't':
      if (line.length() < 2 || arg < 0 || arg > 60) { Serial.println("t: 0..60 C"); break; }
      cmdTemp((int)arg);
      break;
    case 's': cmdSweep(); break;
    case 'a': cmdAbort((uint32_t)arg); break;
    case 'r': {
      uint8_t raw[2];
      epaper.readTemp(raw);
      Serial.printf("TSC raw: 0x%02X 0x%02X\n", raw[0], raw[1]);
      break;
    }
    default: help();
  }
}
