# -*- coding: utf-8 -*-
"""
2ГИС → CSV: rating,author,date_iso,text,platform,organization
Запуск: Яндекс.Браузер + yandexdriver (macOS). Python 3.9.
"""

import csv, re, time
from typing import Optional, Tuple, List, Set, Dict
from datetime import datetime, timedelta
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
from selenium.webdriver.common.action_chains import ActionChains

import os, sys, platform
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# для колёсика (Selenium 4, если доступно)
try:
    from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
except Exception:
    ScrollOrigin = None

# ===== ВХОД =====
DGIS_URLS_FILE = "./Urls/2gis_urls.txt"
FALLBACK_URL = ("https://2gis.ru/penza/search/%D0%B0%D0%B2%D1%82%D0%BE%D0%BB%D0%BE%D1%86%D0%BC%D0%B0%D0%BD/"
                "firm/70000001057701394/44.973806%2C53.220685/tab/reviews?m=44.975027%2C53.220456%2F17.63")

# ===== ЯНДЕКС-БРАУЗЕР (macOS) =====
YANDEXDRIVER_PATH     = "Drivers/Windows/yandexdriver.exe"
PROFILE_DIR           = str(Path.home() / ".yandex-2gis-scraper")

# ===== ВЫХОД =====
OUT_CSV = "Csv/Reviews/2gis_reviews.csv"
# >>> ДОБАВЛЕНО: summary по каждой ссылке
OUT_CSV_SUMMARY = "Csv/Summary/2gis_summary.csv"

# ===== ПАРАМЕТРЫ =====
WAIT_TIMEOUT        = 8
BURSTS              = 12
BURST_MS            = 520
IDLE_LIMIT          = 1
YEARS_LIMIT         = 2
ENFORCE_DATE_CUTOFF = False

# ===== 2ГИС СЕЛЕКТОРЫ =====
AUTHOR_SEL       = "span._wrdavn > span._16s5yj36"
DATE_SEL         = "div._m80g57y > div._a5f6uz"
RATING_FILL_SEL  = "div._1m0m6z5 > div._1fkin5c"

# Текст: сначала неразвёрнутый, если его нет — развёрнутый
TEXT_BLOCK_SEL   = "div._49x36f > a._1wlx08h"   # НЕразвёрнутый текст
ALT_TEXT_SEL     = "div._49x36f > a._1msln3t"   # Развёрнутый текст (fallback)

# ЯВНЫЙ СКРОЛЛ-КОНТЕЙНЕР
SCROLL_CONTAINER_SEL = "div._1rkbbi0x[data-scroll='true']"

# >>> ДОБАВЛЕНО: селекторы summary
SUM_RATING_SEL        = "div._1tam240"            # пример: 3.7
SUM_RATINGS_COUNT_SEL = "div._1y88ofn"            # пример: "4 оценки"
SUM_REVIEWS_COUNT_SEL = "div._qvsf7z > span._1xhlznaa"  # строго по этому пути

# ---- где лежит браузер Яндекс ----
def find_yandex_browser() -> Optional[Path]:
    # 1) ручное переопределение
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

    return None  # Linux и др. — допиши при необходимости

# ---- инициализация Selenium ----
yb = find_yandex_browser()

# ===== СООТВЕТСТВИЕ URL (firm id) → НУЖНЫЙ СЛАГ ОРГАНИЗАЦИИ =====
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

def org_from_url(url: str) -> Optional[str]:
    """
    Достаём firm/<id> из URL и маппим на слаг.
    """
    m = re.search(r"/firm/(\d+)", url)
    if not m:
        return None
    firm_id = m.group(1)
    return ORGANIZATION_MAP_FIRMID.get(firm_id)

def parse_ru_date_to_iso(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().lower()
    s = re.sub(r"редакт.*$", "", s).strip()
    s = re.split(r"[,\u2022•]", s)[0].strip()

    # сегодня/вчера
    if s in RELATIVE_MAP:
        d = datetime.now().date() + timedelta(days=RELATIVE_MAP[s])
        return d.isoformat()

    # "X ... назад"
    mrel = re.match(r"^(\d{1,2})\s+([а-яё]+)\s+назад$", s, flags=re.I)
    if mrel:
        qty = int(mrel.group(1))
        unit = mrel.group(2)
        days_map = {
            "день":1, "дня":1, "дней":1,
            "неделя":7, "недели":7, "недель":7, "неделю":7,
            "месяц":30, "месяца":30, "месяцев":30,
            "год":365, "года":365, "лет":365,
        }
        step = None
        for k, v in days_map.items():
            if unit.startswith(k[:4]):
                step = v
                break
        if step:
            d = datetime.now().date() - timedelta(days=qty * step)
            return d.isoformat()

    # "24 июля 2025" или "24 июля"
    m = re.match(r"^(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?(?:\s*г\.?)?$", s, flags=re.I)
    if m:
        day = int(m.group(1))
        mon = MONTHS_RU.get(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        if mon:
            try:
                return datetime(year, mon, day).date().isoformat()
            except:
                return None

    # "24.07.2025" или "24.07.25"
    m2 = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime(y, mo, d).date().isoformat()
        except:
            return None

    return None

def build_options() -> Options:
    opts = Options()
    opts.binary_location = str(yb)
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
    if not Path(str(yb)).exists():
        raise FileNotFoundError(f"Нет Yandex Browser: {str(yb)}")
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
    try:
        return bool(drv.window_handles)
    except:
        return False

def safe_get(drv, url: str) -> bool:
    try:
        drv.get(url)
        return True
    except (NoSuchWindowException, WebDriverException):
        return False

def inject_perf_css(driver):
    try:
        driver.execute_script("""
            if (!document.getElementById('no-anim-style')) {
              var st = document.createElement('style'); st.id='no-anim-style';
              st.innerHTML='*{animation:none!important;transition:none!important;} html{scroll-behavior:auto!important;}';
              document.head.appendChild(st);
            }
        """)
    except:
        pass

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
        except:
            pass

def ensure_reviews_tab(driver):
    try:
        tabs = driver.find_elements(By.XPATH, "//*[self::a or self::span or self::div][contains(., 'Отзывы')]")
        for t in tabs[:3]:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
                driver.execute_script("arguments[0].click();", t)
                time.sleep(2)
                break
            except:
                pass
    except:
        pass

def switch_to_reviews_iframe(driver) -> bool:
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in iframes:
            try:
                driver.switch_to.frame(frame)
                time.sleep(1)
                if len([el for el in driver.find_elements(By.CSS_SELECTOR, "div, span, p") if len((el.text or '').strip()) > 50]) > 0:
                    return True
                driver.switch_to.default_content()
            except:
                driver.switch_to.default_content()
                continue
        return False
    except:
        return False

def wait_for_reviews_content(driver):
    def _present(drv):
        try:
            return (
                len(drv.find_elements(By.CSS_SELECTOR, TEXT_BLOCK_SEL)) > 0 or
                len(drv.find_elements(By.CSS_SELECTOR, ALT_TEXT_SEL)) > 0
            )
        except:
            return False
    WebDriverWait(driver, WAIT_TIMEOUT).until(_present)

# ====== СКРОЛЛЕР ======
def _is_visible(driver, el) -> bool:
    try:
        rect = driver.execute_script("const r=arguments[0].getBoundingClientRect();return [r.width,r.height];", el)
        return rect and rect[0] > 0 and rect[1] > 0
    except:
        return False

def get_scroll_container(driver):
    # 1) Жёстко целимся в div._1rkbbi0x[data-scroll='true']
    try:
        cand = driver.find_elements(By.CSS_SELECTOR, SCROLL_CONTAINER_SEL)
        cand = [c for c in cand if _is_visible(driver, c)]
        if cand:
            best = None
            best_h = -1
            for c in cand:
                try:
                    ch = int(driver.execute_script("return arguments[0].clientHeight;", c) or 0)
                except:
                    ch = 0
                if ch > best_h:
                    best_h = ch
                    best = c
            if best:
                try:
                    driver.execute_script("arguments[0].focus({preventScroll:true});", best)
                except:
                    pass
                return best
    except:
        pass

    # 2) Фолбэк: эвристика
    try:
        container = driver.execute_script("""
            function isScrollable(el){
              if (!el) return false;
              const st = getComputedStyle(el);
              const oy = st.overflowY;
              return (oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 5;
            }
            const candSel = "[data-qa='reviews-list'], [data-qa='reviews'], [class*='review-list'], \
                             [class*='Reviews'], [class*='reviews'], article";
            const candidates = Array.from(document.querySelectorAll(candSel));
            for (const c of candidates){
              let el = c;
              for (let i=0; i<8 && el; i++){
                if (isScrollable(el)) return el;
                el = el.parentElement;
              }
            }
            let best = null, bestH = 0;
            Array.from(document.querySelectorAll('div,section,main,article')).forEach(el=>{
              if (isScrollable(el) && el.clientHeight > bestH){
                best = el; bestH = el.clientHeight;
              }
            });
            return best || document.scrollingElement || document.body;
        """)
        if container:
            return container
    except:
        pass
    return driver.execute_script("return document.scrollingElement || document.body;")

def _wheel_scroll_once(driver, container, dy: int):
    if ScrollOrigin is None:
        return False
    try:
        origin = ScrollOrigin.from_element(container, 0, 0)
        ActionChains(driver).scroll_from_origin(origin, 0, dy).perform()
        return True
    except Exception:
        return False

def autoscroll_burst(driver, container, ms: int):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", container)
        driver.execute_script("arguments[0].focus({preventScroll:true});", container)
    except:
        pass

    deadline = time.time() + (ms / 1000.0)
    step_ratio = 0.85

    while time.time() < deadline:
        try:
            top, h, ch = driver.execute_script(
                "return [arguments[0].scrollTop, arguments[0].scrollHeight, arguments[0].clientHeight];",
                container
            )
            step = max(200, int(ch * step_ratio))
            _ = _wheel_scroll_once(driver, container, step)  # wheel
            driver.execute_script(  # js
                "arguments[0].scrollTop = Math.min(arguments[0].scrollTop + arguments[1], arguments[0].scrollHeight);",
                container, step
            )
            try:
                driver.execute_script("arguments[0].dispatchEvent(new Event('scroll', {bubbles:true}));", container)
            except:
                pass
            new_top = driver.execute_script("return arguments[0].scrollTop;", container)
            if new_top + ch >= h - 4:
                try:
                    driver.execute_script(
                        "arguments[0].scrollTop = Math.max(0, arguments[0].scrollTop - Math.floor(arguments[0].clientHeight*0.35));",
                        container
                    )
                except:
                    pass
        except Exception:
            try:
                driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight*0.8));")
            except:
                pass

        time.sleep(0.25)

def get_scroll_height(driver, container) -> int:
    try:
        return int(driver.execute_script("return arguments[0].scrollHeight;", container) or 0)
    except:
        try:
            return int(driver.execute_script("return (document.scrollingElement||document.body).scrollHeight;") or 0)
        except:
            return 0
# ====== /СКРОЛЛЕР ======

def _rating_from_spans_count(card) -> Optional[float]:
    try:
        fill_elements = card.find_elements(By.CSS_SELECTOR, RATING_FILL_SEL)
        for fill in fill_elements:
            stars = fill.find_elements(By.TAG_NAME, "span")
            cnt = len(stars)
            if 1 <= cnt <= 5:
                return float(cnt)
    except:
        pass
    return None

def _looks_like_header(text: str, author: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    low = s.lower()
    if low.startswith(("официальный ответ", "ответ владельца")):
        return True
    if author and low.startswith(author.lower()):
        return True
    if re.search(r"\bотзыв(ов)?\b", low):
        return True
    return False

def normalize_review_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(Полезно.*|Читать целиком.*|Свернуть.*|Официальный ответ.*)$", "", s, flags=re.I)
    s = re.sub(r"([.!?…])\s*\d{1,3}$", r"\1", s)
    return s.strip()

def _get_text_by_selectors(card) -> str:
    # primary
    try:
        el = card.find_element(By.CSS_SELECTOR, TEXT_BLOCK_SEL)
        t = (el.text or "").strip()
        if t:
            return t
    except:
        pass
    # fallback
    try:
        el = card.find_element(By.CSS_SELECTOR, ALT_TEXT_SEL)
        t = (el.text or "").strip()
        if t:
            return t
    except:
        pass
    return ""

def find_review_text(card, author: str) -> str:
    tt = normalize_review_text(_get_text_by_selectors(card))
    if not tt:
        return ""
    low = tt.lower()
    if "официальный ответ" in low or "ответ владельца" in low:
        return ""
    if _looks_like_header(tt, author):
        return ""
    if len(tt) >= 2:
        return tt
    return ""

def extract_review_from_card(card, driver) -> dict:
    author = ""
    try:
        author_el = card.find_element(By.CSS_SELECTOR, AUTHOR_SEL)
        author = (author_el.get_attribute("title") or author_el.text or "").strip()
    except:
        pass

    date_raw, date_iso = "", ""
    try:
        # Берём дату из шапки карточки, не из "официального ответа"
        date_els = card.find_elements(
            By.XPATH,
            ".//div[contains(@class,'_m80g57y')]//div[contains(@class,'_a5f6uz')][not(ancestor::*[contains(@class,'_sgs1pz')])]"
        )
        if not date_els:
            # страховка — старый селектор, если разметка поменялась
            date_els = card.find_elements(By.CSS_SELECTOR, DATE_SEL)

        if date_els:
            date_raw = (date_els[0].text or "").strip()
            date_iso = parse_ru_date_to_iso(date_raw) or ""
            if not date_iso:
                try:
                    time_el = date_els[0].find_element(By.CSS_SELECTOR, "time")
                    dt = (time_el.get_attribute("datetime") or "").strip()
                    if dt:
                        date_iso = dt[:10]
                except:
                    pass
    except:
        pass

    rating = _rating_from_spans_count(card)
    text = find_review_text(card, author)
    text = re.sub(r"[\r\n]+", " ", text).strip()

    return {
        "author": author,
        "rating": rating,
        "date_raw": date_raw,
        "date_iso": date_iso,
        "text": text,
    }

def extract_organization(driver) -> str:
    # Фолбэк: попытка вытащить имя со страницы
    try:
        driver.switch_to.default_content()
    except:
        pass
    selectors = [
        "//h1", "//h2", "//h3",
        "//*[contains(@class, 'business-name')]",
        "//*[contains(@class, 'org-name')]",
        "//*[contains(@class, 'company-name')]"
    ]
    for xp in selectors:
        try:
            elements = driver.find_elements(By.XPATH, xp)
            for el in elements:
                t = el.text.strip()
                if 2 < len(t) < 100:
                    return t
        except:
            pass
    try:
        title = (driver.title or "").strip()
        for sep in [" — ", " – ", " - ", " | "]:
            if sep in title:
                return title.split(sep)[0].strip()
        return title
    except:
        return ""

def find_review_cards(driver):
    # Каждый отзыв — это div._1k5soqfl
    try:
        return driver.find_elements(By.CSS_SELECTOR, "div._1k5soqfl")
    except:
        return []

def _coarse_key(author: str, text: str) -> Tuple[str, str]:
    a = (author or "").strip().lower()
    t = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return (a, t)

def collect_visible_batch(driver, seen_unused: set, out: list, cutoff_date, dedupe_index: Dict[Tuple[str,str], int]) -> tuple[int, bool]:
    added, met_old = 0, False
    cards = find_review_cards(driver)
    for card in cards:
        try:
            item = extract_review_from_card(card, driver)
            txt = (item.get("text") or "").strip()
            if not txt or len(txt) < 2:
                continue

            ltxt = txt.lower()
            if "официальный ответ" in ltxt or "ответ владельца" in ltxt:
                continue

            d_iso = item.get("date_iso") or ""
            if ENFORCE_DATE_CUTOFF and not d_iso:
                continue
            if d_iso:
                try:
                    d = datetime.fromisoformat(d_iso[:10]).date()
                    if d < cutoff_date:
                        met_old = True
                        continue
                except Exception:
                    pass

            ckey = _coarse_key(item.get("author",""), txt)
            if ckey not in dedupe_index:
                dedupe_index[ckey] = len(out)
                out.append(item)
                added += 1
            else:
                idx = dedupe_index[ckey]
                cur = out[idx]
                if not (cur.get("date_iso") or cur.get("date_raw")) and (item.get("date_iso") or item.get("date_raw")):
                    cur["date_iso"] = item.get("date_iso") or cur.get("date_iso","")
                    cur["date_raw"] = item.get("date_raw") or cur.get("date_raw","")
                if cur.get("rating") is None and item.get("rating") is not None:
                    cur["rating"] = item["rating"]
        except Exception:
            continue
    return added, met_old

# >>> ДОБАВЛЕНО: сбор summary (rating_avg, ratings_count, reviews_count)
def _textnum_to_int(s: Optional[str]) -> Optional[int]:
    if not s: return None
    t = s.replace("\xa0", " ")
    m = re.search(r"(\d[\d\s]*)", t)
    if not m: return None
    try:
        return int(m.group(1).replace(" ", ""))
    except Exception:
        return None

def _text_to_float(s: Optional[str]) -> Optional[float]:
    if not s: return None
    t = s.strip().replace("\xa0", " ")
    m = re.search(r"(\d+[,\.\u202F]\d+|\d+)", t)
    if not m: return None
    try:
        return float(m.group(1).replace("\u202f", "").replace(",", "."))
    except Exception:
        return None
    
def _nz(v, zero=0):
    return v if v not in (None, "") else zero

def extract_summary_2gis(driver) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """
    rating_avg: div._1tam240
    ratings_count: div._1y88ofn  (например "4 оценки")
    reviews_count: строго span._1xhlznaa, который лежит ПОД div._qvsf7z
    """
    try:
        driver.switch_to.default_content()
    except:
        pass

    rating_avg = ratings_count = reviews_count = None

    # рейтинг
    try:
        el = driver.find_elements(By.CSS_SELECTOR, SUM_RATING_SEL)
        if el:
            rating_avg = _text_to_float(el[0].text)
    except:
        pass

    # всего оценок
    try:
        els = driver.find_elements(By.CSS_SELECTOR, SUM_RATINGS_COUNT_SEL)
        for e in els:
            n = _textnum_to_int(e.text)
            if n is not None:
                ratings_count = n
                break
    except:
        pass

    # всего отзывов — строго по пути div._qvsf7z > span._1xhlznaa
    try:
        el = driver.find_elements(By.CSS_SELECTOR, SUM_REVIEWS_COUNT_SEL)
        if el:
            reviews_count = _textnum_to_int(el[0].text)
    except:
        pass

    # Fallback через HTML с жёстким путём (если вдруг не нашлось визуально)
    if reviews_count is None:
        try:
            html = driver.page_source
            m = re.search(r'<div class="_qvsf7z"[^>]*>.*?<span class="_1xhlznaa">\s*([\d\s]+)\s*</span>', html, re.S)
            if m:
                reviews_count = int(m.group(1).replace("\u202f", "").replace(" ", ""))
        except:
            pass

    return rating_avg, ratings_count, reviews_count

def process_one_url(url: str, forced_org: Optional[str] = None, summary_writer: Optional[csv.DictWriter] = None) -> List[Dict]:
    driver = setup_driver()
    try:
        if not safe_get(driver, url):
            driver.quit(); driver = setup_driver()
            if not safe_get(driver, url):
                return []
        if not ensure_window(driver):
            driver.quit(); return []

        inject_perf_css(driver)
        time.sleep(3)

        # Организация
        org = forced_org or org_from_url(url) or ""
        if not org:
            try:
                org = extract_organization(driver) or ""
            except:
                org = ""

        # >>> ДОБАВЛЕНО: summary до переключений во фреймы/табы
        try:
            rating_avg, ratings_count, reviews_count = extract_summary_2gis(driver)
            if summary_writer is not None:
                summary_writer.writerow({
                    "organization": org,
                    "platform":     "2GIS",
                    "rating_avg":   _nz(rating_avg, 0),
                    "ratings_count":_nz(ratings_count, 0),
                    "reviews_count":_nz(reviews_count, 0),
                })
        except Exception:
            if summary_writer is not None:
                summary_writer.writerow({
                    "organization": org, "platform": "2GIS",
                    "rating_avg": 0, "ratings_count": 0, "reviews_count": 0
                })

        try: click_cookies_if_any(driver)
        except: pass
        try: ensure_reviews_tab(driver)
        except: pass

        switch_to_reviews_iframe(driver)
        wait_for_reviews_content(driver)

        container = get_scroll_container(driver)
        cutoff_date = datetime.now().date() - timedelta(days=365 * YEARS_LIMIT)

        results: List[Dict] = []
        dedupe_index: Dict[Tuple[str,str], int] = {}

        idle = 0
        stop_by_age = False

        added, met_old = collect_visible_batch(driver, set(), results, cutoff_date, dedupe_index)
        if met_old:
            stop_by_age = True

        for _ in range(BURSTS):
            if stop_by_age:
                break
            prev_len = len(results)
            prev_h = get_scroll_height(driver, container)

            autoscroll_burst(driver, container, BURST_MS)
            time.sleep(1.1)

            added, met_old = collect_visible_batch(driver, set(), results, cutoff_date, dedupe_index)
            if met_old:
                stop_by_age = True

            new_h = get_scroll_height(driver, container)
            height_grew = new_h > prev_h + 2

            idle = 0 if (added or len(results) > prev_len or height_grew) else (idle + 1)
            if idle >= IDLE_LIMIT:
                break

        for r in results:
            r["organization"] = org

        print(f"  Собрано: {len(results)} | org={org or '-'}")
        return results

    except (NoSuchWindowException, WebDriverException, TimeoutException):
        return []
    finally:
        try:
            driver.quit()
        except:
            pass

def main():
    try:
        urls = [u.strip() for u in Path(DGIS_URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
        if not urls:
            urls = [FALLBACK_URL]
    except FileNotFoundError:
        urls = [FALLBACK_URL]

    all_rows: List[Dict] = []

    # >>> ДОБАВЛЕНО: подготовка summary CSV
    Path(OUT_CSV_SUMMARY).parent.mkdir(parents=True, exist_ok=True)
    f_sum = open(OUT_CSV_SUMMARY, "w", newline="", encoding="utf-8")
    w_sum = csv.DictWriter(f_sum, fieldnames=["organization","platform","rating_avg","ratings_count","reviews_count"], quoting=csv.QUOTE_ALL)
    w_sum.writeheader()

    for i, url in enumerate(urls, 1):
        org_slug = org_from_url(url) or ""  # берём из карты по firm id
        print(f"[{i}/{len(urls)}] {url}  -> org='{org_slug or '-'}'")
        reviews = process_one_url(url, forced_org=org_slug, summary_writer=w_sum)  # <<< передаём writer
        all_rows.extend(reviews)

    try:
        f_sum.close()
    except:
        pass

    Path(OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["rating","author","date_iso","text","platform","organization"]
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in all_rows:
            w.writerow({
                "rating":       r.get("rating"),
                "author":       (r.get("author") or "").strip(),
                "date_iso":     (r.get("date_iso") or "")[:10],
                "text":         (r.get("text") or "").replace("\r"," ").replace("\n"," ").strip(),
                "platform":     "2GIS",
                "organization": (r.get("organization") or "").strip(),
            })
    print(f"Готово. Всего отзывов: {len(all_rows)}. CSV отзывов: {OUT_CSV}\nSummary: {OUT_CSV_SUMMARY}")

if __name__ == "__main__":
    main()
