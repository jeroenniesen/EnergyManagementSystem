"""Read-only warnings for tariff assumptions that can distort savings reports."""
from __future__ import annotations

from dataclasses import dataclass

from ems.tariffs import TariffPolicy


@dataclass(frozen=True)
class TariffWarning:
    code: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


def validate_tariff_policy(
    policy: TariffPolicy, *, export_model: str = "net_metering"
) -> list[TariffWarning]:
    warnings: list[TariffWarning] = []
    if export_model == "net_metering" and policy.export_fee_eur_per_kwh:
        warnings.append(TariffWarning(
            "export_fee_with_net_metering", "warning",
            "An export fee is configured while net-metering is active; verify the supplier tariff.",
        ))
    if export_model != "net_metering" and policy.export_fee_eur_per_kwh == 0.0:
        warnings.append(TariffWarning(
            "missing_export_fee", "info",
            "Post-2027 export valuation has no export fee configured; check the supplier contract.",
        ))
    if not policy.tibber_total_includes_all and policy.import_fee_eur_per_kwh == 0.0:
        warnings.append(TariffWarning(
            "missing_import_fee", "info",
            "Provider total is marked incomplete but no import fee is configured; savings may be "
            "understated.",
        ))
    return warnings
