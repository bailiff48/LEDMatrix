# Plugins

The fork can run plugins written for ChuckBuilds' plugin-based LEDMatrix core
(v3.x) without changes. The host is `src/plugin_host.py`; the base class is a
slim copy at Chuck's import path, `src/plugin_system/base_plugin.py`.

## Turning a plugin on

A plugin folder here (`plugins/<id>/`) is loaded when `config/config.json`
has a section named after its id with `"enabled": true`:

```json
"fantasy-blitz": {
  "enabled": true,
  "scoring_format": "ppr",
  "watchlist": ["Josh Allen", "Bijan Robinson"]
}
```

Anything left out takes the default from the plugin's `config_schema.json`.
Secrets (for example ESPN `espn_s2` / `swid` for a private league) go in
`config/config_secrets.json` under the same key and path; ConfigManager
merges them in:

```json
"fantasy-blitz": { "league": { "espn_s2": "...", "swid": "{...}" } }
```

Restart after changing config: `sudo systemctl restart ledmatrix`.

## How it fits the display loop

- Each of the plugin's display modes joins the normal rotation after the
  built-in modes. A screen with nothing to show is skipped.
- `update()` runs on a background thread on the plugin's own schedule
  (fantasy-blitz: every 60s while games are live, 15 min otherwise).
- A mode named `<x>_live` with the plugin's `live_priority` on takes over the
  panel through the same live-priority path as the built-in sports. For
  fantasy-blitz that is the big-play alert (`fantasy_live`).
- How long each screen stays up comes from the plugin.

## Vendored plugins

| Plugin | Source | Commit | Version |
|---|---|---|---|
| fantasy-blitz | ChuckBuilds/ledmatrix-plugins `plugins/fantasy-blitz` | 3e45af3 (2026-10-21) | 1.2.0 |

Vendored unmodified (`diff -r` clean). Its `test/` folder holds Chuck's
fixtures and golden images; all eleven 128x32 screens match those goldens
pixel for pixel through this host.

Not supported by the host (plugins fall back on their own): Vegas ticker
mode, web-UI forms from `config_schema.json`, the plugin store, and
`web_ui_actions`.
