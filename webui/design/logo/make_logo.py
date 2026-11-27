# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""Build the Lamplighter logo set from the stylized lamp image.

Input:  source/lamplighter-gemini.webp (Gemini-stylized photo of a gas lamp)
Steps:  1. cut the lamp out of its painted background (rembg, u2net model)
        2. trim the post where it fades into the old vignette
        3. monochrome: iron -> solid tint, glass -> see-through,
           flame -> solid tint (a lantern with a flame in it)
Output: dist/ (see README.md in this directory)

Run in a container:  python make_logo.py [--preview]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).parent
WORK = HERE / "work"
DIST = HERE / "dist"


def cutout() -> Image.Image:
    cached = WORK / "cutout-u2net.png"
    if cached.exists():
        return Image.open(cached).convert("RGBA")
    from rembg import new_session, remove      # only needed on first run
    out = remove(Image.open(HERE / "source/lamplighter-gemini.webp").convert("RGBA"),
                 session=new_session("u2net"))
    WORK.mkdir(exist_ok=True)
    out.save(cached)
    return out


def trim_post(im: Image.Image, keep_ratio: float = 0.93) -> Image.Image:
    """Cut the post with a hard edge before it fades into the old vignette."""
    a = im.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    left, top, right, bottom = a.getbbox()
    cut = top + int((bottom - top) * keep_ratio)
    return im.crop((left, top, right, cut))


def keep_large_regions(mask: Image.Image, min_area: int) -> Image.Image:
    """Keep only connected regions (4-neighbour) of at least min_area pixels."""
    w, h = mask.size
    src = mask.load()
    out = Image.new("L", mask.size, 0)
    dst = out.load()
    seen = bytearray(w * h)
    for y0 in range(h):
        for x0 in range(w):
            if src[x0, y0] < 128 or seen[y0 * w + x0]:
                continue
            stack, region = [(x0, y0)], []
            seen[y0 * w + x0] = 1
            while stack:
                x, y = stack.pop()
                region.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and src[nx, ny] >= 128:
                        seen[ny * w + nx] = 1
                        stack.append((nx, ny))
            if len(region) >= min_area:
                for x, y in region:
                    dst[x, y] = 255
    return out


def masks(im: Image.Image) -> tuple[Image.Image, Image.Image]:
    """(solid, flame) masks.

    Glass panes are the *large* bright areas inside the lamp; the painted
    iron's orange highlight rims are thin lines. An opening (erode, then
    dilate) keeps the panes and drops the rims, so the rims become iron.
    """
    rgb = im.convert("RGB")
    sat = rgb.convert("HSV").getchannel("S")
    lamp = im.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    lum = rgb.convert("L")                                     # ITU-R 601 luma
    bright = Image.composite(lum.point(lambda v: 255 if v > 80 else 0), Image.new("L", im.size, 0), lamp)
    flame_core = Image.composite(
        Image.merge("RGB", (lum.point(lambda v: 255 if v > 205 else 0),
                            sat.point(lambda v: 255 if v < 120 else 0),
                            lamp)).convert("L").point(lambda v: 255 if v > 254 else 0),
        Image.new("L", im.size, 0), lamp)
    # glass = large bright regions (opening removes thin highlight rims)
    glass = bright.filter(ImageFilter.MinFilter(11)).filter(ImageFilter.MaxFilter(11))
    glass = Image.composite(Image.new("L", im.size, 0), glass, flame_core)   # flame is not glass
    glass = keep_large_regions(glass, min_area=1500)                        # panes, not highlights
    solid = Image.composite(Image.new("L", im.size, 0), lamp, glass)        # lamp minus glass
    flame = flame_core.filter(ImageFilter.MedianFilter(5))
    soften = lambda m: m.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    return soften(solid), soften(flame)


def mono(im: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    solid, flame = masks(im)
    shape = Image.new("L", im.size, 0)
    shape.paste(255, mask=solid)
    shape.paste(255, mask=flame)
    out = Image.new("RGBA", im.size, color + (0,))
    out.putalpha(shape)
    return out


if __name__ == "__main__":
    lamp = trim_post(cutout())
    if "--preview" in sys.argv:
        WORK.mkdir(exist_ok=True)
        m = mono(lamp, (90, 90, 90))
        sheet = Image.new("RGB", (1500, 600), (236, 233, 228))
        big = m.resize((int(m.width * 560 / m.height), 560), Image.LANCZOS)
        sheet.paste(big, (40, 20), big)
        x = 500
        for size in (128, 64, 48, 32, 16):
            small = m.resize((max(1, int(m.width * size / m.height)), size), Image.LANCZOS)
            sheet.paste(small, (x, 300 - size // 2), small)
            x += size + 60
        sheet.save(WORK / "preview-mono.png")
        print("wrote work/preview-mono.png", lamp.size)


# --- colors (contrast-checked; see README.md) ---------------------------------
FAMILIES = {
    "purple": {"on_dark": "#B9A6E0", "on_light": "#5B3F96", "favicon": "#8466C4"},
    "blue":   {"on_dark": "#7FB8E1", "on_light": "#2C5080", "favicon": "#3D8BC4"},
}


def rgb(hex_: str) -> tuple[int, int, int]:
    return tuple(int(hex_[i:i + 2], 16) for i in (1, 3, 5))


def head_square(m: Image.Image, head_ratio: float = 0.69, pad: float = 0.06) -> Image.Image:
    """The lantern head (cap to the bottom of the scroll brackets), centred in a square."""
    head = m.crop((0, 0, m.width, int(m.height * head_ratio)))
    head = head.crop(head.getchannel("A").getbbox())
    side = int(max(head.size) * (1 + 2 * pad))
    # Transparent pixels carry the icon's own color, not black: resizing mixes
    # neighbouring RGB values, and hidden black would show as a dark fringe.
    r, g, b, _ = m.getpixel(next((x, y) for y in range(m.height) for x in range(m.width)
                                 if m.getpixel((x, y))[3] == 255))
    sq = Image.new("RGBA", (side, side), (r, g, b, 0))
    sq.paste(head, ((side - head.width) // 2, (side - head.height) // 2), head)
    return sq


def comparison_sheet(lamp: Image.Image) -> Image.Image:
    backgrounds = [("dark sidebar", "#2D1E29", "on_dark"), ("login page", "#5E2750", "on_dark"),
                   ("light sidebar", "#D0BEC8", "on_light"), ("light page", "#E4E1DB", "on_light"),
                   ("white tab", "#FFFFFF", "favicon"), ("dark tab", "#202124", "favicon")]
    W, H = 260, 300
    sheet = Image.new("RGB", (W * len(backgrounds), H * 2), (255, 255, 255))
    for row, family in enumerate(("purple", "blue")):
        for col, (_, bg, use) in enumerate(backgrounds):
            tile = Image.new("RGBA", (W, H), rgb(bg) + (255,))
            m = mono(lamp, rgb(FAMILIES[family][use]))
            if use == "favicon":
                icon = head_square(m)
                for size, x in ((64, 30), (32, 130), (16, 200)):
                    small = icon.resize((size, size), Image.LANCZOS)
                    tile.alpha_composite(small, (x, 150 - size // 2))
            else:
                big = m.resize((int(m.width * 260 / m.height), 260), Image.LANCZOS)
                tile.alpha_composite(big, ((W - big.width) // 2, 20))
                icon = head_square(m).resize((22, 22), Image.LANCZOS)   # sidebar badge size
                tile.alpha_composite(icon, (W - 34, H - 34))
            sheet.paste(tile.convert("RGB"), (col * W, row * H))
    return sheet


if __name__ == "__main__" and "--compare" in sys.argv:
    comparison_sheet(trim_post(cutout())).save(WORK / "compare.png")
    print("wrote work/compare.png")


def small_icon(lamp: Image.Image, color: tuple[int, int, int], size: int) -> Image.Image:
    """Icon for small sizes: the lantern head with strokes thickened so each
    line is still about 1.5 px wide at `size` (optical sizing)."""
    solid, flame = masks(lamp)
    shape = Image.new("L", lamp.size, 0)
    shape.paste(255, mask=solid)
    shape.paste(255, mask=flame)
    m = Image.new("RGBA", lamp.size, color + (0,))
    m.putalpha(shape)
    sq = head_square(m, pad=0.02)
    scale = sq.width / size                                   # source px per output px
    grow = max(1, int(scale * 0.55)) | 1                      # odd kernel; ~0.55 output px each side
    a = sq.getchannel("A").filter(ImageFilter.MaxFilter(grow))
    sq.putalpha(a)
    return sq.resize((size, size), Image.LANCZOS)


if __name__ == "__main__" and "--small" in sys.argv:
    lamp = trim_post(cutout())
    sheet = Image.new("RGB", (16 * 8 * 2 + 32 * 8 * 2 + 100, 32 * 8 + 40), (255, 255, 255))
    x = 10
    for fam in ("purple", "blue"):
        c = rgb(FAMILIES[fam]["favicon"])
        for size in (16, 32):
            for bg, y0 in (((255, 255, 255), 10),):
                ic = small_icon(lamp, c, size)
                tile = Image.new("RGBA", (size, size), bg + (255,))
                tile.alpha_composite(ic)
                big = tile.resize((size * 8, size * 8), Image.NEAREST)   # show the real pixels
                sheet.paste(big.convert("RGB"), (x, y0))
                x += size * 8 + 20
    sheet.save(WORK / "small-icons.png")
    print("wrote work/small-icons.png")


# --- the final set ------------------------------------------------------------

def shape_mask(lamp: Image.Image) -> Image.Image:
    solid, flame = masks(lamp)
    shape = Image.new("L", lamp.size, 0)
    shape.paste(255, mask=solid)
    shape.paste(255, mask=flame)
    return shape


def tinted(mask: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    out = Image.new("RGBA", mask.size, color + (0,))
    out.putalpha(mask)
    return out


def trace_svg(mask: Image.Image, path: Path, title: str) -> None:
    """Vectorise a mask with potrace; the result is filled with currentColor,
    so CSS (the theme tokens) decides its color."""
    import subprocess
    import tempfile
    big = mask.resize((mask.width * 3, mask.height * 3), Image.LANCZOS).point(lambda v: 0 if v >= 128 else 255)
    with tempfile.TemporaryDirectory() as tmp:
        pbm = Path(tmp) / "m.pbm"
        big.convert("1").save(pbm)
        svg = subprocess.run(["potrace", "-s", "--flat", "-t", "8", "-a", "1.1", "-O", "0.4", "-o", "-", str(pbm)],
                             check=True, capture_output=True, text=True).stdout
    svg = svg[svg.index("<svg"):]
    svg = svg.replace('fill="#000000"', 'fill="currentColor"')
    svg = svg.replace("<svg ", f'<svg role="img" aria-label="{title}" ', 1)
    svg = svg.replace(">", f"><title>{title}</title>", 1)
    path.write_text("<!-- Lamplighter logo. SPDX-License-Identifier: GPL-2.0-or-later -->\n" + svg)


def build() -> None:
    DIST.mkdir(exist_ok=True)
    lamp = trim_post(cutout())
    shape = shape_mask(lamp)
    full_h = 1024
    # colour original (cut out, post trimmed)
    color = lamp.resize((int(lamp.width * full_h / lamp.height), full_h), Image.LANCZOS)
    color.save(DIST / "lamplighter-color.png")
    for fam, tints in FAMILIES.items():
        for use in ("on_dark", "on_light"):
            full = tinted(shape, rgb(tints[use]))
            full.resize((int(full.width * full_h / full.height), full_h), Image.LANCZOS).save(
                DIST / f"lamplighter-{fam}-{use.replace('_', '-')}.png")
            head_square(full).resize((512, 512), Image.LANCZOS).save(
                DIST / f"lamplighter-mark-{fam}-{use.replace('_', '-')}.png")
        fav = rgb(tints["favicon"])
        sizes = {s: small_icon(lamp, fav, s) for s in (16, 32, 48)}
        sizes[48].save(DIST / f"favicon-{fam}.ico", sizes=[(16, 16), (32, 32), (48, 48)],
                       append_images=[sizes[16], sizes[32]])
        for s in (32, 192, 512):
            (sizes.get(s) or head_square(tinted(shape, fav)).resize((s, s), Image.LANCZOS)).save(
                DIST / f"favicon-{fam}-{s}.png")
        # phones want an opaque square: the lamp on the aubergine of the login page
        touch = Image.new("RGBA", (180, 180), (0x5E, 0x27, 0x50, 255))
        mark = head_square(tinted(shape, rgb(tints["on_dark"])), pad=0.14).resize((180, 180), Image.LANCZOS)
        touch.alpha_composite(mark)
        touch.convert("RGB").save(DIST / f"apple-touch-icon-{fam}.png")
    # vectors (one shape each; colour comes from CSS via currentColor)
    trace_svg(shape, DIST / "lamplighter.svg", "Lamplighter")
    head = shape.crop((0, 0, shape.width, int(shape.height * 0.69)))
    head = head.crop(head.getbbox())
    side = int(max(head.size) * 1.04)
    sq = Image.new("L", (side, side), 0)
    sq.paste(head, ((side - head.width) // 2, (side - head.height) // 2))
    trace_svg(sq, DIST / "lamplighter-mark.svg", "Lamplighter")
    print("wrote", ", ".join(sorted(p.name for p in DIST.iterdir())))


if __name__ == "__main__" and "--build" in sys.argv:
    build()


def social_preview() -> None:
    """GitHub social preview card: 1280x640, the lamp on the aubergine gradient."""
    from PIL import ImageDraw, ImageFont
    W, H = 1280, 640
    card = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(card)
    a, b = (0x77, 0x21, 0x6F), (0x2C, 0x00, 0x1E)          # the login page's aubergine
    for x in range(W):                                    # diagonal-ish gradient
        for y in range(0, H, 4):
            t = min(1.0, max(0.0, (x / W) * 0.75 + (y / H) * 0.25))
            card.paste(tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)), (x, y, x + 1, y + 4))
    lamp = tinted(shape_mask(trim_post(cutout())), rgb("#7FB8E1"))
    lh = 520
    lamp = lamp.resize((int(lamp.width * lh / lamp.height), lh), Image.LANCZOS)
    card.paste(lamp, (150, (H - lh) // 2), lamp)
    fonts = ["/usr/share/fonts/truetype/ubuntu/Ubuntu-M.ttf", "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    font = lambda i, s: ImageFont.truetype(next(f for f in fonts[i:] if Path(f).exists()), s)
    x0 = 150 + lamp.width + 80
    d.text((x0, 205), "Lamplighter", fill=(0xDF, 0xDB, 0xD2), font=font(0, 92))
    d.text((x0, 330), "A fork of the Robinhood Policy Engine", fill=(0xC9, 0xB8, 0xC4), font=font(1, 34))
    d.text((x0, 378), "for Lustre and POSIX filesystems", fill=(0xC9, 0xB8, 0xC4), font=font(1, 34))
    d.text((x0, 450), "lux-find · lux-du · lux-report · web UI", fill=(0x7F, 0xB8, 0xE1), font=font(1, 28))
    card.save(DIST / "lamplighter-social-preview.png", optimize=True)
    print("wrote dist/lamplighter-social-preview.png")


if __name__ == "__main__" and "--social" in sys.argv:
    social_preview()
