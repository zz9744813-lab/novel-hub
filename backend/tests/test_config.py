from sqlalchemy.engine import make_url

from app.config import Settings


def test_database_url_escapes_credentials_and_preserves_connection_parts():
    settings = Settings(
        postgres_user="writer@example",
        postgres_password="p@ss:/?#%word",
        postgres_host="127.0.0.1",
        postgres_port=55439,
        postgres_db="novel forge",
    )

    parsed = make_url(settings.database_url)

    assert parsed.username == "writer@example"
    assert parsed.password == "p@ss:/?#%word"
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 55439
    assert parsed.database == "novel forge"
