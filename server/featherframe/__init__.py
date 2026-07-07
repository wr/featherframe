"""Featherframe server.

A considerate tenant on a BirdNET-Pi: reads detections read-only, renders
Audubon-style plates for an e-paper frame, and otherwise stays idle.
"""

__version__ = "1.0.0"

# The panel is fixed hardware: Seeed EE03 / E Ink ED103TC2, portrait.
PANEL_WIDTH = 1404
PANEL_HEIGHT = 1872
