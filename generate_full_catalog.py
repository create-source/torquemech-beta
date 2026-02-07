import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "services_catalog.json"
DST = BASE / "services_catalog_full.json"

# Add more “templates” here anytime
EXTRA_CATEGORIES = [
    ("fuel_ignition", "Fuel & Ignition", [
        ("fuel_pump", "Fuel Pump Replacement", 2.0, 6.0, 180, 750),
        ("injector_clean", "Fuel Injector Cleaning", 1.0, 2.5, 20, 80),
        ("ignition_coil", "Ignition Coil (each)", 0.2, 0.8, 35, 180),
        ("maf_sensor", "MAF Sensor Replacement", 0.2, 0.6, 60, 220),
        ("o2_sensor", "O2 Sensor (each)", 0.4, 1.2, 45, 260),
    ]),
    ("exhaust", "Exhaust", [
        ("muffler", "Muffler Replacement", 1.0, 2.5, 120, 650),
        ("cat_convert", "Catalytic Converter", 1.0, 3.5, 350, 2200),
        ("exhaust_leak_diag", "Exhaust Leak Diagnostic", 1.0, 2.0, 0, 0),
        ("gasket_exhaust", "Exhaust Gasket (each)", 0.8, 2.5, 10, 70),
        ("resonator", "Resonator Replacement", 1.0, 2.5, 120, 650),
    ]),
    ("drivetrain", "Drivetrain / Axles", [
        ("cv_axle", "CV Axle (each)", 1.2, 3.5, 120, 650),
        ("wheel_bearing", "Wheel Bearing / Hub (each)", 1.5, 4.0, 120, 650),
        ("u_joint", "U-Joint (each)", 1.0, 2.5, 25, 120),
        ("driveshaft", "Driveshaft Repair", 1.0, 3.0, 100, 600),
        ("diff_reseal", "Differential Reseal", 3.0, 7.0, 25, 120),
    ]),
    ("inspection", "Inspection", [
        ("pre_purchase", "Pre-Purchase Inspection", 1.0, 2.0, 0, 0),
        ("brake_inspection", "Brake Inspection", 0.5, 1.0, 0, 0),
        ("cooling_pressure_test", "Cooling Pressure Test", 0.8, 1.5, 0, 0),
        ("battery_test", "Battery / Charging Test", 0.4, 0.8, 0, 0),
        ("check_engine_diag", "Check Engine Light Diagnostic", 1.0, 2.0, 0, 0),
    ]),
]

def add_category(doc, key, name, services):
    doc["categories"].append({
        "key": key,
        "name": name,
        "services": [
            {
                "code": code,
                "name": sname,
                "labor_low": float(ll),
                "labor_high": float(lh),
                "parts_low": float(pl),
                "parts_high": float(ph),
            }
            for (code, sname, ll, lh, pl, ph) in services
        ]
    })

def main():
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    existing = {c["key"] for c in doc.get("categories", [])}

    for key, name, services in EXTRA_CATEGORIES:
        if key not in existing:
            add_category(doc, key, name, services)

    DST.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Wrote: {DST}")

if __name__ == "__main__":
    main()
