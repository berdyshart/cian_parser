import os
import re
import glob
import time
import random
import pandas as pd
from playwright.sync_api import sync_playwright

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
RAW_DIR = "cian_data_raw"
FULL_DIR = "cian_data_full"
os.makedirs(FULL_DIR, exist_ok=True)

# Словарь перевода стандартных плашек ЦИАН в удобные названия столбцов
FIELD_MAPPING = {
    "Общая площадь": "total_area_detail",
    "Жилая площадь": "living_area",
    "Площадь кухни": "kitchen_area",
    "Высота потолков": "ceiling_height",
    "Санузел": "bathroom",
    "Балкон/лоджия": "balcony",
    "Вид из окон": "window_view",
    "Ремонт": "renovation",
    "Тип дома": "house_type",
    "Тип перекрытий": "floor_type",
    "Год постройки": "build_year",
    "Строительная серия": "building_series",
    "Подъезды": "entrances",
    "Лифты": "elevators",
    "Парковка": "parking",
    "Отопление": "heating",
    "Аварийность": "is_emergency",
    "Газоснабжение": "gas",
    "Мусоропровод": "garbage_chute",
    "Оплата ЖКХ": "utilities",
    "Залог": "deposit",
    "Комиссия с арендатора": "commission_tenant",
    "Предоплата": "prepayment",
    "Срок аренды": "rent_term",
    "Условия проживания": "living_conditions",
    "Тип жилья": "housing_type",
    "Планировка": "layout",
    "Способ продажи": "sale_method",
    "Вид сделки": "transaction_type"
}


# ==============================================================================
# ФУНКЦИЯ ГЛУБОКОГО ПАРСИНГА КАРТОЧКИ ОБЪЯВЛЕНИЯ
# ==============================================================================
def parse_single_offer(page, url):
    details = {
        "exact_address": None,
        "latitude": None,
        "longitude": None,
        "metro_stations": None,
        "description": None,
        "amenities": None,
        "all_raw_specs": None
    }
    # Инициализируем все целевые колонки значением None
    for col in FIELD_MAPPING.values():
        details[col] = None

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)

        # Ждем загрузки основного контента (или проверяем на капчу)
        try:
            page.wait_for_selector('h1', timeout=8000)
        except Exception:
            if page.locator('form[action*="captcha"], #captcha').count() > 0:
                return "CAPTCHA"
            return details

        html = page.content()

        # 1. Извлекаем точные координаты (широту и долготу) из скрытого JSON страницы
        lat_match = re.search(r'"lat"\s*:\s*(5\d\.\d+|4\d\.\d+|6\d\.\d+)',
                              html)
        lng_match = re.search(r'"lng"\s*:\s*(\d{2,3}\.\d+)', html)
        if lat_match and lng_match:
            details["latitude"] = float(lat_match.group(1))
            details["longitude"] = float(lng_match.group(1))

        # 2. Точный адрес из гео-блока под заголовком
        geo_el = page.locator('div[data-name="Geo"], address').first
        if geo_el.count() > 0:
            raw_geo = geo_el.inner_text().replace("На карте", "").strip()
            details["exact_address"] = ", ".join(
                [line.strip() for line in raw_geo.split('\n') if line.strip()])

        # 3. Метро и время до него
        metro_els = page.locator(
            'li[data-name="UndergroundItem"], a[href*="/metro-"]').all()
        if metro_els:
            metros = [m.inner_text().replace('\n', ' ').strip() for m in
                      metro_els]
            details["metro_stations"] = " | ".join(dict.fromkeys(metros))

        # 4. Сбор ВСЕХ плашек характеристик (Жилая площадь, Ремонт, Тип дома, Залог, ЖКХ и т.д.)
        raw_specs_dict = {}
        # На ЦИАН плашки лежат в блоках ObjectFactoidsItem или OfferSummaryInfoItem
        fact_items = page.locator(
            'div[data-name="ObjectFactoidsItem"], div[data-name="OfferSummaryInfoItem"]').all()

        for item in fact_items:
            lines = [l.strip() for l in item.inner_text().split('\n') if
                     l.strip()]
            if len(lines) >= 2:
                key, val = lines[0], lines[1]
                raw_specs_dict[key] = val
                if key in FIELD_MAPPING:
                    details[FIELD_MAPPING[key]] = val

        # Запасной сбор на случай другой верстки плашек (через span-пары)
        if not raw_specs_dict:
            spans = page.locator('span').all()
            texts = [s.inner_text().strip() for s in spans if
                     s.inner_text().strip()]
            for i in range(len(texts) - 1):
                if texts[i] in FIELD_MAPPING:
                    val = texts[i + 1]
                    raw_specs_dict[texts[i]] = val
                    details[FIELD_MAPPING[texts[i]]] = val

        if raw_specs_dict:
            details["all_raw_specs"] = str(raw_specs_dict)

        # 5. Блок "В квартире есть" (Холодильник, Интернет, Стиральная машина и т.д.)
        features_els = page.locator(
            'div[data-name="FeaturesItem"], li[data-name="FeatureItem"]').all()
        if features_els:
            amenities = [f.inner_text().strip() for f in features_els if
                         f.inner_text().strip()]
            details["amenities"] = ", ".join(dict.fromkeys(amenities))

        # 6. Текст описания от собственника/риелтора (полезно для анализа тональности и ремонта)
        desc_el = page.locator('div[data-name="Description"]').first
        if desc_el.count() > 0:
            details["description"] = desc_el.inner_text().replace('\n',
                                                                  ' ').strip()

    except Exception as e:
        print(f"    ⚠️ Ошибка при чтении карточки: {e}")

    return details


# ==============================================================================
# ОСНОВНОЙ ЦИКЛ ОБОГАЩЕНИЯ CSV-ФАЙЛОВ
# ==============================================================================
def run_details_enrichment():
    raw_files = sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))
    if not raw_files:
        print(
            f"❌ В папке {RAW_DIR} нет файлов для обработки. Сначала запустите main.py.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        print("🌐 Открываем ЦИАН для прогрева сессии...")
        page.goto("https://cian.ru", wait_until="domcontentloaded",
                  timeout=30000)
        time.sleep(3)

        for raw_file in raw_files:
            base_name = os.path.basename(raw_file)
            full_file_path = os.path.join(FULL_DIR, base_name)

            df_raw = pd.read_csv(raw_file)
            if df_raw.empty or "url" not in df_raw.columns:
                continue

            # Убираем дубликаты внутри файла по cian_id
            df_raw = df_raw.drop_duplicates(subset=["cian_id"]).reset_index(
                drop=True)

            # Проверяем, какие объявления из этого файла уже были скачаны ранее
            processed_ids = set()
            if os.path.exists(full_file_path):
                df_existing = pd.read_csv(full_file_path)
                if "cian_id" in df_existing.columns:
                    processed_ids = set(df_existing["cian_id"].astype(str))

            print(
                f"\n📂 Файл: {base_name} (Всего: {len(df_raw)}, уже скачано: {len(processed_ids)})")

            batch_rows = []
            for idx, row in df_raw.iterrows():
                cian_id = str(row["cian_id"])
                if cian_id in processed_ids:
                    continue

                url = str(row["url"])
                if not url.startswith("http"):
                    continue

                print(f"  [{idx + 1}/{len(df_raw)}] Парсим ID {cian_id}...")
                extra_data = parse_single_offer(page, url)

                # Если поймали капчу — ждем, пока пользователь решит её, и пробуем снова
                if extra_data == "CAPTCHA":
                    print(
                        "  ⚠️ Поймали капчу! Решите её в окне браузера (ждем 30 сек)...")
                    time.sleep(30)
                    extra_data = parse_single_offer(page, url)
                    if extra_data == "CAPTCHA":
                        print(
                            "  ❌ Капча не пройдена. Сохраняем прогресс и прерываемся.")
                        break

                # Объединяем базовые данные с детальными характеристиками
                full_row = row.to_dict()
                full_row.update(extra_data)
                batch_rows.append(full_row)

                # Сохраняем каждые 10 объявлений (защита от потери данных при обрыве связи)
                if len(batch_rows) >= 10:
                    df_batch = pd.DataFrame(batch_rows)
                    header = not os.path.exists(full_file_path)
                    df_batch.to_csv(full_file_path, mode='a', header=header,
                                    index=False, encoding="utf-8-sig")
                    batch_rows = []
                    print("    💾 Промежуточное сохранение (10 шт.) выполнено.")

                # Обязательная антибан-пауза при заходе в карточки (2.5 - 4.5 сек)
                time.sleep(random.uniform(2.5, 4.5))

            # Сохраняем остаток меньше 10 штук в конце файла
            if batch_rows:
                df_batch = pd.DataFrame(batch_rows)
                header = not os.path.exists(full_file_path)
                df_batch.to_csv(full_file_path, mode='a', header=header,
                                index=False, encoding="utf-8-sig")
                print(
                    f"  ✅ Файл {base_name} полностью обогащен и сохранен в {FULL_DIR}!")

        browser.close()
    print("\n🎉 Глубокий сбор всех характеристик завершен!")


if __name__ == "__main__":
    run_details_enrichment()
