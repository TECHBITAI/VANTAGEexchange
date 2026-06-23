import sqlite3,os,sys
DB=os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'techbit_v2.db')
DB=os.path.abspath(DB)
if not os.path.exists(DB):
    print('DB_NOT_FOUND', DB)
    sys.exit(0)
conn=sqlite3.connect(DB)
cur=conn.cursor()
cur.execute("SELECT tx_id,user_id,type,amount,total_with_fee,created_at FROM transactions WHERE COALESCE(total_with_fee,0) < COALESCE(amount,0) ORDER BY created_at DESC LIMIT 50")
rows=cur.fetchall()
if not rows:
    print('NO_PROBLEM_ROWS')
else:
    for r in rows:
        print('tx_id=',r[0],'user=',r[1],'type=',r[2],'amount=',r[3],'total_with_fee=',r[4],'created_at=',r[5])
conn.close()
