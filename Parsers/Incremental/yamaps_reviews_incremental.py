import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, unquote

import warnings
try:
    from urllib3.exceptions import NotOpenSSLWarning
except ImportError:
    NotOpenSSLWarning = None

if NotOpenSSLWarning is not None:
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.common.exceptions import NoSuchWindowException, WebDriverException, TimeoutException
from selenium.common.exceptions import StaleElementReferenceException
import time

import os
import platform
from typing import Dict, List, Optional, Set, Tuple

YAMAPS_URLS_FILE = "./Urls/yamaps_urls.txt"
FALLBACK_URL = ("https://yandex.ru/maps/org/avtolotsman/1694054504/reviews/"
                "?ll=44.957771%2C53.220474&mode=search&sll=44.986159%2C53.218956"
                "&sspn=0.086370%2C0.033325&tab=reviews&text=автолоцман&z=14")

if platform.system() == "Windows":
    YANDEXDRIVER_PATH = "Drivers/Windows/yandexdriver.exe"
else:
    YANDEXDRIVER_PATH = "Drivers/MacOS/yandexdriver"

SOURCE_REVIEWS_CSV = "Csv/Reviews/all_reviews.csv"

NEW_REVIEWS_CSV = (
    "Csv/Reviews/NewReviews/new_yamaps_reviews.csv"
)

SUMMARY_CSV = (
    "Csv/Summary/NewSummary/new_yamaps_summary.csv"
)

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


def extract_summary_fast(driver):
    rating_avg = None
    ratings_count = None
    reviews_count = None

    try:
        elements = driver.find_elements(
            By.CSS_SELECTOR,
            "div.business-summary-rating-badge-view__rating",
        )
        if elements:
            rating_avg = _float_from_text(
                elements[0].text
            )
    except Exception:
        pass

    try:
        elements = driver.find_elements(
            By.CSS_SELECTOR,
            "span.business-rating-amount-view._summary",
        )
        if elements:
            ratings_count = _num_from_text(
                elements[0].text
            )
    except Exception:
        pass

    try:
        headers = driver.find_elements(
            By.CSS_SELECTOR,
            "h2.card-section-header__title, "
            "h2[class*='card-section-header__title']",
        )

        for header in headers:
            text = (header.text or "").strip()

            if "отзыв" in text.lower():
                reviews_count = _num_from_text(text)
                break
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

def ensure_window(
    drv: webdriver.Chrome,
    attempts: int = 10,
) -> bool:
    for _ in range(attempts):
        try:
            _ = drv.current_window_handle
            _ = drv.current_url
            return True
        except (NoSuchWindowException, WebDriverException):
            time.sleep(0.3)

    return False

def extract_organization_id(url: str) -> str:
    try:
        path = unquote(urlparse(url).path)
        match = re.search(r"/org/[^/]+/(\d+)", path)
        return match.group(1) if match else ""
    except Exception:
        return ""


def _first_visible_review_card(driver):
    cards = driver.find_elements(
        By.CSS_SELECTOR,
        "div.business-review-view",
    )

    for card in cards:
        try:
            if driver.execute_script(
                """
                const el = arguments[0];

                if (!el || !document.contains(el)) {
                    return false;
                }

                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);

                return (
                    rect.width > 0
                    && rect.height > 0
                    && rect.bottom > 0
                    && rect.top < window.innerHeight
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                );
                """,
                card,
            ):
                return card
        except Exception:
            continue

    return None


def safe_get(drv: webdriver.Chrome, url: str) -> bool:
    expected_id = extract_organization_id(url)
    previous_card = _first_visible_review_card(drv)

    try:
        print("  navigation started")

        drv.execute_script(
            "window.location.assign(arguments[0]);",
            url,
        )

        if expected_id:
            WebDriverWait(drv, WAIT_TIMEOUT).until(
                lambda d: extract_organization_id(
                    d.current_url or ""
                ) == expected_id
            )

        print(f"  URL changed: {drv.current_url}")

        if previous_card is not None:
            WebDriverWait(drv, WAIT_TIMEOUT).until(
                lambda d: _old_card_is_gone(
                    previous_card
                )
            )

        WebDriverWait(drv, WAIT_TIMEOUT).until(
            lambda d: _first_visible_review_card(d)
            is not None
        )

        print("  new review panel is ready")
        return True

    except TimeoutException:
        print(
            f"  navigation timeout, current URL: "
            f"{drv.current_url}"
        )
        return False

    except (NoSuchWindowException, WebDriverException) as exc:
        print(
            f"  navigation error: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def _old_card_is_gone(card) -> bool:
    try:
        return not card.is_displayed()
    except StaleElementReferenceException:
        return True
    except Exception:
        return True


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



def scroll_reviews_with_wheel(driver, delta_y: int = 900):

    card = _first_visible_review_card(driver)

    if card is None:
        return {
            "before": 0,
            "after": 0,
            "moved": False,
            "scrollHeight": 0,
            "clientHeight": 0,
        }

    before = driver.execute_script(
        """
        let el = arguments[0];

        while (el && el !== document.body) {
            const style = getComputedStyle(el);
            const canScroll =
                el.scrollHeight > el.clientHeight + 20
                && (
                    style.overflowY === 'auto'
                    || style.overflowY === 'scroll'
                    || style.overflowY === 'overlay'
                );

            if (canScroll) {
                return {
                    element: el,
                    top: el.scrollTop,
                    height: el.scrollHeight,
                    client: el.clientHeight
                };
            }

            el = el.parentElement;
        }

        const root =
            document.scrollingElement || document.body;

        return {
            element: root,
            top: root.scrollTop,
            height: root.scrollHeight,
            client: root.clientHeight
        };
        """,
        card,
    )

    scroll_element = before["element"]
    before_top = before["top"]

    origin = ScrollOrigin.from_element(card)

    ActionChains(driver).scroll_from_origin(
        origin,
        0,
        delta_y,
    ).perform()

    time.sleep(1)

    after_top = driver.execute_script(
        "return arguments[0].scrollTop;",
        scroll_element,
    )

    if after_top == before_top:
        after_top = driver.execute_script(
            """
            const box = arguments[0];
            const step = Math.max(
                box.clientHeight * 0.9,
                700
            );

            box.scrollTop = Math.min(
                box.scrollTop + step,
                box.scrollHeight - box.clientHeight
            );

            box.dispatchEvent(
                new Event('scroll', {bubbles: true})
            );

            return box.scrollTop;
            """,
            scroll_element,
        )

    result = {
        "before": before_top,
        "after": after_top,
        "moved": after_top > before_top,
        "scrollHeight": before["height"],
        "clientHeight": before["client"],
    }

    print(f"  wheel scroll: {result}")
    return result



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

def extract_review(review_el):
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

def normalize_key_part(value: Optional[str]) -> str:
    return re.sub(
        r"\s+",
        " ",
        (value or "").strip().lower(),
    )


def organization_key(value: Optional[str]) -> str:
    return normalize_key_part(value)


def stored_review_key(
    organization: str,
    author: Optional[str],
    date_iso: Optional[str],
    text: Optional[str],
) -> Tuple[str, str, str, str]:

    return (
        organization_key(organization),
        normalize_key_part(author),
        (date_iso or "")[:10],
        normalize_key_part(text),
    )


def parse_stored_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None

    try:
        return date.fromisoformat(
            value.strip()[:10]
        )
    except ValueError:
        return None


def load_existing_reviews(
    csv_path: str,
) -> Tuple[
    Dict[str, Set[Tuple[str, str, str, str]]],
    Dict[str, date],
]:
    keys_by_org: Dict[
        str,
        Set[Tuple[str, str, str, str]],
    ] = {}
    latest_by_org: Dict[str, date] = {}

    path = Path(csv_path)

    if not path.exists() or path.stat().st_size == 0:
        return keys_by_org, latest_by_org

    with path.open(
        "r",
        newline="",
        encoding="utf-8",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            if (
                row.get("platform")
                or ""
            ).strip() != PLATFORM:
                continue

            organization = (
                row.get("organization")
                or ""
            ).strip()

            if not organization:
                continue

            date_value = (
                row.get("date_iso")
                or row.get("dateISO")
                or row.get("date")
                or ""
            )
            parsed_date = parse_stored_date(
                date_value
            )

            if parsed_date is None:
                continue

            org_key = organization_key(
                organization
            )
            key = stored_review_key(
                organization,
                row.get("author"),
                parsed_date.isoformat(),
                row.get("text"),
            )

            keys_by_org.setdefault(
                org_key,
                set(),
            ).add(key)

            previous = latest_by_org.get(
                org_key
            )

            if (
                previous is None
                or parsed_date > previous
            ):
                latest_by_org[org_key] = (
                    parsed_date
                )

    return keys_by_org, latest_by_org


def incremental_fallback_date() -> date:
    today = datetime.now().date()

    try:
        return today.replace(
            year=today.year - YEARS_LIMIT
        )
    except ValueError:
        return today.replace(
            year=today.year - YEARS_LIMIT,
            day=28,
        )


def collect_visible_incremental(
    driver,
    organization: str,
    existing_keys: Set[
        Tuple[str, str, str, str]
    ],
    run_seen: Set[
        Tuple[str, str, str, str]
    ],
    out: List[dict],
    threshold_date: date,
) -> Tuple[int, bool, int]:
    added = 0
    reached_older = False
    already_exists = 0

    cards = driver.find_elements(
        By.CSS_SELECTOR,
        "div.business-review-view",
    )

    for card in cards:
        try:
            expand_all_visible(
                driver,
                card,
            )
            item = extract_review(card)

            text = (
                item.get("text")
                or ""
            ).strip()

            if not text:
                continue

            date_iso = (
                item.get("date_iso")
                or ""
            )[:10]
            parsed_date = parse_stored_date(
                date_iso
            )

            if parsed_date is None:
                continue

            if parsed_date < threshold_date:
                reached_older = True
                continue

            key = stored_review_key(
                organization,
                item.get("author"),
                date_iso,
                text,
            )

            if key in existing_keys:
                already_exists += 1
                continue

            if key in run_seen:
                continue

            run_seen.add(key)
            item["text"] = text
            item["date_iso"] = date_iso
            out.append(item)
            added += 1

        except Exception:
            continue

    return (
        added,
        reached_older,
        already_exists,
    )


REVIEW_FIELDS = [
    "rating",
    "author",
    "date_iso",
    "text",
    "platform",
    "organization",
]


def review_to_row(
    review: dict,
    organization: str,
) -> dict:
    return {
        "rating": review.get("rating"),
        "author": (
            review.get("author")
            or ""
        ).strip(),
        "date_iso": (
            review.get("date_iso")
            or ""
        )[:10],
        "text": (
            review.get("text")
            or ""
        )
        .replace("\r", " ")
        .replace("\n", " ")
        .strip(),
        "platform": PLATFORM,
        "organization": organization,
    }


def append_new_reviews(
    csv_path: str,
    rows: List[dict],
) -> None:
    if not rows:
        return

    path = Path(csv_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    has_content = (
        path.exists()
        and path.stat().st_size > 0
    )

    fieldnames = REVIEW_FIELDS

    if has_content:
        with path.open(
            "r",
            newline="",
            encoding="utf-8",
        ) as existing_file:
            reader = csv.reader(existing_file)
            existing_header = next(
                reader,
                [],
            )

        if existing_header:
            fieldnames = existing_header

            required = {
                "author",
                "date_iso",
                "text",
                "platform",
                "organization",
            }
            missing = required - set(
                fieldnames
            )

            if missing:
                raise ValueError(
                    "В all_reviews.csv отсутствуют "
                    f"обязательные колонки: "
                    f"{sorted(missing)}"
                )

    with path.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            quoting=csv.QUOTE_ALL,
            extrasaction="ignore",
            restval="",
        )

        if not has_content:
            writer.writeheader()

        writer.writerows(rows)

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
    (
        existing_keys_by_org,
        latest_dates_by_org,
    ) = load_existing_reviews(
        SOURCE_REVIEWS_CSV
    )

    print(
        f"[INFO] Existing Yandex Maps organizations: "
        f"{len(existing_keys_by_org)}"
    )
    print(
        f"[INFO] Existing reviews source: "
        f"{SOURCE_REVIEWS_CSV}"
    )
    print(
        f"[INFO] New reviews output: "
        f"{NEW_REVIEWS_CSV}"
    )

    try:
        urls = [
            url.strip()
            for url in Path(
                YAMAPS_URLS_FILE
            ).read_text(
                encoding="utf-8"
            ).splitlines()
            if url.strip()
        ]

        if not urls:
            urls = [FALLBACK_URL]

    except FileNotFoundError:
        urls = [FALLBACK_URL]

    Path(
        NEW_REVIEWS_CSV
    ).parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    Path(
        SUMMARY_CSV
    ).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    new_reviews_file = open(
        NEW_REVIEWS_CSV,
        "w",
        newline="",
        encoding="utf-8",
    )

    new_reviews_writer = csv.DictWriter(
        new_reviews_file,
        fieldnames=REVIEW_FIELDS,
        quoting=csv.QUOTE_ALL,
    )
    new_reviews_writer.writeheader()

    summary_file = open(
        SUMMARY_CSV,
        "w",
        newline="",
        encoding="utf-8",
    )

    summary_writer = csv.DictWriter(
        summary_file,
        fieldnames=[
            "organization",
            "platform",
            "rating_avg",
            "ratings_count",
            "reviews_count",
        ],
        quoting=csv.QUOTE_ALL,
    )
    summary_writer.writeheader()

    driver = setup_driver()
    total_new = 0

    try:
        for index, url in enumerate(
            urls,
            start=1,
        ):
            print(
                f"[{index}/{len(urls)}] "
                f"{url}"
            )

            if not safe_get(
                driver,
                url,
            ):
                print(
                    "  skipping: unable to open URL"
                )
                continue

            if not ensure_window(driver):
                print(
                    "  browser window check failed "
                    "after retries"
                )
                continue

            try:
                WebDriverWait(
                    driver,
                    WAIT_TIMEOUT,
                ).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "div.orgpage-header-view, "
                            "div.business-review-view",
                        )
                    )
                )
            except TimeoutException:
                print(
                    "  skipping: page did not load"
                )
                continue

            current_url = (
                driver.current_url
                or url
            )
            organization = (
                extract_organization_from_url(
                    current_url
                )
                or ""
            )

            if index == 3:
                organization = "kia_avtolotsman"

            org_key = organization_key(
                organization
            )

            existing_keys = (
                existing_keys_by_org.setdefault(
                    org_key,
                    set(),
                )
            )
            latest_date = (
                latest_dates_by_org.get(
                    org_key
                )
            )
            threshold_date = (
                latest_date
                if latest_date is not None
                else incremental_fallback_date()
            )

            print(
                f"  organization={organization or '-'} | "
                f"existing={len(existing_keys)} | "
                f"threshold={threshold_date.isoformat()}"
            )

            inject_perf_css(driver)

            print("  setting newest sort")
            sort_newest_ok = (
                set_sort_newest_yamaps(
                    driver
                )
            )
            print(
                f"  newest sort: "
                f"{sort_newest_ok}"
            )

            run_seen: Set[
                Tuple[str, str, str, str]
            ] = set()
            batch: List[dict] = []
            idle = 0

            expand_all_visible(driver)

            (
                initial_added,
                _,
                initial_existing,
            ) = collect_visible_incremental(
                driver,
                organization,
                existing_keys,
                run_seen,
                batch,
                threshold_date,
            )

            print(
                f"  initial: "
                f"added={initial_added}, "
                f"existing={initial_existing}, "
                f"total_new={len(batch)}"
            )

            for burst_number in range(
                BURSTS
            ):
                previous_count = len(batch)

                scroll_result = (
                    scroll_reviews_with_wheel(
                        driver,
                        delta_y=900,
                    )
                )

                time.sleep(2)
                expand_all_visible(driver)

                (
                    added,
                    reached_older,
                    existing_count,
                ) = collect_visible_incremental(
                    driver,
                    organization,
                    existing_keys,
                    run_seen,
                    batch,
                    threshold_date,
                )

                print(
                    f"  scroll "
                    f"{burst_number + 1}/{BURSTS}: "
                    f"moved={scroll_result['moved']}, "
                    f"added={added}, "
                    f"existing={existing_count}, "
                    f"total_new={len(batch)}, "
                    f"older={reached_older}"
                )

                if reached_older:
                    print(
                        "  stop: reached dates older "
                        "than incremental threshold"
                    )
                    break

                progress = (
                    scroll_result["moved"]
                    or len(batch) > previous_count
                )
                idle = (
                    0
                    if progress
                    else idle + 1
                )

                if idle >= IDLE_LIMIT:
                    print(
                        f"  stop: no movement/new "
                        f"reviews after "
                        f"{IDLE_LIMIT} attempts"
                    )
                    break

            new_rows = [
                review_to_row(
                    review,
                    organization,
                )
                for review in batch
            ]

            new_reviews_writer.writerows(
                new_rows
            )
            new_reviews_file.flush()

            for row in new_rows:
                key = stored_review_key(
                    organization,
                    row.get("author"),
                    row.get("date_iso"),
                    row.get("text"),
                )
                existing_keys.add(key)

                parsed_date = parse_stored_date(
                    row.get("date_iso")
                )

                if parsed_date is not None:
                    current_latest = (
                        latest_dates_by_org.get(
                            org_key
                        )
                    )

                    if (
                        current_latest is None
                        or parsed_date
                        > current_latest
                    ):
                        latest_dates_by_org[
                            org_key
                        ] = parsed_date

            total_new += len(new_rows)

            print(
                "  reading summary after scroll"
            )
            (
                rating_avg,
                ratings_count,
                reviews_count,
            ) = extract_summary_fast(driver)

            summary_writer.writerow(
                {
                    "organization": organization,
                    "platform": PLATFORM,
                    "rating_avg": (
                        rating_avg
                        if rating_avg is not None
                        else ""
                    ),
                    "ratings_count": (
                        ratings_count
                        if ratings_count is not None
                        else ""
                    ),
                    "reviews_count": (
                        reviews_count
                        if reviews_count is not None
                        else ""
                    ),
                }
            )

            summary_file.flush()

            print(
                f"  new reviews appended: "
                f"{len(new_rows)} | "
                f"summary reviews={reviews_count}"
            )

    finally:
        try:
            driver.quit()
        except Exception:
            pass

        new_reviews_file.close()
        summary_file.close()

    print(
        f"Done. New reviews found: "
        f"{total_new}. "
        f"New reviews -> {NEW_REVIEWS_CSV} | "
        f"Source unchanged -> {SOURCE_REVIEWS_CSV} | "
        f"Summary -> {SUMMARY_CSV}"
    )

if __name__ == "__main__":
    main()