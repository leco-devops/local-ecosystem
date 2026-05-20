"""Per-kind data importers."""

from leco_app.data_import.importers.cloudflare import import_d1, import_kv, import_r2
from leco_app.data_import.importers.files import import_files
from leco_app.data_import.importers.mongodb import import_mongodb
from leco_app.data_import.importers.mysql import import_mysql
from leco_app.data_import.importers.postgres import import_postgres
from leco_app.data_import.importers.redis import import_redis

IMPORTERS = {
    "mongodb": import_mongodb,
    "mysql": import_mysql,
    "postgres": import_postgres,
    "redis": import_redis,
    "d1": import_d1,
    "r2": import_r2,
    "kv": import_kv,
    "files": import_files,
}

__all__ = ["IMPORTERS"]
