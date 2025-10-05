# gmaps_reviews.py — один проход + «Сначала новые»
# -*- coding: utf-8 -*-
import re, time, csv, calendar
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote
from datetime import datetime, timedelta
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchWindowException, WebDriverException, TimeoutException

# ====== НАСТРОЙКИ ======
DRIVER_PATH    = "drivers/yandexdriver"  # для Windows укажи свой путь
YANDEX_BINARY  = "/Applications/Yandex.app/Contents/MacOS/Yandex"
URLS_FILE      = "Urls/gmaps_urls.txt"

OUT_CSV_REV    = "Csv/Reviews/gmaps_reviews.csv"   # детальные отзывы (только с текстом и ≤ 2 лет)
OUT_CSV_SUM    = "Csv/Summary/gmaps_summary.csv"   # summary по организации

FIRST_WAIT     = 12
SHORT_WAIT     = 2
SCROLL_PAUSE   = 0.6
SCROLL_HARD_LIMIT = 600

CUTOFF_YEARS   = 3
PLATFORM       = "Google Maps"

ORG = "avtolotsman"

# ====== СЕЛЕКТОРЫ ======
REVIEWS_CONTAINER_CANDIDATES = [
    "div.m6QErb.DxyBCb",
    "div.m6QErb.XiKgde",
    "div[aria-label*='Отзывы']",
    "div[aria-label*='Reviews']",
]
REVIEW_CARD_CSS      = "div.jftiEf.fontBodyMedium"
REVIEW_CARD_FALLBACK = "div.jftiEf"

AUTHOR_CSS = ".d4r55.fontTitleMedium"
RATING_CSS = ".kvMYJc"
DATE_CSS   = ".rsqaWe"
TEXT_CSS   = ".wiI7pd"
EXPAND_BTN_CSS = "button.w8nwRe.kyuRq"

# summary
RATING_BIG_CSS  = "div.fontDisplayLarge"         # 4,4
COUNT_SMALL_CSS = "div.fontBodySmall"            # Отзывов: 522

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

def accept_cookies_if_any(drv):
    for xp in ["//button[contains(., 'Принять')]",
               "//button[contains(., 'Accept')]",
               "//*[contains(@aria-label, 'Принять') or contains(@aria-label, 'Accept')]"]:
        try:
            WebDriverWait(drv, 2).until(EC.element_to_be_clickable((By.XPATH, xp))).click()
            return
        except Exception:
            pass

def click_all_reviews(drv):
    XPATHS = [
        "//button[contains(., 'Все отзывы')]", "//a[contains(., 'Все отзывы')]",
        "//button[contains(., 'Отзывы')]",     "//a[contains(., 'Отзывы')]",
        "//button[contains(., 'All reviews')]", "//a[contains(., 'All reviews')]",
    ]
    for xp in XPATHS:
        try:
            WebDriverWait(drv, 6).until(EC.element_to_be_clickable((By.XPATH, xp))).click()
            return True
        except Exception:
            pass
    return False

def find_reviews_container(drv):
    for css in REVIEWS_CONTAINER_CANDIDATES:
        try:
            el = WebDriverWait(drv, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
            return el
        except Exception:
            continue
    return None

def safe_scroll_js(drv, script, *args):
    try:
        drv.execute_script(script, *args); return True
    except (NoSuchWindowException, WebDriverException):
        return False

def organization_from_url_or_title(drv, url: str) -> str:
    try:
        path = urlparse(url).path  # /maps/place/<name>/...
        m = re.search(r"/place/([^/]+)", path)
        if m:
            slug = unquote(m.group(1)).replace("+", " ").strip()
            slug = slug.split("@", 1)[0].strip()
            return slug
    except Exception:
        pass
    try:
        title = (drv.title or "")
        title = re.split(r"– Google", title)[0].strip()
        return title
    except Exception:
        return ""

# ====== SUMMARY (rating_avg, ratings_count) ======
def extract_summary_gmaps(drv) -> tuple[Optional[float], Optional[int]]:
    rating_avg = None
    try:
        el = WebDriverWait(drv, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, RATING_BIG_CSS)))
        rating_avg = parse_rating(el.text)
    except Exception:
        try:
            for el in drv.find_elements(By.CSS_SELECTOR, RATING_BIG_CSS):
                rating_avg = parse_rating(el.text)
                if rating_avg is not None: break
        except Exception:
            pass

    ratings_count = None
    try:
        for el in drv.find_elements(By.CSS_SELECTOR, COUNT_SMALL_CSS):
            txt = (el.text or "").replace("\xa0", " ").strip()
            m = re.search(r'(Отзывов|Reviews)\s*:\s*([\d\s]+)', txt, flags=re.I)
            if m:
                try:
                    ratings_count = int(m.group(2).replace(" ", ""))
                    break
                except Exception:
                    continue
    except Exception:
        pass

    if ratings_count is None:
        try:
            html = drv.page_source
            m = re.search(r'(?:Отзывов|Reviews)\s*:\s*([\d\s]+)', html, flags=re.I)
            if m:
                ratings_count = int(m.group(1).replace("\u202f", "").replace(" ", ""))
        except Exception:
            pass

    return rating_avg, ratings_count

# ====== «Сначала новые» ======
def set_sort_newest(drv, attempts: int = 3) -> bool:
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

# ====== ЕДИНЫЙ ПРОХОД ======
def one_pass_collect(drv, container, cutoff_date, w_rev, org: str) -> int:
    seen_text_keys = set()
    seen_recent_keys = set()

    last_h = -1
    same_h_iters = 0
    rounds = 0

    while rounds < SCROLL_HARD_LIMIT:
        rounds += 1

        for b in container.find_elements(By.CSS_SELECTOR, EXPAND_BTN_CSS):
            try:
                if b.is_displayed() and b.is_enabled(): b.click()
            except Exception:
                pass

        cards = container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_CSS) \
                or container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_FALLBACK)

        for c in cards:
            try:
                item = extract_card_fields(c)
            except Exception:
                continue

            txt = (item.get("text") or "").strip()
            if not txt:
                continue

            key_all = (item.get("author") or "", txt[:120])
            if key_all not in seen_text_keys:
                seen_text_keys.add(key_all)

            d_iso = item.get("date_iso")
            if not d_iso:
                continue
            try:
                d = datetime.fromisoformat(d_iso[:10]).date()
            except Exception:
                continue

            if d >= cutoff_date:
                key_recent = (item.get("author") or "", item.get("date_text") or "", txt[:120])
                if key_recent not in seen_recent_keys:
                    seen_recent_keys.add(key_recent)
                    w_rev.writerow({
                        "rating":       item.get("rating"),
                        "author":       (item.get("author") or "").strip(),
                        "date_iso":     d.isoformat(),
                        "text":         txt.replace("\r", " ").replace("\n", " ").strip(),
                        "platform":     PLATFORM,
                        "organization": org,
                    })

        try:
            h = drv.execute_script("return arguments[0].scrollHeight;", container)
            drv.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        except Exception:
            break

        if h == last_h:
            same_h_iters += 1
        else:
            same_h_iters = 0
        last_h = h

        if same_h_iters >= 3:
            break

        time.sleep(SCROLL_PAUSE)

    return len(seen_text_keys)

# ====== MAIN ======
def main():
    opts = Options()
    opts.binary_location = YANDEX_BINARY
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    drv = webdriver.Chrome(service=Service(DRIVER_PATH), options=opts)

    try:
        print("Browser:", drv.capabilities.get("browserName"), "Version:", drv.capabilities.get("browserVersion"))
    except Exception:
        pass

    try:
        urls = [u.strip() for u in Path(URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
    except FileNotFoundError:
        urls = []

    Path(OUT_CSV_REV).parent.mkdir(parents=True, exist_ok=True)
    f_rev = open(OUT_CSV_REV, "w", newline="", encoding="utf-8")
    f_sum = open(OUT_CSV_SUM, "w", newline="", encoding="utf-8")
    w_rev = csv.DictWriter(f_rev, fieldnames=["rating","author","date_iso","text","platform","organization"], quoting=csv.QUOTE_ALL)
    w_sum = csv.DictWriter(f_sum, fieldnames=["organization","platform","rating_avg","ratings_count","reviews_count"], quoting=csv.QUOTE_ALL)
    w_rev.writeheader()
    w_sum.writeheader()

    cutoff_date = (datetime.now().date() - timedelta(days=365*CUTOFF_YEARS))

    try:
        for i, base in enumerate(urls, 1):
            url = add_hl_ru(base)
            print(f"[{i}/{len(urls)}] {url}")

            drv.get(url)
            time.sleep(FIRST_WAIT if i == 1 else SHORT_WAIT)

            accept_cookies_if_any(drv)
            click_all_reviews(drv)
            time.sleep(1.2)

            # <<< СНАЧАЛА НОВЫЕ >>>
            set_sort_newest(drv)
            time.sleep(0.6)

            rating_avg, ratings_count = extract_summary_gmaps(drv)

            container = find_reviews_container(drv)
            if not container:
                click_all_reviews(drv)
                container = find_reviews_container(drv)
                if not container:
                    print("  не найден контейнер отзывов, пропускаю")
                    w_sum.writerow({
                        "organization": ORG,
                        "platform": PLATFORM,
                        "rating_avg": rating_avg if rating_avg is not None else "",
                        "ratings_count": ratings_count if ratings_count is not None else "",
                        "reviews_count": ""
                    })
                    continue

            total_text_reviews = one_pass_collect(drv, container, cutoff_date, w_rev, ORG)

            w_sum.writerow({
                "organization": ORG,
                "platform":     PLATFORM,
                "rating_avg":   rating_avg if rating_avg is not None else "",
                "ratings_count":ratings_count if ratings_count is not None else "",
                "reviews_count":total_text_reviews,
            })

            print(f"  summary: rating={rating_avg}, оценок={ratings_count}, текстовых отзывов={total_text_reviews}")
    finally:
        try: drv.quit()
        except Exception: pass
        f_rev.close()
        f_sum.close()

    print(f"Готово. Reviews → {OUT_CSV_REV} | Summary → {OUT_CSV_SUM}")

if __name__ == "__main__":
    main()
