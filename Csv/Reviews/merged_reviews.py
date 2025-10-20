import csv
import sys, io
import platform
from pathlib import Path

if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

INPUTS = [
    "Csv/Reviews/2gis_reviews.csv",
    "Csv/Reviews/gmaps_reviews.csv",
    "Csv/Reviews/yamaps_reviews.csv",
]
OUT = "Csv/Reviews/all_reviews.csv"

def main():
    out_path = Path(OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    writer = None
    header = None
    files_merged = 0
    rows_written = 0

    with out_path.open("w", encoding="utf-8-sig", newline="") as fout:
        for i, p in enumerate(INPUTS, 1):
            path = Path(p)
            if not path.is_file():
                print(f"⚠️ пропущен: {p} (нет файла)")
                continue

            try:
                with path.open("r", encoding="utf-8-sig", newline="") as fin:
                    rdr = csv.reader(fin)
                    try:
                        file_header = next(rdr)
                    except StopIteration:
                        print(f"⚠️ пустой файл: {p}")
                        continue

                    if writer is None:
                        header = file_header
                        writer = csv.writer(fout, quoting=csv.QUOTE_ALL)
                        writer.writerow(header)
                    else:
                        if file_header != header:
                            print(f"⚠️ у {p} другой заголовок, строки будут пропущены")
                            continue

                    file_rows = 0
                    for row in rdr:
                        try:
                            writer.writerow(row)
                            rows_written += 1
                            file_rows += 1
                        except Exception as e:
                            print(f"⚠️ строка пропущена в {p}: {e}")
                            continue

                    files_merged += 1
                    print(f"✓ {p}: добавлено {file_rows} строк")
            except Exception as e:
                print(f"⚠️ не удалось прочитать {p}: {e}")
                continue

    print(f"Готово -> {OUT} | файлов объединено: {files_merged}, строк записано: {rows_written}")

if __name__ == "__main__":
    main()
