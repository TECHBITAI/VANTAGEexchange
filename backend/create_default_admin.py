import sqlite3, hashlib, os
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'techbit_v2.db'
conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()
cur.execute('CREATE TABLE IF NOT EXISTS admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT)')
username='admin'
password='adminpass'
# hash
salt = os.urandom(16)
dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 200000)
hash_val = salt.hex() + ':' + dk.hex()
cur.execute('INSERT OR REPLACE INTO admin_users (username, password_hash) VALUES (?,?)', (username, hash_val))
conn.commit()
conn.close()
print('default admin created: admin / adminpass')
