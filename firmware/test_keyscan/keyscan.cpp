// Throwaway key-pin discovery build (W-574 step 0).
// Enables pullups on all safe, unused XIAO ESP32-S3 pins and prints any
// pin that goes LOW. Press each KEY on the EE03 in turn; the serial log
// tells us the real GPIO mapping. Not part of the shipping firmware.
#include <Arduino.h>

// Candidate GPIOs on the XIAO ESP32-S3 header, excluding panel SPI pins
// (driver.h combo 511 uses them) and the battery ADC.
static const int CANDIDATES[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 43, 44};
static const int N = sizeof(CANDIDATES) / sizeof(CANDIDATES[0]);
static int last[N];

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("keyscan: press each KEY button in turn");
  for (int i = 0; i < N; i++) {
    pinMode(CANDIDATES[i], INPUT_PULLUP);
    last[i] = HIGH;
  }
}

// Periodic state dump so a late-attaching serial reader still sees the levels.
static uint32_t lastDump = 0;
void dumpStates() {
  if (millis() - lastDump < 2000) return;
  lastDump = millis();
  Serial.print("states:");
  for (int i = 0; i < N; i++) Serial.printf(" %d=%d", CANDIDATES[i], digitalRead(CANDIDATES[i]));
  Serial.println();
}

void loop() {
  for (int i = 0; i < N; i++) {
    int v = digitalRead(CANDIDATES[i]);
    if (v != last[i]) {
      Serial.printf("GPIO%d -> %s\n", CANDIDATES[i], v == LOW ? "LOW (pressed)" : "HIGH (released)");
      last[i] = v;
    }
  }
  dumpStates();
  delay(10);
}
