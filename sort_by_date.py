import pandas as pd

df = pd.read_csv("gmaps_reviews.csv", encoding="utf-8")  # pandas корректно читает многострочный столбец text
# гарантируем тип datetime (поддержит и YYYY-MM-DD, и ISO с временем/таймзоной)
df["date_iso"] = pd.to_datetime(df["date_iso"], errors="coerce", utc=True)

df = df.sort_values("date_iso", ascending=False, na_position="last")  # последние сверху
df.to_csv("gmaps_reviews.csv", index=False, encoding="utf-8")
