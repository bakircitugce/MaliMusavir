import pytest

from app.erp import ERPAdapterRegistry


class DummyAdapter:
    async def export_entry(self, document: object) -> object:
        return document


def test_erp_registry_normalizes_provider_names() -> None:
    registry = ERPAdapterRegistry()
    registry.register("  Demo ERP  ", DummyAdapter)

    assert isinstance(registry.create("demo erp"), DummyAdapter)
    assert registry.providers() == ("demo erp",)


def test_erp_registry_rejects_unknown_provider() -> None:
    registry = ERPAdapterRegistry()

    with pytest.raises(LookupError, match="Desteklenmeyen ERP"):
        registry.create("luca")


def test_erp_registry_rejects_duplicate_provider() -> None:
    registry = ERPAdapterRegistry()
    registry.register("demo", DummyAdapter)

    with pytest.raises(ValueError, match="zaten kayıtlı"):
        registry.register("DEMO", DummyAdapter)
