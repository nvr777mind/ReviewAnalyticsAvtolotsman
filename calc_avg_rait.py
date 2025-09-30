# calc_avg_rait.py
# Считает средний рейтинг из CSV (по умолчанию gmaps_reviews.csv)

import csv
import argparse
from collections import defaultdict

def parse_float(s: str):
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None

def truthy(s: str) -> bool:
    if s is None:
        return False
    return str(s).strip().lower() in {"true", "1", "yes", "y", "да"}

def load_rows(path, only_with_text=False, place_filter=None):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rating = parse_float(row.get("rating"))
            if rating is None:
                continue

            if only_with_text:
                if not truthy(row.get("have_text")) and not (row.get("text") or "").strip():
                    continue

            if place_filter:
                title = (row.get("place_title") or "").lower()
                if place_filter.lower() not in title:
                    continue

            # ВАЖНО: сначала исходная строка, ПОТОМ наше числовое поле rating,
            # чтобы строковый rating из CSV не перезаписал число.
            rows.append({**row, "rating": rating})
    return rows

def compute_avg(values):
    if not values:
        return 0.0, 0
    # На всякий случай ещё раз приведём к float (если где-то просочилось строкой)
    vals = []
    for v in values:
        if isinstance(v, (int, float)):
            vals.append(float(v))
        else:
            fv = parse_float(v)
            if fv is not None:
                vals.append(fv)
    if not vals:
        return 0.0, 0
    s = sum(vals)
    n = len(vals)
    return s / n, n

def main():
    ap = argparse.ArgumentParser(description="Средний рейтинг из CSV с отзывами Google Maps.")
    ap.add_argument("--file", default="gmaps_reviews.csv", help="Путь к CSV (по умолчанию gmaps_reviews.csv)")
    ap.add_argument("--only-with-text", action="store_true", help="Считать только отзывы с текстом (have_text=true)")
    ap.add_argument("--place", default=None, help="Фильтр по подстроке в place_title (без учета регистра)")
    ap.add_argument("--per-place", action="store_true", help="Вывести средний рейтинг по каждому place_title")
    args = ap.parse_args()

    try:
        rows = load_rows(args.file, only_with_text=args.only_with_text, place_filter=args.place)
    except FileNotFoundError:
        print(f"Файл не найден: {args.file}")
        return

    if not rows:
        print("Нет подходящих строк (проверьте путь к файлу и фильтры).")
        return

    if args.per_place:
        buckets = defaultdict(list)
        for r in rows:
            buckets[r.get("place_title","")].append(r["rating"])
        print("Средний рейтинг по местам:")
        for place, vals in sorted(buckets.items(), key=lambda x: x[0] or ""):
            avg, n = compute_avg(vals)
            place_name = place if place else "(без названия)"
            print(f"- {place_name}: {avg:.2f} (n={n})")
    
    else:
        avg, n = compute_avg([r["rating"] for r in rows])
        print(f"Средний рейтинг: {avg:.2f} (n={n})")
        try:
            mn = min(float(r["rating"]) for r in rows)
            mx = max(float(r["rating"]) for r in rows)
            print(f"Мин/Макс: {mn:.2f} / {mx:.2f}")
        except Exception:
            pass

if __name__ == "__main__":
    main()
