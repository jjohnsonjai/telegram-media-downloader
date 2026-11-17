"""
Generate a custom icon for Telegram Media Downloader.
Creates a download-arrow themed .ico file.
"""
from PIL import Image, ImageDraw


def create_icon(size=256):
    """Create a download-themed icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle - Telegram blue
    cx, cy = size // 2, size // 2
    r = size // 2 - 4
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 136, 204, 255))

    # Download arrow (white)
    arrow_color = (255, 255, 255, 255)
    lw = size // 16  # line width

    # Vertical line (arrow shaft)
    shaft_top = size * 0.28
    shaft_bottom = size * 0.62
    draw.rectangle(
        [cx - lw // 2, shaft_top, cx + lw // 2, shaft_bottom],
        fill=arrow_color,
    )

    # Arrow head (triangle pointing down)
    head_top = shaft_bottom
    head_bottom = size * 0.72
    head_width = size * 0.18

    # Triangle points
    tip = (cx, head_bottom)
    left = (cx - head_width, head_top)
    right = (cx + head_width, head_top)
    draw.polygon([left, right, tip], fill=arrow_color)

    # Horizontal bar at top (tray/platform)
    bar_y = shaft_top
    bar_width = size * 0.22
    bar_height = lw
    draw.rectangle(
        [cx - bar_width // 2, bar_y - bar_height,
         cx + bar_width // 2, bar_y + bar_height],
        fill=arrow_color,
    )

    return img


def main():
    img = create_icon(256)

    # Save as .ico (multi-size)
    sizes = [256, 128, 64, 48, 32, 16]
    img.save(
        "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print("icon.ico created")

    # Also save PNG for reference
    img.save("icon.png", format="PNG")
    print("icon.png created")


if __name__ == "__main__":
    main()
