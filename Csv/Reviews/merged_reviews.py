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
                print(f"⚠️ missed: {p} (no file)")
                continue

            try:
                with path.open("r", encoding="utf-8-sig", newline="") as fin:
                    rdr = csv.reader(fin)
                    try:
                        file_header = next(rdr)
                    except StopIteration:
                        print(f"⚠️ empty file: {p}")
                        continue

                    if writer is None:
                        header = file_header
                        writer = csv.writer(fout, quoting=csv.QUOTE_ALL)
                        writer.writerow(header)
                    else:
                        if file_header != header:
                            print(f"⚠️ {p} has a different header, lines will be skipped")
                            continue

                    file_rows = 0
                    for row in rdr:
                        try:
                            writer.writerow(row)
                            rows_written += 1
                            file_rows += 1
                        except Exception as e:
                            print(f"⚠️ line missing in {p}: {e}")
                            continue

                    files_merged += 1
                    print(f"✓ {p}: added {file_rows} lines")
            except Exception as e:
                print(f"⚠️ could not be read {p}: {e}")
                continue

    print(f"Done -> {OUT} | files merged: {files_merged}, lines written: {rows_written}")

if __name__ == "__main__":
    main()
