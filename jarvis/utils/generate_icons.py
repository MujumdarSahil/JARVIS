"""Generate PWA icon assets using Pillow only."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _draw_icon(size: int, out_path: Path) -> None:
    img = Image.new("RGBA", (size, size), "#0a0a0f")
    draw = ImageDraw.Draw(img)
    c = size // 2
    draw.ellipse((size * 0.08, size * 0.08, size * 0.92, size * 0.92), outline="#00d4ff", width=max(2, size // 64))
    draw.arc((size * 0.18, size * 0.18, size * 0.82, size * 0.82), start=15, end=140, fill="#00d4ff", width=max(2, size // 48))
    draw.arc((size * 0.18, size * 0.18, size * 0.82, size * 0.82), start=195, end=320, fill="#00d4ff", width=max(2, size // 48))
    draw.arc((size * 0.28, size * 0.28, size * 0.72, size * 0.72), start=60, end=300, fill="#00d4ff", width=max(2, size // 56))
    font = ImageFont.load_default()
    text = "J"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((c - tw // 2, c - th // 2), text, fill="#00d4ff", font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


def generate_icons(base_dir: Path | None = None) -> None:
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    icons = root / "interface" / "web" / "static" / "icons"
    _draw_icon(192, icons / "icon-192.png")
    _draw_icon(512, icons / "icon-512.png")


if __name__ == "__main__":
    generate_icons()
    print("Generated JARVIS PWA icons.")
