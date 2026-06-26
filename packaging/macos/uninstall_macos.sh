#!/usr/bin/env bash
set -euo pipefail
APP_ID="com.clinicaldictation.localservice"
sudo launchctl bootout system "/Library/LaunchDaemons/$APP_ID.plist" >/dev/null 2>&1 || true
sudo rm -f "/Library/LaunchDaemons/$APP_ID.plist"
sudo rm -rf "/Library/Application Support/ClinicalDictationAssistant"
echo "病历助手 removed."