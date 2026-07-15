from typing import Any


# General-purpose maintenance guidance only. These are intentionally conservative
# shop defaults, not OEM, manufacturer, VIN, or model-specific schedules.
MAINTENANCE_LIBRARY: dict[str, dict[str, Any]] = {
    "oil change": {
        "label": "Oil Change",
        "category": "Engine",
        "interval_miles": 5000,
        "interval_months": 6,
        "aliases": [
            "oil",
            "engine oil",
            "motor oil",
            "oil service",
            "engine oil service",
            "oil filter",
            "oil filter change",
            "oil and filter change",
            "oil & filter change",
            "lube oil filter",
        ],
        "suggest_in_ui": True,
    },
    "engine air filter replacement": {
        "label": "Engine Air Filter Replacement",
        "category": "Engine",
        "interval_miles": 15000,
        "interval_months": 12,
        "aliases": ["air filter", "engine filter", "intake filter", "engine air filter"],
        "suggest_in_ui": True,
        "ui_label": "Engine Air Filter",
    },
    "spark plug replacement": {
        "label": "Spark Plug Replacement",
        "category": "Engine",
        "interval_miles": 100000,
        "interval_months": 84,
        "aliases": ["spark plugs", "plugs", "tune up", "tune-up"],
        "suggest_in_ui": True,
        "ui_label": "Spark Plugs",
    },
    "timing belt service": {
        "label": "Timing Belt Service",
        "category": "Engine",
        "interval_miles": 100000,
        "interval_months": 84,
        "aliases": ["timing belt", "timing belt replacement"],
    },
    "drive belt replacement": {
        "label": "Drive Belt Replacement",
        "category": "Engine",
        "interval_miles": 90000,
        "interval_months": None,
        "aliases": ["serpentine belt", "drive belt", "accessory belt"],
        "suggest_in_ui": True,
        "ui_label": "Serpentine Belt",
    },
    "coolant service": {
        "label": "Coolant Service",
        "category": "Fluids",
        "interval_miles": 50000,
        "interval_months": 48,
        "aliases": ["coolant", "antifreeze", "radiator fluid", "coolant flush"],
        "suggest_in_ui": True,
    },
    "brake fluid service": {
        "label": "Brake Fluid Service",
        "category": "Fluids",
        "interval_miles": 30000,
        "interval_months": 24,
        "aliases": ["brake fluid", "brake flush", "brake fluid flush"],
        "suggest_in_ui": True,
    },
    "power steering fluid service": {
        "label": "Power Steering Fluid Service",
        "category": "Fluids",
        "interval_miles": 50000,
        "interval_months": 48,
        "aliases": ["power steering fluid", "power steering flush", "ps fluid"],
    },
    "transmission service": {
        "label": "Transmission Service",
        "category": "Fluids",
        "interval_miles": 60000,
        "interval_months": 48,
        "aliases": [
            "transmission fluid",
            "trans fluid",
            "atf",
            "automatic transmission fluid",
            "transmission flush",
        ],
        "suggest_in_ui": True,
    },
    "differential service": {
        "label": "Differential Service",
        "category": "Fluids",
        "interval_miles": 30000,
        "interval_months": 36,
        "aliases": ["differential fluid", "diff fluid", "rear diff", "front diff"],
    },
    "transfer case service": {
        "label": "Transfer Case Service",
        "category": "Fluids",
        "interval_miles": 30000,
        "interval_months": None,
        "aliases": ["transfer case fluid"],
    },
    "brake pad replacement": {
        "label": "Brake Pad Replacement",
        "category": "Brakes",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["brake pads", "pads", "front brakes", "rear brakes"],
    },
    "brake rotor replacement": {
        "label": "Brake Rotor Replacement",
        "category": "Brakes",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["rotors", "brake rotors"],
    },
    "brake inspection": {
        "label": "Brake Inspection",
        "category": "Brakes",
        "interval_miles": 12000,
        "interval_months": 12,
        "aliases": ["brake check"],
    },
    "tire rotation": {
        "label": "Tire Rotation",
        "category": "Tires/Wheels",
        "interval_miles": 5000,
        "interval_months": 6,
        "aliases": ["rotate tires", "tire rotate", "rotation"],
        "suggest_in_ui": True,
    },
    "tire balance": {
        "label": "Tire Balance",
        "category": "Tires/Wheels",
        "interval_miles": 12000,
        "interval_months": None,
        "aliases": ["wheel balance", "tire balancing"],
    },
    "wheel alignment": {
        "label": "Wheel Alignment",
        "category": "Tires/Wheels",
        "interval_miles": 12000,
        "interval_months": 12,
        "aliases": ["alignment", "four wheel alignment"],
    },
    "battery replacement": {
        "label": "Battery Replacement",
        "category": "Battery/Electrical",
        "interval_miles": None,
        "interval_months": 48,
        "aliases": ["battery", "car battery"],
        "suggest_in_ui": True,
    },
    "battery test": {
        "label": "Battery Test",
        "category": "Battery/Electrical",
        "interval_miles": None,
        "interval_months": 12,
        "aliases": ["battery check", "battery inspection"],
    },
    "alternator replacement": {
        "label": "Alternator Replacement",
        "category": "Battery/Electrical",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["alternator"],
    },
    "starter replacement": {
        "label": "Starter Replacement",
        "category": "Battery/Electrical",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["starter"],
    },
    "cabin air filter replacement": {
        "label": "Cabin Air Filter Replacement",
        "category": "HVAC",
        "interval_miles": 15000,
        "interval_months": 12,
        "aliases": ["cabin filter", "ac filter", "hvac filter", "cabin air filter"],
        "suggest_in_ui": True,
        "ui_label": "Cabin Air Filter",
    },
    "ac service": {
        "label": "AC Service",
        "category": "HVAC",
        "interval_miles": None,
        "interval_months": 24,
        "aliases": ["a c service", "air conditioning service", "refrigerant service"],
    },
    "shock replacement": {
        "label": "Shock Replacement",
        "category": "Suspension/Steering",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["shocks"],
    },
    "strut replacement": {
        "label": "Strut Replacement",
        "category": "Suspension/Steering",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["struts"],
    },
    "control arm replacement": {
        "label": "Control Arm Replacement",
        "category": "Suspension/Steering",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["control arm"],
    },
    "ball joint replacement": {
        "label": "Ball Joint Replacement",
        "category": "Suspension/Steering",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["ball joint"],
    },
    "tie rod replacement": {
        "label": "Tie Rod Replacement",
        "category": "Suspension/Steering",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["tie rod"],
    },
    "wiper blade replacement": {
        "label": "Wiper Blade Replacement",
        "category": "Wipers/Lights",
        "interval_miles": None,
        "interval_months": 12,
        "aliases": ["wipers", "wiper blades"],
    },
    "headlight bulb replacement": {
        "label": "Headlight Bulb Replacement",
        "category": "Wipers/Lights",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["headlight", "headlight bulb"],
    },
    "brake light bulb replacement": {
        "label": "Brake Light Bulb Replacement",
        "category": "Wipers/Lights",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["brake light", "stop light"],
    },
    "turn signal bulb replacement": {
        "label": "Turn Signal Bulb Replacement",
        "category": "Wipers/Lights",
        "interval_miles": None,
        "interval_months": None,
        "aliases": ["turn signal", "blinker"],
    },
    "multi point inspection": {
        "label": "Multi-Point Inspection",
        "category": "General",
        "interval_miles": None,
        "interval_months": 12,
        "aliases": [
            "inspection",
            "vehicle inspection",
            "bumper to bumper inspection",
            "multi-point inspection",
            "multipoint inspection",
        ],
    },
    "safety inspection": {
        "label": "Safety Inspection",
        "category": "General",
        "interval_miles": None,
        "interval_months": 12,
        "aliases": ["safety check"],
    },
    "emissions inspection": {
        "label": "Emissions Inspection",
        "category": "General",
        "interval_miles": None,
        "interval_months": 12,
        "aliases": ["smog", "emissions", "emissions test"],
    },
}


def normalize_maintenance_service_type(service_type: Any) -> str:
    normalized = str(service_type or "").strip().lower()
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in normalized).split()
    )


MAINTENANCE_SERVICE_ALIASES = {
    normalize_maintenance_service_type(alias): service_key
    for service_key, service in MAINTENANCE_LIBRARY.items()
    for alias in service.get("aliases", [])
}

MAINTENANCE_INTERVAL_PRESETS = {
    service_key: {
        "interval_miles": service.get("interval_miles"),
        "interval_months": service.get("interval_months"),
    }
    for service_key, service in MAINTENANCE_LIBRARY.items()
}

MAINTENANCE_SERVICE_OPTION_KEYS = [
    "oil change",
    "tire rotation",
    "engine air filter replacement",
    "cabin air filter replacement",
    "transmission service",
    "brake fluid service",
    "coolant service",
    "spark plug replacement",
    "differential service",
    "power steering fluid service",
    "drive belt replacement",
    "battery replacement",
]

MAINTENANCE_SERVICE_OPTIONS = [
    {
        "name": service.get("ui_label", service["label"]),
        "interval_miles": service.get("interval_miles"),
        "interval_months": service.get("interval_months"),
    }
    for service_key in MAINTENANCE_SERVICE_OPTION_KEYS
    for service in [MAINTENANCE_LIBRARY[service_key]]
]


def resolve_maintenance_service(service_type: Any) -> dict[str, Any] | None:
    normalized = normalize_maintenance_service_type(service_type)
    if normalized in MAINTENANCE_LIBRARY:
        return MAINTENANCE_LIBRARY[normalized]

    alias_key = MAINTENANCE_SERVICE_ALIASES.get(normalized)
    if alias_key:
        return MAINTENANCE_LIBRARY[alias_key]

    for service_key, service in MAINTENANCE_LIBRARY.items():
        if service_key in normalized:
            return service
    return None


def maintenance_defaults_for(service_type: Any) -> dict[str, int | None]:
    service = resolve_maintenance_service(service_type)
    if not service:
        return {}
    return {
        "interval_miles": service.get("interval_miles"),
        "interval_months": service.get("interval_months"),
    }
