"""Generate docs/og.png — 1200x630 OG preview image for FanXP."""

from PIL import Image, ImageDraw, ImageFont
import math, os

W, H = 1200, 630
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "og.png")

img = Image.new("RGB", (W, H), (8, 10, 18))
draw = ImageDraw.Draw(img)

# ── Background gradient (dark navy → slightly lighter) ──────────────────────
for y in range(H):
    t = y / H
    r = int(8  + t * 10)
    g = int(10 + t * 8)
    b = int(18 + t * 20)
    draw.line([(0, y), (W, y)], fill=(r, g, b))

# ── Subtle dot-grid ──────────────────────────────────────────────────────────
for gx in range(0, W, 40):
    for gy in range(0, H, 40):
        draw.ellipse([gx-1, gy-1, gx+1, gy+1], fill=(255, 255, 255, 18))

# ── Glowing accent dots (simulate seat map feel) ─────────────────────────────
import random
random.seed(42)
for _ in range(180):
    x = random.randint(680, 1160)
    y = random.randint(60, 570)
    cyan = (0, 245, 255)
    r = random.randint(2, 5)
    alpha = random.randint(60, 180)
    # glow
    for spread in range(r + 6, r, -2):
        a = max(0, alpha - (r + 6 - spread) * 30)
        draw.ellipse([x-spread, y-spread, x+spread, y+spread],
                     fill=(cyan[0], cyan[1], cyan[2]))
    draw.ellipse([x-r, y-r, x+r, y+r], fill=cyan)

# ── Left panel text ──────────────────────────────────────────────────────────
def try_font(size):
    for name in [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/SF Pro Display Bold.otf",
    ]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()

font_logo   = try_font(72)
font_tag    = try_font(28)
font_stat   = try_font(56)
font_label  = try_font(20)
font_small  = try_font(22)

CYAN   = (0, 245, 255)
WHITE  = (255, 255, 255)
MUTED  = (140, 130, 150)
DIM    = (80, 75, 90)

# Logo: "Fan" white + "XP" cyan
draw.text((72, 80), "Fan", font=font_logo, fill=WHITE)
fan_w = draw.textlength("Fan", font=font_logo)
draw.text((72 + fan_w, 80), "XP", font=font_logo, fill=CYAN)

# Tagline
draw.text((74, 168), "Real-Time Seat Intelligence", font=font_tag, fill=MUTED)
draw.text((74, 200), "for NBA Teams", font=font_tag, fill=MUTED)

# Divider line
draw.line([(72, 260), (520, 260)], fill=DIM, width=1)

# Stats
stats = [
    ("58%",  "avg no-show rate"),
    ("$48k", "revenue per game"),
    ("24",   "teams tracked"),
]
sx = 72
for val, lbl in stats:
    draw.text((sx, 284), val, font=font_stat, fill=CYAN)
    val_w = int(draw.textlength(val, font=font_stat))
    draw.text((sx, 350), lbl, font=font_label, fill=MUTED)
    sx += 180

# Bottom URL
draw.text((74, H - 64), "fan-xp.vercel.app", font=font_small, fill=DIM)

# ── Vertical divider ────────────────────────────────────────────────────────
draw.line([(620, 40), (620, H - 40)], fill=(40, 38, 55), width=1)

# ── Right panel: mini arena silhouette (ellipses) ───────────────────────────
cx, cy = 910, 315
# court outline
draw.ellipse([cx-210, cy-150, cx+210, cy+150], outline=(40, 38, 60), width=2)
draw.ellipse([cx-170, cy-115, cx+170, cy+115], outline=(30, 28, 50), width=1)
# centre circle
draw.ellipse([cx-40, cy-28, cx+40, cy+28], outline=(40, 38, 60), width=1)
# paint lines
draw.rectangle([cx-55, cy-115, cx+55, cy+115], outline=(35, 33, 55), width=1)

img.save(OUT, "PNG", optimize=True)
print(f"✓ Saved {OUT}  ({os.path.getsize(OUT)//1024} KB)")
