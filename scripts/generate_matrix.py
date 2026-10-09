import random
import base64
from pathlib import Path

# --- ХОЛСТ ---
width = 1200
height = 220
bg_color = "#0d1117"

# --- ДОЖДЬ ---
col_width = 20
font_size = 16
chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
num_cols = width // col_width

# --- ПУТИ ---
BASE_DIR   = Path(__file__).resolve().parent.parent   # корень репозитория
ICONS_DIR  = BASE_DIR / "assets" / "icons"
OUTPUT_SVG = BASE_DIR / "assets" / "matrix.svg"

# --- СТРОКИ ИКОНОК ---
row1_files = [
    "Sublime-Lite.svg",
    "VSCode-Dark.svg",
    "PyCharm-Lite.svg",
    "Github-Lite.svg",
    "Git.svg",
]
row2_files = [
    "Python-Lite.svg",
    "CSS.svg",
    "HTML.svg",
    "JavaScript.svg",
]

# --- НАСТРОЙКИ ИКОНОК ---
icon_size = 40          # размер одной иконки
icon_gap_x = 24         # отступ между иконками в строке
icon_gap_y = 24         # отступ между строками

# --- ЗАГРУЗКА ИКОНОК В BASE64 ---
def load_b64(name: str) -> str:
    path = ICONS_DIR / name
    return base64.b64encode(path.read_bytes()).decode("utf-8")

row1_icons = [load_b64(n) for n in row1_files]
row2_icons = [load_b64(n) for n in row2_files]

rows = [row1_icons, row2_icons]

# --- ГЕОМЕТРИЯ ---
row_widths = [len(r) * icon_size + (len(r) - 1) * icon_gap_x for r in rows]
total_height = len(rows) * icon_size + (len(rows) - 1) * icon_gap_y
# Прижимаем блок иконок к верхнему краю
start_y = 0

# --- НАЧИНАЕМ СБОРКУ SVG ---
svg = (
    f'<svg xmlns="http://www.w3.org/2000/svg" '
    f'xmlns:xlink="http://www.w3.org/1999/xlink" '
    f'width="100%" height="{height}" '
    f'viewBox="0 0 {width} {height}" '
    f'preserveAspectRatio="xMidYMid meet">'
)
svg += f'<rect width="100%" height="100%" fill="{bg_color}" />'

# --- СЛОЙ 1: ДОЖДЬ ---
for i in range(num_cols):
    x = i * col_width + col_width // 2
    dur = random.uniform(2.0, 5.0)
    begin = random.uniform(0, 5.0)
    char = random.choice(chars)
    green = random.randint(150, 255)
    color = f"rgb(0, {green}, 0)"

    svg += '<g>'
    svg += (
        f'<animateTransform attributeName="transform" type="translate" '
        f'from="0,{-font_size}" to="0,{height + font_size}" '
        f'dur="{dur}s" begin="{begin}s" repeatCount="indefinite" />'
    )
    svg += (
        f'<text x="{x}" y="0" fill="{color}" font-family="monospace" '
        f'font-size="{font_size}" text-anchor="middle">'
    )
    svg += (
        f'<animate attributeName="opacity" values="0;1;1;0" '
        f'keyTimes="0;0.15;0.6;1" dur="{dur}s" begin="{begin}s" '
        f'repeatCount="indefinite" />'
    )
    svg += f'{char}</text></g>'

# --- СЛОЙ 2: ИКОНКИ (КАЖДАЯ СТРОКА ЦЕНТРИРУЕТСЯ ОТДЕЛЬНО) ---
current_y = start_y
for row_icons, row_width in zip(rows, row_widths):
    start_x = (width - row_width) / 2

    for idx, b64 in enumerate(row_icons):
        x = start_x + idx * (icon_size + icon_gap_x)
        svg += (
            f'<image x="{x}" y="{current_y}" '
            f'width="{icon_size}" height="{icon_size}" '
            f'href="data:image/svg+xml;base64,{b64}" />'
        )

    current_y += icon_size + icon_gap_y

svg += '</svg>'

# --- СОХРАНЕНИЕ РЯДОМ СО СКРИПТОМ ---
OUTPUT = ICONS_DIR / "matrix.svg"
with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write(svg)

print(f"matrix.svg готов: {OUTPUT}")
