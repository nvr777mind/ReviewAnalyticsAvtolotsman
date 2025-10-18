import csv
import re
from datetime import datetime, timedelta
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

import os, sys, platform
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from typing import Optional

YAMAPS_URLS_FILE = "./Urls/yamaps_urls.txt"
FALLBACK_URL = ("https://yandex.ru/maps/org/avtolotsman/1694054504/reviews/"
                "?ll=44.957771%2C53.220474&mode=search&sll=44.986159%2C53.218956"
                "&sspn=0.086370%2C0.033325&tab=reviews&text=автолоцман&z=14")

if platform.system() == "Windows":
    YANDEXDRIVER_PATH = "Drivers/Windows/yandexdriver.exe"
else:
    YANDEXDRIVER_PATH = "Drivers/MacOS/yandexdriver"

OUT_CSV_REVIEWS  = "Csv/Reviews/yamaps_reviews.csv"
OUT_CSV_SUMMARY  = "Csv/Summary/yamaps_summary.csv"

WAIT_TIMEOUT   = 60
BURSTS         = 12
BURST_MS       = 1200
IDLE_LIMIT     = 3
YEARS_LIMIT    = 2

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RELATIVE_MAP = {"сегодня": 0, "вчера": -1}

PLATFORM = "Yandex Maps"

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

def _num_from_text(text: str):
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

def _float_from_text(text: str):
    if not text:
        return None
    t = text.replace("\xa0", " ")
    m = re.search(r"(\d+[,\.\u202F]\d+)", t)
    if m:
        try:
            return float(m.group(1).replace("\u202f", "").replace(",", "."))
        except ValueError:
            pass
    m2 = re.search(r"(?<!\d)(\d)(?![\d,\.])", t)
    if m2:
        return float(m2.group(1))
    return None

def extract_summary(driver):
    """
    Возвращает (rating_avg, ratings_count, reviews_count) с текущей страницы.
    Сначала пробуем явные селекторы, затем разные fallback-и, в т.ч. regex по HTML.
    """
    rating_avg = None
    ratings_count = None
    reviews_count = None

    try:
        r_block = driver.find_elements(By.CSS_SELECTOR, "div.business-summary-rating-badge-view__rating")
        if r_block:
            rating_avg = _float_from_text(r_block[0].inner_text if hasattr(r_block[0], "inner_text") else r_block[0].text)

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
    opts.binary_location = str(yb)
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.page_load_strategy = 'eager'
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

            if not (item.get("text") or "").strip():
                continue

            d_iso = item.get("date_iso")
            if not d_iso:
                continue
            try:
                d = datetime.fromisoformat(d_iso[:10]).date()
            except Exception:
                continue

            if d < cutoff_date:
                met_old = True
                continue

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
        path = urlparse(url).path
        m = re.search(r"/org/([^/]+)/", path)
        if m:
            return unquote(m.group(1))
    except Exception:
        pass
    return ""

def main():
    try:
        urls = [u.strip() for u in Path(YAMAPS_URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip()]
        if not urls:
            urls = [FALLBACK_URL]
    except FileNotFoundError:
        urls = [FALLBACK_URL]

    Path(OUT_CSV_REVIEWS).parent.mkdir(parents=True, exist_ok=True)

    f_rev = open(OUT_CSV_REVIEWS, "w", newline="", encoding="utf-8")
    f_sum = open(OUT_CSV_SUMMARY, "w", newline="", encoding="utf-8")
    w_rev = csv.DictWriter(f_rev, fieldnames=["rating","author","date_iso","text","platform","organization"], quoting=csv.QUOTE_ALL)
    w_sum = csv.DictWriter(f_sum, fieldnames=["organization","platform","rating_avg","ratings_count","reviews_count"], quoting=csv.QUOTE_ALL)
    w_rev.writeheader()
    w_sum.writeheader()

    driver = setup_driver()
    try:
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")

            if not safe_get(driver, url):
                if not safe_get(driver, url):
                    print("  пропускаю: не удалось открыть URL")
                    continue

            if not ensure_window(driver):
                print("  пропускаю: окно браузера недоступно")
                continue

            try:
                WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.orgpage-header-view, div.business-review-view"))
                )
            except TimeoutException:
                print("  пропускаю: страница не загрузилась")
                continue

            current = driver.current_url or url
            organization = extract_organization_from_url(current) or ""

            try:
                try:
                    h2 = driver.find_elements(By.CSS_SELECTOR, "h2.card-section-header__title._wide")
                    if not h2:
                        tabs = driver.find_elements(By.CSS_SELECTOR, '[role="tablist"] [role="tab"]')
                        for t in tabs:
                            if "отзыв" in (t.text or "").lower():
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
                                driver.execute_script("arguments[0].click();", t)
                                WebDriverWait(driver, 8).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, "h2.card-section-header__title._wide"))
                                )
                                break
                except Exception:
                    pass

                rating_avg, ratings_count, reviews_count = extract_summary(driver)
            except Exception:
                rating_avg = ratings_count = reviews_count = None

            w_sum.writerow({
                "organization": organization,
                "platform": PLATFORM,
                "rating_avg": rating_avg if rating_avg is not None else "",
                "ratings_count": ratings_count if ratings_count is not None else "",
                "reviews_count": reviews_count if reviews_count is not None else "",
            })

            try:
                WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.business-review-view"))
                )
            except TimeoutException:
                print("  нет блока отзывов")
                continue

            inject_perf_css(driver)
            set_sort_newest_yamaps(driver)

            container = get_scroll_container(driver)
            cutoff_date = datetime.now().date() - timedelta(days=365*YEARS_LIMIT)

            seen, batch = set(), []
            idle = 0
            stop_by_age = False

            expand_all_visible(driver)
            _, met_old = collect_visible_batch(driver, seen, batch, cutoff_date)
            if met_old:
                stop_by_age = True

            for _ in range(BURSTS):
                if stop_by_age:
                    break
                prev_len = len(batch)
                autoscroll_burst(driver, container, BURST_MS)
                expand_all_visible(driver)
                added, met_old = collect_visible_batch(driver, seen, batch, cutoff_date)
                if met_old:
                    stop_by_age = True
                if added == 0 and len(batch) == prev_len:
                    idle += 1
                else:
                    idle = 0
                if idle >= IDLE_LIMIT:
                    break

            for r in batch:
                w_rev.writerow({
                    "rating":       r.get("rating"),
                    "author":       (r.get("author") or "").strip(),
                    "date_iso":     (r.get("date_iso") or "")[:10],
                    "text":         (r.get("text") or "").replace("\r", " ").replace("\n", " ").strip(),
                    "platform":     PLATFORM,
                    "organization": organization,
                })

            print(f"  summary: rating={rating_avg}, ratings={ratings_count}, reviews={reviews_count} | отзывов собрано: {len(batch)} | org={organization or '-'}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        f_rev.close()
        f_sum.close()

    print(f"Готово. Summary -> {OUT_CSV_SUMMARY} | Reviews -> {OUT_CSV_REVIEWS}")

if __name__ == "__main__":
    main()
