// Bare panel test for the EE02 board + 13.3" Spectra 6 colour panel (combo 510):
// the six inks as full-width bars, each labelled. One full refresh (~20-30 s,
// no partial refresh on this panel). Build: pio run -e ee02_hello -t upload
#include "driver.h"        // Seeed_GFX board/panel selection (-DFF_BOARD_EE02)
#include "TFT_eSPI.h"

EPaper epaper;

void setup() {
  Serial.begin(115200);
  delay(2000);
  // Report the panel pins the build actually compiled with.
  Serial.printf("PINS: ENABLE=%d RST=%d BUSY=%d CS=%d CS1=%d DC=%d MOSI=%d SCLK=%d\n",
                TFT_ENABLE, TFT_RST, TFT_BUSY, TFT_CS, TFT_CS1, TFT_DC, TFT_MOSI, TFT_SCLK);
  Serial.println("hello: begin()");
  epaper.begin();
  Serial.printf("hello: %dx%d, psram free %u\n", epaper.width(), epaper.height(),
                (unsigned)ESP.getFreePsram());

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
  // A frame on the white bar, so the panel's edges are visible on the glass.
  epaper.drawRect(0, 0, W, H, TFT_BLACK);

  Serial.println("hello: update() — full refresh");
  uint32_t t0 = millis();
  epaper.update();
  Serial.printf("hello: done in %lu ms\n", (unsigned long)(millis() - t0));
}

void loop() {}
