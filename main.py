import os
import re
import json
import time
import random
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright

# ==============================================================================
# КОНФИГУРАЦИЯ И СПИСОК ГОРОДОВ
# ==============================================================================
LOCATIONS = {
    "Новосибирск": 4897,
    "Бердск": 4681,
    "Кольцово": 175657,
    "Краснообск": 175684,
    "Омск": 4914,
    "Калачинск": 4781,
    "Екатеринбург": 4743,
    "Верхняя Пышма": 4702,
    "Березовский": 4682,
    "Первоуральск": 4927,
    "Санкт-Петербург": 2,
    "Мурино": 175049,
    "Кудрово": 175013,
    "Всеволожск": 4712,
    "Гатчина": 4718,
    "Москва": 1,
    "Люберцы": 4850,
    "Химки": 5044,
    "Красногорск": 4820,
    "Троицк": 5019,
    "Королёв": 4813
}

# Комнатность: 1=1к, 2=2к, 3=3к, 9=студия
ROOM_TYPES =[1, 2, 3, 9]

# Количество страниц на категорию (1 стр. = 28 объявлений)
MAX_PAGES = 5

OUTPUT_DIR = "cian_data_raw"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ==============================================================================
# ФУНКЦИЯ ИЗВЛЕЧЕНИЯ ДАННЫХ ИЗ КОДА СТРАНИЦЫ
# ==============================================================================
def extract_offers_from_html(html_content):
    # ЦИАН хранит все данные выдачи в скрытом JSON внутри тега script
    match = re.search(
        r"window\._cianConfig\['frontend-serp'\]\s*=\s*(\[.*?\]);",
        html_content, re.DOTALL)
    if not match:
        return []

    try:
        config_data = json.loads(match.group(1))
        for item in config_data:
            if item.get("key") == "initialState":
                return item.get("value", {}).get("results", {}).get("offers",
                                                                    [])
    except Exception:
        pass
    return []


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
        # Запускаем настоящий Chrome в фоновом режиме (headless=True)
        # Если захотите увидеть, как он сам кликает страницы — поменяйте на headless=False
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        # Первый прогревочный заход на главную страницу для получения кук
        print("🌐 Инициализация браузера и получение кук ЦИАН...")
        page.goto("https://cian.ru", wait_until="domcontentloaded",
                  timeout=30000)
        time.sleep(3)

        for deal in deal_types:
            deal_param = "rent" if deal == "rent_long" else "sale"
            type_param = "&type=4" if deal == "rent_long" else ""  # type=4 = длительная аренда

            for city_name, region_id in LOCATIONS.items():
                print(f"\n📍 Город: {city_name} [{deal.upper()}]")
                filename = f"{current_date_str}_{city_name.lower()}_{deal}.csv"
                file_path = os.path.join(OUTPUT_DIR, filename)

                for room in ROOM_TYPES:
                    room_param = f"room{room}=1" if room != 9 else "room9=1"
                    city_room_offers = []

                    for page_num in range(1, MAX_PAGES + 1):
                        url = f"https://cian.rucat.php?deal_type={deal_param}&engine_version=2&offer_type=flat&p={page_num}&region={region_id}&{room_param}{type_param}"

                        try:
                            page.goto(url, wait_until="domcontentloaded",
                                      timeout=25000)
                            # Небольшая пауза для прогрузки скриптов страницы
                            time.sleep(random.uniform(1.5, 3.0))

                            html = page.content()
                            offers = extract_offers_from_html(html)

                            if not offers:
                                # Проверяем, не вылезла ли капча
                                if "captcha" in html.lower() or "робот" in html.lower():
                                    print(
                                        "  ⚠️ Поймали капчу! Делаем паузу 15 секунд...")
                                    time.sleep(15)
                                break

                            for offer in offers:
                                bargain = offer.get("bargainTerms", {})
                                building = offer.get("building", {})
                                geo = offer.get("geo", {})
                                undergrounds = geo.get("undergrounds", [])

                                parsed_item = {
                                    "parsing_date": current_date_str,
                                    "city": city_name,
                                    "deal_type": deal,
                                    "cian_id": offer.get("cianId"),
                                    "price": bargain.get("priceRur"),
                                    "rooms": offer.get(
                                        "roomsCount") if room != 9 else 0,
                                    "total_area": offer.get("totalArea"),
                                    "living_area": offer.get("livingArea"),
                                    "kitchen_area": offer.get("kitchenArea"),
                                    "floor": offer.get("floorNumber"),
                                    "floors_count": building.get(
                                        "floorsCount"),
                                    "build_year": building.get("buildYear"),
                                    "material_type": building.get(
                                        "materialType"),
                                    "address": geo.get("userInput"),
                                    "lat": geo.get("coordinates", {}).get(
                                        "lat"),
                                    "lng": geo.get("coordinates", {}).get(
                                        "lng"),
                                    "metro": undergrounds.get(
                                        "name") if undergrounds else None,
                                    "metro_minutes": undergrounds.get(
                                        "time") if undergrounds else None,
                                    "url": offer.get("fullUrl")
                                }
                                city_room_offers.append(parsed_item)

                            print(
                                f"  ↳ Комнат: {room} | Стр. {page_num}: собрано {len(offers)} шт.")

                        except Exception as e:
                            print(
                                f"  ❌ Ошибка загрузки (комн. {room}, стр. {page_num}): {e}")
                            break

                    # Мгновенная запись в CSV после каждой категории комнат
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
