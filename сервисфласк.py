from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import sqlite3
import uuid
import gspread
from google.oauth2.service_account import Credentials
import re
import logging
import os
import datetime
import requests
import urllib.parse

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "Cache-Control", "Pragma"],
        "expose_headers": ["Content-Type"],
        "supports_credentials": False
    }
})

# Настройки
GOOGLE_SHEETS_CREDS = '/home/Dmitrii2613/sservice/credentials.json'
SPREADSHEET_ID = '1qj1DpMXQVuyYaVXwpKqUdVA4NJO_s5dt3LejKC1fyMg'
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat.db')
CATALOG_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalog.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_favorites_database():
    """Инициализация базы данных для избранного"""
    conn = sqlite3.connect(CATALOG_DB_PATH)
    cursor = conn.cursor()
    
    # Таблица избранных товаров
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
    logger.info("✅ База данных избранного инициализирована")

def check_credentials():
    """Проверка наличия файла credentials"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    creds_path = os.path.join(current_dir, GOOGLE_SHEETS_CREDS)

    print(f"🔍 Проверка файла: {creds_path}")

    if not os.path.exists(creds_path):
        print(f"❌ Файл {GOOGLE_SHEETS_CREDS} не найден!")
        return False

    print(f"✅ Файл {GOOGLE_SHEETS_CREDS} найден")
    return True

def get_google_sheets_data(range_name='A2:J1500'):
    """Получение данных из Google Sheets"""
    try:
        scope = ['https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/spreadsheets']

        creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1

        data = sheet.get(range_name)
        print(f"✅ Успешно получено {len(data)} строк")
        return data

    except Exception as e:
        logger.error(f"Ошибка чтения Google Sheets: {e}")
        return []

def update_database_schema():
    """Обновление схемы базы данных для поддержки длинных описаний"""
    conn = sqlite3.connect('/home/Dmitrii2613/catalog.db')
    cursor = conn.cursor()

    # Изменяем тип поля name в таблице hashes на TEXT без ограничений
    try:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS hashes_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            hash TEXT NOT NULL UNIQUE
        )
        ''')

        # Копируем данные из старой таблицы
        cursor.execute('INSERT INTO hashes_new SELECT * FROM hashes')

        # Удаляем старую таблицу и переименовываем новую
        cursor.execute('DROP TABLE hashes')
        cursor.execute('ALTER TABLE hashes_new RENAME TO hashes')

        print("✅ Схема базы данных обновлена для поддержки длинных описаний")

    except Exception as e:
        print(f"❌ Ошибка обновления схемы: {e}")
        # Если что-то пошло не так, откатываем изменения
        conn.rollback()

    conn.commit()
    conn.close()


def parse_catalog_data(data):
    """Парсинг данных каталога"""
    catalog = {}
    current_section = None
    current_category = None
    current_model = None
    current_submodel = None

    for i, row in enumerate(data):
        if len(row) < 3 or (row[0] == "Раздел" and row[1] == "Категория"):
            continue

        if row[0]:
            current_section = row[0].strip()
            current_category = None
            current_model = None
            current_submodel = None

        if len(row) > 1 and row[1]:
            current_category = row[1].strip()
            current_model = None
            current_submodel = None

        if len(row) > 2 and row[2]:
            current_model = row[2].strip()
            current_submodel = None

        if len(row) > 3 and row[3]:
            current_submodel = row[3].strip()

        if not current_section or not current_category:
            continue

        color = row[4].strip() if len(row) > 4 and row[4] else None
        price = row[5].strip() if len(row) > 5 and row[5] else None
        photo_url = row[6].strip() if len(row) > 6 and row[6] else None
        description = row[7].strip() if len(row) > 7 and row[7] else None
        special_price = row[8].strip() if len(row) > 8 and row[8] else None
        keywords = row[9].strip() if len(row) > 9 and row[9] else None
        photo_description = None

        has_product_data = color or price or photo_url or description or photo_description

        if not has_product_data:
            continue

        if not current_model:
            current_model = "Без модели"
            current_submodel = "Без подмодели"
        elif not current_submodel:
            current_submodel = "Без подмодели"

        if current_section not in catalog:
            catalog[current_section] = {}

        if current_category not in catalog[current_section]:
            catalog[current_section][current_category] = {}

        if current_model not in catalog[current_section][current_category]:
            catalog[current_section][current_category][current_model] = {}

        submodel_key = current_submodel if current_submodel else "Без подмодели"

        if submodel_key not in catalog[current_section][current_category][current_model]:
            catalog[current_section][current_category][current_model][submodel_key] = []

        catalog[current_section][current_category][current_model][submodel_key].append({
            'color': color,
            'price': price,
            'photo_url': photo_url,
            'photo_id': photo_url,
            'row_index': i + 1,
            'description': description,
            'photo_description': photo_description,
            'special_price': special_price,
            'keywords': keywords
        })

    return catalog

def extract_numeric_price(price_str):
    """Извлекает числовое значение из строки цены"""
    if not price_str or not isinstance(price_str, str):
        return float('inf')

    try:
        price_str = str(price_str).strip()
        if not price_str:
            return float('inf')

        clean_str = re.sub(r'[^\d,.]', '', price_str)
        if not clean_str:
            return float('inf')

        clean_str = clean_str.replace(',', '.')
        if clean_str.count('.') > 1:
            parts = clean_str.split('.')
            clean_str = parts[0] + '.' + ''.join(parts[1:])

        result = float(clean_str)
        return result

    except (ValueError, AttributeError, TypeError) as e:
        logger.error(f"Ошибка преобразования цены '{price_str}': {e}")
        return float('inf')

def get_min_price_in_category(catalog, section, category):
    """Получает минимальную цену в категории"""
    min_price = float('inf')

    if section in catalog and category in catalog[section]:
        for model in catalog[section][category].values():
            for submodel in model.values():
                for product in submodel:
                    if product['price']:
                        price_num = extract_numeric_price(product['price'])
                        if price_num != float('inf'):
                            min_price = min(min_price, price_num)

    return min_price if min_price != float('inf') else None

def get_min_price_in_model(catalog, section, category, model):
    """Получает минимальную цену в модели"""
    min_price = float('inf')

    if (section in catalog and
        category in catalog[section] and
        model in catalog[section][category]):

        for submodel in catalog[section][category][model].values():
            for product in submodel:
                if product['price']:
                    price_num = extract_numeric_price(product['price'])
                    if price_num != float('inf'):
                        min_price = min(min_price, price_num)

    return min_price if min_price != float('inf') else None

def get_or_create_hash(name, hash_type):
    """Получает или создает хэш для имени (поддерживает длинные имена)"""
    if name is None:
        name = "None"

    # Обрезаем очень длинные имена (более 1000 символов) чтобы избежать проблем с производительностью
    if len(name) > 1000:
        name = name[:1000] + "..."

    conn = sqlite3.connect('/home/Dmitrii2613/catalog.db')
    cursor = conn.cursor()

    cursor.execute('SELECT hash FROM hashes WHERE name = ? AND type = ?',
                  (name, hash_type))
    result = cursor.fetchone()

    if result:
        conn.close()
        return result[0]

    new_hash = str(uuid.uuid4()).replace('-', '')[:16]

    try:
        cursor.execute('INSERT INTO hashes (name, type, hash) VALUES (?, ?, ?)',
                      (name, hash_type, new_hash))
        conn.commit()
    except Exception as e:
        print(f"❌ Ошибка вставки хэша: {e}")
        # В случае ошибки генерируем хэш на основе имени
        import hashlib
        new_hash = hashlib.md5(name.encode()).hexdigest()[:16]

    conn.close()
    return new_hash

def get_name_by_hash(target_hash):
    """Получает имя по хэшу"""
    if not target_hash or target_hash == 'null' or target_hash == 'empty':
        return (None, None)

    conn = sqlite3.connect('/home/Dmitrii2613/catalog.db')
    cursor = conn.cursor()

    cursor.execute('SELECT name, type FROM hashes WHERE hash = ?', (target_hash,))
    result = cursor.fetchone()
    conn.close()

    return result if result else (None, None)

def extract_photo_filename_from_url(url):
    """Извлекает прямую ссылку на изображение из Google Drive"""
    try:
        if not url or not isinstance(url, str):
            return None

        url = url.strip()

        # Если это уже прямая ссылка на изображение
        if url.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')):
            return url if url.startswith('http') else None

        # Обработка Google Drive ссылок
        if 'drive.google.com' in url:
            file_id = None

            # Разные форматы Google Drive ссылок
            if '/file/d/' in url:
                file_id = url.split('/file/d/')[1].split('/')[0]
            elif 'id=' in url:
                file_id = url.split('id=')[1].split('&')[0]
            elif '/open?id=' in url:
                file_id = url.split('/open?id=')[1].split('&')[0]

            if file_id:
                return f"https://drive.google.com/uc?export=view&id={file_id}"
            else:
                return None

        # Если это обычная HTTP ссылка
        if url.startswith(('http://', 'https://')):
            return url

        return None

    except Exception as e:
        logger.error(f"Ошибка извлечения фото из URL: {e}")
        return None

def init_database():
    """Инициализация базы данных"""
    conn = sqlite3.connect('/home/Dmitrii2613/catalog.db')
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS hashes (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        hash TEXT NOT NULL UNIQUE
    )
    ''')

    special_entries = [
        ("Без модели", "model"),
        ("Без подмодели", "submodel")
    ]

    for name, type_name in special_entries:
        cursor.execute('SELECT hash FROM hashes WHERE name = ? AND type = ?', (name, type_name))
        if not cursor.fetchone():
            new_hash = str(uuid.uuid4()).replace('-', '')[:16]
            cursor.execute('INSERT INTO hashes (name, type, hash) VALUES (?, ?, ?)',
                          (name, type_name, new_hash))

    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

    update_database_schema()

init_database()

# ===== API ROUTES =====

@app.route('/')
def hello_world():
    return jsonify({
        'status': 'ok',
        'message': 'Mini App API работает!',
        'endpoints': {
            'sections': '/api/sections',
            'categories': '/api/categories/<section_hash>',
            'models': '/api/models/<section_hash>/<category_hash>',
            'submodels': '/api/submodels/<section_hash>/<category_hash>/<model_hash>',
            'products': '/api/products/<section_hash>/<category_hash>/<model_hash>/<submodel_hash>',
            'products_by_section': '/api/products_by_section/<section_hash>',
            'products_by_category': '/api/products_by_category/<section_hash>/<category_hash>',
            'products_by_model': '/api/products_by_model/<section_hash>/<category_hash>/<model_hash>',
            'products_by_submodel': '/api/products_by_submodel/<section_hash>/<category_hash>/<model_hash>/<submodel_hash>'
        }
    })


@app.route('/api/sections')
def api_get_sections():
    """API для получения разделов"""
    try:
        print("=" * 50)
        print("🔄 НАЧАЛО ОБРАБОТКИ /api/sections")

        if not check_credentials():
            test_sections = [
                {'name': 'Металлопрокат (тест)', 'id': 'test1'},
                {'name': 'Кровельные материалы (тест)', 'id': 'test2'},
            ]
            return jsonify({'sections': test_sections})

        print("📥 Получение данных из Google Sheets...")
        data = get_google_sheets_data('A2:J1500')
        print(f"📊 Получено строк из Google Sheets: {len(data)}")
        
        # ДИАГНОСТИКА: показываем первые 5 строк
        if data:
            print("🔍 ПЕРВЫЕ 5 СТРОК ИЗ ТАБЛИЦЫ:")
            for i, row in enumerate(data[:5]):
                print(f"  Строка {i+2}: {row}")

        if not data:
            print("❌ Google Sheets вернул пустые данные")
            return jsonify({'sections': []})

        print("🔍 Парсинг каталога...")
        catalog = parse_catalog_data(data)
        print(f"📂 Разделов в каталоге: {len(catalog)}")

        if catalog:
            print("📋 Найденные разделы:")
            for section in catalog.keys():
                print(f"  - {section}")
        else:
            print("❌ Каталог пустой после парсинга")

        sections = []
        for section_name in sorted(catalog.keys()):
            section_hash = get_or_create_hash(section_name, 'section')
            sections.append({
                'name': section_name,
                'id': section_hash
            })
            print(f"  ✅ Добавлен раздел: {section_name} -> {section_hash}")

        print(f"✅ ИТОГО: Отправляем {len(sections)} разделов")
        print("=" * 50)
        return jsonify({'sections': sections})

    except Exception as e:
        logger.error(f"API sections error: {e}")
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        print(f"📋 Traceback: {traceback.format_exc()}")

        return jsonify({'sections': [
            {'name': 'ОШИБКА: ' + str(e), 'id': 'error'}
        ]})

@app.route('/api/categories/<section_hash>')
def api_get_categories(section_hash):
    """API для получения категорий раздела"""
    try:
        print(f"🔍 ЗАПРОС КАТЕГОРИЙ ДЛЯ РАЗДЕЛА: {section_hash}")
        section_name, _ = get_name_by_hash(section_hash)
        print(f"📂 Найден раздел: {section_name}")

        if not section_name:
            print("❌ Раздел не найден по хэшу")
            return jsonify({'categories': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        print(f"📊 Каталог загружен, разделов: {len(catalog)}")

        categories = []
        if section_name in catalog:
            print(f"📋 Категории в разделе '{section_name}': {list(catalog[section_name].keys())}")

            for category_name in sorted(catalog[section_name].keys()):
                min_price = get_min_price_in_category(catalog, section_name, category_name)

                categories.append({
                    'name': category_name,
                    'id': get_or_create_hash(category_name, 'category'),
                    'min_price': min_price if min_price != float('inf') else None
                })
                print(f"  ✅ Добавлена категория: {category_name}")

        print(f"📤 Отправляем {len(categories)} категорий")
        return jsonify({
            'section_name': section_name,
            'categories': categories
        })

    except Exception as e:
        logger.error(f"API categories error: {e}")
        print(f"❌ ОШИБКА в categories: {e}")
        return jsonify({'categories': []})

@app.route('/api/models/<section_hash>/<category_hash>')
def api_get_models(section_hash, category_hash):
    """API для получения моделей категории"""
    try:
        print(f"🔍 ЗАПРОС МОДЕЛЕЙ: section={section_hash}, category={category_hash}")

        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        print(f"📂 Найден путь: {section_name} -> {category_name}")

        if not section_name or not category_name:
            print("❌ Раздел или категория не найдены")
            return jsonify({'models': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        models = []
        if (section_name in catalog and
            category_name in catalog[section_name]):

            print(f"📋 Модели в категории '{category_name}': {list(catalog[section_name][category_name].keys())}")

            for model_name in sorted(catalog[section_name][category_name].keys()):
                min_price = get_min_price_in_model(catalog, section_name, category_name, model_name)
                models.append({
                    'name': model_name,
                    'id': get_or_create_hash(model_name, 'model'),
                    'min_price': min_price if min_price != float('inf') else None
                })
                print(f"  ✅ Добавлена модель: {model_name}")

        print(f"📤 Отправляем {len(models)} моделей")
        return jsonify({
            'section_name': section_name,
            'category_name': category_name,
            'models': models
        })

    except Exception as e:
        logger.error(f"API models error: {e}")
        print(f"❌ ОШИБКА в models: {e}")
        return jsonify({'models': []})

@app.route('/api/submodels/<section_hash>/<category_hash>/<model_hash>')
def api_get_submodels(section_hash, category_hash, model_hash):
    """API для получения подмоделей"""
    try:
        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        if model_hash == 'null' or model_hash == 'empty':
            model_name = "Без модели"
        else:
            model_name, _ = get_name_by_hash(model_hash)

        print(f"🔍 ЗАПРОС ПОДМОДЕЛЕЙ: {section_name} -> {category_name} -> {model_name}")

        if not all([section_name, category_name, model_name]):
            return jsonify({'submodels': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        submodels = []
        if (section_name in catalog and
            category_name in catalog[section_name] and
            model_name in catalog[section_name][category_name]):

            submodels_data = catalog[section_name][category_name][model_name]

            for submodel_name in sorted(submodels_data.keys()):
                products = submodels_data[submodel_name]
                min_price = float('inf')

                for product in products:
                    if product['price']:
                        price_num = extract_numeric_price(product['price'])
                        if price_num != float('inf'):
                            min_price = min(min_price, price_num)

                submodels.append({
                    'name': submodel_name,
                    'id': get_or_create_hash(submodel_name, 'submodel'),
                    'min_price': min_price if min_price != float('inf') else None,
                    'product_count': len(products)
                })
                print(f"  ✅ Добавлена подмодель: {submodel_name} ({len(products)} товаров)")

        print(f"📤 Отправляем {len(submodels)} подмоделей")
        return jsonify({
            'section_name': section_name,
            'category_name': category_name,
            'model_name': model_name,
            'submodels': submodels
        })
    except Exception as e:
        logger.error(f"API submodels error: {e}")
        print(f"❌ ОШИБКА в submodels: {e}")
        return jsonify({'submodels': []})

@app.route('/api/products/<section_hash>/<category_hash>/<model_hash>/<submodel_hash>')
def api_get_products(section_hash, category_hash, model_hash, submodel_hash):
    """API для получения товаров"""
    try:
        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        if model_hash == 'null' or model_hash == 'empty':
            model_name = "Без модели"
        else:
            model_name, _ = get_name_by_hash(model_hash)

        if submodel_hash == 'null' or submodel_hash == 'empty':
            submodel_name = "Без подмодели"
        else:
            submodel_name, _ = get_name_by_hash(submodel_hash)

        print(f"🔍 ЗАПРОС ТОВАРОВ: {section_name} -> {category_name} -> {model_name} -> {submodel_name}")

        if not all([section_name, category_name, model_name, submodel_name]):
            print("❌ Не все параметры найдены по хэшам")
            return jsonify({'products': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        products = []
        if (section_name in catalog and
            category_name in catalog[section_name] and
            model_name in catalog[section_name][category_name] and
            submodel_name in catalog[section_name][category_name][model_name]):

            raw_products = catalog[section_name][category_name][model_name][submodel_name]
            print(f"📦 Найдено сырых товаров: {len(raw_products)}")

            for product in raw_products:
                photo_url = extract_photo_filename_from_url(product.get('photo_url'))

                processed_product = {
                    'color': product['color'],
                    'price': product['price'],
                    'photo_url': photo_url,
                    'photo_description': product.get('photo_description', ''),
                    'description': product.get('description', ''),
                    'special_price': product.get('special_price'),
                    'keywords': product.get('keywords'),
                    'row_index': product['row_index']
                }
                products.append(processed_product)
                print(f"  ✅ Обработан товар: {product['color']}, фото: {photo_url}")

        print(f"📤 Отправляем {len(products)} товаров")
        return jsonify({
            'section_name': section_name,
            'category_name': category_name,
            'model_name': model_name,
            'submodel_name': submodel_name,
            'products': products
        })
    except Exception as e:
        logger.error(f"API products error: {e}")
        print(f"❌ ОШИБКА в products: {e}")
        import traceback
        print(f"📋 Traceback: {traceback.format_exc()}")
        return jsonify({'products': []})

@app.route('/api/products_by_section/<section_hash>')
def api_get_products_by_section(section_hash):
    """API для получения всех товаров раздела"""
    try:
        section_name, _ = get_name_by_hash(section_hash)

        if not section_name:
            return jsonify({'products': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        products = []
        if section_name in catalog:
            for category_name in catalog[section_name].keys():
                for model_name in catalog[section_name][category_name].keys():
                    for submodel_name in catalog[section_name][category_name][model_name].keys():
                        raw_products = catalog[section_name][category_name][model_name][submodel_name]
                        for product in raw_products:
                            photo_url = extract_photo_filename_from_url(product.get('photo_url'))

                            processed_product = {
                                'color': product['color'],
                                'price': product['price'],
                                'photo_url': photo_url,
                                'photo_description': product.get('photo_description', ''),
                                'description': product.get('description', ''),
                                'special_price': product.get('special_price'),
                                'keywords': product.get('keywords'),
                                'section': section_name,
                                'category': category_name,
                                'model': model_name,
                                'submodel': submodel_name
                            }
                            products.append(processed_product)

        return jsonify({
            'section_name': section_name,
            'products': products
        })
    except Exception as e:
        logger.error(f"API products_by_section error: {e}")
        return jsonify({'products': []})

@app.route('/api/products_by_category/<section_hash>/<category_hash>')
def api_get_products_by_category(section_hash, category_hash):
    """API для получения всех товаров категории"""
    try:
        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        if not section_name or not category_name:
            return jsonify({'products': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        products = []
        if (section_name in catalog and
            category_name in catalog[section_name]):

            for model_name in catalog[section_name][category_name].keys():
                for submodel_name in catalog[section_name][category_name][model_name].keys():
                    raw_products = catalog[section_name][category_name][model_name][submodel_name]
                    for product in raw_products:
                        photo_url = extract_photo_filename_from_url(product.get('photo_url'))

                        processed_product = {
                            'color': product['color'],
                            'price': product['price'],
                            'photo_url': photo_url,
                            'photo_description': product.get('photo_description', ''),
                            'description': product.get('description', ''),
                            'special_price': product.get('special_price'),
                            'keywords': product.get('keywords'),
                            'section': section_name,
                            'category': category_name,
                            'model': model_name,
                            'submodel': submodel_name
                        }
                        products.append(processed_product)

        return jsonify({
            'section_name': section_name,
            'category_name': category_name,
            'products': products
        })
    except Exception as e:
        logger.error(f"API products_by_category error: {e}")
        return jsonify({'products': []})

@app.route('/api/products_by_model/<section_hash>/<category_hash>/<model_hash>')
def api_get_products_by_model(section_hash, category_hash, model_hash):
    """API для получения всех товаров модели"""
    try:
        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        if model_hash == 'null' or model_hash == 'empty':
            model_name = "Без модели"
        else:
            model_name, _ = get_name_by_hash(model_hash)

        if not section_name or not category_name or not model_name:
            return jsonify({'products': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        products = []
        if (section_name in catalog and
            category_name in catalog[section_name] and
            model_name in catalog[section_name][category_name]):

            for submodel_name in catalog[section_name][category_name][model_name].keys():
                raw_products = catalog[section_name][category_name][model_name][submodel_name]
                for product in raw_products:
                    photo_url = extract_photo_filename_from_url(product.get('photo_url'))

                    processed_product = {
                        'color': product['color'],
                        'price': product['price'],
                        'photo_url': photo_url,
                        'photo_description': product.get('photo_description', ''),
                        'description': product.get('description', ''),
                        'special_price': product.get('special_price'),
                        'keywords': product.get('keywords'),
                        'section': section_name,
                        'category': category_name,
                        'model': model_name,
                        'submodel': submodel_name
                    }
                    products.append(processed_product)

        return jsonify({
            'section_name': section_name,
            'category_name': category_name,
            'model_name': model_name,
            'products': products
        })
    except Exception as e:
        logger.error(f"API products_by_model error: {e}")
        return jsonify({'products': []})

@app.route('/api/products_by_submodel/<section_hash>/<category_hash>/<model_hash>/<submodel_hash>')
def api_get_products_by_submodel(section_hash, category_hash, model_hash, submodel_hash):
    """API для получения всех товаров подмодели"""
    try:
        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        if model_hash == 'null' or model_hash == 'empty':
            model_name = "Без модели"
        else:
            model_name, _ = get_name_by_hash(model_hash)

        if submodel_hash == 'null' or submodel_hash == 'empty':
            submodel_name = "Без подмодели"
        else:
            submodel_name, _ = get_name_by_hash(submodel_hash)

        if not all([section_name, category_name, model_name, submodel_name]):
            return jsonify({'products': []})

        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        products = []
        if (section_name in catalog and
            category_name in catalog[section_name] and
            model_name in catalog[section_name][category_name] and
            submodel_name in catalog[section_name][category_name][model_name]):

            raw_products = catalog[section_name][category_name][model_name][submodel_name]
            for product in raw_products:
                photo_url = extract_photo_filename_from_url(product.get('photo_url'))

                processed_product = {
                    'color': product['color'],
                    'price': product['price'],
                    'photo_url': photo_url,
                    'photo_description': product.get('photo_description', ''),
                    'description': product.get('description', ''),
                    'special_price': product.get('special_price'),
                    'keywords': product.get('keywords'),
                    'section': section_name,
                    'category': category_name,
                    'model': model_name,
                    'submodel': submodel_name
                }
                products.append(processed_product)

        return jsonify({
            'section_name': section_name,
            'category_name': category_name,
            'model_name': model_name,
            'submodel_name': submodel_name,
            'products': products
        })
    except Exception as e:
        logger.error(f"API products_by_submodel error: {e}")
        return jsonify({'products': []})

# ===== ПОИСК =====

def levenshtein_distance(s1, s2):
    """Вычисление расстояния Левенштейна для нечеткого поиска"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]

def fuzzy_match(query, text, threshold=0.7):
    """Нечеткое сравнение строк с учетом опечаток"""
    if not text:
        return False
    
    query = query.lower()
    text = text.lower()
    
    # Точное совпадение
    if query in text:
        return True
    
    # Разбиваем на слова
    text_words = text.split()
    query_words = query.split()
    
    # Проверяем каждое слово запроса
    for query_word in query_words:
        found = False
        for text_word in text_words:
            # Если слова достаточно похожи
            max_len = max(len(query_word), len(text_word))
            if max_len == 0:
                continue
            distance = levenshtein_distance(query_word, text_word)
            similarity = 1 - (distance / max_len)
            
            if similarity >= threshold:
                found = True
                break
        
        if not found:
            return False
    
    return True

def normalize_search_text(text):
    """Нормализация текста для поиска - убирает дефисы, пробелы, точки"""
    if not text:
        return ''
    
    import re
    # Приводим к нижнему регистру
    text = str(text).lower()
    # Убираем все символы кроме букв и цифр
    text = re.sub(r'[^а-яёa-z0-9]', '', text)
    return text

@app.route('/api/search')
def api_search():
    """🔥 УЛУЧШЕННЫЙ ПОИСК - ищет по всем полям с весами релевантности"""
    try:
        query = request.args.get('q', '').strip().lower()
        
        if not query or len(query) < 2:
            return jsonify({'products': [], 'query': query})
        
        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)
        
        results = []
        query_words = query.split()  # Разбиваем запрос на слова
        
        # Нормализуем запрос (убираем дефисы, пробелы и т.д.)
        normalized_query = normalize_search_text(query)
        normalized_query_words = [normalize_search_text(word) for word in query_words if len(word) >= 2]
        
        # Проходим по всем товарам
        for section_name in catalog.keys():
            for category_name in catalog[section_name].keys():
                for model_name in catalog[section_name][category_name].keys():
                    for submodel_name in catalog[section_name][category_name][model_name].keys():
                        raw_products = catalog[section_name][category_name][model_name][submodel_name]
                        
                        for product in raw_products:
                            # Собираем все поля для поиска
                            color = str(product.get('color', '')).lower()
                            description = str(product.get('description', '')).lower()
                            keywords = str(product.get('keywords', '')).lower()
                            price = str(product.get('price', '')).lower()
                            
                            # Нормализуем названия категорий
                            section_lower = section_name.lower()
                            category_lower = category_name.lower()
                            model_lower = model_name.lower()
                            submodel_lower = submodel_name.lower()
                            
                            # Нормализуем поля для сравнения без спецсимволов
                            color_normalized = normalize_search_text(color)
                            description_normalized = normalize_search_text(description)
                            keywords_normalized = normalize_search_text(keywords)
                            section_normalized = normalize_search_text(section_lower)
                            category_normalized = normalize_search_text(category_lower)
                            model_normalized = normalize_search_text(model_lower)
                            submodel_normalized = normalize_search_text(submodel_lower)
                            
                            # Система весов для релевантности (0-100)
                            relevance = 0
                            matched = False
                            
                            # 1. ТОЧНОЕ совпадение в названии товара (цвет/размер) - максимальный вес
                            if query in color or normalized_query in color_normalized:
                                relevance += 100
                                matched = True
                            
                            # 2. Совпадение в категории - высокий вес
                            if query in category_lower or normalized_query in category_normalized:
                                relevance += 80
                                matched = True
                            
                            # 3. Совпадение в разделе
                            if query in section_lower or normalized_query in section_normalized:
                                relevance += 70
                                matched = True
                            
                            # 4. Совпадение в модели
                            if query in model_lower or normalized_query in model_normalized:
                                relevance += 60
                                matched = True
                            
                            # 5. Совпадение в подмодели
                            if query in submodel_lower or normalized_query in submodel_normalized:
                                relevance += 50
                                matched = True
                            
                            # 6. Совпадение в описании
                            if query in description or normalized_query in description_normalized:
                                relevance += 40
                                matched = True
                            
                            # 7. Совпадение в ключевых словах
                            if query in keywords or normalized_query in keywords_normalized:
                                relevance += 30
                                matched = True
                            
                            # 8. Совпадение в цене (для поиска по цене)
                            if query in price:
                                relevance += 20
                                matched = True
                            
                            # 9. ПОИСК ПО СЛОВАМ - проверяем каждое слово запроса
                            for i, word in enumerate(query_words):
                                if len(word) < 2:
                                    continue
                                
                                # Нормализованное слово для сравнения
                                norm_word = normalized_query_words[i] if i < len(normalized_query_words) else ''
                                word_found = False
                                
                                # Проверяем каждое слово в разных полях
                                if word in color or (norm_word and norm_word in color_normalized):
                                    relevance += 15
                                    word_found = True
                                if word in category_lower or (norm_word and norm_word in category_normalized):
                                    relevance += 12
                                    word_found = True
                                if word in model_lower or (norm_word and norm_word in model_normalized):
                                    relevance += 10
                                    word_found = True
                                if word in section_lower or (norm_word and norm_word in section_normalized):
                                    relevance += 8
                                    word_found = True
                                if word in submodel_lower or (norm_word and norm_word in submodel_normalized):
                                    relevance += 6
                                    word_found = True
                                if word in description or (norm_word and norm_word in description_normalized):
                                    relevance += 5
                                    word_found = True
                                
                                if word_found:
                                    matched = True
                            
                            # Если нашли хоть одно совпадение, добавляем товар
                            if matched:
                                photo_url = extract_photo_filename_from_url(product.get('photo_url'))
                                
                                results.append({
                                    'color': product['color'],
                                    'price': product['price'],
                                    'photo_url': photo_url,
                                    'photo_description': product.get('photo_description', ''),
                                    'description': product.get('description', ''),
                                    'special_price': product.get('special_price'),
                                    'keywords': product.get('keywords'),
                                    'section': section_name,
                                    'category': category_name,
                                    'model': model_name,
                                    'submodel': submodel_name,
                                    'relevance': relevance
                                })
        
        # Сортируем по релевантности (от большего к меньшему)
        results.sort(key=lambda x: x.get('relevance', 0), reverse=True)
        
        # Удаляем поле relevance перед отправкой
        for result in results:
            result.pop('relevance', None)
        
        logger.info(f"🔍 Поиск '{query}': найдено {len(results)} товаров")
        return jsonify({
            'products': results,
            'query': query,
            'count': len(results)
        })
        
    except Exception as e:
        logger.error(f"API search error: {e}")
        import traceback
        print(f"📋 Traceback: {traceback.format_exc()}")
        return jsonify({'products': [], 'query': query})

@app.route('/api/search/suggestions')
def api_search_suggestions():
    """API для автодополнения при поиске"""
    try:
        query = request.args.get('q', '').strip().lower()
        
        if not query or len(query) < 2:
            return jsonify({'suggestions': []})
        
        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)
        
        suggestions_set = set()
        
        # Собираем уникальные названия товаров и ключевые слова
        for section_name in catalog.keys():
            for category_name in catalog[section_name].keys():
                for model_name in catalog[section_name][category_name].keys():
                    for submodel_name in catalog[section_name][category_name][model_name].keys():
                        raw_products = catalog[section_name][category_name][model_name][submodel_name]
                        
                        for product in raw_products:
                            color = product.get('color', '').strip()
                            keywords = product.get('keywords', '').strip()
                            
                            # Добавляем название товара (цвет)
                            if color and query in color.lower():
                                suggestions_set.add(color)
                            
                            # Добавляем ключевые слова
                            if keywords:
                                keyword_list = [k.strip() for k in keywords.split(',')]
                                for keyword in keyword_list:
                                    if keyword and query in keyword.lower():
                                        suggestions_set.add(keyword)
                            
                            # Добавляем названия моделей
                            if query in model_name.lower():
                                suggestions_set.add(model_name)
        
        # Конвертируем в список и ограничиваем до 8 подсказок
        suggestions = sorted(list(suggestions_set))[:8]
        
        logger.info(f"💡 Подсказки для '{query}': {len(suggestions)} вариантов")
        return jsonify({'suggestions': suggestions})
        
    except Exception as e:
        logger.error(f"API suggestions error: {e}")
        import traceback
        print(f"📋 Traceback: {traceback.format_exc()}")
        return jsonify({'suggestions': []})

# ==================== API для получения товара по индексу ====================

@app.route('/api/product_by_index/<section_hash>/<category_hash>/<model_hash>/<submodel_hash>/<int:product_index>', methods=['GET'])
def get_product_by_index(section_hash, category_hash, model_hash, submodel_hash, product_index):
    """Получить конкретный товар по хэшам и индексу"""
    try:
        logger.info(f"📦 Запрос товара: {section_hash}/{category_hash}/{model_hash}/{submodel_hash}/{product_index}")
        
        # Получаем имена по хэшам
        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        if model_hash == 'null' or model_hash == 'empty':
            model_name = "Без модели"
        else:
            model_name, _ = get_name_by_hash(model_hash)

        if submodel_hash == 'null' or submodel_hash == 'empty':
            submodel_name = "Без подмодели"
        else:
            submodel_name, _ = get_name_by_hash(submodel_hash)

        if not all([section_name, category_name, model_name, submodel_name]):
            logger.error("❌ Не все параметры найдены по хэшам")
            return jsonify({'success': False, 'error': 'Invalid hashes'}), 404

        # Получаем данные из Google Sheets
        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        # Находим товары для этой подмодели
        if (section_name in catalog and
            category_name in catalog[section_name] and
            model_name in catalog[section_name][category_name] and
            submodel_name in catalog[section_name][category_name][model_name]):

            raw_products = catalog[section_name][category_name][model_name][submodel_name]
            
            if product_index < 0 or product_index >= len(raw_products):
                logger.error(f"❌ Индекс товара {product_index} вне диапазона (0-{len(raw_products)-1})")
                return jsonify({'success': False, 'error': 'Product index out of range'}), 404
            
            product = raw_products[product_index]
            photo_url = extract_photo_filename_from_url(product.get('photo_url'))

            processed_product = {
                'color': product['color'],
                'price': product['price'],
                'photo_url': photo_url,
                'photo_description': product.get('photo_description', ''),
                'description': product.get('description', ''),
                'row_index': product['row_index'],
                'section_name': section_name,
                'category_name': category_name,
                'model_name': model_name,
                'submodel_name': submodel_name
            }
            
            result = {
                'success': True,
                'product': processed_product
            }
            
            logger.info(f"✅ Товар найден: {product.get('color', 'Unknown')}, цена: {product.get('price', 'N/A')}")
            return jsonify(result)
        else:
            logger.error(f"❌ Товары не найдены для {section_name}/{category_name}/{model_name}/{submodel_name}")
            return jsonify({'success': False, 'error': 'Products not found'}), 404
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения товара: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== API для избранного ====================

@app.route('/api/favorites/add', methods=['POST'])
def add_favorite():
    """Добавить товар в избранное"""
    try:
        data = request.json
        logger.info(f"📥 Получен запрос на добавление в избранное: {data}")
        
        user_id = data.get('user_id')
        section_hash = data.get('section_hash')
        category_hash = data.get('category_hash')
        model_hash = data.get('model_hash')
        submodel_hash = data.get('submodel_hash')
        product_index = data.get('product_index')
        
        if not all([user_id, section_hash, category_hash, model_hash, submodel_hash, product_index is not None]):
            logger.error("❌ Отсутствуют обязательные поля")
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        # Создаём уникальный ключ
        key = f"{user_id}_{section_hash}_{category_hash}_{model_hash}_{submodel_hash}_{product_index}"
        
        # Добавляем в БД
        conn = sqlite3.connect(FAVORITES_DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO favorites (user_id, key, section_hash, category_hash, model_hash, submodel_hash, product_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, key, section_hash, category_hash, model_hash, submodel_hash, product_index))
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Товар добавлен в избранное")
        return jsonify({'success': True, 'message': 'Добавлено в избранное'})
    
    except Exception as e:
        logger.error(f"❌ Ошибка добавления в избранное: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites/remove', methods=['POST'])
def remove_favorite():
    """Удалить товар из избранного"""
    try:
        data = request.json
        user_id = data.get('user_id')
        section_hash = data.get('section_hash')
        category_hash = data.get('category_hash')
        model_hash = data.get('model_hash')
        submodel_hash = data.get('submodel_hash')
        product_index = data.get('product_index')
        
        if not all([user_id, section_hash, category_hash, model_hash, submodel_hash, product_index is not None]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        conn = sqlite3.connect(CATALOG_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM favorites 
            WHERE user_id = ? AND section_hash = ? AND category_hash = ? 
            AND model_hash = ? AND submodel_hash = ? AND product_index = ?
        ''', (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index))
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Товар удален из избранного для пользователя {user_id}")
        return jsonify({'success': True, 'message': 'Removed from favorites'})
        
    except Exception as e:
        logger.error(f"❌ Ошибка удаления из избранного: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites/list/<user_id>', methods=['GET'])
def get_favorites(user_id):
    """Получить список избранных товаров пользователя"""
    try:
        conn = sqlite3.connect(CATALOG_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT section_hash, category_hash, model_hash, submodel_hash, product_index, current_price
            FROM favorites
            WHERE user_id = ?
        ''', (user_id,))
        
        favorites = cursor.fetchall()
        conn.close()
        
        favorites_list = []
        for fav in favorites:
            favorites_list.append({
                'section_hash': fav[0],
                'category_hash': fav[1],
                'model_hash': fav[2],
                'submodel_hash': fav[3],
                'product_index': fav[4],
                'current_price': fav[5]
            })
        
        logger.info(f"✅ Получен список избранного для пользователя {user_id}: {len(favorites_list)} товаров")
        return jsonify({'success': True, 'favorites': favorites_list})
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения избранного: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites/check', methods=['POST'])
def check_favorite():
    """Проверить, находится ли товар в избранном"""
    try:
        data = request.json
        user_id = data.get('user_id')
        section_hash = data.get('section_hash')
        category_hash = data.get('category_hash')
        model_hash = data.get('model_hash')
        submodel_hash = data.get('submodel_hash')
        product_index = data.get('product_index')
        
        if not all([user_id, section_hash, category_hash, model_hash, submodel_hash, product_index is not None]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        conn = sqlite3.connect(CATALOG_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM favorites 
            WHERE user_id = ? AND section_hash = ? AND category_hash = ? 
            AND model_hash = ? AND submodel_hash = ? AND product_index = ?
        ''', (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        is_favorite = count > 0
        return jsonify({'success': True, 'is_favorite': is_favorite})
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки избранного: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    init_favorites_database()
    app.run(debug=True, port=5001, host='0.0.0.0')

                        'name': row[1],  # Название из столбца AB
                        'telegram_id': telegram_id
                    }
                    logger.info(f"✅ Бригада найдена (строка {idx+2}), Telegram ID: {telegram_id}")
                    break
            
            if not brigade_info:
                logger.warning(f"⚠️ Бригада '{brigade_name}' не найдена при отправке сообщения")
            elif not brigade_info['telegram_id']:
                logger.warning(f"⚠️ У бригады '{brigade_name}' нет Telegram ID при отправке сообщения")
            
            if brigade_info and brigade_info['telegram_id']:
                BOT_TOKEN = "7225116016:AAFBknnKHxbZwmjtODXTk-PuM3VjFbw_6LA"
                telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                
                # Формируем сообщение для бригады
                if object_name:
                    message = f"📩 *Сообщение по вашему объекту:* {object_name}\n\n"
                else:
                    message = f"📩 *Новое сообщение от клиента*\n\n"
                
                message += f"👤 *От:* {user_name}\n"
                message += f"💬 *Сообщение:*\n_{message_text}_\n\n"
                message += f"👉 Откройте приложение для продолжения общения"
                
                # Создаем кнопку для открытия конкретного чата
                chat_url = f"https://dmitrii945.github.io/miniapp/?openChat={session_id}"
                
                logger.info(f"Создан упрощенный URL для чата: {chat_url}")
                
                keyboard = {
                    'inline_keyboard': [[
                        {
                            'text': '💬 Открыть чат',
                            'web_app': {'url': chat_url}
                        }
                    ]]
                }
                
                telegram_data = {
                    'chat_id': brigade_info['telegram_id'],
                    'text': message,
                    'parse_mode': 'Markdown',
                    'reply_markup': keyboard
                }
                
                response = requests.post(telegram_url, json=telegram_data, timeout=5)
                logger.info(f"Сообщение отправлено бригаде {brigade_name}: {response.status_code}")
        
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения в Telegram: {e}")
        
        # Определяем тип отправителя: бригада или покупатель
        sender_type = 'user'  # По умолчанию - покупатель
        is_brigade = False
        
        # Проверяем, является ли отправитель бригадой
        # Проверяем ТОЛЬКО по telegram_id, без привязки к конкретному названию бригады
        try:
            brigade_data = get_brigades_data()
            for row in brigade_data:
                # Проверяем ТОЛЬКО telegram_id (столбец AG/row[6])
                if len(row) > 6 and str(row[6]) == str(user_id):
                    sender_type = 'brigade'
                    is_brigade = True
                    logger.info(f"✅ Отправитель {user_name} определен как бригада (ID: {user_id})")
                    break
        except Exception as e:
            logger.warning(f"Ошибка определения типа отправителя: {e}")
        
        # Сохраняем сообщение в БД с правильным типом
        save_chat_message(session_id, sender_type, user_name, message_text)
        
        # НОВОЕ: Если отправитель - бригада, отправляем уведомление пользователю (не бригаде)
        if is_brigade:
            try:
                # Получаем информацию о сессии чата чтобы узнать user_id покупателя
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id, user_name FROM chat_sessions WHERE session_id = ?
                ''', (session_id,))
                session_info = cursor.fetchone()
                conn.close()
                
                if session_info:
                    customer_telegram_id = session_info[0]
                    customer_name = session_info[1]
                    
                    # Отправляем уведомление покупателю
                    BOT_TOKEN = "7225116016:AAFBknnKHxbZwmjtODXTk-PuM3VjFbw_6LA"
                    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    
                    # Формируем сообщение для покупателя
                    if object_name:
                        notify_message = f"📩 *Ответ от бригады по объекту:* {object_name}\n\n"
                    else:
                        notify_message = f"📩 *Ответ от бригады {brigade_name}*\n\n"
                    
                    notify_message += f"👷 *От:* {user_name}\n"
                    notify_message += f"💬 *Сообщение:*\n_{message_text}_\n\n"
                    notify_message += f"👉 Откройте чат для ответа"
                    
                    # Создаем кнопку для открытия чата
                    chat_url = f"https://dmitrii945.github.io/miniapp/?openChat={session_id}"
                    
                    keyboard = {
                        'inline_keyboard': [[
                            {
                                'text': '💬 Открыть чат',
                                'web_app': {'url': chat_url}
                            }
                        ]]
                    }
                    
                    telegram_data = {
                        'chat_id': customer_telegram_id,
                        'text': notify_message,
                        'parse_mode': 'Markdown',
                        'reply_markup': keyboard
                    }
                    
                    response = requests.post(telegram_url, json=telegram_data, timeout=5)
                    logger.info(f"✅ Уведомление отправлено пользователю {customer_name} (ID: {customer_telegram_id}): {response.status_code}")
                else:
                    logger.warning(f"⚠️ Не найдена информация о сессии {session_id} для отправки уведомления пользователю")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления пользователю: {e}")
        
        return jsonify({
            'success': True,
            'message': 'Сообщение отправлено'
        })

    except Exception as e:
        logger.error(f"API send_message error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/history/<session_id>')
def api_get_chat_history(session_id):
    """API для получения истории чата"""
    try:
        logger.info(f"📥 Запрос истории для session_id: {session_id}")
        messages = get_chat_messages(session_id)
        logger.info(f"📥 Найдено сообщений: {len(messages)}")
        
        return jsonify({
            'success': True,
            'messages': messages
        })
    except Exception as e:
        logger.error(f"API chat_history error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/user_sessions/<user_id>')
def api_get_user_chat_sessions(user_id):
    """API для получения всех чатов пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT session_id, brigade_name, object_name, created_at,
                   (SELECT COUNT(*) FROM chat_messages WHERE chat_messages.session_id = chat_sessions.session_id) as message_count
            FROM chat_sessions
            WHERE user_id = ?
            ORDER BY created_at DESC
        ''', (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        sessions = []
        for row in rows:
            session_id, brigade_name, object_name, created_at, message_count = row
            sessions.append({
                'session_id': session_id,
                'brigade_name': brigade_name,
                'object_name': object_name,
                'created_at': created_at,
                'message_count': message_count
            })
        
        logger.info(f"✅ Найдено {len(sessions)} чатов для пользователя {user_id}")
        return jsonify({
            'success': True,
            'sessions': sessions
        })
    except Exception as e:
        logger.error(f"API user_sessions error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/brigade_sessions/<telegram_id>')
def api_get_brigade_chat_sessions(telegram_id):
    """API для получения всех чатов бригады по её Telegram ID"""
    try:
        # Получаем данные бригад чтобы найти ВСЕ названия бригад с данным telegram_id
        brigade_data = get_brigades_data()
        brigade_names = []
        
        for row in brigade_data:
            if len(row) > 6 and str(row[6]) == str(telegram_id) and len(row) > 1 and row[1]:
                brigade_names.append(row[1])
        
        if not brigade_names:
            logger.warning(f"⚠️ Не найдена бригада с Telegram ID: {telegram_id}")
            return jsonify({
                'success': True,
                'sessions': [],
                'message': 'Бригада не найдена'
            })
        
        logger.info(f"📋 Найдено бригад с ID {telegram_id}: {brigade_names}")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Ищем чаты где brigade_name соответствует ЛЮБОЙ из найденных бригад
        placeholders = ','.join('?' * len(brigade_names))
        cursor.execute(f'''
            SELECT session_id, user_id, user_name, object_name, created_at, brigade_name,
                   (SELECT COUNT(*) FROM chat_messages WHERE chat_messages.session_id = chat_sessions.session_id) as message_count
            FROM chat_sessions
            WHERE brigade_name IN ({placeholders})
            ORDER BY created_at DESC
        ''', tuple(brigade_names))
        
        rows = cursor.fetchall()
        conn.close()
        
        sessions = []
        for row in rows:
            session_id, user_id, user_name, object_name, created_at, brigade_name, message_count = row
            sessions.append({
                'session_id': session_id,
                'user_id': user_id,
                'user_name': user_name,
                'object_name': object_name,
                'created_at': created_at,
                'brigade_name': brigade_name,
                'message_count': message_count
            })
        
        logger.info(f"✅ Найдено {len(sessions)} чатов для бригад {brigade_names} (Telegram ID: {telegram_id})")
        return jsonify({
            'success': True,
            'sessions': sessions,
            'brigade_names': brigade_names
        })
    except Exception as e:
        logger.error(f"API brigade_sessions error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/session_info/<session_id>')
def api_get_chat_session_info(session_id):
    """API для получения информации о сессии чата"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT session_id, brigade_name, object_name, user_name
            FROM chat_sessions
            WHERE session_id = ?
        ''', (session_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            session_id, brigade_name, object_name, user_name = row
            return jsonify({
                'success': True,
                'session_id': session_id,
                'brigade_name': brigade_name,
                'object_name': object_name,
                'user_name': user_name
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Session not found'
            }), 404
            
    except Exception as e:
        logger.error(f"API session_info error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/delete/<session_id>', methods=['DELETE'])
def api_delete_chat(session_id):
    """API для удаления чата"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Сначала удаляем все сообщения чата
        cursor.execute('DELETE FROM chat_messages WHERE session_id = ?', (session_id,))
        messages_deleted = cursor.rowcount
        
        # Затем удаляем сессию
        cursor.execute('DELETE FROM chat_sessions WHERE session_id = ?', (session_id,))
        session_deleted = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        if session_deleted > 0:
            logger.info(f"✅ Удален чат: {session_id} ({messages_deleted} сообщений)")
            return jsonify({
                'success': True,
                'message': 'Чат удален',
                'deleted_messages': messages_deleted
            })
        else:
            logger.warning(f"⚠️ Чат не найден для удаления: {session_id}")
            return jsonify({
                'success': False,
                'error': 'Chat not found'
            }), 404
            
    except Exception as e:
        logger.error(f"API delete_chat error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/brigades')
def debug_brigades():
    """Отладочный endpoint для проверки названий бригад"""
    try:
        brigade_data = get_brigades_data()
        brigades_list = []
        
        for idx, row in enumerate(brigade_data[:50]):  # Первые 50
            group_name = row[0] if len(row) > 0 and row[0] else ""
            brigade_name = row[1] if len(row) > 1 and row[1] else ""
            telegram_id = row[6] if len(row) > 6 else None
            
            # Показываем и группы (AA) и бригады (AB)
            if group_name or brigade_name:
                brigades_list.append({
                    'row': idx + 2,  # +2 потому что 1 строка - заголовки
                    'group_name': group_name,
                    'brigade_name': brigade_name,
                    'telegram_id': telegram_id,
                    'has_telegram': bool(telegram_id and str(telegram_id).strip()),
                    'type': 'group' if group_name and not brigade_name else 'brigade'
                })
        
        return jsonify({
            'success': True,
            'total': len(brigade_data),
            'brigades': brigades_list
        })
    except Exception as e:
        logger.error(f"API debug_brigades error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/upload_photo', methods=['POST', 'OPTIONS'])
def upload_photo():
    """Загрузка фото в чат"""
    # Обрабатываем preflight запрос
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
    
    try:
        if 'photo' not in request.files:
            return jsonify({'success': False, 'error': 'Файл не найден'}), 400
        
        photo = request.files['photo']
        session_id = request.form.get('session_id')
        sender_user_id = request.form.get('user_id')  # ID отправителя
        
        logger.info(f"📥 Получен запрос на загрузку фото: session_id={session_id}, user_id={sender_user_id}, file={photo.filename}")
        
        if not photo.filename:
            return jsonify({'success': False, 'error': 'Файл не выбран'}), 400
        
        if not session_id:
            return jsonify({'success': False, 'error': 'session_id не указан'}), 400
        
        # Проверяем расширение файла
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        file_ext = photo.filename.rsplit('.', 1)[1].lower() if '.' in photo.filename else ''
        
        if file_ext not in allowed_extensions:
            return jsonify({'success': False, 'error': 'Недопустимый формат файла'}), 400
        
        # Создаем директорию для загрузок если её нет
        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'chat_photos')
        logger.info(f"📂 Путь для сохранения: {upload_dir}")
        os.makedirs(upload_dir, exist_ok=True)
        
        # Генерируем уникальное имя файла
        unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
        file_path = os.path.join(upload_dir, unique_filename)
        
        logger.info(f"💾 Сохраняем файл: {file_path}")
        
        # Сохраняем файл
        photo.save(file_path)
        
        # Проверяем что файл сохранился
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            logger.info(f"✅ Файл успешно сохранен: {file_path} ({file_size} bytes)")
        else:
            logger.error(f"❌ Файл НЕ сохранился: {file_path}")
        
        # Формируем полный URL для доступа к файлу
        # Для PythonAnywhere используем полный URL
        photo_url = f"https://dmitrii2613.pythonanywhere.com/uploads/chat_photos/{unique_filename}"
        
        logger.info(f"✅ Фото загружено: {photo_url} для сессии {session_id}")
        
        # Получаем информацию о сессии для создания сообщения
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, user_name, brigade_name, object_name FROM chat_sessions WHERE session_id = ?
            ''', (session_id,))
            session_info = cursor.fetchone()
            conn.close()
            
            if session_info:
                user_id = session_info[0]
                user_name = session_info[1]
                brigade_name = session_info[2]
                object_name = session_info[3]
                
                # Определяем тип отправителя (бригада или пользователь)
                sender_type = 'user'
                sender_name = user_name
                
                # Используем sender_user_id для проверки
                check_user_id = sender_user_id if sender_user_id else user_id
                
                # Проверяем, является ли отправитель бригадой
                try:
                    brigade_data = get_brigades_data()
                    for row in brigade_data:
                        if len(row) > 6 and str(row[6]) == str(check_user_id):
                            sender_type = 'brigade'
                            sender_name = brigade_name
                            logger.info(f"👷 Отправитель {check_user_id} определен как бригада")
                            break
                except Exception as e:
                    logger.warning(f"Ошибка определения типа отправителя: {e}")
                
                # Сохраняем сообщение с фото
                message_text = f"📷 [Фото]({photo_url})"
                save_chat_message(session_id, sender_type, sender_name, message_text)
                logger.info(f"✅ Сообщение с фото сохранено в чат")
                
        except Exception as e:
            logger.error(f"Ошибка сохранения сообщения с фото: {e}")
        
        response = jsonify({
            'success': True,
            'photo_url': photo_url,
            'filename': unique_filename
        })
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}")
        error_response = jsonify({'success': False, 'error': str(e)})
        error_response.headers['Access-Control-Allow-Origin'] = '*'
        return error_response, 500

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Отдача загруженных файлов"""
    try:
        logger.info(f"📤 Запрос файла: {filename}")
        logger.info(f"📂 UPLOAD_FOLDER: {UPLOAD_FOLDER}")
        full_path = os.path.join(UPLOAD_FOLDER, filename)
        logger.info(f"📂 Полный путь: {full_path}")
        
        if os.path.exists(full_path):
            logger.info(f"✅ Файл найден, отдаем")
            return send_from_directory(UPLOAD_FOLDER, filename)
        else:
            logger.error(f"❌ Файл НЕ найден: {full_path}")
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        logger.error(f"❌ Ошибка отдачи файла: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== API для получения товара по индексу ====================

@app.route('/api/product_by_index/<section_hash>/<category_hash>/<model_hash>/<submodel_hash>/<int:product_index>', methods=['GET'])
def get_product_by_index(section_hash, category_hash, model_hash, submodel_hash, product_index):
    """Получить конкретный товар по хэшам и индексу"""
    try:
        logger.info(f"📦 Запрос товара: {section_hash}/{category_hash}/{model_hash}/{submodel_hash}/{product_index}")
        
        # Получаем имена по хэшам
        section_name, _ = get_name_by_hash(section_hash)
        category_name, _ = get_name_by_hash(category_hash)

        if model_hash == 'null' or model_hash == 'empty':
            model_name = "Без модели"
        else:
            model_name, _ = get_name_by_hash(model_hash)

        if submodel_hash == 'null' or submodel_hash == 'empty':
            submodel_name = "Без подмодели"
        else:
            submodel_name, _ = get_name_by_hash(submodel_hash)

        if not all([section_name, category_name, model_name, submodel_name]):
            logger.error("❌ Не все параметры найдены по хэшам")
            return jsonify({'success': False, 'error': 'Invalid hashes'}), 404

        # Получаем данные из Google Sheets
        data = get_google_sheets_data('A2:J1500')
        catalog = parse_catalog_data(data)

        # Находим товары для этой подмодели
        if (section_name in catalog and
            category_name in catalog[section_name] and
            model_name in catalog[section_name][category_name] and
            submodel_name in catalog[section_name][category_name][model_name]):

            raw_products = catalog[section_name][category_name][model_name][submodel_name]
            
            if product_index < 0 or product_index >= len(raw_products):
                logger.error(f"❌ Индекс товара {product_index} вне диапазона (0-{len(raw_products)-1})")
                return jsonify({'success': False, 'error': 'Product index out of range'}), 404
            
            product = raw_products[product_index]
            photo_url = extract_photo_filename_from_url(product.get('photo_url'))

            processed_product = {
                'color': product['color'],
                'price': product['price'],
                'photo_url': photo_url,
                'photo_description': product.get('photo_description', ''),
                'description': product.get('description', ''),
                'row_index': product['row_index'],
                'section_name': section_name,
                'category_name': category_name,
                'model_name': model_name,
                'submodel_name': submodel_name
            }
            
            result = {
                'success': True,
                'product': processed_product
            }
            
            logger.info(f"✅ Товар найден: {product.get('color', 'Unknown')}, цена: {product.get('price', 'N/A')}")
            return jsonify(result)
        else:
            logger.error(f"❌ Товары не найдены для {section_name}/{category_name}/{model_name}/{submodel_name}")
            return jsonify({'success': False, 'error': 'Products not found'}), 404
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения товара: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== API для избранного ====================

@app.route('/api/favorites/add', methods=['POST'])
def add_favorite():
    """Добавить товар в избранное"""
    try:
        data = request.json
        logger.info(f"📥 Получен запрос на добавление в избранное: {data}")
        
        user_id = data.get('user_id')
        section_hash = data.get('section_hash')
        category_hash = data.get('category_hash')
        model_hash = data.get('model_hash')
        submodel_hash = data.get('submodel_hash')
        product_index = data.get('product_index')
        current_price = data.get('current_price')
        
        logger.info(f"   user_id: {user_id}")
        logger.info(f"   section_hash: {section_hash}")
        logger.info(f"   current_price: {current_price}")
        
        if not all([user_id, section_hash, category_hash, model_hash, submodel_hash, product_index is not None]):
            logger.error(f"❌ Отсутствуют обязательные поля")
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        conn = sqlite3.connect(CATALOG_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO favorites 
            (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index, current_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index, current_price))
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Товар добавлен в избранное для пользователя {user_id}")
        return jsonify({'success': True, 'message': 'Added to favorites'})
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления в избранное: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites/remove', methods=['POST'])
def remove_favorite():
    """Удалить товар из избранного"""
    try:
        data = request.json
        user_id = data.get('user_id')
        section_hash = data.get('section_hash')
        category_hash = data.get('category_hash')
        model_hash = data.get('model_hash')
        submodel_hash = data.get('submodel_hash')
        product_index = data.get('product_index')
        
        if not all([user_id, section_hash, category_hash, model_hash, submodel_hash, product_index is not None]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        conn = sqlite3.connect(CATALOG_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM favorites 
            WHERE user_id = ? AND section_hash = ? AND category_hash = ? 
            AND model_hash = ? AND submodel_hash = ? AND product_index = ?
        ''', (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index))
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Товар удален из избранного для пользователя {user_id}")
        return jsonify({'success': True, 'message': 'Removed from favorites'})
        
    except Exception as e:
        logger.error(f"❌ Ошибка удаления из избранного: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites/list/<user_id>', methods=['GET'])
def get_favorites(user_id):
    """Получить список избранных товаров пользователя"""
    try:
        conn = sqlite3.connect(CATALOG_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT section_hash, category_hash, model_hash, submodel_hash, product_index, current_price
            FROM favorites
            WHERE user_id = ?
        ''', (user_id,))
        
        favorites = cursor.fetchall()
        conn.close()
        
        favorites_list = []
        for fav in favorites:
            favorites_list.append({
                'section_hash': fav[0],
                'category_hash': fav[1],
                'model_hash': fav[2],
                'submodel_hash': fav[3],
                'product_index': fav[4],
                'current_price': fav[5]
            })
        
        logger.info(f"✅ Получен список избранного для пользователя {user_id}: {len(favorites_list)} товаров")
        return jsonify({'success': True, 'favorites': favorites_list})
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения избранного: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites/check', methods=['POST'])
def check_favorite():
    """Проверить, находится ли товар в избранном"""
    try:
        data = request.json
        user_id = data.get('user_id')
        section_hash = data.get('section_hash')
        category_hash = data.get('category_hash')
        model_hash = data.get('model_hash')
        submodel_hash = data.get('submodel_hash')
        product_index = data.get('product_index')
        
        if not all([user_id, section_hash, category_hash, model_hash, submodel_hash, product_index is not None]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        conn = sqlite3.connect(CATALOG_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM favorites 
            WHERE user_id = ? AND section_hash = ? AND category_hash = ? 
            AND model_hash = ? AND submodel_hash = ? AND product_index = ?
        ''', (user_id, section_hash, category_hash, model_hash, submodel_hash, product_index))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        is_favorite = count > 0
        return jsonify({'success': True, 'is_favorite': is_favorite})
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки избранного: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    init_favorites_database()
    app.run(debug=True, port=5001, host='0.0.0.0')