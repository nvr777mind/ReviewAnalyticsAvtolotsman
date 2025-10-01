# -*- coding: utf-8 -*-
"""
2ГИС → CSV: rating,author,date_iso,text,platform,organization
Запуск: Яндекс.Браузер + yandexdriver (macOS). Python 3.9.
"""

import csv, re, time
from typing import Optional, Tuple, List, Set, Dict
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchWindowException, WebDriverException, TimeoutException, StaleElementReferenceException

# ===== ВХОД =====
DGIS_URLS_FILE = "./Urls/2gis_urls.txt"
FALLBACK_URL = ("https://2gis.ru/penza/search/%D0%B0%D0%B2%D1%82%D0%BE%D0%BB%D0%BE%D1%86%D0%BC%D0%B0%D0%BD/"
                "firm/70000001057701394/44.973806%2C53.220685/tab/reviews?m=44.975027%2C53.220456%2F17.63")

# ===== ЯНДЕКС-БРАУЗЕР (macOS) =====
YANDEX_BROWSER_BINARY = "/Applications/Yandex.app/Contents/MacOS/Yandex"
YANDEXDRIVER_PATH     = "drivers/yandexdriver"
PROFILE_DIR           = str(Path.home() / ".yandex-2gis-scraper")

# ===== ВЫХОД =====
OUT_CSV = "Csv/2gis_reviews.csv"

# ===== ПАРАМЕТРЫ =====
WAIT_TIMEOUT        = 60
BURSTS              = 18
BURST_MS            = 1100
IDLE_LIMIT          = 3
YEARS_LIMIT         = 3
ENFORCE_DATE_CUTOFF = False

# ===== 2ГИС СЕЛЕКТОРЫ =====
AUTHOR_SEL       = "span._16s5yj36"          # автор (в title или text)
DATE_SEL         = "div._a5f6uz"             # дата
RATING_FILL_SEL  = "div._1fkin5c"            # контейнер со span-звёздами
TEXT_BLOCK_SEL   = "a._1wlx08h"              # основной текст отзыва
ALT_TEXT_SEL     = ["a._1msln3t"]            # альтернативный селектор текста

MONTHS_RU = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
             "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}
RELATIVE_MAP = {"сегодня": 0, "вчера": -1}

def parse_ru_date_to_iso(s: Optional[str]) -> Optional[str]:
    if not s: return None
    s = s.strip().lower()
    if s in RELATIVE_MAP:
        d = datetime.now().date() + timedelta(days=RELATIVE_MAP[s])
        return d.isoformat()
    m = re.match(r"^(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?$", s, flags=re.I)
    if m:
        day = int(m.group(1)); mon = MONTHS_RU.get(m.group(2)); year = int(m.group(3)) if m.group(3) else datetime.now().year
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
    except: pass
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
            print("[DBG] cookie banner dismissed")
            break
        except: pass

def probe_dom(driver, note=""):
    try:
        Path("Debug").mkdir(exist_ok=True)
        html_path = "Debug/2gis_page.html"
        png_path  = "Debug/2gis_page.png"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        driver.get_screenshot_as_file(png_path)
    except Exception:
        pass

def ensure_reviews_tab(driver):
    """Кликаем по табу 'Отзывы' (прямой линк предпочтительнее)."""
    # Сначала — точный линк на вкладку
    try:
        a = driver.find_element(By.CSS_SELECTOR, "a[href*='/tab/reviews']")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", a)
        driver.execute_script("arguments[0].click();", a)
        time.sleep(1.5)
        return
    except: pass
    # Фолбэк — по тексту
    try:
        tabs = driver.find_elements(By.XPATH, "//*[self::a or self::span or self::div][contains(., 'Отзывы')]")
        for t in tabs[:3]:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
                driver.execute_script("arguments[0].click();", t)
                time.sleep(1.5)
                break
            except: pass
    except: pass

def switch_to_reviews_iframe(driver) -> bool:
    """2ГИС обычно без iframe, оставляем мягкий поиск — возвращаем False если не нашли."""
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in iframes:
            try:
                driver.switch_to.frame(frame)
                time.sleep(0.5)
                if driver.find_elements(By.CSS_SELECTOR, "div._1k5soqfl, div._49x36f a._1wlx08h, div._49x36f a._1msln3t"):
                    return True
                driver.switch_to.default_content()
            except:
                driver.switch_to.default_content()
        return False
    except:
        return False

def wait_for_reviews_content(driver):
    """Ожидание появления хотя бы одного блока отзыва."""
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "div._1k5soqfl, div._49x36f a._1wlx08h, div._49x36f a._1msln3t")) > 0
    )

# === FIX: точное определение скролл-контейнера по вашему HTML ===
def get_scroll_container(driver, timeout: int = 15):
    """
    Возвращает внутренний скролл-контейнер вкладки «Отзывы»:
    1) точный селектор: div._1rkbbi0x[data-scroll='true']
    2) любой [data-scroll='true'], содержащий карточки отзывов
    3) предок карточек с overflowY=auto|scroll и реальным скроллом
    """
    def _find():
        return driver.execute_script("""
            const isScrollable = el => !!el && (el.scrollHeight > el.clientHeight) &&
                                       ['auto','scroll'].includes(getComputedStyle(el).overflowY);

            // 1) Точный селектор из вашего фрагмента
            let el = document.querySelector('div._1rkbbi0x[data-scroll="true"]');
            if (isScrollable(el)) return el;

            // 2) Любой data-scroll=true, но чтобы внутри были отзывы
            const candidates = Array.from(document.querySelectorAll('[data-scroll="true"]'));
            for (const c of candidates) {
              if ((c.querySelector('div._1k5soqfl') || c.querySelector('div._49x36f a._1wlx08h, div._49x36f a._1msln3t')) && isScrollable(c)) {
                return c;
              }
            }

            // 3) Предок карточек с реальным скроллом
            const cards = document.querySelectorAll('div._1k5soqfl, div._49x36f');
            for (const card of cards) {
              let p = card;
              for (let i=0; i<10 && p; i++) {
                if (isScrollable(p)) return p;
                p = p.parentElement;
              }
            }
            return null;
        """)

    end = time.time() + timeout
    el = _find()
    while not el and time.time() < end:
        time.sleep(0.3)
        el = _find()

    if not el:
        # Фолбэк — body (нежелателен, но лучше, чем ничего)
        el = driver.execute_script("return document.scrollingElement || document.body;")

    # Подсветим для отладки и фокуснём
    try:
        driver.execute_script("arguments[0].style.outline='3px solid red'; arguments[0].focus && arguments[0].focus();", el)
    except: pass
    return el
# === /FIX ===

def autoscroll_burst(driver, container, ms: int):
    driver.execute_script("""
        const box = arguments[0];
        const dur = arguments[1];
        const t0 = performance.now();
        function step(){
          if (performance.now() - t0 < dur) {
            box.scrollTop = Math.min(box.scrollTop + Math.floor(box.clientHeight*0.9), box.scrollHeight);
            setTimeout(step, 150);
          }
        }
        step();
    """, container, ms)

def click_load_more_if_any(driver) -> bool:
    try:
        buttons = driver.find_elements(By.XPATH, "//*[contains(., 'Загрузить ещё') or contains(., 'Показать ещё') or contains(., 'Ещё отзывы')]")
        for btn in buttons:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1)
                return True
            except: continue
        return False
    except:
        return False

def click_expanders_if_any(driver) -> int:
    """Разворачиваем 'Читать целиком' в пределах видимости (необязательно, но полезно)."""
    count = 0
    try:
        expands = driver.find_elements(By.XPATH, "//span[contains(., 'Читать целиком')]")
        for e in expands:
            try:
                driver.execute_script("arguments[0].click();", e)
                count += 1
            except: pass
    except: pass
    return count

def _rating_from_spans_count(card) -> Optional[float]:
    fill_elements = card.find_elements(By.CSS_SELECTOR, RATING_FILL_SEL)
    for fill in fill_elements:
        stars = fill.find_elements(By.TAG_NAME, "span")
        cnt = len(stars)
        if 1 <= cnt <= 5:
            return float(cnt)
    return None

def find_review_text(card):
    # 1) Основной селектор
    try:
        text_el = card.find_element(By.CSS_SELECTOR, TEXT_BLOCK_SEL)
        text = text_el.text.strip()
        if text and len(text) > 10:
            return text
    except: pass
    # 2) Альтернатива
    for sel in ALT_TEXT_SEL:
        try:
            text_el = card.find_element(By.CSS_SELECTOR, sel)
            text = text_el.text.strip()
            if text and len(text) > 10 and not text.startswith('Полезно'):
                return text
        except: continue
    # 3) Хейуристика (длинные тексты, без служебных слов)
    try:
        all_elements = card.find_elements(By.CSS_SELECTOR, "div, p, span")
        for el in all_elements:
            text = el.text.strip()
            if 20 <= len(text) <= 2000 and not any(x in text for x in ['Полезно', 'Читать целиком', 'Официальный ответ']):
                return text
    except: pass
    return ""

def extract_review_from_card(card, driver) -> dict:
    # AUTHOR
    author = ""
    try:
        author_el = card.find_element(By.CSS_SELECTOR, AUTHOR_SEL)
        author = (author_el.get_attribute("title") or author_el.text or "").strip()
    except: pass

    # DATE
    date_raw, date_iso = "", ""
    try:
        date_el = card.find_element(By.CSS_SELECTOR, DATE_SEL)
        date_raw = (date_el.text or "").strip()
        date_iso = parse_ru_date_to_iso(date_raw) or ""
    except: pass

    # RATING
    rating = _rating_from_spans_count(card)

    # TEXT
    text = find_review_text(card)
    text = re.sub(r"[\r\n]+", " ", text).strip()

    return {"author": author, "rating": rating, "date_raw": date_raw, "date_iso": date_iso, "text": text}

def extract_organization(driver) -> str:
    try:
        driver.switch_to.default_content()
    except: pass
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
        except: pass
    try:
        title = (driver.title or "").strip()
        for sep in [" — ", " – ", " - ", " | "]:
            if sep in title: 
                return title.split(sep)[0].strip()
        return title
    except: 
        return ""

def find_review_cards(driver):
    cards = []
    # 1) Основные контейнеры
    try:
        main = driver.find_elements(By.CSS_SELECTOR, "article, [data-qa='review-card'], ._49x36f")
        cards.extend(main)
    except: pass
    # 2) По дате
    try:
        date_elements = driver.find_elements(By.CSS_SELECTOR, DATE_SEL)
        for date_el in date_elements:
            try:
                parent = date_el
                for _ in range(5):
                    parent = parent.find_element(By.XPATH, "./..")
                if parent not in cards:
                    cards.append(parent)
            except: continue
    except: pass
    # 3) По рейтингу
    try:
        rating_elements = driver.find_elements(By.CSS_SELECTOR, RATING_FILL_SEL)
        for rating_el in rating_elements:
            try:
                parent = rating_el
                for _ in range(4):
                    parent = parent.find_element(By.XPATH, "./..")
                if parent not in cards:
                    cards.append(parent)
            except: continue
    except: pass
    return cards

def collect_visible_batch(driver, seen: set, out: list, cutoff_date) -> tuple[int, bool]:
    added, met_old = 0, False
    cards = find_review_cards(driver)
    for card in cards:
        try:
            item = extract_review_from_card(card, driver)
            txt = (item.get("text") or "").strip()
            if not txt or len(txt) < 20:
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
            key = (item.get("author", "").strip(), item.get("date_raw", "").strip(), txt[:80])
            if key not in seen:
                seen.add(key)
                out.append(item)
                added += 1
        except Exception:
            continue
    return added, met_old

def process_one_url(url: str) -> List[Dict]:
    driver = setup_driver()
    try:
        if not safe_get(driver, url):
            driver.quit(); driver = setup_driver()
            if not safe_get(driver, url): return []
        if not ensure_window(driver):
            driver.quit(); return []

        inject_perf_css(driver)
        time.sleep(2)

        probe_dom(driver, "A: initial")

        org = extract_organization(driver) or ""

        try: click_cookies_if_any(driver)
        except: pass

        try: ensure_reviews_tab(driver)
        except: pass

        # Обычно без iframe; мягкая попытка:
        _ = switch_to_reviews_iframe(driver)
        # Важно: возвращаемся в основной контекст
        try: driver.switch_to.default_content()
        except: pass

        wait_for_reviews_content(driver)
        probe_dom(driver, "B: reviews visible")

        # === FIX: берём корректный скролл-контейнер ===
        container = get_scroll_container(driver)
        cutoff_date = datetime.now().date() - timedelta(days=365 * YEARS_LIMIT)

        seen: Set[Tuple[str,str,str]] = set()
        results: List[Dict] = []
        idle = 0
        stop_by_age = False

        # Первый сбор + разворот «Читать целиком»
        try: click_expanders_if_any(driver)
        except: pass
        added, met_old = collect_visible_batch(driver, seen, results, cutoff_date)
        if met_old: stop_by_age = True

        # Цикл скроллинга
        for i in range(BURSTS):
            if stop_by_age:
                break

            prev_len = len(results)
            clicked = click_load_more_if_any(driver)

            # === FIX: контейнер может пересоздаться — переопределяем при необходимости ===
            try:
                autoscroll_burst(driver, container, BURST_MS)
            except (StaleElementReferenceException, WebDriverException):
                container = get_scroll_container(driver)
                autoscroll_burst(driver, container, BURST_MS)

            time.sleep(1.0)

            try: click_expanders_if_any(driver)
            except: pass

            added, met_old = collect_visible_batch(driver, seen, results, cutoff_date)
            if met_old: stop_by_age = True

            if added == 0 and len(results) == prev_len and not clicked:
                idle += 1
            else:
                idle = 0
            if idle >= IDLE_LIMIT:
                break

        for r in results:
            r["organization"] = org

        print(f"  Собрано: {len(results)} | org={org or '-'}")
        return results

    except (NoSuchWindowException, WebDriverException, TimeoutException):
        return []
    finally:
        try: driver.quit()
        except: pass

def main():
    try:
        urls = [u.strip() for u in Path(DGIS_URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
        if not urls: urls = [FALLBACK_URL]
    except FileNotFoundError:
        urls = [FALLBACK_URL]

    all_rows: List[Dict] = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}")
        reviews = process_one_url(url)
        all_rows.extend(reviews)

    # Сохранение результатов
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
    print(f"Готово. Всего строк: {len(all_rows)}. CSV: {OUT_CSV}")

if __name__ == "__main__":
    main()
