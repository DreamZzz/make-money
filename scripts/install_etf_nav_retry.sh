#!/bin/bash
# 安装一次性 LaunchAgent: 5/31 00:48 跑 refetch_etf_nav.sh,跑完自我卸载。
set -e
cd "$(dirname "$0")/.."

PROJECT=$(pwd)
LABEL="com.quant.etf-nav-refetch"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
OUTPUT_DIR="$PROJECT/output"
mkdir -p "$OUTPUT_DIR"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>${PROJECT}/scripts/refetch_etf_nav.sh; launchctl unload -w ${PLIST}; rm -f ${PLIST}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Month</key><integer>5</integer>
    <key>Day</key><integer>31</integer>
    <key>Hour</key><integer>0</integer>
    <key>Minute</key><integer>48</integer>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key>
  <string>${OUTPUT_DIR}/etf_nav_retry_launchd.log</string>
  <key>StandardErrorPath</key>
  <string>${OUTPUT_DIR}/etf_nav_retry_launchd_err.log</string>
</dict>
</plist>
EOF

# 加载(等同 reload)
launchctl unload -w "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "Installed: $PLIST"
echo "Scheduled: 2026-05-31 00:48 (local)"
echo "Script:    $PROJECT/scripts/refetch_etf_nav.sh"
echo "Logs:      $OUTPUT_DIR/etf_nav_retry.log  $OUTPUT_DIR/etf_nav_retry_launchd.log"
echo "Verify:    launchctl list | grep ${LABEL}"
echo "Cancel:    launchctl unload -w $PLIST && rm $PLIST"
