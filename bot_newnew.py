import logging
import requests
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from transliterate import translit

# =========================
# Настройка логирования
# =========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================
# Функция получения курсов от НБ РБ
# =========================
def get_nbrb_rates():
    try:
        # Получаем курс USD/BYN от НБ РБ
        usd_response = requests.get("https://www.nbrb.by/api/exrates/rates/USD?parammode=2")
        # Получаем курс RUB/BYN от НБ РБ  
        rub_response = requests.get("https://www.nbrb.by/api/exrates/rates/RUB?parammode=2")
        
        if usd_response.status_code == 200 and rub_response.status_code == 200:
            usd_data = usd_response.json()
            rub_data = rub_response.json()
            
            usd_byn_rate = usd_data.get("Cur_OfficialRate", 1)
            rub_byn_rate = rub_data.get("Cur_OfficialRate", 1)
            rub_scale = rub_data.get("Cur_Scale", 100)  # Обычно 100 российских рублей
            
            # Правильный расчет: курс за scale единиц
            rub_byn_rate_per_one = rub_byn_rate / rub_scale
            
            logger.info(f"Курсы НБ РБ: 1 USD = {usd_byn_rate} BYN, {rub_scale} RUB = {rub_byn_rate} BYN")
            logger.info(f"Фактически: 1 RUB = {rub_byn_rate_per_one} BYN")
            
            return usd_byn_rate, rub_byn_rate_per_one
        else:
            logger.error("Ошибка получения курсов от НБ РБ")
            return 3.2, 0.035  # Резервные курсы
            
    except Exception as e:
        logger.error(f"Ошибка получения курсов НБ РБ: {e}")
        return 3.2, 0.035

# =========================
# Функция скачивания Excel с Яндекс.Диска
# =========================
def download_excel_from_yandisk():
    try:
        YANDEX_DISK_LINK = "https://disk.yandex.ru/i/lSuHvo09BlUOqA"
        
        # Получаем прямую ссылку для скачивания
        api_url = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={YANDEX_DISK_LINK}"
        response = requests.get(api_url)
        
        if response.status_code != 200:
            logger.error(f"Ошибка при получении ссылки: {response.status_code}")
            return False
        
        download_url = response.json()['href']
        
        # Скачиваем файл
        file_response = requests.get(download_url)
        if file_response.status_code != 200:
            logger.error(f"Ошибка при скачивании: {file_response.status_code}")
            return False
        
        # Сохраняем файл
        with open('price.xlsx', 'wb') as f:
            f.write(file_response.content)
        
        logger.info("Excel файл успешно скачан!")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return False

# =========================
# Загружаем и подготавливаем Excel
# =========================
def load_excel_data():
    try:
        # Сначала пытаемся скачать свежий файл
        download_success = download_excel_from_yandisk()
        
        # Загружаем данные (свежие или старые, если скачивание не удалось)
        df = pd.read_excel("price.xlsx", engine="openpyxl")

        # Убираем строки без нужных данных
        df = df.dropna(subset=['Код', 'Номенклатура', 'Цена'])

        # Обрезаем пробелы и приводим к строкам
        df['Код'] = df['Код'].astype(str).str.strip()
        df['Номенклатура'] = df['Номенклатура'].astype(str).str.strip()

        # Добавляем транслитерированные версии для быстрого поиска
        df['Номенклатура_ru'] = df['Номенклатура'].apply(lambda x: translit(x, 'ru'))
        df['Номенклатура_en'] = df['Номенклатура'].apply(lambda x: translit(x, 'ru', reversed=True))
        
        # Получаем курсы от НБ РБ и конвертируем цены
        usd_byn_rate, rub_byn_rate = get_nbrb_rates()
        
        # Конвертируем в BYN: RUB → BYN
        df['Цена_BYN'] = (df['Цена'] * rub_byn_rate).round(2)
        
        # Конвертируем в USD: BYN → USD
        df['Цена_USD'] = (df['Цена_BYN'] / usd_byn_rate).round(2)
        
        logger.info(f"Данные успешно загружены! Курсы: 1 USD = {usd_byn_rate} BYN, 1 RUB = {rub_byn_rate} BYN")
        return df, usd_byn_rate, rub_byn_rate
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке Excel: {e}")
        return None, None, None

# Загружаем данные при старте
df, current_usd_rate, current_rub_rate = load_excel_data()
if df is None:
    logger.error("Не удалось загрузить данные. Бот остановлен.")
    exit()

# =========================
# Команда /start
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Напиши название товара, номенклатуру или код — я покажу цену 📱\n\n"
        "Также доступны команды:\n"
        "/update - обновить цены с Яндекс.Диска\n"
        "/rate - показать текущие курсы валют"
    )

# =========================
# Команда /update - обновить цены
# =========================
async def update_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global df, current_usd_rate, current_rub_rate
    await update.message.reply_text("🔄 Обновляю цены и курсы...")
    
    success = download_excel_from_yandisk()
    if success:
        new_df, new_usd_rate, new_rub_rate = load_excel_data()
        if new_df is not None:
            df = new_df
            current_usd_rate = new_usd_rate
            current_rub_rate = new_rub_rate
            await update.message.reply_text("✅ Цены и курсы успешно обновлены!")
        else:
            await update.message.reply_text("❌ Ошибка при загрузке новых данных")
    else:
        await update.message.reply_text("❌ Не удалось скачать новые цены")

# =========================
# Команда /rate - показать курсы
# =========================
async def show_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usd_byn_rate, rub_byn_rate = get_nbrb_rates()
    await update.message.reply_text(
        f"💱 Текущие курсы НБ РБ:\n"
        f"💵 1 USD = {usd_byn_rate} BYN\n"
        f"🇷🇺 1 RUB = {rub_byn_rate:.4f} BYN\n"
        f"🔀 1 USD = {usd_byn_rate / rub_byn_rate:.2f} RUB"
    )

# =========================
# Поиск товара
# =========================
async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()

    # Поиск по номенклатуре и коду
    matches = df[
        df['Номенклатура'].str.lower().str.contains(query, na=False) |
        df['Номенклатура_ru'].str.lower().str.contains(query, na=False) |
        df['Номенклатура_en'].str.lower().str.contains(query, na=False) |
        df['Код'].str.contains(query, na=False)
    ]

    if not matches.empty:
        reply_lines = []
        for _, row in matches.iterrows():
            reply_lines.append(
                f"📦 {row['Номенклатура']}\n"
                f"🔢 Код: {row['Код']}\n"
                f"🇷🇺 Цена: {row['Цена']} RUB\n"
                f"🇧🇾 Цена: {row['Цена_BYN']} BYN\n"
                f"💵 Цена: {row['Цена_USD']} USD"
            )
        reply = "\n\n".join(reply_lines)
    else:
        reply = "Товар не найден 😔"

    await update.message.reply_text(reply)

# =========================
# Запуск бота
# =========================
def main():
    TOKEN = "8404122466:AAGDiO50j3eM6KstV_j3hBo2CJTFLOrVhIQ"
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_prices))
    app.add_handler(CommandHandler("rate", show_rate))
    app.add_handler(MessageHandler(filters.TEXT, get_price))

    logger.info("Бот запущен...")
    app.run_polling()

    from flask import Flask
import threading

# Web-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Бот работает!"

@app.route('/healthz')
def health_check():
    return "OK", 200

def run_web():
    app.run(host='0.0.0.0', port=5000, debug=False)

# Запускаем web-сервер в фоне
web_thread = threading.Thread(target=run_web)
web_thread.daemon = True
web_thread.start()

print("🌐 Web-сервер запущен на порту 5000")

if __name__ == "__main__":
    main()