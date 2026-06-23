import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path('..') / 'techbit_v2.db'
PROOF_DIR = Path('..') / 'proof_files'
PROOF_DIR.mkdir(exist_ok=True)

conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()

cur.execute('''
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
    status TEXT,
    created_at TEXT
)
''')

now = datetime.utcnow().isoformat()

# create a simple proof file
proof_path = PROOF_DIR / 'sample_proof.txt'
with open(proof_path, 'w', encoding='utf-8') as f:
    f.write('Sample proof placeholder')

# insert sample rows
cur.execute('INSERT INTO transactions (user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, currency, total_after_conversion, tx_hash_or_code, hidden, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (12345, 'SELL', 100.0, 98.5, 'TRC20', 'TWalletAddr', 'BankTransfer', 'Account: 123', 'sample_proof.txt', 'text/plain', 'USD', 98.5, 'ABC123', 0, 'PENDING', now))

cur.execute('INSERT INTO transactions (user_id, type, amount, total_with_fee, network, wallet_address, payment_method, payment_info, proof_file_id, proof_file_type, currency, total_after_conversion, tx_hash_or_code, hidden, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (54321, 'BUY', 50.0, 49.0, 'ERC20', 'EWalletAddr', 'MobilePay', 'Ref: 456', None, None, 'USD', 49.0, None, 0, 'COMPLETED', now))

conn.commit()
conn.close()
print('Seeded', DB_PATH)
