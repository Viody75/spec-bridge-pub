from app.infrastructure.database import normalize_database_url


def test_neon_url_is_normalized_for_asyncpg() -> None:
    url = normalize_database_url(
        "postgresql://user:password@example.test/db"
        "?sslmode=require&channel_binding=require"
    )
    assert url.drivername == "postgresql+asyncpg"
    assert url.query["ssl"] == "require"
    assert "sslmode" not in url.query
    assert "channel_binding" not in url.query
