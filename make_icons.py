from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

NAVY = (11, 18, 32, 255)
NAVY_2 = (8, 28, 42, 255)
TEAL = (20, 184, 166, 255)
TEAL_DARK = (15, 159, 152, 255)
INK = (238, 247, 246, 255)


def _font(size: int):
    font_size = int(size * 0.38)
    for name in ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "arial.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, font_size)
        except OSError:
            pass
    return ImageFont.load_default()


def _hex_points(size: int, inset: float = 0.18):
    cx = cy = size / 2
    r = size * (0.5 - inset)
    return [
        (cx, cy - r),
        (cx + r * 0.866, cy - r * 0.5),
        (cx + r * 0.866, cy + r * 0.5),
        (cx, cy + r),
        (cx - r * 0.866, cy + r * 0.5),
        (cx - r * 0.866, cy - r * 0.5),
    ]


def build_icon(size: int):
    scale = 4
    canvas = size * scale
    img = Image.new("RGBA", (canvas, canvas), NAVY)
    d = ImageDraw.Draw(img)

    # Subtle shop-tool depth without muddying the favicon at small sizes.
    for y in range(canvas):
        mix = y / max(1, canvas - 1)
        fill = tuple(int(NAVY[i] * (1 - mix) + NAVY_2[i] * mix) for i in range(3)) + (255,)
        d.line([(0, y), (canvas, y)], fill=fill)

    outer = _hex_points(canvas, 0.105)
    inner = _hex_points(canvas, 0.185)
    d.polygon(outer, fill=TEAL_DARK)
    d.polygon(inner, fill=NAVY)

    # Clean mechanical crossbar gives the mark a torque/wrench feel at 16px.
    bar_h = max(5 * scale, int(canvas * 0.06))
    d.rounded_rectangle(
        [int(canvas * 0.22), int(canvas * 0.47), int(canvas * 0.78), int(canvas * 0.47) + bar_h],
        radius=max(2 * scale, bar_h // 2),
        fill=TEAL,
    )

    text = "TM"
    font = _font(canvas)
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (canvas - tw) // 2 - bbox[0]
    y = (canvas - th) // 2 - bbox[1] - int(canvas * 0.015)
    d.text((x, y), text, font=font, fill=INK)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def make_icon(size: int, out_name: str):
    img = build_icon(size)
    out_path = STATIC_DIR / out_name
    img.save(out_path, format="PNG")
    print("Wrote", out_path)


def make_favicon():
    images = [build_icon(size) for size in [16, 32, 48]]
    ico_path = STATIC_DIR / "favicon.ico"
    images[-1].save(ico_path, sizes=[(16, 16), (32, 32), (48, 48)], append_images=images[:-1])
    print("Wrote", ico_path)


if __name__ == "__main__":
    make_icon(48, "favicon-48.png")
    make_icon(192, "icon-192.png")
    make_icon(512, "icon-512.png")
    make_favicon()
