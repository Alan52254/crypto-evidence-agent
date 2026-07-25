from hoyabit_agent.storage.postgres import SCHEMA


def test_schema_has_no_unicode_byte_order_mark() -> None:
    assert not SCHEMA.startswith("\ufeff")
