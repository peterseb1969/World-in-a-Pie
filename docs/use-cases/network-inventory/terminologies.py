"""
Network Inventory Use Case - Terminology Definitions

Defines all terminologies needed for network hardware inventory management.
"""

TERMINOLOGIES = [
    {
        "code": "DEVICE_TYPE",
        "name": "Network Device Type",
        "description": "Classification of network devices",
        "terms": [
            {"code": "SWITCH", "value": "switch", "label": "Switch", "sort_order": 1},
            {"code": "ROUTER", "value": "router", "label": "Router", "sort_order": 2},
            {"code": "ACCESS_POINT", "value": "access_point", "label": "Access Point", "sort_order": 3},
            {"code": "FIREWALL", "value": "firewall", "label": "Firewall", "sort_order": 4},
            {"code": "MODEM", "value": "modem", "label": "Modem/ONT", "sort_order": 5},
            {"code": "SERVER", "value": "server", "label": "Server", "sort_order": 6},
            {"code": "WORKSTATION", "value": "workstation", "label": "Workstation", "sort_order": 7},
            {"code": "NAS", "value": "nas", "label": "NAS Storage", "sort_order": 8},
            {"code": "IP_CAMERA", "value": "ip_camera", "label": "IP Camera", "sort_order": 9},
            {"code": "IOT_SENSOR", "value": "iot_sensor", "label": "IoT Sensor", "sort_order": 10},
            {"code": "PRINTER", "value": "printer", "label": "Network Printer", "sort_order": 11},
            {"code": "UPS", "value": "ups", "label": "UPS", "sort_order": 12},
            {"code": "PDU", "value": "pdu", "label": "PDU", "sort_order": 13},
            {"code": "OTHER", "value": "other", "label": "Other", "sort_order": 99},
        ]
    },
    {
        "code": "MANUFACTURER",
        "name": "Device Manufacturer",
        "description": "Network equipment manufacturers",
        "terms": [
            {"code": "CISCO", "value": "cisco", "label": "Cisco", "aliases": ["Cisco Systems"]},
            {"code": "UBIQUITI", "value": "ubiquiti", "label": "Ubiquiti", "aliases": ["UBNT", "UniFi"]},
            {"code": "NETGEAR", "value": "netgear", "label": "Netgear"},
            {"code": "ARUBA", "value": "aruba", "label": "Aruba", "aliases": ["HPE Aruba"]},
            {"code": "MIKROTIK", "value": "mikrotik", "label": "MikroTik"},
            {"code": "TP_LINK", "value": "tp_link", "label": "TP-Link", "aliases": ["TP-Link", "TPLink"]},
            {"code": "JUNIPER", "value": "juniper", "label": "Juniper"},
            {"code": "DELL", "value": "dell", "label": "Dell"},
            {"code": "HP", "value": "hp", "label": "HP", "aliases": ["Hewlett Packard", "HPE"]},
            {"code": "DLINK", "value": "dlink", "label": "D-Link"},
            {"code": "ZYXEL", "value": "zyxel", "label": "Zyxel"},
            {"code": "FORTINET", "value": "fortinet", "label": "Fortinet"},
            {"code": "SYNOLOGY", "value": "synology", "label": "Synology"},
            {"code": "QNAP", "value": "qnap", "label": "QNAP"},
            {"code": "RASPBERRY_PI", "value": "raspberry_pi", "label": "Raspberry Pi"},
            {"code": "APPLE", "value": "apple", "label": "Apple"},
            {"code": "INTEL", "value": "intel", "label": "Intel"},
            {"code": "OTHER", "value": "other", "label": "Other"},
        ]
    },
    {
        "code": "PORT_SPEED",
        "name": "Port Speed",
        "description": "Physical port speed capabilities",
        "terms": [
            {"code": "10M", "value": "10m", "label": "10 Mbps", "sort_order": 1, "metadata": {"mbps": 10}},
            {"code": "100M", "value": "100m", "label": "100 Mbps", "sort_order": 2, "metadata": {"mbps": 100}},
            {"code": "1G", "value": "1g", "label": "1 Gbps", "sort_order": 3, "metadata": {"mbps": 1000}},
            {"code": "2_5G", "value": "2_5g", "label": "2.5 Gbps", "sort_order": 4, "metadata": {"mbps": 2500}},
            {"code": "5G", "value": "5g", "label": "5 Gbps", "sort_order": 5, "metadata": {"mbps": 5000}},
            {"code": "10G", "value": "10g", "label": "10 Gbps", "sort_order": 6, "metadata": {"mbps": 10000}},
            {"code": "25G", "value": "25g", "label": "25 Gbps", "sort_order": 7, "metadata": {"mbps": 25000}},
            {"code": "40G", "value": "40g", "label": "40 Gbps", "sort_order": 8, "metadata": {"mbps": 40000}},
            {"code": "100G", "value": "100g", "label": "100 Gbps", "sort_order": 9, "metadata": {"mbps": 100000}},
        ]
    },
    {
        "code": "POE_TYPE",
        "name": "PoE Standard",
        "description": "Power over Ethernet standards and capabilities",
        "terms": [
            {"code": "NONE", "value": "none", "label": "No PoE", "sort_order": 0, "metadata": {"max_watts": 0}},
            {"code": "AF_15W", "value": "af_15w", "label": "802.3af (15W)", "sort_order": 1, "metadata": {"max_watts": 15.4, "standard": "802.3af"}},
            {"code": "AT_30W", "value": "at_30w", "label": "802.3at PoE+ (30W)", "sort_order": 2, "metadata": {"max_watts": 30, "standard": "802.3at"}},
            {"code": "BT_TYPE3_60W", "value": "bt_type3_60w", "label": "802.3bt Type 3 (60W)", "sort_order": 3, "metadata": {"max_watts": 60, "standard": "802.3bt"}},
            {"code": "BT_TYPE4_90W", "value": "bt_type4_90w", "label": "802.3bt Type 4 (90W)", "sort_order": 4, "metadata": {"max_watts": 90, "standard": "802.3bt"}},
            {"code": "PASSIVE_24V", "value": "passive_24v", "label": "Passive 24V", "sort_order": 10, "metadata": {"voltage": 24}},
            {"code": "PASSIVE_48V", "value": "passive_48v", "label": "Passive 48V", "sort_order": 11, "metadata": {"voltage": 48}},
        ]
    },
    {
        "code": "CABLE_TYPE",
        "name": "Cable Type",
        "description": "Network cable categories and types",
        "terms": [
            {"code": "CAT5E", "value": "cat5e", "label": "Cat5e", "sort_order": 1, "metadata": {"max_speed": "1g", "max_length_m": 100}},
            {"code": "CAT6", "value": "cat6", "label": "Cat6", "sort_order": 2, "metadata": {"max_speed": "10g", "max_length_m": 55}},
            {"code": "CAT6A", "value": "cat6a", "label": "Cat6a", "sort_order": 3, "metadata": {"max_speed": "10g", "max_length_m": 100}},
            {"code": "CAT7", "value": "cat7", "label": "Cat7", "sort_order": 4, "metadata": {"max_speed": "10g", "max_length_m": 100}},
            {"code": "CAT8", "value": "cat8", "label": "Cat8", "sort_order": 5, "metadata": {"max_speed": "40g", "max_length_m": 30}},
            {"code": "FIBER_OM3", "value": "fiber_om3", "label": "Fiber OM3 (Multimode)", "sort_order": 10, "metadata": {"type": "multimode"}},
            {"code": "FIBER_OM4", "value": "fiber_om4", "label": "Fiber OM4 (Multimode)", "sort_order": 11, "metadata": {"type": "multimode"}},
            {"code": "FIBER_OS2", "value": "fiber_os2", "label": "Fiber OS2 (Singlemode)", "sort_order": 12, "metadata": {"type": "singlemode"}},
            {"code": "DAC", "value": "dac", "label": "Direct Attach Copper (DAC)", "sort_order": 20},
            {"code": "AOC", "value": "aoc", "label": "Active Optical Cable (AOC)", "sort_order": 21},
        ]
    },
    {
        "code": "CABLE_COLOR",
        "name": "Cable Color",
        "description": "Cable jacket colors for identification",
        "terms": [
            {"code": "BLUE", "value": "blue", "label": "Blue", "metadata": {"hex": "#0066CC"}},
            {"code": "GREEN", "value": "green", "label": "Green", "metadata": {"hex": "#00AA00"}},
            {"code": "YELLOW", "value": "yellow", "label": "Yellow", "metadata": {"hex": "#FFCC00"}},
            {"code": "RED", "value": "red", "label": "Red", "metadata": {"hex": "#CC0000"}},
            {"code": "ORANGE", "value": "orange", "label": "Orange", "metadata": {"hex": "#FF6600"}},
            {"code": "WHITE", "value": "white", "label": "White", "metadata": {"hex": "#FFFFFF"}},
            {"code": "BLACK", "value": "black", "label": "Black", "metadata": {"hex": "#000000"}},
            {"code": "GRAY", "value": "gray", "label": "Gray", "metadata": {"hex": "#808080"}},
            {"code": "PURPLE", "value": "purple", "label": "Purple", "metadata": {"hex": "#6600CC"}},
            {"code": "PINK", "value": "pink", "label": "Pink", "metadata": {"hex": "#FF66CC"}},
        ]
    },
    {
        "code": "PORT_STATUS",
        "name": "Port Status",
        "description": "Operational status of network ports",
        "terms": [
            {"code": "UP", "value": "up", "label": "Up", "metadata": {"operational": True}},
            {"code": "DOWN", "value": "down", "label": "Down", "metadata": {"operational": False}},
            {"code": "ADMIN_DOWN", "value": "admin_down", "label": "Administratively Down", "metadata": {"operational": False}},
            {"code": "ERR_DISABLED", "value": "err_disabled", "label": "Error Disabled", "metadata": {"operational": False}},
            {"code": "NOT_CONNECTED", "value": "not_connected", "label": "Not Connected", "metadata": {"operational": False}},
        ]
    },
    {
        "code": "DEVICE_STATUS",
        "name": "Device Status",
        "description": "Device lifecycle status",
        "terms": [
            {"code": "ACTIVE", "value": "active", "label": "Active", "sort_order": 1},
            {"code": "SPARE", "value": "spare", "label": "Spare", "sort_order": 2},
            {"code": "MAINTENANCE", "value": "maintenance", "label": "Under Maintenance", "sort_order": 3},
            {"code": "DECOMMISSIONED", "value": "decommissioned", "label": "Decommissioned", "sort_order": 4},
            {"code": "RMA", "value": "rma", "label": "RMA/Warranty Return", "sort_order": 5},
        ]
    },
    {
        "code": "LOCATION_TYPE",
        "name": "Location Type",
        "description": "Types of physical locations",
        "terms": [
            {"code": "BUILDING", "value": "building", "label": "Building", "sort_order": 1},
            {"code": "FLOOR", "value": "floor", "label": "Floor", "sort_order": 2},
            {"code": "ROOM", "value": "room", "label": "Room", "sort_order": 3},
            {"code": "RACK", "value": "rack", "label": "Rack/Cabinet", "sort_order": 4},
            {"code": "SHELF", "value": "shelf", "label": "Shelf", "sort_order": 5},
            {"code": "WALL", "value": "wall", "label": "Wall Mount", "sort_order": 6},
            {"code": "DESK", "value": "desk", "label": "Desk/Table", "sort_order": 7},
        ]
    },
    {
        "code": "VLAN_PURPOSE",
        "name": "VLAN Purpose",
        "description": "Categories for VLAN usage",
        "terms": [
            {"code": "MANAGEMENT", "value": "management", "label": "Management", "sort_order": 1},
            {"code": "USER_DATA", "value": "user_data", "label": "User Data", "sort_order": 2},
            {"code": "VOIP", "value": "voip", "label": "VoIP", "sort_order": 3},
            {"code": "GUEST", "value": "guest", "label": "Guest", "sort_order": 4},
            {"code": "IOT", "value": "iot", "label": "IoT Devices", "sort_order": 5},
            {"code": "SECURITY_CAMERAS", "value": "security_cameras", "label": "Security Cameras", "sort_order": 6},
            {"code": "STORAGE", "value": "storage", "label": "Storage/SAN", "sort_order": 7},
            {"code": "DMZ", "value": "dmz", "label": "DMZ", "sort_order": 8},
            {"code": "LAB", "value": "lab", "label": "Lab/Test", "sort_order": 9},
        ]
    },
    {
        "code": "SFP_TYPE",
        "name": "SFP Module Type",
        "description": "Types of SFP/SFP+/QSFP modules",
        "terms": [
            {"code": "1G_SX", "value": "1g_sx", "label": "1G SX (Multimode)", "metadata": {"speed": "1g", "type": "multimode", "max_distance_m": 550}},
            {"code": "1G_LX", "value": "1g_lx", "label": "1G LX (Singlemode)", "metadata": {"speed": "1g", "type": "singlemode", "max_distance_m": 10000}},
            {"code": "1G_T", "value": "1g_t", "label": "1G-T (Copper RJ45)", "metadata": {"speed": "1g", "type": "copper", "max_distance_m": 100}},
            {"code": "10G_SR", "value": "10g_sr", "label": "10G SR (Multimode)", "metadata": {"speed": "10g", "type": "multimode", "max_distance_m": 300}},
            {"code": "10G_LR", "value": "10g_lr", "label": "10G LR (Singlemode)", "metadata": {"speed": "10g", "type": "singlemode", "max_distance_m": 10000}},
            {"code": "10G_T", "value": "10g_t", "label": "10G-T (Copper RJ45)", "metadata": {"speed": "10g", "type": "copper", "max_distance_m": 30}},
            {"code": "25G_SR", "value": "25g_sr", "label": "25G SR (Multimode)", "metadata": {"speed": "25g", "type": "multimode"}},
            {"code": "25G_LR", "value": "25g_lr", "label": "25G LR (Singlemode)", "metadata": {"speed": "25g", "type": "singlemode"}},
            {"code": "BIDI", "value": "bidi", "label": "BiDi (Single Fiber)", "metadata": {"single_fiber": True}},
        ]
    },
]
