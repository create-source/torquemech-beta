from copy import deepcopy
from typing import Any


RepairIntelligence = dict[str, Any]


VENDOR_LINK_PLACEHOLDERS = [
    {"label": "OEM/dealer catalog", "status": "VIN-confirmed source"},
    {"label": "RockAuto", "status": "Aftermarket fitment source"},
    {"label": "NAPA", "status": "Local availability source"},
    {"label": "AutoZone", "status": "Local availability source"},
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
            {"label": "Oil capacity examples", "value": "4.4 qt compact 4-cyl; 5.7 qt midsize V6; 7.0 qt light-truck V8"},
            {"label": "Drain plug torque examples", "value": "25 lb-ft small aluminum pan; 30 lb-ft steel pan; 33 lb-ft light truck"},
            {"label": "Cartridge filter cap example", "value": "18 lb-ft / 25 Nm on many marked plastic caps"},
        ],
        "visual_layout": {
            "kind": "Service layout",
            "title": "Oil Change Service Path",
            "svg": "<svg viewBox=\"0 0 660 310\" role=\"img\" xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"660\" height=\"310\" fill=\"#f8fafc\"/><rect x=\"60\" y=\"54\" width=\"540\" height=\"86\" rx=\"16\" fill=\"#e0f2fe\" stroke=\"#0891b2\"/><text x=\"330\" y=\"82\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"17\" font-weight=\"700\" fill=\"#0f172a\">Top side</text><g font-family=\"Arial\" font-size=\"14\" fill=\"#0f172a\"><circle cx=\"170\" cy=\"112\" r=\"20\" fill=\"#fff\" stroke=\"#0891b2\"/><text x=\"170\" y=\"117\" text-anchor=\"middle\">Fill</text><circle cx=\"330\" cy=\"112\" r=\"20\" fill=\"#fff\" stroke=\"#0891b2\"/><text x=\"330\" y=\"117\" text-anchor=\"middle\">Level</text><circle cx=\"490\" cy=\"112\" r=\"20\" fill=\"#fff\" stroke=\"#0891b2\"/><text x=\"490\" y=\"117\" text-anchor=\"middle\">Reset</text></g><rect x=\"60\" y=\"178\" width=\"540\" height=\"86\" rx=\"16\" fill=\"#dcfce7\" stroke=\"#16a34a\"/><text x=\"330\" y=\"206\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"17\" font-weight=\"700\" fill=\"#0f172a\">Underside</text><g font-family=\"Arial\" font-size=\"14\" fill=\"#0f172a\"><rect x=\"138\" y=\"226\" width=\"64\" height=\"26\" rx=\"8\" fill=\"#fff\" stroke=\"#16a34a\"/><text x=\"170\" y=\"244\" text-anchor=\"middle\">Drain</text><rect x=\"292\" y=\"226\" width=\"76\" height=\"26\" rx=\"8\" fill=\"#fff\" stroke=\"#16a34a\"/><text x=\"330\" y=\"244\" text-anchor=\"middle\">Filter</text><rect x=\"448\" y=\"226\" width=\"84\" height=\"26\" rx=\"8\" fill=\"#fff\" stroke=\"#16a34a\"/><text x=\"490\" y=\"244\" text-anchor=\"middle\">Leak check</text></g></svg>",
            "legend": ["Custom TorqueMech layout.", "Use exact capacity from vehicle service data."],
        },
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
            {"label": "Resting voltage", "value": "12.6 V fully charged lead-acid reference"},
            {"label": "Charging voltage", "value": "13.5-14.8 V typical running range"},
            {"label": "Terminal clamp torque", "value": "44-62 lb-in common light-terminal range"},
            {"label": "Hold-down torque", "value": "80-120 lb-in common range"},
        ],
        "visual_layout": {
            "kind": "Battery layout",
            "title": "Battery Replacement Connection Order",
            "svg": "<svg viewBox=\"0 0 650 320\" role=\"img\" xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"650\" height=\"320\" fill=\"#f8fafc\"/><rect x=\"160\" y=\"70\" width=\"330\" height=\"160\" rx=\"18\" fill=\"#e2e8f0\" stroke=\"#64748b\" stroke-width=\"3\"/><rect x=\"196\" y=\"102\" width=\"78\" height=\"54\" rx=\"8\" fill=\"#fee2e2\" stroke=\"#e11d48\" stroke-width=\"3\"/><rect x=\"376\" y=\"102\" width=\"78\" height=\"54\" rx=\"8\" fill=\"#dbeafe\" stroke=\"#2563eb\" stroke-width=\"3\"/><text x=\"235\" y=\"136\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"30\" font-weight=\"700\" fill=\"#e11d48\">+</text><text x=\"415\" y=\"136\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"30\" font-weight=\"700\" fill=\"#2563eb\">-</text><rect x=\"250\" y=\"185\" width=\"150\" height=\"24\" rx=\"8\" fill=\"#fff\" stroke=\"#334155\"/><text x=\"325\" y=\"202\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"13\" font-weight=\"700\" fill=\"#0f172a\">Hold-down</text><text x=\"325\" y=\"262\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"14\" font-weight=\"700\" fill=\"#64748b\">Negative off first; positive on first</text></svg>",
            "legend": ["Custom TorqueMech layout.", "Confirm registration/BMS reset by vehicle."],
        },
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
            {"label": "Wheel lug torque", "value": "80 lb-ft on 2010 Accord example; many passenger cars 76-100 lb-ft"},
            {"label": "Caliper slide bolts", "value": "26 lb-ft on 2010 Accord example"},
            {"label": "Caliper bracket bolts", "value": "80 lb-ft on 2010 Accord example"},
            {"label": "Brake fluid", "value": "DOT 3 on many Honda front brake services"},
        ],
        "visual_layout": {
            "kind": "Brake layout",
            "title": "Front Disc Brake Service Points",
            "svg": "<svg viewBox=\"0 0 660 340\" role=\"img\" xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"660\" height=\"340\" fill=\"#f8fafc\"/><g transform=\"translate(80 48)\"><circle cx=\"120\" cy=\"125\" r=\"98\" fill=\"#e2e8f0\" stroke=\"#64748b\" stroke-width=\"3\"/><circle cx=\"120\" cy=\"125\" r=\"38\" fill=\"#fff\" stroke=\"#94a3b8\" stroke-width=\"2\"/><path d=\"M178 58 h68 q20 0 20 20 v94 q0 20-20 20 h-68 q22-31 22-67 t-22-67z\" fill=\"#fee2e2\" stroke=\"#e11d48\" stroke-width=\"3\"/><rect x=\"194\" y=\"82\" width=\"34\" height=\"86\" rx=\"7\" fill=\"#fff\" stroke=\"#e11d48\"/><rect x=\"234\" y=\"82\" width=\"18\" height=\"86\" rx=\"6\" fill=\"#fecdd3\" stroke=\"#e11d48\"/><text x=\"120\" y=\"252\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"14\" font-weight=\"700\" fill=\"#334155\">Rotor: measure thickness and surface</text></g><g font-family=\"Arial\" font-size=\"14\" fill=\"#0f172a\"><line x1=\"330\" y1=\"118\" x2=\"470\" y2=\"78\" stroke=\"#334155\" stroke-width=\"2\"/><circle cx=\"330\" cy=\"118\" r=\"5\" fill=\"#334155\"/><text x=\"478\" y=\"82\" font-weight=\"700\">Slide pins</text><text x=\"478\" y=\"102\">clean, lube, torque</text><line x1=\"305\" y1=\"165\" x2=\"470\" y2=\"168\" stroke=\"#334155\" stroke-width=\"2\"/><circle cx=\"305\" cy=\"165\" r=\"5\" fill=\"#334155\"/><text x=\"478\" y=\"172\" font-weight=\"700\">Pads + hardware</text></g></svg>",
            "legend": ["Custom TorqueMech layout.", "Shows service points, not exact OEM geometry."],
        },
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
            {"label": "Plug torque", "value": "13 lb-ft / 156 lb-in for common Ford 5.4L 2-valve service"},
            {"label": "Plug gap", "value": "0.054 in / 1.37 mm common Ford 5.4L example"},
            {"label": "Coil bolt torque", "value": "62 lb-in common Ford modular coil fastener value"},
        ],
        "visual_layout": {
            "kind": "Cylinder layout",
            "title": "Ignition Access Map",
            "svg": "<svg viewBox=\"0 0 640 300\" role=\"img\" xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"640\" height=\"300\" fill=\"#f8fafc\"/><text x=\"320\" y=\"28\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"18\" font-weight=\"700\" fill=\"#0f172a\">Front of engine</text><path d=\"M320 44 l-18 30 h36 z\" fill=\"#0f766e\"/><rect x=\"90\" y=\"82\" width=\"190\" height=\"160\" rx=\"14\" fill=\"#e0f2fe\" stroke=\"#0891b2\"/><rect x=\"360\" y=\"82\" width=\"190\" height=\"160\" rx=\"14\" fill=\"#ede9fe\" stroke=\"#7c3aed\"/><text x=\"185\" y=\"112\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"14\" font-weight=\"700\" fill=\"#164e63\">Passenger bank</text><text x=\"455\" y=\"112\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"14\" font-weight=\"700\" fill=\"#4c1d95\">Driver bank</text><g font-family=\"Arial\" font-size=\"18\" font-weight=\"700\" text-anchor=\"middle\"><circle cx=\"125\" cy=\"160\" r=\"24\" fill=\"#fff\" stroke=\"#0891b2\"/><text x=\"125\" y=\"166\" fill=\"#0f172a\">1</text><circle cx=\"165\" cy=\"190\" r=\"24\" fill=\"#fff\" stroke=\"#0891b2\"/><text x=\"165\" y=\"196\" fill=\"#0f172a\">2</text><circle cx=\"205\" cy=\"160\" r=\"24\" fill=\"#fff\" stroke=\"#0891b2\"/><text x=\"205\" y=\"166\" fill=\"#0f172a\">3</text><circle cx=\"245\" cy=\"190\" r=\"24\" fill=\"#fff\" stroke=\"#0891b2\"/><text x=\"245\" y=\"196\" fill=\"#0f172a\">4</text><circle cx=\"395\" cy=\"160\" r=\"24\" fill=\"#fff\" stroke=\"#7c3aed\"/><text x=\"395\" y=\"166\" fill=\"#0f172a\">5</text><circle cx=\"435\" cy=\"190\" r=\"24\" fill=\"#fff\" stroke=\"#7c3aed\"/><text x=\"435\" y=\"196\" fill=\"#0f172a\">6</text><circle cx=\"475\" cy=\"160\" r=\"24\" fill=\"#fff\" stroke=\"#7c3aed\"/><text x=\"475\" y=\"166\" fill=\"#0f172a\">7</text><circle cx=\"515\" cy=\"190\" r=\"24\" fill=\"#fff\" stroke=\"#7c3aed\"/><text x=\"515\" y=\"196\" fill=\"#0f172a\">8</text></g><text x=\"320\" y=\"272\" text-anchor=\"middle\" font-family=\"Arial\" font-size=\"16\" font-weight=\"700\" fill=\"#64748b\">Firewall / cowl side</text></svg>",
            "legend": ["Custom TorqueMech layout.", "Use service data for exact bank naming when diagnosis depends on cylinder numbering."],
        },
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
            {"label": "Cooling system pressure test", "value": "Test to cap/system rating; commonly near 16 psi"},
            {"label": "Engine coolant capacity example", "value": "Approximately 13 qt range on large SUV V8 systems"},
            {"label": "TTY warning", "value": "Treat head bolts as one-time-use when teardown is approved"},
            {"label": "Leak-down evidence", "value": "Adjacent-cylinder leakage or coolant-bottle bubbling is decision-grade evidence"},
        ],
        "visual_layout": {
            "kind": "Diagnostic flow layout",
            "title": "Head Gasket Evidence Flow",
            "svg": "<svg viewBox=\"0 0 680 330\" role=\"img\" xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"680\" height=\"330\" fill=\"#f8fafc\"/><g font-family=\"Arial\" font-size=\"14\" fill=\"#0f172a\"><rect x=\"40\" y=\"40\" width=\"160\" height=\"62\" rx=\"10\" fill=\"#e0f2fe\" stroke=\"#0891b2\"/><text x=\"120\" y=\"66\" text-anchor=\"middle\" font-weight=\"700\">Coolant loss</text><text x=\"120\" y=\"86\" text-anchor=\"middle\">or overheat</text><rect x=\"260\" y=\"40\" width=\"160\" height=\"62\" rx=\"10\" fill=\"#fff\" stroke=\"#64748b\"/><text x=\"340\" y=\"66\" text-anchor=\"middle\" font-weight=\"700\">Pressure test</text><text x=\"340\" y=\"86\" text-anchor=\"middle\">hot and cold</text><rect x=\"480\" y=\"40\" width=\"160\" height=\"62\" rx=\"10\" fill=\"#fff\" stroke=\"#64748b\"/><text x=\"560\" y=\"66\" text-anchor=\"middle\" font-weight=\"700\">Cap / leak source</text><text x=\"560\" y=\"86\" text-anchor=\"middle\">confirm first</text><rect x=\"40\" y=\"154\" width=\"160\" height=\"62\" rx=\"10\" fill=\"#ede9fe\" stroke=\"#7c3aed\"/><text x=\"120\" y=\"180\" text-anchor=\"middle\" font-weight=\"700\">Cold misfire</text><text x=\"120\" y=\"200\" text-anchor=\"middle\">or white smoke</text><rect x=\"260\" y=\"154\" width=\"160\" height=\"62\" rx=\"10\" fill=\"#fff\" stroke=\"#64748b\"/><text x=\"340\" y=\"180\" text-anchor=\"middle\" font-weight=\"700\">Cylinder evidence</text><text x=\"340\" y=\"200\" text-anchor=\"middle\">plug / borescope</text><rect x=\"480\" y=\"154\" width=\"160\" height=\"62\" rx=\"10\" fill=\"#fee2e2\" stroke=\"#e11d48\"/><text x=\"560\" y=\"180\" text-anchor=\"middle\" font-weight=\"700\">Leak-down</text><text x=\"560\" y=\"200\" text-anchor=\"middle\">bubble / adjacent cyl</text></g></svg>",
            "legend": ["Custom TorqueMech layout.", "Use this to decide whether major teardown is justified."],
        },
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
                {"label": "Risk", "value": "Low"},
                {"label": "Labor", "value": "1.0-1.6 hr"},
            ],
            "watch_for": [
                "Seized slide pins",
                "Rotor runout",
                "Uneven inner pad wear",
                "Hardware noise",
            ],
            "must_replace": [
                "Front brake pads",
                "Hardware kit if required",
            ],
            "while_youre_there": ["Front rotors", "Brake fluid service", "Caliper slide service"],
            "critical_checks": [
                "Seized slide pins",
                "Rotor runout",
                "Uneven inner pad wear",
                "Hardware noise",
            ],
            "required_parts": ["Front brake pads", "Hardware kit if required"],
            "recommended_parts": ["Front rotors", "Brake fluid service", "Caliper slide service"],
            "common_add_on_repairs": ["Front rotors", "Brake fluid service", "Caliper slide service"],
            "labor_benchmark": "1.0-1.6 hr",
            "critical_specs": [
                {"label": "Lug nut torque", "value": "Verify by trim"},
                {"label": "Caliper slide bolt torque", "value": "Honda service spec"},
                {"label": "Rotor minimum thickness", "value": "Verify brake package"},
            ],
        }
    ],
    "spark-plugs": [
        {
            "match": {"year": "2002", "make": "Ford", "model": "F-150", "engine": "5.4"},
            "vehicle_specific": True,
            "snapshot": [
                {"label": "Risk", "value": "Medium"},
                {"label": "Labor", "value": "1.9 hr"},
            ],
            "watch_for": [
                "Plug thread damage",
                "Coil boot carbon tracking",
                "Misfire return from weak coils",
                "Tight rear cylinder access",
            ],
            "must_replace": ["Spark plugs", "Coil boots if worn"],
            "while_youre_there": ["Ignition coils if weak", "Fuel filter if overdue", "Throttle body cleaning"],
            "critical_checks": [
                "Plug thread damage",
                "Coil boot carbon tracking",
                "Misfire return from weak coils",
                "Tight rear cylinder access",
            ],
            "required_parts": ["Spark plugs", "Coil boots if worn"],
            "recommended_parts": ["Ignition coils if weak", "Fuel filter if overdue", "Throttle body cleaning"],
            "common_add_on_repairs": ["Ignition coils if weak", "Fuel filter if overdue", "Throttle body cleaning"],
            "labor_benchmark": "1.9 hr",
            "critical_specs": [
                {"label": "Plug torque", "value": "Ford 5.4L 2V spec"},
                {"label": "Plug gap", "value": "Verify by emissions label"},
                {"label": "Coil bolt torque", "value": "Service spec"},
            ],
        }
    ],
    "head-gasket-repair": [
        {
            "match": {"year": "2008", "make": "Toyota", "model": "Sequoia", "engine": "5.7"},
            "vehicle_specific": True,
            "snapshot": [
                {"label": "Risk", "value": "High"},
                {"label": "Labor", "value": "18-24 hr"},
                {"label": "Timing disturbed", "value": "Yes"},
                {"label": "TDC required", "value": "Yes"},
                {"label": "TTY bolts", "value": "Verify / replace if applicable"},
            ],
            "watch_for": [
                "Confirm root cause before teardown",
                "Cylinder-specific coolant intrusion",
                "Timing chain alignment",
                "Head/block deck warpage",
                "Cooling system pressure loss",
            ],
            "must_replace": [
                "Head gasket set",
                "Head bolts if TTY / one-time-use",
                "Coolant",
                "Oil and filter",
            ],
            "while_youre_there": [
                "Water pump",
                "Thermostat",
                "Radiator hoses",
                "Spark plugs",
                "Valve cover gaskets",
                "Timing cover seals",
            ],
            "critical_checks": [
                "Confirm root cause before teardown",
                "Cylinder-specific coolant intrusion",
                "Timing chain alignment",
                "Head/block deck warpage",
                "Cooling system pressure loss",
            ],
            "required_parts": ["Head gasket set", "Head bolts if TTY / one-time-use", "Coolant", "Oil and filter"],
            "recommended_parts": ["Water pump", "Thermostat", "Radiator hoses", "Spark plugs", "Valve cover gaskets", "Timing cover seals"],
            "common_add_on_repairs": ["Water pump", "Thermostat", "Radiator hoses", "Spark plugs", "Valve cover gaskets", "Timing cover seals"],
            "labor_benchmark": "18-24 hr",
            "critical_specs": [
                {"label": "Head bolt sequence", "value": "Toyota 5.7L service info"},
                {"label": "Intake manifold torque", "value": "Toyota 5.7L service info"},
                {"label": "Exhaust manifold torque", "value": "Toyota 5.7L service info"},
                {"label": "Timing alignment", "value": "TDC / chain marks required"},
                {"label": "Coolant capacity", "value": "Verify by exact configuration"},
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
    watch_for = blueprint.get("watch_for") or blueprint.get("critical_checks") or []
    must_replace = blueprint.get("must_replace") or blueprint.get("required_parts") or []
    while_youre_there = (
        blueprint.get("while_youre_there")
        or blueprint.get("common_add_on_repairs")
        or blueprint.get("recommended_parts")
        or []
    )
    return {
        "labor_benchmark": blueprint.get("labor_benchmark") or "Not set",
        "watch_for_count": len(watch_for),
        "must_replace_count": len(must_replace),
        "while_youre_there_count": len(while_youre_there),
        "critical_checks_count": len(blueprint.get("critical_checks") or []),
        "required_parts_count": len(blueprint.get("required_parts") or []),
        "recommended_parts_count": len(blueprint.get("recommended_parts") or []),
        "known_patterns_count": len(blueprint.get("known_failure_patterns") or []),
        "vehicle_specific": bool(blueprint.get("vehicle_specific")),
    }
