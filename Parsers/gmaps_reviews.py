import re, time, csv, calendar
from time import monotonic
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote
from datetime import datetime, timedelta, date
from typing import Optional, Tuple, List, Set

import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from selenium.common.exceptions import StaleElementReferenceException

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

OUT_CSV_REV    = "Csv/Reviews/gmaps_reviews.csv"
OUT_CSV_SUM    = "Csv/Summary/gmaps_summary.csv"

FIRST_WAIT     = 12
SHORT_WAIT     = 2

SCROLL_PAUSE               = 0.25
SCROLL_HARD_LIMIT          = 3000

MAX_SCROLL_SECONDS         = 180
NO_HEIGHT_GROWTH_TOLERANCE = 8
NO_CARD_GROWTH_TOLERANCE   = 12
NO_TEXT_GROWTH_TOLERANCE   = 12
PAGE_DOWN_EVERY_N          = 3
JIGGLE_EVERY_N             = 12
FOCUS_RETRY_EVERY_N        = 10
END_KEY_EVERY_N            = 6

CUTOFF_YEARS   = 2
PLATFORM       = "Google Maps"
ORG            = "avtolotsman"

IS_WINDOWS = (platform.system() == "Windows")
if IS_WINDOWS:
    SCROLL_PAUSE = 0.35
    MAX_SCROLL_SECONDS = 180
    NO_HEIGHT_GROWTH_TOLERANCE = 10
    NO_CARD_GROWTH_TOLERANCE = 14
    NO_TEXT_GROWTH_TOLERANCE = 14
    SCROLL_HARD_LIMIT = 3000

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

    return None

yb = find_yandex_browser()

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
    'сек': 'seconds','секун': 'seconds',
    'мин': 'minutes','минут': 'minutes','мину': 'minutes',
    'час': 'hours','часа': 'hours','часов': 'hours',
    'день': 'days','дня': 'days','дней': 'days','сут': 'days',
    'недел': 'weeks','нед': 'weeks',
    'месяц': 'months','месяца': 'months','месяцев': 'months',
    'год': 'years','года': 'years','лет': 'years'
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

RU_MONTHS = {
    "января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
    "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12
}
EN_MONTHS = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12
}

def normalize_absolute(text: str) -> Optional[str]:
    if not text: return None
    s = text.strip().lower().replace(' г.', '').replace('г.', '').strip()

    mr = re.match(r'(\d{1,2})\s+([а-яё]+)\s+(\d{4})', s)
    if mr:
        d = int(mr.group(1)); mon_name = mr.group(2); y = int(mr.group(3))
        m = RU_MONTHS.get(mon_name, None)
        if m:
            try: return date(y, m, d).isoformat()
            except ValueError: return None

    me = re.match(r'([a-z]+)\s+(\d{1,2}),\s*(\d{4})', s)
    if me:
        mon_name = me.group(1); d = int(me.group(2)); y = int(me.group(3))
        m = EN_MONTHS.get(mon_name, EN_MONTHS.get(mon_name.lower(), None))
        if m:
            try: return date(y, m, d).isoformat()
            except ValueError: return None

    me2 = re.match(r'(\d{1,2})\s+([a-z]+)\s+(\d{4})', s)
    if me2:
        d = int(me2.group(1)); mon_name = me2.group(2); y = int(me2.group(3))
        m = EN_MONTHS.get(mon_name, EN_MONTHS.get(mon_name.lower(), None))
        if m:
            try: return date(y, m, d).isoformat()
            except ValueError: return None

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None

def normalize_date_pref_ru_relative(text: str) -> Optional[str]:
    """Сначала пробуем RU-относительные (rsqaWe: «месяц назад»), затем абсолютные."""
    return normalize_relative_ru(text) or normalize_absolute(text)

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

            is_scrollable = drv.execute_script("return arguments[0] && arguments[0].scrollHeight > arguments[0].clientHeight;", el)
            if not is_scrollable:
                try:
                    drv.execute_script("arguments[0].style.overflowY='auto';", el)
                    is_scrollable = drv.execute_script("return arguments[0].scrollHeight > arguments[0].clientHeight;", el)
                except Exception:
                    pass
            if is_scrollable:
                return el
        except Exception:
            continue
    return None

def organization_from_url_or_title(drv, url: str) -> str:
    try:
        path = urlparse(url).path
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

def extract_summary_gmaps(drv) -> Tuple[Optional[float], Optional[int]]:
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
        el = c.find_element(By.CSS_SELECTOR, DATE_CSS)
        date_text = (el.text or "").strip()
        if not date_text:
            date_text = (el.get_attribute("aria-label") or "").strip()
    except Exception:
        date_text = ""
    if date_text:
        date_iso = normalize_date_pref_ru_relative(date_text)

    if not date_iso:
        full_txt = (c.text or "").strip()
        if full_txt:
            for p in [r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b",
                      r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},\s*\d{4}\b",
                      r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b",
                      r"\b\d{2}\.\d{2}\.\d{4}\b", r"\b\d{4}-\d{2}-\d{2}\b"]:
                m = re.search(p, full_txt, flags=re.I)
                if m:
                    date_iso = normalize_absolute(m.group(0))
                    if date_iso:
                        date_text = m.group(0)
                        break

    text = ""
    try:
        texts = [t.text.strip() for t in c.find_elements(By.CSS_SELECTOR, TEXT_CSS) if t.text.strip()]
        if texts:
            text = max(texts, key=len)
    except Exception:
        pass

    return {
        "rating": rating,
        "author": author,
        "date_text": date_text,
        "date_iso": date_iso,
        "text": text,
    }

def disable_profile_clicks(drv):
    if not IS_WINDOWS:
        return
    js = """
    (function(){
      try {
        document.addEventListener('click', function(e){
          const btn = e.target.closest('button.al6Kxe');
          if (btn) { e.stopPropagation(); e.preventDefault(); }
        }, true);
        const style = document.createElement('style');
        style.textContent = "button.al6Kxe{pointer-events:none !important}";
        document.documentElement.appendChild(style);
      } catch(e){}
    })();
    """
    try:
        drv.execute_script(js)
    except Exception:
        pass

def _close_profile_if_open(drv):
    if not IS_WINDOWS:
        return
    try:
        for xp in ["//div[@role='dialog']//button[@aria-label='Close']",
                   "//div[@role='dialog']//button[@aria-label='Закрыть']"]:
            try:
                WebDriverWait(drv,1).until(EC.element_to_be_clickable((By.XPATH, xp))).click()
                return
            except Exception:
                pass
        has_dialog = False
        try:
            has_dialog = WebDriverWait(drv, 0.5).until(
                EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
            ) is not None
        except Exception:
            has_dialog = False
        if has_dialog:
            drv.execute_script("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape'}));")
    except Exception:
        pass

def _focus_container(drv, container):
    if IS_WINDOWS:
        try:
            drv.execute_script("arguments[0].focus();", container)
            return True
        except Exception:
            return False
    else:
        try:
            ActionChains(drv).move_to_element(container).pause(0.02).click().perform()
            return True
        except Exception:
            try:
                drv.execute_script("arguments[0].focus();", container)
                return True
            except Exception:
                return False

def _is_stale(drv, el) -> bool:
    try:
        return not drv.execute_script("return arguments[0] && document.contains(arguments[0]);", el)
    except Exception:
        return True

def _wheel_burst(drv, container, steps=6, delta=800):
    """Симулируем колесо мыши по контейнеру — помогает дорисовывать «хвост» на Windows."""
    js = """
    (function(el, steps, delta){
      try{
        for (let i=0;i<steps;i++){
          el.dispatchEvent(new WheelEvent('wheel', {deltaY: delta, bubbles:true, cancelable:true}));
        }
      }catch(e){}
    })(arguments[0], arguments[1], arguments[2]);
    """
    try:
        drv.execute_script(js, container, int(steps), int(delta))
    except Exception:
        pass

def _scroll_last_card_into_view(drv, container):
    try:
        cards = (container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_CSS)
                 or container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_FALLBACK))
        if not cards:
            return
        last = cards[-1]
        drv.execute_script("arguments[0].scrollIntoView({block:'end', inline:'nearest'});", last)
    except Exception:
        pass

def scroll_to_end(drv, container) -> Tuple[int, int]:
    """
    Возвращает (total_cards_seen, text_cards_seen) после попытки доскроллить «до упора».
    Защиты:
      - ограничение по времени (MAX_SCROLL_SECONDS)
      - 3 независимых счётчика «нет роста»: высоты, числа карточек, числа карточек с текстом
      - периодический рефокус контейнера и «покачивание»
      - авто-переинициализация контейнера при StaleElementReferenceException
      - на macOS оставлены клавиши END/PGDN, на Windows — JS + Wheel + scrollIntoView для «добора»
    """
    start_ts = monotonic()
    USE_KEYS = not IS_WINDOWS

    last_h = -1
    last_cards = -1
    last_text = -1

    no_h_growth = 0
    no_cards_growth = 0
    no_text_growth = 0

    iters = 0
    total_seen = 0
    text_seen = 0

    _focus_container(drv, container)

    while iters < SCROLL_HARD_LIMIT:
        iters += 1

        if monotonic() - start_ts > MAX_SCROLL_SECONDS:
            break

        if IS_WINDOWS and iters % 7 == 0:
            _close_profile_if_open(drv)

        if _is_stale(drv, container):
            container = find_reviews_container(drv)
            if not container:
                click_all_reviews(drv)
                container = find_reviews_container(drv)
                if not container:
                    break
            _focus_container(drv, container)

        try:
            for b in container.find_elements(By.CSS_SELECTOR, EXPAND_BTN_CSS):
                try:
                    if b.is_displayed() and b.is_enabled():
                        b.click()
                except Exception:
                    pass

            cards = (container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_CSS)
                     or container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_FALLBACK))
            total_seen = len(cards)

            cur_text = 0
            for c in cards:
                try:
                    if any(t.text.strip() for t in c.find_elements(By.CSS_SELECTOR, TEXT_CSS)):
                        cur_text += 1
                except Exception:
                    pass
            text_seen = cur_text

            try:
                h = drv.execute_script("return arguments[0].scrollHeight;", container)
            except Exception:
                break

            try:
                drv.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
            except Exception:
                break

            if USE_KEYS:
                if iters % END_KEY_EVERY_N == 0:
                    try: container.send_keys(Keys.END)
                    except Exception: pass
                if iters % PAGE_DOWN_EVERY_N == 0:
                    try: container.send_keys(Keys.PAGE_DOWN)
                    except Exception: pass

            if iters % JIGGLE_EVERY_N == 0:
                try:
                    drv.execute_script("arguments[0].scrollTop = Math.max(0, arguments[0].scrollTop - 300);", container)
                    time.sleep(0.06 if IS_WINDOWS else 0.05)
                    drv.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
                except Exception:
                    pass

            if iters % FOCUS_RETRY_EVERY_N == 0:
                _focus_container(drv, container)

            if h == last_h:
                no_h_growth += 1
            else:
                no_h_growth = 0

            if total_seen == last_cards:
                no_cards_growth += 1
            else:
                no_cards_growth = 0

            if text_seen == last_text:
                no_text_growth += 1
            else:
                no_text_growth = 0

            last_h = h
            last_cards = total_seen
            last_text = text_seen

            if IS_WINDOWS and (no_h_growth >= 3 or no_cards_growth >= 3):
                _scroll_last_card_into_view(drv, container)
                _wheel_burst(drv, container, steps=8, delta=900)
                try:
                    drv.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
                except Exception:
                    pass
                time.sleep(0.2)
                no_h_growth = max(0, no_h_growth - 2)
                no_cards_growth = max(0, no_cards_growth - 2)

            if (no_h_growth >= NO_HEIGHT_GROWTH_TOLERANCE or
                no_cards_growth >= NO_CARD_GROWTH_TOLERANCE or
                no_text_growth >= NO_TEXT_GROWTH_TOLERANCE):
                break

            time.sleep(SCROLL_PAUSE)

        except StaleElementReferenceException:
            container = find_reviews_container(drv)
            if not container:
                click_all_reviews(drv)
                container = find_reviews_container(drv)
                if not container:
                    break
            _focus_container(drv, container)
            time.sleep(0.1)
            continue

    try:
        if not _is_stale(drv, container):
            _scroll_last_card_into_view(drv, container)
            _wheel_burst(drv, container, steps=8, delta=900) if IS_WINDOWS else None
            drv.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
    except Exception:
        pass
    try:
        if not _is_stale(drv, container):
            for b in container.find_elements(By.CSS_SELECTOR, EXPAND_BTN_CSS):
                try:
                    if b.is_displayed() and b.is_enabled():
                        b.click()
                except Exception:
                    pass
    except StaleElementReferenceException:
        pass

    try:
        if _is_stale(drv, container):
            container = find_reviews_container(drv) or container
        cards = (container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_CSS)
                 or container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_FALLBACK))
    except Exception:
        cards = []
    total_seen = len(cards)
    text_seen = 0
    for c in cards:
        try:
            if any(t.text.strip() for t in c.find_elements(By.CSS_SELECTOR, TEXT_CSS)):
                text_seen += 1
        except Exception:
            pass

    return total_seen, text_seen

def collect_all(drv, container, cutoff_date: date, w_rev, org: str) -> Tuple[int, int]:
    """Полный скролл, подсчёт text-отзывов (для summary) + запись в CSV только отзывов младше 2 лет."""
    _, total_text_reviews = scroll_to_end(drv, container)

    seen_keys: Set[Tuple[str, str]] = set()
    cards = container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_CSS) \
            or container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_FALLBACK)

    written = 0
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

        if d < cutoff_date:
            continue

        key = ((item.get("author") or "").strip(), txt[:160])
        if key in seen_keys:
            continue
        seen_keys.add(key)

        w_rev.writerow({
            "rating":       item.get("rating"),
            "author":       (item.get("author") or "").strip(),
            "date_iso":     d.isoformat(),
            "text":         txt.replace("\r", " ").replace("\n", " ").strip(),
            "platform":     PLATFORM,
            "organization": org,
        })
        written += 1

    return written, total_text_reviews

def main():
    opts = Options()
    opts.binary_location = str(yb)
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
    Path(OUT_CSV_SUM).parent.mkdir(parents=True, exist_ok=True)

    f_rev = open(OUT_CSV_REV, "w", newline="", encoding="utf-8")
    f_sum = open(OUT_CSV_SUM, "w", newline="", encoding="utf-8")

    w_rev = csv.DictWriter(f_rev, fieldnames=["rating","author","date_iso","text","platform","organization"], quoting=csv.QUOTE_ALL)
    w_sum = csv.DictWriter(f_sum, fieldnames=["organization","platform","rating_avg","ratings_count","reviews_count"], quoting=csv.QUOTE_ALL)
    w_rev.writeheader()
    w_sum.writeheader()

    cutoff_date = (datetime.now().date() - timedelta(days=365*CUTOFF_YEARS) - timedelta(days=10))

    try:
        for i, base in enumerate(urls, 1):
            url = add_hl_ru(base)
            print(f"[{i}/{len(urls)}] {url}")

            drv.get(url)
            time.sleep(FIRST_WAIT if i == 1 else SHORT_WAIT)

            accept_cookies_if_any(drv)
            click_all_reviews(drv)
            time.sleep(1.2)

            disable_profile_clicks(drv)

            set_sort_newest(drv)
            time.sleep(0.6)

            rating_avg, ratings_count = extract_summary_gmaps(drv)

            container = find_reviews_container(drv)
            if not container:
                click_all_reviews(drv)
                container = find_reviews_container(drv)
                if not container:
                    print("  Feedback container not found, skipping")
                    w_sum.writerow({
                        "organization": ORG,
                        "platform":     PLATFORM,
                        "rating_avg":   rating_avg if rating_avg is not None else "",
                        "ratings_count":ratings_count if ratings_count is not None else "",
                        "reviews_count":""
                    })
                    continue

            written_recent, total_text_reviews = collect_all(drv, container, cutoff_date, w_rev, ORG)

            w_sum.writerow({
                "organization": ORG,
                "platform":     PLATFORM,
                "rating_avg":   rating_avg if rating_avg is not None else "",
                "ratings_count":ratings_count if ratings_count is not None else "",
                "reviews_count":total_text_reviews,
            })

            print(f"  summary: rating={rating_avg}, ratings={ratings_count}, text (total)={total_text_reviews}, written <2 years ago={written_recent}")
    finally:
        try: drv.quit()
        except Exception: pass
        f_rev.close()
        f_sum.close()

    print(f"Done. Reviews -> {OUT_CSV_REV} | Summary -> {OUT_CSV_SUM}")

if __name__ == "__main__":
    main()
