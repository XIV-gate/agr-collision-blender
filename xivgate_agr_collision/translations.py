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
    "Viewport Output": "Отображение результата",
    "Manual Cleanup": "Ручная очистка",
    "Topology-changing preprocess is enabled": (
        "Включена предобработка с изменением топологии"
    ),
    "Lossless components; validated replacement only": (
        "Компоненты сохраняются; замена только после проверки"
    ),
    "Allow Topology-Changing Preprocess": (
        "Разрешить предобработку с изменением топологии"
    ),
    (
        "Explicitly allow source fusion and removal of separate small or "
        "thin components; keep disabled for lossless collision generation"
    ): (
        "Явно разрешает объединение исходников и удаление отдельных мелких "
        "или тонких компонентов; оставьте выключенным для генерации коллизии "
        "без потерь"
    ),
    "Lossless: no component removal or broad source fusion": (
        "Без потерь: компоненты не удаляются, исходники не объединяются целиком"
    ),
    "Source": "Источник",
    "No active mesh": "Нет активного меш-объекта",
    "Status": "Статус",
    "Ready": "Готово",
    "Source: {}": "Источник: {}",
    "Input / working: {:,} / {:,} tris": "Исходных / рабочих: {:,} / {:,} трис",
    "UCX: {} objects, {:,} tris": "UCX: {} объектов, {:,} трис",
    "Max deviation: {:.3f} m": "Макс. отклонение: {:.3f} м",
    "Min Feature": "Мин. размер детали",
    (
        "When topology-changing preprocessing is explicitly enabled, "
        "separate details smaller than this size may be removed"
    ): (
        "Если предобработка с изменением топологии явно включена, отдельные "
        "детали меньше этого размера могут быть удалены"
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
    "Parts of the model thinner than this receive no collision": (
        "Части модели тоньше этого значения не получают коллизию"
    ),
    "Gap": "Зазор",
    "Air gap between neighbouring hulls; 0.0002 m is the AGR minimum": (
        "Воздушный зазор между соседними выпуклыми оболочками; 0,0002 м — минимум AGR"
    ),
    "Optimization Passes": "Проходы оптимизации",
    (
        "Re-cut the parts that produced the most pieces and keep "
        "variants with fewer pieces; every variant stays exact, so more "
        "passes only trade generation time for a simpler collision. "
        "1 is a single fast pass; around 100 gives the smallest sets on "
        "complex buildings in a few minutes"
    ): (
        "Перерезает части, давшие больше всего кусков, и оставляет варианты "
        "с меньшим их числом; каждый вариант остаётся точным, поэтому "
        "дополнительные проходы меняют только время генерации. 1 — один "
        "быстрый проход; около 100 даёт самые экономные наборы на сложных "
        "зданиях за несколько минут"
    ),
    "Wire Display": "Каркасное отображение",
    "Display generated colliders as wireframe objects": (
        "Отображает созданные коллайдеры в каркасном режиме"
    ),
    "Toggle wire display for the active AGR collision set": (
        "Переключает каркасное отображение активного набора коллизии AGR"
    ),
    "Hide Sources After Generation": "Скрыть исходники после генерации",
    "Hide selected visual sources after a successful generation": (
        "Скрывает выбранные визуальные исходники после успешной генерации"
    ),
    "Hide or restore source objects for the active AGR collision set": (
        "Скрывает или возвращает исходные объекты активного набора коллизии AGR"
    ),
    "Open Progress Console During Generation": (
        "Открывать консоль прогресса при генерации"
    ),
    (
        "Open a temporary console while collision generation is running "
        "and print progress heartbeats"
    ): (
        "Открывает временную консоль на время генерации коллизии и выводит "
        "периодические сообщения о ходе выполнения"
    ),
    "Validate": "Проверить",
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
