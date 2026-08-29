// Bare panel test — Seeed's own GrayLevel16 example pattern, verbatim. The key
// bit my code was missing: an initial fillScreen(TFT_WHITE)+update() to clear the
// panel BEFORE initGrayMode(). Build: pio run -e hello -t upload
#include "driver.h"        // Seeed_GFX board/panel selection (combo 511, EE03)
#include "TFT_eSPI.h"

EPaper epaper;

void setup() {
  Serial.begin(115200);
  delay(2000);
  // Report the panel pins the build actually compiled with.
#ifdef TFT_ENABLE
  Serial.printf("PINS: ENABLE=%d ", TFT_ENABLE);
#else
  Serial.print("PINS: ENABLE=UNDEF ");
#endif
#ifdef TFT_RST
  Serial.printf("RST=%d ", TFT_RST);
#else
  Serial.print("RST=UNDEF ");
#endif
#ifdef TFT_BUSY
  Serial.printf("BUSY=%d ", TFT_BUSY);
#endif
#ifdef TFT_CS
  Serial.printf("CS=%d ", TFT_CS);
#endif
#ifdef TFT_MOSI
  Serial.printf("MOSI=%d ", TFT_MOSI);
#endif
#ifdef TFT_SCLK
  Serial.printf("SCLK=%d ", TFT_SCLK);
#endif
#ifdef ENABLE_EPAPER_BOARD_PIN_SETUPS
  Serial.print("[BOARD_PIN_SETUPS on] ");
#else
  Serial.print("[BOARD_PIN_SETUPS OFF] ");
#endif
  Serial.println();
  Serial.println("hello: begin()");
  epaper.begin();

  Serial.println("hello: clear (1-bit full refresh)");
  epaper.fillScreen(TFT_WHITE);
  epaper.update();                     // refresh once to clear the screen

  Serial.println("hello: gray bands");
  epaper.initGrayMode(GRAY_LEVEL16);
  const uint8_t gray[16] = {
    TFT_GRAY_0,  TFT_GRAY_1,  TFT_GRAY_2,  TFT_GRAY_3,
    TFT_GRAY_4,  TFT_GRAY_5,  TFT_GRAY_6,  TFT_GRAY_7,
    TFT_GRAY_8,  TFT_GRAY_9,  TFT_GRAY_10, TFT_GRAY_11,
    TFT_GRAY_12, TFT_GRAY_13, TFT_GRAY_14, TFT_GRAY_15};
  int16_t W = epaper.width(), H = epaper.height(), bandH = H / 16;
  for (uint8_t i = 0; i < 16; i++) {
    int16_t y = i * bandH, h = (i == 15) ? (H - y) : bandH;
    epaper.fillRect(0, y, W, h, gray[i]);
  }
  epaper.update();
  Serial.println("hello: done");
}

void loop() {}
