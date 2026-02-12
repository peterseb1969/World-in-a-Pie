# Use Case: Network Hardware Inventory (v2 with Document References)

> **Note:** This document describes an updated, superior data model for the Network Hardware Inventory use case, leveraging WIP's `reference` feature. The code and templates in this directory (`terminologies.py`, `templates.py`, `demo_data.py`) are now considered **obsolete** as they implement an older, less robust model based on unvalidated string matching.

## Overview

A comprehensive network hardware inventory system to track all physical network infrastructure, including devices, ports, patch panels, cables, VLANs, and IP assignments. This model prioritizes **data integrity** and **navigability** by using explicit document references.

### The Power of References

The original model for this use case relied heavily on repeating natural keys (like MAC addresses or location codes) as strings to link related documents. While intuitive for human users, this approach creates significant risks:

1.  **No Referential Integrity:** A typo in a MAC address or location code could create orphaned documents, leading to an inconsistent and unreliable inventory.
2.  **No Type Safety:** The system couldn't guarantee that a `location_code` referred to a `LOCATION` document, or that a `switch_mac` referred to a `SWITCH`.
3.  **Complex Queries:** Joining on string fields in PostgreSQL is less efficient and more error-prone than joining on resolved document IDs.

By using WIP's `reference` field type, this revised model establishes **validated, type-safe, and directly navigable links** between all related entities, making it impossible to create an inventory with broken connections.

## Business Requirements (Unchanged)

### What to Track

1.  **Network Devices**: Switches, Routers, Access Points, Firewalls, Modems/ONTs, IoT devices, IP cameras, etc.
2.  **Physical Infrastructure**: Patch panels, Cables (copper, fiber), Racks, Power Distribution Units (PDUs).
3.  **Connectivity**: Port configurations, Cable runs between devices, VLAN assignments, IP address allocations.
4.  **Operational Data**: Firmware versions, Warranty status, Purchase information, Maintenance history.

### Key Questions to Answer (Now with Greater Reliability)

-   "What is *actually* connected to switch port Gi1/0/24?" (Guaranteed valid reference)
-   "Where does the cable from patch panel A, port 12 *really* go?" (Guaranteed valid path)
-   "Which devices are running outdated firmware?"
-   "What is the network path from device X to device Y?"
-   "Which devices are out of warranty?"

---

## Data Model v2: Graph-Oriented Inventory

This model defines a robust graph of network entities, where relationships are enforced by `reference` fields.

### Terminologies (Unchanged)

These provide controlled vocabularies for various attributes.

| Terminology | Purpose | Example Terms |
| :---------- | :------ | :------------ |
| `DEVICE_TYPE` | Classification of network devices | switch, router, access_point, firewall, server, workstation, ip_camera, iot_sensor |
| `MANUFACTURER` | Device manufacturers | cisco, ubiquiti, netgear, aruba, mikrotik, tp_link, juniper |
| `PORT_SPEED` | Physical port speeds | 100m, 1g, 2_5g, 5g, 10g, 25g, 40g, 100g |
| `POE_TYPE` | Power over Ethernet standards | none, af_15w, at_30w, bt_type3_60w, bt_type4_90w, passive_24v, passive_48v |
| `CABLE_TYPE` | Cable categories | cat5e, cat6, cat6a, cat7, fiber_om3, fiber_om4, fiber_os2, dac |
| `CABLE_COLOR` | Cable jacket colors | blue, green, yellow, red, orange, white, black, gray, purple |
| `PORT_STATUS` | Operational status | up, down, admin_down, err_disabled |
| `DEVICE_STATUS` | Device lifecycle status | active, spare, maintenance, decommissioned |
| `LOCATION_TYPE` | Physical location types | building, floor, room, rack, shelf |
| `VLAN_PURPOSE` | VLAN usage categories | management, user_data, voip, guest, iot, security_cameras, storage |
| `SFP_TYPE` | SFP module types | 1g_sx, 1g_lx, 10g_sr, 10g_lr, 25g_sr, bidi, cwdm, dwdm |

### Redesigned Templates

#### 1. LOCATION
Represents physical locations in a hierarchy. `parent_location` is now a self-referencing `reference` field.

```yaml
code: LOCATION
name: Physical Location
identity_fields: [location_code]
fields:
  - name: location_code
    type: string
    required: true
    description: "Unique location code (e.g., 'BLDG-A-FLR-1-RM-101-RACK-A1')"

  - name: location_type
    type: term
    terminology_ref: LOCATION_TYPE
    required: true

  - name: name
    type: string
    required: true
    description: "Human-readable name"

  - name: parent_location
    type: reference # Reference to another LOCATION document
    reference_type: document
    target_templates: [LOCATION]
    description: "Link to the parent LOCATION document for hierarchy (e.g., a room linking to a floor)"

  - name: address
    type: string
    description: "Physical address (for buildings)"

  - name: coordinates
    type: object
    description: "GPS or floor plan coordinates"
    fields:
      - name: x
        type: number
      - name: y
        type: number
      - name: floor
        type: integer

  - name: notes
    type: string
```

#### 2. NETWORK_DEVICE
Core template for all network devices. Its physical `location` is now a validated reference.

```yaml
code: NETWORK_DEVICE
name: Network Device
identity_fields: [mac_address]
fields:
  - name: hostname
    type: string
    required: true
    description: "Device hostname"

  - name: mac_address
    type: string
    required: true
    pattern: "^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
    description: "Primary MAC address (IEEE 802)"

  - name: device_type
    type: term
    terminology_ref: DEVICE_TYPE
    required: true

  - name: manufacturer
    type: term
    terminology_ref: MANUFACTURER
    required: true

  - name: model
    type: string
    required: true

  - name: serial_number
    type: string

  - name: firmware_version
    type: string

  - name: management_ip
    type: string
    pattern: "^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"

  - name: location
    type: reference # Reference to a LOCATION document
    reference_type: document
    target_templates: [LOCATION]
    description: "Physical location of the device"

  - name: rack_position
    type: object
    fields:
      - name: start_u
        type: integer
        description: "Starting rack unit (1 = bottom)"
      - name: height_u
        type: integer
        description: "Height in rack units"

  - name: status
    type: term
    terminology_ref: DEVICE_STATUS
    required: true

  - name: purchase_date
    type: date

  - name: warranty_expiry
    type: date

  - name: purchase_price
    type: number

  - name: power_consumption_watts
    type: number

  - name: notes
    type: string
```

#### 3. SWITCH
Extended device information for switches (inherits `NETWORK_DEVICE`). No additional `reference` fields specific to `SWITCH` are strictly needed here, as it inherits the `location` from `NETWORK_DEVICE`.

```yaml
code: SWITCH
name: Network Switch
extends: NETWORK_DEVICE
identity_fields: [mac_address]
fields:
  - name: is_managed
    type: boolean
    required: true

  - name: layer
    type: integer
    description: "Layer 2 or Layer 3"
    validation:
      min: 2
      max: 3

  - name: total_ports
    type: integer
    required: true

  - name: poe_budget_watts
    type: number
    description: "Total PoE power budget"

  - name: poe_consumed_watts
    type: number
    description: "Current PoE consumption"

  - name: supports_stacking
    type: boolean

  - name: stack_id
    type: string
    description: "Stack identifier if part of a stack"

  - name: stack_role
    type: string
    description: "master, member, standby"

  - name: uplink_ports
    type: array
    items:
      type: string # These could be references to SWITCH_PORT if needed, but string is simpler for now
    description: "List of uplink port identifiers (e.g., 'Gi1/0/24')"
```

#### 4. SWITCH_PORT
Individual port on a switch. Crucially, it now references its `parent_switch` directly.

```yaml
code: SWITCH_PORT
name: Switch Port
identity_fields: [parent_switch, port_id]
fields:
  - name: parent_switch
    type: reference # Reference to a SWITCH document
    reference_type: document
    target_templates: [SWITCH]
    mandatory: true
    description: "Link to the parent SWITCH document"

  - name: port_id
    type: string
    required: true
    description: "Port identifier (e.g., 'Gi1/0/24', 'eth0')"

  - name: port_label
    type: string
    description: "Physical label on the port"

  - name: speed_capability
    type: term
    terminology_ref: PORT_SPEED
    required: true

  - name: current_speed
    type: term
    terminology_ref: PORT_SPEED

  - name: poe_capability
    type: term
    terminology_ref: POE_TYPE
    required: true

  - name: poe_power_allocated_watts
    type: number

  - name: poe_power_drawn_watts
    type: number

  - name: sfp_type
    type: term
    terminology_ref: SFP_TYPE
    description: "If SFP port, what module is installed"

  - name: admin_status
    type: term
    terminology_ref: PORT_STATUS
    required: true

  - name: link_status
    type: term
    terminology_ref: PORT_STATUS

  - name: vlan_mode
    type: string
    description: "access or trunk"

  - name: access_vlan
    type: integer
    description: "VLAN ID for access ports"

  - name: trunk_vlans
    type: array
    items:
      type: integer
    description: "Allowed VLANs for trunk ports"

  - name: native_vlan
    type: integer
    description: "Native VLAN for trunk ports"

  - name: connected_mac
    type: string
    description: "MAC address of directly connected device (if not another port)"

  - name: connected_hostname
    type: string
    description: "Hostname of directly connected device (if known)"

  - name: last_state_change
    type: datetime

  - name: description
    type: string
    description: "Port description/purpose"
```

#### 5. PATCH_PANEL
References its `location`.

```yaml
code: PATCH_PANEL
name: Patch Panel
identity_fields: [panel_id]
fields:
  - name: panel_id
    type: string
    required: true
    description: "Unique panel identifier"

  - name: name
    type: string
    required: true

  - name: location
    type: reference # Reference to a LOCATION document
    reference_type: document
    target_templates: [LOCATION]
    mandatory: true

  - name: rack_position
    type: object
    fields:
      - name: start_u
        type: integer
      - name: height_u
        type: integer

  - name: total_ports
    type: integer
    required: true

  - name: port_type
    type: term
    terminology_ref: CABLE_TYPE
    description: "Default cable type for this panel"

  - name: notes
    type: string
```

#### 6. PATCH_PORT
Individual port on a patch panel. Its connections (`front_connection`, `back_connection`) are the perfect use case for polymorphic `reference` fields.

```yaml
code: PATCH_PORT
name: Patch Panel Port
identity_fields: [parent_panel, port_number]
fields:
  - name: parent_panel
    type: reference # Reference to a PATCH_PANEL document
    reference_type: document
    target_templates: [PATCH_PANEL]
    mandatory: true

  - name: port_number
    type: integer
    required: true

  - name: port_label
    type: string
    description: "Physical label"

  - name: front_connection # Polymorphic reference
    type: reference
    reference_type: document
    # A front connection can be to a device (NETWORK_DEVICE) or a wallplate (another custom template)
    target_templates: [NETWORK_DEVICE, WORKSTATION, IP_CAMERA, WALLPLATE] # Example: add other device types
    description: "What connects to the front (room side) of this patch port"

  - name: back_connection # Polymorphic reference
    type: reference
    reference_type: document
    # A back connection is typically to a SWITCH_PORT or another PATCH_PORT
    target_templates: [SWITCH_PORT, PATCH_PORT]
    description: "What connects to the back (switch/core side) of this patch port"

  - name: cable_type
    type: term
    terminology_ref: CABLE_TYPE

  - name: tested_date
    type: date

  - name: test_result
    type: string
    description: "pass, fail, not_tested"
```

#### 7. CABLE_RUN
Physical cable between two endpoints. This template is dramatically simplified and made robust by using `reference` fields for its endpoints.

```yaml
code: CABLE_RUN
name: Cable Run
identity_fields: [cable_id]
fields:
  - name: cable_id
    type: string
    required: true
    description: "Unique cable identifier/label"

  - name: cable_type
    type: term
    terminology_ref: CABLE_TYPE
    required: true

  - name: cable_color
    type: term
    terminology_ref: CABLE_COLOR

  - name: length_meters
    type: number

  - name: endpoint_a
    type: reference # Polymorphic reference for endpoint A
    reference_type: document
    target_templates: [SWITCH_PORT, PATCH_PORT, NETWORK_DEVICE] # Potential endpoints
    required: true
    description: "The first endpoint of the cable run"

  - name: endpoint_b
    type: reference # Polymorphic reference for endpoint B
    reference_type: document
    target_templates: [SWITCH_PORT, PATCH_PORT, NETWORK_DEVICE] # Potential endpoints
    required: true
    description: "The second endpoint of the cable run"

  - name: install_date
    type: date

  - name: installed_by
    type: string

  - name: tested_date
    type: date

  - name: test_result
    type: string
    description: "pass, fail, not_tested"
```

#### 8. VLAN
VLAN definitions. No `reference` fields are strictly needed here, as VLANs are referenced by `vlan_id` (integer) in other documents.

```yaml
code: VLAN
name: VLAN
identity_fields: [vlan_id]
fields:
  - name: vlan_id
    type: integer
    required: true
    validation:
      min: 1
      max: 4094

  - name: name
    type: string
    required: true

  - name: purpose
    type: term
    terminology_ref: VLAN_PURPOSE

  - name: subnet
    type: string
    description: "CIDR notation (e.g., 192.168.10.0/24)"

  - name: gateway
    type: string

  - name: dhcp_enabled
    type: boolean

  - name: dhcp_range_start
    type: string

  - name: dhcp_range_end
    type: string

  - name: description
    type: string
```

#### 9. IP_ASSIGNMENT
IP address assignments. Now references a `VLAN` and the `NETWORK_DEVICE` it is assigned to.

```yaml
code: IP_ASSIGNMENT
name: IP Address Assignment
identity_fields: [ip_address]
fields:
  - name: ip_address
    type: string
    required: true
    pattern: "^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"

  - name: assigned_to_device
    type: reference # Reference to a NETWORK_DEVICE or subclass
    reference_type: document
    target_templates: [NETWORK_DEVICE] # Or more specific [SWITCH, ROUTER, ACCESS_POINT]
    description: "Link to the NETWORK_DEVICE document this IP is assigned to"

  - name: mac_address
    type: string # Keeping for legacy/discovery, but 'assigned_to_device' is canonical
    description: "Associated MAC for DHCP reservation (may be derived from assigned_to_device)"

  - name: hostname
    type: string

  - name: vlan
    type: reference # Reference to a VLAN document
    reference_type: document
    target_templates: [VLAN]
    description: "Link to the VLAN document this IP is part of"

  - name: assignment_type
    type: string
    description: "static, dhcp_reservation, dhcp_dynamic"

  - name: dns_name
    type: string
    description: "FQDN if registered in DNS"

  - name: last_seen
    type: datetime

  - name: notes
    type: string
```

---

## Mapping to WIP Primitives (v2)

| Network Concept | WIP Primitive | v2 Notes |
| :-------------- | :------------ | :------- |
| Location Hierarchy | Documents (`LOCATION`) | `parent_location` is a `reference` to `LOCATION` for robust hierarchy. |
| Network Devices | Documents (`NETWORK_DEVICE`, `SWITCH`) | `NETWORK_DEVICE` references `LOCATION`. `SWITCH` extends `NETWORK_DEVICE`. |
| Device Ports | Documents (`SWITCH_PORT`, `PATCH_PORT`) | `SWITCH_PORT` references `SWITCH`. `PATCH_PORT` references `PATCH_PANEL` and uses polymorphic `reference` fields for its connections. |
| Cables & Connections | Documents (`CABLE_RUN`) | `endpoint_a` and `endpoint_b` are **polymorphic `reference` fields** targeting various port/device templates. This guarantees link integrity. |
| VLANs | Documents (`VLAN`) | `VLAN` documents define VLANs. `IP_ASSIGNMENT` references `VLAN`. |
| IP Assignments | Documents (`IP_ASSIGNMENT`) | `assigned_to_device` is a `reference` to `NETWORK_DEVICE`. `vlan` is a `reference` to `VLAN`. |
| Attributes | `term` fields, `string`, `number`, `boolean` | For validated attributes (e.g., `DEVICE_TYPE`, `PORT_SPEED`, `firmware_version`). |
| Change history | Document versioning | Automatically tracks all changes. |
| Identity | `identity_fields` | Remains the same for primary identification. |

---

## Example Queries (PostgreSQL Reporting with References)

With `reference` fields, the reporting layer automatically generates columns like `location_document_id`, `parent_switch_document_id`, `endpoint_a_document_id`, etc., which are PostgreSQL `TEXT` fields containing the canonical WIP Document IDs. This allows for simple and robust SQL joins.

### Devices by Location
```sql
SELECT
  nd.hostname,
  loc.name as location_name,
  loc.location_code
FROM doc_network_device AS nd
JOIN doc_location AS loc ON nd.location_document_id = loc.document_id
WHERE loc.location_code LIKE 'BLDG-A-%'
ORDER BY loc.location_code, nd.hostname;
```

### Ports per Switch (Using Direct Reference Join)
```sql
SELECT
  s.hostname AS switch_hostname,
  s.mac_address AS switch_mac,
  COUNT(sp.document_id) AS total_ports
FROM doc_switch AS s
JOIN doc_switch_port AS sp ON sp.parent_switch_document_id = s.document_id
GROUP BY s.hostname, s.mac_address
ORDER BY total_ports DESC;
```

### Cable Trace (Find Path - Now with Validated References)
```sql
-- Find what's connected to a specific switch port
-- (Assuming '019c523f-7085-77d0-a323-e77f20f243cd' is the document_id of a SWITCH_PORT)
WITH target_port AS (
  SELECT document_id FROM doc_switch_port WHERE document_id = '019c523f-7085-77d0-a323-e77f20f243cd'
),
connected_cables AS (
  SELECT
    cr.cable_id,
    cr.cable_type,
    cr.endpoint_a_document_id AS endpoint_a_id,
    cr.endpoint_b_document_id AS endpoint_b_id
  FROM doc_cable_run AS cr, target_port AS tp
  WHERE cr.endpoint_a_document_id = tp.document_id OR cr.endpoint_b_document_id = tp.document_id
)
SELECT * FROM connected_cables;
```
**Benefit:** Queries become simpler and more reliable, as they join on guaranteed valid `document_id`s rather than potentially inconsistent string values.

---

## UI Views (WIP Console Extensions - Enhanced by References)

All UI views benefit from the explicit, navigable references. For example, clicking on a device's location in the UI can directly open the `LOCATION` document, or a cable endpoint can jump to the connected port/device.

### 1. Network Dashboard
- Total devices by type (pie chart)
- Devices by status (active/spare/maintenance)
- PoE utilization across all switches
- Warranty expiration timeline
- Recent changes
- **New:** Alerts for broken references (orphaned devices, disconnected ports).

### 2. Device Detail View
- All device attributes
- **New:** List of directly referenced child entities (e.g., "Ports on this Switch", "IPs assigned to this Device").
- Location in rack (visual) - Now guaranteed to be valid.
- Firmware history
- Maintenance log

### 3. Port Map View
- Visual grid of switch ports
- Color-coded by status (up/down/disabled)
- PoE indicator
- Click to see connected device - Now a **guaranteed jump** to the referenced `NETWORK_DEVICE`, `PATCH_PORT`, or `SWITCH_PORT`.
- Hover for quick info

### 4. Cable Trace Tool
- Select start point (device:port)
- Show path through patch panels
- Highlight on network diagram - **Guaranteed to follow valid links.**
- List all intermediate connections

### 5. Topology View
- Auto-generated from `CABLE_RUN` documents, now based on **explicit graph edges**.
- Show device interconnections accurately.
- Layer by VLAN or physical location.
- Export to diagramming tools.

---

## Automation Opportunities (Now More Robust)

With guaranteed referential integrity, automated discovery and management tools can operate with higher confidence.

### 1. Discovery Integration
- SNMP polling to auto-populate device info.
- LLDP/CDP neighbor discovery for connections.
- ARP table import for MAC→IP mappings.
- Scheduled scans to update link status.
- **New:** Automated reconciliation can now reliably identify discrepancies (e.g., a discovered connection doesn't match an existing validated `CABLE_RUN` document).

### 2. Change Detection
- Compare current state vs WIP records.
- Alert on unexpected changes.
- Automatic document updates via API.
- **New:** Alerts for *actual* broken physical connections, not just typos in the inventory.

### 3. Validation Rules
- Warn if PoE allocation exceeds budget.
- Flag duplicate IP assignments.
- Detect orphaned patch panel ports.
- Verify VLAN consistency across trunks.
- **New:** Complex validation rules spanning multiple documents become more feasible and reliable due to the explicit graph structure.

### 4. Reporting
- Capacity planning (port utilization trends).
- Warranty renewal reminders.
- Firmware compliance reports.
- Power consumption tracking.

---

## Demo Data Specification (Needs Update)

The demo data generation scripts will need to be updated to reflect the new template structures that use `reference` fields. This will involve:
1.  Creating `LOCATION` documents first.
2.  Creating `NETWORK_DEVICE` and `PATCH_PANEL` documents, referencing `LOCATION` IDs.
3.  Creating `SWITCH_PORT` and `PATCH_PORT` documents, referencing their parent device/panel IDs.
4.  Creating `CABLE_RUN` documents, referencing the appropriate `SWITCH_PORT`, `PATCH_PORT`, or `NETWORK_DEVICE` IDs.
5.  Creating `VLAN` documents.
6.  Creating `IP_ASSIGNMENT` documents, referencing `NETWORK_DEVICE` and `VLAN` IDs.

---

## File Structure (Code Obsolete)

```
scripts/
└── seed_use_cases/
    └── network_inventory/
        ├── __init__.py
        ├── terminologies.py    # (Obsolete - needs update for new model)
        ├── templates.py        # (Obsolete - needs update for new model)
        └── demo_data.py        # (Obsolete - needs update for new model)
```
**The Python code files within this directory (`terminologies.py`, `templates.py`, `demo_data.py`, `seed.py`) are now considered obsolete. They implement the older, string-based linking model. To implement the improved data model described in this document, these files would need to be rewritten.**

---

## Next Steps

1.  **Rewrite Template Definitions:** Update `templates.py` to use `reference` fields as described.
2.  **Rewrite Demo Data Generator:** Update `demo_data.py` and `seed.py` to generate data that conforms to the new `reference`-based templates.
3.  **Update Reporting Queries:** Revise `Example Queries` to reflect the PostgreSQL join paths on resolved document IDs.
4.  **Implement UI Enhancements:** Update WIP Console to leverage the navigable references.
