// Improv Wi-Fi over USB serial (W-839): how the dashboard's USB flasher
// (esp-web-tools) gives a freshly flashed frame its Wi-Fi, and learns whether
// it already has one.
//
// A task reads the USB CDC port for Improv v1 packets for as long as the frame
// is awake and answers them: its state (PROVISIONED once joined — a board
// flashed without erasing NVS rejoins by itself, and the flasher then skips
// its Wi-Fi form), what it is, the networks it can see, and new credentials,
// which it joins and keeps exactly as the captive portal would. The captive
// portal stays as it is; Improv runs beside it.
//
// Spec: https://www.improv-wifi.com/serial/
#pragma once

struct ImprovHooks {
  // The dashboard's address once the frame knows it ("" until then): handed
  // to the flasher as the page to open next.
  const char* (*serverUrl)();
  // Whether a network is saved (the frame is still joining it at boot).
  bool (*hasSavedWifi)();
  // New credentials joined. Return true if the caller picks the connection up
  // itself (the captive portal is open: it closes and boot carries on);
  // false and the frame restarts onto the new network.
  bool (*onJoined)();
};

// Starts the Improv task. `name`/`version`/`board` answer REQUEST_INFO.
void improvBegin(const char* name, const char* version, const char* board,
                 const ImprovHooks& hooks);
