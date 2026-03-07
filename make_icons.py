from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

def make_icon(size: int, out_name: str):
    img = Image.new("RGBA", (size, size), (11, 18, 32, 255))  # #0b1220
    d = ImageDraw.Draw(img)

    # gradient-ish diagonal
    for i in range(size):
        a = int(60 + (140 * i / max(1, size-1)))
        d.line([(0, i), (i, 0)], fill=(59, 130, 246, a), width=max(1, size // 64))

    # center badge
    pad = size // 8
    r = size // 5
    d.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=r,
        fill=(255, 255, 255, 18),
        outline=(255, 255, 255, 35),
        width=max(2, size // 96),
    )

    # TM text
    text = "TM"
    font_size = int(size * 0.42)
    font = None
    # Try common fonts; fallback to default
    for name in ["arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]:
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except:
            pass
    if font is None:
        font = ImageFont.load_default()

    tw, th = d.textbbox((0, 0), text, font=font)[2:]
    x = (size - tw) // 2
    y = (size - th) // 2 - int(size * 0.02)

    d.text((x, y), text, font=font, fill=(234, 240, 255, 255))

    out_path = STATIC_DIR / out_name
    img.save(out_path, format="PNG")
    print("Wrote", out_path)

if __name__ == "__main__":
    make_icon(192, "icon-192.png")
    make_icon(512, "icon-512.png")
