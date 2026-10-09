import random

# Размеры и настройки холста
width = 600
height = 200
cols = 40
rows = 10
font_size = 14
cell_width = width / cols
cell_height = height / rows

# Символы для хаоса
chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*"

# Начало SVG
svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
# Фон (совпадает с цветом вашего Hero)
svg += '<rect width="100%" height="100%" fill="#0d1117" />'

# Генерация сетки
for r in range(rows):
    for c in range(cols):
        x = c * cell_width + cell_width / 2
        y = r * cell_height + cell_height / 2 + font_size / 3
        char = random.choice(chars)
        
        # Случайный оттенок зелёного
        green = random.randint(100, 255)
        color = f"rgb(0, {green}, 0)"
        
        # Случайная скорость мерцания
        dur = random.uniform(0.1, 0.5)
        
        svg += f'<text x="{x}" y="{y}" fill="{color}" font-family="monospace" font-size="{font_size}" text-anchor="middle">'
        # Анимация прозрачности (мерцание)
        svg += f'<animate attributeName="opacity" values="0.1;1;0.1" dur="{dur}s" repeatCount="indefinite" />'
        svg += f'{char}</text>'

svg += '</svg>'

# Сохранение файла
with open("matrix.svg", "w", encoding="utf-8") as f:
    f.write(svg)

print("matrix.svg успешно сгенерирован!")
