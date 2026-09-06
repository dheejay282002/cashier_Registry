import os, sys, time, json, re, sqlite3
os.environ['DJANGO_SETTINGS_MODULE'] = 'registry.settings'
import django
django.setup()

from django.db import connection, reset_queries
from django.conf import settings
from django.test import RequestFactory, TestCase
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.utils import timezone
from datetime import timedelta

settings.DEBUG = True  # Enable SQL logging

print("=" * 80)
print("FULL SYSTEM DIAGNOSTIC REPORT")
print("=" * 80)

# ─── 1. DATABASE STATS ─────────────────────────────────────────────
print("\n[1] DATABASE TABLE STATS")
print("-" * 60)
cursor = connection.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
tables = [r[0] for r in cursor.fetchall()]
total_rows = 0
for t in sorted(tables):
    cursor.execute(f'SELECT COUNT(*) FROM [{t}]')
    count = cursor.fetchone()[0]
    total_rows += count
    if count > 0:
        print(f"  {t:40s} {count:>10,} rows")
print(f"  {'TOTAL':40s} {total_rows:>10,} rows")

# ─── 2. MISSING INDEXES ────────────────────────────────────────────
print("\n[2] DATABASE INDEX ANALYSIS")
print("-" * 60)
cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
indexes = set(r[0] for r in cursor.fetchall())
cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'django_%' AND name NOT LIKE 'auth_%'")
for tname, create_sql in cursor.fetchall():
    if create_sql and 'PRIMARY KEY' in create_sql:
        continue
    cursor.execute(f'PRAGMA table_info([{tname}])')
    fks = [r[1] for r in cursor.fetchall() if r[5] > 0]  # FK columns
    for fk_col in fks:
        idx_name = f'{tname}_{fk_col}'
        if not any(idx_name in idx for idx in indexes):
            print(f"  MISSING INDEX: {tname}.{fk_col}")

# ─── 3. QUERY PROFILING: admin_dashboard ────────────────────────────
print("\n[3] QUERY PROFILING: admin_dashboard")
print("-" * 60)

from admin_panel.views import admin_dashboard
factory = RequestFactory()

# Create a test user
try:
    test_user = User.objects.filter(is_superuser=True).first()
    if not test_user:
        test_user = User.objects.create_superuser('diagtest', 'test@test.com', 'testpass123')
        cleanup_user = True
    else:
        cleanup_user = False
except:
    test_user = User.objects.first()
    cleanup_user = False

request = factory.get('/admin-dashboard/')
request.user = test_user
request.session = {}

reset_queries()
start = time.time()
try:
    response = admin_dashboard(request)
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    response = None
elapsed = time.time() - start

query_count = len(connection.queries)
print(f"  Total queries: {query_count}")
print(f"  Total time:    {elapsed*1000:.1f}ms")
if connection.queries:
    total_sql_time = sum(float(q.get('time', 0)) for q in connection.queries)
    print(f"  SQL time:      {total_sql_time*1000:.1f}ms")
    print(f"\n  Top 10 slowest queries:")
    sorted_q = sorted(connection.queries, key=lambda q: float(q.get('time', 0)), reverse=True)[:10]
    for i, q in enumerate(sorted_q, 1):
        sql = q['sql'][:120]
        t = float(q.get('time', 0)) * 1000
        print(f"  {i:2d}. [{t:6.1f}ms] {sql}...")

# ─── 4. QUERY PROFILING: cashier_dashboard ──────────────────────────
print("\n[4] QUERY PROFILING: cashier_dashboard")
print("-" * 60)

from admin_panel.views import cashier_dashboard
request2 = factory.get('/cashier-dashboard/')
request2.user = test_user
request2.session = {}

reset_queries()
start = time.time()
try:
    response2 = cashier_dashboard(request2)
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    response2 = None
elapsed = time.time() - start

query_count2 = len(connection.queries)
print(f"  Total queries: {query_count2}")
print(f"  Total time:    {elapsed*1000:.1f}ms")
if connection.queries:
    total_sql_time = sum(float(q.get('time', 0)) for q in connection.queries)
    print(f"  SQL time:      {total_sql_time*1000:.1f}ms")
    print(f"\n  Top 10 slowest queries:")
    sorted_q = sorted(connection.queries, key=lambda q: float(q.get('time', 0)), reverse=True)[:10]
    for i, q in enumerate(sorted_q, 1):
        sql = q['sql'][:120]
        t = float(q.get('time', 0)) * 1000
        print(f"  {i:2d}. [{t:6.1f}ms] {sql}...")

# ─── 5. QUERY PROFILING: context_processor ──────────────────────────
print("\n[5] QUERY PROFILING: context_processor (per-request cost)")
print("-" * 60)
from admin_panel.context_processors import system_settings
reset_queries()
start = time.time()
system_settings(request)
elapsed = time.time() - start
print(f"  Queries: {len(connection.queries)}")
print(f"  Time:    {elapsed*1000:.1f}ms")
for q in connection.queries:
    print(f"    [{float(q.get('time',0))*1000:.1f}ms] {q['sql'][:140]}")

# ─── 6. MIDDLEWARE PROFILING ────────────────────────────────────────
print("\n[6] MIDDLEWARE ANALYSIS")
print("-" * 60)
from django.conf import settings as dj_settings
mw = dj_settings.MIDDLEWARE
print(f"  Total middleware classes: {len(mw)}")
for m in mw:
    print(f"    - {m}")

# Check middleware that hits DB
print(f"\n  Known DB-hitting middleware:")
print(f"    - RolePermissionsMiddleware: calls _get_profile() = 1 query")
print(f"    - SingleDeviceLoginMiddleware: profile + device checks = 4-5 queries")
print(f"    - context_processors.system_settings: settings + profile = 2 queries")

# ─── 7. N+1 LOOP DETECTION ──────────────────────────────────────────
print("\n[7] N+1 LOOP DETECTION IN VIEWS")
print("-" * 60)
view_files = [
    'admin_panel/views.py',
    'admin_panel/dashboard_views.py',
    'admin_panel/supplier_views.py',
    'admin_panel/cheque_views.py',
    'admin_panel/report_views.py',
    'admin_panel/utils.py',
    'cashier/views.py',
]
for vf in view_files:
    fpath = os.path.join(os.path.dirname(__file__) if '__file__' in dir() else '.', vf)
    if not os.path.exists(fpath):
        fpath = vf
    if not os.path.exists(fpath):
        continue
    with open(fpath) as f:
        lines = f.readlines()
    loops_with_queries = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('for ') and ':' in stripped:
            # Check next 5 lines for DB queries
            for j in range(i, min(i+6, len(lines))):
                next_line = lines[j].strip() if j < len(lines) else ''
                if '.filter(' in next_line and ('aggregate(' in next_line or '.count()' in next_line or '.exists()' in next_line):
                    loops_with_queries.append((i, stripped[:80], j+1, next_line[:100]))
                    break
    if loops_with_queries:
        print(f"\n  {vf}:")
        for loop_line, loop_code, query_line, query_code in loops_with_queries:
            print(f"    Line {loop_line}: {loop_code}")
            print(f"      -> Line {query_line}: {query_code}")

# ─── 8. FILE SIZE ANALYSIS ──────────────────────────────────────────
print("\n[8] FILE SIZE ANALYSIS")
print("-" * 60)
big_files = []
for root, dirs, files in os.walk('.'):
    if '__pycache__' in root or '.git' in root or 'node_modules' in root:
        continue
    for fname in files:
        if fname.endswith('.py') or fname.endswith('.html'):
            fpath = os.path.join(root, fname)
            size = os.path.getsize(fpath)
            lines_count = sum(1 for _ in open(fpath, encoding='utf-8', errors='ignore'))
            if lines_count > 200:
                big_files.append((fpath, lines_count, size))
big_files.sort(key=lambda x: x[1], reverse=True)
for fp, lc, sz in big_files[:15]:
    print(f"  {fp:55s} {lc:>5,} lines  ({sz//1024}KB)")

# ─── 9. TEMPLATE RENDERING ──────────────────────────────────────────
print("\n[9] TEMPLATE CHAIN ANALYSIS")
print("-" * 60)
from django.template.loader import get_template
for tpl_name in ['admin_panel/dashboard.html', 'cashier/dashboard.html']:
    try:
        tpl = get_template(tpl_name)
        chain = []
        node = tpl
        while hasattr(node, 'origin') and node.origin:
            chain.append(str(node.origin))
            if hasattr(node, 'parent') and node.parent:
                node = node.parent
            else:
                break
        print(f"  {tpl_name}:")
        for c in chain:
            print(f"    -> {c}")
    except Exception as e:
        print(f"  {tpl_name}: ERROR - {e}")

# ─── 10. MEMORY / CONNECTION ────────────────────────────────────────
print("\n[10] DATABASE CONNECTION INFO")
print("-" * 60)
print(f"  Engine:   {settings.DATABASES['default']['ENGINE']}")
print(f"  Database: {settings.DATABASES['default']['NAME']}")
db_path = settings.DATABASES['default']['NAME']
if os.path.exists(db_path):
    db_size = os.path.getsize(db_path)
    print(f"  DB Size:  {db_size // (1024*1024)}MB ({db_size // 1024}KB)")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)
