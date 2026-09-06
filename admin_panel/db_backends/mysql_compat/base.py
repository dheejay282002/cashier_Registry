import pymysql

pymysql.install_as_MySQLdb()

from django.db.backends.mysql.base import DatabaseWrapper as MySQLDatabaseWrapper
from django.db.backends.mysql.features import DatabaseFeatures as MySQLDatabaseFeatures


class DatabaseFeatures(MySQLDatabaseFeatures):
    can_return_columns_from_insert = False
    can_return_rows_from_bulk_insert = False


class DatabaseWrapper(MySQLDatabaseWrapper):
    features_class = DatabaseFeatures

    def check_database_version_supported(self):
        return
