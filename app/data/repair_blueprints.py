from copy import deepcopy
from typing import Any


RepairIntelligence = dict[str, Any]


VENDOR_LINK_PLACEHOLDERS = [
    {"label": "OEM", "status": "Placeholder"},
    {"label": "RockAuto", "status": "Placeholder"},
    {"label": "NAPA", "status": "Placeholder"},
    {"label": "AutoZone", "status": "Placeholder"},
]


REPAIR_BLUEPRINTS: dict[str, RepairIntelligence] = {
    "oil-change": {
        "slug": "oil-change",
        "title": "Oil Change",
        "aliases": ["oil change", "oil service", "engine oil", "oil and filter"],
        "vehicle_specific": False,
        "snapshot": [
            {"label": "Quote Focus", "value": "Oil, filter, washer, reset"},
            {"label": "Risk", "value": "Leaks, wrong capacity, stripped plug"},
            {"label": "Benchmark", "value": "0.3-0.6 hr"},
        ],
        "critical_checks": [
            "Confirm viscosity, capacity, and filter style by VIN.",
            "Inspect drain plug threads and sealing washer.",
            "Check for existing engine, cooler, or filter housing leaks.",
            "Verify maintenance reminder reset path.",
        ],
        "known_failure_patterns": [
            "Aged drain plug washers seep after reuse.",
            "Plastic cartridge housings can crack when overtorqued.",
            "Undertray fasteners are often missing or damaged.",
        ],
        "inspection_opportunities": [
            "Oil leaks at pan, valve cover, cooler, and filter housing",
            "Coolant or fuel contamination in drained oil",
            "Belts, hoses, CV boots, tires, and brakes while lifted",
        ],
        "common_add_on_repairs": [
            "Engine air filter",
            "Cabin air filter",
            "Wiper blades",
            "Oil leak diagnosis",
        ],
        "required_parts": ["Engine oil", "Oil filter", "Drain plug washer when equipped"],
        "recommended_parts": ["Drain plug if rounded", "Splash shield clips if damaged"],
        "labor_benchmark": "0.3-0.6 hr; adjust for skid plates, cartridge filters, or access panels.",
        "critical_specs": [
            {"label": "Oil capacity", "value": "Vehicle specific"},
            {"label": "Oil viscosity", "value": "Vehicle specific"},
            {"label": "Drain plug torque", "value": "Vehicle specific"},
        ],
        "repair_steps": [
            "Confirm oil specification, capacity, filter application, and reset procedure.",
            "Inspect oil level and condition before draining.",
            "Drain engine oil and inspect plug, washer, and pan threads.",
            "Replace filter and drain plug washer when required.",
            "Refill, run engine, check for leaks, verify level, and reset reminder.",
        ],
        "tools_required": ["Socket set", "Oil filter wrench", "Drain pan", "Funnel", "Torque wrench"],
        "safety_notes": ["Support the vehicle correctly.", "Use eye protection around hot oil."],
    },
    "battery-replacement": {
        "slug": "battery-replacement",
        "title": "Battery Replacement",
        "aliases": ["battery replacement", "replace battery", "battery", "starting battery"],
        "vehicle_specific": False,
        "snapshot": [
            {"label": "Quote Focus", "value": "Battery, test, reset"},
            {"label": "Risk", "value": "Registration, cable corrosion"},
            {"label": "Benchmark", "value": "0.3-0.8 hr"},
        ],
        "critical_checks": [
            "Test battery state of charge and health before replacement.",
            "Inspect terminals, hold-down, cables, and charging voltage.",
            "Confirm battery group, CCA, venting, and AGM/EFB requirement.",
            "Check whether battery registration or BMS reset is required.",
        ],
        "known_failure_patterns": [
            "Corroded terminals create repeat no-start complaints.",
            "Weak alternator output can be misquoted as battery-only.",
            "Modern battery monitoring systems may need reset after replacement.",
        ],
        "inspection_opportunities": [
            "Terminal and cable end corrosion",
            "Hold-down damage or missing hardware",
            "Parasitic draw concern if battery repeatedly dies",
        ],
        "common_add_on_repairs": ["Terminal service", "Battery cable end", "Charging system test", "Parasitic draw diagnosis"],
        "required_parts": ["Correct battery group and type"],
        "recommended_parts": ["Terminal protectant", "Hold-down hardware if missing", "Memory saver when required"],
        "labor_benchmark": "0.3-0.8 hr; adjust for registration, under-seat, trunk, or cowl location.",
        "critical_specs": [
            {"label": "Battery group", "value": "Vehicle specific"},
            {"label": "CCA rating", "value": "Vehicle specific"},
            {"label": "Terminal torque", "value": "Snug to spec; do not overtighten"},
        ],
        "repair_steps": [
            "Test battery and charging system.",
            "Preserve memory if required.",
            "Disconnect negative first, then positive.",
            "Replace battery and secure hold-down.",
            "Reconnect positive then negative, reset/register if required, and verify crank.",
        ],
        "tools_required": ["Battery tester", "Socket set", "Terminal brush", "Memory saver", "Scan tool when required"],
        "safety_notes": ["Disconnect negative first and reconnect it last.", "Keep tools clear of positive terminal and ground."],
    },
    "front-brake-pads": {
        "slug": "front-brake-pads",
        "title": "Front Brake Pads",
        "aliases": ["front brake pads", "brake pads", "front pads", "pads replacement", "front brakes"],
        "vehicle_specific": False,
        "snapshot": [
            {"label": "Quote Focus", "value": "Pads, hardware, rotors"},
            {"label": "Risk", "value": "Rotor runout, seized slides"},
            {"label": "Benchmark", "value": "1.0-1.6 hr"},
        ],
        "critical_checks": [
            "Measure inner and outer pad thickness on both sides.",
            "Inspect rotor thickness, scoring, heat spots, and runout symptoms.",
            "Check caliper slide pins, boots, piston boot, and hose condition.",
            "Confirm brake package before ordering pads and hardware.",
        ],
        "known_failure_patterns": [
            "Dry slide pins cause tapered pad wear and brake pull.",
            "Pad-only quotes come back when rotors are below spec or pulsating.",
            "Hardware reuse can cause noise on higher-mileage vehicles.",
        ],
        "inspection_opportunities": [
            "Brake fluid condition",
            "Front rotor replacement",
            "Caliper slide service",
            "Tire wear, wheel bearings, and suspension play while wheels are off",
        ],
        "common_add_on_repairs": ["Front rotors", "Brake hardware kit", "Brake fluid service", "Caliper slide service"],
        "required_parts": ["Front brake pads", "Pad hardware when included or required"],
        "recommended_parts": ["Front rotors if below spec or pulsating", "Brake lubricant", "Brake cleaner"],
        "labor_benchmark": "1.0-1.6 hr front axle; add time for rotors, seized hardware, or caliper service.",
        "critical_specs": [
            {"label": "Wheel lug torque", "value": "Vehicle specific"},
            {"label": "Caliper slide bolts", "value": "Vehicle specific"},
            {"label": "Rotor minimum thickness", "value": "Vehicle specific"},
        ],
        "repair_steps": [
            "Confirm pad wear and rotor condition.",
            "Raise vehicle and remove front wheels.",
            "Remove caliper hardware and support caliper.",
            "Install pads and hardware with correct lubricant points.",
            "Torque fasteners, pump pedal, and road test.",
        ],
        "tools_required": ["Lift or jack stands", "Socket set", "Torque wrench", "Brake piston compressor", "Brake cleaner"],
        "safety_notes": ["Pump the brake pedal before moving the vehicle.", "Do not let the caliper hang by the hose."],
    },
    "spark-plugs": {
        "slug": "spark-plugs",
        "title": "Spark Plugs",
        "aliases": ["spark plugs", "spark plug replacement", "replace spark plugs", "tune up", "tune-up"],
        "vehicle_specific": False,
        "snapshot": [
            {"label": "Quote Focus", "value": "Plugs, boots, access"},
            {"label": "Risk", "value": "Thread damage, seized plugs"},
            {"label": "Benchmark", "value": "1.0-3.0 hr"},
        ],
        "critical_checks": [
            "Confirm cylinder count, engine family, and plug type.",
            "Inspect coil boots, plug wells, and oil or coolant fouling.",
            "Check misfire data before replacing ignition parts.",
            "Verify plug gap and torque spec by engine.",
        ],
        "known_failure_patterns": [
            "Oil in plug wells points to valve cover tube seal leakage.",
            "Incorrect plug heat range or gap can create repeat misfires.",
            "High-mileage plugs can seize in aluminum heads.",
        ],
        "inspection_opportunities": [
            "Coil boots and ignition coils",
            "Valve cover gasket or tube seals",
            "Intake gaskets when removal is required",
            "Compression or leak-down if plugs show cylinder-specific damage",
        ],
        "common_add_on_repairs": ["Coil boots", "Ignition coils", "Valve cover gaskets", "Intake plenum gasket"],
        "required_parts": ["Spark plugs"],
        "recommended_parts": ["Coil boots if brittle or carbon tracked", "Anti-seize only if service information calls for it"],
        "labor_benchmark": "1.0-3.0 hr; adjust for intake removal, seized plugs, or engine bay access.",
        "critical_specs": [
            {"label": "Plug torque", "value": "Vehicle specific"},
            {"label": "Plug gap", "value": "Vehicle specific"},
            {"label": "Coil bolt torque", "value": "Vehicle specific"},
        ],
        "repair_steps": [
            "Confirm engine configuration and plug application.",
            "Remove coils or wires and inspect boots.",
            "Remove plugs with engine-condition precautions.",
            "Install correct plugs to spec.",
            "Reassemble, clear codes if applicable, and verify misfire counters.",
        ],
        "tools_required": ["Spark plug socket", "Extension set", "Torque wrench", "Compressed air", "Scan tool when misfire-related"],
        "safety_notes": ["Blow debris out of plug wells before removal.", "Avoid cross-threading aluminum heads."],
    },
    "head-gasket-repair": {
        "slug": "head-gasket-repair",
        "title": "Head Gasket Repair",
        "aliases": ["head gasket", "head gasket repair", "cylinder head gasket", "coolant intrusion"],
        "vehicle_specific": False,
        "snapshot": [
            {"label": "Quote Focus", "value": "Tear-down validation"},
            {"label": "Risk", "value": "Root cause, warped decks"},
            {"label": "Benchmark", "value": "Major engine labor"},
        ],
        "critical_checks": [
            "Verify root cause before teardown.",
            "Pressure-test cooling system.",
            "Confirm cylinder-specific coolant intrusion.",
            "Check head and block deck warpage.",
        ],
        "known_failure_patterns": [
            "Overheating events can warp head or block surfaces.",
            "Cooling system faults can be the cause, not the result.",
            "Timing disassembly errors create major rework risk.",
        ],
        "inspection_opportunities": [
            "Cooling system restriction or leak source",
            "Timing components while accessible",
            "Spark plugs and cylinders for coolant evidence",
            "Oil/coolant cross-contamination",
        ],
        "common_add_on_repairs": ["Water pump", "Thermostat", "Radiator hoses", "Spark plugs", "Valve cover gaskets", "Coolant service"],
        "required_parts": ["Head gasket set", "Head bolts if torque-to-yield or required", "Coolant", "Engine oil and filter"],
        "recommended_parts": ["Water pump", "Thermostat", "Radiator hoses", "Spark plugs", "Valve cover gaskets"],
        "labor_benchmark": "Major engine labor; quote only after access, engine layout, and machine-shop needs are confirmed.",
        "critical_specs": [
            {"label": "Head bolt sequence", "value": "Vehicle specific"},
            {"label": "Head bolt torque/angle", "value": "Vehicle specific"},
            {"label": "Deck warpage limit", "value": "Vehicle specific"},
        ],
        "repair_steps": [
            "Confirm diagnosis and document cylinder-specific evidence.",
            "Set engine timing references before disassembly.",
            "Remove cylinder head following service sequence.",
            "Inspect head and block decks.",
            "Install gasket and bolts to sequence, reassemble timing, refill fluids, and verify repair.",
        ],
        "tools_required": ["Cooling system pressure tester", "Leak-down tester", "Straightedge", "Feeler gauges", "Torque angle gauge"],
        "safety_notes": ["Do not open a hot cooling system.", "Document timing alignment before disassembly."],
    },
}


VEHICLE_BLUEPRINT_OVERRIDES: dict[str, list[RepairIntelligence]] = {
    "front-brake-pads": [
        {
            "match": {"year": "2010", "make": "Honda", "model": "Accord"},
            "vehicle_specific": True,
            "snapshot": [
                {"label": "Vehicle", "value": "2010 Accord"},
                {"label": "Quote Focus", "value": "Pads, hardware, rotors"},
                {"label": "Risk", "value": "Slide pins, rotor pulsation"},
            ],
            "critical_checks": [
                "Verify trim and brake package before ordering front pads.",
                "Inspect caliper slide pins for dry or sticking movement.",
                "Measure front rotors; quote rotors if below spec, scored, or pulsating.",
                "Check brake hose condition and caliper piston boot.",
            ],
            "known_failure_patterns": [
                "Accord front slide pins commonly dry out and create tapered wear.",
                "Rotor pulsation can return if pad-only work is quoted on warped rotors.",
                "Hardware corrosion can create noise after pad replacement.",
            ],
            "critical_specs": [
                {"label": "Front wheel lug nuts", "value": "80 lb-ft typical Accord reference; verify by trim"},
                {"label": "Front caliper slide bolts", "value": "Honda service spec required"},
                {"label": "Front rotor minimum thickness", "value": "Verify by exact brake package"},
            ],
        }
    ],
    "spark-plugs": [
        {
            "match": {"year": "2002", "make": "Ford", "model": "F-150", "engine": "5.4"},
            "vehicle_specific": True,
            "snapshot": [
                {"label": "Vehicle", "value": "2002 F-150 5.4 2V"},
                {"label": "Quote Focus", "value": "Thread risk, coils, boots"},
                {"label": "Risk", "value": "Aluminum head plug threads"},
            ],
            "critical_checks": [
                "Confirm 5.4L 2-valve engine before quoting plug labor.",
                "Inspect plug wells for water, oil, and coil boot carbon tracking.",
                "Evaluate thread condition and previous plug repair history.",
                "Check misfire data so coils are quoted only where supported.",
            ],
            "known_failure_patterns": [
                "Early modular 2V heads are known for spark plug thread damage or plug blowout risk.",
                "Coil boots commonly carbon track and create repeat misfires.",
                "Moisture intrusion in plug wells can mimic coil failure.",
            ],
            "common_add_on_repairs": ["Coil boots", "Ignition coils as needed", "Thread repair insert if damaged", "Valve cover gasket if oil intrusion is present"],
            "recommended_parts": ["Coil boots", "Dielectric grease", "Thread repair contingency if plug threads are damaged"],
            "labor_benchmark": "Quote with thread-risk allowance; add time for damaged threads or seized plugs.",
            "critical_specs": [
                {"label": "Spark plug torque", "value": "Ford 5.4L 2V service spec required"},
                {"label": "Plug gap", "value": "Verify by emissions label and plug application"},
                {"label": "Coil bolt torque", "value": "Verify by service information"},
            ],
        }
    ],
    "head-gasket-repair": [
        {
            "match": {"year": "2008", "make": "Toyota", "model": "Sequoia", "engine": "5.7"},
            "vehicle_specific": True,
            "snapshot": [
                {"label": "Vehicle", "value": "2008 Sequoia 5.7L"},
                {"label": "Quote Focus", "value": "Confirm before teardown"},
                {"label": "Risk", "value": "Timing chain alignment"},
            ],
            "critical_checks": [
                "Verify root cause before teardown.",
                "Pressure-test cooling system.",
                "Confirm cylinder-specific coolant intrusion.",
                "Verify engine at TDC before timing disassembly.",
                "Document timing chain, cam, and crank alignment.",
                "Confirm head bolt replacement requirements.",
                "Do not reuse TTY bolts if applicable.",
                "Check head and block deck warpage.",
                "Inspect timing components while accessible.",
            ],
            "known_failure_patterns": [
                "Cooling system pressure loss can point to external leaks before head gasket teardown.",
                "Cylinder-specific coolant evidence should be documented before major disassembly.",
                "Timing chain/cam/crank alignment errors create high-cost rework on the 5.7L V8.",
                "Overheat history can require machine-shop inspection beyond a gasket-only quote.",
            ],
            "inspection_opportunities": [
                "Water pump seepage or bearing play",
                "Thermostat operation",
                "Upper and lower radiator hoses",
                "Spark plug coolant cleaning or fouling",
                "Valve cover gasket seepage",
                "Timing chains, guides, tensioners, and cam/crank seals",
            ],
            "common_add_on_repairs": ["Water pump", "Thermostat", "Radiator hoses", "Spark plugs", "Valve cover gaskets", "Coolant service"],
            "required_parts": ["Head gasket set", "Head bolts if required/TTY", "Coolant", "Engine oil and filter", "Intake/exhaust gaskets as removed"],
            "recommended_parts": ["Water pump", "Thermostat", "Radiator hoses", "Spark plugs", "Valve cover gaskets", "Coolant service"],
            "labor_benchmark": "Major V8 engine labor; quote after confirming root cause, timing access, and deck inspection plan.",
            "critical_specs": [
                {"label": "Base timing position", "value": "Set and document TDC before chain disassembly"},
                {"label": "Head bolt replacement", "value": "Confirm Toyota 5.7L bolt reuse/TTY rule by service info"},
                {"label": "Deck warpage", "value": "Measure head and block; compare to Toyota service limit"},
                {"label": "Head bolt torque/angle", "value": "Toyota 5.7L service sequence required"},
            ],
        }
    ],
}


def _normalize(value: Any) -> str:
    return " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or "")).split()
    )


def _merge_blueprint(base: RepairIntelligence, override: RepairIntelligence) -> RepairIntelligence:
    merged = deepcopy(base)
    for key, value in override.items():
        if key == "match":
            continue
        merged[key] = deepcopy(value)
    return merged


def _vehicle_matches(vehicle: dict[str, Any], match: dict[str, str]) -> bool:
    for key, expected in match.items():
        expected_value = _normalize(expected)
        if not expected_value:
            continue
        actual_value = _normalize(vehicle.get(key))
        if expected_value not in actual_value:
            return False
    return bool(match)


def get_repair_blueprint_for_work_item(
    title: Any,
    detail: Any,
    vehicle: dict[str, Any],
) -> RepairIntelligence | None:
    haystack = _normalize(f"{title or ''} {detail or ''}")
    if not haystack:
        return None

    selected: RepairIntelligence | None = None
    for blueprint in REPAIR_BLUEPRINTS.values():
        aliases = [blueprint["title"], *blueprint.get("aliases", [])]
        if any(_normalize(alias) in haystack for alias in aliases):
            selected = blueprint
            break

    if not selected:
        return None

    for override in VEHICLE_BLUEPRINT_OVERRIDES.get(selected["slug"], []):
        if _vehicle_matches(vehicle, override.get("match", {})):
            return _with_vendor_links(_merge_blueprint(selected, override))

    return _with_vendor_links(deepcopy(selected))


def _with_vendor_links(intelligence: RepairIntelligence) -> RepairIntelligence:
    intelligence.setdefault("vendor_links", deepcopy(VENDOR_LINK_PLACEHOLDERS))
    return intelligence


def blueprint_summary(blueprint: RepairIntelligence) -> dict[str, Any]:
    return {
        "labor_benchmark": blueprint.get("labor_benchmark") or "Not set",
        "critical_checks_count": len(blueprint.get("critical_checks") or []),
        "required_parts_count": len(blueprint.get("required_parts") or []),
        "recommended_parts_count": len(blueprint.get("recommended_parts") or []),
        "known_patterns_count": len(blueprint.get("known_failure_patterns") or []),
        "vehicle_specific": bool(blueprint.get("vehicle_specific")),
    }
