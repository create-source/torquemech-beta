import json
import time
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


def fetch_all_makes() -> list[str]:
    data = get_json(f"{BASE}/GetAllMakes?format=json")
    makes = sorted({
        normalize_make(row.get("Make_Name", ""))
        for row in data.get("Results", [])
        if normalize_make(row.get("Make_Name", ""))
    })
    return makes


def fetch_models_for_make(make: str) -> list[str]:
    data = get_json(f"{BASE}/GetModelsForMake/{make}?format=json")

    allowed_vehicle_types = {
        "PASSENGER CAR",
        "MULTIPURPOSE PASSENGER VEHICLE",
        "TRUCK",
        "BUS",
        "INCOMPLETE VEHICLE",
    }

    models = set()

    for row in data.get("Results", []):
        vehicle_type = (row.get("VehicleTypeName") or "").upper()
        model = normalize_model(row.get("Model_Name", ""))

        if vehicle_type in allowed_vehicle_types and model:
            models.add(model)

    if len(models) < 2:
        return []

    return sorted(models)


def main():
    makes = fetch_all_makes()
    catalog: dict[str, list[str]] = {}

    print(f"Found {len(makes)} makes")

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

        time.sleep(0.05)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(f"\nSaved {len(catalog)} makes to {OUT}")


if __name__ == "__main__":
    main()