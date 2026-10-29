import json
import sys


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "missing config"}))
        return
    cfg = json.loads(sys.argv[1])
    try:
        import pymysql
    except ImportError:
        print(json.dumps({"ok": False, "error": "pymysql not installed"}))
        return

    try:
        conn = pymysql.connect(
            host=str(cfg.get("host", "")),
            port=int(cfg.get("port") or 3306),
            user=str(cfg.get("user", "")),
            password=str(cfg.get("password") or ""),
            database=str(cfg.get("database", "")),
            connect_timeout=5,
            charset="utf8mb4",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute("SHOW TABLES")
            tables = len(cur.fetchall())
        conn.close()
        print(json.dumps({"ok": True, "version": version, "tables": tables}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
