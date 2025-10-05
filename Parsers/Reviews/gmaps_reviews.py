# gmaps_reviews.py — «Сначала новые», только отзывы с текстом и не старше 2 лет
# -*- coding: utf-8 -*-
import re, time, csv, calendar
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from datetime import datetime, timedelta
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchWindowException, WebDriverException, TimeoutException

# ====== НАСТРОЙКИ ======
DRIVER_PATH   = "drivers/yandexdriver"  # поменять для windows
YANDEX_BINARY = "/Applications/Yandex.app/Contents/MacOS/Yandex" # поменять для windows
URLS_FILE     = "Urls/gmaps_urls.txt"
OUT_CSV       = "Csv/gmaps_reviews.csv"

FIRST_WAIT    = 12
SHORT_WAIT    = 3
SCROLL_ROUNDS = 80      # общий лимит итераций скролла (останавливаемся раньше по дате)
SCROLL_PAUSE  = 0.6

# ====== СЕЛЕКТОРЫ ======
REVIEWS_CONTAINER_CANDIDATES = [
    "div.m6QErb.DxyBCb",
    "div.m6QErb.XiKgde",
    "div[aria-label*='Отзывы']",
    "div[aria-label*='Reviews']",
]
REVIEW_CARD_CSS      = "div.jftiEf.fontBodyMedium"
REVIEW_CARD_FALLBACK = "div.jftiEf"
AUTHOR_CSS           = ".d4r55.fontTitleMedium"
RATING_CSS           = ".kvMYJc"
DATE_CSS             = ".rsqaWe"
TEXT_CSS             = ".wiI7pd"
EXPAND_BTN_CSS       = "button.w8nwRe.kyuRq"

# ====== ДАТЫ ======
def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def _subtract_months(dt: datetime, months: int) -> datetime:
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(dt.day, _last_day_of_month(year, month))
    return dt.replace(year=year, month=month, day=day)

def _subtract_years(dt: datetime, years: int) -> datetime:
    year = dt.year - years
    month = dt.month
    day = min(dt.day, _last_day_of_month(year, month))
    return dt.replace(year=year, month=month, day=day)

_RU_UNITS = {
    'сек': 'seconds', 'секун': 'seconds',
    'мин': 'minutes', 'минут': 'minutes', 'мину': 'minutes',
    'час': 'hours', 'часа': 'hours', 'часов': 'hours',
    'день': 'days', 'дня': 'days', 'дней': 'days', 'сут': 'days',
    'недел': 'weeks', 'нед': 'weeks',
    'месяц': 'months', 'месяца': 'months', 'месяцев': 'months',
    'год': 'years', 'года': 'years', 'лет': 'years'
}

def _apply_delta(now: datetime, unit: str, n: int) -> datetime:
    if unit == 'seconds': return now - timedelta(seconds=n)
    if unit == 'minutes': return now - timedelta(minutes=n)
    if unit == 'hours':   return now - timedelta(hours=n)
    if unit == 'days':    return now - timedelta(days=n)
    if unit == 'weeks':   return now - timedelta(weeks=n)
    if unit == 'months':  return _subtract_months(now, n)
    if unit == 'years':   return _subtract_years(now, n)
    return now

def normalize_relative_ru(text: str, now: Optional[datetime] = None) -> Optional[str]:
    """
    '4 года назад' -> 'YYYY-MM-DD'
    '2 недели назад' -> 'YYYY-MM-DD'
    'месяц назад', 'год назад', 'вчера', 'сегодня' — поддерживаются.
    """
    if not text: return None
    s = text.strip().lower()
    now = now or datetime.now()
    if s.startswith('сегодня'):   return now.date().isoformat()
    if s.startswith('вчера'):     return (now - timedelta(days=1)).date().isoformat()
    if s.startswith('позавчера'): return (now - timedelta(days=2)).date().isoformat()
    if 'только что' in s or 'сейчас' in s: return now.date().isoformat()

    singular_map = {
        'неделю назад': ('weeks', 1),
        'месяц назад':  ('months', 1),
        'год назад':    ('years', 1),
        'день назад':   ('days', 1),
        'час назад':    ('hours', 1),
        'минуту назад': ('minutes', 1),
        'секунду назад':('seconds', 1),
    }
    for key, (unit, val) in singular_map.items():
        if key in s:
            return (_apply_delta(now, unit, val)).date().isoformat()

    m = re.search(r'(\d+)\s+([^\s]+)', s)
    if m and 'назад' in s:
        n = int(m.group(1)); unit_word = m.group(2); unit = None
        for key, base in _RU_UNITS.items():
            if unit_word.startswith(key): unit = base; break
        if unit is None: return None
        return (_apply_delta(now, unit, n)).date().isoformat()
    return None

# ====== УТИЛЫ ======
def add_hl_ru(url: str) -> str:
    """Принудительно включаем русскую локаль у Google Maps."""
    try:
        u = urlparse(url); q = parse_qs(u.query); q["hl"] = ["ru"]
        return urlunparse((u.scheme, u.netloc, u.path, u.params,
                           urlencode({k:(v[0] if isinstance(v,list) else v) for k,v in q.items()}),
                           u.fragment))
    except Exception:
        return url

def parse_rating(raw: str):
    if not raw: return None
    m = re.search(r'([0-5](?:[.,]\d)?)', raw)
    return float(m.group(1).replace(',', '.')) if m else None

def click_all_reviews(drv):
    XPATHS = [
        "//button[contains(., 'Все отзывы')]", "//a[contains(., 'Все отзывы')]",
        "//button[contains(., 'Отзывы')]",     "//a[contains(., 'Отзывы')]",
        "//button[contains(., 'All reviews')]", "//a[contains(., 'All reviews')]",
    ]
    for xp in XPATHS:
        try:
            WebDriverWait(drv, 4).until(EC.element_to_be_clickable((By.XPATH, xp))).click()
            return True
        except Exception:
            pass
    return False

def accept_cookies_if_any(drv):
    for xp in ["//button[contains(., 'Принять')]",
               "//button[contains(., 'Accept')]",
               "//*[contains(@aria-label, 'Принять') or contains(@aria-label, 'Accept')]"]:
        try:
            WebDriverWait(drv, 2).until(EC.element_to_be_clickable((By.XPATH, xp))).click()
            return
        except Exception:
            pass

def find_reviews_container(drv):
    for css in REVIEWS_CONTAINER_CANDIDATES:
        try:
            el = WebDriverWait(drv, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
            return el
        except Exception:
            continue
    return None

def safe_scroll_js(drv, script, *args):
    try:
        drv.execute_script(script, *args); return True
    except (NoSuchWindowException, WebDriverException):
        return False

def set_sort_newest(drv, attempts: int = 3) -> bool:
    """
    Открывает меню сортировки и выбирает «Сначала новые».
    Возвращает True, если получилось (aria-label у кнопки сменился).
    """
    def _open_menu():
        btn_xpaths = [
            "//button[@aria-label='Самые релевантные']",
            "//button[@aria-label='Most relevant']",
            "//button[@aria-label='Сначала новые']",
            "//button[@aria-label='Newest']",
            "//button[contains(@jsaction,'pane.wfvdle654')]",
        ]
        for xp in btn_xpaths:
            try:
                btn = WebDriverWait(drv, 4).until(EC.element_to_be_clickable((By.XPATH, xp)))
                drv.execute_script("arguments[0].click();", btn)
                return btn
            except Exception:
                pass
        return None

    def _wait_menu():
        return WebDriverWait(drv, 4).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='menu' or @role='listbox']"))
        )

    def _pick_item(menu):
        candidates = menu.find_elements(
            By.XPATH,
            ".//*[self::div or self::span][normalize-space(text())='Сначала новые' or normalize-space(text())='Newest']"
        )
        if not candidates:
            return False
        target = candidates[0]
        try:
            drv.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", target)
        except Exception:
            pass
        clickable = drv.execute_script("""
            let el = arguments[0];
            function hasRole(e){ const r=(e.getAttribute&&e.getAttribute('role'))||''; 
                                 return /menuitem|option/i.test(r); }
            while (el && !hasRole(el) && el.tagName.toLowerCase()!=='button') el = el.parentElement;
            return el || arguments[0];
        """, target)
        try:
            drv.execute_script("arguments[0].click();", clickable)
        except Exception:
            try:
                ActionChains(drv).move_to_element(clickable).pause(0.05).click().perform()
            except Exception:
                return False
        return True

    def _is_newest_selected():
        try:
            WebDriverWait(drv, 4).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//button[@aria-label='Сначала новые' or @aria-label='Newest']")
                )
            )
            return True
        except Exception:
            return False

    for _ in range(attempts):
        btn = _open_menu()
        if not btn:
            continue
        try:
            menu = _wait_menu()
        except Exception:
            continue
        if not _pick_item(menu):
            try:
                drv.execute_script("arguments[0].click();", btn)
            except Exception:
                pass
            continue
        if _is_newest_selected():
            return True
    return False

# ====== Парс одной карточки ======
def extract_card_fields(c):
    # раскрыть «Ещё» в карточке
    for b in c.find_elements(By.CSS_SELECTOR, EXPAND_BTN_CSS):
        try:
            if b.is_displayed() and b.is_enabled():
                b.click(); time.sleep(0.02)
        except Exception:
            pass

    author = ""
    try: author = c.find_element(By.CSS_SELECTOR, AUTHOR_CSS).text.strip()
    except Exception: pass

    rating = None
    try:
        r = c.find_element(By.CSS_SELECTOR, RATING_CSS)
        rating = parse_rating((r.get_attribute("aria-label") or r.text or r.get_attribute("title") or ""))
    except Exception:
        pass
    if rating is None:
        try:
            r2 = c.find_element(By.CSS_SELECTOR, "span[aria-label*='из 5'], span[aria-label*='out of 5']")
            rating = parse_rating(r2.get_attribute("aria-label"))
        except Exception:
            pass

    date_text, date_iso = "", None
    try:
        date_text = c.find_element(By.CSS_SELECTOR, DATE_CSS).text.strip()
        date_iso = normalize_relative_ru(date_text)
    except Exception:
        pass

    text = ""
    try:
        texts = [t.text.strip() for t in c.find_elements(By.CSS_SELECTOR, TEXT_CSS) if t.text.strip()]
        if texts: text = max(texts, key=len)
    except Exception:
        pass

    return {
        "rating": rating,
        "author": author,
        "date_text": date_text,
        "date_iso": date_iso,
        "text": text,
    }

# ====== Инкрементальный сбор с остановкой по сроку ======
def harvest_reviews_newest(drv, container, cutoff_date):
    seen = set()
    rows = []

    for _ in range(SCROLL_ROUNDS):
        # раскрываем «Ещё» для всех видимых
        for b in container.find_elements(By.CSS_SELECTOR, EXPAND_BTN_CSS):
            try:
                if b.is_displayed() and b.is_enabled(): b.click()
            except Exception:
                pass

        cards = container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_CSS)
        if not cards:
            cards = container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_FALLBACK)

        stop = False
        for c in cards:
            try:
                item = extract_card_fields(c)
            except Exception:
                continue

            # только отзывы с текстом
            if not item.get("text"):
                continue

            # дата обязателна и должна быть не старше cutoff
            d_iso = item.get("date_iso")
            if not d_iso:
                continue
            try:
                d = datetime.fromisoformat(d_iso[:10]).date()
            except Exception:
                continue

            if d < cutoff_date:
                stop = True
                break

            key = (item.get("author"), item.get("date_text"), (item.get("text") or "")[:80])
            if key in seen:
                continue
            seen.add(key)

            rows.append({
                "rating":    item.get("rating"),
                "author":    item.get("author") or "",
                "date_text": item.get("date_text") or "",
                "date_iso":  d.isoformat(),
                "text":      item.get("text") or "",
                "platform":  "Google Maps",
            })

        if stop:
            break

        # прокручиваем контейнер вниз
        if not safe_scroll_js(
            drv,
            "arguments[0].scrollTop = Math.min(arguments[0].scrollTop + arguments[0].clientHeight*0.95, arguments[0].scrollHeight);",
            container
        ):
            break
        time.sleep(SCROLL_PAUSE)

    return rows

# ====== MAIN ======
def main():
    # браузер: YandexBrowser + yandexdriver
    opts = Options()
    opts.binary_location = YANDEX_BINARY
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    drv = webdriver.Chrome(service=Service(DRIVER_PATH), options=opts)

    try:
        caps = drv.capabilities
        print("Browser:", caps.get("browserName"), "Version:", caps.get("browserVersion"))
    except Exception:
        pass

    try:
        urls = [u.strip() for u in Path(URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
    except FileNotFoundError:
        urls = []

    all_rows = []
    cutoff_date = (datetime.now().date() - timedelta(days=365*3))  # 2 года назад

    try:
        for i, base in enumerate(urls, 1):
            url = add_hl_ru(base)
            print(f"[{i}/{len(urls)}] {url}")

            drv.get(url)
            time.sleep(FIRST_WAIT if i == 1 else SHORT_WAIT)

            accept_cookies_if_any(drv)
            click_all_reviews(drv)
            time.sleep(1.5)

            # ставим «Сначала новые»
            set_sort_newest(drv)
            time.sleep(0.8)

            container = find_reviews_container(drv)
            if not container:
                # запасной контейнер
                container = drv.find_element(By.TAG_NAME, "body")

            rows = harvest_reviews_newest(drv, container, cutoff_date)
            print(f"  собрано отзывов (<=2 года): {len(rows)}")
            all_rows.extend(rows)
    finally:
        try: drv.quit()
        except Exception: pass

    # --- запись CSV: всё в кавычках, без have_text
    Path(OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["rating","author","date_iso","text","platform","organization"]
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in all_rows:
            w.writerow({
                "rating":    r.get("rating"),
                "author":    (r.get("author") or "").strip(),
                "date_iso":  (r.get("date_iso") or "")[:10],
                "text":      (r.get("text") or "").replace("\r", " ").replace("\n", " ").strip(),
                "platform":  "Google Maps",
                "organization": "avtolotsman"
            })

    print(f"Готово. Всего строк: {len(all_rows)}. Файл: {OUT_CSV}")

if __name__ == "__main__":
    main()
