import requests, sys
BASE='http://127.0.0.1:8000'
username='admin'
password='adminpass'
print('Logging in...')
r = requests.post(f'{BASE}/api/login', json={'username':username,'password':password}, timeout=10)
print('Login status:', r.status_code)
try:
    print(r.json())
except Exception:
    print('No JSON response:', r.text)
if r.status_code!=200:
    sys.exit(1)
token = r.json().get('access_token')
headers={'Authorization': f'Bearer {token}'}
print('Fetching transactions...')
r = requests.get(f'{BASE}/api/transactions', headers=headers, timeout=10)
print('List status:', r.status_code)
try:
    txs = r.json()
    print('Found', len(txs), 'transactions')
except Exception:
    print('Failed to parse transactions:', r.text)
    sys.exit(0)
if not txs:
    print('No transactions to test.')
    sys.exit(0)
first = txs[0]
print('First tx:', first.get('tx_id'))
txid = first.get('tx_id')
print('Calling /send_proof...')
r = requests.post(f'{BASE}/api/transaction/{txid}/send_proof', headers=headers, timeout=10)
print('send_proof status:', r.status_code, r.text)
print('Calling /hide...')
r = requests.post(f'{BASE}/api/transaction/{txid}/hide', headers=headers, timeout=10)
print('hide status:', r.status_code, r.text)
print('Done')
