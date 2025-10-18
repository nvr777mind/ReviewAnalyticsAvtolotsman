import csv
from pathlib import Path

NEWREV_DIR = Path("Csv/Reviews/NewReviews")
DELTA_FILES = [
    NEWREV_DIR / "yamaps_new_since.csv",
    NEWREV_DIR / "gmaps_new_since.csv",
    NEWREV_DIR / "2gis_new_since.csv",
]
ALL_NEW_SINCE = NEWREV_DIR / "all_new_since.csv"

ALL_REVIEWS = Path("Csv/Reviews/all_reviews.csv")

BASE_FIELDS = ["rating", "author", "date_iso", "text", "platform", "organization"]


def _norm(s: str) -> str:
    """Нормализация для ключа: трим + свёртка пробелов + убираем \r\n."""
    if s is None:
        return ""
    s = s.replace("\r", " ").replace("\n", " ")
    s = " ".join(s.split())
    return s.strip()


def _make_key(row: dict) -> tuple:
    """Ключ уникальности отзыва."""
    return (
        _norm(row.get("platform", "")).lower(),
        _norm(row.get("organization", "")).lower(),
        _norm(row.get("author", "")),
        _norm(row.get("date_iso", "")),
        _norm(row.get("text", "")),
    )


def _read_csv_safe(path: Path) -> tuple[list[dict], list[str]]:
    """
    Считывает CSV если он есть. Возвращает (rows, fieldnames).
    При отсутствии — ([], BASE_FIELDS).
    """
    if not path.exists():
        return [], BASE_FIELDS[:]
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or BASE_FIELDS[:]
        rows = [row for row in reader]
    return rows, list(fieldnames)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """Перезаписывает CSV с заданными полями, гарантируя наличие всех колонок."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            out = {k: (r.get(k, "") if r.get(k, "") is not None else "") for k in fieldnames}
            w.writerow(out)


def build_all_new_since(delta_paths: list[Path]) -> tuple[list[dict], list[str], int]:
    """
    Объединяет несколько дельт в одну с дедупликацией.
    Возврат: (rows, fieldnames, total_in_sources)
      rows            — уникальные строки
      fieldnames      — объединённые заголовки
      total_in_sources— сколько строк было суммарно во входных файлах
    """
    union_fields: list[str] = BASE_FIELDS[:]
    combined: list[dict] = []
    seen_keys = set()
    total_in_sources = 0

    for p in delta_paths:
        rows, flds = _read_csv_safe(p)
        total_in_sources += len(rows)
        for col in (flds or []):
            if col not in union_fields:
                union_fields.append(col)

        local_seen = set()
        for row in rows:
            for k in BASE_FIELDS:
                if k in row and isinstance(row[k], str):
                    row[k] = _norm(row[k])

            key = _make_key(row)
            if key in local_seen:
                continue
            local_seen.add(key)

            if key in seen_keys:
                continue
            seen_keys.add(key)

            combined.append(row)

    for bf in BASE_FIELDS:
        if bf not in union_fields:
            union_fields.append(bf)

    return combined, union_fields, total_in_sources


def merge_into_all_reviews(all_new_rows: list[dict], new_fields: list[str]) -> int:
    """
    Вливает объединённую дельту в all_reviews.csv с дедупликацией.
    Возврат: сколько реально добавлено.
    """
    all_rows, all_fields = _read_csv_safe(ALL_REVIEWS)

    for col in new_fields:
        if col not in all_fields:
            all_fields.append(col)
    for bf in BASE_FIELDS:
        if bf not in all_fields:
            all_fields.append(bf)

    existing = set(_make_key(r) for r in all_rows)

    added = 0
    for row in all_new_rows:
        key = _make_key(row)
        if key in existing:
            continue
        all_rows.append(row)
        existing.add(key)
        added += 1

    _write_csv(ALL_REVIEWS, all_fields, all_rows)
    return added


def main():
    combined_rows, combined_fields, total_src = build_all_new_since(DELTA_FILES)
    _write_csv(ALL_NEW_SINCE, combined_fields, combined_rows)

    added = merge_into_all_reviews(combined_rows, combined_fields)

    print("[OK] Объединение дельт завершено.")
    print(f"  Источниковых строк всего: {total_src}")
    print(f"  Уникальных в all_new_since: {len(combined_rows)}")
    print(f"  Добавлено в all_reviews: {added}")
    print(f"  all_new_since: {ALL_NEW_SINCE}")
    print(f"  all_reviews:   {ALL_REVIEWS} (включая заголовок: {len(combined_rows) + 1 if not ALL_REVIEWS.exists() else 'см. файл'})")


if __name__ == "__main__":
    main()
