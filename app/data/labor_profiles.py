from typing import Dict, List, Any


LABOR_BREAKDOWN_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "brakes": [
        {"label": "Wheel removal", "weight": 0.15},
        {"label": "Brake component access", "weight": 0.20},
        {"label": "Remove and install parts", "weight": 0.40},
        {"label": "Reassembly and safety check", "weight": 0.25},
    ],
    "electrical": [
        {"label": "Battery disconnect and prep", "weight": 0.10},
        {"label": "Component access", "weight": 0.25},
        {"label": "Disconnect and reconnect wiring", "weight": 0.30},
        {"label": "Install and verification", "weight": 0.35},
    ],
    "cooling": [
        {"label": "Component access", "weight": 0.20},
        {"label": "Drain and manage fluids", "weight": 0.20},
        {"label": "Remove and install parts", "weight": 0.35},
        {"label": "Refill, bleed, and verify", "weight": 0.25},
    ],
    "suspension": [
        {"label": "Wheel removal", "weight": 0.15},
        {"label": "Component removal", "weight": 0.35},
        {"label": "Install and torque", "weight": 0.30},
        {"label": "Final inspection", "weight": 0.20},
    ],
    "engine_minor": [
        {"label": "Component access", "weight": 0.25},
        {"label": "Disconnect surrounding parts", "weight": 0.20},
        {"label": "Remove and install parts", "weight": 0.35},
        {"label": "Reassembly and verification", "weight": 0.20},
    ],
    "engine_major": [
        {"label": "Prep and system disconnection", "weight": 0.15},
        {"label": "Accessory and support removal", "weight": 0.25},
        {"label": "Major assembly removal/install", "weight": 0.35},
        {"label": "Reassembly, fluids, and final checks", "weight": 0.25},
    ],
}


LABOR_SERVICE_PROFILES: Dict[str, Dict[str, Any]] = {
    "brake_pad_replacement": {
        "display_name": "Brake Pad Replacement",
        "template": "brakes",
        "labor_hours": {"min": 1.0, "avg": 1.8, "max": 3.0},
    },
    "brake_rotor_replacement": {
        "display_name": "Brake Rotor Replacement",
        "template": "brakes",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 3.2},
    },
    "front_brake_pads_and_rotors_replacement": {
        "display_name": "Front Brake Pads & Rotors Replacement",
        "template": "brakes",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 3.2},
    },

    "rear_brake_pads_and_rotors_replacement": {
        "display_name": "Rear Brake Pads & Rotors Replacement",
        "template": "brakes",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 3.2},
    },

    "front_brake_pad_replacement": {
        "display_name": "Front Brake Pad Replacement",
        "template": "brakes",
        "labor_hours": {"min": 1.0, "avg": 1.5, "max": 2.5},
    },

    "rear_brake_pad_replacement": {
        "display_name": "Rear Brake Pad Replacement",
        "template": "brakes",
        "labor_hours": {"min": 1.0, "avg": 1.5, "max": 2.5},
    },

    "battery_replacement": {
        "display_name": "Battery Replacement",
        "template": "electrical",
        "labor_hours": {"min": 0.3, "avg": 0.5, "max": 1.0},
    },

    "radiator_replacement": {
        "display_name": "Radiator Replacement",
        "template": "cooling",
        "labor_hours": {"min": 2.0, "avg": 3.0, "max": 5.0},
    },

    "control_arm_replacement": {
        "display_name": "Control Arm Replacement",
        "template": "suspension",
        "labor_hours": {"min": 1.5, "avg": 2.5, "max": 4.0},
    },
    "alternator_replacement": {
        "display_name": "Alternator Replacement",
        "template": "electrical",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 3.5},
    },
    "starter_replacement": {
        "display_name": "Starter Replacement",
        "template": "electrical",
        "labor_hours": {"min": 1.4, "avg": 2.3, "max": 4.0},
    },
    "water_pump_replacement": {
        "display_name": "Water Pump Replacement",
        "template": "cooling",
        "labor_hours": {"min": 2.0, "avg": 3.5, "max": 6.0},
    },
    "strut_replacement": {
        "display_name": "Strut Replacement",
        "template": "suspension",
        "labor_hours": {"min": 1.5, "avg": 2.5, "max": 4.5},
    },
    "spark_plug_replacement": {
        "display_name": "Spark Plug Replacement",
        "template": "engine_minor",
        "labor_hours": {"min": 1.0, "avg": 2.0, "max": 5.0},
    },
    "engine_replacement": {
        "display_name": "Engine Replacement",
        "template": "engine_major",
        "labor_hours": {"min": 15.0, "avg": 22.0, "max": 30.0},
    },

    "head_gasket_replacement": {
        "display_name": "Head Gasket Replacement",
        "template": "engine_major",
        "labor_hours": {"min": 8.0, "avg": 12.0, "max": 18.0},
    },

    "valve_cover_gasket_replacement": {
        "display_name": "Valve Cover Gasket Replacement",
        "template": "engine_minor",
        "labor_hours": {"min": 1.5, "avg": 2.5, "max": 4.0},
    },
}

CATEGORY_TEMPLATE_MAP = {
    "maintenance": "engine_minor",
    "brakes": "brakes",
    "engine": "engine_minor",
    "cooling": "cooling",
    "electrical": "electrical",
    "suspension": "suspension",
    "exhaust": "engine_minor",
    "fuel": "engine_minor",
    "transmission": "engine_major",
    "ac_heating": "cooling",
    "default": "engine_minor",
}

def round_hours(value: float) -> float:
    return round(value, 1)


def get_service_labor_profile(
    service_key: str,
    *,
    display_name: str | None = None,
    category_key: str | None = None,
    labor_min: float | None = None,
    labor_max: float | None = None,
) -> Dict[str, Any] | None:
    profile = LABOR_SERVICE_PROFILES.get(service_key)
    if profile:
        return profile

    if display_name:
        return build_generated_profile(
            service_key,
            display_name=display_name,
            category_key=category_key,
            labor_min=float(labor_min or 0),
            labor_max=float(labor_max or 0),
        )

    return None

def template_for_category(category_key: str | None) -> str:
    key = (category_key or "default").strip().lower()
    return CATEGORY_TEMPLATE_MAP.get(key, CATEGORY_TEMPLATE_MAP["default"])


def build_generated_profile(
    service_key: str,
    *,
    display_name: str,
    category_key: str | None,
    labor_min: float,
    labor_max: float,
) -> Dict[str, Any]:
    labor_min = float(labor_min or 0)
    labor_max = float(labor_max or 0)

    if labor_max <= 0:
        labor_max = max(labor_min, 2.0)

    if labor_min <= 0:
        labor_min = max(0.5, labor_max * 0.5)

    if labor_max < labor_min:
        labor_max = labor_min

    labor_avg = round((labor_min + labor_max) / 2.0, 1)

    return {
        "display_name": display_name,
        "template": template_for_category(category_key),
        "labor_hours": {
            "min": round(labor_min, 1),
            "avg": labor_avg,
            "max": round(labor_max, 1),
        },
    }


def get_breakdown_template(template_key: str) -> List[Dict[str, Any]]:
    return LABOR_BREAKDOWN_TEMPLATES.get(template_key, [])


def clamp_selected_hours(selected: float, min_hours: float, max_hours: float) -> float:
    return float(selected)


def build_labor_breakdown(
    service_key: str,
    selected_hours: float | None = None,
    *,
    display_name: str | None = None,
    category_key: str | None = None,
    labor_min: float | None = None,
    labor_max: float | None = None,
) -> Dict[str, Any] | None:
    profile = get_service_labor_profile(
        service_key,
        display_name=display_name,
        category_key=category_key,
        labor_min=labor_min,
        labor_max=labor_max,
    )
    if not profile:
        return None

    labor = profile["labor_hours"]
    min_hours = float(labor["min"])
    avg_hours = float(labor["avg"])
    max_hours = float(labor["max"])

    if selected_hours is None:
        selected_hours = avg_hours

    selected_hours = clamp_selected_hours(float(selected_hours), min_hours, max_hours)

    template_key = profile["template"]
    template_steps = get_breakdown_template(template_key)

    steps = []
    for step in template_steps:
        step_hours = round_hours(selected_hours * float(step["weight"]))
        steps.append({
            "label": step["label"],
            "weight": step["weight"],
            "hours": step_hours,
        })

    return {
        "service_key": service_key,
        "display_name": profile["display_name"],
        "template": template_key,
        "labor_hours": {
            "min": min_hours,
            "avg": avg_hours,
            "max": max_hours,
            "selected": round_hours(selected_hours),
        },
        "steps": steps,
        "disclaimer": (
            "This shows how labor time is typically distributed for this service. "
            "Actual time may vary depending on vehicle condition and access."
        ),
    }

    