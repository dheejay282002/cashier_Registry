from pathlib import Path
import sqlite3

from django.apps import apps
from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Transfer all data from the local SQLite database into the configured MySQL database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=str(Path(settings.BASE_DIR) / "db.sqlite3"),
            help="Path to the source SQLite database file.",
        )
        parser.add_argument(
            "--create-database",
            action="store_true",
            help="Create the MySQL database if it does not already exist.",
        )

    def handle(self, *args, **options):
        source_path = Path(options["source"])
        if not source_path.exists():
            raise CommandError(f"Source SQLite database not found: {source_path}")

        target_settings = settings.DATABASES["default"]
        if target_settings.get("ENGINE") not in {
            "django.db.backends.mysql",
            "admin_panel.db_backends.mysql_compat",
        }:
            raise CommandError("The default database is not configured for a MySQL-compatible backend.")

        self._ensure_mysql_database(
            database_name=target_settings["NAME"],
            user=target_settings.get("USER", "root"),
            password=target_settings.get("PASSWORD", ""),
            host=target_settings.get("HOST", "127.0.0.1"),
            port=int(target_settings.get("PORT", 3306)),
            create_database=True,
        )

        self.stdout.write(self.style.NOTICE("Creating MySQL schema..."))
        self._create_mysql_schema()

        source_conn = sqlite3.connect(str(source_path))
        source_conn.row_factory = sqlite3.Row
        try:
            with connections["default"].cursor() as target_cursor:
                self._copy_all_tables(source_conn, target_cursor)
            connections["default"].commit()
        finally:
            source_conn.close()

        self.stdout.write(self.style.SUCCESS("SQLite data has been transferred to MySQL."))

    def _ensure_mysql_database(self, database_name, user, password, host, port, create_database):
        if not create_database:
            return

        try:
            import pymysql
        except ImportError as exc:
            raise CommandError("PyMySQL is required to create the MySQL database.") from exc

        connection = pymysql.connect(
            host=host,
            user=user,
            password=password,
            port=port,
            autocommit=True,
            charset="utf8mb4",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            connection.close()

    def _create_mysql_schema(self):
        target_connection = connections["default"]
        existing_tables = set(target_connection.introspection.table_names())
        models = [
            MigrationRecorder.Migration,
            apps.get_model("contenttypes", "ContentType"),
            apps.get_model("auth", "Permission"),
            apps.get_model("auth", "Group"),
            apps.get_model("auth", "User"),
            apps.get_model("sessions", "Session"),
            apps.get_model("admin", "LogEntry"),
            apps.get_model("admin_panel", "SystemSetting"),
            apps.get_model("admin_panel", "AuditLog"),
            apps.get_model("cashier", "Transaction"),
            apps.get_model("cashier", "FundCluster"),
            apps.get_model("cashier", "Supplier"),
            apps.get_model("cashier", "Profile"),
            apps.get_model("cashier", "Report"),
            apps.get_model("cashier", "RoleConfig"),
            apps.get_model("cashier", "Cheque"),
        ]

        with target_connection.schema_editor() as schema_editor:
            for model in models:
                if model._meta.db_table in existing_tables:
                    continue
                schema_editor.create_model(model)

    def _copy_all_tables(self, source_conn, target_cursor):
        tables = [
            row[0]
            for row in source_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

        target_tables = set(connections["default"].introspection.table_names())

        target_cursor.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            for table_name in tables:
                if table_name not in target_tables:
                    continue
                columns = [
                    column_info[1]
                    for column_info in source_conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                ]
                if not columns:
                    continue

                source_rows = source_conn.execute(
                    f'SELECT {", ".join(f"\"{column}\"" for column in columns)} FROM "{table_name}"'
                ).fetchall()

                target_cursor.execute(f"DELETE FROM `{table_name}`")
                if not source_rows:
                    continue

                insert_sql = (
                    f"INSERT INTO `{table_name}` ({', '.join(f'`{column}`' for column in columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})"
                )
                payload = [tuple(row[column] for column in columns) for row in source_rows]
                target_cursor.executemany(insert_sql, payload)

                if table_name in {"django_content_type", "auth_permission", "auth_group", "auth_user"}:
                    self.stdout.write(f"Copied {len(source_rows)} rows from {table_name}")
                else:
                    self.stdout.write(f"Copied {len(source_rows)} rows from {table_name}")
        finally:
            target_cursor.execute("SET FOREIGN_KEY_CHECKS=1")
