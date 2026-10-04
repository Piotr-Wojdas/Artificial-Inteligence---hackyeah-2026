"""Economic and financial profitability estimation for photovoltaic installations in Poland.

Real-world pricing and regulatory model (as of 2024-2026):
  1. Turnkey CAPEX (koszt montażu i osprzętu z 8% VAT dla budynków mieszkalnych):
     - mikroinstalacje 2-4 kWp: ~4 800 zł/kWp
     - 4-7 kWp: ~4 200 zł/kWp
     - 7-12 kWp: ~3 700 zł/kWp
     - 12-20 kWp: ~3 300 zł/kWp
     - > 20 kWp: ~3 000 zł/kWp
  2. Ulga termomodernizacyjna w podatku PIT (12% odliczenia od kosztów inwestycji)
  3. Koszty eksploatacji (OPEX): ~0.8% wartości instalacji rocznie (przeglądy, ubezpieczenie, bufor na falownik)
  4. Ceny energii:
     - Cena zakupu z sieci (taryfa G11 z opłatami dystrybucyjnymi): ~1,10 zł / kWh brutto
     - Rynkowa cena energii w net-billingu (RCEm / TGE): ~0,40 zł / kWh
  5. Autokonsumpcja:
     - Domyślnie 25% (typowy dom bez magazynu)
     - Uniknięty koszt zakupu = 1,10 zł za każdą kWh zużytą na bieżąco
"""

from __future__ import annotations


def estimate_capex_pln(kwp: float) -> float:
    """Estimated gross turnkey installation cost in PLN for a residential building (8% VAT)."""
    if kwp <= 4.0:
        rate = 4800.0
    elif kwp <= 7.0:
        rate = 4200.0
    elif kwp <= 12.0:
        rate = 3700.0
    elif kwp <= 20.0:
        rate = 3300.0
    else:
        rate = 3000.0
    return round(kwp * rate, 0)


def estimate_economics(kwp: float, kwh_year: float,
                       retail_price_pln: float = 1.10,
                       feed_in_price_pln: float = 0.40,
                       self_consumption_pct: float = 25.0) -> dict:
    """Computes a complete, realistic financial profitability report for the installation."""
    kwp = max(0.1, float(kwp))
    kwh_year = max(0.0, float(kwh_year))
    retail_price_pln = max(0.0, float(retail_price_pln))
    feed_in_price_pln = max(0.0, float(feed_in_price_pln))
    self_consumption_pct = min(100.0, max(0.0, float(self_consumption_pct)))

    capex_gross = estimate_capex_pln(kwp)
    # Ulga termomodernizacyjna (12% zwrotu w zeznaniu PIT)
    pit_relief_pln = round(capex_gross * 0.12, 0)
    capex_net = round(capex_gross - pit_relief_pln, 0)

    # Roczny OPEX (serwis, mycie, ubezpieczenie, rezerwa na inwerter)
    annual_opex_pln = round(max(150.0, capex_gross * 0.008), 0)

    # Rozdział energii
    self_ratio = self_consumption_pct / 100.0
    self_kwh = kwh_year * self_ratio
    export_kwh = kwh_year * (1.0 - self_ratio)

    self_savings_pln = round(self_kwh * retail_price_pln, 2)
    export_income_pln = round(export_kwh * feed_in_price_pln, 2)
    gross_savings_annual_pln = round(self_savings_pln + export_income_pln, 2)
    net_savings_annual_pln = round(gross_savings_annual_pln - annual_opex_pln, 2)

    # Okresy zwrotu
    payback_years = round(capex_gross / net_savings_annual_pln, 1) if net_savings_annual_pln > 0 else None
    payback_with_relief_years = round(capex_net / net_savings_annual_pln, 1) if net_savings_annual_pln > 0 else None

    # Skumulowany zysk po 25 latach z uwzględnieniem starzenia paneli (degradacja 0.5% rocznie)
    total_25y_net_savings = sum(net_savings_annual_pln * (1.0 - 0.005 * yr) for yr in range(25))
    profit_25y_pln = round(total_25y_net_savings - capex_gross, 0)

    return {
        "capex_gross_pln": capex_gross,
        "pit_relief_pln": pit_relief_pln,
        "capex_net_pln": capex_net,
        "annual_opex_pln": annual_opex_pln,
        "self_consumption_pct": self_consumption_pct,
        "self_kwh": round(self_kwh, 1),
        "export_kwh": round(export_kwh, 1),
        "self_savings_pln": self_savings_pln,
        "export_income_pln": export_income_pln,
        "gross_savings_annual_pln": gross_savings_annual_pln,
        "net_savings_annual_pln": net_savings_annual_pln,
        "payback_years": payback_years,
        "payback_with_relief_years": payback_with_relief_years,
        "profit_25y_pln": profit_25y_pln,
        "retail_price_pln": retail_price_pln,
        "feed_in_price_pln": feed_in_price_pln,
    }
