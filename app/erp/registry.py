from collections.abc import Callable

from app.erp.adapter import ERPExportAdapter


AdapterFactory = Callable[[], ERPExportAdapter]


class ERPAdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, provider_name: str, factory: AdapterFactory) -> None:
        normalized_name = provider_name.strip().lower()
        if not normalized_name:
            raise ValueError("ERP sağlayıcı adı boş olamaz.")
        if normalized_name in self._factories:
            raise ValueError(f"ERP sağlayıcısı zaten kayıtlı: {normalized_name}")
        self._factories[normalized_name] = factory

    def create(self, provider_name: str) -> ERPExportAdapter:
        normalized_name = provider_name.strip().lower()
        factory = self._factories.get(normalized_name)
        if factory is None:
            raise LookupError(f"Desteklenmeyen ERP sağlayıcısı: {provider_name}")
        return factory()

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


erp_registry = ERPAdapterRegistry()
