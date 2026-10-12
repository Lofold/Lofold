"""Анимированная змейка: случайная еда, безопасный рост,
выход на контур сердца."""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Iterable

# -----------------------------------------------------------------------------
# ПАРАМЕТРЫ ПОЛЯ И ОТРИСОВКИ
# -----------------------------------------------------------------------------

MIN_W, MIN_H = 20, 17
USER_W, USER_H = 0, 0  # 0 — использовать минимальный размер.

CELL = 16
PADDING = 3
STEP_TIME = 0.01
LOOP_CYCLES = 3
BG = "#0d1117"
GRID_CELL_COLOR = "#151d26"
GRID_CELL_INSET = 1
GRID_CELL_RX = 3
SEG_HEAD_SIZE = CELL
SEG_TAIL_SIZE = CELL


def normalize_field(width: int, height: int) -> tuple[int, int]:
    """Ширина чётная, высота нечётная, поле не меньше минимума."""
    width = max(width, MIN_W)
    height = max(height, MIN_H)
    if width % 2:
        width += 1
    if height % 2 == 0:
        height += 1
    return width, height


GRID_W, GRID_H = normalize_field(USER_W, USER_H)
SVG_W = GRID_W * CELL + PADDING * 2
SVG_H = GRID_H * CELL + PADDING * 2


# -----------------------------------------------------------------------------
# КОНТУР СЕРДЦА
# -----------------------------------------------------------------------------
HEART_TEMPLATE = [
    (3, 0),
    (4, 0),
    (5, 0),
    (6, 0),
    (6, 1),
    (7, 1),
    (7, 2),
    (8, 2),
    (8, 3),
    (9, 3),
    (9, 2),
    (10, 2),
    (10, 1),
    (11, 1),
    (11, 0),
    (12, 0),
    (13, 0),
    (14, 0),
    (14, 1),
    (15, 1),
    (15, 2),
    (16, 2),
    (16, 3),
    (16, 4),
    (17, 4),
    (17, 5),
    (16, 5),
    (16, 6),
    (16, 7),
    (15, 7),
    (15, 8),
    (14, 8),
    (14, 9),
    (13, 9),
    (13, 10),
    (12, 10),
    (12, 11),
    (11, 11),
    (11, 12),
    (10, 12),
    (10, 13),
    (9, 13),
    (9, 14),
    (8, 14),
    (8, 13),
    (7, 13),
    (7, 12),
    (6, 12),
    (6, 11),
    (5, 11),
    (5, 10),
    (4, 10),
    (4, 9),
    (3, 9),
    (3, 8),
    (2, 8),
    (2, 7),
    (1, 7),
    (1, 6),
    (1, 5),
    (0, 5),
    (0, 4),
    (1, 4),
    (1, 3),
    (1, 2),
    (2, 2),
    (2, 1),
    (3, 1),
]


def place_heart_on_field(
    template: list[tuple[int, int]], width: int, height: int
) -> list[tuple[int, int]]:
    """Центрирует неизменный шаблон сердца на поле."""
    if not template:
        return []
    min_x = min(x for x, _ in template)
    max_x = max(x for x, _ in template)
    min_y = min(y for _, y in template)
    max_y = max(y for _, y in template)
    offset_x = (width - (max_x - min_x + 1)) // 2 - min_x
    offset_y = (height - (max_y - min_y + 1)) // 2 - min_y
    return [(x + offset_x, y + offset_y) for x, y in template]


def is_closed_track(track: list[tuple[int, int]]) -> bool:
    """Соседство всех пар клеток, включая последнюю и первую."""
    if not track:
        return False
    for i, point in enumerate(track):
        nxt = track[(i + 1) % len(track)]
        if abs(point[0] - nxt[0]) + abs(point[1] - nxt[1]) != 1:
            return False
    return len(set(track)) == len(track)


HEART = place_heart_on_field(HEART_TEMPLATE, GRID_W, GRID_H)
HEART_SET = set(HEART)
TARGET_LEN = len(HEART)


# -----------------------------------------------------------------------------
# ПУТИ И СЛУЖЕБНЫЕ ФУНКЦИИ
# -----------------------------------------------------------------------------

Point = tuple[int, int]


def neighbors(point: Point) -> Iterable[Point]:
    x, y = point
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < GRID_W and 0 <= ny < GRID_H:
            yield nx, ny


def bfs(start: Point, goal: Point, blocked: set[Point]) -> list[Point] | None:
    """Кратчайший путь по 4-соседям; цель всегда разрешена."""
    if start == goal:
        return [start]
    queue = deque([start])
    came_from: dict[Point, Point | None] = {start: None}

    while queue:
        current = queue.popleft()
        for nxt in neighbors(current):
            if nxt in came_from:
                continue
            if nxt in blocked and nxt != goal:
                continue
            came_from[nxt] = current
            if nxt == goal:
                path: list[Point] = []
                node: Point | None = nxt
                while node is not None:
                    path.append(node)
                    node = came_from[node]
                return path[::-1]
            queue.append(nxt)
    return None


def spawn_food(snake: list[Point]) -> Point | None:
    """Случайная клетка, не занятая змейкой."""
    occupied = set(snake)
    free_cells = [
        (x, y)
        for x in range(GRID_W)
        for y in range(GRID_H)
        if (x, y) not in occupied
    ]
    return random.choice(free_cells) if free_cells else None


def make_hamiltonian_cycle() -> list[Point]:
    """Замкнутый маршрут через каждую клетку поля ровно один раз."""
    cycle: list[Point] = [(0, y) for y in range(GRID_H)]
    for x in range(1, GRID_W):
        ys = range(GRID_H - 1, 0, -1) if x % 2 else range(1, GRID_H)
        cycle.extend((x, y) for y in ys)
    cycle.extend((x, 0) for x in range(GRID_W - 1, 0, -1))
    return cycle


def seg_size(index: int, max_len: int) -> float:
    if max_len <= 1:
        return float(SEG_HEAD_SIZE)
    t = index / (max_len - 1)
    return SEG_HEAD_SIZE + (SEG_TAIL_SIZE - SEG_HEAD_SIZE) * t


# -----------------------------------------------------------------------------
# ФАЗА 1–2: СПАВН И РОСТ ДО ДЛИНЫ КОНТУРА
# -----------------------------------------------------------------------------


def grow_phase() -> tuple[
    list[tuple[list[Point], Point | None]],
    list[Point],
    list[Point],
]:
    cycle = make_hamiltonian_cycle()
    if len(cycle) != GRID_W * GRID_H or not is_closed_track(cycle):
        raise RuntimeError("Не удалось построить безопасный цикл")

    head_index = random.randrange(len(cycle))
    snake = [cycle[(head_index - i) % len(cycle)] for i in range(3)]
    food = spawn_food(snake)
    frames: list[tuple[list[Point], Point | None]] = [(list(snake), food)]
    max_steps = GRID_W * GRID_H * (TARGET_LEN + 1)
    steps = 0

    while len(snake) < TARGET_LEN and steps < max_steps:
        head_index = (head_index + 1) % len(cycle)
        new_head = cycle[head_index]
        ate = new_head == food
        # При съедании еды хвост остаётся на месте.
        snake = [new_head] + (snake if ate else snake[:-1])
        steps += 1

        if ate:
            food = None if len(snake) >= TARGET_LEN else spawn_food(snake)
        frames.append((list(snake), food))

    if len(snake) != TARGET_LEN:
        raise RuntimeError(
            f"Змейка не набрала {TARGET_LEN} за {max_steps} ходов"
        )
    return frames, snake, cycle


# -----------------------------------------------------------------------------
# ФАЗА 3: БЕЗОПАСНЫЙ ПЕРЕХОД НА КОНТУР СЕРДЦА
# -----------------------------------------------------------------------------


def simulate_trajectory(
    snake: list[Point], trajectory: list[Point]
) -> list[list[Point]] | None:
    """Симуляция маршрута; отклоняет ход в занятый сегмент (кроме хвоста)."""
    current = list(snake)
    frames = [list(current)]
    for new_head in trajectory[1:]:
        if new_head in current[:-1]:
            return None
        current = [new_head] + current[:-1]
        frames.append(list(current))
    return frames


def plan_transition(
    snake: list[Point], cycles: int = LOOP_CYCLES
) -> list[list[Point]] | None:
    """Кратчайший безопасный вход на сердце + пробные обороты."""
    best: tuple[int, list[list[Point]]] | None = None
    head = snake[0]
    blocked = set(snake[:-1]) | HEART_SET
    n = len(HEART)

    for target_index, target in enumerate(HEART):
        path = bfs(head, target, blocked)
        if path is None:
            continue
        for direction in (1, -1):
            trajectory = list(path)
            index = target_index
            target_count = len(path) + n * (cycles + 2)
            while len(trajectory) < target_count:
                index = (index + direction) % n
                trajectory.append(HEART[index])
            frames = simulate_trajectory(snake, trajectory)
            if frames is not None and (best is None or len(path) < best[0]):
                best = (len(path), frames)

    return best[1] if best else None


def find_safe_transition(
    snake: list[Point], cycle: list[Point]
) -> tuple[list[list[Point]], list[list[Point]]]:
    """Сдвигает змейку по циклу и повторяет поиск до успеха."""
    index_by_point = {point: i for i, point in enumerate(cycle)}
    index = index_by_point[snake[0]]
    alignment_frames: list[list[Point]] = []
    current = list(snake)

    # Состояние повторится не позже чем через полный цикл поля.
    for _ in range(len(cycle)):
        transition = plan_transition(current)
        if transition is not None:
            return alignment_frames, transition
        index = (index + 1) % len(cycle)
        current = [cycle[index]] + current[:-1]
        alignment_frames.append(list(current))

    raise RuntimeError("Не найден безопасный выход на контур сердца")


def make_heart_loop(final_snake: list[Point]) -> list[list[Point]]:
    """Один полный цикл анимации по сердцу, включая замыкающий кадр."""
    head = final_snake[0]
    head_index = HEART.index(head)
    if final_snake[1] == HEART[(head_index - 1) % len(HEART)]:
        direction = 1
    elif final_snake[1] == HEART[(head_index + 1) % len(HEART)]:
        direction = -1
    else:
        raise RuntimeError("После перехода змейка не выстроилась вдоль сердца")

    loop_frames = [list(final_snake)]
    current = list(final_snake)
    for _ in range(len(HEART)):
        head_index = (head_index + direction) % len(HEART)
        current = [HEART[head_index]] + current[:-1]
        loop_frames.append(list(current))

    if loop_frames[-1] != loop_frames[0]:
        raise RuntimeError("Контур не замкнул состояние змейки")
    return loop_frames


def simulate() -> tuple[
    list[list[Point]],
    list[Point | None],
    list[list[Point]],
]:
    grow_frames, final_snake, cycle = grow_phase()
    print(f"Рост: {len(grow_frames)} кадров, длина: {len(final_snake)}")

    alignment_frames, transition_frames = find_safe_transition(
        final_snake, cycle
    )
    # Первый кадр перехода = последний кадр выравнивания.
    transition_frames = transition_frames[1:]

    all_snake: list[list[Point]] = (
        [state for state, _ in grow_frames]
        + alignment_frames
        + transition_frames
    )
    all_food: list[Point | None] = [food for _, food in grow_frames] + [
        None
    ] * (len(alignment_frames) + len(transition_frames))

    final_state: list[Point] = all_snake[-1]
    loop_snake = make_heart_loop(final_state)
    print(
        f"Переход: "
        f"{len(alignment_frames) + len(transition_frames)} кадров; "
        f"цикл: {len(loop_snake) - 1} шагов"
    )
    return all_snake, all_food, loop_snake


# -----------------------------------------------------------------------------
# ФАЗА 4: SVG
# -----------------------------------------------------------------------------


def _values(values: Sequence[object]) -> str:
    return ";".join(str(value) for value in values)


def _animate_once(
    attribute: str,
    values: Sequence[object],
    duration: float,
    mode: str = "linear",
) -> str:
    return (
        f'<animate attributeName="{attribute}" '
        f'values="{_values(values)}" '
        f'calcMode="{mode}" dur="{duration:.6f}s" '
        f'repeatCount="1" fill="freeze"/>'
    )


def _animate_loop(
    attribute: str,
    values: Sequence[object],
    duration: float,
    begin: float,
) -> str:
    return (
        f'<animate attributeName="{attribute}" '
        f'values="{_values(values)}" '
        f'calcMode="linear" dur="{duration:.6f}s" '
        f'begin="{begin:.6f}s" repeatCount="indefinite" fill="freeze"/>'
    )


def build_svg(
    all_snake: list[list[Point]],
    all_food: list[Point | None],
    loop_snake: list[list[Point]],
    output_path: Path,
) -> None:
    if len(all_snake) != len(all_food):
        raise ValueError("Число состояний змейки и еды не совпадает")

    frame_count = len(all_snake)
    initial_steps = max(1, frame_count - 1)
    initial_duration = initial_steps * STEP_TIME
    loop_steps = len(loop_snake) - 1
    loop_duration = loop_steps * STEP_TIME
    max_len = max(len(state) for state in all_snake)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" '
        f'height="{SVG_H}" viewBox="0 0 {SVG_W} {SVG_H}">',
        f'<rect width="100%" height="100%" fill="{BG}" rx="8"/>',
    ]

    for x in range(GRID_W):
        for y in range(GRID_H):
            gx = PADDING + x * CELL + GRID_CELL_INSET
            gy = PADDING + y * CELL + GRID_CELL_INSET
            size = CELL - 2 * GRID_CELL_INSET
            out.append(
                f'<rect x="{gx}" y="{gy}" '
                f'width="{size}" height="{size}" '
                f'rx="{GRID_CELL_RX}" fill="{GRID_CELL_COLOR}"/>'
            )

    food_x = [
        PADDING + food[0] * CELL + CELL / 2 if food else -50
        for food in all_food
    ]
    food_y = [
        PADDING + food[1] * CELL + CELL / 2 if food else -50
        for food in all_food
    ]
    out.append('<circle r="4" fill="#ff3b30">')
    out.append(_animate_once("cx", food_x, initial_duration, "discrete"))
    out.append(_animate_once("cy", food_y, initial_duration, "discrete"))
    out.append("</circle>")

    for segment_index in range(max_len - 1, -1, -1):
        size = seg_size(segment_index, max_len)
        offset = (CELL - size) / 2
        xs: list[float] = []
        ys: list[float] = []
        opacity: list[int] = []

        for state in all_snake:
            if segment_index < len(state):
                px, py = state[segment_index]
                opacity.append(1)
            else:
                px, py = state[-1]
                opacity.append(0)
            xs.append(PADDING + px * CELL + offset)
            ys.append(PADDING + py * CELL + offset)

        loop_x = [
            PADDING + state[segment_index][0] * CELL + offset
            for state in loop_snake
        ]
        loop_y = [
            PADDING + state[segment_index][1] * CELL + offset
            for state in loop_snake
        ]

        if segment_index == 0:
            color = "#d6ffd6"
        elif segment_index == 1:
            color = "#8dff8d"
        else:
            t = segment_index / max(1, max_len - 1)
            green = int(160 + (1 - t) * 80)
            color = f"rgb(20,{green},20)"

        out.append(
            f'<rect width="{size}" height="{size}" '
            f'rx="{size * 0.25}" fill="{color}">'
        )
        out.append(_animate_once("x", xs, initial_duration))
        out.append(_animate_once("y", ys, initial_duration))
        out.append(
            _animate_once("opacity", opacity, initial_duration, "discrete")
        )
        out.append(_animate_loop("x", loop_x, loop_duration, initial_duration))
        out.append(_animate_loop("y", loop_y, loop_duration, initial_duration))
        out.append("</rect>")

    out.append("</svg>")
    output_path.write_text("\n".join(out), encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    print(
        f"Сохранено: {output_path.name}; "
        f"{frame_count} кадров до сердца; "
        f"{max_len} сегментов; {size_kb:.1f} КБ"
    )


if __name__ == "__main__":
    print(f"Поле: {GRID_W} × {GRID_H}")
    print(f"Клеток контура: {TARGET_LEN}")
    if not is_closed_track(HEART):
        raise RuntimeError(
            "Контур сердца не является простым замкнутым треком"
        )
    all_snake, all_food, loop_snake = simulate()
    output = (
        Path(__file__).resolve().parent.parent / "assets" / "snake_heart.svg"
    )
    build_svg(all_snake, all_food, loop_snake, output)
