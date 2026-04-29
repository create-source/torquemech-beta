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
    "brake_rotor": [
        {"label": "Lift vehicle and remove wheels", "weight": 0.15},
        {"label": "Access caliper bracket and rotor hardware", "weight": 0.25},
        {"label": "Remove rotor and clean hub mating surface", "weight": 0.35},
        {"label": "Reassemble, torque wheels, and verify brake feel", "weight": 0.25},
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
    "battery": [
        {"label": "Verify battery condition and charging concern", "weight": 0.20},
        {"label": "Access battery, hold-down, and terminals", "weight": 0.25},
        {"label": "Replace battery and clean connections", "weight": 0.30},
        {"label": "Test charging output and reset memory items if needed", "weight": 0.25},
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
    "radiator": [
        {"label": "Confirm leak or cooling restriction and access radiator", "weight": 0.20},
        {"label": "Drain coolant and disconnect hoses, fans, or brackets", "weight": 0.25},
        {"label": "Replace radiator and transfer required hardware", "weight": 0.30},
        {"label": "Refill, bleed, pressure-test, and verify temperature control", "weight": 0.25},
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
    "fuel_pump": [
        {"label": "Confirm fuel pressure and pump command", "weight": 0.20},
        {"label": "Access pump module, tank, lines, or service panel", "weight": 0.30},
        {"label": "Replace pump/module and secure fuel connections", "weight": 0.30},
        {"label": "Prime system, leak-check, and verify pressure under load", "weight": 0.20},
    ],
    "catalytic_converter": [
        {"label": "Confirm catalyst efficiency and affected bank", "weight": 0.20},
        {"label": "Access converter, oxygen sensors, and exhaust hardware", "weight": 0.25},
        {"label": "Remove converter and install emissions-compliant replacement", "weight": 0.35},
        {"label": "Verify downstream O2 behavior and code readiness", "weight": 0.20},
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

LABOR_SERVICE_EXPLANATIONS: Dict[str, str] = {
    "alternator_replacement": "Time varies because the technician confirms charging output, checks belt routing and battery condition, then works around engine-bay access before verifying warning lights and voltage under load.",
    "thermostat_replacement": "Labor includes confirming slow warm-up or P0128 behavior, accessing the housing, managing coolant loss, then refilling and bleeding the system so air pockets do not create false overheating symptoms.",
    "ignition_coil_replacement_each": "The estimate includes proving the misfire path first, inspecting the plug well and connector, replacing the coil, then clearing codes and confirming misfire counters under idle and load.",
    "spark_plug_replacement": "Time changes with engine layout because coils, wires, covers, or intake parts may need removal before plugs can be inspected, torqued correctly, and verified for smooth operation.",
    "spark_plug_replacement_4_cyl": "Time changes with access because coils or plug wires still need removal, plug wells are inspected, plugs are torqued correctly, and operation is verified after installation.",
    "spark_plug_replacement_v6_v8": "V6 and V8 plug labor can climb when rear-bank plugs sit under tight intake or cowl access, so the job includes careful removal, torque control, and verification after reassembly.",
    "fuel_pump_replacement_in_tank": "Labor is driven by confirming fuel pressure first, checking relay/fuse/voltage supply, safely accessing the tank module, then priming and leak-checking the system after replacement.",
    "fuel_pump_replacement_external": "Labor is driven by confirming pressure and power supply first, accessing the external pump and fuel lines safely, then leak-checking and verifying pressure under load.",
    "catalytic_converter_replacement": "Converter labor includes confirming catalyst efficiency and downstream O2 behavior before replacement, then dealing with exhaust access, sensors, rusted hardware, and emissions-compliant verification.",
    "front_brake_rotors_replacement": "Rotor time includes lifting the vehicle, removing wheels and caliper brackets, cleaning hub surfaces so the new rotor sits true, then reassembling and verifying brake feel.",
    "rear_brake_rotors_replacement": "Rear rotor time includes wheel and caliper access, possible parking-brake hardware considerations, hub cleaning, and final brake feel verification after reassembly.",
    "brake_rotor_replacement": "Rotor time includes lifting the vehicle, removing wheels and caliper brackets, cleaning hub surfaces so the new rotor sits true, then reassembling and verifying brake feel.",
    "radiator_replacement": "Radiator labor includes confirming the leak or restriction, removing hoses/fans/brackets as needed, transferring hardware, then refilling, bleeding, pressure-testing, and checking temperature control.",
    "battery_replacement": "Battery labor includes confirming the battery path, accessing hold-downs and terminals, cleaning connections, installing the battery, and checking charging output or reset needs afterward.",
    "starter_replacement": "Starter labor includes confirming the no-crank path, disconnecting the battery, accessing tight mounting and cable connections, then verifying crank speed and start behavior after installation.",
}

LABOR_SERVICE_REPAIR_SUMMARIES: Dict[str, str] = {
    "alternator_replacement": "Recommended when charging tests confirm weak or unstable alternator output. Replacement restores battery charging and helps prevent repeat dead-battery or no-start complaints.",
    "thermostat_replacement": "Recommended when warm-up data, P0128 history, or cooling behavior points to a sticking thermostat. Delaying repair can leave weak heat, poor temperature control, or overheating risk unresolved.",
    "ignition_coil_replacement_each": "Recommended when misfire data or coil swap testing confirms a weak coil. Delaying repair can worsen rough running and may damage the catalytic converter during an active misfire.",
    "spark_plug_replacement": "Recommended when plug wear, fouling, or service interval supports replacement. Delaying repair can cause misfires, poor fuel economy, hard starts, and extra ignition coil stress.",
    "spark_plug_replacement_4_cyl": "Recommended when plug wear, fouling, or service interval supports replacement. Delaying repair can cause misfires, poor fuel economy, hard starts, and extra ignition coil stress.",
    "spark_plug_replacement_v6_v8": "Recommended when plug wear, fouling, or service interval supports replacement. Delaying repair can cause misfires, poor fuel economy, hard starts, and extra ignition coil stress.",
    "fuel_pump_replacement_in_tank": "Recommended when pressure and command testing confirm weak fuel delivery from the pump. Delaying repair can lead to stalling, hard starts, or a crank-no-start condition.",
    "fuel_pump_replacement_external": "Recommended when pressure and command testing confirm weak fuel delivery from the pump. Delaying repair can lead to stalling, hard starts, or a crank-no-start condition.",
    "catalytic_converter_replacement": "Recommended when catalyst-efficiency testing confirms converter failure after upstream causes are checked. Delaying repair can cause emissions failure, poor power, and repeat catalyst codes.",
    "front_brake_rotors_replacement": "Recommended when rotor wear, scoring, pulsation, or thickness confirms rotor service. Delaying repair can reduce braking quality and accelerate pad or caliper wear.",
    "rear_brake_rotors_replacement": "Recommended when rotor wear, scoring, pulsation, or thickness confirms rotor service. Delaying repair can reduce braking quality and accelerate pad or parking-brake hardware wear.",
    "brake_rotor_replacement": "Recommended when rotor wear, scoring, pulsation, or thickness confirms rotor service. Delaying repair can reduce braking quality and accelerate pad or caliper wear.",
    "radiator_replacement": "Recommended when leak, restriction, or cooling-system testing confirms radiator failure. Delaying repair can lead to coolant loss, overheating, and engine damage risk.",
    "battery_replacement": "Recommended when battery testing confirms low capacity or repeated failure to hold charge. Delaying repair can cause unreliable starts and added strain on the charging system.",
    "starter_replacement": "Recommended when no-crank testing confirms starter failure after battery and cable checks. Delaying repair can leave the vehicle unable to start without warning.",
}


LABOR_SERVICE_PROFILES: Dict[str, Dict[str, Any]] = {
    "brake_pad_replacement": {
        "display_name": "Brake Pad Replacement",
        "template": "brake_pad",
        "labor_hours": {"min": 1.0, "avg": 1.8, "max": 3.0},
    },
    "brake_rotor_replacement": {
        "display_name": "Brake Rotor Replacement",
        "template": "brake_rotor",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 3.2},
    },
    "front_brake_rotors_replacement": {
        "display_name": "Front Brake Rotors Replacement",
        "template": "brake_rotor",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 6.0},
    },
    "rear_brake_rotors_replacement": {
        "display_name": "Rear Brake Rotors Replacement",
        "template": "brake_rotor",
        "labor_hours": {"min": 1.2, "avg": 2.0, "max": 6.0},
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
        "template": "battery",
        "labor_hours": {"min": 0.3, "avg": 0.5, "max": 1.0},
    },

    "radiator_replacement": {
        "display_name": "Radiator Replacement",
        "template": "radiator",
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
    "fuel_pump_replacement_in_tank": {
        "display_name": "Fuel Pump Replacement (In-Tank)",
        "template": "fuel_pump",
        "labor_hours": {"min": 1.2, "avg": 3.6, "max": 6.0},
    },
    "fuel_pump_replacement_external": {
        "display_name": "Fuel Pump Replacement (External)",
        "template": "fuel_pump",
        "labor_hours": {"min": 1.2, "avg": 2.4, "max": 6.0},
    },
    "catalytic_converter_replacement": {
        "display_name": "Catalytic Converter Replacement",
        "template": "catalytic_converter",
        "labor_hours": {"min": 6.0, "avg": 12.0, "max": 18.0},
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
        "why": LABOR_SERVICE_EXPLANATIONS.get(service_key),
        "repair_summary": LABOR_SERVICE_REPAIR_SUMMARIES.get(service_key),
        "disclaimer": (
            "This shows how labor time is typically distributed for this service. "
            "Actual time may vary depending on vehicle condition and access."
        ),
    }

    
