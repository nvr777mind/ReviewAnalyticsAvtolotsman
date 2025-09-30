# 2gis_reviews_yandex.py
# -*- coding: utf-8 -*-
"""
Сбор отзывов 2ГИС (вкладка /tab/reviews) через Selenium + Яндекс.Браузер.
Карточки ищем по текстовым маркерам, а не по классам:
  - в карточке есть "Полезно"
  - и та же карточка содержит строку автора вида "... 7 отзывов"

CSV: author,rating,date_raw,date_iso,text
"""

import os
import re
import csv
import time
import shutil
import tempfile
import argparse
from typing import List, Dict, Tuple, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ====== НАСТРОЙКИ ПО УМОЛЧАНИЮ ======
DEFAULT_URL = "https://2gis.ru/penza/firm/70000001057701394/tab/reviews"
DEFAULT_BROWSER = r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe"
DEFAULT_DRIVER  = r"E:\YandexDriver\yandexdriver.exe"   # поправь под себя

# Регексы для автора/мусора
RE_AUTHOR_LINE = re.compile(r"\b\d+\s+отзыв(ов|а)?\b", re.IGNORECASE)
RE_TRASH = re.compile(r"(Лицензионное соглашение|политика конфиденциальности|Добавить организацию|Реклама в 2ГИС)", re.I)


# ====== ВСПОМОГАТЕЛЬНОЕ ======
def build_driver(browser_path: str, driver_path: str, headless: bool=False) -> Tuple[webdriver.Chrome, str]:
    if not os.path.isfile(browser_path):
        raise FileNotFoundError(f"Не найден браузер: {browser_path}")
    if not os.path.isfile(driver_path):
        raise FileNotFoundError(f"Не найден драйвер: {driver_path}")

    opts = webdriver.ChromeOptions()
    opts.binary_location = browser_path

    # Устойчивый старт ЯБ (чинит DevToolsActivePort/SSL -101)
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

    # ВРЕМЕННЫЙ чистый профиль (реальный профиль часто блокируется/крашится)
    tmp_profile = tempfile.mkdtemp(prefix="yab_profile_")
    opts.add_argument(fr"--user-data-dir={tmp_profile}")

    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=opts)

    # спрячем webdriver и дадим сети поработать без кэша
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        })
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception:
        pass

    return driver, tmp_profile


def wait_ready(driver, url: str, timeout: int=25):
    driver.get(url)
    WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(0.6)
    ActionChains(driver).move_by_offset(5, 5).perform()
    # убедимся, что активна именно вкладка «Отзывы» — ткнём по ней, если есть
    for xp in ('//a[contains(normalize-space(.),"Отзывы")]', '//button[contains(normalize-space(.),"Отзывы")]'):
        tabs = driver.find_elements(By.XPATH, xp)
        for t in tabs:
            try:
                if t.is_displayed():
                    driver.execute_script("arguments[0].click();", t)
                    time.sleep(0.25)
                    break
            except Exception:
                pass


def page_scroll(driver, rounds: int=4, dy: int=1200):
    for _ in range(rounds):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        time.sleep(0.25)


def click_read_more(el):
    """Пробуем кликнуть 'Читать целиком' внутри карточки (любой тег)."""
    for xp in ['.//*[contains(normalize-space(.),"Читать целиком")]',
               './/*[contains(normalize-space(.),"Читать ещё")]',
               './/*[contains(normalize-space(.),"показать полностью")]']:
        btns = el.find_elements(By.XPATH, xp)
        for b in btns:
            try:
                if b.is_displayed():
                    b.click()
                    time.sleep(0.15)
            except Exception:
                try:
                    b_parent = b.find_element(By.XPATH, './ancestor-or-self::*[1]')
                    el._parent.execute_script("arguments[0].click();", b_parent)
                    time.sleep(0.15)
                except Exception:
                    pass


def find_candidate_containers(driver) -> List:
    """
    Берём все элементы, где виден текст «Полезно», и поднимаемся к ближайшему
    див/артикл-контейнеру, который также содержит строку с «… отзыв/отзыва/отзывов».
    """
    nodes = driver.find_elements(By.XPATH, '//*[contains(normalize-space(.),"Полезно")]')
    found = []
    seen_ids = set()

    for n in nodes:
        try:
            # поднимемся по дереву, но не выше 6 уровней
            cand = n
            for _ in range(6):
                cand = cand.find_element(By.XPATH, './ancestor::*[self::div or self::article][1]')
                txt = (cand.text or "").strip()
                if not txt:
                    continue
                # отсечём шапки/футеры
                if RE_TRASH.search(txt):
                    break
                # условие "карточка": есть "отзыв/отзыва/отзывов" рядом с именем
                if (" отзыв" in txt) and ("Полезно" in txt):
                    # попытка дедупликации по hash текста (без счётчика полезности)
                    tkey = re.sub(r"\b\d+\b", "#", txt)[:200]
                    if tkey in seen_ids:
                        break
                    seen_ids.add(tkey)
                    found.append(cand)
                    break
        except Exception:
            continue

    return found


def extract_from_card(card) -> Optional[Dict[str, str]]:
    # раскрыть «Читать целиком», если есть
    click_read_more(card)

    # возьмём строки текста и подчистим явные служебные
    lines = [ln.strip() for ln in (card.text or "").splitlines() if ln.strip()]
    if not lines:
        return None

    # автор: строка, где есть "... N отзыв(ов/а)"
    author = ""
    for ln in lines[:6]:
        if RE_AUTHOR_LINE.search(ln):
            author = RE_AUTHOR_LINE.sub("", ln).strip(" ·")
            author = re.sub(r"^[А-ЯЁ]{1,2}\s+", "", author)  # срежем инициалы типа "АШ "
            break

    # тело: уберём служебные элементы
    body = []
    for ln in lines:
        if "Полезно" == ln or "Читать целиком" in ln or "Читать ещё" in ln:
            continue
        if RE_AUTHOR_LINE.search(ln):  # строку автора не включаем
            continue
        # убираем голые цифры (счётчик "Полезно")
        if ln.isdigit() and len(ln) <= 3:
            continue
        body.append(ln)

    text = " ".join(body).strip()
    if not text:
        return None

    return {
        "author": author,
        "rating": "",        # у карточек 2ГИС часто нет индивидуальной оценки
        "date_raw": "",      # даты на вкладке нередко не показывают
        "date_iso": "",
        "text": text,
    }


def scrape_reviews(driver, max_scroll_rounds: int=12) -> List[Dict[str, str]]:
    # немного прокрутим, чтобы прогрузились блоки
    page_scroll(driver, rounds=max_scroll_rounds)

    cards = find_candidate_containers(driver)
    if not cards:
        # ещё прокрутим — вдруг отложенная подгрузка
        page_scroll(driver, rounds=8)
        cards = find_candidate_containers(driver)

    print(f"[DEBUG] Найдено контейнеров с 'Полезно': {len(cards)}")

    rows: List[Dict[str, str]] = []
    seen = set()

    for i, c in enumerate(cards, 1):
        try:
            rec = extract_from_card(c)
        except Exception:
            rec = None
        if not rec:
            continue
        key = (rec["author"].lower(), rec["text"][:80].lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append(rec)
        print(f"[DEBUG] Карточка {i}: author='{rec['author'][:40]}', text_len={len(rec['text'])}")

    return rows


def save_csv(rows: List[Dict[str, str]], path: str):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["author","rating","date_raw","date_iso","text"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] Сохранено {len(rows)} отзывов в {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL, help="URL вкладки отзывов 2ГИС (…/firm/<id>/tab/reviews)")
    ap.add_argument("--browser", default=DEFAULT_BROWSER, help="Путь к YandexBrowser browser.exe")
    ap.add_argument("--driver",  default=DEFAULT_DRIVER,  help="Путь к yandexdriver.exe")
    ap.add_argument("--headless", action="store_true", help="Headless режим")
    ap.add_argument("--out", default="2gis_reviews.csv", help="CSV для сохранения")
    ap.add_argument("--scroll", type=int, default=12, help="Раундов прокрутки перед сбором")
    args = ap.parse_args()

    driver, tmp_profile = build_driver(args.browser, args.driver, args.headless)
    try:
        wait_ready(driver, args.url)
        rows = scrape_reviews(driver, max_scroll_rounds=args.scroll)
        save_csv(rows, args.out)
    finally:
        driver.quit()
        if tmp_profile and os.path.isdir(tmp_profile):
            shutil.rmtree(tmp_profile, ignore_errors=True)


if __name__ == "__main__":
    main()
