from configparser import ConfigParser
from pathlib import Path
import sys

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "database" / "migrations"
sys.path.insert(0, str(MIGRATIONS_DIR))

from url_utils import escape_configparser_percent  # noqa: E402


def test_database_url_with_encoded_percent_is_safe_for_configparser() -> None:
    database_url = "postgresql+psycopg://user:IrelandDublin123%3F@host.example/db"
    parser = ConfigParser()
    parser.add_section("alembic")

    parser.set("alembic", "sqlalchemy.url", escape_configparser_percent(database_url))

    assert parser.get("alembic", "sqlalchemy.url") == database_url
    assert escape_configparser_percent(database_url) != database_url
