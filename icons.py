"""Small hand-drawn icon set used by the redesigned sidebar and Test console.

Plain Tkinter can't render SVG/icon fonts, and pulling in an icon pack means
either shipping binary assets or another dependency. These are drawn with
PIL's vector primitives instead - a handful of arcs, polygons and lines per
glyph - at whatever size and colour the caller asks for, then cached and
wrapped as a ``customtkinter.CTkImage`` ready to hand to any CTk widget's
``image=`` option.

    import icons
    icons.ctk_image('refresh', '#ffffff', size=28)
"""

from PIL import Image, ImageDraw
import customtkinter as ctk

_cache = {}


def _canvas(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _refresh(size, color):
    """AUTO: two curved arrows chasing each other, i.e. a cycle."""
    img, d = _canvas(size)
    m = size * 0.12
    w = max(2, size // 12)
    d.arc([m, m, size - m, size - m], start=25, end=300, fill=color, width=w)
    # Arrowhead on the open end of the arc.
    tip_x, tip_y = size - m, size * 0.28
    d.polygon([(tip_x, tip_y - size * 0.16), (tip_x + size * 0.16, tip_y),
              (tip_x - size * 0.03, tip_y + size * 0.14)], fill=color)
    return img


def _home(size, color):
    """HOME: a simple roofed house."""
    img, d = _canvas(size)
    m = size * 0.12
    peak = (size / 2, m)
    left = (m, size * 0.48)
    right = (size - m, size * 0.48)
    d.line([left, peak, right], fill=color, width=max(2, size // 12), joint="curve")
    body_top = size * 0.46
    d.rectangle([size * 0.24, body_top, size * 0.76, size - m], outline=color,
                width=max(2, size // 14))
    d.rectangle([size * 0.42, size * 0.66, size * 0.58, size - m], outline=color,
                width=max(1, size // 16))
    return img


def _download(size, color):
    """1st PULL (Load Test): an arrow dropping onto a tray - "load applied"."""
    img, d = _canvas(size)
    m = size * 0.18
    w = max(2, size // 12)
    cx = size / 2
    d.line([(cx, m), (cx, size * 0.6)], fill=color, width=w)
    d.polygon([(cx - size * 0.18, size * 0.42), (cx + size * 0.18, size * 0.42),
              (cx, size * 0.68)], fill=color)
    d.line([(m, size - m), (size - m, size - m)], fill=color, width=w)
    return img


def _ruler(size, color):
    """2nd PULL (Length Test): a ruler with tick marks."""
    img, d = _canvas(size)
    m = size * 0.16
    top = size * 0.32
    bottom = size * 0.68
    d.rectangle([m, top, size - m, bottom], outline=color, width=max(2, size // 14))
    ticks = 5
    for i in range(1, ticks):
        x = m + (size - 2 * m) * i / ticks
        d.line([(x, top), (x, top + (bottom - top) * 0.45)], fill=color,
               width=max(1, size // 20))
    return img


def _document(size, color):
    """TEST RESULT: a page with a checkmark."""
    img, d = _canvas(size)
    m = size * 0.2
    d.rectangle([m, size * 0.1, size - m, size - size * 0.1], outline=color,
                width=max(2, size // 14))
    cx, cy = size / 2, size * 0.58
    d.line([(cx - size * 0.14, cy), (cx - size * 0.03, cy + size * 0.12),
           (cx + size * 0.18, cy - size * 0.14)], fill=color,
           width=max(2, size // 12), joint="curve")
    return img


def _camera(size, color):
    img, d = _canvas(size)
    m = size * 0.16
    d.rounded_rectangle([m, size * 0.28, size - m, size - m * 0.6],
                         radius=size * 0.08, outline=color, width=max(2, size // 14))
    d.polygon([(size * 0.36, size * 0.28), (size * 0.42, size * 0.16),
              (size * 0.58, size * 0.16), (size * 0.64, size * 0.28)],
              fill=color)
    cx, cy, r = size / 2, size * 0.6, size * 0.16
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=max(2, size // 16))
    return img


def _barcode(size, color):
    img, d = _canvas(size)
    m = size * 0.14
    widths = [2, 1, 3, 1, 2, 1, 3, 2]
    x = m
    total = sum(widths) * 1.6
    scale = (size - 2 * m) / total
    for i, w in enumerate(widths):
        bar_w = w * scale
        if i % 2 == 0:
            d.rectangle([x, m, x + bar_w, size - m], fill=color)
        x += bar_w * 1.6
    return img


def _arrow_right(size, color):
    img, d = _canvas(size)
    cy = size / 2
    w = max(2, size // 10)
    d.line([(size * 0.18, cy), (size * 0.72, cy)], fill=color, width=w)
    d.polygon([(size * 0.6, cy - size * 0.18), (size * 0.85, cy),
              (size * 0.6, cy + size * 0.18)], fill=color)
    return img


def _triangle(size, color, pointing):
    """A solid triangle, drawn four times over and scaled down so its slopes
    come out smooth rather than stepped."""
    big = size * 4
    img, d = _canvas(big)
    near, far = big * 0.3, big * 0.75
    if pointing == 'left':
        near, far = big - near, big - far
    d.polygon([(near, big * 0.18), (far, big / 2), (near, big * 0.82)], fill=color)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def _triangle_left(size, color):
    return _triangle(size, color, 'left')


def _triangle_right(size, color):
    return _triangle(size, color, 'right')


def _gear(size, color):
    import math
    img, d = _canvas(size)
    cx, cy = size / 2, size / 2
    r_out, r_in = size * 0.4, size * 0.24
    teeth = 8
    pts = []
    for i in range(teeth * 2):
        angle = math.pi * i / teeth
        r = r_out if i % 2 == 0 else r_out * 0.78
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    d.polygon(pts, fill=color)
    # Punch the centre hole back to transparent.
    d.ellipse([cx - r_in, cy - r_in, cx + r_in, cy + r_in], fill=(0, 0, 0, 0))
    return img


def _swap(size, color):
    """COM Ports: two opposing arrows."""
    img, d = _canvas(size)
    w = max(2, size // 12)
    d.line([(size * 0.15, size * 0.35), (size * 0.75, size * 0.35)], fill=color, width=w)
    d.polygon([(size * 0.75, size * 0.24), (size * 0.9, size * 0.35),
              (size * 0.75, size * 0.46)], fill=color)
    d.line([(size * 0.25, size * 0.65), (size * 0.85, size * 0.65)], fill=color, width=w)
    d.polygon([(size * 0.25, size * 0.54), (size * 0.1, size * 0.65),
              (size * 0.25, size * 0.76)], fill=color)
    return img


def _play(size, color):
    img, d = _canvas(size)
    m = size * 0.22
    d.polygon([(m, size * 0.15), (m, size * 0.85), (size - m, size / 2)], fill=color)
    return img


def _bars(size, color):
    """Work Data: a small bar chart."""
    img, d = _canvas(size)
    m = size * 0.14
    heights = [0.4, 0.75, 0.55]
    bar_w = (size - 2 * m) / (len(heights) * 1.6)
    x = m
    for h in heights:
        top = size - m - (size - 2 * m) * h
        d.rectangle([x, top, x + bar_w, size - m], fill=color)
        x += bar_w * 1.6
    return img


def _shield(size, color):
    img, d = _canvas(size)
    m = size * 0.16
    d.polygon([(size / 2, m), (size - m, size * 0.3), (size - m, size * 0.55),
              (size / 2, size - m), (m, size * 0.55), (m, size * 0.3)],
              outline=color, width=max(2, size // 14))
    return img


def _question(size, color):
    img, d = _canvas(size)
    m = size * 0.12
    d.ellipse([m, m, size - m, size - m], outline=color, width=max(2, size // 14))
    # The hook of the "?", drawn as a partial arc rather than relying on a
    # scalable font - PIL's default text font is a fixed small bitmap.
    d.arc([size * 0.3, size * 0.26, size * 0.7, size * 0.6], start=200, end=80,
          fill=color, width=max(2, size // 14))
    d.line([(size * 0.5, size * 0.58), (size * 0.5, size * 0.68)], fill=color,
           width=max(2, size // 14))
    r = max(1.5, size * 0.045)
    cx, cy = size * 0.5, size * 0.8
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    return img


def _mail(size, color):
    img, d = _canvas(size)
    m = size * 0.16
    d.rectangle([m, size * 0.26, size - m, size - m], outline=color, width=max(2, size // 14))
    d.line([(m, size * 0.26), (size / 2, size * 0.56), (size - m, size * 0.26)],
           fill=color, width=max(2, size // 14), joint="curve")
    return img


def _power(size, color):
    img, d = _canvas(size)
    m = size * 0.2
    w = max(2, size // 12)
    d.arc([m, m, size - m, size - m], start=125, end=55, fill=color, width=w)
    d.line([(size / 2, size * 0.12), (size / 2, size * 0.5)], fill=color, width=w)
    return img


def _box(size, color):
    """Model - Part Number: a small package/cube."""
    img, d = _canvas(size)
    m = size * 0.16
    d.rectangle([m, m, size - m, size - m], outline=color, width=max(2, size // 14))
    d.line([(m, size / 2), (size - m, size / 2)], fill=color, width=max(1, size // 18))
    d.line([(size / 2, m), (size / 2, size - m)], fill=color, width=max(1, size // 18))
    return img


def _clipboard(size, color):
    img, d = _canvas(size)
    m = size * 0.18
    d.rounded_rectangle([m, size * 0.14, size - m, size - m * 0.7], radius=size * 0.06,
                         outline=color, width=max(2, size // 16))
    d.rectangle([size * 0.38, size * 0.06, size * 0.62, size * 0.2], fill=color)
    for i in range(3):
        y = size * 0.36 + i * size * 0.16
        d.line([(size * 0.3, y), (size * 0.7, y)], fill=color, width=max(1, size // 18))
    return img


def _chart(size, color):
    """Load & Length graph header: a simple trend line."""
    img, d = _canvas(size)
    m = size * 0.16
    d.line([(m, size - m), (m, m)], fill=color, width=max(1, size // 18))
    d.line([(m, size - m), (size - m, size - m)], fill=color, width=max(1, size // 18))
    d.line([(m, size * 0.7), (size * 0.45, size * 0.4), (size * 0.65, size * 0.55),
           (size - m, size * 0.2)], fill=color, width=max(2, size // 14), joint="curve")
    return img


def _list(size, color):
    """Lot Information header: simple list rows."""
    img, d = _canvas(size)
    m = size * 0.16
    for i in range(3):
        y = m + i * size * 0.3
        d.ellipse([m, y, m + size * 0.1, y + size * 0.1], fill=color)
        d.line([(m + size * 0.2, y + size * 0.05), (size - m, y + size * 0.05)],
               fill=color, width=max(1, size // 18))
    return img


def _alert(size, color):
    """Fault or caution: the standard warning triangle with a bang."""
    img, d = _canvas(size)
    m = size * 0.1
    w = max(2, size // 12)
    d.polygon([(size / 2, m), (size - m, size - m * 1.4), (m, size - m * 1.4)],
              outline=color, width=w)
    bar_top, bar_bottom = size * 0.38, size * 0.66
    d.line([(size / 2, bar_top), (size / 2, bar_bottom)], fill=color, width=w)
    dot = max(1, size // 14)
    d.ellipse([size / 2 - dot, size * 0.75 - dot,
               size / 2 + dot, size * 0.75 + dot], fill=color)
    return img


def _check(size, color):
    """Pass: a bare tick, no surrounding circle, so it reads at 16px."""
    img, d = _canvas(size)
    d.line([(size * 0.18, size * 0.52), (size * 0.42, size * 0.76),
            (size * 0.84, size * 0.24)],
           fill=color, width=max(2, size // 8), joint="curve")
    return img


def _info(size, color):
    """Neutral or in-progress: a lower-case i in a ring."""
    img, d = _canvas(size)
    m = size * 0.08
    w = max(2, size // 12)
    d.ellipse([m, m, size - m, size - m], outline=color, width=w)
    dot = max(1, size // 16)
    d.ellipse([size / 2 - dot, size * 0.3 - dot,
               size / 2 + dot, size * 0.3 + dot], fill=color)
    d.line([(size / 2, size * 0.45), (size / 2, size * 0.72)],
           fill=color, width=w)
    return img


_DRAWERS = {
    'refresh': _refresh, 'home': _home, 'download': _download, 'ruler': _ruler,
    'document': _document, 'camera': _camera, 'barcode': _barcode,
    'arrow_right': _arrow_right, 'gear': _gear, 'swap': _swap, 'play': _play,
    'bars': _bars, 'shield': _shield, 'question': _question, 'mail': _mail,
    'power': _power, 'box': _box, 'clipboard': _clipboard, 'chart': _chart,
    'list': _list, 'alert': _alert, 'check': _check, 'info': _info,
    'triangle_left': _triangle_left, 'triangle_right': _triangle_right,
}


def image(name, color, size=28):
    """The raw PIL image for one icon, drawn fresh (not cached)."""
    drawer = _DRAWERS.get(name)
    if drawer is None:
        raise KeyError(f"Unknown icon '{name}'")
    return drawer(size, color)


def ctk_image(name, color, size=28):
    """A cached CTkImage for `name` in `color` at `size`, ready for image=."""
    key = (name, color, size)
    cached = _cache.get(key)
    if cached is None:
        pil_image = image(name, color, size)
        cached = ctk.CTkImage(light_image=pil_image, dark_image=pil_image,
                              size=(size, size))
        _cache[key] = cached
    return cached
