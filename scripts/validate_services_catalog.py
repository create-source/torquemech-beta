from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = ROOT_DIR / "services_catalog.json"
CODE_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
SEARCH_METADATA_FIELDS = ("aliases", "keywords", "summary", "symptoms")
REVIEWED_DUPLICATE_CONCEPT_ALLOWLIST: dict[frozenset[str], str] = {
    frozenset(("alternator_diagnosis", "alternator_replacement")): "diagnosis versus repair",
    frozenset(("battery_replacement", "battery_test")): "test versus replacement",
    frozenset(("front_brake_pads_replacement", "rear_brake_pads_replacement")): "legitimate location variant",
    frozenset(("front_brake_pads_and_rotors_replacement", "rear_brake_pads_and_rotors_replacement")): "legitimate location variant",
    frozenset(("front_brake_rotors_replacement", "rear_brake_rotors_replacement")): "legitimate location variant",
    frozenset(("bumper_cover_replacement_front", "bumper_cover_replacement_rear")): "legitimate location variant",
    frozenset(("front_diff_service_fluid_inspect", "rear_diff_service_fluid_inspect")): "legitimate location variant",
    frozenset(("front_differential_replacement", "rear_differential_replacement")): "legitimate location variant",
    frozenset(("oxygen_sensor_replacement_upstream", "oxygen_sensor_replacement_downstream")): "legitimate location variant",
    frozenset(("upper_radiator_hose_replacement", "lower_radiator_hose_replacement")): "legitimate location variant",
    frozenset(("starter_diagnosis", "starter_replacement")): "diagnosis versus repair",
    frozenset(("throttle_body_replacement", "throttle_body_service")): "service versus replacement",
    frozenset(("transmission_diagnostic", "transmission_replacement")): "diagnosis versus repair",
    frozenset(("wheel_bearing_replacement_front", "wheel_bearing_replacement_rear")): "legitimate location variant",
}


@dataclass
class ValidationIssue:
    message: str
    location: str = ""

    def format(self) -> str:
        return f"{self.location}: {self.message}" if self.location else self.message


@dataclass
class ValidationResult:
    categories: int = 0
    services: int = 0
    services_with_search_metadata: int = 0
    services_without_search_metadata: int = 0
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> Any:
    with path.open("r", encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


def normalize_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def concept_key(value: Any) -> str:
    normalized = normalize_name(value)
    normalized = re.sub(
        r"\b(front|rear|left|right|upper|lower|upstream|downstream|each|pair|set|per|tire|if|applicable)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"\b(replacement|service|diagnosis|diagnostic|inspection|testing|test|calibration)\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def has_search_metadata(service: dict[str, Any]) -> bool:
    for field_name in SEARCH_METADATA_FIELDS:
        value = service.get(field_name)
        if field_name == "summary":
            if isinstance(value, str) and value.strip():
                return True
        elif isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value):
            return True
    return False


def service_location(category_key: Any, index: int, code: Any = "") -> str:
    code_text = str(code or "").strip()
    suffix = f".{code_text}" if code_text else f"[{index}]"
    return f"category {category_key or '<missing>'} service{suffix}"


def validate_string_array(
    result: ValidationResult,
    service: dict[str, Any],
    field_name: str,
    location: str,
) -> list[str]:
    value = service.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        result.errors.append(ValidationIssue(f"`{field_name}` must be an array of nonempty strings when present", location))
        return []
    strings: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            result.errors.append(ValidationIssue(f"`{field_name}[{idx}]` must be a nonempty string", location))
            continue
        strings.append(item.strip())
    return strings


def validate_labor_number(result: ValidationResult, service: dict[str, Any], field_name: str, location: str) -> float | None:
    value = service.get(field_name)
    if value is None or value == "":
        result.errors.append(ValidationIssue(f"`{field_name}` is required", location))
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        result.errors.append(ValidationIssue(f"`{field_name}` must be numeric", location))
        return None
    numeric = float(value)
    if numeric <= 0:
        result.errors.append(ValidationIssue(f"`{field_name}` must be greater than zero", location))
    return numeric


def add_suffix_warnings(result: ValidationResult, service: dict[str, Any], location: str) -> None:
    code = str(service.get("code") or "")
    name = str(service.get("name") or "")
    name_normalized = normalize_name(name)

    suffix_pairs = (
        ("each", "_each"),
        ("pair", "_pair"),
        ("per tire", "_per_tire"),
        ("if applicable", "_if_applicable"),
    )
    for name_term, code_suffix in suffix_pairs:
        name_has = f" {name_term} " in f" {name_normalized} "
        code_has = code.endswith(code_suffix)
        if name_has != code_has:
            result.warnings.append(
                ValidationIssue(
                    f"inconsistent suffix convention for `{name_term}` between code `{code}` and name `{name}`",
                    location,
                )
            )


def validate_catalog_data(catalog: Any) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(catalog, dict):
        result.errors.append(ValidationIssue("catalog must be a JSON object"))
        return result

    categories = catalog.get("categories")
    if not isinstance(categories, list):
        result.errors.append(ValidationIssue("catalog must include `categories` as an array"))
        return result

    result.categories = len(categories)
    category_keys: Counter[str] = Counter()
    service_codes: Counter[str] = Counter()
    normalized_names: defaultdict[str, list[str]] = defaultdict(list)
    concept_services: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    canonical_names: dict[str, str] = {}
    alias_map: defaultdict[str, list[str]] = defaultdict(list)

    for category_index, category in enumerate(categories):
        category_location = f"category[{category_index}]"
        if not isinstance(category, dict):
            result.errors.append(ValidationIssue("category must be an object", category_location))
            continue

        category_key = str(category.get("key") or "").strip()
        category_name = str(category.get("name") or "").strip()
        if not category_key:
            result.errors.append(ValidationIssue("category `key` is required", category_location))
        else:
            category_keys[category_key] += 1

        if not category_name:
            result.errors.append(ValidationIssue("category `name` is required", category_location))

        services = category.get("services")
        if not isinstance(services, list):
            result.errors.append(ValidationIssue("category `services` must be an array", category_location))
            continue

        for service_index, service in enumerate(services):
            result.services += 1
            if not isinstance(service, dict):
                result.errors.append(ValidationIssue("service must be an object", f"{category_location}.services[{service_index}]"))
                continue

            code = str(service.get("code") or "").strip()
            name = str(service.get("name") or "").strip()
            location = service_location(category_key, service_index, code)

            if not category_key:
                result.errors.append(ValidationIssue("service must belong to a category with a nonempty key", location))

            if not code:
                result.errors.append(ValidationIssue("service `code` is required", location))
            else:
                service_codes[code] += 1
                if not CODE_RE.fullmatch(code):
                    result.errors.append(ValidationIssue("service `code` must be lowercase snake_case", location))

            if not name:
                result.errors.append(ValidationIssue("service `name` is required", location))
            else:
                normalized = normalize_name(name)
                normalized_names[normalized].append(location)
                canonical_names[normalized] = location
                concept = concept_key(name)
                if concept:
                    concept_services[concept].append((code, name))

            labor_min = validate_labor_number(result, service, "labor_hours_min", location)
            labor_max = validate_labor_number(result, service, "labor_hours_max", location)
            if labor_min is not None and labor_max is not None and labor_max < labor_min:
                result.errors.append(ValidationIssue("`labor_hours_max` must not be less than `labor_hours_min`", location))

            aliases = validate_string_array(result, service, "aliases", location)
            validate_string_array(result, service, "keywords", location)
            validate_string_array(result, service, "symptoms", location)

            summary = service.get("summary")
            if summary is not None and (not isinstance(summary, str) or not summary.strip()):
                result.errors.append(ValidationIssue("`summary` must be a nonempty string when present", location))

            normalized_aliases = [normalize_name(alias) for alias in aliases]
            duplicate_aliases = sorted(alias for alias, count in Counter(normalized_aliases).items() if alias and count > 1)
            for alias in duplicate_aliases:
                result.errors.append(ValidationIssue(f"duplicate alias `{alias}` within service", location))

            for alias in normalized_aliases:
                if alias:
                    alias_map[alias].append(location)

            if has_search_metadata(service):
                result.services_with_search_metadata += 1
            else:
                result.services_without_search_metadata += 1
                result.warnings.append(ValidationIssue("service lacks optional searchable metadata", location))

            add_suffix_warnings(result, service, location)

    for key, count in sorted(category_keys.items()):
        if count > 1:
            result.errors.append(ValidationIssue(f"duplicate category key `{key}` appears {count} times"))

    for code, count in sorted(service_codes.items()):
        if count > 1:
            result.errors.append(ValidationIssue(f"duplicate service code `{code}` appears {count} times"))

    for normalized, locations in sorted(normalized_names.items()):
        if normalized and len(locations) > 1:
            result.errors.append(ValidationIssue(f"duplicate normalized service name `{normalized}` appears at {', '.join(locations)}"))

    for alias, locations in sorted(alias_map.items()):
        canonical_location = canonical_names.get(alias)
        if canonical_location and canonical_location not in locations:
            result.errors.append(
                ValidationIssue(
                    f"alias `{alias}` exactly conflicts with canonical service name at {canonical_location}; used by {', '.join(locations)}"
                )
            )

    for concept, service_items in sorted(concept_services.items()):
        unique_items = sorted(set(service_items))
        unique_codes = frozenset(code for code, _name in unique_items if code)
        if unique_codes in REVIEWED_DUPLICATE_CONCEPT_ALLOWLIST:
            continue
        if concept and len(unique_items) > 1:
            formatted_items = [f"{name} ({code})" for code, name in unique_items]
            result.warnings.append(
                ValidationIssue(
                    f"suspicious duplicate concept `{concept}` appears as: {', '.join(formatted_items[:6])}"
                )
            )

    return result


def validate_catalog_file(path: Path = DEFAULT_CATALOG_PATH) -> ValidationResult:
    result = ValidationResult()
    try:
        catalog = load_catalog(path)
    except json.JSONDecodeError as exc:
        result.errors.append(ValidationIssue(f"invalid JSON: {exc}"))
        return result
    except OSError as exc:
        result.errors.append(ValidationIssue(f"unable to read catalog: {exc}"))
        return result
    return validate_catalog_data(catalog)


def print_result(result: ValidationResult) -> None:
    print("Services catalog validation")
    print(f"Categories: {result.categories}")
    print(f"Services: {result.services}")
    print(f"Errors: {len(result.errors)}")
    print(f"Warnings: {len(result.warnings)}")
    print(f"Services with searchable metadata: {result.services_with_search_metadata}")
    print(f"Services without searchable metadata: {result.services_without_search_metadata}")

    if result.errors:
        print("\nErrors:")
        for issue in result.errors:
            print(f"- {issue.format()}")

    if result.warnings:
        print("\nWarnings:")
        for issue in result.warnings:
            print(f"- {issue.format()}")

    print("\nResult: valid" if result.valid else "\nResult: invalid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate TorqueMech services_catalog.json.")
    parser.add_argument(
        "catalog",
        nargs="?",
        default=str(DEFAULT_CATALOG_PATH),
        help="Path to services_catalog.json. Defaults to the project root catalog.",
    )
    args = parser.parse_args(argv)

    result = validate_catalog_file(Path(args.catalog))
    print_result(result)
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main())
