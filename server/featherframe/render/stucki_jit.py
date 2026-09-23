"""The colour panel's Stucki diffusion, compiled (Numba).

`spectra._diffuse_stucki_py` is the reference: this is the same loop, line for
line, in the same order of float64 operations, so it picks the same ink for
every pixel (a test holds the two equal). It is ~10 s of CPU in Python on
the box and a fraction of a second compiled. Numba is optional: where it
does not install (a 32-bit Pi), `spectra` falls back to the Python loop.
Importing this module raises ImportError when Numba is absent.
"""
from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True, nogil=True)
def diffuse(mapped, inkset, pal_n, pal_q, choices, n_choices, stucki, white):  # pragma: no cover - compiled
    h, w = inkset.shape
    out = np.empty((h, w), dtype=np.uint8)
    pad = 2
    n = (w + 2 * pad) * 3
    e0 = np.zeros(n)
    e1 = np.zeros(n)
    e2 = np.zeros(n)
    for y in range(h):
        ltr = (y % 2 == 0)
        sgn = 1 if ltr else -1
        for xi in range(w):
            x = xi if ltr else w - 1 - xi
            out[y, x] = white
            i = (x + pad) * 3
            er = e0[i]
            eg = e0[i + 1]
            eb = e0[i + 2]
            r = np.float64(mapped[y, x, 0]) + er
            g = np.float64(mapped[y, x, 1]) + eg
            b = np.float64(mapped[y, x, 2]) + eb
            if r > 0.995 and g > 0.995 and b > 0.995 and er == 0.0 and eg == 0.0 and eb == 0.0:
                continue                                  # clean paper: white, no error
            rq = r ** 0.5 if r > 0.0 else 0.0
            gq = g ** 0.5 if g > 0.0 else 0.0
            bq = b ** 0.5 if b > 0.0 else 0.0
            best = 0
            bd = 1e9
            s = inkset[y, x]
            for c in range(n_choices[s]):
                k = choices[s, c]
                d = (rq - pal_q[k, 0]) ** 2 + (gq - pal_q[k, 1]) ** 2 + (bq - pal_q[k, 2]) ** 2
                if d < bd:
                    best = k
                    bd = d
            out[y, x] = best
            dr = (r - pal_n[best, 0]) / 42.0
            dg = (g - pal_n[best, 1]) / 42.0
            db = (b - pal_n[best, 2]) / 42.0
            if dr == 0.0 and dg == 0.0 and db == 0.0:
                continue
            for m in range(stucki.shape[0]):
                dx = stucki[m, 0]
                dy = stucki[m, 1]
                wt = np.float64(stucki[m, 2])
                t = (x + pad + dx * sgn) * 3
                if dy == 0:
                    e0[t] += dr * wt
                    e0[t + 1] += dg * wt
                    e0[t + 2] += db * wt
                elif dy == 1:
                    e1[t] += dr * wt
                    e1[t + 1] += dg * wt
                    e1[t + 2] += db * wt
                else:
                    e2[t] += dr * wt
                    e2[t + 1] += dg * wt
                    e2[t + 2] += db * wt
        e0 = e1
        e1 = e2
        e2 = np.zeros(n)
    return out
