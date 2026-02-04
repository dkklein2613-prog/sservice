import logging
import uuid
import sqlite3
import re
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram import InlineQueryResultArticle, InputTextMessageContent
from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import InlineQueryHandler
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.error import BadRequest
import requests



print("✅ Скрипт начал выполнение")
print("✅ Импорты успешны")

# Настройки
ADMIN_IDS = [1606292950]
CHANNEL_ID = "@skidkaservis"
BOT_TOKEN = "7225116016:AAFBknnKHxbZwmjtODXTk-PuM3VjFbw_6LA"
CHANNEL_LINK = "https://t.me/skidkaservis"
ADMIN_USERNAME = "@DiDimanager72"

PHOTOS_PER_PAGE = 5
GOOGLE_SHEETS_CREDS = 'credentials.json'
SPREADSHEET_ID = '1qj1DpMXQVuyYaVXwpKqUdVA4NJO_s5dt3LejKC1fyMg'

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

print("✅ Настройки загружены")

def init_price_tracking():
    """Инициализация таблицы для отслеживания цен"""
    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY,
        product_key TEXT UNIQUE,
        section TEXT,
        category TEXT,
        model TEXT,
        submodel TEXT,
        color TEXT,
        old_price TEXT,
        new_price TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Проверяем существует ли таблица favorites
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='favorites'")
    table_exists = cursor.fetchone()
    
    if table_exists:
        # Проверяем наличие колонки section_hash
        cursor.execute("PRAGMA table_info(favorites)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'section_hash' not in columns:
            # Пересоздаем таблицу с правильной структурой
            logger.info("🔄 Миграция таблицы favorites...")
            cursor.execute('DROP TABLE IF EXISTS favorites')
    
    # Создаем таблицу избранных товаров пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        section_hash TEXT NOT NULL,
        category_hash TEXT NOT NULL,
        model_hash TEXT NOT NULL,
        submodel_hash TEXT NOT NULL,
        product_index INTEGER NOT NULL,
        current_price TEXT,
        added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, section_hash, category_hash, model_hash, submodel_hash, product_index)
    )
    ''')

    conn.commit()
    conn.close()
    logger.info("✅ Таблица favorites инициализирована")

def init_database():
    """Инициализация базы данных SQLite"""
    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()

    # Основная таблица товаров (упрощенная)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY,
        section TEXT NOT NULL,
        category TEXT NOT NULL,
        model TEXT,
        submodel TEXT,
        color TEXT,
        price TEXT,
        row_index INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Таблица для хранения хэшей (для callback_data)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS hashes (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        hash TEXT NOT NULL UNIQUE
    )
    ''')

    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()

def get_or_create_hash(name, hash_type):
    """Получает или создает хэш для имени"""
    if name is None:
        name = "None"

    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()

    cursor.execute('SELECT hash FROM hashes WHERE name = ? AND type = ?',
                  (name, hash_type))
    result = cursor.fetchone()

    if result:
        conn.close()
        return result[0]

    # Генерируем UUID вместо MD5
    new_hash = str(uuid.uuid4()).replace('-', '')[:16]

    cursor.execute('INSERT INTO hashes (name, type, hash) VALUES (?, ?, ?)',
                  (name, hash_type, new_hash))
    conn.commit()
    conn.close()

    return new_hash

def get_name_by_hash(target_hash):
    """Получает имя по хэшу"""
    if not target_hash:
        return (None, None)

    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()

    cursor.execute('SELECT name, type FROM hashes WHERE hash = ?', (target_hash,))
    result = cursor.fetchone()
    conn.close()

    return result if result else (None, None)

def get_google_sheets_data(range_name='A2:I1500'):
    """Получение данных из Google Sheets"""
    try:
        scope = ['https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/spreadsheets']

        creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1

        return sheet.get(range_name)
    except Exception as e:
        logger.error(f"Ошибка чтения Google Sheets: {e}")
        return []

def parse_catalog_data(data):
    """Парсинг данных каталога с учетом фото в колонке G и описаний в колонках H и I"""
    catalog = {}
    current_section = None
    current_category = None
    current_model = None
    current_submodel = None

    for i, row in enumerate(data):
        # Пропускаем пустые строки и заголовок
        if len(row) < 3 or (row[0] == "Раздел" and row[1] == "Категория"):
            continue

        # Обновляем текущие значения
        if row[0]:  # Раздел
            current_section = row[0].strip()
            current_category = None
            current_model = None
            current_submodel = None

        if len(row) > 1 and row[1]:  # Категория
            current_category = row[1].strip()
            current_model = None
            current_submodel = None

        if len(row) > 2 and row[2]:  # Модель
            current_model = row[2].strip()
            current_submodel = None

        if len(row) > 3 and row[3]:  # Подмодель
            current_submodel = row[3].strip()

        # Пропускаем строки без раздела или категории
        if not current_section or not current_category:
            continue

        # Если нет модели, создаем фиктивную модель "Без модели"
        if not current_model:
            current_model = "Без модели"
            current_submodel = "Без подмодели"

        # Получаем цвет, цену, фото и описания
        color = row[4].strip() if len(row) > 4 and row[4] else None
        price = row[5].strip() if len(row) > 5 and row[5] else None
        photo_url = row[6].strip() if len(row) > 6 and row[6] else None

        # Колонка H - для AI чата
        description = row[7].strip() if len(row) > 7 and row[7] else None

        # Колонка I - для описания под фото
        photo_description = row[8].strip() if len(row) > 8 and row[8] else None

        # Если нет подмодели, используем "Без подмодели"
        submodel_key = current_submodel if current_submodel else "Без подмодели"

        # Создаем хэши для навигации
        section_hash = get_or_create_hash(current_section, 'section')
        category_hash = get_or_create_hash(current_category, 'category')
        model_hash = get_or_create_hash(current_model, 'model')
        submodel_hash = get_or_create_hash(submodel_key, 'submodel')

        # Добавляем в структуру данных
        if current_section not in catalog:
            catalog[current_section] = {}

        if current_category not in catalog[current_section]:
            catalog[current_section][current_category] = {}

        if current_model not in catalog[current_section][current_category]:
            catalog[current_section][current_category][current_model] = {}

        if submodel_key not in catalog[current_section][current_category][current_model]:
            catalog[current_section][current_category][current_model][submodel_key] = []

        # Добавляем вариант товара
        if color or price or photo_url or description or photo_description:
            product_index = len(catalog[current_section][current_category][current_model][submodel_key])
            
            catalog[current_section][current_category][current_model][submodel_key].append({
                'color': color,
                'price': price,
                'photo_url': photo_url,
                'photo_id': photo_url,
                'row_index': i + 1,
                'description': description,        # для AI (колонка H)
                'photo_description': photo_description,  # для фото (колонка I)
                'section_hash': section_hash,
                'category_hash': category_hash,
                'model_hash': model_hash,
                'submodel_hash': submodel_hash,
                'product_index': product_index
            })

    return catalog

async def show_dynamic_sections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает разделы из таблицы"""
    if not await is_user_subscribed(update.callback_query.from_user.id, context):
        await update.callback_query.answer("❌ Подпишитесь на канал!", show_alert=True)
        return
    
    # Просто отправляем сообщение с Mini App кнопкой
    await update.callback_query.edit_message_text(
        "📱 *Каталог товаров*\n\n"
        "Нажмите кнопку ниже чтобы открыть интерактивный каталог:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Открыть каталог", web_app=WebAppInfo(url="https://dmitrii945.github.io/miniapp/"))],
            [InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK)],
            [InlineKeyboardButton("🛍 Поддержка", url="https://t.me/SkidkaService01")]
        ])
    )



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда start - обрабатывает deep links для шаринга товаров"""
    user = update.effective_user
    
    # Сохраняем пользователя в базу данных
    save_user_to_db(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    # Проверяем подписку (если не админ)
    if user.id not in ADMIN_IDS and not await is_user_subscribed(user.id, context):
        keyboard = [[InlineKeyboardButton("📢 Подписаться", url=CHANNEL_LINK)]]
        await update.message.reply_text(
            "❌ Доступ разрешён только подписчикам канала.\n"
            "Пожалуйста, подпишитесь и нажмите /start снова.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Обрабатываем startapp deep link - ИСПРАВЛЕННАЯ ЧАСТЬ
    args = context.args
    if args:
        # Обрабатываем startapp=item_ параметры
        if len(args) > 0 and 'startapp=item_' in args[0]:
            try:
                # Извлекаем параметры товара из startapp ссылки
                startapp_param = args[0]
                if 'startapp=item_' in startapp_param:
                    item_params = startapp_param.replace('startapp=item_', '')
                    params = item_params.split('_')
                    
                    if len(params) >= 5:
                        section_hash, category_hash, model_hash, submodel_hash, product_index = params[:5]
                        
                        # Создаем URL для Mini App с параметрами товара
                        mini_app_url = f"https://dmitrii945.github.io/miniapp/?section={section_hash}&category={category_hash}&model={model_hash}&submodel={submodel_hash}&product={product_index}"
                        
                        # Отправляем сообщение с кнопкой, которая сразу откроет Mini App с товаром
                        await update.message.reply_text(
                            "🛍️ *Товар найден!*\n\n"
                            "Нажмите кнопку ниже чтобы открыть товар в каталоге:\n\n"
                            "🌟 Стройматериалы по уникально низким ценам!\n\n"
                            "📍 Адрес отдела продаж: г. Тюмень, ул. Барабинская, д. 3а, стр. 4\n"
                            "⏰ Понедельник - пятница: 09:00-18:00\n"
                            "⏰ Суббота - воскресенье: 09:00-16:00\n\n"
                            "📞 Звони! 60-01-60!",
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("📂 Открыть товар в каталоге", web_app=WebAppInfo(url=mini_app_url))],
                                [InlineKeyboardButton("🚕 Заказать Яндекс.Такси", url="https://3.redirect.appmetrica.yandex.com/route?end-lat=57.15728&end-lon=65.610084&ref=external_site_button")],
                                [InlineKeyboardButton("📍 Мы на карте 2ГИС", url="https://2gis.ru/tyumen/firm/70000001048108193/65.610084%2C57.15728?m=65.610306%2C57.157317%2F19.23")],
                                [InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK)],
                                [InlineKeyboardButton("🛍 Поддержка", url="https://t.me/SkidkaService01")]
                            ])
                        )
                        return
            except Exception as e:
                logger.error(f"Ошибка обработки startapp: {e}")
                # При ошибке показываем стандартный каталог
                pass
        
        # Обрабатываем параметр catalog из кнопки канала
        elif len(args) > 0 and args[0] == 'catalog':
            # Показываем каталог с Mini App кнопкой
            await update.message.reply_text(
                "🛍️ *Добро пожаловать в каталог!*\n\n"
                "Нажмите кнопку ниже чтобы открыть интерактивный каталог:\n\n"
                "🌟 Стройматериалы по уникально низким ценам!\n\n"
                "📍 Адрес отдела продаж: г. Тюмень, ул. Барабинская, д. 3а, стр. 4\n"
                "⏰ Понедельник - пятница: 09:00-18:00\n"
                "⏰ Суббота - воскресенье: 09:00-16:00\n\n"
                "📞 Звони! 60-01-60!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📂 Открыть каталог", web_app=WebAppInfo(url="https://dmitrii945.github.io/miniapp/"))],
                    [InlineKeyboardButton("🚕 Заказать Яндекс.Такси", url="https://3.redirect.appmetrica.yandex.com/route?end-lat=57.15728&end-lon=65.610084&ref=external_site_button")],
                    [InlineKeyboardButton("📍 Мы на карте 2ГИС", url="https://2gis.ru/tyumen/firm/70000001048108193/65.610084%2C57.15728?m=65.610306%2C57.157317%2F19.23")],
                    [InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK)],
                    [InlineKeyboardButton("🛍 Поддержка", url="https://t.me/SkidkaService01")]
                ])
            )
            return
        
        # Обрабатываем стандартные параметры share
        elif len(args) > 0 and args[0].startswith('share_'):
            try:
                share_params = args[0].replace('share_', '').split('_')
                if len(share_params) >= 5:
                    section_hash, category_hash, model_hash, submodel_hash, product_index = share_params[:5]
                    await handle_share_command(update, context, 
                                             section_hash, category_hash, 
                                             model_hash, submodel_hash, product_index)
                    return
            except Exception as e:
                logger.error(f"Ошибка обработки deep link: {e}")
    
    # Стандартное приветствие (если нет параметров или ошибка)
    welcome_message = (
        "📱 *Каталог товаров*\n\n"
        "Нажмите кнопку ниже чтобы открыть интерактивный каталог:\n\n"
        "🌟 Стройматериалы по уникально низким ценам!\n\n"
        "📍 Адрес отдела продаж: г. Тюмень, ул. Барабинская, д. 3а, стр. 4\n"
        "⏰ Понедельник - пятница: 09:00-18:00\n"
        "⏰ Суббота - воскресенье: 09:00-16:00\n\n"
        "📞 Звони! 60-01-60!"
    )

    keyboard = [
        [InlineKeyboardButton("📂 Открыть каталог", web_app=WebAppInfo(url="https://dmitrii945.github.io/miniapp/"))],
        [InlineKeyboardButton("🚕 Заказать Яндекс.Такси", url="https://3.redirect.appmetrica.yandex.com/route?end-lat=57.15728&end-lon=65.610084&ref=external_site_button")],
        [InlineKeyboardButton("📍 Мы на карте 2ГИС", url="https://2gis.ru/tyumen/firm/70000001048108193/65.610084%2C57.15728?m=65.610306%2C57.157317%2F19.23")],
        [InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🛍 Поддержка", url="https://t.me/SkidkaService01")]
    ]

    await update.message.reply_text(
        welcome_message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_share_command(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             section_hash=None, category_hash=None, 
                             model_hash=None, submodel_hash=None, product_index=None):
    """Обработчик команды /share с параметрами товара"""
    try:
        # Если параметры не переданы, парсим из аргументов команды
        if not all([section_hash, category_hash, model_hash, submodel_hash, product_index]):
            args = context.args
            if not args or len(args) < 5:
                await update.message.reply_text("❌ Неверный формат команды. Используйте: /share section_hash category_hash model_hash submodel_hash product_index")
                return
            
            try:
                section_hash, category_hash, model_hash, submodel_hash, product_index_str = args[:5]
                product_index = int(product_index_str)
            except (ValueError, IndexError):
                await update.message.reply_text("❌ Неверный формат команды. Индекс товара должен быть числом.")
                return
        
        # Получаем данные товара из API
        product_data = await get_product_from_api(section_hash, category_hash, model_hash, submodel_hash, product_index)
        
        if not product_data or 'error' in product_data:
            await update.message.reply_text("❌ Товар не найден или был удален")
            return

        if not product_data.get('success'):
            await update.message.reply_text("❌ Товар не найден")
            return

        product = product_data['product']
        
        # Формируем сообщение с фото
        caption = f"🏷 {product.get('color', 'Без названия')}\n"
        if product.get('price'):
            caption += f"💵 Цена: {product['price']}\n"
        if product.get('photo_description'):
            caption += f"📝 {product['photo_description']}\n"
        if product.get('description'):
            caption += f"ℹ️ {product['description']}\n"
        
        caption += f"\n📍 Раздел: {product.get('section_name', 'Не указан')}\n"
        caption += f"📂 Категория: {product.get('category_name', 'Не указана')}\n"
        
        if product.get('model_name') and product['model_name'] != "Без модели":
            caption += f"🔧 Модель: {product['model_name']}\n"
        
        if product.get('submodel_name') and product['submodel_name'] != "Без подмодели":
            caption += f"⚙️ Подмодель: {product['submodel_name']}\n"

        # СПЕЦИАЛЬНЫЙ DEEP LINK КАК НА СКРИНЕ - ИСПРАВЛЕННАЯ ССЫЛКА
        mini_app_deep_link = f"https://t.me/SSERVICE72_bot?startapp=item_{section_hash}_{category_hash}_{model_hash}_{submodel_hash}_{product_index}"
        manager_url = "https://t.me/SkidkaService01"
        
        keyboard = [
            [InlineKeyboardButton("📂 Открыть в каталоге", url=mini_app_deep_link)],
            [InlineKeyboardButton("💬 Написать менеджеру", url=manager_url)]
        ]

        # Отправляем товар с кнопками
        if product.get('photo_url'):
            try:
                await update.message.reply_photo(
                    photo=product['photo_url'],
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
                await update.message.reply_text(
                    caption + "\n\n📷 Фото недоступно",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        else:
            await update.message.reply_text(
                caption + "\n\n📷 Фото отсутствует",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Ошибка в handle_share_command: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке товара")

async def get_product_from_api(section_hash, category_hash, model_hash, submodel_hash, product_index):
    """Получает данные товара из Flask API с использованием requests"""
    try:
        url = f"https://dmitrii2613.pythonanywhere.com/api/product_by_index/{section_hash}/{category_hash}/{model_hash}/{submodel_hash}/{product_index}"
        
        response = requests.get(url, timeout=30)
        response.raise_for_status()  # Вызывает исключение для статусов 4xx/5xx
        
        return response.json()
            
    except requests.exceptions.Timeout:
        logger.error("Timeout при запросе к API")
        return {'error': 'Timeout'}
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка запроса к API: {e}")
        return {'error': f'Request error: {str(e)}'}
    except Exception as e:
        logger.error(f"Ошибка при обработке ответа API: {e}")
        return {'error': str(e)}

def save_user_to_db(user_id, username, first_name, last_name):
    """Сохраняет пользователя в базу данных"""
    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()

    try:
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))

        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователя: {e}")
    finally:
        conn.close()

async def is_user_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        member = await context.bot.get_chat_member(chat_id="@skidkaservis", user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False

def add_to_favorites(user_id, section_hash, category_hash, model_hash, submodel_hash, product_index, current_price):
    """Добавляет товар в избранное пользователя"""
    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO favorites 
            (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index, current_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index, current_price))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления в избранное: {e}")
        return False
    finally:
        conn.close()

def remove_from_favorites(user_id, section_hash, category_hash, model_hash, submodel_hash, product_index):
    """Удаляет товар из избранного пользователя"""
    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            DELETE FROM favorites 
            WHERE user_id = ? AND section_hash = ? AND category_hash = ? 
            AND model_hash = ? AND submodel_hash = ? AND product_index = ?
        ''', (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления из избранного: {e}")
        return False
    finally:
        conn.close()

def get_user_favorites(user_id):
    """Получает список избранных товаров пользователя"""
    conn = sqlite3.connect('catalog.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT section_hash, category_hash, model_hash, submodel_hash, product_index, current_price
            FROM favorites
            WHERE user_id = ?
        ''', (user_id,))
        
        return cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения избранного: {e}")
        return []
    finally:
        conn.close()

async def check_price_changes(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка изменения цен для избранных товаров"""
    try:
        conn = sqlite3.connect('catalog.db')
        cursor = conn.cursor()
        
        # Получаем все избранные товары
        cursor.execute('SELECT DISTINCT user_id, section_hash, category_hash, model_hash, submodel_hash, product_index, current_price FROM favorites')
        favorites = cursor.fetchall()
        
        logger.info(f"🔍 Начинаем проверку цен. Найдено избранных товаров: {len(favorites)}")
        
        if len(favorites) == 0:
            logger.info("⚠️ В таблице favorites нет записей. Проверка пропущена.")
            conn.close()
            return
        
        for user_id, section_hash, category_hash, model_hash, submodel_hash, product_index, old_price in favorites:
            try:
                logger.info(f"🔍 Проверка товара для пользователя {user_id}: {section_hash}/{category_hash}/{model_hash}/{submodel_hash}/{product_index}")
                logger.info(f"   Сохраненная цена: {old_price}")
                
                # Получаем актуальную информацию о товаре из API
                product_data = await get_product_from_api(section_hash, category_hash, model_hash, submodel_hash, product_index)
                
                if product_data and product_data.get('success'):
                    product = product_data['product']
                    new_price = product.get('price', '')
                    
                    logger.info(f"   Актуальная цена: {new_price}")
                    
                    # Проверяем изменение цены
                    if old_price and new_price and old_price != new_price:
                        logger.info(f"   ⚠️ ЦЕНА ИЗМЕНИЛАСЬ! Было: {old_price}, Стало: {new_price}")
                        
                        # Цена изменилась - отправляем уведомление
                        try:
                            # Определяем направление изменения цены
                            price_change = "📉 подешевел" if is_price_lower(new_price, old_price) else "📈 подорожал"
                            
                            message = (
                                f"💰 *Изменение цены в избранном!*\n\n"
                                f"🏷 {product.get('color', 'Товар')}\n"
                                f"\n"
                                f"Было: {old_price}\n"
                                f"Стало: {new_price}\n"
                                f"\n"
                                f"Товар {price_change}!"
                            )
                            
                            # Формируем кнопку для просмотра товара
                            mini_app_url = f"https://dmitrii945.github.io/miniapp/?section={section_hash}&category={category_hash}&model={model_hash}&submodel={submodel_hash}&product={product_index}"
                            keyboard = InlineKeyboardMarkup([
                                [InlineKeyboardButton("🛍️ Посмотреть товар", web_app=WebAppInfo(url=mini_app_url))]
                            ])
                            
                            # Отправляем уведомление пользователю
                            if product.get('photo_url'):
                                await context.bot.send_photo(
                                    chat_id=user_id,
                                    photo=product['photo_url'],
                                    caption=message,
                                    reply_markup=keyboard,
                                    parse_mode="Markdown"
                                )
                            else:
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text=message,
                                    reply_markup=keyboard,
                                    parse_mode="Markdown"
                                )
                            
                            # Обновляем цену в базе данных
                            cursor.execute('''
                                UPDATE favorites 
                                SET current_price = ? 
                                WHERE user_id = ? AND section_hash = ? AND category_hash = ? 
                                AND model_hash = ? AND submodel_hash = ? AND product_index = ?
                            ''', (new_price, user_id, section_hash, category_hash, model_hash, submodel_hash, product_index))
                            conn.commit()
                            
                            logger.info(f"✅ Уведомление об изменении цены отправлено пользователю {user_id}")
                            
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
                    else:
                        logger.info(f"   ✓ Цена не изменилась")
                else:
                    logger.warning(f"   ❌ Не удалось получить данные товара из API")
                
                # Небольшая задержка между запросами
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Ошибка проверки цены товара: {e}")
                continue
        
        conn.close()
        logger.info("✅ Проверка цен завершена")
        
    except Exception as e:
        logger.error(f"Ошибка в check_price_changes: {e}")

def is_price_lower(new_price_str, old_price_str):
    """Сравнивает две цены и определяет, стала ли новая цена ниже"""
    try:
        # Извлекаем числа из строк (убираем все кроме цифр и точки)
        new_price = float(re.sub(r'[^0-9.]', '', new_price_str))
        old_price = float(re.sub(r'[^0-9.]', '', old_price_str))
        return new_price < old_price
    except:
        return False

async def post_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Публикация поста в канал с инлайн кнопкой Mini App"""
    user_id = update.effective_user.id
    
    # Проверка прав администратора
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав для публикации постов в канал")
        return
    
    # Проверяем, что это ответ на сообщение
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "📝 Для публикации поста:\n"
            "1. Ответьте на сообщение с фото и текстом командой /post\n"
            "2. Или отправьте сообщение в формате:\n"
            "   /post <текст поста>"
        )
        return
    
    replied_message = update.message.reply_to_message
    
    # Формируем кнопку для открытия Mini App (через бота с start параметром)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Открыть каталог", url="https://t.me/SSERVICE72_bot?start=catalog")],
        [InlineKeyboardButton("💬 Связаться с нами", url="https://t.me/SkidkaService01")]
    ])
    
    try:
        # Если в ответе есть фото
        if replied_message.photo:
            photo = replied_message.photo[-1].file_id  # Берем фото лучшего качества
            caption = replied_message.caption or ""
            
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            await update.message.reply_text("✅ Пост с фото успешно опубликован в канал!")
        
        # Если это текстовое сообщение
        elif replied_message.text:
            text = replied_message.text
            
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            await update.message.reply_text("✅ Пост с текстом успешно опубликован в канал!")
        
        # Если это видео
        elif replied_message.video:
            video = replied_message.video.file_id
            caption = replied_message.caption or ""
            
            await context.bot.send_video(
                chat_id=CHANNEL_ID,
                video=video,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            await update.message.reply_text("✅ Пост с видео успешно опубликован в канал!")
        
        else:
            await update.message.reply_text("❌ Поддерживаются только фото, видео или текстовые сообщения")
    
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")
        await update.message.reply_text(f"❌ Ошибка публикации: {str(e)}")

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Автоматическая обработка постов, опубликованных в канале"""
    try:
        # Получаем информацию о посте в канале
        channel_post = update.channel_post
        
        if not channel_post:
            return
        
        # Формируем кнопки для открытия Mini App (через бота с start параметром)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Открыть каталог", url="https://t.me/SSERVICE72_bot?start=catalog")],
            [InlineKeyboardButton("💬 Связаться с нами", url="https://t.me/SkidkaService01")]
        ])
        
        # Редактируем пост, добавляя кнопки
        try:
            if channel_post.photo:
                # Если это фото с подписью
                await context.bot.edit_message_reply_markup(
                    chat_id=channel_post.chat_id,
                    message_id=channel_post.message_id,
                    reply_markup=keyboard
                )
            elif channel_post.video:
                # Если это видео
                await context.bot.edit_message_reply_markup(
                    chat_id=channel_post.chat_id,
                    message_id=channel_post.message_id,
                    reply_markup=keyboard
                )
            elif channel_post.text:
                # Если это текстовое сообщение
                await context.bot.edit_message_reply_markup(
                    chat_id=channel_post.chat_id,
                    message_id=channel_post.message_id,
                    reply_markup=keyboard
                )
            
            logger.info(f"✅ Кнопка добавлена к посту {channel_post.message_id} в канале")
            
        except BadRequest as e:
            logger.error(f"Ошибка редактирования поста: {e}")
        except Exception as e:
            logger.error(f"Неизвестная ошибка при добавлении кнопки: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка в handle_channel_post: {e}")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Объединенный обработчик всех текстовых сообщений"""
    # Перенаправляем в Mini App
    await update.message.reply_text(
        "📱 Для работы с каталогом используйте наш интерактивный каталог:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Открыть каталог", web_app=WebAppInfo(url="https://dmitrii945.github.io/miniapp/"))]
        ])
    )

async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-запросов для поиска товаров с фото"""
    query = update.inline_query
    query_text = query.query
    if not query_text or len(query_text) < 2:
        return

    logger.info(f"Inline запрос: {query_text}")

    # Для inline-режима также перенаправляем в Mini App
    results = [
        InlineQueryResultArticle(
            id="mini_app",
            title="📂 Открыть каталог",
            description="Интерактивный каталог товаров",
            input_message_content=InputTextMessageContent(
                f"🔍 Поиск: '{query_text}'\n\n"
                "Для просмотра результатов используйте наш интерактивный каталог:",
                parse_mode="Markdown"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Открыть каталог", web_app=WebAppInfo(url="https://dmitrii945.github.io/miniapp/"))]
            ])
        )
    ]

    await query.answer(results, cache_time=300)

async def setup_mini_app(application):
    """Настройка Mini App в боте"""
    try:
        # Устанавливаем кнопку меню
        menu_button = MenuButtonWebApp(
            text="📱 Каталог",
            web_app=WebAppInfo(url="https://dmitrii945.github.io/miniapp/")
        )

        await application.bot.set_chat_menu_button(menu_button=menu_button)
        logger.info("✅ Mini App кнопка установлена")

    except Exception as e:
        logger.error(f"❌ Ошибка установки Mini App: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)

    # Логируем дополнительную информацию
    if update and update.callback_query:
        logger.error(f"Callback data: {update.callback_query.data}")
        logger.error(f"User data: {context.user_data}")

    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Произошла ошибка. Пожалуйста, попробуйте позже."
            )
        except:
            pass  # Игнорируем ошибки при отправке сообщения об ошибке

def main():
    """Основная функция"""
    # Инициализация базы данных
    init_database()
    init_price_tracking()

    # Создание приложения
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("share", handle_share_command))
    application.add_handler(CommandHandler("post", post_to_channel))

    # Обработчики callback-запросов
    application.add_handler(CallbackQueryHandler(show_dynamic_sections, pattern=r"^show_catalog$"))

    # Inline обработчик
    application.add_handler(InlineQueryHandler(handle_inline_query))

    # Обработчик постов в канале (автоматическое добавление кнопок)
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    # Объединенный обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    # Настройка Mini App
    application.post_init = setup_mini_app

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    # Настройка периодической проверки цен (каждые 5 минут)
    job_queue = application.job_queue
    job_queue.run_repeating(check_price_changes, interval=300, first=60)
    logger.info("✅ Запущена периодическая проверка цен (каждые 5 минут)")

    # Запуск бота
    application.run_polling()
    logger.info("Бот запущен")

if __name__ == "__main__":
    main()