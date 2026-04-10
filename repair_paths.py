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


def _cylinder_misfire_path(cylinder: int) -> dict:
    return {
        "title": f"Cylinder {cylinder} Misfire",
        "severity": "moderate",
        "symptoms": [
            "Rough idle at stoplights",
            "Loss of power under load",
            "Engine shake or stumble on acceleration",
            "Flashing check engine light if the misfire is severe",
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
                "check": "Swap the coil to another cylinder and see if the misfire follows.",
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
            "title": f"Cylinder {cylinder} Ignition / Injector Path",
            "diagram_image": "/static/diagrams/ignition_injector_path.svg",
            "items": [
                "Check ignition coil power and ground on the affected cylinder",
                "Inspect injector connector fit, pin drag, and harness routing",
                "Verify shared engine ground integrity before replacing parts",
            ],
        },
        "repairs": [
            {
                "label": "Replace Spark Plug",
                "labor_range": "0.4-1.0 hr",
                "service_code": "spark_plug_replacement_4_cyl",
            },
            {
                "label": "Replace Ignition Coil",
                "labor_range": "0.5-1.2 hr",
                "service_code": "ignition_coil_replacement_each",
            },
            {
                "label": "Replace Fuel Injector",
                "labor_range": "1.0-2.2 hr",
                "service_code": "fuel_injector_replacement_each",
            },
            {
                "label": "Perform Vacuum Leak Smoke Test",
                "labor_range": "0.8-2.5 hr",
                "service_code": "vacuum_leak_diagnosis_smoke_test",
            },
        ],
    }


def _lean_condition_path(bank_label: str) -> dict:
    return {
        "title": f"Lean Condition - {bank_label}",
        "severity": "moderate",
        "symptoms": [
            "Rough idle or light stumble",
            "Hesitation on tip-in or light acceleration",
            "Higher-than-normal fuel trims",
            "Possible pinging or lack of power under load",
        ],
        "causes": [
            {
                "label": "Vacuum Leak",
                "check": "Smoke test intake hoses, PCV plumbing, and manifold seals.",
            },
            {
                "label": "Dirty MAF Sensor",
                "check": "Compare MAF readings to expected airflow at idle and cruise.",
            },
            {
                "label": "Fuel Delivery Issue",
                "check": "Review fuel pressure and injector balance if trims stay high.",
            },
            {
                "label": "Exhaust Leak",
                "check": "Inspect for leaks ahead of the upstream sensor on the affected bank.",
            },
        ],
        "electrical": {
            "title": f"{bank_label} Air Metering / Fuel Trim Checks",
            "items": [
                "Review short- and long-term fuel trims at idle and cruise",
                "Inspect MAF signal plausibility before replacing sensors",
                "Check upstream O2 or A/F sensor response on the affected bank",
                "Verify there is no shared ground or connector issue skewing sensor data",
            ],
        },
        "repairs": [
            {
                "label": "Perform Vacuum Leak Smoke Test",
                "labor_range": "0.8-2.5 hr",
                "service_code": "vacuum_leak_diagnosis_smoke_test",
            },
            {
                "label": "Replace Mass Air Flow Sensor",
                "labor_range": "0.5-1.0 hr",
                "service_code": "mass_air_flow_sensor_replacement",
            },
            {
                "label": "Fuel System Diagnostic",
                "labor_range": "0.8-2.5 hr",
                "service_code": "fuel_system_diagnostic",
            },
            {
                "label": "Service PCV System",
                "labor_range": "0.8-3.0 hr",
                "service_code": "pcv_system_service",
            },
        ],
    }


def _evap_path(title: str, leak_scope: str) -> dict:
    return {
        "title": title,
        "severity": "low",
        "symptoms": [
            "Check engine light with little or no drivability change",
            "Fuel smell around the vehicle after parking or refueling",
            "EVAP monitor not ready for emissions testing",
        ],
        "causes": [
            {
                "label": "Gas Cap / Seal",
                "check": "Inspect the cap seal and filler neck for damage or debris.",
            },
            {
                "label": "EVAP Hose Leak",
                "check": f"Look for {leak_scope} leaks at tank, canister, and engine-bay lines.",
            },
            {
                "label": "Purge or Vent Valve Fault",
                "check": "Command the valves if possible and inspect their connectors and hoses.",
            },
        ],
        "electrical": {
            "title": "EVAP Purge / Vent Checks",
            "items": [
                "Verify gas cap seal and filler neck condition first",
                "Smoke test lines from canister to engine bay",
                "Command purge and vent valves with a scan tool if available",
                "Inspect vent valve wiring and connectors for corrosion or damage",
            ],
        },
        "repairs": [
            {
                "label": "EVAP Leak Smoke Test",
                "labor_range": "0.8-2.5 hr",
                "service_code": "evap_leak_test_smoke_test",
            },
            {
                "label": "Replace EVAP Purge Valve",
                "labor_range": "0.8-1.5 hr",
                "service_code": "evap_purge_valve_replacement",
            },
            {
                "label": "Replace EVAP Vent Valve",
                "labor_range": "0.8-1.5 hr",
                "service_code": "evap_vent_valve_replacement",
            },
        ],
    }


REPAIR_PATHS = {
    "P0300": {
        "title": "Random / Multiple Cylinder Misfire",
        "severity": "moderate",
        "symptoms": [
            "Rough idle or random stumble",
            "Hesitation under acceleration",
            "Flashing check engine light during heavier load",
            "Reduced fuel economy and poor throttle response",
        ],
        "causes": [
            {
                "label": "Ignition System",
                "check": "Inspect coils, plugs, and shared ignition power on affected cylinders.",
            },
            {
                "label": "Fuel Delivery",
                "check": "Review injector operation and fuel pressure when multiple cylinders misfire.",
            },
            {
                "label": "Air / Vacuum Leak",
                "check": "Smoke test the intake path and inspect the MAF before replacing parts.",
            },
            {
                "label": "Sensor Input Issue",
                "check": "Confirm MAF and fuel trim data make sense before going deeper.",
            },
        ],
        "electrical": {
            "title": "Ignition / Fuel Trim Checks",
            "items": [
                "Review misfire counters and fuel trims on the scan tool",
                "Check coil power supply and shared grounds on affected cylinders",
                "Inspect injector connectors and MAF wiring before replacing parts",
                "Verify no intake leak is skewing bank-to-bank airflow readings",
            ],
        },
        "repairs": [
            {
                "label": "Replace Spark Plugs",
                "labor_range": "0.6-1.5 hr",
                "service_code": "spark_plug_replacement_4_cyl",
            },
            {
                "label": "Replace Ignition Coil",
                "labor_range": "0.5-1.2 hr",
                "service_code": "ignition_coil_replacement_each",
            },
            {
                "label": "Fuel Injector Cleaning",
                "labor_range": "0.8-1.5 hr",
                "service_code": "fuel_injector_cleaning_on_car",
            },
            {
                "label": "Perform Vacuum Leak Smoke Test",
                "labor_range": "0.8-2.5 hr",
                "service_code": "vacuum_leak_diagnosis_smoke_test",
            },
        ],
    },
    "P0301": _cylinder_misfire_path(1),
    "P0302": _cylinder_misfire_path(2),
    "P0303": _cylinder_misfire_path(3),
    "P0304": _cylinder_misfire_path(4),
    "P0171": _lean_condition_path("Bank 1"),
    "P0174": _lean_condition_path("Bank 2"),
    "P0420": {
        "title": "Catalyst Efficiency Below Threshold - Bank 1",
        "severity": "low_moderate",
        "symptoms": [
            "Check engine light with mild or no drivability symptoms",
            "Failed emissions inspection",
            "Sulfur smell after hard driving in some cases",
            "Reduced power if the converter is starting to restrict flow",
        ],
        "causes": [
            {
                "label": "Aging Catalyst",
                "check": "Confirm the converter is actually weak before replacing it.",
            },
            {
                "label": "Downstream O2 Sensor Bias",
                "check": "Compare downstream sensor switching to upstream activity.",
            },
            {
                "label": "Exhaust Leak",
                "check": "Inspect for leaks ahead of the converter or rear sensor.",
            },
            {
                "label": "Underlying Rich / Misfire Condition",
                "check": "Fix misfire or fuel-trim faults first so a new converter is not damaged.",
            },
        ],
        "electrical": {
            "title": "Catalyst / Downstream O2 Checks",
            "items": [
                "Compare upstream and downstream O2 sensor switching behavior",
                "Check for exhaust leaks before the converter and sensor",
                "Review fuel trims, misfire history, and coolant temp data first",
                "Verify the engine is reaching closed loop and normal operating temperature",
            ],
        },
        "repairs": [
            {
                "label": "Catalyst Efficiency Diagnosis",
                "labor_range": "0.8-2.5 hr",
                "service_code": "catalyst_efficiency_diagnosis",
            },
            {
                "label": "Replace Downstream Oxygen Sensor",
                "labor_range": "0.8-1.2 hr",
                "service_code": "oxygen_sensor_replacement_downstream",
            },
            {
                "label": "Repair Exhaust Leak",
                "labor_range": "1.0-4.5 hr",
                "service_code": "exhaust_leak_repair",
            },
            {
                "label": "Replace Catalytic Converter",
                "labor_range": "2.0-5.0 hr",
                "service_code": "catalytic_converter_replacement",
            },
        ],
    },
    "P0442": {
        **_evap_path("EVAP Small Leak", "small"),
        "repairs": [
            {
                "label": "EVAP Small Leak Diagnosis",
                "labor_range": "0.8-2.5 hr",
                "service_code": "evap_small_leak_diagnosis",
            },
            {
                "label": "EVAP Leak Smoke Test",
                "labor_range": "0.8-2.5 hr",
                "service_code": "evap_leak_test_smoke_test",
            },
            {
                "label": "Replace EVAP Purge Valve",
                "labor_range": "0.8-1.5 hr",
                "service_code": "evap_purge_valve_replacement",
            },
            {
                "label": "Replace EVAP Vent Valve",
                "labor_range": "0.8-1.5 hr",
                "service_code": "evap_vent_valve_replacement",
            },
        ],
    },
    "P0455": {
        **_evap_path("EVAP Gross Leak", "large"),
        "symptoms": [
            "Check engine light with little or no drivability change",
            "Stronger fuel smell than a small EVAP leak",
            "EVAP monitor not ready for emissions testing",
            "Loose or missing gas cap is common",
        ],
        "repairs": [
            {
                "label": "EVAP System Diagnosis",
                "labor_range": "0.8-2.5 hr",
                "service_code": "evap_system_diagnosis",
            },
            {
                "label": "EVAP Leak Smoke Test",
                "labor_range": "0.8-2.5 hr",
                "service_code": "evap_leak_test_smoke_test",
            },
            {
                "label": "Replace EVAP Purge Valve",
                "labor_range": "0.8-1.5 hr",
                "service_code": "evap_purge_valve_replacement",
            },
            {
                "label": "Replace EVAP Vent Valve",
                "labor_range": "0.8-1.5 hr",
                "service_code": "evap_vent_valve_replacement",
            },
        ],
    },
}


def _maf_performance_path() -> dict:
    return {
        "title": "MAF Range / Performance",
        "severity": "moderate",
        "symptoms": [
            "Hesitation or stumble on acceleration",
            "Surging or unstable idle",
            "Poor fuel economy",
            "Reduced power under load",
        ],
        "causes": [
            {"label": "Dirty or biased MAF sensor", "check": "Compare grams-per-second readings to expected airflow."},
            {"label": "Intake leak after the MAF", "check": "Inspect clamps, ducting, and unmetered air paths."},
            {"label": "Restricted air filter or ducting", "check": "Inspect the air box, filter, and snorkel for restrictions."},
            {"label": "Throttle body contamination", "check": "Inspect the throttle plate for carbon buildup affecting airflow."},
        ],
        "electrical": {
            "title": "Air Metering Checks",
            "items": [
                "Compare MAF readings to RPM and calculated load",
                "Inspect the MAF connector, reference, and ground before replacing parts",
                "Check for intake leaks after the MAF housing",
                "Verify the air filter and ducting are not restricted or collapsed",
            ],
        },
        "repairs": [
            {"label": "Replace Mass Air Flow Sensor", "labor_range": "0.5-1.0 hr", "service_code": "mass_air_flow_sensor_replacement"},
            {"label": "Perform Vacuum Leak Smoke Test", "labor_range": "0.8-2.5 hr", "service_code": "vacuum_leak_diagnosis_smoke_test"},
            {"label": "Perform Intake Leak Diagnosis", "labor_range": "0.8-2.5 hr", "service_code": "intake_leak_diagnosis"},
            {"label": "Clean Throttle Body", "labor_range": "0.6-1.0 hr", "service_code": "throttle_body_cleaning"},
        ],
    }


def _iat_high_path() -> dict:
    return {
        "title": "Intake Air Temperature High Input",
        "severity": "low_moderate",
        "symptoms": [
            "Rich running on cold start",
            "Poor fuel economy",
            "Delayed drivability correction after startup",
            "Check engine light with little else at times",
        ],
        "causes": [
            {"label": "Open IAT circuit", "check": "Inspect the sensor connector for spread terminals or broken wires."},
            {"label": "Failed IAT sensor", "check": "Compare sensor resistance or scan-tool temperature to ambient."},
            {"label": "Integrated MAF/IAT issue", "check": "Some vehicles house the IAT inside the MAF assembly."},
        ],
        "electrical": {
            "title": "IAT Circuit Checks",
            "items": [
                "Compare IAT reading to ambient temperature on a cold soak",
                "Inspect the connector and harness for opens or broken conductors",
                "Verify the shared sensor reference and ground are stable",
                "Confirm whether the IAT is integrated into the MAF assembly before replacing parts",
            ],
        },
        "repairs": [
            {"label": "Replace Mass Air Flow Sensor", "labor_range": "0.5-1.0 hr", "service_code": "mass_air_flow_sensor_replacement"},
            {"label": "Perform Intake Leak Diagnosis", "labor_range": "0.8-2.5 hr", "service_code": "intake_leak_diagnosis"},
            {"label": "Throttle Body Service", "labor_range": "0.8-1.5 hr", "service_code": "throttle_body_service"},
        ],
    }


def _thermostat_path() -> dict:
    return {
        "title": "Engine Not Reaching Normal Temperature",
        "severity": "low_moderate",
        "symptoms": [
            "Long warm-up time",
            "Weak cabin heat",
            "Poor fuel economy",
            "Coolant temperature stays lower than expected on the scan tool",
        ],
        "causes": [
            {"label": "Thermostat stuck open", "check": "Monitor coolant temperature rise from a cold start."},
            {"label": "Low coolant or air pocket", "check": "Check coolant level and bleed the system if needed."},
            {"label": "ECT sensor bias", "check": "Compare scan-tool data to actual engine temperature."},
        ],
        "electrical": {
            "title": "Cooling System / Sensor Checks",
            "items": [
                "Compare ECT to ambient on a cold engine",
                "Verify coolant level before chasing sensor faults",
                "Check thermostat housing for seepage or prior repairs",
                "Confirm the engine reaches closed loop at a normal temperature",
            ],
        },
        "repairs": [
            {"label": "Replace Thermostat", "labor_range": "1.0-3.0 hr", "service_code": "thermostat_replacement"},
            {"label": "Replace Thermostat Housing", "labor_range": "1.0-3.0 hr", "service_code": "thermostat_housing_replacement"},
            {"label": "Replace Coolant Temperature Sensor", "labor_range": "0.8-1.5 hr", "service_code": "coolant_temperature_sensor_replacement"},
        ],
    }


def _egr_flow_path() -> dict:
    return {
        "title": "EGR Flow Insufficient",
        "severity": "moderate",
        "symptoms": [
            "Check engine light with little or no drivability change",
            "Spark knock or ping under load on some vehicles",
            "Failed emissions inspection",
            "Part-throttle hesitation on some systems",
        ],
        "causes": [
            {"label": "Carbon-clogged EGR passages", "check": "Inspect passages and ports for carbon restriction."},
            {"label": "Faulty EGR valve", "check": "Command or test valve movement if applicable."},
            {"label": "Vacuum or control fault", "check": "Inspect control hoses, solenoids, and feedback sensors on older systems."},
        ],
        "electrical": {
            "title": "EGR Command / Feedback Checks",
            "items": [
                "Verify the PCM is commanding EGR when operating conditions are correct",
                "Inspect feedback or DPFE sensor response if equipped",
                "Check vacuum supply and solenoid operation on vacuum-controlled systems",
                "Confirm the passages are not carbon-blocked before replacing the valve",
            ],
        },
        "repairs": [
            {"label": "Perform EGR Diagnosis", "labor_range": "0.8-2.5 hr", "service_code": "egr_diagnosis_if_applicable"},
            {"label": "Replace EGR Valve", "labor_range": "1.0-2.5 hr", "service_code": "egr_valve_replacement_if_applicable"},
            {"label": "Perform Vacuum Leak Smoke Test", "labor_range": "0.8-2.5 hr", "service_code": "vacuum_leak_diagnosis_smoke_test"},
        ],
    }


def _catalyst_path(title: str) -> dict:
    return {
        "title": title,
        "severity": "low_moderate",
        "symptoms": [
            "Check engine light with mild or no drivability symptoms",
            "Failed emissions inspection",
            "Possible sulfur smell after hard driving",
            "Reduced power if the converter is becoming restricted",
        ],
        "causes": [
            {"label": "Aging catalyst", "check": "Confirm converter efficiency is actually low before replacement."},
            {"label": "Rear O2 sensor bias", "check": "Compare downstream switching to upstream sensor activity."},
            {"label": "Exhaust leak", "check": "Inspect for leaks before the converter and rear sensor."},
            {"label": "Underlying rich or misfire condition", "check": "Correct upstream engine faults first."},
        ],
        "electrical": {
            "title": "Catalyst / O2 Checks",
            "items": [
                "Compare upstream and downstream O2 sensor activity",
                "Inspect for exhaust leaks ahead of the catalyst",
                "Review fuel trims, misfire data, and coolant temperature",
                "Confirm the engine reaches closed loop and normal operating temperature",
            ],
        },
        "repairs": [
            {"label": "Catalyst Efficiency Diagnosis", "labor_range": "0.8-2.5 hr", "service_code": "catalyst_efficiency_diagnosis"},
            {"label": "Replace Downstream Oxygen Sensor", "labor_range": "0.8-1.2 hr", "service_code": "oxygen_sensor_replacement_downstream"},
            {"label": "Repair Exhaust Leak", "labor_range": "1.0-4.5 hr", "service_code": "exhaust_leak_repair"},
            {"label": "Replace Catalytic Converter", "labor_range": "2.0-5.0 hr", "service_code": "catalytic_converter_replacement"},
        ],
    }


def _idle_high_path() -> dict:
    return {
        "title": "Idle Speed Too High",
        "severity": "moderate",
        "symptoms": [
            "Idle speed higher than expected after warm-up",
            "Hanging RPM between shifts",
            "Rough or unstable idle",
            "Occasional stalling after throttle closure on some vehicles",
        ],
        "causes": [
            {"label": "Throttle body carbon buildup", "check": "Inspect the throttle plate and bore for sticking carbon."},
            {"label": "Vacuum leak", "check": "Smoke test the intake and PCV system for unmetered air."},
            {"label": "Throttle body or idle control fault", "check": "Verify commanded versus actual throttle angle."},
            {"label": "Airflow meter issue", "check": "Review MAF or load calculations for plausibility."},
        ],
        "electrical": {
            "title": "Idle Control Checks",
            "items": [
                "Check commanded idle speed against actual RPM",
                "Inspect the throttle body for sticking or contamination",
                "Smoke test for vacuum leaks before replacing the throttle body",
                "Review airflow and throttle angle data for plausibility",
            ],
        },
        "repairs": [
            {"label": "Clean Throttle Body", "labor_range": "0.6-1.0 hr", "service_code": "throttle_body_cleaning"},
            {"label": "Throttle Body Service", "labor_range": "0.8-1.5 hr", "service_code": "throttle_body_service"},
            {"label": "Perform Vacuum Leak Smoke Test", "labor_range": "0.8-2.5 hr", "service_code": "vacuum_leak_diagnosis_smoke_test"},
            {"label": "Replace Throttle Body", "labor_range": "1.0-2.5 hr", "service_code": "throttle_body_replacement"},
        ],
    }


def _transmission_request_path() -> dict:
    return {
        "title": "TCM Requested MIL Illumination",
        "severity": "moderate",
        "symptoms": [
            "Check engine light with transmission warning behavior",
            "Harsh shifts or limp mode",
            "Delayed engagement or abnormal shift timing",
            "Transmission-related companion codes stored in the TCM",
        ],
        "causes": [
            {"label": "Stored transmission fault", "check": "Scan the TCM for the actual underlying code first."},
            {"label": "Fluid condition problem", "check": "Check fluid level, color, and odor before deeper teardown."},
            {"label": "Wiring or connector issue", "check": "Inspect the transmission connector for fluid intrusion or damage."},
            {"label": "Solenoid or internal fault", "check": "Use companion TCM codes to narrow the circuit or hydraulic problem."},
        ],
        "electrical": {
            "title": "Transmission Control Checks",
            "items": [
                "Scan the TCM for companion codes before quoting repairs",
                "Inspect transmission connector condition and harness routing",
                "Review fluid level and condition first",
                "Verify input and output speed sensor data if available",
            ],
        },
        "repairs": [
            {"label": "Perform Transmission Diagnostic", "labor_range": "0.8-2.5 hr", "service_code": "transmission_diagnostic"},
            {"label": "Perform Transmission Fluid Service", "labor_range": "1.0-2.5 hr", "service_code": "transmission_fluid_service"},
            {"label": "Replace Solenoid Pack", "labor_range": "1.0-4.0 hr", "service_code": "solenoid_pack_replacement_if_applicable"},
            {"label": "Replace Transmission", "labor_range": "6.0-18.0 hr", "service_code": "transmission_replacement"},
        ],
    }


def _tcc_path() -> dict:
    return {
        "title": "Torque Converter Clutch Not Locking",
        "severity": "moderate_high",
        "symptoms": [
            "Higher RPM than normal at cruise",
            "Converter clutch shudder or no lockup",
            "Poor highway fuel economy",
            "Heat buildup from excess slip",
        ],
        "causes": [
            {"label": "Low or degraded transmission fluid", "check": "Check fluid level, condition, and service history."},
            {"label": "TCC solenoid or valve body issue", "check": "Use scan data to confirm command versus actual lockup."},
            {"label": "Wiring or connector fault", "check": "Inspect for fluid intrusion at the transmission connector."},
            {"label": "Torque converter wear", "check": "Confirm the converter is failing before major repairs."},
        ],
        "electrical": {
            "title": "TCC / Transmission Checks",
            "items": [
                "Review commanded TCC lockup versus actual slip speed",
                "Check fluid level and condition before replacing parts",
                "Inspect the transmission connector and harness for fluid intrusion",
                "Look for companion transmission codes that narrow the failure path",
            ],
        },
        "repairs": [
            {"label": "Perform Transmission Diagnostic", "labor_range": "0.8-2.5 hr", "service_code": "transmission_diagnostic"},
            {"label": "Perform Transmission Fluid Service", "labor_range": "1.0-2.5 hr", "service_code": "transmission_fluid_service"},
            {"label": "Replace Torque Converter", "labor_range": "6.0-12.0 hr", "service_code": "torque_converter_replacement_if_applicable"},
            {"label": "Replace Solenoid Pack", "labor_range": "1.0-4.0 hr", "service_code": "solenoid_pack_replacement_if_applicable"},
        ],
    }


def _air_fuel_sensor_path() -> dict:
    return {
        "title": "Upstream O2 / A-F Sensor Stuck Lean",
        "severity": "moderate",
        "symptoms": [
            "Lean surge or hesitation",
            "Poor cold-start fueling correction",
            "High positive fuel trims",
            "Check engine light with fuel-control symptoms",
        ],
        "causes": [
            {"label": "Vacuum leak", "check": "Smoke test intake and PCV paths before replacing sensors."},
            {"label": "Exhaust leak ahead of sensor", "check": "Inspect the manifold and front pipe for false oxygen entry."},
            {"label": "Fuel delivery issue", "check": "Confirm pressure and injector performance if trims stay high."},
            {"label": "Sensor bias or failure", "check": "Verify sensor response rate and commanded fuel correction."},
        ],
        "electrical": {
            "title": "Fuel Control / Sensor Checks",
            "items": [
                "Review fuel trims at idle and cruise",
                "Inspect for intake or exhaust leaks before replacing the sensor",
                "Verify upstream sensor response on the scan tool",
                "Check fuel pressure and injector performance if the lean condition is real",
            ],
        },
        "repairs": [
            {"label": "Replace Air Fuel Ratio Sensor", "labor_range": "0.8-1.5 hr", "service_code": "air_fuel_ratio_sensor_replacement"},
            {"label": "Replace Upstream Oxygen Sensor", "labor_range": "0.8-1.2 hr", "service_code": "oxygen_sensor_replacement_upstream"},
            {"label": "Perform Vacuum Leak Smoke Test", "labor_range": "0.8-2.5 hr", "service_code": "vacuum_leak_diagnosis_smoke_test"},
            {"label": "Perform Fuel System Diagnostic", "labor_range": "0.8-2.5 hr", "service_code": "fuel_system_diagnostic"},
        ],
    }


REPAIR_PATHS.update({
    "P0101": _maf_performance_path(),
    "P0113": _iat_high_path(),
    "P0128": _thermostat_path(),
    "P0401": _egr_flow_path(),
    "P0430": _catalyst_path("Catalyst Efficiency Below Threshold - Bank 2"),
    "P0507": _idle_high_path(),
    "P0700": _transmission_request_path(),
    "P0741": _tcc_path(),
    "P0456": {
        **_evap_path("EVAP Very Small Leak", "very small"),
        "repairs": [
            {"label": "EVAP Small Leak Diagnosis", "labor_range": "0.8-2.5 hr", "service_code": "evap_small_leak_diagnosis"},
            {"label": "EVAP Leak Smoke Test", "labor_range": "0.8-2.5 hr", "service_code": "evap_leak_test_smoke_test"},
            {"label": "Replace EVAP Purge Valve", "labor_range": "0.8-1.5 hr", "service_code": "evap_purge_valve_replacement"},
            {"label": "Replace EVAP Vent Valve", "labor_range": "0.8-1.5 hr", "service_code": "evap_vent_valve_replacement"},
        ],
    },
    "P2195": _air_fuel_sensor_path(),
})
