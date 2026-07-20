# Services Catalog Ambiguity Review

Phase 3.1 Batch 3 reviewed the validator's suspicious duplicate-concept warnings against the current `services_catalog.json`. No service codes, service names, labor hours, categories, or category assignments were changed.

## A. Executive Summary

Most duplicate-concept warnings are legitimate variants caused by location, diagnostic-versus-repair scope, or test-versus-replacement scope. These should remain separate quoteable services because they represent different labor, parts, customer intent, or repair workflow states.

Two pairs require future cleanup planning:

- `electrical_diagnostic` and `electrical_diagnosis`
- `heater_control_valve_replacement_if_applicable` and `heater_control_valve_replacement`

Both pairs are intentionally left unchanged in this batch. Any later consolidation needs a compatibility map because service codes are stable identifiers used by estimator links, repair content, saved estimates, and historical records.

## B. Review Of All Current Warnings

| Concept | Service codes | Canonical names | Classification | Both remain? | Reason | Future action | Search-confusion risk |
| --- | --- | --- | --- | --- | --- | --- | --- |
| alternator | `alternator_diagnosis`, `alternator_replacement` | Alternator Diagnosis; Alternator Replacement | diagnosis versus repair | Yes | Diagnosis prices testing and confirmation; replacement prices the alternator job. | Keep both; ensure aliases keep diagnosis language separate from replacement. | Low if "diagnosis" and "replacement" remain prominent. |
| battery | `battery_test`, `battery_replacement` | Battery Test; Battery Replacement | test/inspection versus replacement | Yes | Battery testing can precede replacement, but it is separately quoteable. | Keep both; use "battery load test" and "battery replacement" distinctly. | Low. |
| brake pads | `front_brake_pads_replacement`, `rear_brake_pads_replacement` | Front Brake Pads Replacement; Rear Brake Pads Replacement | legitimate location variant | Yes | Front and rear pad jobs differ by axle and quote scope. | Keep both; future taxonomy still Brakes. | Low if front/rear aliases stay scoped. |
| brake pads and rotors | `front_brake_pads_and_rotors_replacement`, `rear_brake_pads_and_rotors_replacement` | Front Brake Pads & Rotors Replacement; Rear Brake Pads & Rotors Replacement | legitimate location variant | Yes | Axle-specific combined brake jobs are distinct quotes. | Keep both. | Low. |
| brake rotors | `front_brake_rotors_replacement`, `rear_brake_rotors_replacement` | Front Brake Rotors Replacement; Rear Brake Rotors Replacement | legitimate location variant | Yes | Rotor labor and parts are axle-specific. | Keep both. | Low. |
| bumper cover | `bumper_cover_replacement_front`, `bumper_cover_replacement_rear` | Bumper Cover Replacement (Front); Bumper Cover Replacement (Rear) | legitimate location variant | Yes | Front and rear bumper covers differ in parts and procedure. | Keep both; map to Body & Exterior. | Low. |
| diff fluid inspect | `front_diff_service_fluid_inspect`, `rear_diff_service_fluid_inspect` | Front Diff Service (Fluid + Inspect); Rear Diff Service (Fluid + Inspect) | legitimate location variant | Yes | Front and rear differentials can be separate service locations. | Keep both; standardize "Diff" versus "Differential" wording later if desired. | Low to medium because "diff" is shorthand. |
| differential | `front_differential_replacement`, `rear_differential_replacement` | Front Differential Replacement; Rear Differential Replacement | legitimate location variant | Yes | Front and rear differential replacement are distinct assemblies and labor paths. | Keep both. | Low. |
| electrical | `electrical_diagnostic`, `electrical_diagnosis` | Electrical Diagnostic; Electrical Diagnosis | true ambiguity / naming inconsistency | Not decided | Names and labor ranges overlap; category placement differs. | Future controlled cleanup: pick one canonical current-code target and support legacy redirect/alias. | High because aliases and summaries are nearly identical. |
| heater control valve | `heater_control_valve_replacement_if_applicable`, `heater_control_valve_replacement` | Heater Control Valve Replacement (If Applicable); Heater Control Valve Replacement | true ambiguity / likely duplicate | Not decided | Same component and identical labor range, but one is in Cooling and one is in HVAC. | Future controlled cleanup: decide whether one code is legacy or whether category-specific quoting is intentional. | High because aliases are effectively identical. |
| oxygen sensor | `oxygen_sensor_replacement_upstream`, `oxygen_sensor_replacement_downstream` | Oxygen Sensor Replacement (Upstream); Oxygen Sensor Replacement (Downstream) | legitimate location variant | Yes | Upstream and downstream sensors have different diagnostic meaning and location. | Keep both; preserve upstream/downstream search terms. | Low if aliases do not collapse both into generic "oxygen sensor replacement." |
| radiator hose | `upper_radiator_hose_replacement`, `lower_radiator_hose_replacement` | Upper Radiator Hose Replacement; Lower Radiator Hose Replacement | legitimate location variant | Yes | Upper and lower hoses are different parts and can have different access. | Keep both. | Low. |
| starter | `starter_diagnosis`, `starter_replacement` | Starter Diagnosis; Starter Replacement | diagnosis versus repair | Yes | No-crank testing and starter replacement are distinct workflow stages. | Keep both; ensure "car won't start" searches can surface diagnosis before replacement. | Low to medium, because customer language often points to both. |
| throttle body | `throttle_body_service`, `throttle_body_replacement` | Throttle Body Service; Throttle Body Replacement | test/inspection/service versus replacement | Yes | Cleaning/service and replacement are different scopes. | Keep both; avoid using replacement aliases on service. | Medium if aliases overuse generic "throttle body repair." |
| transmission | `transmission_diagnostic`, `transmission_replacement` | Transmission Diagnostic; Transmission Replacement | diagnosis versus repair | Yes | A diagnostic quote is very different from transmission replacement. | Keep both; future taxonomy remains Transmission. | Low to medium. |
| wheel bearing | `wheel_bearing_replacement_front`, `wheel_bearing_replacement_rear` | Wheel Bearing Replacement (Front); Wheel Bearing Replacement (Rear) | legitimate location variant | Yes | Front and rear wheel bearings are distinct locations and labor paths. | Keep both; future taxonomy may move to Wheels, Tires & Alignment or Suspension depending taxonomy decision. | Low. |

## C. True Ambiguity Findings

### `electrical_diagnostic` Versus `electrical_diagnosis`

Current records:

- `electrical_diagnostic`: category `electrical`, name `Electrical Diagnostic`, labor `0.8` to `2.5`.
- `electrical_diagnosis`: category `diagnostics`, name `Electrical Diagnosis`, labor `0.8` to `2.5`.

Findings:

- They appear to represent the same generic quoteable service.
- The names differ only by noun/adjective form.
- Labor ranges are identical.
- `electrical_diagnostic` is referenced by symptom pages, repair guides, system hubs, and frontend search clusters.
- `electrical_diagnosis` is present in the generic Diagnostics category and may be selected through category browsing.

Recommendation:

- Do not consolidate until a redirect policy exists.
- Treat `electrical_diagnostic` as a likely legacy/public-link code because current content references it directly.
- If a future canonical code is chosen, keep both service codes resolvable for estimator prefill and historical records.

### `heater_control_valve_replacement_if_applicable` Versus `heater_control_valve_replacement`

Current records:

- `heater_control_valve_replacement_if_applicable`: category `cooling`, name `Heater Control Valve Replacement (If Applicable)`, labor `1.0` to `4.0`.
- `heater_control_valve_replacement`: category `ac_heat`, name `Heater Control Valve Replacement`, labor `1.0` to `4.0`.

Findings:

- Both describe replacement of the heater control valve.
- Labor ranges are identical.
- The difference appears to be category ownership and whether the component is optional by vehicle.
- Frontend service search references `heater_control_valve_replacement`.
- No repair guide references were found for either code in this review.

Recommendation:

- Do not consolidate in this batch.
- Future cleanup should decide whether HVAC owns this service, with Cooling retaining a legacy alias, or whether Cooling and HVAC need distinct quote scopes.
- Any consolidation requires a legacy-code compatibility map.

## D. Legacy Compatibility Risks

Service codes are stable IDs. Renaming or removing them can break:

- saved estimates and estimator line items
- repair-guide estimator links
- symptom and system-hub links
- frontend quick search and shortcut clusters
- bookmarks such as `/estimator?service=...`
- historical repair and invoice records
- any external API consumers or shared links

The highest-risk code from this review is `electrical_diagnostic` because it is referenced outside the catalog.

## E. Recommended Canonical And Redirect Policy

Future cleanup should use these rules:

- Keep service codes stable whenever possible.
- If a duplicate must be consolidated, pick a canonical service code and keep legacy codes as aliases or redirects.
- API lookup by legacy code should return the canonical service payload plus a `legacy_code` or compatibility marker only after API compatibility is deliberately designed.
- Frontend prefill should continue resolving old `service` query parameters.
- Historical records should keep the originally selected service code and display name.
- Repair guides and content links can migrate gradually after legacy lookup is in place.
- Do not change labor ranges during a redirect unless the pricing policy is reviewed separately.

## F. Items Approved To Remain As Legitimate Variants

The following reviewed pairs are legitimate and were added to the validator's reviewed duplicate-concept allowlist:

- `alternator_diagnosis` / `alternator_replacement`
- `battery_test` / `battery_replacement`
- `front_brake_pads_replacement` / `rear_brake_pads_replacement`
- `front_brake_pads_and_rotors_replacement` / `rear_brake_pads_and_rotors_replacement`
- `front_brake_rotors_replacement` / `rear_brake_rotors_replacement`
- `bumper_cover_replacement_front` / `bumper_cover_replacement_rear`
- `front_diff_service_fluid_inspect` / `rear_diff_service_fluid_inspect`
- `front_differential_replacement` / `rear_differential_replacement`
- `oxygen_sensor_replacement_upstream` / `oxygen_sensor_replacement_downstream`
- `upper_radiator_hose_replacement` / `lower_radiator_hose_replacement`
- `starter_diagnosis` / `starter_replacement`
- `throttle_body_service` / `throttle_body_replacement`
- `transmission_diagnostic` / `transmission_replacement`
- `wheel_bearing_replacement_front` / `wheel_bearing_replacement_rear`

## G. Items Requiring Future Controlled Cleanup

These remain warnings:

- `electrical_diagnostic` / `electrical_diagnosis`
- `heater_control_valve_replacement_if_applicable` / `heater_control_valve_replacement`

Future cleanup should include a compatibility map, a content-link audit, search-alias cleanup, and tests for legacy service resolution before any catalog consolidation.
