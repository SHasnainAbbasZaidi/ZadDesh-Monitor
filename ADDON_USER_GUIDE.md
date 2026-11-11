# ZadDesh add-on guide

## Enable the add-ons

Apply the database migrations with `alembic upgrade head` from `core`, using the environment of your existing ZadDesh instance. Build the web bundle with `npm run build` from `web` and deploy it with the upgraded core. Add the settings you want to the existing ZadDesh YAML and restart the core.

```yaml
features:
  incident_workspace: true
  network_diagram_beta: true
  topology_allowed_ranges:
    - 192.0.2.0/24
  event_retention_days: 0
```

Replace the example range with the specific management networks that the collector may query. An empty allowlist prevents discovery. Set `ZADDESH_COLLECTOR_NETWORK_DIAGRAM_BETA=true` in the collector service environment and restart the upgraded collector. Keep the existing encryption key, collector authentication and MongoDB settings. Use TLS between the collector and core when they communicate off-host.

## Alerts, Events and Logs

Open **Alerts & Events** in the sidebar and select a tab. Each entry starts with a short title. Select a title to see the message and context. Alerts show severity, status, timing and occurrences. Open an alert to see its timeline, available metric samples, related events and logs, and webhook delivery status. Use **UTC** to view timestamps in UTC. Admins can acknowledge, resolve, silence and comment. The alert list also supports bulk actions and saved filters.

A new failure after recovery reopens the same alert. The new episode gets its own start time and the timeline and occurrence count carry forward. Five state transitions within ten minutes mark the incident as flapping. A maintenance window or a configured down parent can suppress an incident. An offline collector is shown separately from an observed target failure.

The Logs tab uses the existing MongoDB log store. It supports live refresh, pause, filters, copy and download. Retention follows `logs.retention_days`. Set `features.event_retention_days` to a positive number to expire older SQL events on subsequent metric ingestion. Zero disables that cleanup. Log masking recognizes common named secret fields and credential formats. Keep unlabeled secrets out of incoming log messages.

To receive incident notifications, set `ZADDESH_FEATURES__INCIDENT_WEBHOOK_URL` to your receiver. The core accepts HTTPS, or HTTP on loopback for testing. It sends an open or resolved event with an `Idempotency-Key` header. Configure the receiver to use that key to deduplicate retries.

## Network Diagram Beta

1. Create or edit an enabled SNMPv3 device check with a stored credential. Use the management IP as both the inventory address and check target. The credential needs read access to system and interface OIDs and LLDP-MIB, or CDP-MIB on supported equipment. Enable LLDP or CDP on the relevant interfaces.
2. As an Admin, open **Network Diagram**, create a diagram, select one or more seed checks and choose **Run discovery now**. The Discovery panel shows an outcome per seed.
3. Select a node or link to inspect it. Choose **Unlock layout** to move nodes and **Save layout** to persist the positions. Use **Changes detected** to record approval or dismissal. The diagram displays observations before review. Manually added nodes and links are retained during discovery.
4. Choose **Publish** to make the diagram available as a read-only widget. A User can open **Overview**, choose **Add diagram widget**, select the published diagram, and set its site or problems-only view, size and labels. Unpublishing removes the graph from that widget.

Inventory health and alert counts update from the core. Link state and utilization update when discovery observes the interfaces. Run discovery again to collect a new sample. Utilization needs two valid counter samples.
