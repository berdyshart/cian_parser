import os
import time
import random
from datetime import datetime
import pandas as pd
import cianparser

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
LOCATIONS = [
    "Омск", "Калачинск"
    "Москва", "Люберцы", "Химки", "Красногорск", "Троицк", "Королёв",
    "Санкт-Петербург", "Мурино", "Кудрово", "Всеволожск", "Гатчина",
    "Новосибирск", "Бердск", "Кольцово", "Краснообск",
    "Екатеринбург", "Верхняя Пышма", "Березовский", "Первоуральск",
]

# Ограничимся самыми популярными типами для скорости (1к, 2к, 3к и студии)
ROOM_TYPES = [1, 2, 3, 9]

# Собираем первые 5 страниц (около 250 самых свежих квартир на каждый тип)
# Это ускорит парсинг в 6 раз и даст достаточную выборку для статистики
MAX_PAGES = 5

OUTPUT_DIR = "cian_data_raw"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def run_parsing():
    current_date_str = datetime.now().strftime("%Y-%m-%d")
    current_day_of_week = datetime.now().weekday()  # 2 = Среда

    deal_types = ["rent_long"]
    if current_day_of_week == 2:
        print(f"🔥 Среда ({current_date_str}). Собираем: АРЕНДА + ПОКУПКА.")
        deal_types.append("sale")
    else:
        print(f"📅 Обычный день ({current_date_str}). Собираем только АРЕНДУ.")

    for deal in deal_types:
        for location in LOCATIONS:
            print(f"\n📍 Начинаем: {location} [{deal.upper()}]")

            # Имя файла для города (будем дописывать в него по мере сбора)
            filename = f"{current_date_str}_{location.lower()}_{deal}.csv"
            file_path = os.path.join(OUTPUT_DIR, filename)

            for room in ROOM_TYPES:
                print(f"  ↳ Комнатность {room} (до {MAX_PAGES} стр.)...")
                try:
                    parser = cianparser.CianParser(location=location)
                    data = parser.get_flats(
                        deal_type=deal,
                        rooms=(room,),
                        additional_settings={"start_page": 1,
                                             "end_page": MAX_PAGES}
                    )

                    if data:
                        for item in data:
                            item['parsing_date'] = current_date_str
                            item['deal_type_custom'] = deal
                            item['search_location'] = location
                            item['rooms_category'] = room

                        df = pd.DataFrame(data)

                        # МГНОВЕННОЕ СОХРАНЕНИЕ (дописываем в файл сразу)
                        # Если файла еще нет - пишем с заголовками, если есть - без заголовков
                        header = not os.path.exists(file_path)
                        df.to_csv(file_path, mode='a', header=header,
                                  index=False, encoding="utf-8-sig")
                        print(f"    ✅ Сохранено {len(data)} шт. в {filename}")
                    else:
                        print("    📭 Пусто.")

                except Exception as e:
                    print(f"    ❌ Ошибка на комнатности {room}: {e}")

                # Минимальная пауза 1-2 секунды
                time.sleep(random.uniform(1.0, 2.0))

    print("\n🎉 Сбор завершен!")


if __name__ == "__main__":
    run_parsing()
