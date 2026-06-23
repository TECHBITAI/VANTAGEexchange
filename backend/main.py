from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import sqlite3
from typing import List, Optional
import asyncio
import os
from uuid import uuid4
from jose import jwt, JWTError
import hashlib, binascii
from datetime import datetime, timedelta


# Simple in-memory token store for admin auth (demo only)
SECRET_KEY = os.environ.get('JWT_SECRET', uuid4().hex)
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# Simple in-memory token store kept for compatibility (not used for JWT)
TOKENS = {}


def fire_and_forget(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(coro)
        except RuntimeError:
            return None
        return None

    if loop.is_running():
        return loop.create_task(coro)
    return loop.run_until_complete(coro)


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast_json(self, message):
        for conn in list(self.active_connections):
            try:
                await conn.send_json(message)
            except Exception:
                self.disconnect(conn)


manager = ConnectionManager()
from pathlib import Path

# Resolve paths relative to this file, so the app works regardless of CWD
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / 'techbit_v2.db'
PROOF_DIR = ROOT / 'proof_files'
PROOF_DIR.mkdir(exist_ok=True)

# Load .env if present (simple loader)
env_path = ROOT / '.env'
if env_path.exists():
    try:
        with env_path.open('r', encoding='utf8') as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith('#') or '=' not in ln:
                    continue
                k, v = ln.split('=', 1)
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

# Optional Telegram bot token (used to notify users from backend when admin completes)
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = None
ADMIN_GROUP_LINK = os.environ.get('ADMIN_GROUP_LINK', 'https://t.me/+JWJo1MIGc082YzU0')


app = FastAPI(title='VANTAGE Exchange Admin API')

# Ensure admin_users table exists so /api/login works even if scripts weren't run
def ensure_admin_table():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            telegram_username TEXT,
            telegram_chat_id TEXT
        )
    ''')
    conn.commit()
    conn.close()

ensure_admin_table()
# create a default admin if none exists (username=admin / adminpass) for initial access
def ensure_default_admin():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM admin_users')
    try:
        count = cur.fetchone()[0]
    except Exception:
        count = 0
    if count == 0:
        import os, hashlib
        salt = os.urandom(16)
        dk = hashlib.pbkdf2_hmac('sha256', b'adminpass', salt, 200000)
        hash_val = salt.hex() + ':' + dk.hex()
        # create default admin (username/password) and a telegram-tracked admin entry
        cur.execute('INSERT OR REPLACE INTO admin_users (username, password_hash, telegram_username) VALUES (?,?,?)', ('admin', hash_val, '@TECHBITTrading'))
        conn.commit()
    conn.close()


def get_setting(key: str):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        cur.execute('SELECT value FROM settings WHERE key = ?', (key,))
        r = cur.fetchone()
        return r[0] if r else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def resolve_admin_telegram_username():
    # Attempt to resolve a stored admin telegram username to a chat_id using the Bot API
    admin_username = os.environ.get('ADMIN_TELEGRAM_USERNAME') or get_setting('admin_telegram_username') or '@TECHBITTrading'
    # persist chosen username
    try:
        set_setting('admin_telegram_username', admin_username)
    except Exception:
        pass
    if not TELEGRAM_TOKEN:
        return
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_TOKEN)
        # Bot.get_chat accepts '@username' and returns chat object
        chat = asyncio.run(bot.get_chat(admin_username))
        chat_id = str(chat.id)
        # store resolved chat id globally and in settings
        global ADMIN_CHAT_ID
        ADMIN_CHAT_ID = chat_id
        set_setting('admin_chat_id', chat_id)
        # also update admin_users table mapping if a record exists with this telegram username
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            cur.execute('UPDATE admin_users SET telegram_chat_id = ? WHERE telegram_username = ?', (chat_id, admin_username))
            conn.commit()
            conn.close()
        except Exception:
            pass
    except Exception:
        # failed to resolve; leave ADMIN_CHAT_ID as-is
        return

ensure_default_admin()


def ensure_messages_table():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS admin_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            user_id TEXT,
            username TEXT,
            incoming INTEGER,
            text TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    conn.close()

ensure_messages_table()


def run_migrations():
    # Fix transactions with NULL total_with_fee by setting them to amount
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("UPDATE transactions SET total_with_fee = amount WHERE total_with_fee IS NULL")
        # Also fix any rows where total_with_fee is wrongly less than amount
        cur.execute("UPDATE transactions SET total_with_fee = amount WHERE total_with_fee < amount")
        conn.commit()
        conn.close()
    except Exception:
        pass

run_migrations()

class Transaction(BaseModel):
    tx_id: int
    user_id: int
    type: str
    amount: float
    total_with_fee: Optional[float]
    network: Optional[str]
    wallet_address: Optional[str]
    payment_method: Optional[str]
    payment_info: Optional[str]
    proof_file_id: Optional[str]
    proof_file_type: Optional[str]
    currency: Optional[str]
    total_after_conversion: Optional[float]
    tx_hash_or_code: Optional[str]
    hidden: int
    status: str
    created_at: str


def build_transaction_admin_message(
    tx_id: int,
    user_id: int,
    tx_type: str,
    amount: Optional[float],
    total_with_fee: Optional[float] = None,
    network: Optional[str] = None,
    wallet_address: Optional[str] = None,
    payment_method: Optional[str] = None,
    payment_info: Optional[str] = None,
    tx_hash_or_code: Optional[str] = None,
    action: str = 'طلب جديد',
) -> str:
    lines = [f'📢 {action}: طلب رقم #{tx_id}', f'👤 المستخدم: {user_id}', f'🔄 النوع: {tx_type}']
    if amount is not None:
        lines.append(f'💰 المبلغ: {amount}')
    if total_with_fee is not None:
        lines.append(f'💵 الإجمالي مع الرسوم: {total_with_fee}')
    if network:
        lines.append(f'🌐 الشبكة: {network}')
    if wallet_address:
        lines.append(f'📍 العنوان: {wallet_address}')
    if payment_method:
        lines.append(f'💳 طريقة الدفع: {payment_method}')
    if payment_info:
        lines.append(f'📝 معلومات الدفع: {payment_info}')
    if tx_hash_or_code:
        lines.append(f'🔑 إثبات المعاملة: {tx_hash_or_code}')
    return '\n'.join(lines)


def fetch_transactions(status_filter: Optional[str] = None, show_hidden: bool = False, page: int = 1, per_page: int = 25, sort_by: str = 'created_at', sort_dir: str = 'DESC', q: Optional[str] = None) -> List[Transaction]:
    # whitelist sortable columns to avoid SQL injection
    allowed_sort = {'tx_id', 'user_id', 'amount', 'status', 'created_at'}
    if sort_by not in allowed_sort:
        sort_by = 'created_at'
    sort_dir = 'DESC' if sort_dir.upper() == 'DESC' else 'ASC'

    offset = max(page - 1, 0) * per_page

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    base_select = 'SELECT tx_id, user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, currency, total_after_conversion, tx_hash_or_code, hidden, status, created_at FROM transactions'
    params = []
    where = []
    if status_filter:
        where.append('status = ?')
        params.append(status_filter)
    if q:
        # flexible search: exact tx_id/user_id or LIKE on wallet/payment
        try:
            qi = int(q)
            where.append('(tx_id = ? OR user_id = ?)')
            params.extend([qi, qi])
        except Exception:
            where.append('(wallet_address LIKE ? OR payment_info LIKE ?)')
            likeq = '%' + q + '%'
            params.extend([likeq, likeq])
    if not show_hidden:
        where.append('hidden = 0')
    sql = base_select
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += f' ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?'
    params.extend([per_page, offset])
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        if 'no such table' in str(e).lower():
            return []
        raise
    conn.close()

    txs = []
    for r in rows:
        txs.append(Transaction(
            tx_id=r[0], user_id=r[1], type=r[2], amount=r[3], total_with_fee=r[4], network=r[5],
            wallet_address=r[6], payment_method=r[7], payment_info=r[8], proof_file_id=r[9], proof_file_type=r[10],
            currency=r[11], total_after_conversion=r[12], tx_hash_or_code=r[13], hidden=r[14], status=r[15], created_at=r[16]
        ))
    return txs

def require_jwt(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail='missing authorization header')
    if authorization.startswith('Bearer '):
        token = authorization.split(' ', 1)[1]
    else:
        token = authorization
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get('sub')
    except JWTError:
        raise HTTPException(status_code=401, detail='invalid token')


@app.get('/api/transactions', response_model=List[Transaction])
def api_list_transactions(status: Optional[str] = None, show_hidden: bool = False, page: int = 1, per_page: int = 25, sort_by: str = 'created_at', sort_dir: str = 'DESC', q: Optional[str] = None, username: str = Depends(require_jwt)):
    return fetch_transactions(status_filter=status, show_hidden=show_hidden, page=page, per_page=per_page, sort_by=sort_by, sort_dir=sort_dir, q=q)


@app.post('/api/login')
def api_login(payload: dict):
    username = payload.get('username')
    password = payload.get('password')
    if not username or not password:
        raise HTTPException(status_code=400, detail='username and password required')
    # check user in DB
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('SELECT password_hash FROM admin_users WHERE username = ?', (username,))
    r = cur.fetchone()
    conn.close()
    if not r:
        raise HTTPException(status_code=401, detail='invalid credentials')
    password_hash = r[0]
    try:
        salt_hex, dk_hex = password_hash.split(':')
        salt = bytes.fromhex(salt_hex)
        test_dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 200000)
        if test_dk.hex() != dk_hex:
            raise HTTPException(status_code=401, detail='invalid credentials')
    except ValueError:
        raise HTTPException(status_code=401, detail='invalid credentials')
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    token = jwt.encode({'sub': username, 'exp': expire}, SECRET_KEY, algorithm=ALGORITHM)
    return {'access_token': token, 'token_type': 'bearer'}




@app.get('/api/transaction/{tx_id}', response_model=Transaction)
def api_get_transaction(tx_id: int, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT tx_id, user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, currency, total_after_conversion, tx_hash_or_code, hidden, status, created_at FROM transactions WHERE tx_id = ?', (tx_id,))
        r = cursor.fetchone()
    except sqlite3.OperationalError as e:
        conn.close()
        if 'no such table' in str(e).lower():
            raise HTTPException(status_code=404, detail='Transaction not found')
        raise
    conn.close()
    if not r:
        raise HTTPException(status_code=404, detail='Transaction not found')
    return Transaction(
        tx_id=r[0], user_id=r[1], type=r[2], amount=r[3], total_with_fee=r[4], network=r[5],
        wallet_address=r[6], payment_method=r[7], payment_info=r[8], proof_file_id=r[9], proof_file_type=r[10],
        currency=r[11], total_after_conversion=r[12], tx_hash_or_code=r[13], hidden=r[14], status=r[15], created_at=r[16]
    )


@app.post('/api/transaction/{tx_id}/complete')
def api_complete_transaction(tx_id: int, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET status = ? WHERE tx_id = ?', ('COMPLETED', tx_id))
    conn.commit()
    # fetch minimal info for notification
    cursor.execute('SELECT user_id, amount, currency FROM transactions WHERE tx_id = ?', (tx_id,))
    r = cursor.fetchone()
    conn.close()
    # notify websocket clients
    fire_and_forget(manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'status': 'COMPLETED'}))
    # notify admin chat
    try:
        if r:
            user_id, amount, currency = r
            tx = fetch_transaction(tx_id)
            if tx:
                payload = tx.model_dump() if hasattr(tx, 'model_dump') else tx.dict() if hasattr(tx, 'dict') else tx
                fire_and_forget(send_admin_notification(build_transaction_admin_message(
                    tx_id=tx_id,
                    user_id=payload.get('user_id', user_id),
                    tx_type=payload.get('type', 'UNKNOWN'),
                    amount=payload.get('amount', amount),
                    total_with_fee=payload.get('total_with_fee'),
                    network=payload.get('network'),
                    wallet_address=payload.get('wallet_address'),
                    payment_method=payload.get('payment_method'),
                    payment_info=payload.get('payment_info'),
                    tx_hash_or_code=payload.get('tx_hash_or_code'),
                    action='✅ موافقة وإتمام الطلب',
                )))
            # notify user via bot as well
            try:
                fire_and_forget(_send_proof_via_bot(user_id, Path(''), f'✅ طلبك رقم {tx_id} تم تنفيذه بنجاح.'))
            except Exception:
                pass
    except Exception:
        pass
    return {'status': 'ok', 'tx_id': tx_id}


@app.post('/api/transaction/{tx_id}/reject')
def api_reject_transaction(tx_id: int, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET status = ? WHERE tx_id = ?', ('REJECTED', tx_id))
    conn.commit()
    conn.close()
    import asyncio
    fire_and_forget(manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'status': 'REJECTED'}))
    # notify admin
    try:
        tx = fetch_transaction(tx_id)
        if tx:
            payload = tx.model_dump() if hasattr(tx, 'model_dump') else tx.dict() if hasattr(tx, 'dict') else tx
            fire_and_forget(send_admin_notification(build_transaction_admin_message(
                tx_id=tx_id,
                user_id=payload.get('user_id'),
                tx_type=payload.get('type', 'UNKNOWN'),
                amount=payload.get('amount'),
                total_with_fee=payload.get('total_with_fee'),
                network=payload.get('network'),
                wallet_address=payload.get('wallet_address'),
                payment_method=payload.get('payment_method'),
                payment_info=payload.get('payment_info'),
                tx_hash_or_code=payload.get('tx_hash_or_code'),
                action='❌ رفض الطلب',
            )))
    except Exception:
        pass
    return {'status': 'ok', 'tx_id': tx_id}


async def _send_proof_via_bot(chat_id: int, proof_path: Path, message: Optional[str] = None):
    if not TELEGRAM_TOKEN:
        return False
    try:
        from telegram import Bot
    except Exception:
        return False

    bot = Bot(token=TELEGRAM_TOKEN)

    def _send():
        try:
            if proof_path.exists() and proof_path.is_file():
                suffix = proof_path.suffix.lower()
                if suffix in ('.jpg', '.jpeg', '.png', '.webp'):
                    with proof_path.open('rb') as fh:
                        bot.send_photo(chat_id=chat_id, photo=fh, caption=message or '')
                    return True
            # fallback to text message when no image
            bot.send_message(chat_id=chat_id, text=message or 'تمت معالجة طلبك.')
            return True
        except Exception:
            return False

    return await asyncio.to_thread(_send)


async def send_text_via_bot(chat_id: int, text: str):
    if not TELEGRAM_TOKEN:
        return False
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_TOKEN)

        async def _s():
            try:
                await bot.send_message(chat_id=chat_id, text=text)
                return True
            except Exception:
                return False

        return await _s()
    except Exception:
        return False


def _send_admin_notification_sync(message: str):
    if not TELEGRAM_TOKEN:
        return False
    try:
        import asyncio
        from telegram import Bot
        bot = Bot(token=TELEGRAM_TOKEN)
        chat_id = ADMIN_GROUP_LINK or get_setting('admin_group_link') or os.environ.get('ADMIN_GROUP_LINK')
        if not chat_id:
            chat_id = ADMIN_CHAT_ID or get_setting('admin_chat_id') or os.environ.get('ADMIN_CHAT_ID')
        if not chat_id:
            return False

        async def _send():
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                return True
            except Exception:
                try:
                    await bot.send_message(chat_id=int(chat_id), text=message)
                    return True
                except Exception:
                    return False

        try:
            return asyncio.run(_send())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_send())
            finally:
                loop.close()
    except Exception:
        return False

async def send_admin_notification(message: str):
    return await asyncio.to_thread(_send_admin_notification_sync, message)

# Predefined accounts for payment instructions
ACCOUNTS = {
    'الكريمي': 'حساب حاسب 2325013',
    'محفظة جيب': 'محفظة جيب 739942424',
    'نجم حوالة': 'نجم حوالة - باسم خالد وليد عبدالله المسني - 784983835',
    'الامتياز': 'الامتياز - باسم خالد وليد عبدالله المسني - 784983835',
    'القطيبي': 'القطيبي - يرجى اختيار الشبكة (SAR/USD)'
}


class PaymentPayload(BaseModel):
    account_key: str
    note: Optional[str] = None


@app.post('/api/transaction/{tx_id}/send_payment_info')
def api_send_payment_info(tx_id: int, payload: PaymentPayload, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM transactions WHERE tx_id = ?', (tx_id,))
    r = cur.fetchone()
    conn.close()
    if not r:
        raise HTTPException(status_code=404, detail='Transaction not found')
    user_id = r[0]
    acc = ACCOUNTS.get(payload.account_key)
    if not acc:
        raise HTTPException(status_code=400, detail='Unknown account')
    msg = f'مرحباً، هذه بيانات تحويل طلبك #{tx_id}:\n{acc}\n'
    if payload.note:
        msg += f'ملاحظة: {payload.note}\n'
    msg += '\nشكراً لتعاملكم.'
    fire_and_forget(_send_proof_via_bot(user_id, Path(''), msg))
    return {'status': 'ok', 'tx_id': tx_id, 'account': payload.account_key}


@app.post('/api/transaction/{tx_id}/hide')
def api_hide_transaction(tx_id: int, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET hidden = 1 WHERE tx_id = ?', (tx_id,))
    conn.commit()
    conn.close()
    fire_and_forget(manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'hidden': 1}))
    return {'status': 'ok', 'tx_id': tx_id}


@app.post('/api/transaction/{tx_id}/send_proof')
def api_send_proof(tx_id: int, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, proof_file_id, proof_file_type FROM transactions WHERE tx_id = ?', (tx_id,))
    r = cursor.fetchone()
    if not r:
        conn.close()
        raise HTTPException(status_code=404, detail='Transaction not found')
    user_id, proof_file_id, proof_file_type = r
    # update status to COMPLETED
    cursor.execute('UPDATE transactions SET status = ? WHERE tx_id = ?', ('COMPLETED', tx_id))
    conn.commit()
    conn.close()

    # broadcast update
    fire_and_forget(manager.broadcast_json({'type': 'update', 'tx_id': tx_id, 'status': 'COMPLETED'}))

    # attempt to send proof via Telegram in background
    proof_sent = False
    message = f'✅ طلبك رقم {tx_id} تم تنفيذه بنجاح.'
    if proof_file_id and proof_file_type == 'photo':
        proof_path = PROOF_DIR / proof_file_id
        # if file absent, try common prefixed filename
        if not proof_path.exists():
            alt = PROOF_DIR / f'proof_{tx_id}{proof_path.suffix or ''}'
            if alt.exists():
                proof_path = alt

        if proof_path.exists():
            fire_and_forget(_send_proof_via_bot(user_id, proof_path, message))
            proof_sent = True
            # move proof into user folder as archive
            try:
                user_dir = PROOF_DIR / f'user_{user_id}'
                user_dir.mkdir(exist_ok=True)
                dst = user_dir / proof_path.name
                if not dst.exists():
                    proof_path.replace(dst)
            except Exception:
                pass
        else:
            # send only text message if image missing
            fire_and_forget(_send_proof_via_bot(user_id, Path(''), message))

    else:
        fire_and_forget(_send_proof_via_bot(user_id, Path(''), message))

    # notify admin as well
    try:
        tx = fetch_transaction(tx_id)
        if tx:
            payload = tx.model_dump() if hasattr(tx, 'model_dump') else tx.dict() if hasattr(tx, 'dict') else tx
            fire_and_forget(send_admin_notification(build_transaction_admin_message(
                tx_id=tx_id,
                user_id=payload.get('user_id'),
                tx_type=payload.get('type', 'UNKNOWN'),
                amount=payload.get('amount'),
                total_with_fee=payload.get('total_with_fee'),
                network=payload.get('network'),
                wallet_address=payload.get('wallet_address'),
                payment_method=payload.get('payment_method'),
                payment_info=payload.get('payment_info'),
                tx_hash_or_code=payload.get('tx_hash_or_code'),
                action='✅ إرسال إثبات/إكمال الطلب',
            )))
    except Exception:
        pass

    return {'status': 'ok', 'tx_id': tx_id, 'proof_sent': proof_sent}


@app.get('/proofs/{filename}')
def get_proof_file(filename: str, username: str = Depends(require_jwt)):
    path = PROOF_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail='File not found')
    return FileResponse(str(path))


@app.get('/api/bot/status')
def api_bot_status(username: str = Depends(require_jwt)):
    # Check if Bot token is configured and reachable
    if not TELEGRAM_TOKEN:
        return {'running': False, 'reason': 'no_token'}
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_TOKEN)
        me = bot.get_me()
        return {'running': True, 'bot_user': me.username, 'bot_id': me.id}
    except Exception as e:
        return {'running': False, 'reason': str(e)}


@app.post('/api/bot/start')
def api_bot_start(username: str = Depends(require_jwt)):
    # Attempt to spawn bot.py as a subprocess if present
    try:
        import subprocess, sys
        bot_path = ROOT / 'bot.py'
        if not bot_path.exists():
            raise HTTPException(status_code=404, detail='bot.py not found')
        # start detached process
        if os.name == 'nt':
            DETACHED = subprocess.CREATE_NEW_PROCESS_GROUP
            p = subprocess.Popen([sys.executable, str(bot_path)], creationflags=DETACHED)
        else:
            p = subprocess.Popen([sys.executable, str(bot_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'started': True, 'pid': p.pid}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/settings')
def api_get_settings(username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        cur.execute('SELECT key, value FROM settings')
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {}
    conn.close()
    return {k: v for k, v in rows}


class SettingsPayload(BaseModel):
    key: str
    value: str


@app.post('/api/settings')
def api_set_setting(payload: SettingsPayload, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (payload.key, payload.value))
    conn.commit()
    conn.close()
    return {'status': 'ok', 'key': payload.key, 'value': payload.value}


@app.get('/api/admin/messages')
def api_get_admin_messages(limit: int = 100, username: str = Depends(require_jwt)):
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('SELECT id, chat_id, user_id, username, incoming, text, created_at FROM admin_messages ORDER BY id DESC LIMIT ?', (limit,))
    rows = cur.fetchall()
    conn.close()
    msgs = [{'id': r[0], 'chat_id': r[1], 'user_id': r[2], 'username': r[3], 'incoming': bool(r[4]), 'text': r[5], 'created_at': r[6]} for r in rows]
    return msgs


class AdminIncoming(BaseModel):
    chat_id: Optional[str]
    user_id: Optional[str]
    username: Optional[str]
    text: str


@app.post('/api/admin/message')
def api_post_admin_message(payload: AdminIncoming):
    # Endpoint for bot to post incoming messages to the admin UI
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('INSERT INTO admin_messages (chat_id, user_id, username, incoming, text) VALUES (?,?,?,?,?)', (payload.chat_id, payload.user_id, payload.username, 1, payload.text))
    conn.commit()
    conn.close()
    # broadcast via websocket so UI updates
    fire_and_forget(manager.broadcast_json({'type': 'admin_message'}))
    return {'status': 'ok'}


class AdminReply(BaseModel):
    user_id: Optional[str]
    chat_id: Optional[str]
    text: str


@app.post('/api/admin/send')
def api_admin_send(payload: AdminReply, username: str = Depends(require_jwt)):
    # send a message to a user via bot and log it
    target = payload.chat_id or payload.user_id
    if not target:
        raise HTTPException(status_code=400, detail='missing target')
    try:
        # attempt to send as int chat_id first
        try:
            fire_and_forget(send_text_via_bot(int(target), payload.text))
        except Exception:
            fire_and_forget(send_text_via_bot(target, payload.text))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    # log admin outgoing message
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('INSERT INTO admin_messages (chat_id, user_id, username, incoming, text) VALUES (?,?,?,?,?)', (payload.chat_id, payload.user_id, username, 0, payload.text))
    conn.commit()
    conn.close()
    fire_and_forget(manager.broadcast_json({'type': 'admin_message'}))
    return {'status': 'ok'}


@app.get('/api/reports')
def api_reports(period: Optional[str] = 'month', compare: Optional[int] = 0, username: str = Depends(require_jwt)):
    # period: day, week, month, year
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        # Treat missing total_with_fee as equal to amount (no fee) to avoid
        # negative aggregates when total_with_fee is NULL.
        cur.execute("SELECT COUNT(*), SUM(amount), SUM(COALESCE(total_with_fee, amount)) FROM transactions WHERE hidden=0")
        total_ops, sum_amount, sum_total = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return {'total_ops': 0, 'sum_amount': 0, 'sum_total': 0, 'status_counts': {}, 'chart_rows': []}

    total_ops = total_ops or 0
    sum_amount = sum_amount or 0.0
    sum_total = sum_total or 0.0

    # estimate fees as sum(total_with_fee - amount); if total_with_fee is NULL
    # treat it as amount (=> zero fee) to avoid large negative fees coming from NULLs
    cur.execute('SELECT SUM(COALESCE(total_with_fee, amount) - COALESCE(amount,0)) FROM transactions WHERE hidden=0')
    res = cur.fetchone()
    sum_fee = res[0] or 0.0

    cur.execute("SELECT status, COUNT(*) FROM transactions WHERE hidden=0 GROUP BY status")
    status_counts = {r[0]: r[1] for r in cur.fetchall()}

    # chart aggregation
    if period == 'day':
        date_expr = "strftime('%Y-%m-%d', created_at)"
        span_days = 1
    elif period == 'week':
        date_expr = "strftime('%Y-%W', created_at)"
        span_days = 7
    elif period == 'year':
        date_expr = "strftime('%Y', created_at)"
        span_days = 365
    else:
        date_expr = "strftime('%Y-%m', created_at)"
        span_days = 30

    cur.execute(f"SELECT {date_expr} as period, SUM(COALESCE(total_with_fee,amount)) FROM transactions WHERE hidden=0 GROUP BY period ORDER BY period DESC LIMIT 12")
    chart_rows = cur.fetchall()[::-1]

    prev_sum_total = None
    if compare:
        # compute previous period sum_total using simple date subtraction
        try:
            if span_days >= 365:
                cur.execute("SELECT SUM(COALESCE(total_with_fee,0)) FROM transactions WHERE hidden=0 AND date(created_at) >= date('now','-2 years') AND date(created_at) < date('now','-1 year')")
            else:
                cur.execute(f"SELECT SUM(COALESCE(total_with_fee,0)) FROM transactions WHERE hidden=0 AND date(created_at) >= date('now','-{span_days*2} days') AND date(created_at) < date('now','-{span_days} days')")
            prev_sum_total = cur.fetchone()[0] or 0.0
        except Exception:
            prev_sum_total = 0.0

    # buy/sell counts
    cur.execute("SELECT type, COUNT(*) FROM transactions WHERE hidden=0 GROUP BY type")
    type_counts = {r[0]: r[1] for r in cur.fetchall()}

    # top customers
    cur.execute("SELECT user_id, COUNT(*), SUM(COALESCE(total_with_fee,0)) FROM transactions WHERE hidden=0 GROUP BY user_id ORDER BY SUM(COALESCE(total_with_fee,0)) DESC LIMIT 10")
    top_customers = [{'user_id': r[0], 'count': r[1], 'sum_total': r[2] or 0.0} for r in cur.fetchall()]
    # top buyers
    cur.execute("SELECT user_id, COUNT(*), SUM(COALESCE(total_with_fee,0)) FROM transactions WHERE hidden=0 AND type='BUY' GROUP BY user_id ORDER BY SUM(COALESCE(total_with_fee,0)) DESC LIMIT 10")
    top_buyers = [{'user_id': r[0], 'count': r[1], 'sum_total': r[2] or 0.0} for r in cur.fetchall()]
    # top sellers
    cur.execute("SELECT user_id, COUNT(*), SUM(COALESCE(total_with_fee,0)) FROM transactions WHERE hidden=0 AND type='SELL' GROUP BY user_id ORDER BY SUM(COALESCE(total_with_fee,0)) DESC LIMIT 10")
    top_sellers = [{'user_id': r[0], 'count': r[1], 'sum_total': r[2] or 0.0} for r in cur.fetchall()]

    conn.close()

    return {'total_ops': total_ops, 'sum_amount': sum_amount, 'sum_total': sum_total, 'sum_fee': sum_fee, 'status_counts': status_counts, 'chart_rows': chart_rows, 'prev_sum_total': prev_sum_total, 'type_counts': type_counts, 'top_customers': top_customers, 'top_buyers': top_buyers, 'top_sellers': top_sellers}


@app.websocket('/ws/transactions')
async def websocket_transactions(ws: WebSocket, token: Optional[str] = None):
    # token expected as query param ?token=JWT
    try:
        if not token:
            await ws.close(code=4001)
            return
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        await ws.close(code=4002)
        return
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# Mount a simple static frontend folder if present
frontend_dir = Path(__file__).parent / 'static'
if frontend_dir.exists():
    app.mount('/', StaticFiles(directory=str(frontend_dir), html=True), name='static')


# Try to resolve admin telegram username to a chat id at startup (if BOT_TOKEN provided)
try:
    resolve_admin_telegram_username()
except Exception:
    pass

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('backend.main:app', host='127.0.0.1', port=8000, reload=True)
