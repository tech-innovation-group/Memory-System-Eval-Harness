# OpenViking / EchoMemory token report hourly refresh

## Stable report paths

- `/Users/chx/locomo-eval-web/static/openviking_token_observability_design_20260615.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/openviking_token_observability_design_20260615.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/openviking_token_observability_latest.html`

`openviking_token_observability_latest.html` is the stable mobile/browser path.

## Generator

- Script: `/Users/chx/locomo-eval-web/scripts/render_echomemory_openviking_token_design_report.py`
- Refresh shell: `/Users/chx/locomo-eval-web/scripts/refresh_openviking_echomemory_token_report.sh`

The generator reads the current run artifacts directly:

- OpenViking tool_on: `runs/openviking_v024_formal_conv30_fixed_20260616/openviking_qa`
- OpenViking tool_off: `runs/openviking_v024_notool_full_20260616`
- OpenViking search_only: `runs/openviking_v024_searchonly_full_20260616`
- OpenViking import: `runs/openviking_v024_formal_import_20260616/openviking_import_summary.json`
- EchoMemory: `runs/echomemory_v010_conv30_eval_20260615_123200`

## Hourly refresh

- LaunchAgent label: `com.locomo-eval.openviking-echomemory-token-report-refresh`
- plist: `/Users/chx/Library/LaunchAgents/com.locomo-eval.openviking-echomemory-token-report-refresh.plist`
- interval: `3600` seconds

Useful commands:

```bash
launchctl print gui/$(id -u)/com.locomo-eval.openviking-echomemory-token-report-refresh
tail -f /Users/chx/locomo-eval-web/runs/openviking_echomemory_token_report_refresh.log
```

The HTML also includes:

```html
<meta http-equiv="refresh" content="3600" />
```

So the page auto-refreshes in the browser every hour, and launchd regenerates the file every hour.
