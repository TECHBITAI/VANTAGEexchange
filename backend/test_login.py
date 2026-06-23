import requests
import sys
BASE='http://127.0.0.1:8000'
try:
    r = requests.post(BASE+'/api/login', json={'username':'admin','password':'adminpass'})
    print('login status', r.status_code)
    print(r.text)
    if r.status_code==200:
        token = r.json().get('access_token')
        headers={'Authorization': 'Bearer '+token}
        t = requests.get(BASE+'/api/transactions', headers=headers)
        print('transactions status', t.status_code)
        print(t.text)
except Exception as e:
    print('error', e)
    sys.exit(1)
