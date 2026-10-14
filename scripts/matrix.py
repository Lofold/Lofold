import base64
import html
import random
from pathlib import Path
from typing import TypedDict

# --- ХОЛСТ ---
width = 1012
height = 200

# --- ДОЖДЬ ---
col_width = 20
font_size = 16

# Максимально богатый набор символов для дождя.
# Все три XML-специальных знака (& < >) включены —
# они безопасно превращаются в entity через html.escape() при вставке.
chars = (
    # Цифры и латиница
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    # Пунктуация (включая XML-специальные)
    "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    # Греческий
    "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
    "αβγδεζηθικλμνξοπρστυφχψω"
    # Кириллица
    "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    # Математика
    "≠≤≥±×÷∞∑∏√∫≈≡∝∈∉⊂⊃⊆⊇∪∩∧∨¬∀∃∄∅∇∂"
    "①②③④⑤⑥⑦⑧⑨⑩½⅓¼⅔¾⅛⅜⅝⅞"
    # Стрелки
    "←↑→↓↔↕↖↗↘↙⇐⇑⇒⇓⇔⇕"
    # Прочие знаки
    "†‡§¶©®™°′″¢£¥€₽§¤¦◊○●◘◙◦☼☽♀♂"
)
num_cols = width // col_width

# --- ПУТИ ---
BASE_DIR = Path(__file__).resolve().parent.parent  # корень репозитория
ICONS_DIR = BASE_DIR / "assets" / "icons"

# --- НАСТРОЙКИ ИКОНОК ---
icon_size = 40
icon_gap_x = 24
icon_gap_y = 24


class ThemeDict(TypedDict):
    """Структура одной темы в THEMES."""

    suffix: str
    bg: str
    rain_min: int
    rain_max: int
    row1: list[str]
    row2: list[str]


# --- ТЕМЫ ---
# Каждая тема задаёт:
#   • suffix       — суффикс имени файла иконок и выходного SVG;
#   • bg           — цвет фона (RGBA, #RRGGBBAA);
#   • rain_min/max — диапазон яркости зелёных символов;
#   • row1/row2    — списки имён иконок.
THEMES: dict[str, ThemeDict] = {
    "dark": {
        "suffix": "-dark",
        "bg": "#0d111700",
        "rain_min": 150,
        "rain_max": 255,
        "row1": [
            "Sublime-Dark.svg",
            "VSCode-Dark.svg",
            "PyCharm-Dark.svg",
            "Python-Dark.svg",
            "Github-Dark.svg",
        ],
        "row2": [
            "CSS.svg",
            "HTML.svg",
            "JavaScript.svg",
            "Git.svg",
        ],
    },
    "light": {
        "suffix": "-light",
        "bg": "#f6f8fa00",  # фон GitHub light
        "rain_min": 60,  # потемнее, чтобы было видно на светлом
        "rain_max": 160,
        "row1": [
            "Sublime-Light.svg",
            "VSCode-Light.svg",
            "PyCharm-Light.svg",
            "Python-Light.svg",
            "Github-Light.svg",
        ],
        "row2": [
            "CSS.svg",
            "HTML.svg",
            "JavaScript.svg",
            "Git.svg",
        ],
    },
}


def load_b64(name: str) -> str:
    """Читает SVG-иконку и возвращает её base64-строку."""
    path = ICONS_DIR / name
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def build_matrix(theme_name: str) -> Path:
    """Собирает один SVG для указанной темы. Возвращает путь к файлу."""
    theme = THEMES[theme_name]

    # --- ИКОНКИ В BASE64 ---
    row1_icons = [load_b64(n) for n in theme["row1"]]
    row2_icons = [load_b64(n) for n in theme["row2"]]
    rows = [row1_icons, row2_icons]

    # --- ГЕОМЕТРИЯ ---
    row_widths = [len(r) * icon_size + (len(r) - 1) * icon_gap_x for r in rows]
    total_icon_height = len(rows) * icon_size + (len(rows) - 1) * icon_gap_y
    start_y = (height - total_icon_height) / 2

    # --- НАЧАЛО SVG ---
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="100%" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet">'
    )
    svg += f'<rect width="100%" height="100%" fill="{theme["bg"]}" />'

    # --- СЛОЙ 1: ДОЖДЬ ---
    for i in range(num_cols):
        x = i * col_width + col_width // 2
        dur = random.uniform(2.0, 5.0)
        begin = random.uniform(0, 5.0)
        char = html.escape(random.choice(chars))
        green = random.randint(theme["rain_min"], theme["rain_max"])
        color = f"rgb(0, {green}, 0)"

        svg += "<g>"
        svg += (
            f'<animateTransform attributeName="transform" '
            f'type="translate" '
            f'from="0,{-font_size}" to="0,{height + font_size}" '
            f'dur="{dur}s" begin="{begin}s" '
            f'repeatCount="indefinite" />'
        )
        svg += (
            f'<text x="{x}" y="{-font_size}" fill="{color}" '
            f'font-family="Consolas, Menlo, DejaVu Sans Mono, '
            f'Noto Sans Mono, monospace" '
            f'font-size="{font_size}" text-anchor="middle">'
        )
        svg += (
            f'<animate attributeName="opacity" values="0;1;1;0" '
            f'keyTimes="0;0.15;0.6;1" dur="{dur}s" begin="{begin}s" '
            f'repeatCount="indefinite" />'
        )
        svg += f"{char}</text></g>"

    # --- СЛОЙ 2: ИКОНКИ ---
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

    svg += "</svg>"

    # --- СОХРАНЕНИЕ ---
    out = BASE_DIR / "assets" / f"matrix{theme['suffix']}.svg"
    out.write_text(svg, encoding="utf-8")
    return out


if __name__ == "__main__":
    for name in THEMES:
        path = build_matrix(name)
        size_kb = path.stat().st_size / 1024
        print(f"{name:5s} → {path.name}  ({size_kb:.1f} КБ)")
