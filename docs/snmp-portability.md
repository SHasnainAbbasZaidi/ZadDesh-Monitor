# SNMP discovery across environments

Device identity uses reported SNMP data, not device IPs, site names, or the current installation's inventory. The standard ENTITY-MIB path accepts manufacturers and model strings that are absent from ZadDesh's built-in catalog. Protocol credentials and network access remain installation-specific.

## Identity selection

- Read system description (`1.3.6.1.2.1.1.1.0`) and system object ID (`1.3.6.1.2.1.1.2.0`).
- Read ENTITY-MIB physical class, manufacturer, and model columns (`1.3.6.1.2.1.47.1.1.1.1.5`, `.12`, `.13`). Select chassis rows before stack rows. Never identify the device using a known fan, port, or line-card model.
- If several chassis disagree on model, retain their common reported manufacturer and use an available system product/family identity instead of arbitrarily selecting a member.
- Use exact product OID mappings, then enterprise mappings and recognizable description patterns where standard hardware inventory is absent. Reported chassis manufacturer/model take priority over catalog values.
- Zabbix inventory uses both item keys and numeric `snmp_oid` fields. A site's custom item names do not prevent standard OID discovery. ENTITY-MIB module items are not treated as chassis models.

Numeric OIDs with or without a leading dot and numeric or labelled physical classes are accepted. Multiline SNMP text remains readable; binary identifiers remain hex. Missing values do not erase existing hardware details, and manual device fields are preserved.

The physical inventory definitions follow [ENTITY-MIB](https://www.rfc-editor.org/rfc/rfc4133). Zabbix discovery uses the documented [item.get](https://www.zabbix.com/documentation/current/en/manual/api/reference/item/get) search/output fields.

## Extending identity without modifying code

For a device that reports a product OID but no physical inventory, add a verified mapping to the installation's `zaddesh.yaml`. Restart the core after changing configuration. Product keys must be exact numeric enterprise OIDs; enterprise keys must be numeric enterprise numbers. Vendor/model values are limited to 64 characters. Invalid mapping keys fail configuration validation.

The following is an **illustrative configuration only**, not an enabled catalog or real device claim. Replace these example identifiers and names with the manufacturer's published values:

```yaml
snmp_identity:
  products:
    "1.3.6.1.4.1.99999.7":
      vendor: "Example Manufacturer"
      model: "Example Model"
  enterprises:
    "99999": "Example Manufacturer"
```

Environment variables are also supported through the existing settings mechanism: `ZADDESH_SNMP_IDENTITY__PRODUCTS` and `ZADDESH_SNMP_IDENTITY__ENTERPRISES` accept JSON mappings. Enterprise mappings identify a manufacturer, not an exact hardware model. Mappings are local configuration data; no executable MIB files or internet lookup are required by identity resolution.

## Deploying elsewhere

Configure each target's supported SNMP version, port, credentials, and SNMPv3 context/authentication/privacy settings. Permit the collector's source address in agent ACLs and firewalls. Register the devices in the new installation; do not copy the current deployment's target IPs or credentials. Configure permitted topology CIDRs for that network where required by the discovery workflow.

Not all agents expose ENTITY-MIB, process lists, services, resource metrics, or neighbor tables. Linux/Windows system descriptions alone do not establish the hardware manufacturer. If the agent restricts or omits hardware OIDs, the UI must state that identity was not reported. A vendor's private resource MIB may require an additional template; generic identity support is not a claim of universal support for every vendor's metrics.

## Verification scope

Automated tests exercise unfamiliar manufacturer/model strings, leading-dot OIDs, labelled physical classes, modules, heterogeneous chassis, custom Zabbix item names, and configuration extensions. These are synthetic fixtures, not certification on physical equipment from every listed vendor. The current live environment remains the only hardware deployment verified in this session. Validate the target agents, SNMP versions, views, and credentials during rollout to another network.
