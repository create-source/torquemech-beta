from typing import Dict, List, Any


LABOR_BREAKDOWN_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "spark_plug": [
        {"label": "Access ignition coils or plug wires", "weight": 0.25},
        {"label": "Remove plugs and inspect wells and threads", "weight": 0.25},
        {"label": "Install plugs to proper torque and spec", "weight": 0.30},
        {"label": "Reassemble and verify smooth operation", "weight": 0.20},
    ],
    "ignition_coil": [
        {"label": "Confirm cylinder location and access coil", "weight": 0.20},
        {"label": "Inspect connector, boot, and plug well", "weight": 0.25},
        {"label": "Replace coil and transfer seals if needed", "weight": 0.30},
        {"label": "Clear codes and verify misfire data", "weight": 0.25},
    ],
    "brakes": [
        {"label": "Wheel removal", "weight": 0.15},
        {"label": "Brake component access", "weight": 0.20},
        {"label": "Remove and install parts", "weight": 0.40},
        {"label": "Reassembly and safety check", "weight": 0.25},
    ],
    "brake_pad": [
        {"label": "Lift vehicle and remove wheels", "weight": 0.15},
        {"label": "Inspect pads, caliper slides, and hardware", "weight": 0.20},
        {"label": "Replace pads and service contact points", "weight": 0.40},
        {"label": "Reassemble, torque wheels, and verify pedal feel", "weight": 0.25},
    ],
    "electrical": [
        {"label": "Battery disconnect and prep", "weight": 0.10},
        {"label": "Component access", "weight": 0.25},
        {"label": "Disconnect and reconnect wiring", "weight": 0.30},
        {"label": "Install and verification", "weight": 0.35},
    ],
    "alternator": [
        {"label": "Disconnect battery and inspect belt routing", "weight": 0.15},
        {"label": "Access alternator and electrical connections", "weight": 0.25},
        {"label": "Replace alternator and set belt tension path", "weight": 0.35},
        {"label": "Verify charging output and warning lights", "weight": 0.25},
    ],
    "starter": [
        {"label": "Disconnect battery and confirm starter access", "weight": 0.15},
        {"label": "Inspect main cable, trigger wire, and mounting", "weight": 0.25},
        {"label": "Replace starter and secure connections", "weight": 0.35},
        {"label": "Verify crank speed and no-start symptoms", "weight": 0.25},
    ],
    "cooling": [
        {"label": "Component access", "weight": 0.20},
        {"label": "Drain and manage fluids", "weight": 0.20},
        {"label": "Remove and install parts", "weight": 0.35},
        {"label": "Refill, bleed, and verify", "weight": 0.25},
    ],
    "water_pump": [
        {"label": "Access pump, belt drive, and cooling hoses", "weight": 0.20},
        {"label": "Drain coolant and inspect leak path", "weight": 0.20},
        {"label": "Replace pump and clean sealing surface", "weight": 0.35},
        {"label": "Refill, bleed, and verify temperature control", "weight": 0.25},
    ],
    "thermostat": [
        {"label": "Access thermostat housing and hose connections", "weight": 0.20},
        {"label": "Drain coolant to service level", "weight": 0.20},
        {"label": "Replace thermostat and housing seal", "weight": 0.35},
        {"label": "Refill, bleed, and verify warm-up behavior", "weight": 0.25},
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
    "valve_cover_gasket": [
        {"label": "Access valve cover and protect surrounding components", "weight": 0.25},
        {"label": "Disconnect coils, hoses, and harness retainers", "weight": 0.20},
        {"label": "Replace gasket and clean sealing surfaces", "weight": 0.35},
        {"label": "Reassemble and inspect for oil leaks", "weight": 0.20},
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
        "template": "brake_pad",
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
        "template": "brake_pad",
        "labor_hours": {"min": 1.0, "avg": 1.5, "max": 2.5},
    },

    "rear_brake_pad_replacement": {
        "display_name": "Rear Brake Pad Replacement",
        "template": "brake_pad",
        "labor_hours": {"min": 1.0, "avg": 1.5, "max": 2.5},
    },
    "front_brake_pads_replacement": {
        "display_name": "Front Brake Pads Replacement",
        "template": "brake_pad",
        "labor_hours": {"min": 1.0, "avg": 1.5, "max": 2.5},
    },
    "rear_brake_pads_replacement": {
        "display_name": "Rear Brake Pads Replacement",
        "template": "brake_pad",
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
        "template": "alternator",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 3.5},
    },
    "starter_replacement": {
        "display_name": "Starter Replacement",
        "template": "starter",
        "labor_hours": {"min": 1.4, "avg": 2.3, "max": 4.0},
    },
    "water_pump_replacement": {
        "display_name": "Water Pump Replacement",
        "template": "water_pump",
        "labor_hours": {"min": 2.0, "avg": 3.5, "max": 6.0},
    },
    "strut_replacement": {
        "display_name": "Strut Replacement",
        "template": "suspension",
        "labor_hours": {"min": 1.5, "avg": 2.5, "max": 4.5},
    },
    "spark_plug_replacement": {
        "display_name": "Spark Plug Replacement",
        "template": "spark_plug",
        "labor_hours": {"min": 1.0, "avg": 2.0, "max": 5.0},
    },
    "spark_plug_replacement_4_cyl": {
        "display_name": "Spark Plug Replacement (4-Cyl)",
        "template": "spark_plug",
        "labor_hours": {"min": 1.0, "avg": 1.8, "max": 4.0},
    },
    "spark_plug_replacement_v6_v8": {
        "display_name": "Spark Plug Replacement (V6/V8)",
        "template": "spark_plug",
        "labor_hours": {"min": 1.0, "avg": 2.5, "max": 5.0},
    },
    "ignition_coil_replacement_each": {
        "display_name": "Ignition Coil Replacement (each)",
        "template": "ignition_coil",
        "labor_hours": {"min": 0.5, "avg": 1.2, "max": 3.0},
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
        "template": "valve_cover_gasket",
        "labor_hours": {"min": 1.5, "avg": 2.5, "max": 4.0},
    },
    "thermostat_replacement": {
        "display_name": "Thermostat Replacement",
        "template": "thermostat",
        "labor_hours": {"min": 1.0, "avg": 2.0, "max": 4.0},
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

    
