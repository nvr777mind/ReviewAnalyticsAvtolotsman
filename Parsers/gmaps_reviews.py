import calendar
import csv
import os
import platform
import re
import subprocess
import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse, unquote

from selenium import webdriver
from selenium.common.exceptions import (
    SessionNotCreatedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

try:
    from urllib3.exceptions import NotOpenSSLWarning
except ImportError:
    NotOpenSSLWarning = None

if NotOpenSSLWarning:
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)


BASE_DIR = Path(__file__).resolve().parent.parent
URLS_FILE = BASE_DIR / "Urls" / "gmaps_urls.txt"
REVIEWS_CSV = BASE_DIR / "Csv" / "Reviews" / "gmaps_reviews.csv"
SUMMARY_CSV = BASE_DIR / "Csv" / "Summary" / "gmaps_summary.csv"

DRIVER_PATH = (
    BASE_DIR / "Drivers" / "Windows" / "yandexdriver.exe"
    if platform.system() == "Windows"
    else BASE_DIR / "Drivers" / "MacOS" / "yandexdriver"
)
SCRAPER_PROFILE_DIR = Path.home() / ".gmaps-scraper-profile"

FIRST_WAIT = 12
NEXT_WAIT = 3
MAX_SCROLL_SECONDS = 180
SCROLL_HARD_LIMIT = 3000
SCROLL_PAUSE = 0.35 if platform.system() == "Windows" else 0.25
NO_GROWTH_LIMIT = 12 if platform.system() == "Windows" else 10
CUTOFF_YEARS = 2
CUTOFF_EXTRA_DAYS = 10

PLATFORM_NAME = "Google Maps"
DEFAULT_ORGANIZATION = "avtolotsman"

REVIEW_CARD_SELECTOR = "div.jftiEf.fontBodyMedium, div.jftiEf"
AUTHOR_SELECTOR = ".d4r55.fontTitleMedium"
RATING_SELECTOR = ".kvMYJc, span[aria-label*='из 5'], span[aria-label*='out of 5']"
DATE_SELECTOR = ".rsqaWe"
TEXT_SELECTOR = ".wiI7pd"
EXPAND_BUTTON_SELECTOR = "button.w8nwRe.kyuRq"

SUMMARY_RATING_SELECTOR = "div.fontDisplayLarge"
SUMMARY_COUNT_SELECTOR = "div.fontBodySmall"

CONTAINER_SELECTORS = (
    "div.m6QErb.DxyBCb",
    "div.m6QErb.XiKgde",
    "div[aria-label*='Отзывы']",
    "div[aria-label*='Reviews']",
)

RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def find_yandex_browser() -> Optional[Path]:
    custom = os.environ.get("YANDEX_BROWSER_PATH")
    if custom:
        path = Path(custom).expanduser()
        if path.is_file():
            return path

    if platform.system() == "Windows":
        candidates = [
            Path.home() / "AppData" / "Local" / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
        ]
    else:
        candidates = [Path("/Applications/Yandex.app/Contents/MacOS/Yandex")]

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


def create_driver() -> webdriver.Chrome:
    browser = find_yandex_browser()
    if browser is None:
        raise FileNotFoundError("Yandex Browser not found")
    if not DRIVER_PATH.is_file():
        raise FileNotFoundError(f"Yandex Driver not found: {DRIVER_PATH}")

    stop_stale_drivers()
    SCRAPER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.binary_location = str(browser)
    for argument in (
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--enable-gpu-rasterization",
        "--no-sandbox",
        f"--user-data-dir={SCRAPER_PROFILE_DIR}",
        "--profile-directory=Default",
    ):
        options.add_argument(argument)

    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])

    driver = webdriver.Chrome(service=Service(str(DRIVER_PATH)), options=options)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(45)
    driver.implicitly_wait(0)
    return driver


def add_hl_ru(url: str) -> str:
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["hl"] = ["ru"]
        encoded_query = urlencode({key: value[0] if isinstance(value, list) else value for key, value in query.items()})
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, encoded_query, parsed.fragment))
    except Exception:
        return url


def force_reviews_url(url: str) -> str:
    url = add_hl_ru(url)
    if "!9m1!1b1" in url:
        return url
    try:
        parsed = urlparse(url)
        if "/data=" not in parsed.path:
            return url
        path = parsed.path.rstrip("/") + "!9m1!1b1"
        return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment))
    except Exception:
        return url


def organization_from_url_or_title(driver, url: str) -> str:
    try:
        match = re.search(r"/place/([^/]+)", urlparse(url).path)
        if match:
            slug = unquote(match.group(1)).replace("+", " ").split("@", 1)[0].strip()
            if slug:
                return slug
    except Exception:
        pass
    try:
        title = re.split(r"– Google| - Google", driver.title or "")[0].strip()
        return title or DEFAULT_ORGANIZATION
    except Exception:
        return DEFAULT_ORGANIZATION


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def subtract_months(value: datetime, months: int) -> datetime:
    year = value.year
    month = value.month - months
    while month <= 0:
        month += 12
        year -= 1
    return value.replace(year=year, month=month, day=min(value.day, last_day_of_month(year, month)))


def subtract_years(value: datetime, years: int) -> datetime:
    year = value.year - years
    return value.replace(year=year, day=min(value.day, last_day_of_month(year, value.month)))


def apply_relative_delta(now: datetime, unit: str, amount: int) -> datetime:
    if unit == "seconds":
        return now - timedelta(seconds=amount)
    if unit == "minutes":
        return now - timedelta(minutes=amount)
    if unit == "hours":
        return now - timedelta(hours=amount)
    if unit == "days":
        return now - timedelta(days=amount)
    if unit == "weeks":
        return now - timedelta(weeks=amount)
    if unit == "months":
        return subtract_months(now, amount)
    if unit == "years":
        return subtract_years(now, amount)
    return now


def parse_relative_date_ru(text: str, now: Optional[datetime] = None) -> Optional[str]:
    if not text:
        return None
    now = now or datetime.now()
    value = text.strip().lower()

    direct = {
        "сегодня": ("days", 0),
        "вчера": ("days", 1),
        "позавчера": ("days", 2),
        "только что": ("seconds", 0),
        "сейчас": ("seconds", 0),
        "день назад": ("days", 1),
        "неделю назад": ("weeks", 1),
        "месяц назад": ("months", 1),
        "год назад": ("years", 1),
        "час назад": ("hours", 1),
        "минуту назад": ("minutes", 1),
        "секунду назад": ("seconds", 1),
    }
    for key, (unit, amount) in direct.items():
        if value.startswith(key):
            return apply_relative_delta(now, unit, amount).date().isoformat()

    match = re.search(r"(\d+)\s+([^\s]+).*назад", value)
    if not match:
        return None

    amount = int(match.group(1))
    unit_word = match.group(2)
    unit_prefixes = {
        "seconds": ("сек", "секун"),
        "minutes": ("мин", "минут", "мину"),
        "hours": ("час", "часа", "часов"),
        "days": ("день", "дня", "дней", "сут"),
        "weeks": ("недел", "нед"),
        "months": ("месяц", "месяца", "месяцев"),
        "years": ("год", "года", "лет"),
    }
    for unit, prefixes in unit_prefixes.items():
        if unit_word.startswith(prefixes):
            return apply_relative_delta(now, unit, amount).date().isoformat()
    return None


def parse_absolute_date(text: str) -> Optional[str]:
    if not text:
        return None
    value = text.strip().lower().replace(" г.", "").replace("г.", "").strip()

    ru_match = re.match(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", value)
    if ru_match:
        day = int(ru_match.group(1))
        month = RU_MONTHS.get(ru_match.group(2))
        year = int(ru_match.group(3))
        if month:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None

    for pattern in (r"([a-z]+)\s+(\d{1,2}),\s*(\d{4})", r"(\d{1,2})\s+([a-z]+)\s+(\d{4})"):
        match = re.match(pattern, value)
        if not match:
            continue
        if pattern.startswith("([a-z]"):
            month_name = match.group(1)
            day = int(match.group(2))
            year = int(match.group(3))
        else:
            day = int(match.group(1))
            month_name = match.group(2)
            year = int(match.group(3))
        month = EN_MONTHS.get(month_name)
        if month:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_review_date(text: str) -> Optional[str]:
    return parse_relative_date_ru(text) or parse_absolute_date(text)


def get_cutoff_date() -> date:
    today = datetime.now().date()
    try:
        cutoff = today.replace(year=today.year - CUTOFF_YEARS)
    except ValueError:
        cutoff = today.replace(year=today.year - CUTOFF_YEARS, day=28)
    return cutoff - timedelta(days=CUTOFF_EXTRA_DAYS)


def reviews_are_visible(driver) -> bool:
    try:
        return any(card.is_displayed() for card in driver.find_elements(By.CSS_SELECTOR, REVIEW_CARD_SELECTOR))
    except Exception:
        return False


def accept_cookies(driver) -> None:
    try:
        clicked = driver.execute_script(
            """
            const words = ['принять', 'accept'];
            for (const button of document.querySelectorAll('button, [role="button"]')) {
                const text = (button.innerText || button.textContent || button.getAttribute('aria-label') || '').trim().toLowerCase();
                if (words.some(word => text.includes(word))) {
                    button.click();
                    return text;
                }
            }
            return '';
            """
        )
        if clicked:
            print(f"  cookie accepted: {clicked!r}")
    except Exception:
        pass


def click_reviews_panel(driver) -> bool:
    if reviews_are_visible(driver):
        print("  reviews are already visible")
        return True

    selectors = (
        "[role='tab'][aria-label*='отзыв' i]",
        "[role='tab'][aria-label*='review' i]",
        "button[aria-label*='отзыв' i]",
        "button[aria-label*='review' i]",
        "[role='button'][aria-label*='отзыв' i]",
        "[role='button'][aria-label*='review' i]",
        "[jsaction*='moreReviews']",
        "[jsaction*='review']",
        "div.F7nice",
        "span.UY7F9",
        "span.MW4etd",
        "div.fontDisplayLarge",
    )

    candidates = []
    seen: Set[str] = set()
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for element in elements:
            try:
                if element.id in seen or not element.is_displayed():
                    continue
                seen.add(element.id)
                candidates.append(element)
            except Exception:
                continue

    print(f"  review opener candidates: {len(candidates)}")

    for element in candidates:
        try:
            label = (element.get_attribute("aria-label") or element.get_attribute("title") or element.text or element.get_attribute("class") or "").strip()
            lowered = label.lower()
            if any(value in lowered for value in ("оставить отзыв", "написать отзыв", "write a review", "add a review")):
                continue

            clickable = driver.execute_script(
                """
                let el = arguments[0];
                while (el && el !== document.body) {
                    const role = el.getAttribute ? (el.getAttribute('role') || '') : '';
                    const jsaction = el.getAttribute ? (el.getAttribute('jsaction') || '') : '';
                    if (el.tagName === 'BUTTON' || el.tagName === 'A' || role === 'button' || role === 'tab' || /review/i.test(jsaction)) return el;
                    el = el.parentElement;
                }
                return arguments[0];
                """,
                element,
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", clickable)
            driver.execute_script("arguments[0].click();", clickable)
            print(f"  clicked review opener: {label!r}")
            WebDriverWait(driver, 10).until(reviews_are_visible)
            print("  reviews opened")
            return True
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    print("  reviews panel did not open")
    return False


def open_reviews(driver, url: str) -> bool:
    reviews_url = force_reviews_url(url)
    normal_url = add_hl_ru(url)
    driver.get(reviews_url)
    time.sleep(FIRST_WAIT)
    accept_cookies(driver)

    if reviews_are_visible(driver) or click_reviews_panel(driver):
        return True

    if reviews_url != normal_url:
        print("  direct reviews URL failed; trying normal URL")
        driver.get(normal_url)
        time.sleep(NEXT_WAIT)
        accept_cookies(driver)
        if reviews_are_visible(driver) or click_reviews_panel(driver):
            return True
    return False


def find_reviews_container(driver):
    for selector in CONTAINER_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                if driver.execute_script("return arguments[0].scrollHeight > arguments[0].clientHeight;", element):
                    return element
            except Exception:
                continue
    return None


def focus_container(driver, container) -> None:
    try:
        ActionChains(driver).move_to_element(container).pause(0.05).click().perform()
    except Exception:
        try:
            driver.execute_script("arguments[0].focus();", container)
        except Exception:
            pass


def is_stale(driver, element) -> bool:
    try:
        return not driver.execute_script("return arguments[0] && document.contains(arguments[0]);", element)
    except Exception:
        return True


def parse_rating(value: str) -> Optional[float]:
    if not value:
        return None
    match = re.search(r"([0-5](?:[.,]\d)?)", value)
    return float(match.group(1).replace(",", ".")) if match else None


def parse_int(value: str) -> Optional[int]:
    if not value:
        return None
    match = re.search(r"\d[\d\s\u00a0\u202f]*", value)
    digits = re.sub(r"\D", "", match.group(0)) if match else ""
    return int(digits) if digits else None


def extract_summary(driver) -> Tuple[Optional[float], Optional[int]]:
    rating = None
    ratings_count = None

    try:
        for element in driver.find_elements(By.CSS_SELECTOR, SUMMARY_RATING_SELECTOR):
            rating = parse_rating(element.text)
            if rating is not None:
                break
    except Exception:
        pass

    try:
        for element in driver.find_elements(By.CSS_SELECTOR, SUMMARY_COUNT_SELECTOR):
            text = (element.text or "").replace("\xa0", " ")
            lowered = text.lower()
            if "отзыв" not in lowered and "review" not in lowered:
                continue
            ratings_count = parse_int(text)
            if ratings_count is not None:
                break
    except Exception:
        pass

    return rating, ratings_count


def set_sort_newest(driver) -> bool:
    button = None
    for xpath in (
        "//button[@aria-label='Самые релевантные']",
        "//button[@aria-label='Most relevant']",
        "//button[@aria-label='Сначала новые']",
        "//button[@aria-label='Newest']",
    ):
        try:
            button = WebDriverWait(driver, 4).until(lambda drv: drv.find_element(By.XPATH, xpath))
            driver.execute_script("arguments[0].click();", button)
            break
        except Exception:
            button = None

    if button is None:
        return False

    try:
        menu = WebDriverWait(driver, 4).until(lambda drv: drv.find_element(By.XPATH, "//div[@role='menu' or @role='listbox']"))
        item = menu.find_element(By.XPATH, ".//*[normalize-space(text())='Сначала новые' or normalize-space(text())='Newest']")
        clickable = driver.execute_script(
            """
            let el = arguments[0];
            while (el && el.tagName !== 'BUTTON' && !/menuitem|option/i.test(el.getAttribute('role') || '')) el = el.parentElement;
            return el || arguments[0];
            """,
            item,
        )
        driver.execute_script("arguments[0].click();", clickable)
        time.sleep(0.6)
        return True
    except Exception:
        return False


def expand_visible_reviews(root) -> None:
    try:
        buttons = root.find_elements(By.CSS_SELECTOR, EXPAND_BUTTON_SELECTOR)
    except Exception:
        return
    for button in buttons:
        try:
            if button.is_displayed() and button.is_enabled():
                button.click()
                time.sleep(0.02)
        except Exception:
            continue


def extract_review(card) -> dict:
    expand_visible_reviews(card)
    author = ""
    rating = None
    date_iso = None
    text = ""

    try:
        author = card.find_element(By.CSS_SELECTOR, AUTHOR_SELECTOR).text.strip()
    except Exception:
        pass

    try:
        rating_element = card.find_element(By.CSS_SELECTOR, RATING_SELECTOR)
        rating = parse_rating(rating_element.get_attribute("aria-label") or rating_element.text or rating_element.get_attribute("title") or "")
    except Exception:
        pass

    try:
        date_element = card.find_element(By.CSS_SELECTOR, DATE_SELECTOR)
        date_text = (date_element.text or date_element.get_attribute("aria-label") or "").strip()
        date_iso = parse_review_date(date_text)
    except Exception:
        pass

    try:
        texts = [item.text.strip() for item in card.find_elements(By.CSS_SELECTOR, TEXT_SELECTOR) if item.text.strip()]
        if texts:
            text = max(texts, key=len)
    except Exception:
        pass

    return {"rating": rating, "author": author, "date_iso": date_iso, "text": text}


def review_key(review: dict) -> Tuple[str, str]:
    author = (review.get("author") or "").strip().lower()
    text = re.sub(r"\s+", " ", (review.get("text") or "").strip().lower())
    return author, text[:200]


def count_cards(container) -> Tuple[int, int]:
    try:
        cards = container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_SELECTOR)
    except Exception:
        return 0, 0

    text_count = 0
    for card in cards:
        try:
            if card.find_elements(By.CSS_SELECTOR, TEXT_SELECTOR):
                text_count += 1
        except Exception:
            pass
    return len(cards), text_count


def scroll_to_end(driver, container) -> Tuple[int, int]:
    start_time = monotonic()
    last_height = -1
    last_cards = -1
    last_text_cards = -1
    no_growth = 0

    focus_container(driver, container)

    for iteration in range(1, SCROLL_HARD_LIMIT + 1):
        if monotonic() - start_time > MAX_SCROLL_SECONDS:
            break

        if is_stale(driver, container):
            container = find_reviews_container(driver)
            if container is None:
                break
            focus_container(driver, container)

        try:
            expand_visible_reviews(container)
            cards_count, text_cards = count_cards(container)
            height = int(driver.execute_script("return arguments[0].scrollHeight;", container) or 0)
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)

            if iteration % 3 == 0:
                try:
                    container.send_keys(Keys.PAGE_DOWN)
                except Exception:
                    pass
            if iteration % 6 == 0:
                try:
                    container.send_keys(Keys.END)
                except Exception:
                    pass
            if iteration % 12 == 0:
                driver.execute_script(
                    "arguments[0].scrollTop = Math.max(0, arguments[0].scrollTop - 300); arguments[0].scrollTop = arguments[0].scrollHeight;",
                    container,
                )

            grew = height != last_height or cards_count != last_cards or text_cards != last_text_cards
            no_growth = 0 if grew else no_growth + 1
            last_height = height
            last_cards = cards_count
            last_text_cards = text_cards

            if no_growth >= NO_GROWTH_LIMIT:
                break
            time.sleep(SCROLL_PAUSE)

        except StaleElementReferenceException:
            container = find_reviews_container(driver)
            if container is None:
                break
            focus_container(driver, container)
            time.sleep(0.1)
        except Exception:
            break

    try:
        expand_visible_reviews(container)
        return count_cards(container)
    except Exception:
        return last_cards, last_text_cards


def collect_recent_reviews(driver, container, cutoff_date: date) -> Tuple[List[dict], int]:
    _, total_text_reviews = scroll_to_end(driver, container)
    try:
        cards = container.find_elements(By.CSS_SELECTOR, REVIEW_CARD_SELECTOR)
    except Exception:
        cards = []

    reviews: List[dict] = []
    seen: Set[Tuple[str, str]] = set()
    for card in cards:
        try:
            review = extract_review(card)
        except Exception:
            continue

        text = (review.get("text") or "").strip()
        date_iso = review.get("date_iso")
        if not text or not date_iso:
            continue

        try:
            review_date = datetime.fromisoformat(date_iso[:10]).date()
        except Exception:
            continue

        if review_date < cutoff_date:
            continue

        key = review_key(review)
        if key in seen:
            continue
        seen.add(key)

        review["date_iso"] = review_date.isoformat()
        review["text"] = text.replace("\r", " ").replace("\n", " ").strip()
        reviews.append(review)

    return reviews, total_text_reviews


def read_urls() -> List[str]:
    try:
        return [line.strip() for line in URLS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def write_review_rows(writer, rows: List[dict], organization: str) -> None:
    for row in rows:
        writer.writerow(
            {
                "rating": row.get("rating"),
                "author": (row.get("author") or "").strip(),
                "date_iso": row.get("date_iso") or "",
                "text": (row.get("text") or "").strip(),
                "platform": PLATFORM_NAME,
                "organization": organization,
            }
        )


def process_url(driver, url: str, review_writer, summary_writer, index: int, total: int, cutoff_date: date) -> None:
    print(f"[{index}/{total}] {force_reviews_url(url)}")

    if not open_reviews(driver, url):
        print("  reviews panel is unavailable, skipping")
        return

    time.sleep(1.2)
    set_sort_newest(driver)
    rating, ratings_count = extract_summary(driver)

    container = find_reviews_container(driver)
    if container is None:
        click_reviews_panel(driver)
        container = find_reviews_container(driver)
    if container is None:
        print("  reviews container not found, skipping")
        return

    organization = organization_from_url_or_title(driver, url)
    reviews, total_text_reviews = collect_recent_reviews(driver, container, cutoff_date)
    write_review_rows(review_writer, reviews, organization)

    summary_writer.writerow(
        {
            "organization": organization,
            "platform": PLATFORM_NAME,
            "rating_avg": rating if rating is not None else "",
            "ratings_count": ratings_count if ratings_count is not None else "",
            "reviews_count": total_text_reviews,
        }
    )

    print(
        f"  summary: rating={rating}, ratings={ratings_count}, "
        f"text_total={total_text_reviews}, written_recent={len(reviews)}"
    )


def main() -> None:
    urls = read_urls()
    if not urls:
        print(f"No URLs found: {URLS_FILE}")
        return

    cutoff = get_cutoff_date()
    print(f"cutoff date: {cutoff.isoformat()}")
    print(f"Google Maps scraper profile: {SCRAPER_PROFILE_DIR}")

    REVIEWS_CSV.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)

    driver = None
    try:
        driver = create_driver()
        with (
            REVIEWS_CSV.open("w", newline="", encoding="utf-8") as reviews_file,
            SUMMARY_CSV.open("w", newline="", encoding="utf-8") as summary_file,
        ):
            review_writer = csv.DictWriter(
                reviews_file,
                fieldnames=["rating", "author", "date_iso", "text", "platform", "organization"],
                quoting=csv.QUOTE_ALL,
            )
            summary_writer = csv.DictWriter(
                summary_file,
                fieldnames=["organization", "platform", "rating_avg", "ratings_count", "reviews_count"],
                quoting=csv.QUOTE_ALL,
            )
            review_writer.writeheader()
            summary_writer.writeheader()

            for index, url in enumerate(urls, start=1):
                process_url(driver, url, review_writer, summary_writer, index, len(urls), cutoff)
                reviews_file.flush()
                summary_file.flush()

    except SessionNotCreatedException as error:
        print(f"[GMAPS WARN] {type(error).__name__}: {error}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass

    print(f"Done. Reviews -> {REVIEWS_CSV} | Summary -> {SUMMARY_CSV}")


if __name__ == "__main__":
    main()