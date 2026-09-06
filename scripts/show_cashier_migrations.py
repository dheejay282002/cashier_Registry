import sqlite3, os
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
db = os.path.join(BASE_DIR, 'db.sqlite3')
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("SELECT name,app FROM django_migrations WHERE app='cashier'")
rows = cur.fetchall()
print('applied cashier migrations:')
for r in rows:
    print(r)
conn.close()
