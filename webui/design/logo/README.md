# Lamplighter logo

A Victorian gas street lamp. It started as Jonathan Mills's own photograph, which Google Gemini stylized (`source/lamplighter-gemini.webp`). `make_logo.py` turns that into the logo set in `dist/`.

## Build

The build runs in a container. Run it from this directory:

```sh
podman run --rm -v "$PWD:/logo" -w /logo docker.io/library/python:3.12-slim sh -c \
  'apt-get -qq update && apt-get -qq install -y potrace >/dev/null; pip install -q "rembg[cpu]" pillow; python make_logo.py --build'
```

The steps:
1. **Cut out the lamp:** rembg with the u2net model. Gemini painted a fake transparency checkerboard and a vignette into the image, so it has no real alpha channel.
2. **Trim the post:** cut it with a hard edge before it fades into the old vignette.
3. **Monochrome:**
   - the iron becomes solid
   - the glass panes become see-through (they are the *large* bright regions; the thin highlight rims count as iron)
   - the flame stays solid
4. **Small icons:** strokes are thickened before shrinking (optical sizing), so 16 px still reads as a lantern.
5. **Vectors:** potrace makes `lamplighter.svg` and `lamplighter-mark.svg`. Both are filled with `currentColor`, so CSS sets their color.

`--preview`, `--compare` and `--small` write the review images to `work/`. That directory is not committed: it also holds the rembg cutout cache.

## Colors

Every tint was contrast-checked: at least 3:1 against the backgrounds it is used on.

| Family | On dark backgrounds | On light backgrounds | Favicon (white and dark browser tabs) |
|---|---|---|---|
| **blue (in use)** | Sky `#7FB8E1` | Bolin Creek `#2C5080` | `#3D8BC4` |
| purple (alternative) | Lavender `#B9A6E0` | Deep purple `#5B3F96` | `#8466C4` |

## Files in `dist/`

| File | Use |
|---|---|
| `lamplighter.svg`, `lamplighter-mark.svg` | The full lamp and the lantern head. They are vectors filled with `currentColor`, used by the web UI as CSS masks colored by the theme's `--logo` token. |
| `lamplighter-{blue,purple}-on-{dark,light}.png` | The full lamp, 1024 px tall, transparent |
| `lamplighter-mark-{blue,purple}-on-{dark,light}.png` | The lantern head, 512 px square, transparent |
| `favicon-{blue,purple}.ico` | 16, 32 and 48 px in one file |
| `favicon-{blue,purple}-{32,192,512}.png` | PNG icons |
| `apple-touch-icon-{blue,purple}.png` | 180 px, the lamp on the login page's aubergine |
| `lamplighter-color.png` | The stylized original, cut out, in color |

The web UI uses the blue set: see `webui/frontend/public/` and the `--logo` token in `tokens.css`.

## Licensing note

The artwork is derived from Jonathan Mills's own photograph and stylized with Google Gemini. Purely AI-generated images may not be protected by copyright in the US. Trademark protection works independently of that.
