# Packet maps

A packet map describes one body's submission form: its fields, in the
portal's order, and where each value comes from. Registrar uses it to lay
out the copy-ready packet for each entry (`/packets/<credential>`,
`python3 -m cpebot packet <credential>`).

**Adding a body means adding a file here. It does not mean changing code.**

## Which map a credential uses

1. the first map whose `credentials:` list names the credential;
2. otherwise the first map whose `body_id` matches the credential's
   `body_id` in `bodies.yaml` (and that has no `credentials:` list);
3. otherwise `generic.yaml`, which must exist.

`python3 -m cpebot packetmaps` prints the result for every credential.

## Format (schema v1)

The files use the same YAML subset as `bodies.yaml`: double-quoted strings,
block mappings and lists, `[...]` flow lists, `#` comments. No single quotes,
no `{...}` flow maps, no multi-line strings.

```yaml
schema_version: 1
id: "examplebody"                 # unique; the file name is not significant
form_name: "Example portal: Add activity"
body_id: "examplebody"            # or credentials: ["cred_id", ...]
verified: false                   # true only once checked against the live form
verified_note: "What was checked, when, and against what."
source_url: null                  # public page describing the form, if any
holder_note: "One line shown above the packet."
date_format: "%m/%d/%Y"           # strftime; default %Y-%m-%d
fields:
  - label: "CPE group"            # exactly as the portal labels it
    source: "group.id"
    map:                          # optional value translation
      group_a: "Group A"
    default: "—"                  # optional; used for unmapped values
    required: true                # empty value is flagged [MISSING]
    note: "Shown under the value."
    format: "%d %b %Y"            # optional, date sources only
```

Unknown keys, unknown sources and duplicate labels are load errors.

**A packet map never holds a way into the portal.** Any key containing
`password`, `login`, `username`, `token`, `secret`, `session`, `cookie`,
`otp` and similar is refused at load time. You log into the portal yourself;
Registrar fills nothing in on your behalf and stores no portal login.

## Value sources

| source | value |
|---|---|
| `entry.activity_date` | activity date, per `format` / `date_format` |
| `entry.title` | entry title |
| `entry.provider` | provider / sponsor |
| `entry.hours` | hours as logged |
| `entry.count` | item count as logged |
| `entry.notes` | the entry's notes |
| `entry.uuid` | Registrar's entry id |
| `entry.tags` / `entry.tag_labels` | tag ids / labels, comma-separated |
| `entry.markers` / `entry.marker_labels` | topic-marker ids / labels |
| `credit.amount` | credits claimed for this activity, before caps |
| `credit.unit` | the credential's unit label (CPE, CEU, PDU, ...) |
| `bucket.id` / `bucket.name` | the category the engine places the entry in |
| `group.id` / `group.name` | the group(s) the entry feeds |
| `holder.name` | attendee name from Settings |
| `credential.name` / `body.name` | from `bodies.yaml` |
| `evidence.filenames` / `evidence.count` | attached files, as named in the bundle |
| `literal` | the field's own `value` |
| `holder_choice` | nothing: shown as "choose in portal" |

Use `holder_choice` for anything Registrar does not know (ISC2's CISSP
domain, a portal's own activity-type dropdown). A blank that says so is
better than a guess that looks like an answer.

## The shipped maps

- `generic.yaml` — Registrar's own field set; the fallback.
- `isc2.yaml` — ISC2 / CISSP. **Unverified**: the labels were drafted, not
  read off the live form. Correct them the first time you submit and set
  `verified: true` with a dated note.

## Relationship to bodies.yaml

Packet maps are deliberately separate from `bodies.yaml`, which holds the
rules and their provenance. If `bodies.yaml` later carries a
`submission.packet_map: <id>` key, `cpebot.packets.map_for()` is the single
place that should honour it ahead of the lookups above.
