import json
import time
import re
from pathlib import Path

import requests

BASE = "https://vpic.nhtsa.dot.gov/api/vehicles"
OUT = Path("data/vehicle_catalog.json")
TIMEOUT = 30


def get_json(url: str):
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def normalize_make(name: str) -> str:
    return " ".join((name or "").strip().upper().split())


def normalize_model(name: str) -> str:
    return " ".join((name or "").strip().upper().split())


def looks_like_real_vehicle_make(make: str) -> bool:
    if not make:
        return False

    make = " ".join(make.strip().upper().split())

    if len(make) < 3:
        return False

    if make.isdigit():
        return False

    if make[0].isdigit():
        return False

    digit_count = sum(ch.isdigit() for ch in make)
    if digit_count >= 3:
        return False

    skip_terms = {
        "TRAILER", "TRAILERS", "FABRICATION", "INDUSTRIES", "INDUSTRIAL",
        "EQUIPMENT", "MANUFACTURING", "MFG", "COACH", "MOTORCYCLE",
        "SCOOTER", "BICYCLE", "POWERSPORTS", "MARINE", "BOAT",
        "RV", "CAMPER", "FIRE", "EMERGENCY", "AMBULANCE",
        "AGRICULTURAL", "TRACTOR", "FORKLIFT", "LOW SPEED", "OFF ROAD",
        "CUSTOM", "WELDING", "FAB", "CHASSIS", "BODY", "TOOL",
        "MACHINE", "MACHINING", "CART", "CARTS", "GO KART", "GO-CART",
        "MESSAGE SYSTEMS", "ENTERPRISE", "DYNAMIC", "CONSTRUCTION"
    }

    if any(term in make for term in skip_terms):
        return False

    words = make.split()
    if len(words) > 4:
        return False

    return True

def fetch_all_makes() -> list[str]:
    vehicle_types = [
        "car",
        "multipurpose passenger vehicle",
        "truck",
        "bus",
        "incomplete vehicle",
    ]

    makes = set()

    for vehicle_type in vehicle_types:
        data = get_json(f"{BASE}/GetMakesForVehicleType/{vehicle_type}?format=json")

        for row in data.get("Results", []):
            make = normalize_make(row.get("MakeName", ""))
            if make and looks_like_real_vehicle_make(make):
                makes.add(make)

        time.sleep(0.02)

    return sorted(makes)


def fetch_models_for_make(make: str) -> list[str]:
    vehicle_types = [
        "car",
        "multipurpose passenger vehicle",
        "truck",
        "bus",
        "incomplete vehicle",
    ]

    models = set()

    for vehicle_type in vehicle_types:
        data = get_json(
            f"{BASE}/GetModelsForMakeYear/make/{make}/vehicletype/{vehicle_type}?format=json"
        )

        for row in data.get("Results", []):
            model = normalize_model(row.get("Model_Name", ""))
            if model:
                models.add(model)

        time.sleep(0.02)

    if len(models) < 2:
        return []

    return sorted(models)


def main():
    makes = fetch_all_makes()
    catalog = {}

    print(f"Filtered to {len(makes)} candidate makes")

    for idx, make in enumerate(makes, start=1):

        try:
            models = fetch_models_for_make(make)

            if not models:
                print(f"[{idx}/{len(makes)}] {make}: skipped")
                continue

            catalog[make] = models
            print(f"[{idx}/{len(makes)}] {make}: {len(models)} models")

        except Exception as e:
            print(f"[{idx}/{len(makes)}] FAILED {make}: {e}")

        time.sleep(0.03)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    print(f"\nSaved {len(catalog)} makes to {OUT}")


if __name__ == "__main__":
    main()