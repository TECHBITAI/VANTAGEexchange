import sqlite3
from pathlib import Path
from getpass import getpass
import hashlib, os, binascii


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 200000)
    return salt.hex() + ':' + dk.hex()

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'techbit_v2.db'

conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    password_hash TEXT
)
''')
username = input('admin username: ')
pw = getpass('password: ')
hash = hash_password(pw)
cur.execute('INSERT OR REPLACE INTO admin_users (username, password_hash) VALUES (?,?)', (username, hash))
conn.commit()
conn.close()
print('Created admin', username)
