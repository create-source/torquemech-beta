from __future__ import annotations

import json
from pathlib import Path

import pytest

import main


ROOT = Path(__file__).resolve().parents[1]
EDUCATION_PATH = ROOT / "data" / "service_education.json"


def load_education_file() -> dict:
    return json.loads(EDUCATION_PATH.read_text(encoding="utf-8-sig"))


def test_service_education_file_exists_and_is_valid_json() -> None:
    assert EDUCATION_PATH.exists(), "data/service_education.json must exist"

    data = load_education_file()

    assert data["version"] == "1.0.0"
    assert isinstance(data["services"], dict)
    assert data["services"]


@pytest.mark.parametrize(
    "service_code",
    [
        "oil_and_filter_change",
        "front_brake_pads_replacement",
        "battery_replacement",
        "alternator_replacement",
        "spark_plug_replacement_4_cyl",
        "thermostat_replacement",
        "water_pump_replacement",
        "radiator_replacement",
        "tire_rotation",
        "cabin_air_filter_replacement",
    ],
)
def test_representative_service_has_structured_education(
    service_code: str,
) -> None:
    services = load_education_file()["services"]

    assert service_code in services

    education = services[service_code]

    assert education["title"].strip()
    assert education["summary"].strip()
    assert education["delay_risk"].strip()
    assert education["customer_note"].strip()

    assert isinstance(education["symptoms"], list)
    assert education["symptoms"]
    assert all(str(item).strip() for item in education["symptoms"])


def test_service_education_codes_exist_in_frozen_catalog() -> None:
    services = load_education_file()["services"]

    missing_codes = [
        service_code
        for service_code in services
        if main.find_service_by_code(service_code) is None
    ]

    assert missing_codes == []


def test_estimate_pdf_request_defaults_service_education_off() -> None:
    request_model = main.MultiPDFRequest(
        year=2020,
        make="Toyota",
        model="Camry",
        lineItems=[],
    )

    assert request_model.includeServiceEducation is False


def test_supported_service_uses_structured_education() -> None:
    education = main.estimate_service_education(
        "front_brake_pads_replacement"
    )

    assert education["title"] == (
        "Understanding Your Front Brake Pad Service"
    )
    assert "Brake pads press against" in education["summary"]
    assert education["symptoms"]
    assert "stopping distance" in education["delay_risk"]


def test_unknown_service_returns_no_structured_education() -> None:
    education = main.estimate_service_education(
        "service_code_that_does_not_exist"
    )

    assert education == {}


def test_pdf_source_only_adds_education_when_option_is_enabled() -> None:
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "if req.includeServiceEducation:" in main_source
    assert (
        "education = estimate_service_education(it.serviceCode)"
        in main_source
    )


def test_pdf_source_does_not_enable_education_by_default() -> None:
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "includeServiceEducation: bool = False" in main_source
    assert "includeServiceEducation: bool = True" not in main_source