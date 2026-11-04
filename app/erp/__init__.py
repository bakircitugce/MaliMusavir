from app.erp.adapter import (
    ERPExportAdapter,
    ERPExportDocument,
    ERPExportResult,
)
from app.erp.fake import InMemoryERPExportAdapter
from app.erp.logo import LogoTigerERPExportAdapter
from app.erp.universal_csv import UniversalCSVERPExportAdapter
from app.erp.export_service import export_approved_entry
from app.erp.retry import retry_export
from app.erp.registry import ERPAdapterRegistry, erp_registry
from app.erp.tracking import (
    get_export_record,
    get_or_create_export,
    list_export_records,
    record_export_failure,
    record_export_result,
)

# Global erp_registry'ye standart adaptörleri kaydet
if "fake" not in erp_registry.providers():
    erp_registry.register("fake", InMemoryERPExportAdapter)
if "logo" not in erp_registry.providers():
    erp_registry.register("logo", LogoTigerERPExportAdapter)
if "universal_csv" not in erp_registry.providers():
    erp_registry.register("universal_csv", UniversalCSVERPExportAdapter)

__all__ = [
    "ERPExportAdapter",
    "ERPExportDocument",
    "ERPExportResult",
    "InMemoryERPExportAdapter",
    "LogoTigerERPExportAdapter",
    "UniversalCSVERPExportAdapter",
    "export_approved_entry",
    "retry_export",
    "ERPAdapterRegistry",
    "erp_registry",
    "get_export_record",
    "record_export_result",
    "get_or_create_export",
    "record_export_failure",
    "list_export_records",
]
