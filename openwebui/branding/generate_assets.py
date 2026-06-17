#!/usr/bin/env python3
"""
Generate OpenWebUI branding assets.

By default this creates simple `giva` text assets. Pass a source PNG if you want
to build assets from a custom black-on-transparent logo.
Outputs into this folder; docker-compose mounts them over OpenWebUI's /app/build/static.

  python generate_assets.py [SOURCE_PNG]

Design choices for the dark/orange theme:
- In-app logo + dark splash  -> WHITE wordmark (legible on the dark UI).
- Light splash               -> ORANGE wordmark.
- Favicons / app icons       -> ORANGE chevron mark only (the wordmark is illegible square).
- apple-touch / PWA icons    -> orange chevron on a dark square (home-screen friendly).
"""
import base64
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
WHITE = (255, 255, 255)
ORANGE = (255, 140, 0)
DARK = (22, 19, 16, 255)  # matches dashboard --bg


def recolor(img, rgb):
    out = Image.new("RGBA", img.size, rgb + (255,))
    out.putalpha(img.getchannel("A"))
    return out


def trim(img):
    bb = img.getbbox()
    return img.crop(bb) if bb else img


def fit_width(word, width, margin=0.04):
    inner = int(width * (1 - 2 * margin))
    ratio = inner / word.width
    im = word.resize((inner, max(1, int(word.height * ratio))), Image.LANCZOS)
    pad = int(width * margin)
    canvas = Image.new("RGBA", (width, im.height + pad * 2), (0, 0, 0, 0))
    canvas.paste(im, (pad, pad), im)
    return canvas


def square(mark, size, margin=0.14, bg=(0, 0, 0, 0)):
    inner = int(size * (1 - 2 * margin))
    im = mark.copy()
    im.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), bg)
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    return canvas


def text_logo(text, rgb, size=180):
    try:
        font = ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", size)
        except OSError:
            font = ImageFont.load_default(size=size)
    probe = Image.new("L", (1, 1), 0)
    draw = ImageDraw.Draw(probe)
    bb = draw.textbbox((0, 0), text, font=font)
    pad = max(12, size // 10)
    img = Image.new("RGBA", (bb[2] - bb[0] + pad * 2, bb[3] - bb[1] + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((pad - bb[0], pad - bb[1]), text, font=font, fill=rgb + (255,))
    return trim(img)


def main():
    if len(sys.argv) > 1:
        src = Image.open(Path(sys.argv[1])).convert("RGBA")
        w, h = src.size
        white_word = trim(recolor(src, WHITE))
        orange_word = trim(recolor(src, ORANGE))
        top = src.crop((0, 0, w, int(h * 0.52)))
        mark_bb = top.getbbox()
        mark = src.crop(mark_bb)
        orange_mark = recolor(mark, ORANGE)
    else:
        white_word = text_logo("giva", WHITE)
        orange_word = text_logo("giva", ORANGE)
        orange_mark = text_logo("g", ORANGE, size=220)
        mark_bb = orange_mark.getbbox()

    out = {}
    # in-app logo + splashes (wordmark)
    out["logo.png"] = fit_width(white_word, 512)
    out["splash-dark.png"] = fit_width(white_word, 512, margin=0.10)
    out["splash.png"] = fit_width(orange_word, 512, margin=0.10)
    # browser favicons: orange chevron, transparent
    for name, sz in [("favicon.png", 96), ("favicon-96x96.png", 96),
                     ("favicon-dark.png", 96)]:
        out[name] = square(orange_mark, sz)
    # apple / PWA icons: orange chevron on a dark square
    out["apple-touch-icon.png"] = square(orange_mark, 180, bg=DARK)
    out["web-app-manifest-192x192.png"] = square(orange_mark, 192, bg=DARK)
    out["web-app-manifest-512x512.png"] = square(orange_mark, 512, bg=DARK)

    for name, im in out.items():
        im.save(HERE / name)

    # multi-size .ico
    square(orange_mark, 64).save(HERE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    # svg that embeds the 192 png (so svg-preferring browsers still get our mark)
    b64 = base64.b64encode((HERE / "web-app-manifest-192x192.png").read_bytes()).decode()
    (HERE / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
        f'<image width="192" height="192" href="data:image/png;base64,{b64}"/></svg>')

    print("generated:", ", ".join(sorted(list(out) + ["favicon.ico", "favicon.svg"])))
    print("mark bbox:", mark_bb, " wordmark:", white_word.size)


if __name__ == "__main__":
    main()
