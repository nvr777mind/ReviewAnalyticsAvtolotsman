# -*- coding: utf-8 -*-
"""
Инкрементальный сбор отзывов с 2ГИС БЕЗ проверки наличия в all_reviews:
- Пороговая дата берётся из Csv/Reviews/all_reviews.csv для платформы '2GIS' (только для остановки по дате).
- По каждой ссылке из Urls/2gis_urls.txt:
    * открываем страницу, переходим на «Отзывы»,
    * снимаем summary (rating_avg, ratings_count, reviews_count),
    * скроллим и пишем ТОЛЬКО отзывы СТРОГО НОВЕЕ пороговой даты,
    * как только встретился отзыв с датой <= пороговой — останавливаемся.
- Дедуп делаем только в пределах текущего запуска (author_norm + text_signature), чтобы не словить дубль из-за особенностей скролла.
- Новые отзывы → Csv/Reviews/NewReviews/2gis_new_since.csv
- Новый summary → Csv/Summary/NewSummary/2gis_summary_new.csv, где reviews_count = старое из Csv/Summary/2gis_summary.csv + добавленные новые.
Совместим с Python 3.9.
"""

import csv, re, time, unicodedata
from typing import Optional, Tuple, List, Dict
from datetime import datetime, timedelta, date
from pathlib import Path

import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchWindowException, WebDriverException, TimeoutException

# ===== ПУТИ =====
DGIS_URLS_FILE       = "./Urls/2gis_urls.txt"
FALLBACK_URL         = ("https://2gis.ru/penza/search/%D0%B0%D0%B2%D1%82%D0%BE%D0%BB%D0%BE%D1%86%D0%BC%D0%B0%D0%BD/"
                        "firm/70000001057701394/44.973806%2C53.220685/tab/reviews?m=44.975027%2C53.220456%2F17.63")

ALL_REVIEWS_CSV      = "Csv/Reviews/all_reviews.csv"      # используем только для пороговой даты
SUMMARY_BASE_CSV     = "Csv/Summary/2gis_summary.csv"     # для инкремента reviews_count

OUT_CSV_REV_DELTA    = "Csv/Reviews/NewReviews/2gis_new_since.csv"
OUT_CSV_SUMMARY_NEW  = "Csv/Summary/NewSummary/2gis_summary_new.csv"

PLATFORM             = "2GIS"

# ===== БРАУЗЕР =====
YANDEX_BROWSER_BINARY = "/Applications/Yandex.app/Contents/MacOS/Yandex"
YANDEXDRIVER_PATH     = "drivers/yandexdriver"
PROFILE_DIR           = str(Path.home() / ".yandex-2gis-scraper")

# ===== ПАРАМЕТРЫ =====
WAIT_TIMEOUT        = 20
BURSTS              = 30
BURST_MS            = 1100
IDLE_LIMIT          = 3
ENFORCE_DATE_CUTOFF = False
YEARS_LIMIT_HINT    = 2

# ===== 2ГИС СЕЛЕКТОРЫ =====
AUTHOR_SEL       = "span._wrdavn > span._16s5yj36"
DATE_SEL         = "div._m80g57y > div._a5f6uz"
RATING_FILL_SEL  = "div._1m0m6z5 > div._1fkin5c"
TEXT_BLOCK_SEL   = "div._49x36f > a._1wlx08h"     # НЕразвёрнутый текст
ALT_TEXT_SEL     = "div._49x36f > a._1msln3t"     # Развёрнутый текст (fallback)
SCROLL_CONTAINER_SEL = "div._1rkbbi0x[data-scroll='true']"

# summary
SUM_RATING_SEL        = "div._1tam240"                  # 3.7
SUM_RATINGS_COUNT_SEL = "div._1y88ofn"                  # "4 оценки"
SUM_REVIEWS_COUNT_SEL = "div._qvsf7z > span._1xhlznaa"  # строго

# ===== МАППИНГ firm id → slug =====
ORGANIZATION_MAP_FIRMID: Dict[str, str] = {
    "70000001057701394": "avtolotsman_probeg",
    "70000001086881480": "avtolotsman",
    "5911502791905673":  "kia_avtolotsman",
    "5911502792028090":  "mazda_avtolotsman",
    "70000001083460643": "moskvich_avtolotsman",
    "5911502792136575":  "shkoda_avtolotsman",
    "70000001071267471": "avtolotsman_deteyling",
    "70000001083645814": "changan_avtolotsman",
    "70000001101283058": "liven_avtolotsman",
}

MONTHS_RU = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
             "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}
RELATIVE_MAP = {"сегодня": 0, "вчера": -1}

# ===== НОРМАЛИЗАЦИИ/КЛЮЧИ =====
def norm_text(s: str) -> str:
    if not s: return ""
    x = unicodedata.normalize("NFKC", s).lower()
    x = re.sub(r"\s+", " ", x).strip()
    x = re.sub(r"[«»\"'”“„‚…‐-–—·•/\\\(\)\[\]\{\},.;:!?]", "", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x

def norm_author(s: str) -> str:
    if not s: return ""
    x = unicodedata.normalize("NFKC", s).lower()
    x = re.sub(r"\s+", " ", x).strip()
    return x

def text_signature(s: str, length: int = 180) -> str:
    return norm_text(s)[:length]

# ===== ДАТЫ =====
def parse_ru_date_to_iso(s: Optional[str]) -> Optional[str]:
    if not s: return None
    s = s.strip().lower()
    s = re.sub(r"редакт.*$", "", s).strip()
    s = re.split(r"[,\u2022•]", s)[0].strip()

    if s in RELATIVE_MAP:
        d = datetime.now().date() + timedelta(days=RELATIVE_MAP[s])
        return d.isoformat()

    mrel = re.match(r"^(\d{1,2})\s+([а-яё]+)\s+назад$", s, flags=re.I)
    if mrel:
        qty = int(mrel.group(1)); unit = mrel.group(2)
        days_map = {
            "день":1, "дня":1, "дней":1,
            "неделя":7, "недели":7, "недель":7, "неделю":7,
            "месяц":30, "месяца":30, "месяцев":30,
            "год":365, "года":365, "лет":365,
        }
        step = None
        for k, v in days_map.items():
            if unit.startswith(k[:4]): step = v; break
        if step:
            d = datetime.now().date() - timedelta(days=qty * step)
            return d.isoformat()

    m = re.match(r"^(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?(?:\s*г\.?)?$", s, flags=re.I)
    if m:
        day = int(m.group(1)); mon = MONTHS_RU.get(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        if mon:
            try: return datetime(year, mon, day).date().isoformat()
            except: return None

    m2 = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if y < 100: y += 2000
        try: return datetime(y, mo, d).date().isoformat()
        except: return None

    return None

# ===== БРАУЗЕР =====
def build_options() -> Options:
    opts = Options()
    opts.binary_location = YANDEX_BROWSER_BINARY
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=ru-RU,ru")
    opts.page_load_strategy = "eager"
    opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    opts.add_argument("--profile-directory=Default")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return opts

def setup_driver() -> webdriver.Chrome:
    if not Path(YANDEX_BROWSER_BINARY).exists():
        raise FileNotFoundError(f"Нет Yandex Browser: {YANDEX_BROWSER_BINARY}")
    if not Path(YANDEXDRIVER_PATH).is_file():
        raise FileNotFoundError(f"Нет yandexdriver: {YANDEXDRIVER_PATH}")
    service = Service(executable_path=YANDEXDRIVER_PATH)
    drv = webdriver.Chrome(service=service, options=build_options())
    drv.set_page_load_timeout(120); drv.set_script_timeout(120); drv.implicitly_wait(0)
    try:
        drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
    except:
        pass
    return drv

def ensure_window(drv) -> bool:
    try: return bool(drv.window_handles)
    except: return False

def safe_get(drv, url: str) -> bool:
    try: drv.get(url); return True
    except (NoSuchWindowException, WebDriverException): return False

def inject_perf_css(driver):
    try:
        driver.execute_script("""
            if (!document.getElementById('no-anim-style')) {
              var st = document.createElement('style'); st.id='no-anim-style';
              st.innerHTML='*{animation:none!important;transition:none!important;} html{scroll-behavior:auto!important;}';
              document.head.appendChild(st);
            }
        """)
    except: pass

def click_cookies_if_any(driver):
    btn_xps = [
        "//*[self::button or self::span][contains(., 'Понятно')]",
        "//*[self::button or self::span][contains(., 'Принять')]",
        "//*[self::button or self::span][contains(., 'Хорошо')]",
        "//*[self::button or self::span][contains(., 'Ок') or contains(., 'OK')]",
        "//*[self::button or self::span][contains(., 'Согласен')]",
    ]
    for xp in btn_xps:
        try:
            btn = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.XPATH, xp)))
            driver.execute_script("arguments[0].click();", btn)
            break
        except: pass

def ensure_reviews_tab(driver):
    try:
        tabs = driver.find_elements(By.XPATH, "//*[self::a or self::span or self::div][contains(., 'Отзывы')]")
        for t in tabs[:3]:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
                driver.execute_script("arguments[0].click();", t)
                time.sleep(2)
                break
            except: pass
    except: pass

def switch_to_reviews_iframe(driver) -> bool:
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in iframes:
            try:
                driver.switch_to.frame(frame); time.sleep(1)
                # простая эвристика "внутри есть контент"
                if len([el for el in driver.find_elements(By.CSS_SELECTOR, "div, span, p") if len((el.text or '').strip()) > 50]) > 0:
                    return True
                driver.switch_to.default_content()
            except:
                driver.switch_to.default_content(); continue
        return False
    except: return False

def wait_for_reviews_content(driver):
    def _present(drv):
        try:
            return (len(drv.find_elements(By.CSS_SELECTOR, TEXT_BLOCK_SEL)) > 0 or
                    len(drv.find_elements(By.CSS_SELECTOR, ALT_TEXT_SEL)) > 0)
        except: return False
    WebDriverWait(driver, WAIT_TIMEOUT).until(_present)

# ===== СКРОЛЛ =====
def _is_visible(driver, el) -> bool:
    try:
        w,h = driver.execute_script("const r=arguments[0].getBoundingClientRect();return [r.width,r.height];", el)
        return w>0 and h>0
    except: return False

def get_scroll_container(driver):
    try:
        cand = [c for c in driver.find_elements(By.CSS_SELECTOR, SCROLL_CONTAINER_SEL) if _is_visible(driver, c)]
        if cand:
            best = max(cand, key=lambda el: (driver.execute_script("return arguments[0].clientHeight;", el) or 0))
            try: driver.execute_script("arguments[0].focus({preventScroll:true});", best)
            except: pass
            return best
    except: pass
    try:
        return driver.execute_script("return document.scrollingElement || document.body;")
    except: return driver.find_element(By.TAG_NAME, "body")

def get_scroll_height(driver, container) -> int:
    try: return int(driver.execute_script("return arguments[0].scrollHeight;", container) or 0)
    except: return int(driver.execute_script("return (document.scrollingElement||document.body).scrollHeight;") or 0)

def autoscroll_burst(driver, container, ms: int):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", container)
        driver.execute_script("arguments[0].focus({preventScroll:true});", container)
    except: pass
    deadline = time.time() + (ms/1000.0)
    while time.time() < deadline:
        try:
            ch = int(driver.execute_script("return arguments[0].clientHeight;", container) or 600)
            step = max(200, int(ch*0.85))
            driver.execute_script("arguments[0].scrollTop = Math.min(arguments[0].scrollTop + arguments[1], arguments[0].scrollHeight);", container, step)
        except:
            driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight*0.8));")
        time.sleep(0.25)

# ===== ПАРСИНГ КАРТОЧКИ =====
def _rating_from_spans_count(card) -> Optional[float]:
    try:
        for fill in card.find_elements(By.CSS_SELECTOR, RATING_FILL_SEL):
            stars = fill.find_elements(By.TAG_NAME, "span")
            if 1 <= len(stars) <= 5:
                return float(len(stars))
    except: pass
    return None

def _looks_like_header(text: str, author: str) -> bool:
    s = (text or "").strip()
    if not s: return True
    low = s.lower()
    if low.startswith(("официальный ответ", "ответ владельца")): return True
    if author and low.startswith(author.lower()): return True
    if re.search(r"\bотзыв(ов)?\b", low): return True
    return False

def normalize_review_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(Полезно.*|Читать целиком.*|Свернуть.*|Официальный ответ.*)$", "", s, flags=re.I)
    s = re.sub(r"([.!?…])\s*\d{1,3}$", r"\1", s)
    return s.strip()

def _get_text_by_selectors(card) -> str:
    try:
        el = card.find_element(By.CSS_SELECTOR, TEXT_BLOCK_SEL)
        t = (el.text or "").strip()
        if t: return t
    except: pass
    try:
        el = card.find_element(By.CSS_SELECTOR, ALT_TEXT_SEL)
        t = (el.text or "").strip()
        if t: return t
    except: pass
    return ""

def find_review_text(card, author: str) -> str:
    tt = normalize_review_text(_get_text_by_selectors(card))
    if not tt: return ""
    low = tt.lower()
    if "официальный ответ" in low or "ответ владельца" in low: return ""
    if _looks_like_header(tt, author): return ""
    return tt if len(tt) >= 2 else ""

def extract_review_from_card(card, driver) -> dict:
    author = ""
    try:
        author_el = card.find_element(By.CSS_SELECTOR, AUTHOR_SEL)
        author = (author_el.get_attribute("title") or author_el.text or "").strip()
    except: pass

    date_raw, date_iso = "", ""
    try:
        date_els = card.find_elements(
            By.XPATH,
            ".//div[contains(@class,'_m80g57y')]//div[contains(@class,'_a5f6uz')][not(ancestor::*[contains(@class,'_sgs1pz')])]"
        ) or card.find_elements(By.CSS_SELECTOR, DATE_SEL)
        if date_els:
            date_raw = (date_els[0].text or "").strip()
            date_iso = parse_ru_date_to_iso(date_raw) or ""
            if not date_iso:
                try:
                    time_el = date_els[0].find_element(By.CSS_SELECTOR, "time")
                    dt = (time_el.get_attribute("datetime") or "").strip()
                    if dt: date_iso = dt[:10]
                except: pass
    except: pass

    rating = _rating_from_spans_count(card)
    text = find_review_text(card, author)
    text = re.sub(r"[\r\n]+", " ", text).strip()

    return {"author": author, "rating": rating, "date_raw": date_raw, "date_iso": date_iso, "text": text}

def extract_organization_from_url(url: str) -> Optional[str]:
    m = re.search(r"/firm/(\d+)", url)
    if not m: return None
    firm_id = m.group(1)
    return ORGANIZATION_MAP_FIRMID.get(firm_id)

def extract_summary_2gis(driver) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    try: driver.switch_to.default_content()
    except: pass
    rating_avg = ratings_count = reviews_count = None
    try:
        el = driver.find_elements(By.CSS_SELECTOR, SUM_RATING_SEL)
        if el:
            t = el[0].text.strip().replace("\xa0"," ")
            m = re.search(r"(\d+[,\.\u202F]\d+|\d+)", t)
            if m: rating_avg = float(m.group(1).replace("\u202f","").replace(",", "."))
    except: pass
    try:
        els = driver.find_elements(By.CSS_SELECTOR, SUM_RATINGS_COUNT_SEL)
        for e in els:
            m = re.search(r"(\d[\d\s]*)", (e.text or "").replace("\xa0"," "))
            if m: ratings_count = int(m.group(1).replace(" ","")); break
    except: pass
    try:
        el = driver.find_elements(By.CSS_SELECTOR, SUM_REVIEWS_COUNT_SEL)
        if el:
            m = re.search(r"(\d[\d\s]*)", (el[0].text or "").replace("\xa0"," "))
            if m: reviews_count = int(m.group(1).replace(" ",""))
    except: pass
    if reviews_count is None:
        try:
            html = driver.page_source
            m = re.search(r'<div class="_qvsf7z"[^>]*>.*?<span class="_1xhlznaa">\s*([\d\s]+)\s*</span>', html, re.S)
            if m: reviews_count = int(m.group(1).replace("\u202f","").replace(" ",""))
        except: pass
    return rating_avg, ratings_count, reviews_count

def find_review_cards(driver):
    try: return driver.find_elements(By.CSS_SELECTOR, "div._1k5soqfl")
    except: return []

# ===== CSV/ALL_REVIEWS: только пороговые даты =====
def _try_parse_date(s: Optional[str]) -> Optional[date]:
    if not s: return None
    s = s.strip()[:10]
    try: return datetime.fromisoformat(s).date()
    except Exception: pass
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100: y += 2000
        try: return date(y, mo, d)
        except Exception: return None
    return None

def normalize_org(name: str) -> str:
    if not name: return ""
    s = unicodedata.normalize("NFKC", name).lower()
    s = re.sub(r"[«»\"'”“„‚,]", " ", s)
    s = " ".join(s.split())
    return s

def load_latest_dates_by_org(all_reviews_csv: str, platform: str) -> Dict[str, date]:
    latest: Dict[str, date] = {}
    p = Path(all_reviews_csv)
    if not p.exists(): return latest
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        date_cols = ["date_iso", "dateISO", "date", "Date", "DATE"]
        for row in reader:
            try:
                if (row.get("platform") or "").strip() != platform: continue
                org_key = normalize_org((row.get("organization") or "").strip())
                if not org_key: continue
                d_str = next((row[c] for c in date_cols if c in row and row[c]), None)
                d = _try_parse_date(d_str)
                if not d: continue
                prev = latest.get(org_key)
                if prev is None or d > prev: latest[org_key] = d
            except Exception:
                continue
    return latest

def _int_from_any(x) -> Optional[int]:
    if x is None: return None
    s = str(x).replace("\u202f", " ").replace("\xa0", " ").strip()
    m = re.search(r"(\d[\d\s]*)", s)
    if not m: return None
    try: return int(m.group(1).replace(" ", ""))
    except Exception: return None

def load_prev_reviews_count(summary_csv: str, platform: str) -> Dict[str, int]:
    res: Dict[str, int] = {}
    p = Path(summary_csv)
    if not p.exists(): return res
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                if (row.get("platform") or "").strip() != platform: continue
                org_key = normalize_org((row.get("organization") or "").strip())
                if not org_key: continue
                rc = _int_from_any(row.get("reviews_count"))
                if rc is None: continue
                res[org_key] = rc
            except Exception:
                continue
    return res

# ===== Сбор партии (локальный дедуп) =====
def collect_visible_batch(driver, cutoff_date: date,
                          dedupe_index: Dict[Tuple[str,str], int],
                          out: List[Dict]) -> Tuple[int, bool]:
    added, met_old = 0, False
    for card in find_review_cards(driver):
        try:
            item = extract_review_from_card(card, driver)
            txt = (item.get("text") or "").strip()
            if not txt or len(txt) < 2:
                continue
            if "официальный ответ" in txt.lower():
                continue

            d_iso = item.get("date_iso") or ""
            if ENFORCE_DATE_CUTOFF and not d_iso:
                continue
            if d_iso:
                try:
                    d = datetime.fromisoformat(d_iso[:10]).date()
                    if d <= cutoff_date:
                        met_old = True
                        continue
                except Exception:
                    pass

            key = (norm_author(item.get("author") or ""), text_signature(txt))
            if key not in dedupe_index:
                dedupe_index[key] = len(out)
                out.append(item)
                added += 1
            else:
                idx = dedupe_index[key]
                cur = out[idx]
                if not (cur.get("date_iso") or cur.get("date_raw")) and (item.get("date_iso") or item.get("date_raw")):
                    cur["date_iso"] = item.get("date_iso") or cur.get("date_iso","")
                    cur["date_raw"] = item.get("date_raw") or cur.get("date_raw","")
                if cur.get("rating") is None and item.get("rating") is not None:
                    cur["rating"] = item["rating"]
        except Exception:
            continue
    return added, met_old



def _nz(v, zero=0):
    return v if v not in (None, "") else zero

# ===== ОСНОВНОЙ ПРОЦЕСС ОДНОГО URL =====
def process_one_url(url: str,
                    forced_org: Optional[str],
                    cutoff_date: date) -> Tuple[str, List[Dict], Tuple[Optional[float], Optional[int], Optional[int]]]:
    driver = setup_driver()
    try:
        if not safe_get(driver, url):
            driver.quit(); driver = setup_driver()
            if not safe_get(driver, url):
                return "", [], (None, None, None)
        if not ensure_window(driver):
            driver.quit(); return "", [], (None, None, None)

        inject_perf_css(driver); time.sleep(2)
        org = forced_org or extract_organization_from_url(url) or ""

        # summary (до фреймов/таба)
        try:
            rating_avg, ratings_count, reviews_count = extract_summary_2gis(driver)
        except Exception:
            rating_avg = ratings_count = reviews_count = None

        try: click_cookies_if_any(driver)
        except: pass
        try: ensure_reviews_tab(driver)
        except: pass

        switch_to_reviews_iframe(driver)
        wait_for_reviews_content(driver)

        container = get_scroll_container(driver)

        results: List[Dict] = []
        dedupe_index: Dict[Tuple[str,str], int] = {}

        idle = 0
        stop_by_age = False

        added, met_old = collect_visible_batch(driver, cutoff_date, dedupe_index, results)
        if met_old: stop_by_age = True

        for _ in range(BURSTS):
            if stop_by_age: break
            prev_len = len(results)
            prev_h = get_scroll_height(driver, container)

            autoscroll_burst(driver, container, BURST_MS)
            time.sleep(1.0)

            added, met_old = collect_visible_batch(driver, cutoff_date, dedupe_index, results)
            if met_old: stop_by_age = True

            new_h = get_scroll_height(driver, container)
            height_grew = new_h > prev_h + 2
            idle = 0 if (added or len(results) > prev_len or height_grew) else (idle + 1)
            if idle >= IDLE_LIMIT: break

        for r in results:
            r["organization"] = org

        print(f"  Собрано новых: {len(results)} | org={org or '-'} | стоп по порогу: {stop_by_age}")
        return org, results, (rating_avg, ratings_count, reviews_count)

    except (NoSuchWindowException, WebDriverException, TimeoutException):
        return "", [], (None, None, None)
    finally:
        try: driver.quit()
        except: pass

# ===== MAIN =====
def main():
    # URL’ы
    try:
        urls = [u.strip() for u in Path(DGIS_URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
        if not urls: urls = [FALLBACK_URL]
    except FileNotFoundError:
        urls = [FALLBACK_URL]

    # Пороговые даты из all_reviews (только для остановки по дате)
    latest_by_org = load_latest_dates_by_org(ALL_REVIEWS_CSV, PLATFORM)
    print(f"[INFO] Пороговые даты: {len(latest_by_org)} орг.")

    # Старый summary (для инкремента reviews_count)
    prev_counts = load_prev_reviews_count(SUMMARY_BASE_CSV, PLATFORM)

    # Выходные CSV
    Path(OUT_CSV_SUMMARY_NEW).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_CSV_REV_DELTA).parent.mkdir(parents=True, exist_ok=True)

    f_rev = open(OUT_CSV_REV_DELTA, "w", newline="", encoding="utf-8")
    w_rev = csv.DictWriter(f_rev, fieldnames=["rating","author","date_iso","text","platform","organization"], quoting=csv.QUOTE_ALL)
    w_rev.writeheader()

    total_written_by_org: Dict[str, int] = {}
    # КОПИМ ЕДИНЫЙ summary ПО ОРГАНИЗАЦИИ
    summary_by_org: Dict[str, Dict[str, float]] = {}

    try:
        for i, url in enumerate(urls, 1):
            org_slug = extract_organization_from_url(url) or ""
            org_key = normalize_org(org_slug)
            cutoff = latest_by_org.get(org_key, date(1900,1,1))

            print(f"[{i}/{len(urls)}] {url} -> org='{org_slug or '-'}' | порог={cutoff.isoformat()}")

            org, reviews, (rating_avg, ratings_count, reviews_count) = process_one_url(
                url, forced_org=org_slug, cutoff_date=cutoff
            )

            # Запись дельты отзывов
            written = 0
            for r in reviews:
                w_rev.writerow({
                    "rating":       r.get("rating"),
                    "author":       (r.get("author") or "").strip(),
                    "date_iso":     (r.get("date_iso") or "")[:10],
                    "text":         (r.get("text") or "").replace("\r"," ").replace("\n"," ").strip(),
                    "platform":     PLATFORM,
                    "organization": (r.get("organization") or "").strip(),
                })
                written += 1
            ok = normalize_org(org or org_slug)
            total_written_by_org[ok] = total_written_by_org.get(ok, 0) + written
            print(f"  новых записано: {written}")

            # Агрегируем summary по организации (берём последнюю ненулевую метрику, иначе 0)
            try:
                if ok not in summary_by_org:
                    summary_by_org[ok] = {
                        "organization": org or org_slug or "",
                        "rating_avg":   _nz(rating_avg, 0),
                        "ratings_count":_nz(ratings_count, 0),
                        "reviews_count":_nz(reviews_count, 0),
                    }
            except Exception:
                if summary_by_org is not None:
                    summary_by_org.writerow({
                        "organization": org, "platform": "2GIS",
                        "rating_avg": 0, "ratings_count": 0, "reviews_count": 0
                    })

    finally:
        try: f_rev.flush(); f_rev.close()
        except: pass

    # Пишем ИТОГОВЫЙ summary (по одной строке на org) с инкрементом счётчика
    with open(OUT_CSV_SUMMARY_NEW, "w", newline="", encoding="utf-8") as fsum2:
        w2 = csv.DictWriter(fsum2, fieldnames=["organization","platform","rating_avg","ratings_count","reviews_count"], quoting=csv.QUOTE_ALL)
        w2.writeheader()
        for ok, data in summary_by_org.items():
            org_label = data.get("organization","")
            prev  = prev_counts.get(ok, 0)
            added = total_written_by_org.get(ok, 0)
            w2.writerow({
                "organization": org_label,
                "platform":     PLATFORM,
                "rating_avg":   _nz(data.get("rating_avg")),
                "ratings_count":_nz(data.get("ratings_count")),
                "reviews_count": _nz(prev) + _nz(added),
            })

    print(f"\nГотово.")
    print(f"Δ-отзывы (2ГИС) → {OUT_CSV_REV_DELTA}")
    print(f"Summary (новый, 2ГИС) → {OUT_CSV_SUMMARY_NEW}")
    print(f"Базовое summary (для старого счётчика) → {SUMMARY_BASE_CSV}")
    print(f"Пороговая база дат → {ALL_REVIEWS_CSV}")

if __name__ == "__main__":
    main()
