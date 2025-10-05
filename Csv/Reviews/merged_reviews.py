# merged_reviews.py
# просто объединяет несколько CSV с одинаковым хедером в один

import csv
from pathlib import Path

INPUTS = [
    "Csv/2gis_reviews.csv",
    "Csv/gmaps_reviews.csv",
    "Csv/yamaps_reviews.csv",
]
OUT = "Csv/all_reviews.csv"

def main():
    out_path = Path(OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    writer = None
    header = None

    with out_path.open("w", encoding="utf-8", newline="") as fout:
        for i, p in enumerate(INPUTS, 1):
            path = Path(p)
            if not path.is_file():
                print(f"⚠️  пропущен: {p} (нет файла)")
                continue

            # читаем с utf-8-sig, чтобы спокойно проглотить возможный BOM
            with path.open("r", encoding="utf-8-sig", newline="") as fin:
                rdr = csv.reader(fin)
                try:
                    file_header = next(rdr)
                except StopIteration:
                    print(f"⚠️  пустой файл: {p}")
                    continue

                if writer is None:
                    header = file_header
                    writer = csv.writer(fout, quoting=csv.QUOTE_ALL)
                    writer.writerow(header)
                else:
                    # проверка, что структура совпадает
                    if file_header != header:
                        print(f"⚠️  у {p} другой заголовок, строки будут пропущены")
                        continue

                for row in rdr:
                    writer.writerow(row)

    print(f"Готово → {OUT}")

if __name__ == "__main__":
    main()
