# TorqueMech Pro CRM Architecture Blueprint

Internal planning note for the future TorqueMech Pro CRM flow. This document is architecture-only and does not define any public Beta UI, routes, navigation, accounts, login, billing, subscriptions, or upgrade prompts.

## Beta Visibility Rule

Customer CRM remains dormant during Beta. The schema may initialize at startup, but no CRM pages, `/pro` routes, navigation links, public dashboards, customer profile screens, or shop-profile preview surfaces should be accessible until a future Pro release explicitly gates and exposes them.

Current Beta behavior should remain limited to existing public workflows:

- Repair estimator
- OBD lookup and diagnostic content
- Repair guides and cost pages
- PDF generation
- Parts Center

## 1. Customer Record

Table: `customers`

Purpose: stores the person or business attached to future saved estimates, vehicles, service history, and reminders.

Key fields:

- `id`: primary CRM customer identifier.
- `shop_id`: nullable tenant boundary for future shared-schema SaaS isolation.
- `first_name`, `last_name`: customer display and search identity.
- `phone`, `email`: contact and lookup fields.
- `notes`: internal shop notes.
- `created_at`, `updated_at`: audit timestamps.

Workflow role: a customer can own one or more vehicles and can be used as the anchor for estimate history, service history, reminders, approvals, and future communication records.

## 2. Customer Vehicle Record

Table: `customer_vehicles`

Purpose: stores vehicles tied to saved customers.

Key fields:

- `id`: primary vehicle identifier.
- `customer_id`: required link to `customers.id`.
- `shop_id`: nullable tenant boundary matching the customer and future shop context.
- `year`, `make`, `model`, `engine`: service identification fields.
- `vin`, `license_plate`, `mileage`: shop lookup and service context fields.
- `notes`: vehicle-specific internal notes.
- `created_at`, `updated_at`: audit timestamps.

Workflow role: a customer can have multiple vehicles. Each vehicle can accumulate service history and maintenance reminders.

## 3. Estimate Connection

There is no CRM-facing estimate persistence UI yet. Future estimate linkage should attach generated estimates to both `customer_id` and `vehicle_id` before creating or updating CRM records.

Recommended future behavior:

- Keep the current Beta estimator untouched until Pro is intentionally enabled.
- When Pro estimate saving is introduced, map estimator vehicle fields into `customer_vehicles`.
- Store estimate totals and status transitions in `service_history` when an estimate becomes a tracked service record.
- Preserve `shop_id` across customer, vehicle, estimate, and service records to enforce tenant isolation.

## 4. Service History Connection

Table: `service_history`

Purpose: tracks estimate and repair lifecycle events for a customer vehicle.

Key fields:

- `customer_id`: required link to `customers.id`.
- `vehicle_id`: required link to `customer_vehicles.id`.
- `shop_id`: nullable tenant boundary for future Pro shops.
- `service_title`, `service_notes`: human-readable service summary and detail.
- `mileage_at_service`, `service_date`: service context for historical lookup.
- `estimate_total`: nullable amount captured from a future saved estimate.
- `status`: lifecycle state, limited to `estimate`, `approved`, `completed`, or `declined`.
- `created_at`, `updated_at`: audit timestamps for service record management.

Workflow role: service history is the bridge between an estimate and the vehicle's long-term maintenance record.

## 5. Maintenance Reminder Connection

Table: `maintenance_reminders`

Purpose: stores date-based and mileage-based future service reminders.

Key fields:

- `customer_id`: required link to `customers.id`.
- `vehicle_id`: required link to `customer_vehicles.id`.
- `shop_id`: nullable tenant boundary.
- `service_type`: reminder label, such as oil change, brakes, or inspection.
- `due_date`: optional calendar trigger.
- `due_mileage`: optional mileage trigger.
- `reminder_status`: limited to `pending`, `notified`, `completed`, or `dismissed`.
- `last_notified_at`: nullable timestamp for future communication tracking.
- `notes`: internal reminder notes.
- `created_at`, `updated_at`: audit timestamps.

Workflow role: reminders can be generated from completed service history, manually created by a future CRM workflow, or derived from mileage/date intervals.

## 6. Future Discrepancy Tracking

Future discrepancy tracking should live on or near `service_history` because discrepancies are usually tied to a specific estimate or service event.

Current dormant support:

- `service_history.discrepancy_notes`

Recommended future expansion, only when needed:

- Add a separate `service_discrepancies` table if multiple discrepancy records per service are required.
- Track original estimate amount, revised amount, reason, staff note, customer-facing note, and resolution status.
- Keep discrepancy records tied to `shop_id`, `customer_id`, and `vehicle_id` either directly or through `service_history`.

## 7. Future Customer Approval Records

Future approval tracking should begin with the dormant fields on `service_history`.

Current dormant support:

- `service_history.customer_authorized_at`
- `service_history.customer_authorized_by`
- `service_history.authorization_notes`

Recommended future expansion, only when needed:

- Add a dedicated `customer_approvals` table if approvals need signatures, IP/user-agent audit data, multi-step approvals, or multiple approval events per service.
- Store approval records against `service_history.id`.
- Keep approval records tenant-aware through `shop_id`.
- Do not connect approval records to public routes until authentication, shop context, and authorization rules exist.

## 8. Hidden During Beta

The CRM foundation stays hidden during Beta by following these constraints:

- Do not register `/pro` or CRM routers.
- Keep `/shop-profile` and preview surfaces inaccessible.
- Do not add CRM links to navigation, homepage, estimator, OBD, repair guides, PDFs, footer, or Parts Center.
- Do not add login, accounts, billing, subscriptions, dashboards, or upgrade prompts.
- Allow only schema initialization and internal documentation until a future gated Pro release.

## Relationship Summary

```text
customers
  -> customer_vehicles
       -> service_history
       -> maintenance_reminders

service_history
  -> future discrepancy tracking
  -> future customer approval records
```

This keeps the future Pro CRM path tenant-ready through `shop_id` while preserving the current Beta experience.
