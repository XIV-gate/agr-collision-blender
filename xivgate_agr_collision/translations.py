# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Blender-native user interface translations.

import bpy


_RU_DEFAULT = {
    "AGR Collision": "Коллайдер AGR",
    "Collision Quality": "Качество коллизии",
    "Generate & Validate": "Создание и проверка",
    "Last Result": "Последний результат",
    "Advanced Collision Settings": "Расширенные настройки коллизии",
    "Geometry Preprocessing": "Предварительная обработка геометрии",
    "Convex Search Limits": "Ограничения поиска выпуклых частей",
    "Viewport Output": "Отображение результата",
    "Source": "Источник",
    "No active mesh": "Нет активного меш-объекта",
    "Accuracy": "Точность",
    "Exact Geometry Repair": "Точное восстановление геометрии",
    "Exact Split Limits": "Ограничения точного разбиения",
    "Output": "Результат",
    "Status": "Статус",
    "Ready": "Готово",
    "Source: {}": "Источник: {}",
    "Input / working: {:,} / {:,} tris": "Исходных / рабочих: {:,} / {:,} трис",
    "UCX: {} objects, {:,} tris": "UCX: {} объектов, {:,} трис",
    "Max deviation: {:.3f} m": "Макс. отклонение: {:.3f} м",
    "Tolerance": "Допуск",
    "Maximum allowed collision deviation; 0.10 m is the strict universal AGR limit": (
        "Максимально допустимое отклонение коллизии; 0,10 м — строгий "
        "универсальный лимит AGR"
    ),
    "Min Feature": "Мин. размер детали",
    "Separate details smaller than this size may be removed during preprocessing": (
        "Отдельные детали меньше этого размера могут быть удалены "
        "при предварительной обработке"
    ),
    "Fuse Selected Geometry": "Объединить выбранную геометрию",
    (
        "Merge nearby vertices in the combined hidden proxy before volume repair; "
        "this can reconnect walls and other parts split across source objects"
    ): (
        "Объединяет близкие вершины в общем скрытом прокси до восстановления "
        "объёма; позволяет соединить стены и другие части, разделённые между "
        "исходными объектами"
    ),
    "Fuse Distance": "Расстояние объединения",
    "Maximum distance used to merge nearby proxy vertices": (
        "Максимальное расстояние для объединения близких вершин прокси"
    ),
    "Skip Separate Thin Parts": "Пропускать отдельные тонкие части",
    (
        "Ignore separate thin components such as canopies and fences; "
        "the largest component is never removed"
    ): (
        "Игнорирует отдельные тонкие компоненты, например козырьки и ограждения; "
        "крупнейший компонент никогда не удаляется"
    ),
    "Thin Threshold": "Порог толщины",
    "Maximum thickness of separate components that may be ignored": (
        "Максимальная толщина отдельных компонентов, которые можно игнорировать"
    ),
    "Gap": "Зазор",
    "Air gap between neighbouring hulls; 0.0002 m is the AGR minimum": (
        "Воздушный зазор между соседними выпуклыми оболочками; 0,0002 м — минимум AGR"
    ),
    "Optimization Passes": "Проходы оптимизации",
    (
        "Try deterministic tie variants and keep the smallest complete result; "
        "each pass runs the full search"
    ): (
        "Проверяет детерминированные варианты с равной оценкой и сохраняет "
        "наименьший полный результат; каждый проход выполняет полный поиск"
    ),
    "Seed": "Начальное значение",
    "Base seed for deterministic split tie variants": (
        "Базовый сид для детерминированных вариантов разбиения с равной оценкой"
    ),
    "Max Parts": "Макс. частей",
    "Hard maximum number of UCX hulls": "Жёсткий максимум количества UCX-оболочек",
    "Search Depth": "Глубина поиска",
    "Maximum number of recursive separating planes": (
        "Максимальное количество рекурсивных разделяющих плоскостей"
    ),
    "Wire Display": "Каркасное отображение",
    "Display generated colliders as wireframe objects": (
        "Отображает созданные коллайдеры в каркасном режиме"
    ),
    "Hide Sources After Generation": "Скрыть исходники после генерации",
    "Hide selected visual sources after a successful generation": (
        "Скрывает выбранные визуальные исходники после успешной генерации"
    ),
    "Last Source": "Последний источник",
    "Last Colliders": "Последние коллайдеры",
    "Last Triangles": "Последние треугольники",
    "Last Deviation": "Последнее отклонение",
    "Last Input Triangles": "Последние исходные треугольники",
    "Last Proxy Triangles": "Последние треугольники прокси",
    "Analyze selected objects through the same hidden preprocessing used by generation": (
        "Анализирует выбранные объекты с той же скрытой предварительной "
        "обработкой, которая используется при генерации"
    ),
    "Build a new convex UCX set and atomically replace the previous generated set": (
        "Создаёт новый выпуклый набор UCX и целиком заменяет предыдущий "
        "сгенерированный набор"
    ),
    "Validate naming, convexity, closure, intersections and AGR triangle budget": (
        "Проверяет имена, выпуклость, замкнутость, пересечения и бюджет "
        "треугольников AGR"
    ),
    "Remove only generated colliders associated with the active source": (
        "Удаляет только сгенерированные коллайдеры, связанные с активным источником"
    ),
    "Analysis failed": "Ошибка анализа",
    "Analyzed in {:.2f}s": "Проанализировано за {:.2f} с",
    "Building proxy...": "Построение прокси...",
    "Searching convex decomposition...": "Поиск выпуклого разбиения...",
    "Generated with warnings in {:.2f}s": "Создано с предупреждениями за {:.2f} с",
    "Valid result in {:.2f}s": "Корректный результат за {:.2f} с",
    "Generation failed": "Ошибка генерации",
    "Validation passed": "Проверка пройдена",
    "Validation failed": "Проверка не пройдена",
    "Removed {} collider(s)": "Удалено коллайдеров: {}",
}

_RU_OPERATORS = {
    "Analyze Selected": "Анализировать выбранное",
    "Generate / Regenerate": "Создать / пересоздать",
    "Validate Colliders": "Проверить коллайдеры",
    "Remove Generated": "Удалить созданное",
}

TRANSLATIONS = {
    "ru_RU": {
        **{("*", source): target for source, target in _RU_DEFAULT.items()},
        **{
            ("Operator", source): target
            for source, target in _RU_OPERATORS.items()
        },
    }
}


def register(module_name):
    bpy.app.translations.register(module_name, TRANSLATIONS)


def unregister(module_name):
    try:
        bpy.app.translations.unregister(module_name)
    except (RuntimeError, ValueError):
        pass


def iface(message):
    return bpy.app.translations.pgettext_iface(message)
