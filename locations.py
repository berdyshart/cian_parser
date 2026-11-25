import time
import requests

cities = [
    "Новосибирск", "Бердск", "Кольцово Новосибирская область", "Краснообск",
    "Омск", "Калачинск", "Екатеринбург", "Верхняя Пышма",
    "Березовский Свердловская область", "Первоуральск", "Санкт-Петербург", "Мурино",
    "Кудрово", "Всеволожск", "Гатчина", "Москва",
    "Люберцы", "Химки", "Красногорск", "Троицк Москва", "Королёв"
]

url = "https://cian.ru"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Origin": "https://cian.ru",
    "Referer": "https://cian.ru/",
}

locations = {}
for city in cities:
    response = requests.get(url, params={"q": city}, headers=headers)
    if response.status_code == 200:
        data = response.json()
        items = data.get("items", [])
        if items:
            # Выводим первый результат и его полное название для проверки
            locations[city] = (items[0].get("id"), items[0].get("fullName"))
        else:
            locations[city] = None
    else:
        print(f"Ошибка {response.status_code} для {city}: {response.text[:100]}")
    time.sleep(0.5)

print("LOCATIONS = {")
for k, v in locations.items():
    print(f'    "{k}": {v},')
print("}")
