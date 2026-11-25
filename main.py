import os
import re
import time
import random
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright

# ==============================================================================
# КОНФИГУРАЦИЯ И СПИСОК ГОРОДОВ
# ==============================================================================
# Формат: "Название": ("поддомен", "точный_путь_города")
LOCATIONS = {
    # 1. Новосибирский кластер
    "Новосибирск": ("novosibirsk", ""),
    "Бердск": ("novosibirsk", "novosibirskaya-oblast-berdsk"),
    "Кольцово": ("novosibirsk", "novosibirskaya-oblast-kolcovo-01306058"),
    "Краснообск": ("novosibirsk", "novosibirskaya-oblast-novosibirskiy-rayon-krasnoobsk-01332373"),

    # 2. Омский кластер
    "Омск": ("omsk", ""),
    "Калачинск": ("omsk", "omskaya-oblast-kalachinsk"),

    # 3. Екатеринбургский кластер
    "Екатеринбург": ("ekb", ""),
    "Верхняя Пышма": ("ekb", "sverdlovskaya-oblast-verhnyaya-pyshma"),
    "Березовский": ("ekb", "sverdlovskaya-oblast-berezovskiy"),
    "Первоуральск": ("ekb", "sverdlovskaya-oblast-pervouralsk"),

    # 4. Санкт-Петербургский кластер
    "Санкт-Петербург": ("spb", ""),
    "Мурино": ("spb", "leningradskaya-oblast-vsevolozhskiy-rayon-murino-01100288"),
    "Кудрово": ("spb", "leningradskaya-oblast-vsevolozhskiy-rayon-kudrovo-01100260"),
    "Всеволожск": ("spb", "leningradskaya-oblast-vsevolozhsk"),
    "Гатчина": ("spb", "leningradskaya-oblast-gatchina"),

    # 5. Московский кластер
    "Москва": ("www", ""),
    "Люберцы": ("lyubertsy", "moskovskaya-oblast-lyubercy"),
    "Химки": ("khimki", "moskovskaya-oblast-himki"),
    "Красногорск": ("krasnogorsk", "moskovskaya-oblast-krasnogorsk"),
    "Троицк": ("www", "moskva-troick"),
    "Королёв": ("korolev", "moskovskaya-oblast-korolev")
}



# Комнатность: 1=1к, 2=2к, 3=3к, 9=студия
ROOM_TYPES = [1, 2, 3, 9]

# Количество страниц на категорию (1 стр. = ~28 объявлений)
MAX_PAGES = 5

OUTPUT_DIR = "cian_data_raw"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ==============================================================================
# ФУНКЦИЯ ИЗВЛЕЧЕНИЯ ДАННЫХ ПРЯМО С ЭКРАНА БРАУЗЕРА
# ==============================================================================
def extract_offers_from_page(page, current_date_str, city_name, deal, room):
    try:
        page.wait_for_selector('a[href*="/rent/flat/"], a[href*="/sale/flat/"]', timeout=10000)
    except Exception:
        return []

    links = page.locator('a[href*="/rent/flat/"], a[href*="/sale/flat/"]').all()
    seen_ids = set()
    results = []

    for link in links:
        try:
            url = link.get_attribute("href")
            if not url:
                continue

            # Ищем ID квартиры в ссылке (работает даже если в конце ссылки есть ?utm=...)
            id_match = re.search(r'/(?:rent|sale)/flat/(\d+)', url)
            if not id_match:
                continue

            cian_id = id_match.group(1)
            if cian_id in seen_ids:
                continue

            # Поднимаемся вверх по дереву элементов до карточки, содержащей цену (₽) и площадь (м²)
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

            seen_ids.add(cian_id)
            lines = [line.strip() for line in card_text.split('\n') if line.strip()]

            # Извлекаем цену в рублях
            price = None
            for l in lines:
                if '₽' in l and ('/мес' in l or 'млн' in l or 'тыс' in l or re.search(r'\d\s*₽', l)):
                    price_match = re.search(r'([\d\s]+)\s*₽', l)
                    if price_match:
                        digits = re.sub(r'\D', '', price_match.group(1))
                        if digits:
                            price = int(digits)
                            break

            # Извлекаем площадь и этаж
            title_line = next((l for l in lines if 'м²' in l), "")
            area_match = re.search(r'([\d,]+)\s*м²', title_line)
            total_area = float(area_match.group(1).replace(',', '.')) if area_match else None

            floor_match = re.search(r'(\d+)/(\d+)\s*этаж', title_line)
            floor = int(floor_match.group(1)) if floor_match else None
            floors_count = int(floor_match.group(2)) if floor_match else None

            # Извлекаем адрес
            address_line = next((l for l in lines if city_name in l or 'ул.' in l or 'просп.' in l or 'р-н' in l or 'ш.' in l), "")

            clean_url = f"https://cian.ru/{'rent' if deal == 'rent_long' else 'sale'}/flat/{cian_id}/"

            results.append({
                "parsing_date": current_date_str,
                "city": city_name,
                "deal_type": deal,
                "cian_id": cian_id,
                "price": price,
                "rooms": room if room != 9 else 0,
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
# ОСНОВНОЙ ЦИКЛ СБОРА ЧЕРЕЗ БРАУЗЕР
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
        context = browser.new_context(
            viewport={"width": 1366, "height": 768}
        )
        page = context.new_page()

        print("🌐 Инициализация браузера и получение кук ЦИАН...")
        page.goto("https://cian.ru", wait_until="domcontentloaded",
                  timeout=30000)
        time.sleep(3)

        for deal in deal_types:
            # Формируем начало ЧПУ-ссылки ЦИАН: купить или снять квартиру
            action_slug = "snyat-kvartiru" if deal == "rent_long" else "kupit-kvartiru"

            for city_name, (subdomain, city_slug) in LOCATIONS.items():
                print(f"\n📍 Город: {city_name} [{deal.upper()}]")
                filename = f"{current_date_str}_{city_name.lower()}_{deal}.csv"
                file_path = os.path.join(OUTPUT_DIR, filename)

                for room in ROOM_TYPES:
                    # В ЧПУ-ссылках ЦИАН комнатность задается параметром ?room1=1 и страницей &p=2
                    room_q = f"room{room}=1" if room != 9 else "room9=1"
                    city_room_offers = []

                    for page_num in range(1, MAX_PAGES + 1):
                        suffix = f"-{city_slug}" if city_slug else ""
                        base_url = f"https://{subdomain}.cian.ru/{action_slug}{suffix}/"
                        url = f"{base_url}?{room_q}&p={page_num}"

                        try:
                            page.goto(url, wait_until="domcontentloaded",
                                      timeout=25000)
                            offers = extract_offers_from_page(page,
                                                              current_date_str,
                                                              city_name, deal,
                                                              room)

                            if not offers:
                                if page.locator(
                                        'form[action*="captcha"], #captcha').count() > 0:
                                    print(
                                        "  ⚠️ Реальная капча! Решите её в браузере (ждем 25 сек)...")
                                    time.sleep(25)
                                    offers = extract_offers_from_page(page,
                                                                      current_date_str,
                                                                      city_name,
                                                                      deal,
                                                                      room)

                                if not offers:
                                    break

                            city_room_offers.extend(offers)
                            print(
                                f"  ↳ Комнат: {room} | Стр. {page_num}: собрано {len(offers)} шт.")
                            time.sleep(random.uniform(1.0, 2.0))

                        except Exception as e:
                            print(
                                f"  ❌ Ошибка загрузки (комн. {room}, стр. {page_num}): {e}")
                            break

                    if city_room_offers:
                        df = pd.DataFrame(city_room_offers)
                        header = not os.path.exists(file_path)
                        df.to_csv(file_path, mode='a', header=header,
                                  index=False, encoding="utf-8-sig")

                if os.path.exists(file_path):
                    print(f"💾 Файл успешно обновлен: {filename}")
                time.sleep(random.uniform(2.0, 4.0))

        browser.close()
    print("\n🎉 Сбор данных полностью завершен!")


if __name__ == "__main__":
    run_parsing()
