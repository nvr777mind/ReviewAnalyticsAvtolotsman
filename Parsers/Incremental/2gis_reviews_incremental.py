import csv
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchWindowException,
    SessionNotCreatedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait

try:
    from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
except ImportError:
    ScrollOrigin = None

try:
    from urllib3.exceptions import NotOpenSSLWarning
except ImportError:
    NotOpenSSLWarning = None

if NotOpenSSLWarning:
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)


def find_project_root() -> Path:
    current_file = Path(__file__).resolve()

    for parent in current_file.parents:
        if (
            (parent / "Urls").is_dir()
            and (parent / "Csv").is_dir()
            and (parent / "Drivers").is_dir()
        ):
            return parent

    raise FileNotFoundError(
        "Project root not found: expected folders "
        "'Urls', 'Csv' and 'Drivers'."
    )


BASE_DIR = find_project_root()
URLS_FILE = BASE_DIR / "Urls" / "2gis_urls.txt"

SOURCE_REVIEWS_CSV = (
    BASE_DIR / "Csv" / "Reviews" / "all_reviews.csv"
)

NEW_REVIEWS_CSV = (
    BASE_DIR
    / "Csv"
    / "Reviews"
    / "NewReviews"
    / "new_2gis_reviews.csv"
)

NEW_SUMMARY_CSV = (
    BASE_DIR
    / "Csv"
    / "Summary"
    / "NewSummary"
    / "new_2gis_summary.csv"
)

FALLBACK_URL = (
    "https://2gis.ru/penza/search/"
    "%D0%B0%D0%B2%D1%82%D0%BE%D0%BB%D0%BE%D1%86%D0%BC%D0%B0%D0%BD/"
    "firm/70000001057701394/44.973806%2C53.220685/tab/reviews"
    "?m=44.975027%2C53.220456%2F17.63"
)

DRIVER_PATH = (
    BASE_DIR / "Drivers" / "Windows" / "yandexdriver.exe"
    if platform.system() == "Windows"
    else BASE_DIR / "Drivers" / "MacOS" / "yandexdriver"
)

WAIT_TIMEOUT = 20
SCROLL_HARD_LIMIT = 1000
SCROLL_PAUSE = 1.1
IDLE_LIMIT = 4
YEARS_LIMIT = 2
PLATFORM_NAME = "2GIS"

REVIEW_CARD_SEL = "div._1rowqpjv"
AUTHOR_SEL = "span._19h0cqe"
DATE_SEL = "span._10c0hgu"
RATING_SEL = "div._1u60bur > div._y10azs"
TEXT_SELECTORS = ("a._co8kyiw", "div._49x36f > a._1msln3t")
SCROLL_CONTAINER_SEL = "div._8hh56jx[data-scroll='true']"

SUMMARY_RATING_SEL = "div._1tam240"
SUMMARY_RATINGS_COUNT_SEL = "div._1y88ofn"
SUMMARY_REVIEWS_COUNT_SEL = "div._4v626nk > span"

ORGANIZATION_BY_FIRM_ID = {
    "70000001057701394": "avtolotsman_probeg",
    "70000001086881480": "avtolotsman",
    "70000001083460643": "avtolotsman",
    "5911502791905673": "kia_avtolotsman",
    "5911502792136575": "shkoda_avtolotsman",
    "70000001071267471": "avtolotsman_deteyling",
    "70000001083645814": "changan_avtolotsman",
}

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def find_yandex_browser() -> Optional[Path]:
    custom = os.environ.get("YANDEX_BROWSER_PATH")
    if custom and Path(custom).expanduser().is_file():
        return Path(custom).expanduser()

    candidates = (
        [
            Path.home() / "AppData" / "Local" / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
        ]
        if platform.system() == "Windows"
        else [Path("/Applications/Yandex.app/Contents/MacOS/Yandex")]
    )
    return next((path for path in candidates if path.is_file()), None)


def stop_stale_drivers() -> None:
    if platform.system() != "Windows":
        return
    for name in ("yandexdriver.exe", "chromedriver.exe"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", name],
                timeout=2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass


def create_driver() -> Tuple[webdriver.Chrome, str]:
    browser = find_yandex_browser()
    if browser is None:
        raise FileNotFoundError("Yandex Browser not found")
    if not DRIVER_PATH.is_file():
        raise FileNotFoundError(f"Yandex Driver not found: {DRIVER_PATH}")

    stop_stale_drivers()
    profile = tempfile.mkdtemp(prefix="2gis_profile_")

    options = Options()
    options.binary_location = str(browser)
    options.page_load_strategy = "eager"
    for arg in (
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-blink-features=AutomationControlled",
        "--lang=ru-RU,ru",
        "--start-maximized",
    ):
        options.add_argument(arg)

    options.add_argument(f"--user-data-dir={profile}")
    options.add_experimental_option(
        "excludeSwitches", ["enable-automation", "enable-logging"]
    )

    driver = webdriver.Chrome(
        service=Service(str(DRIVER_PATH)),
        options=options,
    )
    driver.set_page_load_timeout(35)
    driver.set_script_timeout(35)
    driver.implicitly_wait(0)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty("
                    "navigator,'webdriver',{get:()=>undefined});"
                )
            },
        )
    except Exception:
        pass

    return driver, profile


def close_driver(driver: Optional[webdriver.Chrome], profile: Optional[str]) -> None:
    try:
        if driver:
            driver.quit()
    except Exception:
        pass
    if profile:
        shutil.rmtree(profile, ignore_errors=True)


def firm_id_from_url(url: str) -> str:
    match = re.search(r"/firm/(\d+)", url or "")
    return match.group(1) if match else ""


def organization_from_url(url: str) -> str:
    return ORGANIZATION_BY_FIRM_ID.get(firm_id_from_url(url), "")


def is_visible(driver, element) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const el=arguments[0];
                if(!el||!document.contains(el)) return false;
                const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                return r.width>0&&r.height>0
                    &&s.display!=='none'&&s.visibility!=='hidden';
                """,
                element,
            )
        )
    except Exception:
        return False


def reset_all_scroll(driver) -> None:
    try:
        driver.execute_script(
            """
            for(const el of document.querySelectorAll(
                '[data-scroll="true"],main,section,article,div'
            )){
                if(el.scrollHeight>el.clientHeight+20) el.scrollTop=0;
            }
            window.scrollTo(0,0);
            """
        )
    except Exception:
        pass


def navigate(driver, url: str) -> bool:
    expected_id = firm_id_from_url(url)
    try:
        previous_url = driver.current_url or ""
        old_cards = driver.find_elements(By.CSS_SELECTOR, REVIEW_CARD_SEL)
        old_marker = old_cards[0] if old_cards else None

        print("  navigation started")
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass

        reset_all_scroll(driver)
        driver.execute_script("window.location.assign(arguments[0]);", url)

        WebDriverWait(driver, WAIT_TIMEOUT).until(
            lambda drv: (
                (drv.current_url or "") != previous_url
                and (not expected_id or expected_id in (drv.current_url or ""))
            )
        )
        print(f"  URL changed: {driver.current_url}")

        if old_marker is not None:
            try:
                WebDriverWait(driver, 8).until(
                    lambda drv: not drv.execute_script(
                        "return document.contains(arguments[0]);", old_marker
                    )
                )
            except Exception:
                pass

        WebDriverWait(driver, WAIT_TIMEOUT).until(
            lambda drv: bool(
                drv.find_elements(
                    By.CSS_SELECTOR,
                    f"{SCROLL_CONTAINER_SEL},"
                    f"{SUMMARY_RATING_SEL},"
                    "a[href*='/tab/reviews']",
                )
            )
        )

        reset_all_scroll(driver)
        print("  2GIS page is ready")
        return True

    except TimeoutException:
        current = ""
        try:
            current = driver.current_url or ""
        except Exception:
            pass
        print(f"  navigation timeout: {current}")
        return bool(current and (not expected_id or expected_id in current))

    except (NoSuchWindowException, WebDriverException) as error:
        print(f"  navigation error: {type(error).__name__}: {error}")
        return False


def accept_cookies(driver) -> None:
    try:
        clicked = driver.execute_script(
            """
            const allowed=['понятно','принять','хорошо','ок','согласен'];
            for(const el of document.querySelectorAll('button,[role="button"]')){
                const r=el.getBoundingClientRect();
                if(r.width<=0||r.height<=0) continue;
                const t=(el.innerText||el.textContent||
                         el.getAttribute('aria-label')||'').trim().toLowerCase();
                if(allowed.some(x=>t===x||t.startsWith(x+' '))){
                    el.click(); return t;
                }
            }
            return '';
            """
        )
        if clicked:
            print(f"  cookie button clicked: {clicked!r}")
    except Exception:
        pass


def subtract_months(value: date, months: int) -> date:
    year, month = value.year, value.month - months
    while month <= 0:
        month += 12
        year -= 1

    next_month = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))


def parse_review_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    text = re.sub(r"редакт.*$", "", raw.strip().lower()).strip()
    text = re.split(r"[,•]", text)[0].strip()
    today = datetime.now().date()

    direct = {"сегодня": 0, "вчера": 1, "позавчера": 2}
    if text in direct:
        return (today - timedelta(days=direct[text])).isoformat()

    singular = {
        "день назад": ("days", 1),
        "неделю назад": ("weeks", 1),
        "месяц назад": ("months", 1),
        "год назад": ("years", 1),
    }

    unit = ""
    amount = 0
    if text in singular:
        unit, amount = singular[text]
    else:
        match = re.match(r"^(\d+)\s+([а-яё]+)\s+назад$", text)
        if match:
            amount = int(match.group(1))
            word = match.group(2)
            if word.startswith("дн"):
                unit = "days"
            elif word.startswith("недел"):
                unit = "weeks"
            elif word.startswith("месяц"):
                unit = "months"
            elif word.startswith(("год", "лет")):
                unit = "years"

    if unit == "days":
        return (today - timedelta(days=amount)).isoformat()
    if unit == "weeks":
        return (today - timedelta(weeks=amount)).isoformat()
    if unit == "months":
        return subtract_months(today, amount).isoformat()
    if unit == "years":
        try:
            return today.replace(year=today.year - amount).isoformat()
        except ValueError:
            return today.replace(year=today.year - amount, day=28).isoformat()

    match = re.match(
        r"^(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?(?:\s*г\.?)?$", text
    )
    if match:
        day = int(match.group(1))
        month = RU_MONTHS.get(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if month:
            try:
                parsed = date(year, month, day)
                if not match.group(3) and parsed > today:
                    parsed = parsed.replace(year=year - 1)
                return parsed.isoformat()
            except ValueError:
                return None

    match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", text)
    if match:
        day, month, year = map(int, match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    return None


def get_cutoff_date() -> date:
    today = datetime.now().date()
    try:
        return today.replace(year=today.year - YEARS_LIMIT)
    except ValueError:
        return today.replace(year=today.year - YEARS_LIMIT, day=28)


def normalize_key_part(value: Optional[str]) -> str:
    return re.sub(
        r"\s+",
        " ",
        (value or "").strip().lower(),
    )


def organization_key(value: Optional[str]) -> str:
    return normalize_key_part(value)


def stored_review_key(
    author: Optional[str],
    date_iso: Optional[str],
    text: Optional[str],
) -> Tuple[str, str, str]:
    return (
        normalize_key_part(author),
        (date_iso or "")[:10],
        normalize_key_part(text),
    )


def parse_stored_date(
    value: Optional[str],
) -> Optional[date]:
    if not value:
        return None

    try:
        return date.fromisoformat(
            value.strip()[:10]
        )
    except ValueError:
        return None


def load_existing_reviews(
    csv_path: Path,
) -> Tuple[
    Dict[str, Set[Tuple[str, str, str]]],
    Dict[str, date],
]:
    keys_by_org: Dict[
        str,
        Set[Tuple[str, str, str]],
    ] = {}
    latest_by_org: Dict[str, date] = {}

    if (
        not csv_path.exists()
        or csv_path.stat().st_size == 0
    ):
        return keys_by_org, latest_by_org

    with csv_path.open(
        "r",
        newline="",
        encoding="utf-8",
    ) as source_file:
        reader = csv.DictReader(source_file)

        for row in reader:
            if (
                row.get("platform")
                or ""
            ).strip() != PLATFORM_NAME:
                continue

            organization = (
                row.get("organization")
                or ""
            ).strip()

            if not organization:
                continue

            parsed_date = parse_stored_date(
                row.get("date_iso")
                or row.get("dateISO")
                or row.get("date")
            )

            if parsed_date is None:
                continue

            org_key = organization_key(
                organization
            )

            key = stored_review_key(
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


def first_number(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"\d[\d\s\u00a0\u202f]*", text)
    digits = re.sub(r"\D", "", match.group(0)) if match else ""
    return int(digits) if digits else None


def first_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", text)
    return float(match.group(0).replace(",", ".")) if match else None


def extract_summary(
    driver,
    timeout: float = 8.0,
) -> Tuple[
    Optional[float],
    Optional[int],
    Optional[int],
]:
    deadline = time.monotonic() + timeout

    rating_avg: Optional[float] = None
    ratings_count: Optional[int] = None
    reviews_count: Optional[int] = None

    def visible_texts(
        selector: str,
    ) -> List[str]:
        values: List[str] = []

        try:
            elements = driver.find_elements(
                By.CSS_SELECTOR,
                selector,
            )
        except Exception:
            return values

        for element in elements:
            try:
                if not is_visible(
                    driver,
                    element,
                ):
                    continue

                value = (
                    element.text
                    or element.get_attribute(
                        "aria-label"
                    )
                    or element.get_attribute(
                        "title"
                    )
                    or ""
                ).strip()

                if value:
                    values.append(value)
            except Exception:
                continue

        return values

    while time.monotonic() < deadline:
        for value in visible_texts(
            SUMMARY_RATING_SEL
        ):
            parsed = first_float(value)

            if (
                parsed is not None
                and 1.0 <= parsed <= 5.0
            ):
                rating_avg = parsed
                break

        for value in visible_texts(
            SUMMARY_RATINGS_COUNT_SEL
        ):
            parsed = first_number(value)

            if parsed is not None:
                ratings_count = parsed
                break

        for value in visible_texts(
            SUMMARY_REVIEWS_COUNT_SEL
        ):
            parsed = first_number(value)

            if parsed is not None:
                reviews_count = parsed
                break

        if rating_avg is not None:
            break

        time.sleep(0.4)

    print(
        "  summary read: "
        f"rating={rating_avg}, "
        f"ratings={ratings_count}, "
        f"reviews={reviews_count}"
    )

    return (
        rating_avg,
        ratings_count,
        reviews_count,
    )


def find_review_cards(driver, container=None) -> List:
    try:
        root = container or find_scroll_container(
            driver
        )

        return root.find_elements(
            By.CSS_SELECTOR,
            REVIEW_CARD_SEL,
        )
    except Exception:
        return []

def extract_rating(card) -> Optional[float]:
    try:
        for element in card.find_elements(By.CSS_SELECTOR, RATING_SEL):
            value = first_float(
                element.get_attribute("aria-label")
                or element.get_attribute("title")
                or ""
            )
            if value is not None and 1 <= value <= 5:
                return value

            stars = element.find_elements(By.TAG_NAME, "span")
            if 1 <= len(stars) <= 5:
                return float(len(stars))
    except Exception:
        pass
    return None


def extract_text(card) -> str:
    values: List[str] = []
    for selector in TEXT_SELECTORS:
        try:
            values.extend(
                element.text.strip()
                for element in card.find_elements(By.CSS_SELECTOR, selector)
                if element.text.strip()
            )
        except Exception:
            pass

    if not values:
        return ""

    text = re.sub(r"\s+", " ", max(values, key=len))
    text = re.sub(
        r"(Полезно.*|Читать целиком.*|Свернуть.*|Официальный ответ.*)$",
        "",
        text,
        flags=re.I,
    )
    return text.strip()


def extract_review(card) -> Optional[dict]:
    try:
        author_element = card.find_element(By.CSS_SELECTOR, AUTHOR_SEL)
        author = (
            author_element.get_attribute("title")
            or author_element.text
            or ""
        ).strip()
    except Exception:
        author = ""

    try:
        date_element = card.find_element(By.CSS_SELECTOR, DATE_SEL)
        raw_date = (
            date_element.text
            or date_element.get_attribute("aria-label")
            or ""
        ).strip()
        date_iso = parse_review_date(raw_date) or ""
    except Exception:
        date_iso = ""

    text = extract_text(card)
    if not text:
        return None

    lowered = text.lower()
    if "официальный ответ" in lowered or "ответ владельца" in lowered:
        return None

    return {
        "rating": extract_rating(card),
        "author": author,
        "date_iso": date_iso,
        "text": text,
    }


def collect_reviews(
    driver,
    container,
    reviews: List[dict],
    index: Dict[str, int],
    cutoff: date,
) -> Tuple[int, int]:
    added = 0
    skipped_old = 0

    for card in find_review_cards(driver, container):
        review = extract_review(card)
        if not review or not review["date_iso"]:
            continue

        try:
            review_date = date.fromisoformat(review["date_iso"][:10])
        except ValueError:
            continue

        if review_date < cutoff:
            skipped_old += 1
            continue

        key = re.sub(r"\s+", " ", review["text"].strip().lower())
        if key not in index:
            index[key] = len(reviews)
            reviews.append(review)
            added += 1
            continue

        current = reviews[index[key]]
        if not current["author"] and review["author"]:
            current["author"] = review["author"]
        if current["rating"] is None and review["rating"] is not None:
            current["rating"] = review["rating"]
        if not current["date_iso"] and review["date_iso"]:
            current["date_iso"] = review["date_iso"]

    return added, skipped_old


def find_scroll_container(driver):
    try:
        candidates = driver.find_elements(
            By.CSS_SELECTOR,
            SCROLL_CONTAINER_SEL,
        )
    except Exception:
        candidates = []

    best = None
    best_score = -1

    for element in candidates:
        try:
            if not is_visible(driver, element):
                continue

            _, height, client = scroll_metrics(
                driver,
                element,
            )

            if height <= client + 20:
                continue

            card_count = len(
                element.find_elements(
                    By.CSS_SELECTOR,
                    REVIEW_CARD_SEL,
                )
            )

            score = (
                card_count * 100_000
                + client * 100
                + height
            )

            if score > best_score:
                best = element
                best_score = score
        except Exception:
            continue

    if best is not None:
        return best

    try:
        cards = driver.find_elements(
            By.CSS_SELECTOR,
            REVIEW_CARD_SEL,
        )

        for card in cards:
            if not is_visible(driver, card):
                continue

            container = driver.execute_script(
                """
                let el = arguments[0];

                while (el && el !== document.body) {
                    const style = getComputedStyle(el);

                    if (
                        el.scrollHeight > el.clientHeight + 20
                        && (
                            style.overflowY === 'auto'
                            || style.overflowY === 'scroll'
                            || style.overflowY === 'overlay'
                        )
                    ) {
                        return el;
                    }

                    el = el.parentElement;
                }

                return null;
                """,
                card,
            )

            if container is not None:
                return container
    except Exception:
        pass

    return driver.execute_script(
        "return document.scrollingElement || document.body;"
    )

def scroll_metrics(driver, container) -> Tuple[int, int, int]:
    try:
        top, height, client = driver.execute_script(
            "return [arguments[0].scrollTop,"
            "arguments[0].scrollHeight,"
            "arguments[0].clientHeight];",
            container,
        )
        return int(top), int(height), int(client)
    except Exception:
        return 0, 0, 0


def reset_container(driver, container) -> None:
    try:
        driver.execute_script(
            "arguments[0].scrollTop=0;"
            "arguments[0].dispatchEvent("
            "new Event('scroll',{bubbles:true}));",
            container,
        )
        time.sleep(0.3)
    except Exception:
        pass


def scroll_once(driver, container) -> bool:
    before_top, _, client = scroll_metrics(
        driver,
        container,
    )

    if client <= 0:
        return False

    step = max(
        400,
        int(client * 0.9),
    )

    try:
        driver.execute_script(
            """
            if (!arguments[0].hasAttribute('tabindex')) {
                arguments[0].setAttribute('tabindex', '-1');
            }

            arguments[0].focus({preventScroll: true});
            """,
            container,
        )
    except Exception:
        pass

    if ScrollOrigin is not None:
        try:
            origin = ScrollOrigin.from_element(
                container,
                0,
                0,
            )

            ActionChains(driver).scroll_from_origin(
                origin,
                0,
                step,
            ).perform()

            time.sleep(0.25)

            after_native, _, _ = scroll_metrics(
                driver,
                container,
            )

            if after_native > before_top:
                return True
        except Exception:
            pass

    try:
        after_js = driver.execute_script(
            """
            const element = arguments[0];
            const step = arguments[1];

            element.scrollTop = Math.min(
                element.scrollTop + step,
                element.scrollHeight
            );

            element.dispatchEvent(
                new Event(
                    'scroll',
                    {bubbles: true}
                )
            );

            return element.scrollTop;
            """,
            container,
            step,
        )

        time.sleep(0.15)
        return int(after_js or 0) > before_top

    except Exception:
        return False


def process_url(
    driver,
    url: str,
    cutoff: date,
) -> Tuple[List[dict], dict]:
    organization = organization_from_url(url)

    if not navigate(driver, url):
        return [], {
            "organization": organization,
            "platform": PLATFORM_NAME,
            "rating_avg": 0,
            "ratings_count": 0,
            "reviews_count": 0,
        }

    accept_cookies(driver)

    try:
        WebDriverWait(
            driver,
            WAIT_TIMEOUT,
        ).until(
            lambda drv: len(
                find_review_cards(
                    drv,
                    find_scroll_container(drv),
                )
            ) > 0
        )
    except TimeoutException:
        print(
            "  reviews cards were not detected "
            "before scrolling"
        )

    rating, ratings_count, reviews_count = extract_summary(driver)
    container = find_scroll_container(driver)
    reset_container(driver, container)

    top, height, client = scroll_metrics(
        driver,
        container,
    )

    print(
        f"  container ready: "
        f"top={top}, height={height}, "
        f"client={client}, "
        f"cards={len(find_review_cards(driver, container))}"
    )

    print(
        f"  date cutoff for organization: "
        f"{cutoff.isoformat()}"
    )

    reviews: List[dict] = []
    index: Dict[str, int] = {}

    added, old = collect_reviews(
        driver,
        container,
        reviews,
        index,
        cutoff,
    )
    print(f"  initial: added={added}, old={old}, total={len(reviews)}")

    idle = 0

    for number in range(
        1,
        SCROLL_HARD_LIMIT + 1,
    ):
        container = find_scroll_container(
            driver
        )

        (
            before_top,
            before_height,
            before_client,
        ) = scroll_metrics(
            driver,
            container,
        )

        moved = scroll_once(
            driver,
            container,
        )
        time.sleep(SCROLL_PAUSE)

        added, old = collect_reviews(
            driver,
            container,
            reviews,
            index,
            cutoff,
        )

        (
            after_top,
            after_height,
            after_client,
        ) = scroll_metrics(
            driver,
            container,
        )

        at_bottom = (
            after_client > 0
            and after_top + after_client
            >= after_height - 5
        )

        progress = (
            moved
            or added > 0
            or after_top > before_top
            or after_height > before_height
        )

        idle = (
            0
            if progress
            else idle + 1
        )

        print(
            f"  scroll {number}: "
            f"moved={moved}, "
            f"top={before_top}->{after_top}, "
            f"height={before_height}->{after_height}, "
            f"added={added}, "
            f"old_skipped={old}, "
            f"total_in_threshold={len(reviews)}, "
            f"bottom={at_bottom}"
        )

        if (
            at_bottom
            and idle >= IDLE_LIMIT
        ):
            print(
                "  stop: reached the end "
                "of the reviews list"
            )
            break

        if idle >= IDLE_LIMIT:
            print(
                f"  stop: no progress after "
                f"{IDLE_LIMIT} attempts"
            )
            break

    for review in reviews:
        review["platform"] = PLATFORM_NAME
        review["organization"] = organization

    summary = {
        "organization": organization,
        "platform": PLATFORM_NAME,
        "rating_avg": (
            rating
            if rating is not None
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

    print(f"  collected={len(reviews)} | org={organization or '-'}")
    return reviews, summary


def read_urls() -> List[str]:
    try:
        urls = [
            line.strip()
            for line in URLS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except FileNotFoundError:
        urls = []
    return urls or [FALLBACK_URL]


def write_new_reviews(
    reviews: List[dict],
) -> None:
    NEW_REVIEWS_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fields = [
        "rating",
        "author",
        "date_iso",
        "text",
        "platform",
        "organization",
    ]

    with NEW_REVIEWS_CSV.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        writer.writerows(reviews)


def main() -> None:
    urls = read_urls()

    (
        existing_keys_by_org,
        latest_dates_by_org,
    ) = load_existing_reviews(
        SOURCE_REVIEWS_CSV
    )

    print(
        f"[INFO] Existing 2GIS organizations: "
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

    fallback_cutoff = get_cutoff_date()

    all_new_reviews: List[dict] = []
    driver = None
    profile = None

    NEW_SUMMARY_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        driver, profile = create_driver()

        with NEW_SUMMARY_CSV.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "organization",
                    "platform",
                    "rating_avg",
                    "ratings_count",
                    "reviews_count",
                ],
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()

            for index, url in enumerate(
                urls,
                start=1,
            ):
                organization = organization_from_url(
                    url
                )
                org_key = organization_key(
                    organization
                )

                existing_keys = (
                    existing_keys_by_org.setdefault(
                        org_key,
                        set(),
                    )
                )
                latest_date = latest_dates_by_org.get(
                    org_key
                )
                cutoff = (
                    latest_date
                    if latest_date is not None
                    else fallback_cutoff
                )

                print(
                    f"[{index}/{len(urls)}] {url} "
                    f"-> org='{organization or '-'}' | "
                    f"existing={len(existing_keys)} | "
                    f"threshold={cutoff.isoformat()}"
                )

                reviews, summary = process_url(
                    driver,
                    url,
                    cutoff,
                )

                new_for_url: List[dict] = []

                for review in reviews:
                    key = stored_review_key(
                        review.get("author"),
                        review.get("date_iso"),
                        review.get("text"),
                    )

                    if key in existing_keys:
                        continue

                    existing_keys.add(key)
                    new_for_url.append(review)

                    parsed_date = parse_stored_date(
                        review.get("date_iso")
                    )
                    current_latest = (
                        latest_dates_by_org.get(
                            org_key
                        )
                    )

                    if (
                        parsed_date is not None
                        and (
                            current_latest is None
                            or parsed_date > current_latest
                        )
                    ):
                        latest_dates_by_org[
                            org_key
                        ] = parsed_date

                all_new_reviews.extend(
                    new_for_url
                )

                writer.writerow(summary)
                file.flush()

                print(
                    f"  collected in threshold: "
                    f"{len(reviews)} | "
                    f"new after comparison: "
                    f"{len(new_for_url)}"
                )

    except SessionNotCreatedException as error:
        print(
            f"[2GIS WARN] "
            f"{type(error).__name__}: "
            f"{error}"
        )

    finally:
        close_driver(driver, profile)

    write_new_reviews(all_new_reviews)

    print(
        f"Done. New reviews: "
        f"{len(all_new_reviews)}. "
        f"New reviews -> {NEW_REVIEWS_CSV} | "
        f"Source unchanged -> "
        f"{SOURCE_REVIEWS_CSV} | "
        f"Summary -> {NEW_SUMMARY_CSV}"
    )


if __name__ == "__main__":
    main()