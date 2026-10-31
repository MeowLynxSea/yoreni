---
name: home-widgets
description: Create and maintain Home widgets for glanceable values, metric grids, progress bars, charts, timelines, lists, tables, links, composed panels, or custom HTML.
metadata: {"yoreni":{"emoji":"🧩"}}
---

# Home widgets

A Home widget is a small card on the user's Home panel. Use the `widget` tool when the user wants something to stay visible and update in place: today's weather, a countdown, a build status, a short task list, a quota table, a metric.

Widgets belong to this yoreni install and show in every WebUI session. Home lists them in the user's stored order; Settings lets the user hide, reorder, refresh, and delete them, so do not build a management surface of your own.

If `widget` is not in the tool list, Home widgets are unavailable in this runtime. Say so instead of writing files by hand.

## Actions

| Action | Use it to |
| --- | --- |
| `list` | See every widget with its id, view, schedule, and last error. |
| `get` | Read one widget's definition, stored data, HTML, and script source. |
| `create` | Add a widget; needs `widget_id`, `title`, and `view`. |
| `update` | Change only the fields you pass; omitted fields keep their values. |
| `set_data` | Replace the stored data after validating it against the view. |
| `refresh` | Run the backend command now. |
| `delete` | Remove the widget, its data, and its Home placement. |

Common calls:

```
widget(action="list")
widget(action="get", widget_id="weather")
widget(action="create", widget_id="weather", title="Weather", view="stat", description="Berlin")
widget(action="update", widget_id="weather", title="Berlin weather")
widget(action="set_data", widget_id="weather", data={"value": "8°C", "label": "Berlin"})
widget(action="refresh", widget_id="weather")
widget(action="delete", widget_id="weather")
```

`widget_id` is required for every action except `list`. Use 1–64 letters, digits, `_`, or `-`; `goal` and `automations` are taken by built-in widgets. Pick a short, stable id from the topic (`weather`, `deploys`, `reading-list`) rather than a timestamp.

`update` cannot change the id. To rename, create a new widget and delete the old one. `argv=[]` removes a backend command; `every_seconds=0` clears the schedule.

## Pick a view

| View | Renders | Data shape |
| --- | --- | --- |
| `stat` | One large value with optional caption | `{"value": string\|number, "label"?: string, "detail"?: string}` |
| `metrics` | Up to 6 labelled values in a 1–3 column grid | `{"items": [{"label": string, "value": string\|number, "detail"?: string, "tone"?: tone}], "columns"?: 1\|2\|3}` |
| `progress` | Up to 6 bars, each a value against a limit | `{"items": [{"label": string, "value": number, "max"?: number, "detail"?: string, "tone"?: tone}]}` |
| `facts` | Up to 8 label/value rows separated by hairlines | `{"items": [{"label": string, "value": string\|number, "tone"?: tone}]}` |
| `list` | Up to 20 rows with a title and trailing metadata | `{"items": [{"title": string, "detail"?: string, "meta"?: string}]}` |
| `table` | Up to 6 columns × 20 rows | `{"columns": [string], "rows": [[string\|number]]}` |
| `timeline` | Up to 8 ordered events on a rail, with tone dots | `{"items": [{"title": string, "detail"?: string, "meta"?: string, "tone"?: tone}]}` |
| `chart` | A bar or line chart of up to 12 points | `{"type"?: "bar"\|"line", "max"?: number, "items": [{"label": string, "value": number}]}` |
| `badges` | Up to 8 pills for tags and short statuses | `{"items": [{"label": string, "detail"?: string, "tone"?: tone}]}` |
| `links` | Up to 6 rows that open an http(s) URL | `{"items": [{"label": string, "href": string, "detail"?: string, "meta"?: string}]}` |
| `text` | One paragraph | `{"text": string}` (≤4000 characters) |
| `panel` | A composed card: lead line, blocks, footer note | `{"lead"?: string, "blocks": [block], "note"?: string}` |
| `html` | An HTML fragment in a sandboxed frame | any JSON object |

Prefer a template view. It already inherits the panel's typography, spacing, theme, and accessibility, so the card looks native without extra work. Choose by what the data is:

- One number that carries the card → `stat`; several numbers of equal weight → `metrics`.
- Usage against a limit → `progress`; a value over time → `chart`.
- Ordered events → `timeline`; unordered rows → `list`.
- Dense label/value pairs → `facts`; a grid with headers → `table`.
- Short statuses or tags → `badges`; a pointer to the source → `links`.
- A summary line plus several blocks → `panel`.

Reach for `html` only when the layout cannot be expressed with these blocks.

`tone` is `neutral` (default), `positive`, `warning`, or `critical`. It colors a value, bar, dot, or pill, but the label must still carry the meaning; never rely on color alone.

```
widget(action="create", widget_id="focus", title="Focus", view="stat")
widget(action="create", widget_id="limits", title="Limits", view="metrics")
widget(action="create", widget_id="storage", title="Storage", view="progress")
widget(action="create", widget_id="region", title="Region", view="facts")
widget(action="create", widget_id="tasks", title="Today", view="list")
widget(action="create", widget_id="quota", title="Quota", view="table")
widget(action="create", widget_id="deploys", title="Deploys", view="timeline")
widget(action="create", widget_id="traffic", title="Traffic", view="chart")
widget(action="create", widget_id="flags", title="Flags", view="badges")
widget(action="create", widget_id="docs", title="Docs", view="links")
widget(action="create", widget_id="note", title="Note", view="text")
widget(action="create", widget_id="account", title="Account", view="panel")
widget(action="create", widget_id="status", title="Status", view="html", html="<p id='status'></p>")
```

A template widget with no data renders nothing, and `create` does not take `data`: create it with a backend command, or call `set_data` in the same turn. After changing a widget's `view`, send data in the new shape; old cached data is not converted.

## Rich panels

`panel` builds a whole card from blocks, so a dashboard-like widget does not need HTML. `lead` is one summary sentence above the blocks, `note` is a muted footer, and `blocks` is an ordered list of 1–8 entries:

| Block `kind` | Fields |
| --- | --- |
| `metrics` | `items` and `columns` as in `view="metrics"` |
| `progress` | `items` as in `view="progress"` |
| `facts` | `items` as in `view="facts"` |
| `list` | `items` as in `view="list"` |
| `table` | `columns` and `rows` as in `view="table"` |
| `timeline` | `items` as in `view="timeline"` |
| `chart` | `type`, `max`, and `items` as in `view="chart"` |
| `badges` | `items` as in `view="badges"` |
| `links` | `items` as in `view="links"` |
| `text` | `text` |
| `divider` | no other fields; a hairline that separates groups |

Every block except `divider` accepts an optional `title`, rendered as a small caption above it.

```
widget(
  action="create",
  widget_id="quota",
  title="Hark 额度",
  view="panel",
  every_seconds=300,
  script=(
    "import json\n"
    "print(json.dumps({\n"
    "  'lead': 'Hark Pro (free) · 今日剩余 100%，本月剩余 98%。',\n"
    "  'blocks': [\n"
    "    {'kind': 'metrics', 'items': [\n"
    "      {'label': '今日剩余', 'value': '100%'},\n"
    "      {'label': '每日重置', 'value': '5:03 AM'},\n"
    "      {'label': '本月剩余', 'value': '98%'},\n"
    "      {'label': '每月重置', 'value': '8:00 AM'},\n"
    "    ]},\n"
    "    {'kind': 'progress', 'title': '本月用量', 'items': [\n"
    "      {'label': 'Hark Pro', 'value': 2, 'max': 100, 'detail': '2 / 100'},\n"
    "    ]},\n"
    "  ],\n"
    "  'note': '每 5 分钟刷新',\n"
    "}))\n"
  ),
)
```

Keep one idea per panel. A lead line, one metrics block, and a note cover most cards; add other blocks only when they carry the same topic, and split unrelated topics into separate widgets.

## Data update

```
widget(action="set_data", widget_id="focus", data={"value": 3, "label": "open tasks"})
```

`set_data` rejects data that does not match the view, so a bad payload never reaches Home. A successful write refreshes the card without reloading the page.

Use `set_data` when the value comes from the conversation, a scheduled turn, or the agent's own work. Use a backend command when the value must refresh without the agent.

## Backend commands

A widget command is argv, not a shell: pipes, `&&`, and globs do not work. Put that logic inside the script.

Pass the source with `script` (default filename `data.py`). With `script` and no `argv`, the command becomes `["python3", "data.py"]`. For another runtime, set `script_filename` and `argv` together; a command accepts at most 8 arguments of 200 characters each.

```
widget(
  action="create",
  widget_id="deploys",
  title="Deploys",
  view="list",
  script=(
    "import json\n"
    "print(json.dumps({'items': [{'title': 'api', 'meta': 'healthy'}]}))\n"
  ),
  every_seconds=300,
)
```

Contract:

- Working directory, `HOME`, and `TMPDIR` all point at the widget's own directory. Keep temp files there and clean them up.
- Print exactly one JSON object on stdout. Log lines, progress text, and multiple objects are invalid output. Anything printed on stderr is only used to explain a failure.
- `timeout_seconds` defaults to 30 and accepts 1–120. A run that exceeds it is killed and recorded as the widget's error.
- Non-zero exit, missing executable, timeout, invalid JSON, and data that does not match the view all become the widget's last error shown on Home; the previous data stays.
- When the exec sandbox is active, the project workspace is bound read-only for the command. Read from it, write only inside the widget directory.
- Never write `widget.json`, `view.html`, or `data.json` from the script; the gateway owns those files.
- `every_seconds` accepts 30–86400. Create, and an update that changes the script or argv, run the command once immediately so failures surface in the result; the scheduled refresh starts one interval later.
- `python3` must be on `PATH`. On Windows, or when only `python` exists, pass `argv=["python", "data.py"]` explicitly.

After a failed create, the widget is still saved with the error attached. Fix the script with `update`, then call `refresh` and check `get`.

## HTML widgets

Use `html` only when no template fits, and follow the `yoreni-design` skill for color, type, spacing, and voice. `html` is a fragment, not a full document. The gateway supplies `<!doctype html>`, the CSP, base styles, and the data bootstrap; your markup is placed in `<body>`.

```
<div id="value"></div>
<script>
  function render() {
    document.getElementById("value").textContent = window.yoreniWidget.data.value + "°C";
  }
  render();
  window.addEventListener("yoreni:data", render);
</script>
```

Contract:

- The frame has an opaque origin with scripts only: no same-origin access, no cookies, no `localStorage`, no fetch or XHR, no top navigation, no popups. Inline all CSS and JavaScript.
- The frame background is transparent so the card surface shows through. Do not paint `html` or `body` unless the widget needs its own contrast surface, and theme that surface for both light and dark.
- Images may load from `https:` or `data:` URIs. Everything else is blocked by the content policy.
- `window.yoreniWidget` exists before your markup runs: `{data, updatedAt, theme, refresh()}`. `theme` is `{scheme, foreground, mutedForeground, border, primary, fontFamily, fontSize}`.
- Data refreshes arrive as a `yoreni:data` window event (`event.detail` is the new data) and in `window.yoreniWidget.data`. Re-render from that event instead of reloading the page.
- Theme colors are also available as `--widget-foreground`, `--widget-muted-foreground`, `--widget-border`, and `--widget-primary`. Body font and text color follow the user's theme, and the frame starts at 13px.
- The frame reports its own height; the panel clamps it to 40–600px. Keep the layout compact and let long content scroll inside the frame.
- `window.yoreniWidget.refresh()` asks the gateway to re-run the backend command. It only does something when the widget has a command.
- A widget whose command fails keeps its previous data; the panel shows one muted error line under the frame.

## Limits

- At most 24 widgets per install.
- Title 60 characters, description 120 (shown in Settings).
- HTML 128 KB, script 256 KB, stored data 256 KB.
- List 20 items, table 6 columns × 20 rows with one cell per column, text 4000 characters.
- Metrics and progress 6 items each, facts 8, chart 12 points, timeline 8, badges 8, links 6; panel 8 blocks.

## Rules of thumb

- One widget per topic. Update the existing widget instead of creating a second one for the same value.
- Ask before deleting or repurposing a widget the user already sees.
- Write `title` and `description` for the user: the title is the card heading, and the description is the Settings hint that should say what refreshes the value.
- After creating or changing a widget, tell the user what appears on Home and how often it refreshes. Do not paste the manifest, the script, or file paths.
