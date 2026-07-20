# TorqueMech Services Catalog Standard

This standard documents the current `services_catalog.json` contract and the Phase 3.1 direction for expanding the service catalog without breaking existing estimator behavior.

## Source of Truth

The live estimator service catalog is `services_catalog.json` at the project root. Existing service codes are stable identifiers. Do not rename existing codes unless a migration and compatibility plan exists.

The current loader safely accepts additional JSON fields on categories and services, but estimator API compatibility must be preserved.

## Required Category Fields

Each category object must include:

- `key`: nonempty lowercase identifier used by the estimator API and frontend.
- `name`: nonempty customer-readable category label.

Optional category field:

- `description`: short category description. This can safely coexist in the JSON catalog, but current `/api/categories` responses expose only `key` and `name`.

Each category owns a `services` array. Every service must appear inside exactly one category.

## Required Service Fields

Each service object must include:

- `code`: stable lowercase snake_case service identifier.
- `name`: customer-readable service name.
- `labor_hours_min`: numeric minimum labor estimate, greater than zero.
- `labor_hours_max`: numeric maximum labor estimate, greater than zero and not less than `labor_hours_min`.

These fields are required for current estimator service loading and labor default behavior.

## Recommended Searchable Metadata

The following optional fields can safely coexist with the current loader:

- `aliases`: array of nonempty strings for common alternate names.
- `keywords`: array of nonempty strings for search terms, symptoms, OBD phrases, and shop shorthand.
- `summary`: nonempty customer-readable one-sentence description.
- `symptoms`: array of nonempty strings for customer complaint phrases.

When adding metadata, prefer terms a customer or technician would actually type, such as "no crank," "pedal pulsation," "coolant leak," or "battery light."

## Service Code Convention

Service codes must be lowercase snake_case:

```text
system_component_action
```

Examples:

- `cooling_water_pump_replacement`
- `brakes_front_pads_replacement`
- `electrical_alternator_testing`

Current codes may not always follow the ideal system/component/action pattern. Existing codes remain stable even if imperfect.

Rules:

- Use descriptive component/action wording.
- Avoid vehicle-specific wording unless the service only applies to that vehicle family or technology.
- Use `front` and `rear` before the component when axle position changes labor or parts.
- Use `left` and `right` only when side-specific quoting is required; otherwise prefer `each`, `pair`, or an axle-level code.
- Use `upper` and `lower` only when the component is meaningfully different, such as `upper_radiator_hose_replacement`.
- Use `_each` for one component priced per item.
- Use `_pair` for a two-part pair priced together.
- Use `_per_tire` for tire-level work where quantity usually multiplies.
- Use `_if_applicable` only when the vehicle may not have the component; avoid it for services that should become a better technology-specific category later.

## Service Naming Convention

Service names must be customer-readable Title Case.

Preferred action words:

- `Replacement`
- `Inspection`
- `Diagnosis`
- `Testing`
- `Calibration`
- `Service`

Rules:

- Distinguish diagnosis from repair. Do not name a diagnostic service like a replacement service.
- Distinguish each-side pricing from pair, set, and axle pricing in the visible name.
- Use parenthetical qualifiers when they clarify quote scope, such as `(each)`, `(pair)`, `(per tire)`, `(front)`, or `(rear)`.
- Avoid ambiguous duplicate concepts. If two services look similar, make the difference explicit in both code and name.
- Keep customer-facing names concise; put extra search language in metadata.

## Future Category Taxonomy

The proposed bumper-to-bumper taxonomy is:

- Maintenance & Inspections
- Engine Mechanical
- Engine Performance & Diagnostics
- Cooling System
- Fuel System
- Exhaust & Emissions
- Transmission
- Drivetrain & Differentials
- Steering
- Suspension
- Brakes
- Wheels, Tires & Alignment
- Starting & Charging
- Electrical & Wiring
- Lighting
- HVAC
- Glass, Mirrors & Wipers
- Interior & Accessories
- Body & Exterior
- Restraint & Safety Systems
- ADAS & Safety Electronics
- Hybrid & EV

Do not change the live category keys until compatibility work is planned.

## Future Mapping From Existing Categories

Existing categories can eventually map forward as follows:

- `maintenance` -> Maintenance & Inspections; some tire services may move to Wheels, Tires & Alignment.
- `engine` -> Engine Mechanical and Engine Performance & Diagnostics.
- `cooling` -> Cooling System.
- `brakes` -> Brakes; ABS or calibration work may move to ADAS & Safety Electronics or Electrical & Wiring.
- `suspension` -> Steering and Suspension.
- `drivetrain` -> Drivetrain & Differentials.
- `transmission` -> Transmission.
- `ac_heat` -> HVAC.
- `electrical` -> Starting & Charging, Electrical & Wiring, Lighting, Interior & Accessories, and Glass, Mirrors & Wipers.
- `fuel` -> Fuel System and Engine Performance & Diagnostics.
- `exhaust` -> Exhaust & Emissions.
- `body_paint` -> Body & Exterior, Glass, Mirrors & Wipers, and Interior & Accessories.
- `diagnostics` -> Engine Performance & Diagnostics, Electrical & Wiring, ADAS & Safety Electronics, and system-specific diagnostic categories.

When categories are split later, keep existing service codes stable and add aliases or redirect metadata where needed.
