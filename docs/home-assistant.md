# Home Assistant entities, actions and events

## Entities

There is one service device, **Home Inventory**, with summary entities only. Individual items are never entities.

| Entity | Meaning |
|---|---|
| `sensor.home_inventory_total_items` | Total quantity of all items (attributes: records, products, last activity) |
| `sensor.home_inventory_containers` | Number of containers |
| `sensor.home_inventory_locations` | Number of locations |
| `sensor.home_inventory_recorded_value` | Sum of purchase price × quantity (monetary, in the configured currency) |
| `sensor.home_inventory_items_on_loan` | Units currently lent out |
| `sensor.home_inventory_overdue_loans` | Loans past their expected return date |
| `sensor.home_inventory_temporarily_relocated` | Items and containers away from their normal place |
| `sensor.home_inventory_warranties_expiring` | Warranties ending within `warranty_notice_days` |
| `sensor.home_inventory_items_expiring_soon` | Holdings reaching their use-by date within `expiry_notice_days` |
| `sensor.home_inventory_expired_items` | Holdings past their use-by date |
| `sensor.home_inventory_products_running_low` | Products below their low-stock threshold |
| `sensor.home_inventory_maintenance_overdue` | Maintenance tasks past their date, hours or uses |
| `sensor.home_inventory_maintenance_due_soon` | Maintenance tasks within their "due soon" window |
| `sensor.home_inventory_repairs_in_progress` | Customer repair jobs not yet completed |
| `sensor.home_inventory_repairs_awaiting_pickup` | Completed repairs waiting to be collected |
| `sensor.home_inventory_supplies_below_minimum` | Products below their minimum (restock) |
| `sensor.home_inventory_shopping_list_items` | Open shopping-list entries |
| `binary_sensor.home_inventory_inventory_app` | App availability (attribute `live_events`) |

## Operating hours from sensors

Equipment can read its hour meter (and/or a starts counter) from any Home Assistant entity whose state is a number: open the item → Maintenance → **Edit** → **Read from Home Assistant**. Units h, min, s and d are converted to hours; counters are used as-is. The App reads them every 15 minutes through the Home Assistant API and records a reading only when the value has gone up. Useful sources: a smart plug's runtime/"on time" sensor, or a [`history_stats`](https://www.home-assistant.io/integrations/history_stats/) sensor counting the hours a plug or power sensor was on.

## Actions

| Action | Returns | Example |
|---|---|---|
| `home_inventory.search` | answer, matching items with locations | `query: "Where are my DeWalt batteries?"` |
| `home_inventory.get_item_location` | where an item is, status, loan | `name: impact wrench` |
| `home_inventory.get_container_contents` | items inside | `container: "15"` (number, name or label code) |
| `home_inventory.move_item` | new location | `item_id: 12, container: "15"` or `location: "Garage"`, optional `quantity`, `temporary` |
| `home_inventory.move_container` | new location | `container: "15", location: "Shed"` or `into_container: "3"` |
| `home_inventory.adjust_quantity` | new quantity | `item_id: 7, delta: -1` |
| `home_inventory.refresh` | | Refresh sensors now |

The Home Assistant user who triggers an action (via the UI, a script or a voice assistant) is passed to the App for permission checks and history.

### Example: ask where something is

```yaml
script:
  where_is:
    fields:
      thing: { example: "hedge trimmer" }
    sequence:
      - action: home_inventory.get_item_location
        data: { name: "{{ thing }}" }
        response_variable: found
      - action: notify.mobile_app_phone
        data:
          message: "{{ found.answer }}"
```

## Events

| Event | Data |
|---|---|
| `home_inventory_item_added` | item_ids, product_id, name, quantity, location, user |
| `home_inventory_item_removed` | item_id, name, quantity, location, user |
| `home_inventory_item_moved` | item_id, name, quantity, from, to, temporary, user |
| `home_inventory_container_moved` | container_id, name, number, from, to, temporary, user |
| `home_inventory_container_inventory_updated` | container_id, (added, updated, removed), user |
| `home_inventory_item_loaned` | item_id, loan_id, name, borrower, quantity, expected_return, user |
| `home_inventory_item_returned` | item_id, loan_id, name, borrower, user |
| `home_inventory_warranty_expiring` | item_id, name, warranty_expires, days_left |
| `home_inventory_item_expiring` | item_id, name, quantity, expires_on, days_left, expired, location |
| `home_inventory_low_stock` | product_id, name, quantity, min_quantity |
| `home_inventory_maintenance_completed` | item_id, name, task, performed_on, hours, next_due_on, next_due_hours, user |
| `home_inventory_maintenance_due` | item_id, item_name, task_id, title, status (due_soon/overdue), next_due_on, days_left, hours_left, estimated_due_on, parts_ready, parts_missing, added_to_shopping_list, message |
| `home_inventory_repair_job_status` | job_id, number, status, previous, user (no customer details) |
| `home_inventory_tag_scanned` | tag_id, target_type, target_id, default_action, user |
| `home_inventory_shopping_list_updated` | reason (added, opened_at_shop, in_cart, purchased…), open_items, user |

Warranty, expiry and low-stock events fire once per crossing. They re-arm when the date changes or the stock is replenished.

### Example: shopping-list on low stock

```yaml
automation:
  - alias: Restock consumables
    triggers:
      - trigger: event
        event_type: home_inventory_low_stock
    actions:
      - action: todo.add_item
        target: { entity_id: todo.shopping_list }
        data: { item: "{{ trigger.event.data.name }}" }
```

### Example: warranty ending reminder

The App sends `home_inventory_warranty_expiring` once per item when its warranty enters the `warranty_notice_days` window (30 days by default).

```yaml
automation:
  - alias: Warranty ending soon
    triggers:
      - trigger: event
        event_type: home_inventory_warranty_expiring
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "Warranty ending"
          message: "{{ trigger.event.data.name }}: warranty ends in {{ trigger.event.data.days_left }} days ({{ trigger.event.data.warranty_expires }})."
```

### Example: overdue loan reminder

```yaml
automation:
  - alias: Overdue loans
    triggers:
      - trigger: numeric_state
        entity_id: sensor.home_inventory_overdue_loans
        above: 0
    actions:
      - action: notify.notify
        data: { message: "Something you lent is overdue — check Inventory › Loans." }
```
