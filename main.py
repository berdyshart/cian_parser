import os
import re
import time
import random
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright

# ==============================================================================
# КОНФИГУРАЦИЯ И ТОЧНАЯ СЕТКА ГОРОДОВ
# ==============================================================================
LOCATIONS = {
    "Новосибирск": ("novosibirsk", ""),
    "Бердск": ("novosibirsk", "novosibirskaya-oblast-berdsk"),
    "Кольцово": ("novosibirsk", "novosibirskaya-oblast-kolcovo-01306058"),
    "Краснообск": ("novosibirsk",
                   "novosibirskaya-oblast-novosibirskiy-rayon-krasnoobsk-01332373"),
    "Омск": ("omsk", ""),
    "Калачинск": ("omsk", "omskaya-oblast-kalachinsk"),
    "Екатеринбург": ("ekb", ""),
    "Верхняя Пышма": ("ekb", "sverdlovskaya-oblast-verhnyaya-pyshma"),
    "Березовский": ("ekb", "sverdlovskaya-oblast-berezovskiy"),
    "Первоуральск": ("ekb", "sverdlovskaya-oblast-pervouralsk"),
    "Санкт-Петербург": ("spb", ""),
    "Мурино": ("spb",
               "leningradskaya-oblast-vsevolozhskiy-rayon-murino-01100288"),
    "Кудрово": ("spb",
                "leningradskaya-oblast-vsevolozhskiy-rayon-kudrovo-01100260"),
    "Всеволожск": ("spb", "leningradskaya-oblast-vsevolozhsk"),
    "Гатчина": ("spb", "leningradskaya-oblast-gatchina"),
    "Москва": ("www", ""),
    "Люберцы": ("lyubertsy", "moskovskaya-oblast-lyubercy"),
    "Химки": ("khimki", "moskovskaya-oblast-himki"),
    "Красногорск": ("krasnogorsk", "moskovskaya-oblast-krasnogorsk"),
    "Троицк": ("www", "moskva-troick"),
    "Королёв": ("korolev", "moskovskaya-oblast-korolev")
}

# Глубина сбора (30 страниц = до ~840 уникальных квартир на город)
MAX_PAGES = 30

OUTPUT_DIR = "cian_data_raw"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ==============================================================================
# ФУНКЦИЯ ИЗВЛЕЧЕНИЯ ДАННЫХ С ЭКРАНА (С РЕАЛЬНЫМИ КОМНАТАМИ И РАБОЧЕЙ ССЫЛКОЙ)
# ==============================================================================
def extract_offers_from_page(page, current_date_str, city_name, deal,
                             global_seen_ids):
    try:
        page.wait_for_selector(
            'a[href*="/rent/flat/"], a[href*="/sale/flat/"]', timeout=10000)
    except Exception:
        return []

    links = page.locator(
        'a[href*="/rent/flat/"], a[href*="/sale/flat/"]').all()
    results = []

    for link in links:
        try:
            url = link.get_attribute("href")
            if not url:
                continue

            id_match = re.search(r'/(?:rent|sale)/flat/(\d+)', url)
            if not id_match:
                continue

            cian_id = id_match.group(1)
            # Отсекаем дубликаты (включая рекламные ТОП-объявления, повторяющиеся на страницах)
            if cian_id in global_seen_ids:
                continue

            card = link
            card_text = ""
            for _ in range(8):
                parent = card.locator("..")
                if parent.count() == 0:
                    break
                text = parent.inner_text()
                if '₽' in text and 'м²' in text:
                    card_text = text
                    break
                card = parent

            if not card_text:
                continue

            global_seen_ids.add(cian_id)
            lines = [line.strip() for line in card_text.split('\n') if
                     line.strip()]

            # 1. Цена
            price = None
            for l in lines:
                if '₽' in l and (
                        '/мес' in l or 'млн' in l or 'тыс' in l or re.search(
                        r'\d\s*₽', l)):
                    price_match = re.search(r'([\d\s]+)\s*₽', l)
                    if price_match:
                        digits = re.sub(r'\D', '', price_match.group(1))
                        if digits:
                            price = int(digits)
                            break

            # 2. Заголовок: извлекаем реальное кол-во комнат, площадь и этаж
            title_line = next((l for l in lines if 'м²' in l), "")

            rooms = None
            if 'студия' in title_line.lower():
                rooms = 0
            else:
                room_match = re.search(r'(\d+)-комн', title_line.lower())
                if room_match:
                    rooms = int(room_match.group(1))

            area_match = re.search(r'([\d,]+)\s*м²', title_line)
            total_area = float(
                area_match.group(1).replace(',', '.')) if area_match else None

            floor_match = re.search(r'(\d+)/(\d+)\s*этаж', title_line)
            floor = int(floor_match.group(1)) if floor_match else None
            floors_count = int(floor_match.group(2)) if floor_match else None

            # 3. Адрес
            address_line = next((l for l in lines if
                                 city_name in l or 'ул.' in l or 'просп.' in l or 'р-н' in l or 'ш.' in l),
                                "")

            # 4. Гарантированно правильная ссылка со слэшем
            deal_part = "rent" if deal == "rent_long" else "sale"
            clean_url = "/".join(
                ["https:/", "www.cian.ru", deal_part, "flat", str(cian_id),
                 ""])

            results.append({
                "parsing_date": current_date_str,
                "city": city_name,
                "deal_type": deal,
                "cian_id": cian_id,
                "price": price,
                "rooms": rooms,
                "total_area": total_area,
                "floor": floor,
                "floors_count": floors_count,
                "address": address_line,
                "url": clean_url
            })
        except Exception:
            continue

    return results


# ==============================================================================
# ОСНОВНОЙ ЦИКЛ СБОРА
# ==============================================================================
def run_parsing():
    current_date_str = datetime.now().strftime("%Y-%m-%d")
    current_day_of_week = datetime.now().weekday()  # 2 = Среда

    deal_types = ["rent_long"]
    if current_day_of_week == 2:
        print(
            f"🔥 Сегодня среда ({current_date_str}). Собираем: АРЕНДА + ПОКУПКА.")
        deal_types.append("sale")
    else:
        print(f"📅 Обычный день ({current_date_str}). Собираем только АРЕНДУ.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        print("🌐 Инициализация браузера и прогрев сессии...")
        page.goto("https://cian.ru", wait_until="domcontentloaded",
                  timeout=30000)
        time.sleep(3)

        for deal in deal_types:
            action_slug = "snyat-kvartiru" if deal == "rent_long" else "kupit-kvartiru"

            for city_name, (subdomain, city_slug) in LOCATIONS.items():
                print(f"\n📍 Город: {city_name} [{deal.upper()}]")
                filename = f"{current_date_str}_{city_name.lower()}_{deal}.csv"
                file_path = os.path.join(OUTPUT_DIR, filename)

                suffix = f"-{city_slug}" if city_slug else ""
                start_url = f"https://{subdomain}.cian.ru/{action_slug}{suffix}/"

                try:
                    page.goto(start_url, wait_until="domcontentloaded",
                              timeout=25000)
                except Exception as e:
                    print(f"  ❌ Ошибка открытия {start_url}: {e}")
                    continue

                # Множество для контроля уникальности ID внутри всего города
                global_seen_ids = set()
                city_offers = []

                for page_num in range(1, MAX_PAGES + 1):
                    offers = extract_offers_from_page(page, current_date_str,
                                                      city_name, deal,
                                                      global_seen_ids)

                    if not offers and page_num == 1:
                        if page.locator(
                                'form[action*="captcha"], #captcha').count() > 0:
                            print(
                                "  ⚠️ Капча! Решите её в браузере (ждем 25 сек)...")
                            time.sleep(25)
                            offers = extract_offers_from_page(page,
                                                              current_date_str,
                                                              city_name, deal,
                                                              global_seen_ids)

                    if offers:
                        city_offers.extend(offers)
                        print(
                            f"  ↳ Стр. {page_num}: +{len(offers)} уникальных шт. (Всего по городу: {len(city_offers)})")
                    else:
                        print(f"  ↳ Стр. {page_num}: новых объявлений нет.")

                    # Ищем кнопку перехода на следующую страницу ("Дальше" или номер страницы)
                    next_btn = page.locator(
                        'a:has-text("Дальше"), a[href*="p=' + str(
                            page_num + 1) + '"]').first
                    if next_btn.count() > 0 and page_num < MAX_PAGES:
                        try:
                            # Запоминаем текст первой ссылки до клика
                            first_href_before = page.locator(
                                'a[href*="/rent/flat/"], a[href*="/sale/flat/"]').first.get_attribute(
                                "href")

                            next_btn.scroll_into_view_if_needed()
                            time.sleep(random.uniform(1.0, 1.5))
                            next_btn.click()

                            # Ждем до 8 секунд, пока список квартир на экране реально не обновится
                            for _ in range(16):
                                time.sleep(0.5)
                                current_first_href = page.locator(
                                    'a[href*="/rent/flat/"], a[href*="/sale/flat/"]').first.get_attribute(
                                    "href")
                                if current_first_href != first_href_before:
                                    break
                        except Exception:
                            break
                    else:
                        break

                if city_offers:
                    df = pd.DataFrame(city_offers)
                    df.to_csv(file_path, index=False, encoding="utf-8-sig")
                    print(
                        f"💾 Город {city_name} сохранен: {len(city_offers)} уникальных квартир в {filename}")

                time.sleep(random.uniform(2.0, 4.0))

        browser.close()
    print("\n🎉 Сбор базовых списков полностью завершен!")


if __name__ == "__main__":
    run_parsing()
