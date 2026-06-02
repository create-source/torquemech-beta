# TorqueMech Repair Blueprint Architecture

Internal planning document for scalable TorqueMech Repair Intelligence and Repair Guides. This is documentation-only and does not define any routes, templates, UI, database changes, estimator behavior, repair guide behavior, or Parts Center behavior.

## Objective

TorqueMech should scale repair intelligence by separating reusable repair knowledge from vehicle-specific variation.

Target model:

```text
Master Repair Blueprint
+
Vehicle Overlay
=
Final Repair Guide
```

This avoids creating thousands of duplicate guides such as:

- 2002 Ford F-150 Brake Pads
- 2003 Ford F-150 Brake Pads
- 2004 Ford F-150 Brake Pads

Instead, TorqueMech maintains one high-quality `Brake Pad Replacement` blueprint and applies vehicle overlays only where vehicle-specific details matter.

## 1. Master Repair Blueprint Model

A master repair blueprint is the reusable source of truth for a repair procedure across most vehicles.

Examples:

- Brake Pad Replacement
- Alternator Replacement
- Water Pump Replacement

Recommended blueprint fields:

- `repair_name`: canonical repair name.
- `category`: repair system, such as brakes, charging, cooling, suspension, engine, or electrical.
- `difficulty`: general difficulty rating.
- `labor_range`: base labor range before vehicle-specific adjustments.
- `tools_required`: common tools needed for the repair.
- `safety_notes`: safety warnings, lift/support notes, battery disconnect guidance, fluid cautions, and PPE reminders.
- `procedure_steps`: step-by-step repair procedure.
- `common_mistakes`: frequent technician or DIY errors to avoid.
- `related_repairs`: nearby or commonly bundled repairs.
- `parts_needed`: standard parts and consumables.

Blueprint responsibilities:

- Explain the general repair path.
- Provide reusable labor context.
- Power repair guide pages, estimator preloads, and Parts Center suggestions.
- Stay vehicle-agnostic unless a detail applies broadly across most vehicles.

## 2. Vehicle Overlay Model

Vehicle-specific data should be stored separately from the master blueprint. Overlays modify or enrich the blueprint for a specific year/make/model, platform, engine, drivetrain, or trim group.

Examples:

- 2002 Ford F-150
- 2008 Toyota Sequoia
- 2015 Honda Accord

Recommended overlay fields:

- `vehicle_key`: normalized vehicle identity, such as year/make/model plus optional engine or drivetrain.
- `repair_key`: link back to the master blueprint.
- `torque_specs`: fastener torque values and tightening sequence notes.
- `socket_sizes`: common socket, wrench, hex, Torx, or specialty sizes.
- `drive_type`: FWD, RWD, AWD, 4WD, hybrid, EV, or other configuration notes.
- `labor_adjustments`: additions or reductions to the master labor range.
- `vehicle_specific_notes`: details that only apply to the vehicle or platform.
- `known_variations`: trim, production split, brake package, engine, axle, or market variations.
- `engine_specific_differences`: engine-dependent access, belt routing, cooling layout, alternator location, or component differences.

Overlay responsibilities:

- Add exact specs without duplicating the entire guide.
- Flag variations that affect labor or procedure order.
- Preserve a clean distinction between general repair knowledge and vehicle-specific repair intelligence.

## 3. Repair Intelligence Structure

Future repair guides should compose several intelligence layers around the master blueprint.

```text
Repair Guide
├─ Blueprint
├─ Torque Specs
├─ Symptoms
├─ Estimate
├─ Related Repairs
└─ Parts Center
```

Layer responsibilities:

- `Blueprint`: core reusable procedure, safety notes, tools, common mistakes, and parts needed.
- `Torque Specs`: vehicle overlay specs when available, with clear fallback messaging when unavailable.
- `Symptoms`: symptoms and diagnostic context connected to the repair.
- `Estimate`: labor range, likely repair path, and related repair preloads for the estimator.
- `Related Repairs`: adjacent repairs, bundled work, and follow-up inspections.
- `Parts Center`: recommended parts, related components, and consumables.

## 4. Scalability Goals

TorqueMech should avoid duplicating full repair content per vehicle. Duplicate guide generation becomes hard to maintain, creates inconsistent repair advice, and makes updates expensive.

Preferred architecture:

```text
Master Blueprint: Brake Pad Replacement
Vehicle Overlay: 2002 Ford F-150 front disc brake notes
Final Guide: Brake Pad Replacement for 2002 Ford F-150
```

Benefits:

- One master procedure can serve many vehicles.
- Vehicle-specific differences remain targeted and easier to validate.
- Updates to safety notes, common mistakes, tools, and general procedure steps propagate across all relevant guides.
- Overlays can be added incrementally for high-traffic vehicles first.
- Missing overlay data does not block the generic repair guide from working.

Overlay precedence:

1. Exact year/make/model/engine overlay.
2. Platform or generation overlay.
3. Make/model family overlay.
4. Master blueprint fallback.

## 5. Estimator Integration

Future flow:

```text
Repair Guide
→ Send To Estimator
→ Preload Labor Range
→ Preload Related Repairs
```

Recommended behavior:

- The repair guide exposes a future internal repair key, not free-form text only.
- The estimator receives the repair key, vehicle context, and overlay context.
- Base labor comes from the master blueprint.
- Labor adjustments come from the vehicle overlay when available.
- Related repairs preload as optional line items or suggestions, not automatic charges.
- Existing Beta estimator behavior should remain unchanged until this flow is intentionally implemented.

Example:

```text
Brake Pad Replacement guide
→ estimator receives repair_key=brake_pad_replacement
→ estimator loads base labor range
→ vehicle overlay adjusts labor for axle, brake package, or drivetrain
→ estimator suggests rotors, calipers, brake fluid, or hardware where appropriate
```

## 6. Parts Center Integration

Future flow:

```text
Repair Guide
→ Recommended Parts
→ Related Components
→ Common Bundled Repairs
```

Recommended behavior:

- `parts_needed` from the master blueprint provides baseline parts.
- Vehicle overlays refine part families, fitment notes, and known component variations.
- Parts Center can show related components such as hardware kits, gaskets, seals, fluids, belts, pulleys, or sensors.
- Common bundled repairs should come from blueprint relationships and overlay-specific patterns.

Example:

```text
Water Pump Replacement
→ recommended parts: water pump, gasket, coolant
→ related components: thermostat, serpentine belt, radiator cap
→ bundled repairs: coolant flush, thermostat replacement, belt replacement
```

## Future Data Shape

Potential internal structures:

```text
repair_blueprints
  id
  repair_key
  repair_name
  category
  difficulty
  labor_range_min
  labor_range_max
  tools_required
  safety_notes
  procedure_steps
  common_mistakes
  related_repairs
  parts_needed

vehicle_repair_overlays
  id
  repair_key
  year
  make
  model
  engine
  drivetrain
  torque_specs
  socket_sizes
  labor_adjustment_min
  labor_adjustment_max
  vehicle_specific_notes
  known_variations
  engine_specific_differences
```

These structures are planning concepts only. No database tables should be added until a later implementation phase.

## Guardrails

For this planning phase:

- Do not add routes.
- Do not add templates.
- Do not add UI.
- Do not change the database.
- Do not change estimator behavior.
- Do not change repair guide behavior.
- Do not change OBD behavior.
- Do not change PDF behavior.
- Do not change navigation.
- Do not change Parts Center behavior.

Repair Blueprint Architecture should remain an internal planning reference until a future implementation milestone.
