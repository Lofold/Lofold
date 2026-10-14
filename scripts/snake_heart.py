"""Анимированная змейка: свободный рост, безопасная укладка на контур
сердца, вечный цикл.

Возможности:
  • int-индексация клеток и A* с манхэттенской эвристикой;
  • параллельные попытки роста через ProcessPoolExecutor
    (пул переиспользуется между батчами);
  • LRU-кэш reachable-множеств. Не инкрементальный: стартовая клетка меняется
    каждый ход, поэтому hit-rate держится в районе единиц-десятков процентов —
    заметный выигрыш есть, но принципиального ускорения не даёт.
  • опциональный JPS-диспетчер. По умолчанию ВЫКЛЮЧЕН: классический JPS
    определён для 8-связной сетки, а здесь 4-связная — выигрыша нет,
    а корректность требует отдельных тестов;
  • дедупликация кадров перед генерацией SVG;
  • отладочная визуализация дерева поиска A* в отдельный SVG.

Надёжность:
  • антизастой («голодный бросок»);
  • tail-chase в середину тела;
  • ретраи укладки со сменой подсева rng и jitter безопасными ходами;
  • ретраи всей симуляции;
  • явные исключения с сохранением первопричины (`raise ... from`).

Отладка:
  • SEED для воспроизводимости;
  • VISUALIZE_SEARCH — отдельный SVG с деревом поиска A*.
"""

# region IMPORTS: __future__; Standard library

from __future__ import annotations

import heapq
import logging
import os
import random
import sys
import traceback
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Iterable, Sequence

# endregion


# region ПАРАМЕТРЫ

# ВАЖНО: MAX_W должен быть чётным, MAX_H — нечётным.
# normalize_field сначала clamp'ит по MAX, потом правит чётность,
# поэтому при несоблюдении инварианта значение вылезет за границу.
MIN_W, MIN_H = 18, 15
MAX_W, MAX_H = 60, 41

# Размер поля, запрошенный пользователем.
# (0, 0) → берётся MIN_W × MIN_H. Задай вручную для поля другого размера.
USER_W, USER_H = 0, 0

# Фиксированный seed для воспроизводимости результата.
# None — каждый запуск даёт новую змейку (то, что нужно для профиля).
SEED: int | None = None

# Длительность одного кадра анимации в секундах.
# Итоговая длительность = STEP_TIME * (число кадров - 1).
STEP_TIME = 0.1

# --- Направление движения по контуру ---
#   0 — авто: как получилось при укладке;
#  +1 — по возрастанию индексов HEART;
#  -1 — по убыванию индексов HEART.
# Влияет и на выбор траектории укладки, и на сам цикл — визуального
# «скачка» на стыке фаз нет.
LOOP_DIRECTION = 0

# Размер одной клетки сетки в пикселях и внешний отступ SVG.
CELL = 16
PADDING = 0

# Размеры сегментов змейки: голова — самый крупный, хвост — вдвое меньше,
# промежуточные сегменты линейно интерполируются.
SEG_HEAD_SIZE = CELL
SEG_TAIL_SIZE = CELL // 2

# Отступ от края клетки и радиус скругления — формируют «квадратик»
# визуально внутри клетки.
GRID_CELL_INSET = 1
GRID_CELL_RX = 3

# Прозрачный фон SVG (альфа-канал 00), чтобы вписаться в тему GitHub.
BG = "#0d111700"

# Базовый цвет обычной клетки сетки.
GRID_CELL_COLOR = "#151d26"

# --- Отображение контура сердца ---
# True  — клетки контура подсвечены HEART_CELL_COLOR (видны как «сердце»);
# False — контур не виден: цвет совпадает с обычной клеткой сетки.
SHOW_HEART_CONTOUR = False

# Цвет контура сердца: при SHOW_HEART_CONTOUR=False совпадает с клеткой.
HEART_CELL_COLOR = "#1a3325" if SHOW_HEART_CONTOUR else GRID_CELL_COLOR

# Пороги «безопасного» свободного места считаются как доля от ОСТАВШЕГОСЯ
# свободного пула (TOTAL_CELLS - len(snake)), а не от TOTAL_CELLS.
# Иначе для длинной змейки порог недостижим.
FREE_THRESHOLD_SHORT = 0.25
FREE_THRESHOLD_MEDIUM = 0.35
FREE_THRESHOLD_LONG = 0.45

# os.cpu_count() может вернуть None (документировано) или 0.
# Fallback — 4: типичный ноутбучный минимум, не перегружает машину.
# MAX_WORKERS ограничивает сверху: на многоядерных машинах больше 16
# процессов для такой задачи — оверхед на старт интерпретатора.
DEFAULT_WORKERS = 4
MAX_WORKERS = 16
PARALLEL_WORKERS = min(os.cpu_count() or DEFAULT_WORKERS, MAX_WORKERS)

# Размер батча параллельных попыток:
#  • batch = PARALLEL_WORKERS * BATCH_SIZE_FACTOR.
# Больше воркеров — больше работы параллельно, но выше шанс «промахнуться»
# мимо нужного количества попыток.
BATCH_SIZE_FACTOR = 2

# Сколько шагов без еды терпеть до включения «голодного режима» с
# ослабленными порогами безопасности.
HUNGRY_LIMIT = 200

# Минимальный запас свободных клеток, ниже которого ход считается небезопасным.
SAFETY_MARGIN = 8

# Сколько самых дальних свободных целей рассматривать при «прогулке»
# (используется, когда змейка уже пересекает контур и надо уйти с него).
WANDER_TARGETS = 8

# Максимум попыток роста суммарно по всем воркерам.
GROW_ATTEMPTS = 2000

# Максимум попыток всей симуляции (рост + укладка).
SIMULATION_ATTEMPTS = 20

# Максимум попыток укладки на одном состоянии змейки (ретраи с jitter).
ALIGNMENT_RETRIES = 20

# Сколько случайных безопасных ходов делать между попытками укладки,
# чтобы сбить детерминированный паттерн.
ALIGNMENT_JITTER_STEPS = 8

# Сколько шагов без изменения длины терпеть, прежде чем считать рост
# провалившимся.
STALL_LIMIT = 300

# Максимум шагов роста в одной попытке (страховка от бесконечного цикла).
MAX_STEPS_PER_ATTEMPT = 12000

# Максимум итераций поиска безопасного входа на контур.
ALIGNMENT_LIMIT = 600

# JPS на 4-связной сетке не даёт выигрыша и требует аккуратной
# forced-neighbor логики для кардинального движения — по умолчанию A*.
USE_JPS = False

# Включает LRU-кэш над BFS-достижимостью (см. ReachableTracker).
USE_REACHABLE_CACHE = True
REACHABLE_CACHE_SIZE = 512

# Отладка: нарисовать дерево поиска A* для кадра VISUALIZE_FRAME
# в отдельный SVG с именем VISUALIZE_OUTPUT_NAME.
VISUALIZE_SEARCH = False
VISUALIZE_FRAME = 100
VISUALIZE_OUTPUT_NAME = "snake_search_debug.svg"

# Магические числа — с именами.
INF = 10**9  # «бесконечность» для сравнений в A*/JPS
FOOD_BONUS = 500.0  # приоритет еды в эвристике выбора хода
OFFSCREEN_COORD = -50.0  # координата «скрытой» еды после поедания

# Логгер этого модуля.
LOG = logging.getLogger("snake")

# endregion


# region ВЫВОДНЫЕ КОНСТАНТЫ

# По какому направлению двигаться к началу контура
if LOOP_DIRECTION not in (-1, 0, 1):
    raise ValueError("LOOP_DIRECTION должен быть -1, 0 или 1")

# Тип координат клетки: (x, y) в пикселях сетки.
Point = tuple[int, int]

# endregion


# region УТИЛИТЫ


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Склонение русских существительных: 1 кошка, 2 кошки, 5 кошек."""
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 19:
        return many
    last = n_abs % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def setup_logging() -> None:
    """Настройка логирования в stdout в формате «только сообщение»."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# endregion

# region ИНЛАЙН-СТАТУС ДЛЯ КОНСОЛИ

# Ширина строки, до которой обрезаются/дополняются промежуточные статусы.
_INLINE_WIDTH = 80


def _inline_status(text: str) -> None:
    """Однострочный статус, обновляемый НА МЕСТЕ.

    На TTY — перерисовываем через `\\r`. В пайпе/файле — молчим:
    промежуточные попытки не нужны, всё равно будет финальный лог.
    """
    if not sys.stdout.isatty():
        return
    sys.stdout.write("\r" + text.ljust(_INLINE_WIDTH)[:_INLINE_WIDTH])
    sys.stdout.flush()


def _inline_finish(text: str) -> None:
    """Затирает инлайн-строку и печатает финальный лог с новой строки."""
    if sys.stdout.isatty():
        sys.stdout.write("\r" + " " * _INLINE_WIDTH + "\r")
        sys.stdout.flush()
    LOG.info(text)


# endregion


# region ПОЛЕ И СЕРДЦЕ


def normalize_field(width: int, height: int) -> tuple[int, int]:
    """Ограничивает поле по MIN/MAX и приводит к чётности/нечётности."""
    width = max(MIN_W, min(width, MAX_W))
    height = max(MIN_H, min(height, MAX_H))
    # После clamp — приводим к чётности.
    # ВАЖНО: clamp до чётности, чтобы не вылететь за MAX.
    if width % 2:
        width += 1
    if height % 2 == 0:
        height += 1
    return width, height


# Итоговые размеры сетки и SVG в пикселях.
GRID_W, GRID_H = normalize_field(USER_W, USER_H)
SVG_W = GRID_W * CELL + PADDING * 2
SVG_H = GRID_H * CELL + PADDING * 2
TOTAL_CELLS = GRID_W * GRID_H

# Шаблон контура сердца: последовательность клеток, образующая замкнутый цикл
# (каждый следующий — сосед по 4-связности, последний примыкает к первому).
# Внутри шаблона координаты локальные, при переносе на поле центрируются.
HEART_TEMPLATE: list[Point] = [
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
    template: list[Point], width: int, height: int
) -> list[Point]:
    """Центрирует шаблон сердца внутри поля заданного размера."""
    if not template:
        return []
    xs = [x for x, _ in template]
    ys = [y for _, y in template]
    off_x = (width - (max(xs) - min(xs) + 1)) // 2 - min(xs)
    off_y = (height - (max(ys) - min(ys) + 1)) // 2 - min(ys)
    return [(x + off_x, y + off_y) for x, y in template]


# Абсолютные координаты контура на поле и их множество для быстрых проверок.
HEART = place_heart_on_field(HEART_TEMPLATE, GRID_W, GRID_H)
HEART_SET = set(HEART)

# Целевая длина змейки — ровно столько клеток в контуре.
TARGET_LEN = len(HEART)


def is_closed_track(track: list[Point]) -> bool:
    """Проверка: трек замкнут, без повторов, каждый сосед — сосед по сетке."""
    if not track or len(track) != len(set(track)):
        return False
    for i, point in enumerate(track):
        nxt = track[(i + 1) % len(track)]
        if abs(point[0] - nxt[0]) + abs(point[1] - nxt[1]) != 1:
            return False
    return True


# endregion


# region INT-ИНДЕКСАЦИЯ


def _idx(x: int, y: int) -> int:
    """Плоский индекс клетки (x, y) в row-major порядке."""
    return y * GRID_W + x


def p2i(p: Point) -> int:
    """Point → int: (x, y) → y * GRID_W + x (row-major)."""
    return _idx(p[0], p[1])


def i2p(i: int) -> Point:
    """Int → Point: обратное преобразование."""
    return (i % GRID_W, i // GRID_W)


def p2i_set(points: Iterable[Point]) -> set[int]:
    """Множество плоских индексов для набора точек."""
    return {p2i(p) for p in points}


def i2p_list(indices: Iterable[int]) -> list[Point]:
    """Список точек обратно из плоских индексов."""
    return [i2p(i) for i in indices]


def int_neighbors(i: int) -> list[int]:
    """4-связные соседи клетки i (лево, право, верх, низ)."""
    x = i % GRID_W
    y = i // GRID_W
    result: list[int] = []
    if x > 0:
        result.append(i - 1)
    if x < GRID_W - 1:
        result.append(i + 1)
    if y > 0:
        result.append(i - GRID_W)
    if y < GRID_H - 1:
        result.append(i + GRID_W)
    return result


# Множество всех клеток поля в виде точек (x, y).
ALL_CELLS: set[Point] = {(x, y) for x in range(GRID_W) for y in range(GRID_H)}


class ReachableTracker:
    """LRU-кэш reachable-множеств.

    Ключ — (start, frozenset(blocked)). Не инкрементальный: стартовая
    клетка меняется каждый ход, поэтому hit-rate держится в районе
    единиц-десятков процентов. Заметный выигрыш на повторных вызовах,
    но принципиального ускорения не даёт.
    """

    def __init__(self, max_cache: int = REACHABLE_CACHE_SIZE) -> None:
        """max_cache должен быть положительным — иначе кэш бессмысленен."""
        if max_cache <= 0:
            raise ValueError("max_cache должен быть положительным")
        self.max_cache = max_cache
        self.cache: dict[tuple[int, frozenset[int]], frozenset[int]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, start: int, blocked: set[int]) -> set[int]:
        """Возвращает reachable-множество с LRU-кэшированием."""
        key = (start, frozenset(blocked))
        hit = self.cache.get(key)
        if hit is not None:
            self.hits += 1
            return set(hit)
        self.misses += 1
        result = reachable_int(start, set(blocked))
        if len(self.cache) >= self.max_cache:
            first = next(iter(self.cache))
            del self.cache[first]
        self.cache[key] = frozenset(result)
        return result

    def stats(self) -> str:
        """Строка со статистикой кэша для финального лога."""
        total = self.hits + self.misses
        if total == 0:
            return "Кэш: пусто"
        return (
            f"Кэш reachable: hits={self.hits}, misses={self.misses}, "
            f"size={len(self.cache)}, "
            f"hit-rate={100 * self.hits / total:.1f}%"
        )


# Глобальный трекер reachable, включается флагом USE_REACHABLE_CACHE.
_REACH_TRACKER: ReachableTracker | None = (
    ReachableTracker() if USE_REACHABLE_CACHE else None
)

# endregion


# region БАЗОВЫЙ REACHABLE


def reachable_int(
    start: int,
    blocked: set[int],
    allowed: set[int] | None = None,
) -> set[int]:
    """
    BFS от start по плоским индексам, обходя blocked и (опционально) allowed.
    """
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in int_neighbors(current):
            if nxt in seen or nxt in blocked:
                continue
            if allowed is not None and nxt not in allowed:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen


def reachable_cached(start: int, blocked: set[int]) -> set[int]:
    """Обёртка над _REACH_TRACKER с фоллбэком на чистый BFS."""
    if _REACH_TRACKER is not None:
        return _REACH_TRACKER.get(start, blocked)
    return reachable_int(start, blocked)


# endregion


# region A*


def _manhattan_h(a: int, b: int) -> int:
    """Манхэттенская эвристика для плоских индексов."""
    return abs(a % GRID_W - b % GRID_W) + abs(a // GRID_W - b // GRID_W)


def a_star_int(
    start: int,
    goal: int,
    blocked: set[int],
    allowed: set[int] | None = None,
) -> list[int] | None:
    """A* по плоским индексам с манхэттенской эвристикой.

    Возвращает список плоских индексов от start до goal (включительно)
    или None, если путь не найден.
    """
    if start == goal:
        return [start]
    if allowed is not None and (start not in allowed or goal not in allowed):
        return None

    open_heap: list[tuple[int, int]] = [(_manhattan_h(start, goal), start)]
    came_from: dict[int, int] = {start: -1}
    g_score: dict[int, int] = {start: 0}

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current == goal:
            path: list[int] = []
            node = current
            while node != -1:
                path.append(node)
                node = came_from[node]
            return path[::-1]
        g_cur = g_score[current]
        for nxt in int_neighbors(current):
            if nxt in blocked and nxt != goal:
                continue
            if allowed is not None and nxt not in allowed and nxt != goal:
                continue
            new_g = g_cur + 1
            if new_g < g_score.get(nxt, INF):
                g_score[nxt] = new_g
                came_from[nxt] = current
                heapq.heappush(
                    open_heap, (new_g + _manhattan_h(nxt, goal), nxt)
                )
    return None


def a_star_int_with_trace(
    start: int,
    goal: int,
    blocked: set[int],
    allowed: set[int] | None = None,
) -> tuple[list[int] | None, set[int]]:
    """
    A* с возвратом множества explored-узлов (для отладочной визуализации).
    """
    if start == goal:
        return [start], {start}
    if allowed is not None and (start not in allowed or goal not in allowed):
        return None, set()

    open_heap: list[tuple[int, int]] = [(_manhattan_h(start, goal), start)]
    came_from: dict[int, int] = {start: -1}
    g_score: dict[int, int] = {start: 0}
    explored: set[int] = set()

    while open_heap:
        _, current = heapq.heappop(open_heap)
        explored.add(current)
        if current == goal:
            path: list[int] = []
            node = current
            while node != -1:
                path.append(node)
                node = came_from[node]
            return path[::-1], explored
        g_cur = g_score[current]
        for nxt in int_neighbors(current):
            if nxt in blocked and nxt != goal:
                continue
            if allowed is not None and nxt not in allowed and nxt != goal:
                continue
            new_g = g_cur + 1
            if new_g < g_score.get(nxt, INF):
                g_score[nxt] = new_g
                came_from[nxt] = current
                heapq.heappush(
                    open_heap, (new_g + _manhattan_h(nxt, goal), nxt)
                )
    return None, explored


# endregion


# region JPS (оставлен, но по умолчанию не используется — см. шапку)


def _has_forced_neighbor_horizontal(
    nx: int, ny: int, dx: int, blocked: set[int]
) -> bool:
    """Проверка форсированных соседей при горизонтальном движении."""
    if ny - 1 >= 0:
        if (
            _idx(nx, ny - 1) not in blocked
            and _idx(nx - dx, ny - 1) in blocked
        ):
            return True
    if ny + 1 < GRID_H:
        if (
            _idx(nx, ny + 1) not in blocked
            and _idx(nx - dx, ny + 1) in blocked
        ):
            return True
    return False


def _has_forced_neighbor_vertical(
    nx: int, ny: int, dy: int, blocked: set[int]
) -> bool:
    """Проверка форсированных соседей при вертикальном движении."""
    if nx - 1 >= 0:
        if (
            _idx(nx - 1, ny) not in blocked
            and _idx(nx - 1, ny - dy) in blocked
        ):
            return True
    if nx + 1 < GRID_W:
        if (
            _idx(nx + 1, ny) not in blocked
            and _idx(nx + 1, ny - dy) in blocked
        ):
            return True
    return False


def _jps_jump(
    x: int,
    y: int,
    dx: int,
    dy: int,
    blocked: set[int],
    gx: int,
    gy: int,
) -> tuple[int, int] | None:
    """Ищет jump point из (x, y) в направлении (dx, dy).

    Возвращает координаты jump point или None, если упёрлись в стену/границу.
    """
    nx, ny = x + dx, y + dy
    while 0 <= nx < GRID_W and 0 <= ny < GRID_H:
        nidx = ny * GRID_W + nx
        if nidx in blocked:
            return None
        if nx == gx and ny == gy:
            return (nx, ny)

        if dx != 0:
            if _has_forced_neighbor_horizontal(nx, ny, dx, blocked):
                return (nx, ny)
            if nx == gx:
                return (nx, ny)
        else:
            if _has_forced_neighbor_vertical(nx, ny, dy, blocked):
                return (nx, ny)
            if ny == gy:
                return (nx, ny)

        nx += dx
        ny += dy
    return None


def _reconstruct_jps_path(
    came_from: dict[tuple[int, int], tuple[int, int] | None],
    end: tuple[int, int],
) -> list[int]:
    """Восстанавливает полный путь по цепочке jump point'ов."""
    jps_points: list[tuple[int, int]] = [end]
    cur = end
    while came_from[cur] is not None:
        parent = came_from[cur]
        assert parent is not None
        jps_points.append(parent)
        cur = parent
    jps_points.reverse()

    full: list[int] = [_idx(jps_points[0][0], jps_points[0][1])]
    for i in range(1, len(jps_points)):
        px, py = jps_points[i - 1]
        nx, ny = jps_points[i]
        dx = 0 if nx == px else (1 if nx > px else -1)
        dy = 0 if ny == py else (1 if ny > py else -1)
        while (px, py) != (nx, ny):
            px += dx
            py += dy
            full.append(_idx(px, py))
    return full


def jps4_int(start: int, goal: int, blocked: set[int]) -> list[int] | None:
    """JPS-поиск пути по 4-связной сетке.

    ВНИМАНИЕ: классический JPS определён для 8-связной сетки; здесь
    используется по инерции — см. комментарий к USE_JPS.
    """
    if start == goal:
        return [start]

    sx, sy = start % GRID_W, start // GRID_W
    gx, gy = goal % GRID_W, goal // GRID_W

    open_heap: list[tuple[int, int, int, int, tuple[int, int] | None]] = [
        (_manhattan_h(start, goal), 0, sx, sy, None)
    ]
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {(sx, sy): None}
    g_score: dict[tuple[int, int], int] = {(sx, sy): 0}

    while open_heap:
        _, g, cx, cy, parent_dir = heapq.heappop(open_heap)
        if cx == gx and cy == gy:
            return _reconstruct_jps_path(came_from, (cx, cy))

        if parent_dir is None:
            dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        else:
            pdx, pdy = parent_dir
            dirs = [(pdx, pdy)]
            if pdx != 0:
                dirs.extend([(0, 1), (0, -1)])
            else:
                dirs.extend([(1, 0), (-1, 0)])

        for dx, dy in dirs:
            jp = _jps_jump(cx, cy, dx, dy, blocked, gx, gy)
            if jp is None:
                continue
            jx, jy = jp
            step_cost = abs(jx - cx) + abs(jy - cy)
            ng = g + step_cost
            if ng < g_score.get((jx, jy), INF):
                g_score[(jx, jy)] = ng
                came_from[(jx, jy)] = (cx, cy)
                nh = abs(jx - gx) + abs(jy - gy)
                heapq.heappush(open_heap, (ng + nh, ng, jx, jy, (dx, dy)))
    return None


# endregion


# region ДИСПЕТЧЕР ПОИСКА


def find_path_int(
    start: int,
    goal: int,
    blocked: set[int],
    allowed: set[int] | None = None,
) -> list[int] | None:
    """Диспетчер: JPS (если включён и allowed=None) или A*."""
    if USE_JPS and allowed is None:
        return jps4_int(start, goal, blocked)
    return a_star_int(start, goal, blocked, allowed)


def bfs(
    start: Point,
    goal: Point,
    blocked: set[Point],
    allowed: set[Point] | None = None,
) -> list[Point] | None:
    """Точечный интерфейс поверх find_path_int: Point → int → path → Point."""
    s_i, g_i = p2i(start), p2i(goal)
    b_i = p2i_set(blocked)
    a_i = p2i_set(allowed) if allowed is not None else None
    path = find_path_int(s_i, g_i, b_i, a_i)
    if path is None:
        return None
    return i2p_list(path)


def reachable(
    start: Point,
    blocked: set[Point],
    allowed: set[Point] | None = None,
) -> set[Point]:
    """Множество достижимых точек от start, обходя blocked (и allowed)."""
    s_i = p2i(start)
    b_i = p2i_set(blocked)
    if allowed is None:
        return {i2p(i) for i in reachable_cached(s_i, b_i)}
    a_i = p2i_set(allowed)
    return {i2p(i) for i in reachable_int(s_i, b_i, a_i)}


def neighbors(
    point: Point, allowed: set[Point] | None = None
) -> Iterable[Point]:
    """4-связные соседи точки, с опциональной фильтрацией по allowed."""
    for i in int_neighbors(p2i(point)):
        p = i2p(i)
        if allowed is None or p in allowed:
            yield p


# endregion


# region ФАЗЫ 1–2: СВОБОДНЫЙ РОСТ


def initial_snake(rng: random.Random) -> list[Point]:
    """Ставит стартовую змейку из 3 клеток в случайное место поля."""
    cells = list(ALL_CELLS)
    rng.shuffle(cells)
    dirs = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    for head in cells:
        rng.shuffle(dirs)
        for dx, dy in dirs:
            cand = [(head[0] + i * dx, head[1] + i * dy) for i in range(3)]
            if all(p in ALL_CELLS for p in cand):
                return cand
    raise RuntimeError("Не удалось разместить стартовую змейку")


def spawn_food(snake: list[Point], rng: random.Random) -> Point | None:
    """Случайная свободная клетка для еды. None — если поле заполнено."""
    occupied = set(snake)
    free_cells = [c for c in ALL_CELLS if c not in occupied]
    return rng.choice(free_cells) if free_cells else None


def free_threshold(length: int) -> float:
    """Порог безопасного свободного места: растёт с длиной змейки."""
    ratio = length / TARGET_LEN
    if ratio < 0.4:
        return FREE_THRESHOLD_SHORT
    if ratio < 0.7:
        return FREE_THRESHOLD_MEDIUM
    return FREE_THRESHOLD_LONG


def safe_after_move(
    snake: list[Point],
    next_head: Point,
    ate: bool,
    relaxed: bool = False,
) -> tuple[bool, int]:
    """
    Проверка: безопасный ход: нет коллизий, хвост достижим, достаточно места.

    `relaxed=True` — ослабленный режим (голодная змейка), требующий меньше
    свободного пространства.
    """
    test = [next_head] + (snake if ate else snake[:-1])
    if len(set(test)) != len(test):
        return False, 0

    blocked = set(test[:-1])
    space = reachable(test[0], blocked)
    if test[-1] not in space:
        return False, 0

    free_count = len(space - set(test))
    if relaxed:
        need = max(SAFETY_MARGIN, len(test) // 2)
    else:
        # Доля от ОСТАВШЕГОСЯ свободного пула — иначе для длинной
        # змейки порог недостижим (был баг с * TOTAL_CELLS).
        free_pool = TOTAL_CELLS - len(test)
        need = max(
            free_threshold(len(test)) * free_pool,
            SAFETY_MARGIN,
        )
    return free_count >= need, free_count


def _first_step_if_safe(
    snake: list[Point],
    path: list[Point] | None,
    food: Point,
    hungry: bool,
) -> tuple[Point, bool] | None:
    """Первый шаг пути, если он безопасен. Иначе None."""
    if not path or len(path) < 2:
        return None
    first = path[1]
    ate = first == food
    if safe_after_move(snake, first, ate, relaxed=hungry)[0]:
        return first, ate
    return None


def _score_candidate(
    snake: list[Point],
    nxt: Point,
    food: Point,
    rng: random.Random,
    hungry: bool,
) -> tuple[float, Point, bool] | None:
    """Оценка одного хода. None — если ход отбрасывается."""
    ate = nxt == food
    safe, area = safe_after_move(snake, nxt, ate, relaxed=hungry)
    if not safe:
        test = [nxt] + (snake if ate else snake[:-1])
        if len(set(test)) != len(test):
            return None
        blocked2 = set(test[:-1])
        space = reachable(test[0], blocked2)
        if test[-1] not in space:
            return None
        area = len(space - set(test))
    score = float(area) + (FOOD_BONUS if ate else 0.0)
    score += rng.random() * 0.5
    return score, nxt, ate


def choose_move(
    snake: list[Point],
    food: Point,
    rng: random.Random,
    hungry: bool = False,
) -> tuple[Point | None, bool]:
    """Выбирает следующий ход по трём стратегиям (в порядке приоритета):

    1. Кратчайший путь к еде, если первый шаг безопасен.
    2. Tail-chase в середину тела, чтобы не застрять.
    3. Fallback: оценка всех 4-связных ходов по площади свободного места.

    Возвращает (next_head, ate) или (None, False), если ходов нет.
    """
    head, tail = snake[0], snake[-1]
    blocked = set(snake[:-1])
    tail_target = snake[len(snake) // 2]

    # 1. Кратчайший путь к еде — если первый шаг безопасен.
    step = _first_step_if_safe(snake, bfs(head, food, blocked), food, hungry)
    if step is not None:
        return step

    # 2. Иначе — tail-chase в середину тела.
    step = _first_step_if_safe(
        snake, bfs(head, tail_target, blocked), food, hungry
    )
    if step is not None:
        return step

    # 3. Fallback — оценка всех ходов.
    candidates: list[tuple[float, Point, bool]] = []
    for nxt in neighbors(head):
        if nxt in blocked and nxt != tail:
            continue
        scored = _score_candidate(snake, nxt, food, rng, hungry)
        if scored is not None:
            candidates.append(scored)

    if not candidates:
        return None, False
    _, best, ate = max(candidates, key=lambda item: item[0])
    return best, ate


def _advance_snake(
    snake: list[Point],
    move: Point,
    ate: bool,
) -> list[Point]:
    """Новое состояние змейки после хода: еда = рост, нет еды = движение."""
    if ate:
        return [move] + snake
    return [move] + snake[:-1]


def _apply_eating(
    snake: list[Point],
    rng: random.Random,
) -> Point | None:
    """Новая еда после поедания. None — если места больше нет
    (это ожидаемо только когда длина дошла до TARGET_LEN)."""
    if len(snake) == TARGET_LEN:
        return None
    return spawn_food(snake, rng)


def _grow_one_step(
    snake: list[Point],
    food: Point,
    rng: random.Random,
    steps_since_eat: int,
) -> tuple[list[Point], Point | None, int] | None:
    """Один шаг роста: выбирает ход, двигает змейку, обновляет еду.

    Returns: (snake, food, steps_since_eat) или None при тупике.
    """
    hungry = steps_since_eat >= HUNGRY_LIMIT
    move, ate = choose_move(snake, food, rng, hungry=hungry)
    if move is None:
        return None

    snake = _advance_snake(snake, move, ate)

    if not ate:
        return snake, food, steps_since_eat + 1

    new_food = _apply_eating(snake, rng)
    if new_food is None and len(snake) < TARGET_LEN:
        return None
    return snake, new_food, 0


def _update_stall(
    snake_len: int,
    last_len: int,
    stall: int,
) -> tuple[int, int, bool]:
    """Возвращает (last_len, stall, failed). failed=True — пора сдаваться."""
    if snake_len != last_len:
        return snake_len, 0, False
    stall += 1
    return last_len, stall, stall >= STALL_LIMIT


def grow_phase_once(
    seed: int | None = None,
) -> tuple[list[tuple[list[Point], Point | None]], list[Point]] | None:
    """Одна попытка свободного роста до TARGET_LEN.

    Returns: (frames, final_snake) или None при тупике/переросте.
    """
    rng = random.Random(seed)
    snake = initial_snake(rng)
    food: Point | None = spawn_food(snake, rng)
    if food is None:
        return None

    frames: list[tuple[list[Point], Point | None]] = [(list(snake), food)]
    stall = 0
    last_len = len(snake)
    steps_since_eat = 0

    for _ in range(MAX_STEPS_PER_ATTEMPT):
        if len(snake) == TARGET_LEN:
            return frames, snake

        # Здесь food: Point — он стал бы None только при достижении
        # TARGET_LEN, а этот случай отсечён проверкой выше.
        step_result = _grow_one_step(snake, food, rng, steps_since_eat)
        if step_result is None:
            return None
        snake, new_food, steps_since_eat = step_result

        if len(snake) > TARGET_LEN:
            return None

        # new_food == None только когда змейка доросла до TARGET_LEN:
        # новую еду спавнить уже некуда. Добавляем финальный кадр
        # без еды и выходим.
        if new_food is None:
            frames.append((list(snake), None))
            return frames, snake

        food = new_food

        last_len, stall, failed = _update_stall(len(snake), last_len, stall)
        if failed:
            return None

        prev_state, prev_food = frames[-1]
        if prev_food != food or prev_state != snake:
            frames.append((list(snake), food))

    return None


def grow_worker(seed: int) -> int | None:
    """Обёртка для ProcessPoolExecutor: возвращает seed при успехе.

    Список кадров не передаём через IPC — он занимает сотни КБ.
    Вместо этого главный процесс один раз переиграет победителя.
    """
    try:
        return seed if grow_phase_once(seed) is not None else None
    except Exception:
        # Не глотаем молча — иначе баги в grow_phase_once маскируются
        # под «неудачную попытку».
        traceback.print_exc()
        return None


def grow_phase_parallel(
    rng: random.Random,
) -> tuple[list[tuple[list[Point], Point | None]], list[Point]]:
    """Параллельный поиск seed'а, при котором рост доходит до TARGET_LEN.

    Пробует батчами по batch = PARALLEL_WORKERS * BATCH_SIZE_FACTOR seed'ов,
    пока не найдёт удачный или не исчерпает GROW_ATTEMPTS.
    """
    batch = max(PARALLEL_WORKERS * BATCH_SIZE_FACTOR, 4)
    attempts = 0
    winner_seed: int | None = None

    with ProcessPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        while attempts < GROW_ATTEMPTS and winner_seed is None:
            remaining = GROW_ATTEMPTS - attempts
            cur_batch = min(batch, remaining)
            seeds = [rng.randrange(0, 2**32) for _ in range(cur_batch)]
            # ex.map сохраняет порядок результатов → детерминированный
            # победитель при заданном SEED.
            for res in ex.map(grow_worker, seeds):
                attempts += 1
                _inline_status(f"Рост: попытка {attempts}/{GROW_ATTEMPTS}…")
                if res is not None:
                    winner_seed = res
                    break

    if winner_seed is None:
        raise RuntimeError(
            f"Не удалось набрать {TARGET_LEN} клеток "
            f"за {GROW_ATTEMPTS} попыток"
        )

    # Воркер возвращает только seed: список кадров — сотни КБ, гонять
    # его через IPC дороже, чем один раз переиграть здесь.
    result = grow_phase_once(winner_seed)
    if result is None:
        raise RuntimeError("Победивший seed не воспроизвёлся (недетерминизм?)")
    frames, snake = result
    suffix = (
        f"{len(frames)} {_plural_ru(len(frames), 'кадр', 'кадра', 'кадров')}"
    )
    if attempts == 1:
        _inline_finish(f"Рост: успех, {suffix}")
    else:
        _inline_finish(f"Рост: успех при попытке {attempts}, {suffix}")
    return frames, snake


# endregion


# region ФАЗА 3: ПЕРЕХОД НА КОНТУР


def simulate_trajectory(
    snake: list[Point], trajectory: list[Point]
) -> list[list[Point]] | None:
    """Симулирует пошаговое движение змейки по траектории.

    Returns: список кадров или None при коллизии/выходе за поле.
    """
    current = list(snake)
    frames = [list(current)]
    for new_head in trajectory[1:]:
        if new_head not in ALL_CELLS:
            return None
        if new_head in current[:-1]:
            return None
        current = [new_head] + current[:-1]
        if len(set(current)) != len(current):
            return None
        frames.append(list(current))
    return frames


def _transition_directions() -> tuple[int, ...]:
    """Набор направлений обхода контура с учётом LOOP_DIRECTION.

    0 — оба направления; иначе только заданное.
    """
    if LOOP_DIRECTION == 0:
        return (1, -1)
    return (LOOP_DIRECTION,)


def _build_trajectory(
    path: list[Point],
    target_index: int,
    direction: int,
    n: int,
) -> list[Point]:
    """
    Продолжает путь от target_index обходом контура в направлении direction.
    """
    trajectory = list(path)
    index = target_index
    wanted_len = len(path) + n - 1
    while len(trajectory) < wanted_len:
        index = (index + direction) % n
        trajectory.append(HEART[index])
    return trajectory


def _try_transition_candidate(
    snake: list[Point],
    path: list[Point],
    target_index: int,
    direction: int,
    n: int,
) -> list[list[Point]] | None:
    """Пробует уложить змейку на контур, придя в target_index.

    Returns: кадры перехода или None, если укладка невалидна.
    """
    trajectory = _build_trajectory(path, target_index, direction, n)
    frames = simulate_trajectory(snake, trajectory)
    if frames is None:
        return None

    final_state = frames[-1]
    if (
        len(final_state) != n
        or any(p not in HEART_SET for p in final_state)
        or len(set(final_state)) != n
    ):
        return None

    try:
        make_heart_loop(final_state)
    except (RuntimeError, ValueError):
        return None
    return frames


def plan_transition(
    snake: list[Point],
    rng: random.Random,
) -> list[list[Point]] | None:
    """Ищет кратчайшую безопасную укладку на контур.

    РАНДОМИЗАЦИЯ: порядок обхода целевых клеток контура перемешивается,
    чтобы при повторных вызовах на одном состоянии получать разные
    результаты.
    """
    best: tuple[int, list[list[Point]]] | None = None
    head = snake[0]
    n = len(HEART)
    body_set = set(snake[:-1])
    directions = _transition_directions()

    order = list(range(n))
    rng.shuffle(order)

    for target_index in order:
        target = HEART[target_index]
        if target in body_set:
            continue
        allowed = ALL_CELLS - (HEART_SET - {target})
        blocked = body_set | (HEART_SET - {target})
        path = bfs(head, target, blocked, allowed)
        if path is None or len(path) < 2:
            continue

        for direction in directions:
            frames = _try_transition_candidate(
                snake, path, target_index, direction, n
            )
            if frames is None:
                continue
            if best is None or len(path) < best[0]:
                best = (len(path), frames)

    return best[1] if best else None


def wander_path(
    current: list[Point], rng: random.Random
) -> list[Point] | None:
    """Случайная «прогулка» в свободной зоне (вне контура сердца)."""
    head = current[0]
    blocked = set(current[:-1]) | HEART_SET
    allowed = (ALL_CELLS - HEART_SET) | {head}
    space = reachable(head, blocked, allowed)
    free = space - set(current)
    if not free:
        return None
    targets = sorted(
        free,
        key=lambda cell: -(abs(cell[0] - head[0]) + abs(cell[1] - head[1])),
    )
    top = targets[:WANDER_TARGETS]
    rng.shuffle(top)
    for target in top:
        path = bfs(head, target, blocked, allowed)
        if path is not None and len(path) >= 2:
            return path
    return None


def random_safe_step(
    current: list[Point], rng: random.Random
) -> list[Point] | None:
    """Случайный безопасный ход — используется для jitter."""
    head = current[0]
    blocked = set(current[:-1])
    safe_moves = [n for n in neighbors(head) if n not in blocked]
    if not safe_moves:
        return None
    rng.shuffle(safe_moves)
    for nxt in safe_moves:
        moved = [nxt] + current[:-1]
        if len(set(moved)) != len(moved):
            continue
        return moved
    return None


def _apply_jitter(
    current: list[Point],
    rng: random.Random,
    frames: list[list[Point]],
) -> list[Point]:
    """Несколько случайных безопасных ходов для сброса паттерна."""
    for _ in range(ALIGNMENT_JITTER_STEPS):
        moved = random_safe_step(current, rng)
        if moved is None:
            break
        current = moved
        frames.append(list(current))
    return current


def _apply_wander(
    current: list[Point],
    rng: random.Random,
    frames: list[list[Point]],
) -> tuple[list[Point], bool]:
    """Прогулка по свободному пространству. True, если что-то сделали."""
    wander = wander_path(current, rng)
    if wander is None or len(wander) < 2:
        return current, False
    for nxt in wander[1:]:
        if nxt in current[:-1]:
            break
        current = [nxt] + current[:-1]
        frames.append(list(current))
    return current, True


def _best_gradient_head(
    current: list[Point],
    rng: random.Random,
) -> Point | None:
    """Голова, минимизирующая расстояние до контура. None, если нет ходов."""
    body = set(current[:-1])
    candidates: list[tuple[int, Point]] = []
    for nxt in neighbors(current[0]):
        if nxt in body:
            continue
        moved = [nxt] + current[:-1]
        if len(set(moved)) != len(moved):
            continue
        distance = INF
        moved_body = set(moved[:-1])
        for target in HEART:
            if target in moved_body:
                continue
            allowed = ALL_CELLS - (HEART_SET - {target})
            blocked = moved_body | (HEART_SET - {target})
            p = bfs(moved[0], target, blocked, allowed)
            if p is not None:
                distance = min(distance, len(p))
        candidates.append((distance, nxt))

    if not candidates:
        return None
    best_dist = min(dist for dist, _ in candidates)
    best_moves = [nxt for dist, nxt in candidates if dist == best_dist]
    return rng.choice(best_moves)


def find_safe_transition(
    snake: list[Point],
    rng: random.Random,
) -> tuple[list[list[Point]], list[list[Point]]]:
    """Ищет безопасный переход на контур.

    Стратегии по кругу: план укладки → jitter → wander → градиент.
    Raises: RuntimeError если ни одна стратегия не сработала.
    """
    alignment_frames: list[list[Point]] = []
    current = list(snake)

    for step in range(ALIGNMENT_LIMIT):
        transition = plan_transition(current, rng)
        if transition is not None:
            return alignment_frames, transition

        if step > 0 and step % 3 == 0:
            current = _apply_jitter(current, rng, alignment_frames)
            continue

        if any(p in HEART_SET for p in current):
            current, moved = _apply_wander(current, rng, alignment_frames)
            if moved:
                continue

        next_head = _best_gradient_head(current, rng)
        if next_head is None:
            moved = random_safe_step(current, rng)
            if moved is None:
                raise RuntimeError("Не найден безопасный шаг к контуру")
            current = moved
            alignment_frames.append(list(current))
            continue

        current = [next_head] + current[:-1]
        alignment_frames.append(list(current))

    raise RuntimeError("Не удалось найти безопасный вход на контур")


def make_heart_loop(final_snake: list[Point]) -> list[list[Point]]:
    """Генерирует бесконечный цикл: змейка бежит по контуру сердца.

    Определяет направление (по второму сегменту) и генерирует len(HEART)
    кадров, после которых состояние повторяется.
    """
    if len(final_snake) != len(HEART) or set(final_snake) != HEART_SET:
        raise RuntimeError("Змейка должна целиком занимать контур")
    head_index = HEART.index(final_snake[0])

    if final_snake[1] == HEART[(head_index - 1) % len(HEART)]:
        direction = 1
    elif final_snake[1] == HEART[(head_index + 1) % len(HEART)]:
        direction = -1
    else:
        raise RuntimeError("Сегменты не следуют по контуру")

    if LOOP_DIRECTION != 0 and direction != LOOP_DIRECTION:
        raise RuntimeError(
            f"Направление движения {direction} не совпадает "
            f"с LOOP_DIRECTION={LOOP_DIRECTION}"
        )

    loop_frames = [list(final_snake)]
    current = list(final_snake)
    for _ in range(len(HEART)):
        head_index = (head_index + direction) % len(HEART)
        current = [HEART[head_index]] + current[:-1]
        if len(set(current)) != len(current):
            raise RuntimeError("Самопересечение при движении")
        loop_frames.append(list(current))
    if loop_frames[-1] != loop_frames[0]:
        raise RuntimeError("Обход не замкнул состояние")
    return loop_frames


def simulate_once(
    rng: random.Random,
) -> tuple[list[list[Point]], list[Point | None], list[list[Point]]]:
    """Одна полная попытка: рост → укладка на контур → цикл.

    Returns: (all_snake, all_food, loop_snake).
    Raises: RuntimeError после ALIGNMENT_RETRIES неудач.
    """
    grow_frames, final_snake = grow_phase_parallel(rng)

    alignment_frames: list[list[Point]] = []
    transition_frames: list[list[Point]] = []
    last_error: Exception | None = None

    for attempt in range(1, ALIGNMENT_RETRIES + 1):
        _inline_status(
            f"Укладка на контур: попытка {attempt}/{ALIGNMENT_RETRIES}…"
        )
        try:
            sub_rng = random.Random(rng.randrange(0, 2**32))
            alignment_frames, transition_frames = find_safe_transition(
                final_snake, sub_rng
            )
            break
        except RuntimeError as err:
            last_error = err
    else:
        _inline_finish("Укладка на контур: провал")
        raise RuntimeError(
            f"Укладка не удалась за {ALIGNMENT_RETRIES} попыток: {last_error}"
        ) from last_error

    if attempt == 1:
        _inline_finish("Укладка на контур: успех")
    else:
        _inline_finish(
            f"Укладка на контур: успех при попытке "
            f"{attempt}/{ALIGNMENT_RETRIES}"
        )

    transition_frames = transition_frames[1:]
    all_snake = (
        [state for state, _ in grow_frames]
        + alignment_frames
        + transition_frames
    )
    all_food: list[Point | None] = [food for _, food in grow_frames] + [
        None
    ] * (len(alignment_frames) + len(transition_frames))
    loop_snake = make_heart_loop(all_snake[-1])
    transition_count = len(alignment_frames) + len(transition_frames)
    _inline_finish(
        f"Переход: {transition_count} "
        f"{_plural_ru(transition_count, 'кадр', 'кадра', 'кадров')}; "
        f"цикл: {len(loop_snake) - 1} "
        f"{_plural_ru(len(loop_snake) - 1, 'шаг', 'шага', 'шагов')}"
    )
    return all_snake, all_food, loop_snake


def simulate() -> tuple[
    list[list[Point]], list[Point | None], list[list[Point]]
]:
    """Точка входа симуляции: ретраи до SIMULATION_ATTEMPTS раз."""
    rng = random.Random(SEED) if SEED is not None else random.Random()
    if SEED is not None:
        LOG.info("SEED = %d", SEED)

    last_error: Exception | None = None
    for attempt in range(1, SIMULATION_ATTEMPTS + 1):
        try:
            result = simulate_once(rng)
            if attempt == 1:
                _inline_finish("Симуляция: успех")
            else:
                _inline_finish(
                    f"Симуляция: успех с попытки "
                    f"{attempt}/{SIMULATION_ATTEMPTS}"
                )
            return result
        except RuntimeError as error:
            last_error = error
    _inline_finish("Симуляция: провал")
    raise RuntimeError(
        f"Не удалось построить анимацию за {SIMULATION_ATTEMPTS} попыток: "
        f"{last_error}"
    ) from last_error


# endregion


# region ФАЗА 4: SVG


def _values(values: Sequence[object]) -> str:
    """Собирает список значений в строку через ';' для SVG-атрибута values."""
    return ";".join(str(value) for value in values)


def _fmt(value: float | int) -> str:
    """Форматирует координату: убирает trailing '.0' у целых чисел."""
    s = f"{value:.1f}" if isinstance(value, float) else str(value)
    return s.rstrip("0").rstrip(".") if "." in s else s


def _animate_once(
    attribute: str,
    values: Sequence[object],
    duration: float,
    mode: str = "linear",
) -> str:
    """<animate> на один проход: отрисовать рост/переход и замереть."""
    return (
        f'<animate attributeName="{attribute}" values="{_values(values)}" '
        f'calcMode="{mode}" dur="{duration:.3f}s" '
        'repeatCount="1" fill="freeze"/>'
    )


def _animate_loop(
    attribute: str,
    values: Sequence[object],
    duration: float,
    begin: float,
) -> str:
    """<animate> с бесконечным повторением: цикл движения по контуру.

    begin = initial_duration, чтобы цикл стартовал после фазы роста.
    """
    return (
        f'<animate attributeName="{attribute}" values="{_values(values)}" '
        f'calcMode="linear" dur="{duration:.3f}s" '
        f'begin="{begin:.3f}s" repeatCount="indefinite" fill="freeze"/>'
    )


def _render_grid(out: list[str]) -> None:
    """
    Рисует сетку: обычные клетки — GRID_CELL_COLOR, контур — HEART_CELL_COLOR.
    """
    for x in range(GRID_W):
        for y in range(GRID_H):
            gx = PADDING + x * CELL + GRID_CELL_INSET
            gy = PADDING + y * CELL + GRID_CELL_INSET
            size = CELL - 2 * GRID_CELL_INSET
            color = (
                HEART_CELL_COLOR if (x, y) in HEART_SET else GRID_CELL_COLOR
            )
            out.append(
                f'<rect x="{gx}" y="{gy}" width="{size}" height="{size}" '
                f'rx="{GRID_CELL_RX}" fill="{color}"/>'
            )


def dedupe_frames(
    all_snake: list[list[Point]],
    all_food: list[Point | None],
) -> tuple[list[list[Point]], list[Point | None]]:
    """Удаляет подряд идущие дубликаты кадров (snake+food одинаковы)."""
    if not all_snake:
        return [], []
    out_snake: list[list[Point]] = [all_snake[0]]
    out_food: list[Point | None] = [all_food[0]]
    prev_key = (tuple(all_snake[0]), all_food[0])
    for i in range(1, len(all_snake)):
        key = (tuple(all_snake[i]), all_food[i])
        if key == prev_key:
            continue
        out_snake.append(all_snake[i])
        out_food.append(all_food[i])
        prev_key = key
    return out_snake, out_food


def build_svg(
    all_snake: list[list[Point]],
    all_food: list[Point | None],
    loop_snake: list[list[Point]],
    output_path: Path,
) -> None:
    """Генерирует SVG-анимацию: рост → переход → вечный цикл по контуру.

    Каждый сегмент змейки — отдельный <rect> с анимацией x/y/opacity
    для фазы роста и <animate> с repeatCount="indefinite" для цикла.
    """
    raw_count = len(all_snake)
    all_snake, all_food = dedupe_frames(all_snake, all_food)
    dedup_count = len(all_snake)
    if raw_count != dedup_count:
        LOG.info(
            "Дедупликация: %d → %d кадров (−%.0f%%)",
            raw_count,
            dedup_count,
            100.0 * (raw_count - dedup_count) / max(raw_count, 1),
        )

    if len(all_snake) != len(all_food):
        raise ValueError("Число состояний змейки и еды не совпадает")
    if not all_snake or not loop_snake:
        raise ValueError("Недостаточно кадров для генерации SVG")

    frame_count = len(all_snake)
    initial_duration = max(1, frame_count - 1) * STEP_TIME
    loop_duration = max(1, len(loop_snake) - 1) * STEP_TIME
    max_len = max(len(state) for state in all_snake)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" '
        f'height="{SVG_H}" viewBox="0 0 {SVG_W} {SVG_H}">',
        f'<rect width="100%" height="100%" fill="{BG}"/>',
    ]
    _render_grid(out)

    # Еда: точка в центре клетки; при отсутствии еды уводим за экран
    # (OFFSCREEN_COORD), чтобы не мелькала на последних кадрах.
    food_x = [
        _fmt(PADDING + food[0] * CELL + CELL / 2) if food else OFFSCREEN_COORD
        for food in all_food
    ]
    food_y = [
        _fmt(PADDING + food[1] * CELL + CELL / 2) if food else OFFSCREEN_COORD
        for food in all_food
    ]
    out.append('<circle r="4" fill="#ff3b30">')
    out.append(_animate_once("cx", food_x, initial_duration, "discrete"))
    out.append(_animate_once("cy", food_y, initial_duration, "discrete"))
    out.append("</circle>")

    # Сегменты змейки рисуем от хвоста к голове — голова поверх.
    for segment_index in range(max_len - 1, -1, -1):
        size = SEG_HEAD_SIZE + (SEG_TAIL_SIZE - SEG_HEAD_SIZE) * (
            segment_index / max(1, max_len - 1)
        )
        offset = (CELL - size) / 2
        xs: list[str] = []
        ys: list[str] = []
        opacity: list[int] = []
        for state in all_snake:
            if segment_index < len(state):
                px, py = state[segment_index]
                opacity.append(1)
            else:
                px, py = state[-1]
                opacity.append(0)
            xs.append(_fmt(PADDING + px * CELL + offset))
            ys.append(_fmt(PADDING + py * CELL + offset))

        loop_x = [
            _fmt(PADDING + state[segment_index][0] * CELL + offset)
            for state in loop_snake
        ]
        loop_y = [
            _fmt(PADDING + state[segment_index][1] * CELL + offset)
            for state in loop_snake
        ]

        # Голова и шея — ярче, остальные сегменты — градиент по длине.
        if segment_index == 0:
            color = "#d6ffd6"
        elif segment_index == 1:
            color = "#8dff8d"
        else:
            green = int(160 + (1 - segment_index / max(1, max_len - 1)) * 80)
            color = f"rgb(20,{green},20)"

        out.append(
            f'<rect width="{_fmt(size)}" height="{_fmt(size)}" '
            f'rx="{_fmt(size * 0.25)}" fill="{color}">'
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out), encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    LOG.info("SVG сохранён: %s (%.1f КБ)", output_path, size_kb)


# endregion


# region ВИЗУАЛИЗАЦИЯ


def visualize_search_at_frame(
    all_snake: list[list[Point]],
    all_food: list[Point | None],
    frame: int,
    output_path: Path,
) -> None:
    """Отладочная визуализация: explored-узлы (серые) + путь A* (жёлтый)."""
    if frame < 0 or frame >= len(all_snake):
        LOG.info("Кадр %d вне диапазона — визуализация пропущена", frame)
        return
    snake = all_snake[frame]
    food = all_food[frame]
    if food is None:
        LOG.info("Кадр %d без еды — визуализация пропущена", frame)
        return

    start_i = p2i(snake[0])
    goal_i = p2i(food)
    blocked_i = p2i_set(set(snake[:-1]))
    path, explored = a_star_int_with_trace(start_i, goal_i, blocked_i)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" '
        f'height="{SVG_H}" viewBox="0 0 {SVG_W} {SVG_H}">',
        f'<rect width="100%" height="100%" fill="{BG}"/>',
    ]
    _render_grid(out)

    # Серые клетки — всё, что A* успел раскрыть.
    for i in explored:
        x, y = i2p(i)
        gx = PADDING + x * CELL + GRID_CELL_INSET
        gy = PADDING + y * CELL + GRID_CELL_INSET
        size = CELL - 2 * GRID_CELL_INSET
        out.append(
            f'<rect x="{gx}" y="{gy}" width="{size}" height="{size}" '
            f'rx="{GRID_CELL_RX}" fill="#3a3a3a" opacity="0.6"/>'
        )

    # Жёлтые — сам путь.
    if path:
        for i in path:
            x, y = i2p(i)
            gx = PADDING + x * CELL + GRID_CELL_INSET
            gy = PADDING + y * CELL + GRID_CELL_INSET
            size = CELL - 2 * GRID_CELL_INSET
            out.append(
                f'<rect x="{gx}" y="{gy}" width="{size}" height="{size}" '
                f'rx="{GRID_CELL_RX}" fill="#ffcc00" opacity="0.7"/>'
            )

    # Змейка поверх всего.
    for idx, p in enumerate(snake):
        x, y = p
        gx = PADDING + x * CELL + GRID_CELL_INSET
        gy = PADDING + y * CELL + GRID_CELL_INSET
        size = CELL - 2 * GRID_CELL_INSET
        if idx == 0:
            color = "#d6ffd6"
        elif idx == 1:
            color = "#8dff8d"
        else:
            green = int(160 + (1 - idx / max(1, len(snake) - 1)) * 80)
            color = f"rgb(20,{green},20)"
        out.append(
            f'<rect x="{gx}" y="{gy}" width="{size}" height="{size}" '
            f'rx="{GRID_CELL_RX}" fill="{color}"/>'
        )

    fx = PADDING + food[0] * CELL + CELL / 2
    fy = PADDING + food[1] * CELL + CELL / 2
    out.append(f'<circle cx="{fx}" cy="{fy}" r="4" fill="#ff3b30"/>')
    out.append("</svg>")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out), encoding="utf-8")
    LOG.info(
        "Визуализация: %s (кадр %d, explored=%d, path=%s)",
        output_path,
        frame,
        len(explored),
        len(path) if path else "нет",
    )


# endregion


# region ENTRY

if __name__ == "__main__":
    setup_logging()
    LOG.info("Поле: %d × %d (%d клеток)", GRID_W, GRID_H, TOTAL_CELLS)
    LOG.info("Клеток контура: %d", TARGET_LEN)
    LOG.info("Воркеров: %d", PARALLEL_WORKERS)
    LOG.info("Алгоритм поиска: %s", "JPS" if USE_JPS else "A*")
    LOG.info(
        "Кэш reachable: %s", "включён" if USE_REACHABLE_CACHE else "выключен"
    )

    if not is_closed_track(HEART):
        raise RuntimeError("Контур сердца не является замкнутым треком")
    if TOTAL_CELLS < TARGET_LEN + 5:
        raise RuntimeError("Поля слишком мало для целевой длины змейки")

    all_snake, all_food, loop_snake = simulate()

    if _REACH_TRACKER is not None:
        LOG.info(_REACH_TRACKER.stats())

    output = (
        Path(__file__).resolve().parent.parent / "assets" / "snake_heart.svg"
    )
    build_svg(all_snake, all_food, loop_snake, output)

    if VISUALIZE_SEARCH:
        debug_output = output.parent / VISUALIZE_OUTPUT_NAME
        visualize_search_at_frame(
            all_snake, all_food, VISUALIZE_FRAME, debug_output
        )
# endregion
