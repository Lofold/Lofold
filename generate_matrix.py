import random
import html

# Размеры и настройки холста (уменьшили плотность)
width = 600
height = 200
cols = 30
rows = 8
font_size = 14
cell_width = width / cols
cell_height = height / rows

# Символы для хаоса (УБРАЛИ амперсанд &)
chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^*"

# Начало SVG
svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
svg += '<rect width="100%" height="100%" fill="#0d1117" />'

# Генерация сетки
for r in range(rows):
    for c in range(cols):
        x = c * cell_width + cell_width / 2
        y = r * cell_height + cell_height / 2 + font_size / 3
        
        # Берем случайный символ и экранируем его на всякий случай
        char = html.escape(random.choice(chars))
        
        green = random.randint(100, 255)
        color = f"rgb(0, {green}, 0)"
        
        dur = random.uniform(0.1, 0.5)
        
        svg += f'<text x="{x}" y="{y}" fill="{color}" font-family="monospace" font-size="{font_size}" text-anchor="middle">'
        svg += f'<animate attributeName="opacity" values="0.1;1;0.1" dur="{dur}s" repeatCount="indefinite" />'
        svg += f'{char}</text>'

svg += '</svg>'

# Сохранение файла
with open("matrix.svg", "w", encoding="utf-8") as f:
    f.write(svg)

print("matrix.svg успешно сгенерирован!")
