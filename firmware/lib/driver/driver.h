// Seeed_GFX panel + board selection for the Featherframe.
//
// Combo 511 = the 10.3" monochrome ePaper (E Ink ED103TC2, 1404x1872, 16-gray,
// IT8951 T-CON), pulling in User_Setups/Setup511_Seeed_XIAO_EPaper_10inch3.h.
// The board define wires up the EE03 driver board's control/enable pins.
//
// Do not change these unless you swap panels — they must match your hardware.

#define BOARD_SCREEN_COMBO 511
#define USE_XIAO_EPAPER_DISPLAY_BOARD_EE03
