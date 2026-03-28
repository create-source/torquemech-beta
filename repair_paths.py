REPAIR_PATHS = {
    "P0301": {
        "title": "Cylinder 1 Misfire",
        "severity": "moderate",
        "symptoms": [
            "Rough idle",
            "Loss of power",
            "Engine shaking",
        ],
        "causes": [
            {
                "label": "Spark Plug",
                "image": "/static/repair-path/spark-plug.webp",
                "check": "Inspect for wear, oil fouling, or heavy carbon buildup.",
            },
            {
                "label": "Ignition Coil",
                "image": "/static/repair-path/ignition-coil.webp",
                "check": "Swap coil to another cylinder and see if the misfire follows.",
            },
            {
                "label": "Fuel Injector",
                "image": "/static/repair-path/fuel-injector.webp",
                "check": "Listen for injector clicking and inspect connector condition.",
            },
            {
                "label": "Vacuum Leak",
                "image": "/static/repair-path/vacuum-leak.webp",
                "check": "Inspect nearby hoses and intake seals for cracks or leaks.",
            },
        ],
        "electrical": {
            "title": "Cylinder 1 Ignition / Injector Path",
            "diagram_image": "/static/diagrams/ignition_injector_path.svg",
            "items": [
                "Check ignition coil power supply",
                "Inspect injector connector and wiring",
                "Verify engine ground integrity",
            ],
        },
        "repairs": [
            {
                "label": "Replace Spark Plug",
                "labor_range": "0.4–1.0 hr",
                "service_code": "spark_plug_replacement",
            },
            {
                "label": "Replace Ignition Coil",
                "labor_range": "0.5–1.2 hr",
                "service_code": "ignition_coil_replacement",
            },
            {
                "label": "Repair Injector Wiring",
                "labor_range": "0.8–2.0 hr",
                "service_code": "injector_wiring_repair",
            },
        ],
    }
}