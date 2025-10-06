# -*- coding: utf-8 -*-
"""
merged_summary.py
Склеивает платформенные summary из:
  - Csv/yamaps_summary.csv
  - Csv/gmaps_summary.csv
  - Csv/2gis_summary.csv
в один файл:
  - Csv/merged_summary.csv

Схема колонок сохраняется:
  "organization","platform","rating_avg","ratings_count","reviews_count"

Правила:
- Отсутствующие файлы тихо пропускаются.
- Пустые значения числовых колонок -> 0 (int/float).
- Дубликаты по (organization, platform) — оставляем последнюю встретившуюся строку.
- Сортировка в выходе: organization (A→Я), затем platform в порядке:
  Yandex Maps, Google Maps, 2GIS, затем прочие.
"""

import csv
from pathlib import Path

# ---- входные пути
IN_FILES = [
    Path("Csv/Summary/yamaps_summary.csv"),
    Path("Csv/Summary/gmaps_summary.csv"),
    Path("Csv/Summary/2gis_summary.csv"),
]

# ---- выходной путь
OUT_FILE = Path("Csv/Summary/merged_summary.csv")

# ---- вспомогалки
PLATFORM_ORDER = {
    "Yandex Maps": 0,
    "Google Maps": 1,
    "2GIS": 2,
}

def to_float(x):
    if x is None: return 0.0
    s = str(x).strip().replace("\xa0", " ").replace("\u202f", " ")
    if s == "": return 0.0
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        # если вдруг приходит что-то типа "—" или "N/A"
        return 0.0

def to_int(x):
    if x is None: return 0
    s = str(x).strip().replace("\xa0", " ").replace("\u202f", " ")
    if s == "": return 0
    # вытащим только цифры и пробелы
    digits = "".join(ch for ch in s if ch.isdigit() or ch == " ")
    digits = digits.replace(" ", "")
    try:
        return int(digits) if digits else 0
    except Exception:
        return 0

def platform_sort_key(p):
    return PLATFORM_ORDER.get(p, 99)

def read_one(path: Path):
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        # ожидаемые поля
        for row in r:
            rows.append({
                "organization": (row.get("organization") or "").strip(),
                "platform":     (row.get("platform") or "").strip(),
                "rating_avg":   to_float(row.get("rating_avg")),
                "ratings_count":to_int(row.get("ratings_count")),
                "reviews_count":to_int(row.get("reviews_count")),
            })
    return rows

def main():
    # собираем все
    merged = []
    for p in IN_FILES:
        merged.extend(read_one(p))

    if not merged:
        print("Нет входных файлов с данными. Нечего объединять.")
        return

    # удаляем дубли по (organization, platform) — оставляем последнюю
    dedup = {}
    for row in merged:
        key = (row["organization"], row["platform"])
        dedup[key] = row

    # сортируем
    merged_unique = sorted(
        dedup.values(),
        key=lambda x: (x["organization"].lower(), platform_sort_key(x["platform"]), x["platform"].lower())
    )

    # пишем csv
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["organization","platform","rating_avg","ratings_count","reviews_count"], quoting=csv.QUOTE_ALL)
        w.writeheader()
        for row in merged_unique:
            w.writerow({
                "organization": row["organization"],
                "platform":     row["platform"],
                "rating_avg":   f'{row["rating_avg"]:.2f}'.rstrip('0').rstrip('.') if row["rating_avg"] else 0,
                "ratings_count":row["ratings_count"],
                "reviews_count":row["reviews_count"],
            })

    print(f"Готово. Итоговый файл: {OUT_FILE} (строк: {len(merged_unique)})")

if __name__ == "__main__":
    main()
