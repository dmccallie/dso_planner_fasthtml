
from typing import Iterable, Sequence, Tuple

# ------------------------ Color scale utilities ------------------------------
# Lightweight replacement for JS `color-scales` (3+ stops, gamma bias, sRGB/linear)
# GPT 9/2/2025

"""
    SCORE_SCALE = ColorScale(
        vmin=0, vmax=100,
        colors=["#a50026", "#ffff00", "#1a9850"],
        gamma=0.5,
        space='linear'
    )
    bg = SCORE_SCALE.css_rgba(r["score"])
    fg = best_text_color(SCORE_SCALE.rgb01(r["score"]))
    Td(r["score"], style=f"background:{bg}; color:{fg};")
"""
"""
Alternative:

# pip install matplotlib
from matplotlib import cm, colors

cmap = cm.get_cmap("viridis")               # or 'RdYlGn', 'coolwarm', etc.
norm = colors.Normalize(vmin=min_alt, vmax=max_alt)

def css_rgba_alt(alt):
    r,g,b,a = cmap(norm(alt))
    return f"rgba({int(r*255)},{int(g*255)},{int(b*255)},{a:.3f})"

"""

def _hex_to_rgb01(h: str) -> Tuple[float, float, float]:
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(ch*2 for ch in h)
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return (r, g, b)


def _rgb01_to_css(rgb: tuple[float, float, float], alpha: float = 1.0) -> str:
    r, g, b = (max(0, min(255, int(round(c * 255)))) for c in rgb)
    a = max(0.0, min(1.0, float(alpha)))
    return f"rgba({r},{g},{b},{a})"


def _srgb_to_linear(c: float) -> float:
    return c/12.92 if c <= 0.04045 else ((c+0.055)/1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return 12.92*c if c <= 0.0031308 else 1.055*(c ** (1/2.4)) - 0.055


def _interp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class ColorScale:
    """Piecewise-linear color scale with gamma bias.
    colors: list of hex ('#a50026') or 0..1 RGB tuples
    space:  'srgb' (default) or 'linear' (interpolate in linear-light)
    gamma:  value-domain bias; <1 emphasises high end, >1 emphasises low end
    """
    def __init__(self, vmin: float, vmax: float, colors: Sequence[str|Sequence[float]], gamma: float = 1.0, space: str = 'linear'):
        self.vmin = float(vmin); self.vmax = float(vmax)
        self.colors = [(_hex_to_rgb01(c) if isinstance(c, str) else tuple(c)) for c in colors]
        self.gamma = float(gamma)
        self.space = space

    def _mix_rgb(self, a: tuple[float,float,float], b: tuple[float,float,float], t: float) -> Tuple[float,float,float]:
        if self.space == 'linear':
            la = tuple(_srgb_to_linear(c) for c in a)
            lb = tuple(_srgb_to_linear(c) for c in b)
            lc = tuple(_interp(la[i], lb[i], t) for i in range(3))
            return (_linear_to_srgb(lc[0]), _linear_to_srgb(lc[1]), _linear_to_srgb(lc[2]))
        else:
            return (_interp(a[0], b[0], t), _interp(a[1], b[1], t), _interp(a[2], b[2], t))

    def _t(self, v: float) -> float:
        if self.vmax == self.vmin:
            t = 0.5
        else:
            t = (float(v) - self.vmin) / (self.vmax - self.vmin)
        t = max(0.0, min(1.0, t))
        return t ** self.gamma if self.gamma != 1.0 else t

    def rgb01(self, v: float) -> tuple[float,float,float]:
        t = self._t(v)
        n = len(self.colors) - 1
        if n <= 0:
            return self.colors[0]
        seg = min(int(t * n), n-1)
        local = t * n - seg
        return self._mix_rgb(self.colors[seg], self.colors[seg+1], local)

    def css_rgba(self, v: float, alpha: float = 1.0) -> str:
        return _rgb01_to_css(self.rgb01(v), alpha)


def best_text_color(rgb01: tuple[float,float,float]) -> str:
    """Choose black/white text for contrast using WCAG relative luminance."""
    def f(c: float) -> float:
        return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4
    r, g, b = (f(x) for x in rgb01)
    L = 0.2126*r + 0.7152*g + 0.0722*b
    return "#000" if L > 0.5 else "#fff"

# Example scale similar to your JS snippet
SCORE_SCALE = ColorScale(
    vmin=0, vmax=100,
    colors=["#a50026", "#ffff00", "#1a9850"],
    gamma=0.5,         # bias toward the upper end a bit (like your example)
    space='linear'     # interpolate in linear light for smoother blends
)

# alternative using matplotlib
from matplotlib import cm, colors

class MapPlotLibColorScale:
    def __init__(self, model:str = "RdYlGn", vmin=20, vmax=90):
        self.cmap = cm.get_cmap(model)
        self.norm = colors.Normalize(vmin=float(vmin), vmax=float(vmax))

    def as_css_rgba(self, alt):
        r,g,b,a = self.cmap(self.norm(alt))
        return f"rgba({int(r*255)},{int(g*255)},{int(b*255)},{a:.3f})"
    
    def as_rgb_tuple(self, alt):
        r,g,b,a = self.cmap(self.norm(alt))
        return (r,g,b)



