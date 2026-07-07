"""Rendering pipeline for Featherframe.

species -> composed 1404x1872 grayscale plate -> dithered framebuffer.

Submodules:
    theme        layout + tonal constants (the "house style")
    typography   font loading, faux small caps, caption blocks, fallback plate
    plate        Audubon plate loading, content-aware crop, paper normalisation
    provider     ArtProvider interface + AudubonProvider (v1)
    compose      single-detection full-frame composition
    collage      daily grid composition
    finish       contrast + dithering to 16 / 1 levels
    framebuffer  pack to the panel's native wire format (FFF) + ETag
    pipeline     orchestration used by the scheduler and `make preview`
"""
