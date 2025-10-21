import re
import time
import csv
import calendar
import unicodedata
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Set, Tuple

import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import os, sys, platform
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

if platform.system() == "Windows":
    DRIVER_PATH = "Drivers/Windows/yandexdriver.exe"
else:
    DRIVER_PATH = "Drivers/MacOS/yandexdriver"

URLS_FILE      = "Urls/gmaps_urls.txt"

ALL_REVIEWS_CSV      = "Csv/Reviews/all_reviews.csv"
SUMMARY_BASE_CSV     = "Csv/Summary/gmaps_summary.csv"
OUT_CSV_REV_DELTA    = "Csv/Reviews/NewReviews/gmaps_new_since.csv"
OUT_CSV_SUMMARY_NEW  = "Csv/Summary/NewSummary/gmaps_summary_new.csv"

FIRST_WAIT     = 12
SHORT_WAIT     = 2
SCROLL_PAUSE   = 0.6
SCROLL_HARD_LIMIT = 600
PLATFORM       = "Google Maps"

def find_yandex_browser() -> Optional[Path]:

    env = os.environ.get("YANDEX_BROWSER_PATH")
    if env and Path(env).is_file():
        return Path(env)

    if platform.system() == "Windows":
        home_candidate = Path.home() / "AppData" / "Local" / "Yandex" / "YandexBrowser" / "Application" / "browser.exe"

        candidates = [
            home_candidate,
            Path(os.environ.get("LOCALAPPDATA", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None
    
    else:
        p = Path("/Applications/Yandex.app/Contents/MacOS/Yandex")
        return p if p.is_file() else None
    

yb = find_yandex_browser()

def normalize_org(name: str) -> str:
    if not name:
        return ""
    s = unquote(name).replace("+", " ")
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[«»\"'”“„‚,]", " ", s)
    s = " ".join(s.split())
    return s

ORG_LABEL = "avtolotsman"
ORG_KEY   = normalize_org(ORG_LABEL)

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

RATING_BIG_CSS  = "div.fontDisplayLarge"
COUNT_SMALL_CSS = "div.fontBodySmall"

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
_EN_UNITS = {
    'second': 'seconds', 'sec': 'seconds',
    'minute': 'minutes', 'min': 'minutes',
    'hour': 'hours', 'hr': 'hours',
    'day': 'days',
    'week': 'weeks', 'wk': 'weeks',
    'month': 'months',
    'year': 'years', 'yr': 'years'
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

def normalize_relative(text: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    if not text: return None
    s = (text or "").strip().lower()
    now = now or datetime.now()

    if s.startswith(('сегодня', 'today')): return now.date().isoformat()
    if s.startswith(('вчера', 'yesterday')): return (now - timedelta(days=1)).date().isoformat()
    if 'позавчера' in s: return (now - timedelta(days=2)).date().isoformat()
    if 'только что' in s or 'just now' in s or 'сейчас' in s: return now.date().isoformat()

    singular_ru = {
        'неделю назад': ('weeks', 1),
        'месяц назад':  ('months', 1),
        'год назад':    ('years', 1),
        'день назад':   ('days', 1),
        'час назад':    ('hours', 1),
        'минуту назад': ('minutes', 1),
        'секунду назад':('seconds', 1),
    }
    for k,(u,v) in singular_ru.items():
        if k in s:
            return _apply_delta(now, u, v).date().isoformat()

    singular_en = {
        'a week ago': ('weeks', 1),
        'a month ago': ('months', 1),
        'a year ago': ('years', 1),
        'a day ago': ('days', 1),
        'an hour ago': ('hours', 1),
        'a minute ago': ('minutes', 1),
        'a second ago': ('seconds', 1),
    }
    for k,(u,v) in singular_en.items():
        if k in s:
            return _apply_delta(now, u, v).date().isoformat()

    if 'назад' in s:
        m = re.search(r'(\d+)\s+([^\s]+)', s)
        if m:
            n = int(m.group(1)); word = m.group(2); unit = None
            for key, base in _RU_UNITS.items():
                if word.startswith(key):
                    unit = base; break
            if unit:
                return _apply_delta(now, unit, n).date().isoformat()

    if 'ago' in s:
        m = re.search(r'(\d+)\s+([a-z]+)', s)
        if m:
            n = int(m.group(1)); word = m.group(2); unit = None
            for key, base in _EN_UNITS.items():
                if word.startswith(key):
                    unit = base; break
            if unit:
                return _apply_delta(now, unit, n).date().isoformat()

    m = re.search(r'([a-z]+)\s+(\d{4})', s, re.I)
    if m:
        try:
            dt = datetime.strptime(m.group(0).title(), "%B %Y")
            return dt.date().replace(day=1).isoformat()
        except Exception:
            pass
    return None

def norm_text(s: str) -> str:
    if not s:
        return ""
    x = unicodedata.normalize("NFKC", s).lower()
    x = re.sub(r"\s+", " ", x).strip()
    x = re.sub(r"[«»\"'”“„‚…‐-–—\-–—·•/\\\(\)\[\]\{\},.;:!?]", "", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x

def norm_author(s: str) -> str:
    if not s:
        return ""
    x = unicodedata.normalize("NFKC", s).lower()
    x = re.sub(r"\s+", " ", x).strip()
    return x

def text_signature(s: str, length: int = 180) -> str:
    """Сигнатура для дедупликации по тексту (усечённый нормализованный текст)."""
    n = norm_text(s)
    return n[:length]

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
    xps = [
        "//button[contains(., 'Принять')]",
        "//button[contains(., 'Accept')]",
        "//*[contains(@aria-label, 'Принять') or contains(@aria-label, 'Accept')]",
    ]
    for xp in xps:
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

def extract_summary_gmaps(drv) -> tuple:
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


def _int_from_any(x) -> Optional[int]:
    if x is None:
        return None
    s = str(x).replace("\u202f", " ").replace("\xa0", " ").strip()
    m = re.search(r"(\d[\d\s]*)", s)
    if not m:
        return None
    try:
        return int(m.group(1).replace(" ", ""))
    except Exception:
        return None

def load_prev_reviews_count(summary_csv: str, platform: str) -> Dict[str, int]:
    res: Dict[str, int] = {}
    p = Path(summary_csv)
    if not p.exists():
        return res
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                if (row.get("platform") or "").strip() != platform:
                    continue
                org_key = normalize_org((row.get("organization") or "").strip())
                if not org_key:
                    continue
                rc = _int_from_any(row.get("reviews_count"))
                if rc is None:
                    continue
                res[org_key] = rc
            except Exception:
                continue
    return res


def _try_parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = s.strip()[:10]
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        pass
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except Exception:
            return None
    return None

def load_latest_dates_by_org(all_reviews_csv: str, platform: str) -> Dict[str, date]:
    latest: Dict[str, date] = {}
    p = Path(all_reviews_csv)
    if not p.exists():
        return latest
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        date_cols = ["date_iso", "dateISO", "date", "Date", "DATE"]
        for row in reader:
            try:
                if (row.get("platform") or "").strip() != platform:
                    continue
                org_key = normalize_org((row.get("organization") or "").strip())
                if not org_key:
                    continue
                d_str = next((row[c] for c in date_cols if c in row and row[c]), None)
                d = _try_parse_date(d_str)
                if not d:
                    continue
                prev = latest.get(org_key)
                if prev is None or d > prev:
                    latest[org_key] = d
            except Exception:
                continue
    return latest


def load_existing_review_keys(all_reviews_csv: str, platform: str) -> Dict[str, Set[Tuple[str, str]]]:
    """
    Возвращает {normalized_org: set((author_norm, text_sig), ...)} из all_reviews.csv
    Ожидаемые поля: platform, organization, author, text.
    """
    res: Dict[str, Set[Tuple[str, str]]] = {}
    p = Path(all_reviews_csv)
    if not p.exists():
        return res
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                if (row.get("platform") or "").strip() != platform:
                    continue
                org_key = normalize_org((row.get("organization") or "").strip())
                if not org_key:
                    continue
                author = norm_author(row.get("author") or "")
                text_sig = text_signature(row.get("text") or "")
                if not text_sig:
                    continue
                res.setdefault(org_key, set()).add((author, text_sig))
            except Exception:
                continue
    return res


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


def extract_card_fields(c):
    for b in c.find_elements(By.CSS_SELECTOR, EXPAND_BTN_CSS):
        try:
            if b.is_displayed() and b.is_enabled():
                b.click(); time.sleep(0.02)
        except Exception:
            pass

    author = ""
    try:
        author = c.find_element(By.CSS_SELECTOR, AUTHOR_CSS).text.strip()
    except Exception:
        pass

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
        date_iso = normalize_relative(date_text)
    except Exception:
        pass

    text = ""
    try:
        texts = [t.text.strip() for t in c.find_elements(By.CSS_SELECTOR, TEXT_CSS) if t.text.strip()]
        if texts:
            text = max(texts, key=len)
    except Exception:
        pass

    return {"rating": rating, "author": author, "date_text": date_text, "date_iso": date_iso, "text": text}


def collect_delta_gmaps(
    drv,
    container,
    threshold: date,
    w_rev,
    organization: str,
    existing_keys_for_org: Set[Tuple[str, str]]
) -> int:
    """
    Скроллит контейнер отзывов. Пишет в CSV только отзывы с датой > threshold.
    Перед записью проверяет, нет ли уже такого отзыва в all_reviews (author_norm + text_signature).
    Останавливается, как только встретит отзыв с датой <= threshold.
    Возвращает: сколько новых (записанных) отзывов.
    """
    seen_recent_keys = set()
    written = 0

    last_h = -1
    same_h_iters = 0
    rounds = 0
    stop = False

    while not stop and rounds < SCROLL_HARD_LIMIT:
        rounds += 1

        for b in container.find_elements(By.CSS_SELECTOR, EXPAND_BTN_CSS):
            try:
                if b.is_displayed() and b.is_enabled():
                    b.click()
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

            d_iso = item.get("date_iso")
            if not d_iso:
                continue
            try:
                d = datetime.fromisoformat(d_iso[:10]).date()
            except Exception:
                continue

            if d <= threshold:
                stop = True
                break

            key_recent = (item.get("author") or "", item.get("date_text") or "", txt[:120])
            if key_recent in seen_recent_keys:
                continue
            seen_recent_keys.add(key_recent)

            a_norm = norm_author(item.get("author") or "")
            t_sig  = text_signature(txt)
            if (a_norm, t_sig) in existing_keys_for_org:
                continue

            w_rev.writerow({
                "rating":       item.get("rating"),
                "author":       (item.get("author") or "").strip(),
                "date_iso":     d.isoformat(),
                "text":         txt.replace("\r", " ").replace("\n", " ").strip(),
                "platform":     PLATFORM,
                "organization": organization,
            })
            written += 1

            existing_keys_for_org.add((a_norm, t_sig))

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

    return written

def main():
    latest_by_org = load_latest_dates_by_org(ALL_REVIEWS_CSV, PLATFORM)
    if latest_by_org:
        print(f"[INFO] Найдены последние даты по {len(latest_by_org)} организациям в '{ALL_REVIEWS_CSV}'.")
    else:
        print(f"[INFO] '{ALL_REVIEWS_CSV}' не найден или пуст — будем собирать всё, что есть (порог = 2 года).")

    existing_keys = load_existing_review_keys(ALL_REVIEWS_CSV, PLATFORM)
    print(f"[INFO] Загружены ключи для дедупликации из '{ALL_REVIEWS_CSV}': "
          f"{sum(len(v) for v in existing_keys.values())} штук (по всем организациям).")

    prev_counts = load_prev_reviews_count(SUMMARY_BASE_CSV, PLATFORM)
    prev_count = prev_counts.get(ORG_KEY, 0)
    print(f"[INFO] Старый reviews_count из '{SUMMARY_BASE_CSV}' для '{ORG_LABEL}': {prev_count}")

    opts = Options()
    opts.binary_location = str(yb)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    drv = webdriver.Chrome(service=Service(DRIVER_PATH), options=opts)

    try:
        urls = [u.strip() for u in Path(URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
    except FileNotFoundError:
        urls = []

    Path(OUT_CSV_REV_DELTA).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_CSV_SUMMARY_NEW).parent.mkdir(parents=True, exist_ok=True)
    f_rev = open(OUT_CSV_REV_DELTA, "w", newline="", encoding="utf-8")
    f_sum = open(OUT_CSV_SUMMARY_NEW, "w", newline="", encoding="utf-8")
    w_rev = csv.DictWriter(f_rev, fieldnames=["rating","author","date_iso","text","platform","organization"], quoting=csv.QUOTE_ALL)
    w_sum = csv.DictWriter(f_sum, fieldnames=["organization","platform","rating_avg","ratings_count","reviews_count"], quoting=csv.QUOTE_ALL)
    w_rev.writeheader()
    w_sum.writeheader()

    total_written = 0
    last_rating_avg, last_ratings_count = None, None

    try:
        for i, base in enumerate(urls, 1):
            url = add_hl_ru(base)
            print(f"[{i}/{len(urls)}] {url}")

            drv.get(url)
            time.sleep(FIRST_WAIT if i == 1 else SHORT_WAIT)
            accept_cookies_if_any(drv)

            click_all_reviews(drv)
            time.sleep(1.0)

            set_sort_newest(drv)
            time.sleep(0.6)

            rating_avg, ratings_count = extract_summary_gmaps(drv)
            last_rating_avg, last_ratings_count = rating_avg, ratings_count

            container = find_reviews_container(drv)
            if not container:
                click_all_reviews(drv)
                container = find_reviews_container(drv)
                if not container:
                    print("  [WARN] не найден контейнер отзывов, пропускаю")
                    continue

            cutoff_default = date.today() - timedelta(days=365 * 2 + 10)
            threshold = latest_by_org.get(ORG_KEY, cutoff_default)
            print(f"  Организация: {ORG_LABEL} | Пороговая дата (последняя в all_reviews): {threshold.isoformat()}")

            existing_keys_for_org = existing_keys.get(ORG_KEY, set())

            written = collect_delta_gmaps(drv, container, threshold, w_rev, ORG_LABEL, existing_keys_for_org)
            total_written += written
            print(f"  новых отзывов записано (после дедупа): {written}")

            existing_keys[ORG_KEY] = existing_keys_for_org

        new_reviews_count = max(0, prev_count) + max(0, total_written)
        w_sum.writerow({
            "organization": ORG_LABEL,
            "platform":     PLATFORM,
            "rating_avg":   last_rating_avg if last_rating_avg is not None else "",
            "ratings_count":last_ratings_count if last_ratings_count is not None else "",
            "reviews_count": new_reviews_count,
        })
        print(f"[INFO] Итоговый reviews_count для summary: {new_reviews_count} "
              f"(старое={prev_count} + новые={total_written})")

    finally:
        try: drv.quit()
        except Exception: pass
        f_rev.close()
        f_sum.close()

    print(f"\nГотово.")
    print(f"Отзывы (Google) -> {OUT_CSV_REV_DELTA}")
    print(f"Summary (новый, Google) -> {OUT_CSV_SUMMARY_NEW}")
    print(f"Базовое summary (для старого счётчика) -> {SUMMARY_BASE_CSV}")


if __name__ == "__main__":
    main()
