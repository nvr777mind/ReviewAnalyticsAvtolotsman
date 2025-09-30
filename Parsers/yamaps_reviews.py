# 2gis_reviews_yandex.py
# -*- coding: utf-8 -*-

import os
import re
import csv
import time
import shutil
import tempfile
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


DEFAULT_URL = "https://2gis.ru/penza/firm/70000001057701394/tab/reviews"
DEFAULT_BROWSER = r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe"
DEFAULT_DRIVER  = r"E:\YandexDriver\yandexdriver.exe"   # поменяйте под себя


# -------------------- Утилиты --------------------

RU_MONTHS = {
    'января':1,'февраля':2,'марта':3,'апреля':4,'мая':5,'июня':6,
    'июля':7,'августа':8,'сентября':9,'октября':10,'ноября':11,'декабря':12
}
DATE_RE = re.compile(r'(\d{1,2})\s+([А-Яа-я]+)\s+(\d{4})')
AD_TRASH = re.compile(r"(за[её]м|кредит|микрофинанс|1\s*место|реклама|акци[яи])", re.I)

def ru_date_to_iso(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low == "сегодня":
        return datetime.now().strftime("%Y-%m-%d")
    if low == "вчера":
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    m = DATE_RE.search(s)
    if m:
        d, mon_name, y = m.group(1), m.group(2).lower(), m.group(3)
        mon = RU_MONTHS.get(mon_name)
        if mon:
            try:
                return datetime(int(y), mon, int(d)).strftime("%Y-%m-%d")
            except Exception:
                return ""
    # ISO/датавремя
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""

def looks_like_review(rec: Dict[str, str]) -> bool:
    text = (rec.get("text") or "").strip()
    if not text:
        return False
    if AD_TRASH.search(text):
        return False
    return True

def save_csv(rows: List[Dict[str, str]], path: str):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["author","rating","date_raw","date_iso","text"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] Сохранено {len(rows)} отзывов в {path}")


# -------------------- Драйвер Я.Браузера --------------------

def build_driver(browser_path: str, driver_path: str, headless: bool=False) -> Tuple[webdriver.Chrome, str]:
    if not os.path.isfile(browser_path):
        raise FileNotFoundError(f"Не найден браузер: {browser_path}")
    if not os.path.isfile(driver_path):
        raise FileNotFoundError(f"Не найден драйвер: {driver_path}")

    opts = webdriver.ChromeOptions()
    opts.binary_location = browser_path

    # стабильный старт (фикс DevToolsActivePort / SSL -101 и т.п.)
    opts.add_argument("--remote-debugging-port=0")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-quic")
    opts.add_argument("--proxy-server=direct://")
    opts.add_argument("--proxy-bypass-list=*")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-running-insecure-content")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=ru-RU,ru")
    opts.add_argument("--start-maximized")
    if headless:
        opts.add_argument("--headless=new")

    # чистый временный профиль — меньше шансов на краш
    tmp_profile = tempfile.mkdtemp(prefix="yab_profile_")
    opts.add_argument(fr"--user-data-dir={tmp_profile}")

    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=opts)

    # скрыть webdriver и ускорить сеть
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        })
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception:
        pass

    return driver, tmp_profile


# -------------------- Парсинг DOM без классов --------------------

def expand_more(driver):
    # «Читать целиком»/«Ещё» на разных страницах
    xps = [
        '//button[contains(normalize-space(.),"Читать целиком")]',
        '//button[contains(normalize-space(.),"Показать ещё")]',
        '//button[contains(normalize-space(.),"Ещё")]',
        '//div[@role="button"][contains(normalize-space(.),"Ещё")]',
        '//button[contains(., "Read more")]',
        '//a[contains(normalize-space(.),"Читать целиком")]',
    ]
    for xp in xps:
        btns = driver.find_elements(By.XPATH, xp)
        for b in btns:
            try:
                if b.is_displayed():
                    driver.execute_script("arguments[0].click();", b)
                    time.sleep(0.15)
            except Exception:
                pass

def smooth_scroll(driver, steps=3, dy=1000):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        time.sleep(0.25)

def guess_review_blocks(driver) -> List:
    """
    Ищем «карточки» по текстовым маркерам, не по классам:
    - в карточке почти всегда есть «Полезно» (кнопка) и/или «Читать целиком»;
    - рядом с автором бывает строка «N отзыв/отзыва/отзывов».
    """
    # 1) сначала большие контейнеры, где внутри встречается «Полезно»
    blocks = driver.find_elements(
        By.XPATH,
        '//div[.//text()[contains(., "Полезно")] or .//text()[contains(., "Читать целиком")]]'
    )
    # 2) плюс любые <article> с «Полезно»
    blocks += driver.find_elements(By.XPATH, '//article[.//text()[contains(., "Полезно")]]')

    # чуть почистим дубли (по веб-элементам Selenium сравнение не работает — оставим как есть)
    return blocks

def extract_from_block(block) -> Optional[Dict[str, str]]:
    raw = (block.text or "").strip()
    if not raw:
        return None

    # Разбиваем на строки и чистим явный мусор
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    if not lines:
        return None

    # 1) Автор — ищем строку вида «Имя Фамилия ... N отзыв»
    author = ""
    for i, ln in enumerate(lines[:4]):  # как правило, в первых строках
        if re.search(r"\b\d+\s+отзыв", ln, re.I) or re.search(r"\b\d+\s+отзыва", ln, re.I) or re.search(r"\b\d+\s+отзывов", ln, re.I):
            # имя автора — всё, что до «N отзыв»
            author = re.sub(r"\s*\b\d+\s+отзыв(а|ов)?\b.*", "", ln, flags=re.I).strip()
            # иногда впереди инициалы «АШ», «АГ» — отрежем, если это 2 заглавные
            author = re.sub(r"^[А-ЯЁ]{1,2}\s+", "", author)
            break
    if not author:
        # fallback: возьмём первую информативную строку, где нет «Полезно/Читать»
        for ln in lines[:5]:
            if ("Полезно" not in ln) and ("Читать" not in ln) and (len(ln.split()) >= 2):
                author = ln.strip()
                break

    # 2) Дата — ищем по русскому формату или «Сегодня/Вчера»
    date_raw = ""
    for ln in lines:
        if DATE_RE.search(ln) or ln.lower() in ("сегодня","вчера"):
            date_raw = ln.strip()
            break
    date_iso = ru_date_to_iso(date_raw) if date_raw else ""

    # 3) Рейтинг — на 2ГИС не всегда есть звёзды в карточке; попытаемся вытащить из aria-label/текста
    rating = ""
    try:
        rate_el = None
        for xp in [
            './/*[@aria-label[contains(., "из 5")]]',
            './/*[@title[contains(., "из 5")]]',
            './/*[contains(., "из 5")]'
        ]:
            els = block.find_elements(By.XPATH, xp)
            if els:
                rate_el = els[0]
                break
        if rate_el:
            txt = (rate_el.get_attribute("aria-label") or rate_el.get_attribute("title") or rate_el.text or "").strip()
            m = re.search(r'(\d+(?:[.,]\d+)?)\s*из\s*5', txt)
            if m:
                rating = m.group(1).replace(',', '.')
    except Exception:
        pass

    # 4) Текст — всё между автором и «Полезно/Читать целиком»
    # Возьмём весь текст и выкинем явные служебные строки
    body_lines = []
    for ln in lines:
        if any(x in ln for x in ("Полезно","Читать целиком","С ответами","Положительные","Отрицательные")):
            continue
        if re.search(r"\b\d+\s+отзыв", ln, re.I):  # строка автора с числом отзывов — пропускаем
            continue
        body_lines.append(ln)
    # Часто первая строка — автор, вторая — уже текст. Подрежем шапку если совпадает с автором
    if author and body_lines and author in body_lines[0]:
        body_lines = body_lines[1:]
    text = " ".join(body_lines).strip()

    rec = {"author": author, "rating": rating, "date_raw": date_raw, "date_iso": date_iso, "text": text}
    if not looks_like_review(rec):
        return None
    return rec


def scrape_reviews(driver, max_rounds: int = 80) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()

    for _ in range(max_rounds):
        smooth_scroll(driver, steps=2, dy=1200)
        expand_more(driver)
        time.sleep(0.2)

        blocks = guess_review_blocks(driver)
        for b in blocks:
            try:
                rec = extract_from_block(b)
            except Exception:
                rec = None
            if not rec:
                continue
            key = (rec["author"].lower(), rec["date_raw"].lower(), rec["text"][:40].lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)

        # если карточек немного и список не растёт — можно завершать
        if len(out) >= 3:  # для текущей карточки 2ГИС действительно 3 отзыва
            break

    return out


# -------------------- main --------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL, help="Ссылка на вкладку отзывов 2ГИС")
    ap.add_argument("--browser", default=DEFAULT_BROWSER, help="Путь к YandexBrowser browser.exe")
    ap.add_argument("--driver",  default=DEFAULT_DRIVER,  help="Путь к yandexdriver.exe")
    ap.add_argument("--headless", action="store_true", help="Headless режим")
    ap.add_argument("--rounds", type=int, default=80, help="Максимум циклов прокрутки")
    ap.add_argument("--out", default="2gis_reviews.csv", help="CSV для сохранения")
    args = ap.parse_args()

    driver, tmp_profile = build_driver(args.browser, args.driver, args.headless)
    try:
        driver.get(args.url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        ActionChains(driver).move_by_offset(5, 5).perform()
        time.sleep(0.8)

        # иногда полезно щёлкнуть по вкладке «Отзывы», если не активна
        for xp in ['//a[contains(normalize-space(.),"Отзывы")]', '//button[contains(normalize-space(.),"Отзывы")]']:
            tabs = driver.find_elements(By.XPATH, xp)
            for t in tabs:
                try:
                    if t.is_displayed():
                        driver.execute_script("arguments[0].click();", t)
                        time.sleep(0.2)
                        break
                except Exception:
                    pass

        rows = scrape_reviews(driver, max_rounds=args.rounds)
        # небольшой постпроцесс рейтинга
        for r in rows:
            if r["rating"]:
                try:
                    r["rating"] = float(r["rating"])
                except Exception:
                    pass

        save_csv(rows, args.out)

    finally:
        driver.quit()
        if tmp_profile and os.path.isdir(tmp_profile):
            try:
                shutil.rmtree(tmp_profile, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    main()
