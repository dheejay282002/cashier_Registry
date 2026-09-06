import sqlite3
import os
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
db = os.path.join(BASE_DIR, 'db.sqlite3')
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("PRAGMA table_info('cashier_profile')")
cols = cur.fetchall()
print('cashier_profile columns:')
for c in cols:
    print(c)
conn.close()
