# -*- coding: utf-8 -*-
"""Simple modular dashboard for the ST7735 display."""

import os
import time
import socket
import psutil
import platform
import requests
from typing import Callable, List, Tuple
from PIL import Image, ImageDraw, ImageFont

from st7735s import ST7735S

# ----------------------------------------------------
# Fonts
# ----------------------------------------------------
FONT_URLS = {
    "regular": "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Regular.ttf",
    "bold": "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Bold.ttf",
    "mono": "https://github.com/googlefonts/roboto/raw/main/src/hinted/RobotoMono-Regular.ttf",
}

FONT_DIR = "fonts"
FONTS = {}


def _download_font(name: str, url: str) -> str:
    """Download font file if not already present."""
    os.makedirs(FONT_DIR, exist_ok=True)
    path = os.path.join(FONT_DIR, os.path.basename(url))
    if not os.path.exists(path):
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        except Exception:
            return ""
    return path


def _load_fonts() -> None:
    """Load all fonts into the FONTS mapping."""
    sizes = {
        "small": 10,
        "text": 12,
    }
    for key, url in FONT_URLS.items():
        path = _download_font(key, url)
        for style, size in sizes.items():
            font_key = f"{style}_{key}"
            try:
                FONTS[font_key] = ImageFont.truetype(path, size)
            except Exception:
                FONTS[font_key] = ImageFont.load_default()


# ----------------------------------------------------
# Helpers
# ----------------------------------------------------

def get_ip() -> str:
    for iface, addrs in psutil.net_if_addrs().items():
        if iface == "lo":
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET:
                return addr.address
    return "N/A"


def format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{days}d {hours}h {mins}m"


# ----------------------------------------------------
# Screen drawing functions
# ----------------------------------------------------

ScreenFunc = Callable[[ImageDraw.ImageDraw, int, int], None]


def draw_header(draw: ImageDraw.ImageDraw, width: int) -> None:
    host = socket.gethostname()
    ip = get_ip()
    text = f"{host} @ {ip}"
    font = FONTS.get("text_regular")
    draw.text((2, 0), text, font=font, fill="white")
    draw.line([(0, 14), (width, 14)], fill="#333333")


# ---- individual screens ----

def screen_system(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    temp = 0.0
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = int(f.read()) / 1000.0
    except Exception:
        pass
    font = FONTS.get("text_regular")
    draw.text((2, 18), f"CPU: {cpu:.0f}%", font=font, fill="white")
    draw.text((width//2, 18), f"T:{temp:.0f}C", font=font, fill="white", anchor="mm")
    draw.text((2, 32), f"MEM: {mem:.0f}%", font=font, fill="white")


def screen_network(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    net = psutil.net_io_counters()
    font = FONTS.get("text_regular")
    draw.text((2, 18), f"RX: {net.bytes_recv//1024}k", font=font, fill="white")
    draw.text((2, 32), f"TX: {net.bytes_sent//1024}k", font=font, fill="white")


def screen_uptime(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    uptime = time.time() - psutil.boot_time()
    text = format_uptime(uptime)
    font = FONTS.get("bold_regular")
    draw.text((width//2, height//2), text, font=font, fill="#BBBBFF", anchor="mm")


def screen_processes(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    font = FONTS.get("small_regular")
    procs = sorted(psutil.process_iter(['name', 'cpu_percent']),
                   key=lambda p: p.info['cpu_percent'], reverse=True)[:3]
    y = 18
    for p in procs:
        name = p.info['name'][:10]
        cpu = p.info['cpu_percent']
        draw.text((2, y), f"{name:10} {cpu:5.1f}%", font=font, fill="white")
        y += 12


# Register screens in order
SCREENS: List[ScreenFunc] = [
    screen_system,
    screen_network,
    screen_uptime,
    screen_processes,
]


# ----------------------------------------------------
# Main loop
# ----------------------------------------------------

if __name__ == "__main__":
    _load_fonts()
    display = ST7735S(rotation=90, x_offset=24)
    width, height = display.width, display.height
    idx = 0
    last = 0.0
    try:
        while True:
            if time.time() - last > 5:
                img, draw = display.create_canvas("#000000")
                draw_header(draw, width)
                SCREENS[idx](draw, width, height)
                display.display(img)
                idx = (idx + 1) % len(SCREENS)
                last = time.time()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        display.close()
