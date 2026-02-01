# Use Case: Network Hardware Inventory

## Overview

A comprehensive network hardware inventory system to track all physical network infrastructure including switches, routers, access points, patch panels, cables, and their interconnections.

## Business Requirements

### What to Track

1. **Network Devices**
   - Switches (managed/unmanaged, L2/L3)
   - Routers
   - Access Points (WiFi)
   - Firewalls
   - Modems/ONTs
   - IoT devices, IP cameras, etc.

2. **Physical Infrastructure**
   - Patch panels
   - Cables (copper, fiber)
   - Racks and enclosures
   - Power distribution units (PDUs)

3. **Connectivity**
   - Port configurations
   - Cable runs between devices
   - VLAN assignments
   - IP address allocations

4. **Operational Data**
   - Firmware versions
   - Warranty status
   - Purchase information
   - Maintenance history

### Key Questions to Answer

- "What is connected to switch port Gi1/0/24?"
- "Which ports support PoE+ and how much power budget remains?"
- "Where does the cable from patch panel A, port 12 go?"
- "Which devices are running outdated firmware?"
- "What is the network path from device X to device Y?"
- "Which devices are out of warranty?"

---

## Data Model

### Terminologies

| Terminology | Purpose | Example Terms |
|-------------|---------|---------------|
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

### Templates

#### 1. LOCATION
Physical locations in a hierarchy (building → floor → room → rack).

```yaml
code: LOCATION
name: Physical Location
identity_fields: [location_code]
fields:
  - name: location_code
    type: string
    required: true
    description: "Unique location identifier (e.g., 'RACK-A1')"

  - name: location_type
    type: term
    terminology_ref: LOCATION_TYPE
    required: true

  - name: name
    type: string
    required: true
    description: "Human-readable name"

  - name: parent_location
    type: string
    description: "Parent location code for hierarchy"

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
Core template for all network devices.

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
    description: "Primary MAC address"

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
    pattern: "^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"

  - name: location_code
    type: string
    description: "Reference to LOCATION"

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
Extended device information for switches (inherits NETWORK_DEVICE).

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
      type: string
    description: "List of uplink port identifiers"
```

#### 4. SWITCH_PORT
Individual port on a switch.

```yaml
code: SWITCH_PORT
name: Switch Port
identity_fields: [switch_mac, port_id]
fields:
  - name: switch_mac
    type: string
    required: true
    description: "MAC address of parent switch"

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
    description: "MAC address of connected device"

  - name: connected_hostname
    type: string
    description: "Hostname of connected device (if known)"

  - name: last_state_change
    type: datetime

  - name: description
    type: string
    description: "Port description/purpose"
```

#### 5. PATCH_PANEL
Patch panel for cable management.

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

  - name: location_code
    type: string
    required: true

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
Individual port on a patch panel.

```yaml
code: PATCH_PORT
name: Patch Panel Port
identity_fields: [panel_id, port_number]
fields:
  - name: panel_id
    type: string
    required: true

  - name: port_number
    type: integer
    required: true

  - name: port_label
    type: string
    description: "Physical label"

  - name: front_connection
    type: object
    description: "What connects to front (room side)"
    fields:
      - name: type
        type: string
        description: "device, wallplate, patch_panel"
      - name: reference
        type: string
        description: "Device MAC, wallplate ID, or panel:port"

  - name: back_connection
    type: object
    description: "What connects to back (switch side)"
    fields:
      - name: type
        type: string
      - name: reference
        type: string

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
Physical cable between two endpoints.

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
    type: object
    required: true
    fields:
      - name: type
        type: string
        description: "switch_port, patch_port, device, wallplate"
      - name: device_mac
        type: string
      - name: port_id
        type: string
      - name: panel_id
        type: string
      - name: port_number
        type: integer

  - name: endpoint_b
    type: object
    required: true
    fields:
      - name: type
        type: string
      - name: device_mac
        type: string
      - name: port_id
        type: string
      - name: panel_id
        type: string
      - name: port_number
        type: integer

  - name: install_date
    type: date

  - name: installed_by
    type: string

  - name: tested_date
    type: date

  - name: test_result
    type: string

  - name: notes
    type: string
```

#### 8. VLAN
VLAN definitions.

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
IP address assignments (DHCP reservations or static).

```yaml
code: IP_ASSIGNMENT
name: IP Address Assignment
identity_fields: [ip_address]
fields:
  - name: ip_address
    type: string
    required: true
    pattern: "^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"

  - name: mac_address
    type: string
    description: "Associated MAC for DHCP reservation"

  - name: hostname
    type: string

  - name: vlan_id
    type: integer

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

## Mapping to WIP Primitives

| Network Concept | WIP Primitive | Notes |
|-----------------|---------------|-------|
| Device categories | Terminology (DEVICE_TYPE) | switch, router, AP, etc. |
| Port speeds | Terminology (PORT_SPEED) | Standardized values |
| PoE standards | Terminology (POE_TYPE) | Includes power levels |
| Device records | Documents (NETWORK_DEVICE) | One doc per device |
| Port configs | Documents (SWITCH_PORT) | One doc per port |
| Cables | Documents (CABLE_RUN) | Links two endpoints |
| VLANs | Documents (VLAN) | Network segmentation |
| Device relationships | term_references | Links to manufacturer, type |
| Change history | Document versioning | Track firmware updates, moves |
| Identity | identity_fields | MAC for devices, composite for ports |

---

## Example Queries (via PostgreSQL Reporting)

### Devices by Type
```sql
SELECT device_type, COUNT(*) as count
FROM doc_network_device
WHERE status = 'active'
GROUP BY device_type;
```

### PoE Budget Utilization
```sql
SELECT
  d.hostname,
  d.poe_budget_watts,
  d.poe_consumed_watts,
  ROUND(d.poe_consumed_watts / d.poe_budget_watts * 100, 1) as utilization_pct
FROM doc_switch d
WHERE d.poe_budget_watts > 0
ORDER BY utilization_pct DESC;
```

### Devices with Expiring Warranty
```sql
SELECT hostname, manufacturer, model, warranty_expiry
FROM doc_network_device
WHERE warranty_expiry BETWEEN NOW() AND NOW() + INTERVAL '90 days'
ORDER BY warranty_expiry;
```

### Port Utilization per Switch
```sql
SELECT
  d.hostname,
  COUNT(p.port_id) as total_ports,
  COUNT(CASE WHEN p.link_status = 'up' THEN 1 END) as ports_in_use,
  ROUND(COUNT(CASE WHEN p.link_status = 'up' THEN 1 END)::numeric / COUNT(p.port_id) * 100, 1) as utilization_pct
FROM doc_network_device d
JOIN doc_switch_port p ON d.mac_address = p.switch_mac
WHERE d.device_type = 'switch'
GROUP BY d.hostname
ORDER BY utilization_pct DESC;
```

### Cable Trace (Find Path)
```sql
-- Find what's connected to a specific switch port
WITH port_info AS (
  SELECT * FROM doc_switch_port
  WHERE switch_mac = '00:11:22:33:44:55' AND port_id = 'Gi1/0/24'
)
SELECT
  c.cable_id,
  c.cable_type,
  c.endpoint_a,
  c.endpoint_b
FROM doc_cable_run c, port_info p
WHERE c.endpoint_a->>'device_mac' = p.switch_mac
  AND c.endpoint_a->>'port_id' = p.port_id
   OR c.endpoint_b->>'device_mac' = p.switch_mac
  AND c.endpoint_b->>'port_id' = p.port_id;
```

---

## UI Views (WIP Console Extensions)

### 1. Network Dashboard
- Total devices by type (pie chart)
- Devices by status (active/spare/maintenance)
- PoE utilization across all switches
- Warranty expiration timeline
- Recent changes

### 2. Device Detail View
- All device attributes
- List of ports (for switches)
- Connected devices
- Location in rack (visual)
- Firmware history
- Maintenance log

### 3. Port Map View
- Visual grid of switch ports
- Color-coded by status (up/down/disabled)
- PoE indicator
- Click to see connected device
- Hover for quick info

### 4. Cable Trace Tool
- Select start point (device:port)
- Show path through patch panels
- Highlight on network diagram
- List all intermediate connections

### 5. Topology View
- Auto-generated from CABLE_RUN documents
- Show device interconnections
- Layer by VLAN or physical location
- Export to diagramming tools

---

## Automation Opportunities

### 1. Discovery Integration
- SNMP polling to auto-populate device info
- LLDP/CDP neighbor discovery for connections
- ARP table import for MAC→IP mappings
- Scheduled scans to update link status

### 2. Change Detection
- Compare current state vs WIP records
- Alert on unexpected changes
- Automatic document updates via API

### 3. Validation Rules
- Warn if PoE allocation exceeds budget
- Flag duplicate IP assignments
- Detect orphaned patch panel ports
- Verify VLAN consistency across trunks

### 4. Reporting
- Capacity planning (port utilization trends)
- Warranty renewal reminders
- Firmware compliance reports
- Power consumption tracking

---

## Demo Data Specification

For the demo implementation, create:

1. **Locations**
   - 1 building ("Home Office")
   - 2 rooms ("Server Closet", "Office")
   - 1 rack ("Rack-A")

2. **Devices**
   - 2 switches (1 managed PoE, 1 unmanaged)
   - 1 router
   - 1 access point
   - 5 end devices (workstation, NAS, IP camera, etc.)

3. **Patch Panel**
   - 1 x 24-port panel

4. **Cables**
   - 10-15 cable runs connecting everything

5. **VLANs**
   - Management (VLAN 1)
   - User Data (VLAN 10)
   - IoT (VLAN 20)
   - Guest (VLAN 99)

6. **IP Assignments**
   - Static IPs for infrastructure
   - DHCP reservations for known devices

This provides enough data to demonstrate all queries and relationships.

---

## File Structure

```
scripts/
└── seed_use_cases/
    └── network_inventory/
        ├── __init__.py
        ├── terminologies.py    # DEVICE_TYPE, PORT_SPEED, etc.
        ├── templates.py        # All template definitions
        └── demo_data.py        # Sample network inventory
```

---

## Next Steps

1. Review and refine the data model
2. Create the terminology definitions
3. Create the template definitions
4. Implement demo data generator
5. Create seed script
6. Test queries in PostgreSQL
7. Consider UI enhancements for network views
