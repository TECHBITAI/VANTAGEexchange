import os
import sqlite3
import logging
import asyncio
import threading
from pathlib import Path

from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
import json

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


def load_env(path: str = '.env'):
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

load_env()

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "alive"}), 200


def run_health_server():
    port = int(os.getenv('PORT', '5000'))
    app.run(host='0.0.0.0', port=port)

BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')
DEFAULT_ADMIN_CHAT = None
GROUP_LINK = os.getenv('ADMIN_GROUP_LINK')
if GROUP_LINK:
    GROUP_LINK = GROUP_LINK.strip() or None
admin_id_raw = os.getenv('ADMIN_CHAT_ID', '').strip()

if admin_id_raw.startswith('-'):
    try:
        ADMIN_CHAT_ID = int(admin_id_raw)
    except ValueError:
        ADMIN_CHAT_ID = admin_id_raw
elif admin_id_raw.startswith('+'):
    admin_id_raw = admin_id_raw[1:]
    if admin_id_raw.isdigit():
        ADMIN_CHAT_ID = int(admin_id_raw)
    else:
        ADMIN_CHAT_ID = admin_id_raw
elif admin_id_raw and not admin_id_raw.startswith('@') and admin_id_raw.isdigit():
    ADMIN_CHAT_ID = int(admin_id_raw)
else:
    ADMIN_CHAT_ID = admin_id_raw

logging.info('Loaded ADMIN_CHAT_ID=%s (%s)', ADMIN_CHAT_ID, type(ADMIN_CHAT_ID).__name__)
DB_PATH = 'techbit_v2.db'
LOGO_FILE = Path('0a2e5644-e32e-4c1e-bee4-350a311b8c38.jfif')
RESOLVED_ADMIN_CHAT_ID = None

DEFAULT_SETTINGS = {
    'rate_buy_USD': '1.00',
    'rate_buy_YER': '535.00',
    'rate_buy_SAR': '3.81',
    'rate_sell_USD': '1.00',
    'rate_sell_YER': '530.00',
    'rate_sell_SAR': '3.75',
    'fee_buy_percent': '3.00',
    'fee_sell_percent': '3.00'
}

CHOOSE_ACTION, KYC_VERIFY = range(2)
BUY_AMOUNT, BUY_CURRENCY, BUY_NETWORK, BUY_ADDRESS, BUY_METHOD, BUY_REVIEW, BUY_PROOF = range(2, 9)
SELL_AMOUNT, SELL_CURRENCY, SELL_NETWORK, SELL_METHOD, SELL_INFO, SELL_PROOF, SELL_CONFIRM = range(9, 16)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            usdt_balance REAL DEFAULT 0.0,
            kyc_status TEXT DEFAULT 'NOT_VERIFIED'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            amount REAL,
            total_with_fee REAL,
            network TEXT,
            wallet_address TEXT,
            payment_method TEXT,
            payment_info TEXT,
            proof_file_id TEXT,
            proof_file_type TEXT,
            currency TEXT,
            total_after_conversion REAL,
            tx_hash_or_code TEXT,
            hidden INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            client_msg_id INTEGER
        )
    ''')

    def ensure_column(table, column, definition):
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        if column not in columns:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    ensure_column('transactions', 'proof_file_id', 'TEXT')
    ensure_column('transactions', 'proof_file_type', 'TEXT')
    ensure_column('transactions', 'hidden', 'INTEGER DEFAULT 0')
    ensure_column('transactions', 'currency', 'TEXT')
    ensure_column('transactions', 'total_after_conversion', 'REAL')
    ensure_column('transactions', 'tx_hash_or_code', 'TEXT')
    ensure_column('transactions', 'client_msg_id', 'INTEGER')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    for key, value in DEFAULT_SETTINGS.items():
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
        if key in ['rate_buy_SAR', 'rate_sell_SAR']:
            cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))

    conn.commit()
    conn.close()


init_db()

def get_setting(key: str, default: str = None) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return default
    return row[0]


def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()


def get_db_connection():
    return sqlite3.connect(DB_PATH)


def run_async_task(coroutine):
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coroutine)
        finally:
            new_loop.close()

    return asyncio.run(coroutine)


def fetch_transactions(status_filter=None, show_hidden=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    sql = 'SELECT tx_id, user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, hidden, status, created_at FROM transactions'
    params = []
    where_clauses = []
    if status_filter:
        where_clauses.append('status = ?')
        params.append(status_filter)
    if not show_hidden:
        where_clauses.append('hidden = 0')
    if where_clauses:
        sql += ' WHERE ' + ' AND '.join(where_clauses)
    sql += ' ORDER BY created_at DESC'
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


def fetch_transaction(tx_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT tx_id, user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, hidden, status, created_at, client_msg_id FROM transactions WHERE tx_id = ?',
        (tx_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row


def fetch_transaction_notification_data(tx_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT tx_id, user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, tx_hash_or_code, currency, total_after_conversion, client_msg_id FROM transactions WHERE tx_id = ?',
        (tx_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row


def update_transaction_status(tx_id, new_status):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET status = ? WHERE tx_id = ?', (new_status, tx_id))
    conn.commit()
    conn.close()


def update_transaction_hidden(tx_id, hidden=1):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET hidden = ? WHERE tx_id = ?', (hidden, tx_id))
    conn.commit()
    conn.close()


async def send_user_message(bot: Bot, chat_id: int, text: str, photo: str = None):
    try:
        if photo:
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
        return True
    except Exception as exc:
        logging.exception('Failed to send Telegram message to %s', chat_id)
        return False


def get_exchange_rate(action_type: str, currency: str) -> float:
    key = f'rate_{action_type.lower()}_{currency}'
    value = get_setting(key, DEFAULT_SETTINGS.get(key, '1.00'))
    try:
        return float(value)
    except ValueError:
        return float(DEFAULT_SETTINGS.get(key, '1.00'))


def escape_markdown(text: str) -> str:
    if not text:
        return ''
    return text.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('`', '\\`')


def check_kyc(user_id: int, username: str = '') -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT kyc_status FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('INSERT INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        conn.commit()
        status = 'NOT_VERIFIED'
    else:
        status = row[0]
    conn.close()
    return status


def update_kyc_status(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET kyc_status = 'VERIFIED' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_user_balance(user_id: int, username: str = '') -> float:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    cursor.execute('SELECT usdt_balance FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0.0
    conn.commit()
    conn.close()
    return balance


def update_balance(user_id: int, amount: float, username: str = ''):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    cursor.execute('UPDATE users SET usdt_balance = usdt_balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()


def add_transaction(user_id: int, tx_type: str, amount: float, total_with_fee: float = None,
                    network: str = None, wallet_address: str = None, payment_method: str = None,
                    payment_info: str = None, proof_file_id: str = None, proof_file_type: str = None,
                    currency: str = None, total_after_conversion: float = None, tx_hash_or_code: str = None,
                    hidden: int = 0, client_msg_id: int = None) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO transactions (user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, currency, total_after_conversion, tx_hash_or_code, hidden, client_msg_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (user_id, tx_type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, currency, total_after_conversion, tx_hash_or_code, hidden, client_msg_id)
    )
    tx_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return tx_id


def action_keyboard():
    keyboard = [
        [InlineKeyboardButton('🟢 شراء / إيداع USDT', callback_data='act_BUY')],
        [InlineKeyboardButton('🔴 بيع / سحب USDT', callback_data='act_SELL')]
    ]
    return InlineKeyboardMarkup(keyboard)


def normalize_telegram_target(target):
    if not target:
        return None
    if isinstance(target, int):
        return target
    value = str(target).strip()
    if not value:
        return None
    if value.startswith('https://t.me/') or value.startswith('http://t.me/'):
        value = value.split('/')[-1]
        if value.startswith('+'):
            return value
        return f'@{value}'
    return value


def get_admin_target():
    return ADMIN_CHAT_ID or GROUP_LINK


def build_admin_notification_text(tx_id, user_name, username, tx_type, amount=None, total_with_fee=None,
                                 network=None, wallet_address=None, payment_method=None,
                                 payment_info=None, tx_hash_or_code=None, currency=None, total_after_conversion=None, action='📬 طلب جديد'):
    lines = [f'{action}: طلب رقم #{tx_id}', f'👤 العميل: {user_name}']
    if username:
        lines.append(f'@{username}')
    lines.append(f'🔄 النوع: {tx_type}')
    if amount is not None:
        lines.append(f'💰 المبلغ المطلق: {amount} USDT')
    if total_with_fee is not None:
        lines.append(f'💵 الإجمالي مع الرسوم (USDT): {total_with_fee} USDT')
    if currency and total_after_conversion is not None:
        lines.append(f'🪙 المقابل الكاش المحلي: {total_after_conversion:.2f} {currency}')
    if network:
        lines.append(f'🌐 الشبكة: {network}')
    if wallet_address:
        lines.append(f'📍 العنوان: {wallet_address}')
    if payment_method:
        lines.append(f'💳 طريقة الدفع/الاستلام: {payment_method}')
    if payment_info:
        lines.append(f'📝 معلومات الحساب: {payment_info}')
    if tx_hash_or_code:
        lines.append(f'🔑 إثبات المعاملة: {tx_hash_or_code}')
    return '\n'.join(lines)


def build_admin_action_markup(tx_id, user_id, tx_type):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('✅ موافقة', callback_data=f'adm_app_{tx_type}_{tx_id}_{user_id}'),
            InlineKeyboardButton('❌ رفض', callback_data=f'adm_rej_{tx_type}_{tx_id}_{user_id}'),
        ],
        [InlineKeyboardButton('✏️ تعديل', callback_data=f'adm_edit_{tx_type}_{tx_id}_{user_id}')],
    ])


async def send_to_admin(method, *args, **kwargs):
    admin_chat_id = get_admin_target()
    if admin_chat_id is None:
        raise RuntimeError('Admin chat ID is not configured or could not be resolved')
    try:
        normalized = normalize_telegram_target(admin_chat_id)
        kwargs['chat_id'] = normalized
        return await method(*args, **kwargs)
    except Exception as exc:
        logging.exception('Failed to send admin notification to %s', admin_chat_id)
        raise


def networks_keyboard():
    keyboard = [
        [InlineKeyboardButton('🌐 BSC / BNB Smart Chain (BEP20)', callback_data='net_BEP20')],
        [InlineKeyboardButton('🌐 TRX / Tron (TRC20)', callback_data='net_TRC20')],
        [InlineKeyboardButton('🌐 ETH / Ethereum (ERC20)', callback_data='net_ERC20')],
        [InlineKeyboardButton('🌐 SOL / Solana', callback_data='net_SOL')]
    ]
    return InlineKeyboardMarkup(keyboard)


def currencies_keyboard():
    keyboard = [
        [InlineKeyboardButton('🇾🇪 ريال يمني (YER)', callback_data='cur_YER')],
        [InlineKeyboardButton('🇸🇦 ريال سعودي (SAR)', callback_data='cur_SAR')],
        [InlineKeyboardButton('🇺🇸 دولار أمريكي (USD)', callback_data='cur_USD')]
    ]
    return InlineKeyboardMarkup(keyboard)


def payment_methods_keyboard():
    keyboard = [
        [InlineKeyboardButton('🏦 الشبكة الموحدة', callback_data='pay_Mowahada')],
        [InlineKeyboardButton('🏦 كريمي', callback_data='pay_Kuraimi')],
        [InlineKeyboardButton('📱 جيب', callback_data='pay_Jeeb')],
        [InlineKeyboardButton('⭐ شبكة النجم', callback_data='pay_Najm')],
        [InlineKeyboardButton('💎 شبكة الامتياز', callback_data='pay_Emtiaz')]
    ]
    return InlineKeyboardMarkup(keyboard)


def confirmation_keyboard():
    keyboard = [
        [InlineKeyboardButton('✅ تأكيد البيانات وإرسال الطلب للمشرف', callback_data='confirm_yes')],
        [InlineKeyboardButton('❌ إلغاء هذه العملية', callback_data='confirm_no')]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data.clear()
    await update.message.reply_text(
        text=f"✨ مرحباً بك {user.first_name} في بوت التداول الرسمي لمنصة **VANTAGE EXCHANGE**.\n\n"
             f"الرجاء تحديد نوع العملية التي تريد تنفيذها الآن للبدء:",
        reply_markup=action_keyboard(),
        parse_mode="Markdown"
    )
    return CHOOSE_ACTION


async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.replace('act_', '')
    context.user_data['action_type'] = action
    user_id = query.from_user.id

    kyc_status = check_kyc(user_id, query.from_user.username or '')
    if kyc_status == 'NOT_VERIFIED':
        await query.message.reply_text(
            "🔒 **إجراء أمني إلزامي (لأول مرة فقط):**\n"
            "يرجى إرسال صورة واضحة من إثبات الهوية الخاص بك (جواز سفر أو بطاقة شخصية) لتفعيل حسابك والمتابعة المعاملة.",
            parse_mode="Markdown"
        )
        return KYC_VERIFY

    return await prompt_for_amount(query.message, action)


async def handle_kyc_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    action = context.user_data.get('action_type', 'BUY')

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document and update.message.document.mime_type.startswith('image/'):
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("⚠️ عذراً، يجب رفع صورة مستند التحقق (الهوية) بشكل صحيح للمتابعة.")
        return KYC_VERIFY

    update_kyc_status(user_id)

    caption = (
        f"🪪 **مستند تحقيق هوية جديد للتأشير**\n\n"
        f"👤 العميل: {escape_markdown(update.effective_user.first_name)} (@{escape_markdown(update.effective_user.username or 'غير معروف')})\n"
        f"🆔 الرقم التعريفي: `{user_id}`"
    )

    try:
        await send_to_admin(context.bot.send_photo,
                            photo=file_id,
                            caption=caption,
                            parse_mode='Markdown')
    except Exception as exc:
        logging.error('Failed to send KYC to admin: %s', exc)

    await update.message.reply_text("✅ تم تأكيد وحفظ مستند الهوية بنجاح، حسابك مؤكد الآن.")
    return await prompt_for_amount(update.message, action)


async def prompt_for_amount(message, action: str):
    if action == 'BUY':
        await message.reply_text('🟢 يرجى كتابة كمية USDT التي ترغب بشرائها / إيداعها:\n(أرسل أرقام فقط باللغة الإنجليزية, مثال: 150)')
        return BUY_AMOUNT

    await message.reply_text('🔴 يرجى كتابة كمية USDT التي ترغب ببيعها للشركة / سحبها كاش:\n(أرسل أرقام فقط باللغة الإنجليزية, مثال: 200)')
    return SELL_AMOUNT


async def get_buy_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.replace(',', '.'))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text('⚠️ يرجى إدخل قيمة عددية صحيحة باللغة الإنجليزية أكبر من الصفر:')
        return BUY_AMOUNT

    if amount < 33.0:
        fee = 1.0
        fee_label = "1.00 USDT ثابتة (لأن المبلغ أقل من 33 USDT)"
    else:
        fee = round(amount * 0.03, 2)
        fee_label = "3%"

    total = round(amount + fee, 2)
    context.user_data['buy_amount'] = amount
    context.user_data['buy_fee'] = fee
    context.user_data['buy_total'] = total
    context.user_data['buy_fee_label'] = fee_label

    await update.message.reply_text(
        '💵 يرجى تحديد العملة التي ترغب بالدفع من خلالها لشراء الرصيد الإلكتروني:',
        reply_markup=currencies_keyboard()
    )
    return BUY_CURRENCY


async def get_buy_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currency = query.data.replace('cur_', '')
    context.user_data['buy_currency'] = currency

    rate = get_exchange_rate('BUY', currency)
    total_usdt = context.user_data['buy_total']
    total_local = round(total_usdt * rate, 2)

    context.user_data['buy_total_after_conversion'] = total_local
    context.user_data['buy_rate'] = rate

    await query.message.reply_text(
        f"📊 **تفاصيل احتساب عملية الشراء والاستلام المحلي:**\n"
        f"🔹 الكمية المطلوبة: {context.user_data['buy_amount']} USDT\n"
        f"🔹 عمولة الخدمة: {context.user_data['buy_fee_label']}\n"
        f"➕ الإجمالي المطلق بالـ USDT: {total_usdt:.2f} USDT\n"
        f"📈 سعر صرف الشراء المعتمد: 1 USDT = {rate:.2f} {currency}\n"
        f"💵 المبلغ الإجمالي المطلوب تحويله محلياً: **{total_local:.2f} {currency}**\n\n"
        f"⚠️ **إخلاء مسؤولية:** يرجى التأكد تماماً من اختيار وعنوان الشبكة المطابق لمحفظتك الرقمية لتفادي ضياع الأصول.\n"
        f"💡 **نصيحة VANTAGE:** ننصحك باختيار شبكة **Binance Smart Chain (BEP-20)** كونه الخيار الأسرع إلكترونياً والأقل استهلاكاً للرسوم.\n\n"
        f"يرجى تحديد الشبكة التي تود استلام الـ USDT عليها:",
        reply_markup=networks_keyboard(),
        parse_mode="Markdown"
    )
    return BUY_NETWORK


async def get_buy_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['buy_network'] = query.data.replace('net_', '')
    await query.message.reply_text('📍 يرجى إرسال عنوان محفظتك الرقمية (المحفظة التي ستستقبل الـ USDT عليها):')
    return BUY_ADDRESS


async def get_buy_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['buy_address'] = update.message.text.strip()
    await update.message.reply_text('💳 يرجى اختيار جهة التحويل المحلية المناسبة لك للتحويل للشركة عبرها:', reply_markup=payment_methods_keyboard())
    return BUY_METHOD


async def get_buy_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method_key = query.data.replace('pay_', '')
    context.user_data['buy_method'] = method_key

    methods_text = {
        'Mowahada': 'تحويل باسم خالد وليد عبدالله المسني  781948562 ',
        'Kuraimi': 'حساب كريمي رقم: 2325013 باسم (شركة VANTAGE EXCHANGE)',
        'Jeeb': 'حساب محفظة جيب رقم: 739942424 باسم (شركة VANTAGE EXCHANGE)',
        'Najm': 'تحويل باسم خالد وليد عبدالله المسني  781948562',
        'Emtiaz': 'تحويل باسم خالد وليد عبدالله المسني  781948562'
    }
    context.user_data['buy_payment_info_details'] = methods_text.get(method_key, '')

    amount = context.user_data['buy_amount']
    total = context.user_data['buy_total']
    currency = context.user_data['buy_currency']
    total_local = context.user_data['buy_total_after_conversion']
    network = context.user_data['buy_network']
    address = context.user_data['buy_address']
    
    method_display_name = "الشبكة الموحدة" if method_key == 'Mowahada' else method_key
    payment_info = context.user_data['buy_payment_info_details']

    summary = (
        '📊 **مراجعة وتأكيد بيانات عملية الشراء والاستلام المحلي:**\n\n'
        f'🔹 صافي الكمية المطلوبة: {amount} USDT\n'
        f'➕ عمولة الخدمة المحتسبة: {context.user_data["buy_fee_label"]}\n'
        f'🔹 الإجمالي شامل عمولة الخدمة: {total:.2f} USDT\n'
        f'📈 سعر الصرف المحتسب: 1 USDT = {context.user_data["buy_rate"]:.2f} {currency}\n'
        f'💵 المبلغ الكلي المطلوب تحويله بالعملة المحلية: **{total_local:.2f} {currency}**\n'
        '⚠️ *تنبيه هام:* يضاف على الإجمالي عمولة تحويل شبكة البلوكشين (Gas Fees)، ويتحملها العميل بالكامل عن التنفيذ الفعلي.\n'
        f'🌐 الشبكة المختارة: {network}\n'
        f'📍 محفظتك الرقمية للإيداع: `{escape_markdown(address)}`\n'
        f'💳 جهة التحويل المحلية: {method_display_name}\n'
        f'📌 تفاصيل حساب الشركة للتحويل: {payment_info}\n\n'
        'هل كافة البيانات أعلاه صحيحة ومؤكدة لبدء التحويل؟'
    )
    await query.message.reply_text(summary, reply_markup=confirmation_keyboard(), parse_mode='Markdown')
    return BUY_REVIEW


async def process_buy_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'confirm_no':
        await query.message.reply_text('❌ تم إلغاء العملية بنجاح. يمكنك البدء من جديد عبر الأمر /start')
        return ConversationHandler.END

    await query.edit_message_text(
        text=(query.message.text + "\n\n🔄 **[وضع العميل: تم تأكيد البيانات وإرسالها]**"),
        reply_markup=None,
        parse_mode='Markdown'
    )

    temp_msg = await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f'📥 يرجى الآن تحويل القيمة المحلية المقابلة وهي (**{context.user_data["buy_total_after_conversion"]:.2f} {context.user_data["buy_currency"]}**) إلى الحساب التالي التابع للمنصة:\n\n'
             f'`{context.user_data["buy_payment_info_details"]}`\n\n'
             '📸 بعد التحويل الناجح، يرجى **رفع صورة سند التحويل أو لقطة الشاشة** هنا مباشرة لتأكيد المعاملة يدوياً وتصديرها للمشرفين.'
    )
    context.user_data['buy_client_msg_id'] = temp_msg.message_id
    return BUY_PROOF


async def get_buy_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text('⚠️ يرجى إرسال صورة واضحة إثبات تحويل صالح.')
        return BUY_PROOF

    user = update.effective_user
    photo = update.message.photo[-1]
    amount = context.user_data['buy_amount']
    total = context.user_data['buy_total']
    currency = context.user_data['buy_currency']
    total_local = context.user_data['buy_total_after_conversion']
    network = context.user_data['buy_network']
    address = context.user_data['buy_address']
    method = context.user_data['buy_method']
    payment_info = context.user_data['buy_payment_info_details']
    client_msg_id = context.user_data.get('buy_client_msg_id')

    tx_id = add_transaction(
        user.id,
        'BUY',
        amount,
        total_with_fee=total,
        network=network,
        wallet_address=address,
        payment_method=method,
        payment_info=payment_info,
        proof_file_id=photo.file_id,
        proof_file_type='photo',
        currency=currency,
        total_after_conversion=total_local,
        client_msg_id=client_msg_id
    )

    admin_markup = build_admin_action_markup(tx_id=tx_id, user_id=user.id, tx_type='BUY')

    try:
        admin_text = build_admin_notification_text(
            tx_id=tx_id,
            user_name=user.first_name,
            username=user.username,
            tx_type='BUY',
            amount=amount,
            total_with_fee=total,
            network=network,
            wallet_address=address,
            payment_method=method,
            payment_info=payment_info,
            currency=currency,
            total_after_conversion=total_local,
            action='🚨 طلب شراء/إيداع USDT جديد',
        )
        await send_to_admin(context.bot.send_photo,
                            photo=photo.file_id,
                            caption=admin_text,
                            reply_markup=admin_markup,
                            parse_mode='Markdown')
    except Exception as exc:
        logging.error('Failed to send BUY proof to admin: %s', exc)

    await update.message.reply_text('✅ تم تصدير سند التحويل للمشرف بنجاح لمطابقتها فورياً. سيتم إرسال العملة الرقمية وإشعارك فور تفعيل الإيداع.')
    return ConversationHandler.END


async def get_sell_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.replace(',', '.'))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text('⚠️ يرجى إدخل قيمة عددية صحيحة باللغة الإنجليزية:')
        return SELL_AMOUNT

    if amount < 33.0:
        fee = 1.0
        fee_label = "1.00 USDT ثابتة (لأن المبلغ أقل من 33 USDT)"
        net_amount_usdt = round(amount - fee, 2)
    else:
        fee_label = "3%"
        net_amount_usdt = round(amount * 0.97, 2)

    context.user_data['amount'] = amount
    context.user_data['sell_fee_label'] = fee_label
    context.user_data['net_amount_usdt'] = net_amount_usdt

    await update.message.reply_text(
        '💵 يرجى تحديد عملة استلام المقابل الكاش المحلي لمبلغ البيع الخاص بك:',
        reply_markup=currencies_keyboard()
    )
    return SELL_CURRENCY


async def get_sell_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currency = query.data.replace('cur_', '')
    context.user_data['currency'] = currency

    rate = get_exchange_rate('SELL', currency)
    net_usdt = context.user_data['net_amount_usdt']
    total_local = round(net_usdt * rate, 2)

    context.user_data['total_after_conversion'] = total_local
    context.user_data['rate'] = rate

    await query.message.reply_text(
        f'📊 **تفاصيل احتساب عملية البيع والاستلام المحلي:**\n'
        f'🔹 الكمية المدخلة: {context.user_data["amount"]} USDT\n'
        f'🔹 الصافي بعد خصم عمولة الشركة ({context.user_data["sell_fee_label"]}): {net_usdt:.2f} USDT\n'
        f'📈 سعر الصرف الحالي للبيع: 1 USDT = {rate:.2f} {currency}\n'
        f'💵 صافي المبلغ الذي ستستلمه بالكاش المحلي: **{total_local:.2f} {currency}**\n\n'
        f'⚠️ **إخلاء مسؤولية:** يرجى التأكد تماماً من توافق عنوان الشبكة وعناوين الإرسال.\n'
        f'💡 **نصيحة VANTAGE:** شبكة **BSC (BEP-20)** هي الأرخص رسوماً والأسرع في قراءة العمليات.\n\n'
        'الآن، يرجى تحديد نوع شبكة تحويل USDT التي ستقوم بالإرسال عبرها إلى الشركة:',
        reply_markup=networks_keyboard(),
        parse_mode='Markdown'
    )
    return SELL_NETWORK


async def get_sell_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    network = query.data.replace('net_', '')
    context.user_data['network'] = network

    COMPANY_WALLETS = {
        'BEP20': '0x71C7656EC7ab88b098defB751B7401B5f6d1476B',
        'TRC20': 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
        'ERC20': '0x71C7656EC7ab88b098defB751B7401B5f6d1476B',
        'SOL': 'HwAMgRymW7CE8vV8GvV1dfM8d9JkS4f6bY9B6fL9D'
    }
    context.user_data['company_wallet'] = COMPANY_WALLETS.get(network, 'تواصل مع الإدارة للعنوان')

    await query.message.reply_text('💳 يرجى اختيار جهة تحويل الكاش المحلي التي ترغب باستلام الأموال عليها من شركتنا:', reply_markup=payment_methods_keyboard())
    return SELL_METHOD


async def get_sell_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['payment_method'] = query.data.replace('pay_', '')

    await query.message.reply_text(
        '📝 يرجى كتابة تفاصيل حسابك المستلم بالكامل:\n'
        '(مثال: الاسم الثلاثي، اسم الشبكة المحلية، رقم الحساب أو المحفظة، ورقم هاتف المستلم بدقة بالغة):'
    )
    return SELL_INFO


async def get_sell_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['payment_info'] = update.message.text

    await update.message.reply_text(
        f'⚠️ **الخطوة المتبقية للإيداع:**\n'
        f'يرجى تحويل كمية **{context.user_data["amount"]} USDT** الآن لعنوان محفظة الشركة التالي على شبكة **({context.user_data["network"]})**:\n\n'
        f'`{context.user_data["company_wallet"]}`\n\n'
        '📣 *تنبيه:* يرجى العلم بأن جميع رسوم شبكة البلوكشين (Gas Fees) تقع على عاتقك كعميل بالكامل.\n\n'
        '✍️ بعد الانتهاء من الإرسال بنجاح، يرجى كتابة **رمز المعاملة الرقمي (Hash / TxID) أو إرسال صورة سند تأكيد التحويل** من محفظتك هنا فوراً لتأكيد المعاملة يدوياً:'
    )
    return SELL_PROOF


async def get_sell_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data['tx_hash_or_code'] = 'سند بصري (مرفق أدناه)'
        context.user_data['proof_photo_id'] = update.message.photo[-1].file_id
    else:
        context.user_data['tx_hash_or_code'] = update.message.text
        context.user_data['proof_photo_id'] = None

    method_key = context.user_data["payment_method"]
    method_display_name = "الشبكة الموحدة" if method_key == 'Mowahada' else method_key

    summary = (
        '📊 **مراجعة وتأكيد بيانات عملية البيع/السحب:**\n\n'
        f'🔹 كمية العملة الرقمية المرسلة: {context.user_data["amount"]} USDT\n'
        f'🔹 الشبكة المستخدمة: {context.user_data["network"]}\n'
        f'💳 جهة استلام الكاش المحلي: {method_display_name}\n'
        f'📌 بيانات حسابك للاستلام: {context.user_data["payment_info"]}\n'
        f'💵 إجمالي المبلغ الصافي للاستلام الكاش: **{context.user_data["total_after_conversion"]:.2f} {context.user_data["currency"]}**\n'
        f'🔑 إثبات المعاملة المكتوب: `{escape_markdown(context.user_data["tx_hash_or_code"] or "-" )}`\n\n'
        'هل كافة البيانات صحيحة وتؤكد إرسال المعاملة للمراجعة؟'
    )

    await update.message.reply_text(summary, reply_markup=confirmation_keyboard(), parse_mode='Markdown')
    return SELL_CONFIRM


async def process_sell_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == 'confirm_no':
        await query.message.reply_text('❌ تم إلغاء المعاملة والعودة للقائمة الرئيسية عبر /start')
        return ConversationHandler.END

    await query.edit_message_text(
        text=(query.message.text + "\n\n🔄 **[وضع العميل: تم تأكيد البيانات وإرسالها]**"),
        reply_markup=None,
        parse_mode='Markdown'
    )

    temp_msg = await context.bot.send_message(
        chat_id=user.id,
        text='✅ تم إرسال وتأكيد بيانات البيع/السحب بنجاح إلى المشرف. طلبك الآن قيد التنفيذ، وسيصلك إشعار عند تأكيد إتمام العملية بنجاح مع صورة العملية.'
    )

    tx_id = add_transaction(
        user.id,
        'SELL',
        context.user_data['amount'],
        total_with_fee=None,
        network=context.user_data.get('network'),
        wallet_address=context.user_data.get('company_wallet'),
        payment_method=context.user_data.get('payment_method'),
        payment_info=context.user_data.get('payment_info'),
        currency=context.user_data.get('currency'),
        total_after_conversion=context.user_data.get('total_after_conversion'),
        tx_hash_or_code=context.user_data.get('tx_hash_or_code'),
        proof_file_id=context.user_data.get('proof_photo_id'),
        proof_file_type='photo' if context.user_data.get('proof_photo_id') else None,
        client_msg_id=temp_msg.message_id
    )

    admin_markup = build_admin_action_markup(tx_id=tx_id, user_id=user.id, tx_type='SELL')

    caption_text = build_admin_notification_text(
        tx_id=tx_id,
        user_name=user.first_name,
        username=user.username,
        tx_type='SELL',
        amount=context.user_data['amount'],
        total_with_fee=None,
        network=context.user_data['network'],
        wallet_address=context.user_data.get('company_wallet'),
        payment_method=context.user_data['payment_method'],
        payment_info=context.user_data['payment_info'],
        tx_hash_or_code=context.user_data['tx_hash_or_code'],
        currency=context.user_data.get('currency'),
        total_after_conversion=context.user_data.get('total_after_conversion'),
        action='🚨 طلب بيع/سحب USDT جديد',
    )

    if context.user_data.get('proof_photo_id'):
        await send_to_admin(context.bot.send_photo,
                            photo=context.user_data['proof_photo_id'],
                            caption=caption_text,
                            reply_markup=admin_markup,
                            parse_mode='Markdown')
    else:
        await send_to_admin(context.bot.send_message,
                            text=caption_text,
                            reply_markup=admin_markup,
                            parse_mode='Markdown')

    return ConversationHandler.END


async def admin_decision_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split('_')
    action = data_parts[1]
    tx_type = data_parts[2]
    tx_id = int(data_parts[3])
    client_id = int(data_parts[4])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if action == 'app':
        cursor.execute("UPDATE transactions SET status = 'COMPLETED' WHERE tx_id = ?", (tx_id,))
        conn.commit()
        tx_row = fetch_transaction_notification_data(tx_id)
        if tx_row:
            _, tx_user_id, tx_kind, tx_amount, tx_total_with_fee, tx_network, tx_wallet_address, tx_payment_method, tx_payment_info, tx_hash_or_code, tx_currency, tx_total_local, client_msg_id = tx_row
            try:
                await send_to_admin(
                    context.bot.send_message,
                    text=build_admin_notification_text(
                        tx_id=tx_id,
                        user_name=client_id,
                        username=None,
                        tx_type=tx_kind,
                        amount=tx_amount,
                        total_with_fee=tx_total_with_fee,
                        network=tx_network,
                        wallet_address=tx_wallet_address,
                        payment_method=tx_payment_method,
                        payment_info=tx_payment_info,
                        tx_hash_or_code=tx_hash_or_code,
                        currency=tx_currency,
                        total_after_conversion=tx_total_local,
                        action='✅ موافقة على الطلب',
                    )
                )
            except Exception as exc:
                logging.exception('Failed to notify admin about approval: %s', exc)

        if tx_type == 'BUY' and tx_row:
            amount = float(tx_row[3] or 0.0)
            wallet = tx_row[6] or ''
            
            if client_msg_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=client_id,
                        message_id=client_msg_id,
                        text=(
                            f'✅ تمت الموافقة من قبل الإدارة على طلب الشراء.\n'
                            f'🔹 نوع الطلب: شراء وإيداع USDT\n'
                            f'🔹 المبلغ: *{amount:.2f} USDT*\n\n'
                            'جاري إيداع الرصيد من قبل الإدارة. سيتم إشعارك فور اكتمال الإيداع.'
                        ),
                        parse_mode='Markdown'
                    )
                except Exception:
                    logging.exception('Failed to update client message automatically for BUY approval')

            try:
                admin_confirm_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton('✅ تمت عملية الإيداع بنجاح', callback_data=f'user_deposit_done_{tx_id}_{client_id}')]
                ])
                await send_to_admin(
                    context.bot.send_message,
                    text=(
                        f'✅ يرجى تأكيد إيداع رصيد طلب الشراء رقم #{tx_id} للمستخدم `{client_id}`.\n'
                        f'🔹 المبلغ: *{amount:.2f} USDT*\n'
                        f'📌 المحفظة/الحساب: `{wallet or tx_row[8] or "-"}`\n\n'
                        'اضغط الزر عند اكتمال الإيداع:'
                    ),
                    parse_mode='Markdown',
                    reply_markup=admin_confirm_markup
                )
            except Exception:
                logging.exception('Failed to send deposit-confirm button to admin')
                
        elif tx_type == 'SELL' and tx_row:
            amount = float(tx_row[3] or 0.0)
            total_local = float(tx_row[11] or 0.0)
            currency = tx_row[10] or ''
            payment_method = "الشبكة الموحدة" if tx_row[7] == 'Mowahada' else tx_row[7]
            
            if client_msg_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=client_id,
                        message_id=client_msg_id,
                        text=(
                            f'✅ تمت الموافقة من قبل الإدارة على طلب البيع والتصفية.\n'
                            f'🔹 كمية البيع: *{amount:.2f} USDT*\n'
                            f'💵 صافي الكاش المستحق: *{total_local:.2f} {currency}*\n\n'
                            f'جاري الآن تحويل المبلغ المالي إلى حسابك المحلي عبر ({payment_method}). سيتم إشعارك فور تحويل الكاش بنجاح.'
                        ),
                        parse_mode='Markdown'
                    )
                except Exception:
                    logging.exception('Failed to update client message automatically for SELL approval')

            try:
                admin_cash_confirm_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton('✅ تمت عملية تحويل الكاش بنجاح', callback_data=f'user_cash_done_{tx_id}_{client_id}')]
                ])
                await send_to_admin(
                    context.bot.send_message,
                    text=(
                        f'💵 يرجى تأكيد تحويل الكاش المحلي لطلب البيع رقم #{tx_id} للمستخدم `{client_id}`.\n'
                        f'🔹 المبلغ الكاش المطلق: *{total_local:.2f} {currency}*\n'
                        f'💳 طريقة الاستلام: {payment_method}\n'
                        f'📌 بيانات الحساب المستلم للعميل:\n`{tx_row[8]}`\n\n'
                        'اضغط الزر أدناه بعد إرسال الحوالة الكاش للعميل وقفل المعاملة:'
                    ),
                    parse_mode='Markdown',
                    reply_markup=admin_cash_confirm_markup
                )
            except Exception:
                logging.exception('Failed to send cash-confirm button to admin')

        status_text = '\n\n✅ [تمت الموافقة وتم تحديث حالة المعاملة]'
    else:
        cursor.execute("UPDATE transactions SET status = 'REJECTED' WHERE tx_id = ?", (tx_id,))
        conn.commit()
        tx_row = fetch_transaction_notification_data(tx_id)
        if tx_row:
            _, tx_user_id, tx_kind, tx_amount, tx_total_with_fee, tx_network, tx_wallet_address, tx_payment_method, tx_payment_info, tx_hash_or_code, tx_currency, tx_total_local, client_msg_id = tx_row
            try:
                await send_to_admin(
                    context.bot.send_message,
                    text=build_admin_notification_text(
                        tx_id=tx_id,
                        user_name=client_id,
                        username=None,
                        tx_type=tx_kind,
                        amount=tx_amount,
                        total_with_fee=tx_total_with_fee,
                        network=tx_network,
                        wallet_address=tx_wallet_address,
                        payment_method=tx_payment_method,
                        payment_info=tx_payment_info,
                        tx_hash_or_code=tx_hash_or_code,
                        currency=tx_currency,
                        total_after_conversion=tx_total_local,
                        action='❌ رفض الطلب',
                    )
                )
            except Exception as exc:
                logging.exception('Failed to notify admin about rejection: %s', exc)
        
        if client_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=client_id,
                    message_id=client_msg_id,
                    text='❌ إشعار مالي: نأسف لإبلاغك بأن طلبك قد تم رفضه أو إلغاؤه من قبل الإدارة لعدم تطابق البيانات أو عدم استلام العملة/الحوالة. يرجى مراجعة الدعم.'
                )
            except Exception:
                pass
        else:
            await context.bot.send_message(
                chat_id=client_id,
                text='❌  @TECHBITTrading ثإشعار مالي: نأسف لإبلاغك بأن طلبك قد تم رفضه أو إلغاؤه من قبل الإدارة لعدم تطابق البيانات أو عدم استلام العملة/الحوالة. يرجى مراجعة الدعم.'
            )
        status_text = '\n\n❌ [تم رفض المعاملة وإلغاؤها من قبل المشرف]'

    try:
        if query.message.caption is not None:
            await query.edit_message_caption(caption=(query.message.caption or '') + status_text)
        else:
            await query.edit_message_text(text=(query.message.text or '') + status_text)
    except Exception:
        logging.exception('Failed to update admin message status')

    conn.close()


async def user_deposit_done_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    if len(parts) < 4:
        await query.message.reply_text('خطأ: بيانات غير كاملة.')
        return
    tx_id = int(parts[3])
    client_id = int(parts[4]) if len(parts) > 4 else query.from_user.id

    tx_row = fetch_transaction_notification_data(tx_id)
    if not tx_row:
        await query.message.reply_text('لم أتمكن من العثور على بيانات المعاملة.')
        return

    _, tx_user_id, tx_kind, tx_amount, tx_total_with_fee, tx_network, tx_wallet_address, tx_payment_method, tx_payment_info, tx_hash_or_code, tx_currency, tx_total_local, client_msg_id = tx_row
    method_display_name = "الشبكة الموحدة" if tx_payment_method == 'Mowahada' else tx_payment_method

    try:
        update_balance(client_id, float(tx_amount or 0.0))
    except Exception:
        logging.exception('Failed to update balance on user deposit confirmation')

    try:
        await query.edit_message_text(text=(query.message.text or '') + '\n\n💚 [تم شحن وإيداع الرصيد للعميل بنجاح]')
    except Exception:
        pass

    if client_msg_id:
        try:
            final_invoice_text = (
                f'📊 **ملخص عملية الشراء والإيداع المكتملة:**\n\n'
                f'🔹 كمية العملة الرقمية المستلمة: {tx_amount} USDT\n'
                f'🌐 الشبكة المستخدمة: {tx_network}\n'
                f'📍 عنوان محفظتك للإيداع: `{escape_markdown(tx_wallet_address or "-")}`\n'
                f'💳 جهة التحويل المحلية: {method_display_name}\n'
                f'💵 إجمالي القيمة المحلية المحولة: **{tx_total_local:.2f} {tx_currency}**\n\n'
                f'**✅ تم إتمام طلبك بنجاح**'
            )
            await context.bot.edit_message_text(
                chat_id=client_id,
                message_id=client_msg_id,
                text=final_invoice_text,
                parse_mode='Markdown'
            )
        except Exception:
            logging.exception('Failed to rewrite client session message to final BUY invoice state')

    await send_final_promo_and_rating(context.bot, client_id, tx_id, f'✅ تم إضافة *{float(tx_amount or 0.0):.2f} USDT* إلى رصيد محفظتك الرقمية بنجاح.')


async def user_cash_done_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    if len(parts) < 4:
        await query.message.reply_text('خطأ: بيانات غير كاملة.')
        return
    tx_id = int(parts[3])
    client_id = int(parts[4])

    tx_row = fetch_transaction_notification_data(tx_id)
    if not tx_row:
        await query.message.reply_text('لم أتمكن من العثور على بيانات المعاملة.')
        return

    _, _, _, tx_amount, _, tx_network, _, tx_payment_method, tx_payment_info, tx_hash_or_code, tx_currency, tx_total_local, client_msg_id = tx_row
    method_display_name = "الشبكة الموحدة" if tx_payment_method == 'Mowahada' else tx_payment_method

    try:
        await query.edit_message_text(text=(query.message.text or '') + '\n\n💵 [تم إرسال الكاش وإغلاق المعاملة بالكامل]')
    except Exception:
        logging.exception('Failed to update admin message text on cash done')

    if client_msg_id:
        try:
            final_invoice_text = (
                f'📊 **ملخص عملية البيع والسحب المكتملة:**\n\n'
                f'🔹 كمية العملة الرقمية المرسلة: {tx_amount} USDT\n'
                f'🔹 الشبكة المستخدمة: {tx_network}\n'
                f'💳 جهة استلام الكاش المحلي: {method_display_name}\n'
                f'📌 بيانات حسابك للاستلام: {tx_payment_info}\n'
                f'💵 إجمالي المبلغ الصافي المستلم: **{tx_total_local:.2f} {tx_currency}**\n'
                f'🔑 إثبات المعاملة المكتوب: `{escape_markdown(tx_hash_or_code or "-")}`\n\n'
                f'**✅ تم إتمام طلبك بنجاح**'
            )
            await context.bot.edit_message_text(
                chat_id=client_id,
                message_id=client_msg_id,
                text=final_invoice_text,
                parse_mode='Markdown'
            )
        except Exception:
            logging.exception('Failed to rewrite client session message to final invoice state')

    await send_final_promo_and_rating(
        context.bot, 
        client_id, 
        tx_id, 
        f'💵 إشعار استلام كاش: تم إرسال وتسليم مبلغ الحوالة المقدر بـ **{float(tx_total_local or 0.0):.2f} {tx_currency}** إلى حسابك عبر ({method_display_name}) بنجاح وبشكل نهائي.'
    )


async def send_final_promo_and_rating(bot: Bot, client_id: int, tx_id: int, status_message: str):
    promo_text = (
        f'{status_message}\n\n'
        'شكرًا لاختياركم شركتنا.\n\n'
        '📈 **قم بتجربة خدمات TECH BIT TRADING:**\n'
        '🔹 مجتمع TECHBIT النقاشي المفتوح.\n'
        '🔹 قناة TECHBIT VIP الحصرية الخاصة بالتوصيات المتقدمة.\n'
        '🔹 مكتبة TECH BIT لتعليم واحتراف مهارات التداول السلوكي.\n'
        '🔹 دورات أكاديمية كيان لتعلم واحتراف التداول.\n'
        '🔹 بوتات TECH BIT البرمجية للتداول التلقائي والاحترافي.\n\n'
        'يرجى تقييم جودة الخدمة أدناه لقفل الطلب وبدء عملية جديدة:'
    )

    academy_url = 'https://t.me/TECHBITTrading?text=مرحباً%20TECH%20BIT،%20أود%20الاستفسار%20والتسجيل%20في%20دورات%20أكاديمية%20كيان%20لتعلم%20واحتراف%20التداول.'

    rating_keyboard = [
        [
            InlineKeyboardButton('⭐️', callback_data=f'rate_{tx_id}_1'),
            InlineKeyboardButton('⭐️⭐️', callback_data=f'rate_{tx_id}_2'),
            InlineKeyboardButton('⭐️⭐️⭐️', callback_data=f'rate_{tx_id}_3')
        ],
        [
            InlineKeyboardButton('⭐️⭐️⭐️⭐️', callback_data=f'rate_{tx_id}_4'),
            InlineKeyboardButton('⭐️⭐️⭐️⭐️⭐️', callback_data=f'rate_{tx_id}_5')
        ],
        [InlineKeyboardButton('👥 مجتمع TECHBIT', url='https://t.me/TechBitCommunity')],
        [InlineKeyboardButton('👑 قناة TECHBIT VIP "توصيات"', url='https://t.me/TECHBITTrading?text=أريد%20الإنضمام%20إلى%20قناة%20توصيات%20TECHBIT%20VIP')],
        [InlineKeyboardButton('📚 مكتبة TECH BIT "كتب تداول"', url='https://t.me/TECHBITTrading?text=أريد%20الإنضمام%20إلى%20مكتبة%20TECH%20BIT%20لتعلم%20التداول')],
        [InlineKeyboardButton('🎓 دورات أكاديمية كيان التعليمية', url=academy_url)],
        [InlineKeyboardButton('🤖 بوتات TECH BIT "تداول تلقائي"', url='https://t.me/TECHBITTrading?text=أريد%20الإنضمام%20وتجربة%20بوتات%20TECH%20BIT%20للتداول%20التلقائي')]
    ]

    try:
        await bot.send_message(
            chat_id=client_id,
            text=promo_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(rating_keyboard)
        )
    except Exception:
        logging.exception('Failed to send promo and rating screen')


async def user_rating_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        await query.edit_message_text(
            text='✨ شكراً لتقييمك، وللبدء بمعاملة أخرى اضغط على /start',
            reply_markup=None,
            parse_mode='Markdown'
        )
    except Exception:
        logging.exception('Failed to acknowledge user rating')
        
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('❌ تم إلغاء المعاملة الجارية والعودة من البداية. يمكنك تشغيل البوت مجدداً عبر الأمر /start')
    return ConversationHandler.END


def ensure_settings_table():
    init_db()


def start_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    global RESOLVED_ADMIN_CHAT_ID

    if isinstance(ADMIN_CHAT_ID, int):
        RESOLVED_ADMIN_CHAT_ID = ADMIN_CHAT_ID
    else:
        RESOLVED_ADMIN_CHAT_ID = ADMIN_CHAT_ID
        logging.warning('ADMIN_CHAT_ID is not an integer: %s', ADMIN_CHAT_ID)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), CallbackQueryHandler(start, pattern='^(start_buy|start_sell)$')],
        states={
            CHOOSE_ACTION: [CallbackQueryHandler(handle_action, pattern='^act_')],
            KYC_VERIFY: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, handle_kyc_photo)],
            BUY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_amount)],
            BUY_CURRENCY: [CallbackQueryHandler(get_buy_currency, pattern='^cur_')],
            BUY_NETWORK: [CallbackQueryHandler(get_buy_network, pattern='^net_')],
            BUY_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_address)],
            BUY_METHOD: [CallbackQueryHandler(get_buy_method, pattern='^pay_')],
            BUY_REVIEW: [CallbackQueryHandler(process_buy_confirmation, pattern='^confirm_')],
            BUY_PROOF: [MessageHandler(filters.PHOTO & ~filters.COMMAND, get_buy_proof)],
            SELL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sell_amount)],
            SELL_CURRENCY: [CallbackQueryHandler(get_sell_currency, pattern='^cur_')],
            SELL_NETWORK: [CallbackQueryHandler(get_sell_network, pattern='^net_')],
            SELL_METHOD: [CallbackQueryHandler(get_sell_method, pattern='^pay_')],
            SELL_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sell_info)],
            SELL_PROOF: [MessageHandler((filters.PHOTO | (filters.TEXT & ~filters.COMMAND)), get_sell_proof)],
            SELL_CONFIRM: [CallbackQueryHandler(process_sell_confirmation, pattern='^confirm_')]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(admin_decision_handler, pattern='^adm_'))
    application.add_handler(CallbackQueryHandler(user_deposit_done_handler, pattern='^user_deposit_done_'))
    application.add_handler(CallbackQueryHandler(user_cash_done_handler, pattern='^user_cash_done_'))
    application.add_handler(CallbackQueryHandler(user_rating_handler, pattern='^rate_'))
    
    async def forward_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            msg = update.message
            if not msg:
                return
            
            user_name = msg.from_user.first_name if msg.from_user else "عميل"
            username = msg.from_user.username or "لا يوجد"
            user_id = msg.from_user.id if msg.from_user else "غير معروف"
            text = msg.text or msg.caption or "[رسالة وسائط]"
            
            admin_text = (
                f"📩 **رسالة واردة جديدة من عميل:**\n\n"
                f"👤 الاسم: {user_name}\n"
                f"🆔 المعرف: `{user_id}`\n"
                f"🔗 اليوزر: @{username}\n\n"
                f"💬 النص: {text}"
            )
            
            await send_to_admin(context.bot.send_message, text=admin_text, parse_mode='Markdown')
        except Exception:
            logging.exception('forward_incoming failed')

    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, forward_incoming))
    application.run_polling()


bot_thread_started = False


def start_bot_background():
    global bot_thread_started
    if bot_thread_started:
        return
    bot_thread_started = True
    threading.Thread(target=start_bot, daemon=True).start()


def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    start_bot()


if __name__ == '__main__':
    main()
