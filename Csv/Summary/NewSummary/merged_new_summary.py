# -*- coding: utf-8 -*-
"""
Объединение трёх *summary_new.csv в один all_new_summary.csv без слияния в all_summary.

Источники (если какого-то файла нет — он пропускается):
  Csv/Summary/NewSummary/yamaps_summary_new.csv
  Csv/Summary/NewSummary/gmaps_summary_new.csv
  Csv/Summary/NewSummary/2gis_summary_new.csv

Результат:
  Csv/Summary/NewSummary/all_new_summary.csv

Полям принудительно приводятся типы/форматы:
  - rating_avg: число -> строка; если пусто/ошибка -> "0"
    (ноль пишется как "0", а не "0.0")
  - ratings_count, reviews_count: целые -> строка; пусто/ошибка -> "0"
"""

import csv
from pathlib import Path

# ----- Пути -----
BASE_DIR = Path("Csv/Summary/NewSummary")
INPUTS = [
    BASE_DIR / "yamaps_summary_new.csv",
    BASE_DIR / "gmaps_summary_new.csv",
    BASE_DIR / "2gis_summary_new.csv",
]
OUT = BASE_DIR / "all_new_summary.csv"

FIELDS = ["organization", "platform", "rating_avg", "ratings_count", "reviews_count"]


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        rows = []
        for row in r:
            rows.append({k: (row.get(k, "") or "").strip() for k in (r.fieldnames or FIELDS)})
        return rows


def _to_rating_str(x: str) -> str:
    """rating_avg → строка: '0' если пусто/некорректно; иначе компактное представление."""
    s = (x or "").replace(",", ".").strip()
    if not s:
        return "0"
    try:
        val = float(s)
    except Exception:
        return "0"
    # избегаем '0.0'
    if abs(val) < 1e-9:
        return "0"
    # убираем лишний .0 у целых
    if abs(val - int(val)) < 1e-9:
        return str(int(val))
    # короткая форма без лишних нулей в конце
    s = f"{val:.10g}"  # ограничим длину
    return s


def _to_int_str(x: str) -> str:
    """целочисленные поля → строка: '0' если пусто/некорректно."""
    s = (x or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    if not s:
        return "0"
    # вытащим первую числовую подпоследовательность
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif num:
            break
    if not num:
        # вдруг это уже число с минусом/плюсом или '123 456'
        try:
            return str(int("".join(s.split())))
        except Exception:
            return "0"
    try:
        return str(int(num))
    except Exception:
        return "0"


def _key(row: dict) -> tuple[str, str]:
    return (
        (row.get("platform") or "").strip().lower(),
        (row.get("organization") or "").strip().lower(),
    )


def _normalize_row(row: dict) -> dict:
    return {
        "organization": (row.get("organization") or "").strip(),
        "platform": (row.get("platform") or "").strip(),
        "rating_avg": _to_rating_str(row.get("rating_avg", "")),
        "ratings_count": _to_int_str(row.get("ratings_count", "")),
        "reviews_count": _to_int_str(row.get("reviews_count", "")),
    }


def main():
    # читаем все входы
    raw_rows: list[dict] = []
    for p in INPUTS:
        raw_rows.extend(_read_rows(p))

    # нормализуем и дедупим по (platform, organization)
    # если дубликаты — берём тот, у которого больше reviews_count (как более «свежий»/полный)
    merged: dict[tuple[str, str], dict] = {}
    for r in raw_rows:
        nr = _normalize_row(r)
        k = _key(nr)
        if k not in merged:
            merged[k] = nr
        else:
            # выбираем запись с бОльшим reviews_count
            try:
                cur = int(merged[k]["reviews_count"])
                new = int(nr["reviews_count"])
            except Exception:
                cur = 0
                new = 0
            if new > cur:
                merged[k] = nr

    # записываем результат
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        # можно упорядочить по platform, затем organization
        for _, row in sorted(merged.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            w.writerow(row)

    print(f"[OK] Готово. Записано строк: {len(merged)}")
    print(f"Файл: {OUT}")


if __name__ == "__main__":
    main()
