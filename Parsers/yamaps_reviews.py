# -*- coding: utf-8 -*-
import csv
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, unquote

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchWindowException, WebDriverException, TimeoutException

# ===== вход =====
YAMAPS_URLS_FILE = "./Urls/yamaps_urls.txt"  # по одной ссылке на строку
FALLBACK_URL = ("https://yandex.ru/maps/org/avtolotsman/1694054504/reviews/"
                "?ll=44.957771%2C53.220474&mode=search&sll=44.986159%2C53.218956"
                "&sspn=0.086370%2C0.033325&tab=reviews&text=автолоцман&z=14")

# === УКАЖИ ПУТИ К БРАУЗЕРУ И ДРАЙВЕРУ ===
YANDEX_BROWSER_BINARY = "/Applications/Yandex.app/Contents/MacOS/Yandex" # поменять для windows
YANDEXDRIVER_PATH     = "drivers/yandexdriver" # поменять для windows

# === КУДА ПИСАТЬ CSV ===
OUT_CSV = "Csv/yamaps_reviews.csv"

# Параметры
WAIT_TIMEOUT   = 60
BURSTS         = 12      # сколько "рывков" автоскролла сделаем на страницу
BURST_MS       = 1200    # длительность одного рывка (мс)
IDLE_LIMIT     = 3       # сколько раз подряд можно не находить новых карточек прежде чем остановиться
YEARS_LIMIT    = 3       # ЛИМИТ возраста отзывов в годах

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RELATIVE_MAP = {"сегодня": 0, "вчера": -1}

def parse_rating(aria_label: str):
    if not aria_label:
        return None
    m = re.search(r"Оценка\s+([0-9]+(?:[.,][0-9]+)?)", aria_label, flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None

def parse_ru_date_to_iso(s: str):
    """Возвращает только дату 'YYYY-MM-DD' (None, если не распознали)."""
    if not s:
        return None
    s = s.strip().lower()
    if s in RELATIVE_MAP:
        d = datetime.now().date() + timedelta(days=RELATIVE_MAP[s])
        return d.isoformat()

    m = re.match(r"^(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?$", s, flags=re.I)
    if m:
        day = int(m.group(1))
        mon = MONTHS_RU.get(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        if mon:
            try:
                return datetime(year, mon, day).date().isoformat()
            except Exception:
                return None

    m2 = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if y < 100: y += 2000
        try:
            return datetime(y, mo, d).date().isoformat()
        except Exception:
            return None
    return None

def build_options() -> Options:
    opts = Options()
    opts.binary_location = YANDEX_BROWSER_BINARY
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.page_load_strategy = 'eager'
    # постоянный профиль: логин, куки, пройденные капчи сохраняются
    user_dir = str(Path.home() / ".yandex-scraper-profile")
    opts.add_argument(f"--user-data-dir={user_dir}")
    opts.add_argument("--profile-directory=Default")
    return opts

def setup_driver() -> webdriver.Chrome:
    service = Service(executable_path=YANDEXDRIVER_PATH)
    drv = webdriver.Chrome(service=service, options=build_options())
    drv.set_page_load_timeout(120)
    drv.set_script_timeout(120)
    drv.implicitly_wait(0)
    return drv

def ensure_window(drv: webdriver.Chrome) -> bool:
    try:
        return bool(drv.window_handles)
    except Exception:
        return False

def safe_get(drv: webdriver.Chrome, url: str) -> bool:
    try:
        drv.get(url)
        return True
    except (NoSuchWindowException, WebDriverException):
        return False

def get_scroll_container(driver):
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.business-review-view"))
    )
    first_review = driver.find_element(By.CSS_SELECTOR, "div.business-review-view")
    return driver.execute_script("""
        var el = arguments[0];
        function isScrollable(e){
            if(!e) return false;
            var s = getComputedStyle(e);
            return /(auto|scroll)/.test(s.overflowY);
        }
        while (el){
            if (isScrollable(el)) return el;
            el = el.parentElement;
        }
        return document.scrollingElement || document.body;
    """, first_review)

def inject_perf_css(driver):
    try:
        driver.execute_script("""
            if (!document.getElementById('no-anim-style')) {
              var st = document.createElement('style');
              st.id = 'no-anim-style';
              st.innerHTML = '*{animation:none!important;transition:none!important;} html{scroll-behavior:auto!important;}';
              document.head.appendChild(st);
            }
        """)
    except (NoSuchWindowException, WebDriverException):
        pass

def autoscroll_burst(driver, container, ms: int):
    try:
        driver.execute_async_script("""
            const box = arguments[0];
            const dur = arguments[1] | 0;
            const done = arguments[2];
            const step = () => {
                box.scrollTop = Math.min(box.scrollTop + box.clientHeight * 1.35, box.scrollHeight);
            };
            const t0 = performance.now();
            let rafId = 0;
            const tick = () => {
                step();
                if ((performance.now() - t0) < dur &&
                    (box.scrollTop + box.clientHeight + 4) < box.scrollHeight) {
                    rafId = requestAnimationFrame(tick);
                } else {
                    cancelAnimationFrame(rafId);
                    done(box.scrollTop);
                }
            };
            requestAnimationFrame(tick);
        """, container, ms)
    except (NoSuchWindowException, WebDriverException):
        pass

def expand_all_visible(driver, scope=None):
    root = scope if scope is not None else driver
    try:
        for b in root.find_elements(By.CSS_SELECTOR, "span.business-review-view__expand"):
            try:
                driver.execute_script("arguments[0].click();", b)
            except Exception:
                pass
    except Exception:
        pass

# --- сортировка: «По новизне»
def set_sort_newest_yamaps(driver, attempts: int = 3) -> bool:
    def _open():
        try:
            btn = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "div.rating-ranking-view"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            driver.execute_script("arguments[0].click();", btn)
            return btn
        except Exception:
            return None

    def _pick():
        xps = [
            "//*[normalize-space(text())='По новизне']",
            "//*[@role='menuitem' or @role='option'][normalize-space(.)='По новизне']",
            "//div[contains(@class,'menu') or contains(@class,'popup')]//*[normalize-space(text())='По новизне']",
        ]
        for xp in xps:
            try:
                el = WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.XPATH, xp)))
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                try:
                    driver.execute_script("arguments[0].click();", el)
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
                    except Exception:
                        continue
                return True
            except Exception:
                continue
        return False

    def _ok():
        try:
            WebDriverWait(driver, 6).until(
                EC.text_to_be_present_in_element(
                    (By.CSS_SELECTOR, "div.rating-ranking-view span"), "По новизне"
                )
            )
            return True
        except Exception:
            try:
                txts = driver.execute_script("""
                    var b = document.querySelector('div.rating-ranking-view');
                    if(!b) return '';
                    return Array.from(b.querySelectorAll('span')).map(s=>s.textContent.trim()).join(' ');
                """)
                return "По новизне" in (txts or "")
            except Exception:
                return False

    for _ in range(attempts):
        btn = _open()
        if not btn:
            continue
        if not _pick():
            continue
        if _ok():
            return True
    return False

def extract_review(review_el, driver):
    author = ""
    try:
        author = review_el.find_element(By.CSS_SELECTOR, 'a.business-review-view__link span[itemprop="name"]').text.strip()
    except Exception:
        try:
            author = review_el.find_element(By.CSS_SELECTOR, "span[itemprop='name']").text.strip()
        except Exception:
            pass

    rating = None
    try:
        rating_el = review_el.find_element(By.CSS_SELECTOR, "div.business-rating-badge-view__stars")
        rating = parse_rating(rating_el.get_attribute("aria-label") or "")
    except Exception:
        pass

    date_raw, date_iso = "", None
    try:
        date_raw = review_el.find_element(By.CSS_SELECTOR, "span.business-review-view__date span").text.strip()
        date_iso = parse_ru_date_to_iso(date_raw)
    except Exception:
        pass

    text = ""
    try:
        text = review_el.find_element(By.CSS_SELECTOR, "div.spoiler-view__text span.spoiler-view__text-container").text.strip()
    except Exception:
        try:
            text = review_el.find_element(By.CSS_SELECTOR, "[itemprop='reviewBody'], .business-review-view__text").text.strip()
        except Exception:
            pass

    return {"author": author, "rating": rating, "date_raw": date_raw, "date_iso": date_iso, "text": text}

def collect_visible_batch(driver, seen: set, out: list, cutoff_date) -> tuple[int, bool]:
    """
    Собираем видимые карточки (только с НЕпустым текстом).
    Возвращаем (сколько добавили, встретили_старый_отзыв_bool).
    Добавляем только те, у которых дата >= cutoff_date.
    """
    added = 0
    met_old = False
    cards = driver.find_elements(By.CSS_SELECTOR, "div.business-review-view")
    for c in cards:
        try:
            expand_all_visible(driver, c)
            item = extract_review(c, driver)

            # обязательно наличие текста
            if not (item.get("text") or "").strip():
                continue

            # дата обязательна
            d_iso = item.get("date_iso")
            if not d_iso:
                continue
            try:
                d = datetime.fromisoformat(d_iso[:10]).date()
            except Exception:
                continue

            if d < cutoff_date:
                met_old = True
                continue  # старые НЕ добавляем

            key = (item["author"], item["date_raw"], (item["text"] or "")[:80])
            if key not in seen:
                seen.add(key)
                out.append(item)
                added += 1
        except Exception:
            pass
    return added, met_old

def extract_organization_from_url(url: str) -> str:
    try:
        path = urlparse(url).path  # /maps/org/avtolotsman/1694054504/reviews/
        m = re.search(r"/org/([^/]+)/", path)
        if m:
            return unquote(m.group(1))
    except Exception:
        pass
    return ""

def process_one_url(url: str) -> list[dict]:
    """
    Возвращает список отзывов по ссылке (с учётом фильтра по дате), уже
    дополненных полем 'organization'.
    Каждый URL обрабатываем в отдельной сессии браузера для устойчивости.
    """
    driver = setup_driver()
    try:
        if not safe_get(driver, url):
            driver.quit()
            driver = setup_driver()
            if not safe_get(driver, url):
                return []

        # иногда окно падает на загрузке — проверим
        if not ensure_window(driver):
            driver.quit()
            return []

        # ждём хотя бы одну карточку
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.business-review-view"))
        )

        inject_perf_css(driver)

        # организация
        current = driver.current_url or url
        organization = extract_organization_from_url(current)

        # сортировка «По новизне»
        set_sort_newest_yamaps(driver)

        container = get_scroll_container(driver)

        cutoff_date = datetime.now().date() - timedelta(days=365*YEARS_LIMIT)

        seen, results = set(), []
        idle = 0
        stop_by_age = False

        expand_all_visible(driver)
        _, met_old = collect_visible_batch(driver, seen, results, cutoff_date)
        if met_old:
            stop_by_age = True

        for _ in range(BURSTS):
            if stop_by_age:
                break
            prev_len = len(results)
            autoscroll_burst(driver, container, BURST_MS)
            expand_all_visible(driver)
            added, met_old = collect_visible_batch(driver, seen, results, cutoff_date)
            if met_old:
                stop_by_age = True

            if added == 0 and len(results) == prev_len:
                idle += 1
            else:
                idle = 0
            if idle >= IDLE_LIMIT:
                break

        # приклеим organization
        for r in results:
            r["organization"] = organization

        print(f"  собрано: {len(results)} | org={organization or '-'}")
        return results

    except (NoSuchWindowException, WebDriverException, TimeoutException):
        return []
    finally:
        try:
            driver.quit()
        except Exception:
            pass

def main():
    # читаем список ссылок; если файла нет — используем fallback
    try:
        urls = [u.strip() for u in Path(YAMAPS_URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
        if not urls:
            urls = [FALLBACK_URL]
    except FileNotFoundError:
        urls = [FALLBACK_URL]

    all_rows = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}")
        all_rows.extend(process_one_url(url))

    # -------- CSV: "rating","author","date_iso","text","platform","organization"
    Path(OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["rating","author","date_iso","text","platform","organization"]
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in all_rows:
            date_iso = (r.get("date_iso") or "")
            row = {
                "rating":       r.get("rating"),
                "author":       (r.get("author") or "").strip(),
                "date_iso":     date_iso[:10],
                "text":         (r.get("text") or "").replace("\r", " ").replace("\n", " ").strip(),
                "platform":     "Yandex Maps",
                "organization": (r.get("organization") or "").strip(),
            }
            w.writerow(row)

    print(f"Готово. Всего строк: {len(all_rows)}. CSV: {OUT_CSV}")

if __name__ == "__main__":
    main()
