# -*- coding: utf-8 -*-

"""
Инкрементальный сбор отзывов с Яндекс.Карт:
- Берём "пороговую" дату из Csv/Reviews/all_reviews.csv (по платформе 'Yandex Maps' и организации).
- Для каждой ссылки из Urls/yamaps_urls.txt:
    * открываем страницу,
    * собираем summary (rating_avg, ratings_count, reviews_count),
    * сортируем «По новизне»,
    * скроллим и собираем отзывы СТРОГО НОВЕЕ пороговой даты,
    * как только встретился отзыв с датой <= пороговой — останавливаемся.
- Новые отзывы пишем в Csv/Reviews/yamaps_new_since.csv.
- Новый summary пишем в Csv/Summary/yamaps_summary_new.csv.
"""

import csv
import re
from datetime import datetime, timedelta, date
from pathlib import Path
from urllib.parse import urlparse, unquote

import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchWindowException, WebDriverException, TimeoutException

from typing import Optional, Dict

import os, sys, platform
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# ===================== НАСТРОЙКИ =====================
# Вход
IN_ALL_REVIEWS_CSV   = "Csv/Reviews/all_reviews.csv"   # общий пул ваших отзывов
YAMAPS_URLS_FILE     = "Urls/yamaps_urls.txt"          # по одной ссылке на строку

# Браузер/драйвер (под macOS; поменяйте при необходимости под Windows/Linux)
YANDEXDRIVER_PATH     = "drivers/Windows/yandexdriver.exe"

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

# ---- инициализация Selenium ----
yb = find_yandex_browser()

# Выход
OUT_CSV_DELTA         = "Csv/Reviews/NewReviews/yamaps_new_since.csv"
OUT_CSV_SUMMARY_NEW   = "Csv/Summary/NewSummary/yamaps_summary_new.csv"

# Параметры
WAIT_TIMEOUT   = 60
BURSTS         = 12       # число «рывков» автоскролла
BURST_MS       = 1200     # длительность рывка, мс
IDLE_LIMIT     = 3        # сколько раз подряд не прибавилось карточек — тогда выходим
ONLY_WITH_TEXT = True     # собирать только отзывы с непустым текстом
PLATFORM       = "Yandex Maps"

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RELATIVE_MAP = {"сегодня": 0, "вчера": -1}
# =====================================================


# --------------------- Вспомогательные парсеры ---------------------
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


def parse_ru_date_to_iso(s: Optional[str]) -> Optional[str]:
    """Возвращает дату в формате YYYY-MM-DD (строка) или None."""
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
                return date(year, mon, day).isoformat()
            except Exception:
                return None

    m2 = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d).isoformat()
        except Exception:
            return None
    return None


def extract_organization_from_url(url: str) -> str:
    """Извлекаем «организацию» из URL вида /maps/org/<orgname>/<id>/reviews/"""
    try:
        path = urlparse(url).path
        m = re.search(r"/org/([^/]+)/", path)
        if m:
            return unquote(m.group(1))
    except Exception:
        pass
    return ""


# --------------------- Работа с CSV (all_reviews.csv) ---------------------
def _try_parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = s.strip()[:10]
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        pass
    # Попробуем dd.mm.yyyy
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
    """
    Читает общий CSV и возвращает словарь:
        { organization: максимальная_дата_на_этой_платформе }
    Ожидаемые колонки: platform, organization, date_iso (или date/dateISO).
    Неупавшие строки без нужных полей игнорируются.
    """
    latest: Dict[str, date] = {}
    p = Path(all_reviews_csv)
    if not p.exists():
        return latest

    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        date_cols_priority = ["date_iso", "dateISO", "date", "Date", "DATE"]
        for row in reader:
            try:
                if (row.get("platform") or "").strip() != platform:
                    continue
                org = (row.get("organization") or "").strip()
                if not org:
                    continue
                d_str = None
                for dc in date_cols_priority:
                    if dc in row and row[dc]:
                        d_str = row[dc]
                        break
                d = _try_parse_date(d_str)
                if not d:
                    continue
                prev = latest.get(org)
                if (prev is None) or (d > prev):
                    latest[org] = d
            except Exception:
                continue
    return latest


# --------------------- SUMMARY утилиты ---------------------
def _num_from_text(text: Optional[str]):
    if not text:
        return None
    t = text.replace("\xa0", " ")
    m = re.search(r"(\d[\d\s]*)", t)
    if not m:
        return None
    try:
        return int(m.group(1).replace(" ", ""))
    except ValueError:
        return None


def _float_from_text(text: Optional[str]):
    if not text:
        return None
    t = text.replace("\xa0", " ")
    m = re.search(r"(\d+[,\.\u202F]\d+)", t)
    if m:
        try:
            return float(m.group(1).replace("\u202f", "").replace(",", "."))
        except ValueError:
            pass
    m2 = re.search(r"(?<!\d)(\d)(?![\d,\.])", t)  # иногда просто «4»
    if m2:
        return float(m2.group(1))
    return None


def extract_summary(driver):
    """
    Возвращает (rating_avg, ratings_count, reviews_count) с текущей страницы.
    Пробует явные селекторы, затем фоллбеки и regex по HTML.
    """
    rating_avg = None
    ratings_count = None
    reviews_count = None

    try:
        r_block = driver.find_elements(By.CSS_SELECTOR, "div.business-summary-rating-badge-view__rating")
        if r_block:
            rating_avg = _float_from_text(getattr(r_block[0], "inner_text", "") or r_block[0].text)

        r_span = driver.find_elements(By.CSS_SELECTOR, "span.business-rating-amount-view._summary")
        if r_span:
            ratings_count = _num_from_text(r_span[0].text)

        h2 = driver.find_elements(By.CSS_SELECTOR, "h2.card-section-header__title._wide")
        if h2:
            reviews_count = _num_from_text(h2[0].text)
    except Exception:
        pass

    if reviews_count is None:
        try:
            h2_any = driver.find_elements(By.CSS_SELECTOR, "h2.card-section-header__title, h2[class*='card-section-header__title']")
            for el in h2_any:
                t = (el.text or "").lower()
                if "отзыв" in t:
                    reviews_count = _num_from_text(el.text)
                    if reviews_count:
                        break
        except Exception:
            pass

    if reviews_count is None:
        try:
            candidates = driver.find_elements(By.XPATH, "//*[self::a or self::div or self::span][contains(translate(., 'ОТЗЫВЫ', 'отзывы'), 'отзывы')]")
            for el in candidates:
                txt = (el.text or "").replace("\xa0", " ").strip()
                m = re.search(r"отзыв[а-я]*[^0-9]*([\d\s]+)", txt, flags=re.I)
                if not m:
                    m = re.search(r"Отзывы[^0-9]*([\d\s]+)", txt, flags=re.I)
                if m:
                    try:
                        reviews_count = int(m.group(1).replace(" ", ""))
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    html = driver.page_source
    if rating_avg is None:
        m = re.search(r'Рейтинг[^0-9]*?(\d+[,\.\u202F]\d+)', html)
        if m:
            rating_avg = float(m.group(1).replace("\u202f", "").replace(",", "."))
        else:
            m2 = re.search(
                r'business-summary-rating-badge-view__rating-text">(\d)</span>.*?_separator.*?</span>.*?business-summary-rating-badge-view__rating-text">(\d)',
                html, re.S
            )
            if m2:
                rating_avg = float(f"{m2.group(1)}.{m2.group(2)}")

    if ratings_count is None:
        m = re.search(r'class="business-rating-amount-view _summary"[^>]*>\s*([\d\s]+)\s+оцен', html, re.I)
        if m:
            try:
                ratings_count = int(m.group(1).replace(" ", ""))
            except Exception:
                pass

    if reviews_count is None:
        m = re.search(r'>\s*([\d\s]+)\s+отзыв(?:ов|а)?\s*<', html, re.I)
        if not m:
            m = re.search(r'Отзывы[^0-9]{0,12}([\d\s]+)<', html, re.I)
        if not m:
            m = re.search(r'data-qa="reviews-count"[^>]*>\s*([\d\s]+)\s*<', html, re.I)
        if m:
            try:
                reviews_count = int(m.group(1).replace("\u202f", "").replace(" ", ""))
            except Exception:
                pass

    return rating_avg, ratings_count, reviews_count


# --------------------- Selenium утилиты ---------------------
def build_options() -> Options:
    opts = Options()
    opts.binary_location = str(yb)
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.page_load_strategy = "eager"
    # Важно: если сталкиваетесь с конфликтом профиля, удалите следующие две строки
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
    except Exception:
        pass


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
        if not _open():
            continue
        if not _pick():
            continue
        if _ok():
            return True
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
    except Exception:
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


# --------------------- Извлечение карточек ---------------------
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

    return {
        "author": author,
        "rating": rating,
        "date_raw": date_raw,
        "date_iso": date_iso,
        "text": text,
    }


def collect_visible_delta(driver, seen: set, out: list, strictly_newer_than: date) -> tuple:
    """
    Собираем видимые карточки.
    Возвращает (сколько_добавили, встретили_не_новее_пороговой_bool).
    Добавляем только те, у которых дата > strictly_newer_than.
    Как только встречаем дату <= пороговой — отмечаем и НЕ добавляем её.
    """
    added = 0
    met_not_newer = False

    cards = driver.find_elements(By.CSS_SELECTOR, "div.business-review-view")
    for c in cards:
        try:
            expand_all_visible(driver, c)
            item = extract_review(c, driver)

            d_iso = item.get("date_iso")
            if not d_iso:
                continue
            try:
                d = datetime.fromisoformat(d_iso[:10]).date()
            except Exception:
                continue

            if d <= strictly_newer_than:
                met_not_newer = True
                continue

            if ONLY_WITH_TEXT and not (item.get("text") or "").strip():
                continue

            key = (item.get("author") or "", item.get("date_raw") or "", (item.get("text") or "")[:80])
            if key not in seen:
                seen.add(key)
                out.append(item)
                added += 1
        except Exception:
            pass

    return added, met_not_newer


# --------------------- Основной цикл ---------------------
def main():
    # 1) Загружаем «последние даты» по организациям из общего CSV
    latest_by_org = load_latest_dates_by_org(IN_ALL_REVIEWS_CSV, PLATFORM)
    if latest_by_org:
        print(f"[INFO] Найдены последние даты по {len(latest_by_org)} организациям в '{IN_ALL_REVIEWS_CSV}'.")
    else:
        print(f"[INFO] '{IN_ALL_REVIEWS_CSV}' не найден или пуст — будем собирать всё, что есть (порог = 1900-01-01).")

    # 2) Список ссылок
    try:
        urls = [u.strip() for u in Path(YAMAPS_URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
    except FileNotFoundError:
        print(f"[WARN] Файл ссылок '{YAMAPS_URLS_FILE}' не найден.")
        urls = []

    if not urls:
        print("[ERROR] Нет входных ссылок для обработки.")
        return

    # Подготовка выходных CSV
    Path(OUT_CSV_DELTA).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_CSV_SUMMARY_NEW).parent.mkdir(parents=True, exist_ok=True)

    out_f_reviews = open(OUT_CSV_DELTA, "w", encoding="utf-8", newline="")
    out_f_summary = open(OUT_CSV_SUMMARY_NEW, "w", encoding="utf-8", newline="")

    reviews_writer = csv.DictWriter(
        out_f_reviews,
        fieldnames=["rating", "author", "date_iso", "text", "platform", "organization"],
        quoting=csv.QUOTE_ALL,
    )
    reviews_writer.writeheader()

    summary_writer = csv.DictWriter(
        out_f_summary,
        fieldnames=["organization", "platform", "rating_avg", "ratings_count", "reviews_count"],
        quoting=csv.QUOTE_ALL,
    )
    summary_writer.writeheader()

    driver = setup_driver()
    try:
        for i, url in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] {url}")
            if not safe_get(driver, url) or not ensure_window(driver):
                print("  [SKIP] Не удалось открыть окно/URL.")
                continue

            # ожидаем шапку или любую карточку
            try:
                WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.orgpage-header-view, div.business-review-view"))
                )
            except TimeoutException:
                print("  [SKIP] Страница не загрузилась.")
                continue

            organization = extract_organization_from_url(driver.current_url or url) or ""
            threshold = latest_by_org.get(organization, date(1900, 1, 1))

            print(f"  Организация: {organization or '-'} | Пороговая дата (последняя в all_reviews): {threshold.isoformat()}")

            # --- SUMMARY (до перехода к отзывам/скроллу)
            try:
                # Убедимся, что вкладка «Отзывы» открыта (там видны нужные блоки)
                try:
                    h2 = driver.find_elements(By.CSS_SELECTOR, "h2.card-section-header__title._wide")
                    if not h2:
                        tabs = driver.find_elements(By.CSS_SELECTOR, '[role="tablist"] [role="tab"]')
                        for t in tabs:
                            if "отзыв" in (t.text or "").lower():
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
                                driver.execute_script("arguments[0].click();", t)
                                WebDriverWait(driver, 8).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.business-review-view"))
                                )
                                break
                except Exception:
                    pass

                rating_avg, ratings_count, reviews_count = extract_summary(driver)
            except Exception:
                rating_avg = ratings_count = reviews_count = None

            summary_writer.writerow({
                "organization": organization,
                "platform": PLATFORM,
                "rating_avg": rating_avg if rating_avg is not None else "",
                "ratings_count": ratings_count if ratings_count is not None else "",
                "reviews_count": reviews_count if reviews_count is not None else "",
            })

            # --- REVIEWS
            try:
                WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.business-review-view"))
                )
            except TimeoutException:
                print("  [INFO] Блок отзывов не найден.")
                continue

            inject_perf_css(driver)
            set_sort_newest_yamaps(driver)  # «По новизне»

            container = get_scroll_container(driver)

            seen, batch = set(), []
            stop = False
            expand_all_visible(driver)

            # первый проход
            _, met_not_newer = collect_visible_delta(driver, seen, batch, threshold)
            if met_not_newer:
                stop = True

            # автоскролл
            idle = 0
            for _ in range(BURSTS):
                if stop:
                    break
                prev_len = len(batch)
                autoscroll_burst(driver, container, BURST_MS)
                expand_all_visible(driver)
                added, met_not_newer = collect_visible_delta(driver, seen, batch, threshold)
                if met_not_newer:
                    stop = True
                if added == 0 and len(batch) == prev_len:
                    idle += 1
                else:
                    idle = 0
                if idle >= IDLE_LIMIT:
                    break

            # пишем «дельту» (только нужные колонки)
            for r in batch:
                reviews_writer.writerow({
                    "rating":       r.get("rating"),
                    "author":       (r.get("author") or "").strip(),
                    "date_iso":     (r.get("date_iso") or "")[:10],
                    "text":         (r.get("text") or "").replace("\r", " ").replace("\n", " ").strip(),
                    "platform":     PLATFORM,
                    "organization": organization,
                })

            print(f"  Новых отзывов собрано: {len(batch)} (до первой даты <= {threshold.isoformat()})")

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        out_f_reviews.close()
        out_f_summary.close()

    print(f"\nГотово.")
    print(f"Отзывы → {OUT_CSV_DELTA}")
    print(f"Summary (новый) → {OUT_CSV_SUMMARY_NEW}")


if __name__ == "__main__":
    main()
