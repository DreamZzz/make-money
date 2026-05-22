#!/usr/bin/env bash
# Install the StartInterval watchdog that owns open/close scheduled workflows.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$PROJECT/output"
LABEL="com.quant.scheduler-watchdog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
OLD_LABELS=(
  "com.quant.daily-update"
  "com.quant.open-paper-trade"
)

_python_is_compatible() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

_resolve_python() {
  if [ -n "${PYTHON:-}" ]; then
    if _python_is_compatible "$PYTHON"; then
      echo "$PYTHON"
      return
    fi
    echo "PYTHON=$PYTHON is not Python 3.12+." >&2
    exit 1
  fi

  for candidate in python3.12 /opt/homebrew/bin/python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
      if _python_is_compatible "$resolved"; then
        echo "$resolved"
        return
      fi
    elif [ -x "$candidate" ] && _python_is_compatible "$candidate"; then
      echo "$candidate"
      return
    fi
  done

  echo "Could not find Python 3.12+. Set PYTHON=/path/to/python3.12 and rerun." >&2
  exit 1
}

_python_can_import() {
  "$1" - "$2" <<'PY' >/dev/null 2>&1
import importlib.util
import sys
raise SystemExit(0 if importlib.util.find_spec(sys.argv[1]) is not None else 1)
PY
}

_resolve_qlib_python() {
  if [ -n "${QLIB_PYTHON:-}" ]; then
    if _python_is_compatible "$QLIB_PYTHON" && _python_can_import "$QLIB_PYTHON" qlib; then
      echo "$QLIB_PYTHON"
      return
    fi
    echo "QLIB_PYTHON=$QLIB_PYTHON is not Python 3.12+ with qlib; falling back to project resolver." >&2
  fi

  for candidate in "$PROJECT/.venv-qlib/bin/python" "$1" python3.12 /opt/homebrew/bin/python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
    elif [ -x "$candidate" ]; then
      resolved="$candidate"
    else
      continue
    fi
    if _python_is_compatible "$resolved" && _python_can_import "$resolved" qlib; then
      echo "$resolved"
      return
    fi
  done

  echo "$1"
}

PYTHON_BIN="$(_resolve_python)"
QLIB_PYTHON_BIN="$(_resolve_qlib_python "$PYTHON_BIN")"
mkdir -p "$OUTPUT_DIR" "$HOME/Library/LaunchAgents"

for old_label in "${OLD_LABELS[@]}"; do
  old_plist="$HOME/Library/LaunchAgents/$old_label.plist"
  if [ -f "$old_plist" ]; then
    launchctl bootout "gui/$(id -u)" "$old_plist" >/dev/null 2>&1 || true
    launchctl unload "$old_plist" >/dev/null 2>&1 || true
    echo "Disabled calendar LaunchAgent: $old_label"
  fi
done

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl unload "$PLIST" >/dev/null 2>&1 || true

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$PROJECT/scripts/scheduler_watchdog.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHON</key>
    <string>$PYTHON_BIN</string>
    <key>QLIB_PYTHON</key>
    <string>$QLIB_PYTHON_BIN</string>
    <key>PYTHONPATH</key>
    <string>$PROJECT</string>
    <key>PATH</key>
    <string>$(dirname "$PYTHON_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>no_proxy</key>
    <string>*</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$OUTPUT_DIR/scheduler_watchdog.log</string>
  <key>StandardErrorPath</key>
  <string>$OUTPUT_DIR/scheduler_watchdog_error.log</string>
</dict>
</plist>
PLIST

chmod 644 "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

echo "Installed $LABEL"
echo "State: $OUTPUT_DIR/scheduler_state.json"
echo "Logs:  $OUTPUT_DIR/scheduler_watchdog.log / scheduler_watchdog_error.log"
