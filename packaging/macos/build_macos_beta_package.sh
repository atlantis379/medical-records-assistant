#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
  echo "This beta package is Apple Silicon only. Please build on arm64 macOS, not $ARCH." >&2
  exit 1
fi
if [[ "$(sysctl -in sysctl.proc_translated 2>/dev/null || echo 0)" == "1" ]]; then
  echo "This shell is running under Rosetta. Open a native arm64 Terminal and retry." >&2
  exit 1
fi

DIST_DIR="$PROJECT_ROOT/dist/macos"
APP_ID="com.clinicaldictation.localservice"
VERSION="$(python3 - <<'PY'
import json, pathlib
m=json.loads(pathlib.Path('extension/manifest.json').read_text(encoding='utf-8'))
print(m['version'])
PY
)"
PKG_NAME="bingli-assistant-macos-arm64-v${VERSION}-beta"
STAGE="$DIST_DIR/$PKG_NAME"
PAYLOAD="$STAGE/payload"
INSTALL_ROOT="$PAYLOAD/Library/Application Support/ClinicalDictationAssistant"
LAUNCH_DAEMONS="$PAYLOAD/Library/LaunchDaemons"
SCRIPTS_DIR="$STAGE/scripts"
MODEL_CACHE="${MODELSCOPE_CACHE:-$HOME/.cache/modelscope/hub}"
CREATE_PKG="${CREATE_PKG:-1}"

REQUIRED_MODELS=(
  "models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
  "models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
  "models/iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
)

copy_dir() {
  local src="$1"
  local dst="$2"
  if [[ ! -e "$src" ]]; then
    echo "Missing: $src" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$dst")"
  rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' "$src" "$dst"
}

cd "$PROJECT_ROOT"
rm -rf "$STAGE"
mkdir -p "$INSTALL_ROOT" "$LAUNCH_DAEMONS" "$SCRIPTS_DIR"

echo "Building macOS Apple Silicon beta package: $STAGE"

copy_dir "$PROJECT_ROOT/extension/" "$INSTALL_ROOT/extension/"
copy_dir "$PROJECT_ROOT/server/" "$INSTALL_ROOT/server/"
[[ -d "$PROJECT_ROOT/docs" ]] && copy_dir "$PROJECT_ROOT/docs/" "$INSTALL_ROOT/docs/"
[[ -f "$PROJECT_ROOT/README.md" ]] && cp "$PROJECT_ROOT/README.md" "$INSTALL_ROOT/README.md"
[[ -f "$PROJECT_ROOT/THIRD_PARTY_NOTICES.md" ]] && cp "$PROJECT_ROOT/THIRD_PARTY_NOTICES.md" "$INSTALL_ROOT/THIRD_PARTY_NOTICES.md"

if [[ ! -d "$PROJECT_ROOT/.venv-macos" ]]; then
  echo "Creating macOS arm64 virtual environment..."
  python3 -m venv "$PROJECT_ROOT/.venv-macos"
  "$PROJECT_ROOT/.venv-macos/bin/python" -m pip install --upgrade pip
  "$PROJECT_ROOT/.venv-macos/bin/python" -m pip install -r "$PROJECT_ROOT/server/requirements.txt"
fi
copy_dir "$PROJECT_ROOT/.venv-macos/" "$INSTALL_ROOT/.venv/"

mkdir -p "$INSTALL_ROOT/models/modelscope/hub"
for rel in "${REQUIRED_MODELS[@]}"; do
  src="$MODEL_CACHE/$rel"
  dst="$INSTALL_ROOT/models/modelscope/hub/$rel"
  if [[ ! -d "$src" ]]; then
    echo "Missing required model cache: $src" >&2
    echo "Run once on this Mac with network access, or copy models into: $MODEL_CACHE" >&2
    exit 1
  fi
  copy_dir "$src/" "$dst/"
done

cat > "$INSTALL_ROOT/start_server_macos.command" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
APP_ROOT="$(cd "$(dirname "$0")" && pwd)"
export ASR_DEVICE="${ASR_DEVICE:-cpu}"
export ASR_PRELOAD_STREAMING="${ASR_PRELOAD_STREAMING:-1}"
export MODELSCOPE_CACHE="$APP_ROOT/models/modelscope/hub"
export MODELSCOPE_OFFLINE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONUTF8=1
PY_SITE="$(find "$APP_ROOT/.venv/lib" -maxdepth 2 -type d -name site-packages | head -n 1)"
export PYTHONPATH="$PY_SITE:$APP_ROOT"
cd "$APP_ROOT"
"$APP_ROOT/.venv/bin/python" -B -m uvicorn server.app:app --host 127.0.0.1 --port 8765
EOS
chmod +x "$INSTALL_ROOT/start_server_macos.command"

cat > "$INSTALL_ROOT/stop_server_macos.command" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
PIDS="$(lsof -ti tcp:8765 || true)"
if [[ -n "$PIDS" ]]; then
  kill $PIDS
fi
EOS
chmod +x "$INSTALL_ROOT/stop_server_macos.command"

cat > "$INSTALL_ROOT/check_service_macos.command" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
curl -s http://127.0.0.1:8765/health || true
echo
read -r -p "Press Enter to close..." _
EOS
chmod +x "$INSTALL_ROOT/check_service_macos.command"

cat > "$LAUNCH_DAEMONS/$APP_ID.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$APP_ID</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Library/Application Support/ClinicalDictationAssistant/.venv/bin/python</string>
    <string>-B</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>server.app:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8765</string>
  </array>
  <key>WorkingDirectory</key><string>/Library/Application Support/ClinicalDictationAssistant</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ASR_DEVICE</key><string>cpu</string>
    <key>ASR_PRELOAD_STREAMING</key><string>1</string>
    <key>MODELSCOPE_CACHE</key><string>/Library/Application Support/ClinicalDictationAssistant/models/modelscope/hub</string>
    <key>MODELSCOPE_OFFLINE</key><string>1</string>
    <key>HF_HUB_OFFLINE</key><string>1</string>
    <key>TRANSFORMERS_OFFLINE</key><string>1</string>
    <key>PYTHONUTF8</key><string>1</string>
    <key>PYTHONPATH</key><string>/Library/Application Support/ClinicalDictationAssistant/.venv/lib/python3.12/site-packages:/Library/Application Support/ClinicalDictationAssistant</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/clinical-dictation-service.log</string>
  <key>StandardErrorPath</key><string>/tmp/clinical-dictation-service-error.log</string>
</dict>
</plist>
EOF

cat > "$SCRIPTS_DIR/postinstall" <<EOF
#!/usr/bin/env bash
set -e
chown -R root:wheel "/Library/Application Support/ClinicalDictationAssistant" || true
chmod +x "/Library/Application Support/ClinicalDictationAssistant/start_server_macos.command" || true
chmod +x "/Library/Application Support/ClinicalDictationAssistant/stop_server_macos.command" || true
chmod +x "/Library/Application Support/ClinicalDictationAssistant/check_service_macos.command" || true
chown root:wheel "/Library/LaunchDaemons/$APP_ID.plist" || true
chmod 644 "/Library/LaunchDaemons/$APP_ID.plist" || true
launchctl bootout system "/Library/LaunchDaemons/$APP_ID.plist" >/dev/null 2>&1 || true
launchctl bootstrap system "/Library/LaunchDaemons/$APP_ID.plist" || true
launchctl enable system/$APP_ID || true
exit 0
EOF
chmod +x "$SCRIPTS_DIR/postinstall"

cat > "$STAGE/README_MACOS_BETA.md" <<EOF
# 病历助手 macOS Apple Silicon Beta

Version: v$VERSION
Architecture: arm64 Apple Silicon only

## Install

1. Double-click the generated pkg, or run:

   sudo installer -pkg ./bingli-assistant-macos-arm64-v$VERSION-beta.pkg -target /

2. The local service listens on:

   http://127.0.0.1:8765/health

3. Chrome/Edge extension installation for beta:

   - Open chrome://extensions or edge://extensions
   - Enable Developer mode
   - Load unpacked extension from:
     /Library/Application Support/ClinicalDictationAssistant/extension

## Manual service commands

- Start manually:
  /Library/Application\ Support/ClinicalDictationAssistant/start_server_macos.command

- Stop:
  /Library/Application\ Support/ClinicalDictationAssistant/stop_server_macos.command

## Notes

- This beta package is Apple Silicon arm64 only and is not signed or notarized yet.
- For external distribution, sign and notarize the pkg with an Apple Developer ID.
- English model is not included by default.
EOF

if [[ "$CREATE_PKG" == "1" ]]; then
  PKG_PATH="$DIST_DIR/bingli-assistant-macos-arm64-v$VERSION-beta.pkg"
  rm -f "$PKG_PATH"
  pkgbuild \
    --arch arm64 \
    --root "$PAYLOAD" \
    --scripts "$SCRIPTS_DIR" \
    --identifier "$APP_ID" \
    --version "$VERSION" \
    --install-location / \
    "$PKG_PATH"
  echo "PKG created: $PKG_PATH"
else
  TAR_PATH="$DIST_DIR/bingli-assistant-macos-arm64-v$VERSION-beta.tar.gz"
  rm -f "$TAR_PATH"
  tar -czf "$TAR_PATH" -C "$STAGE" payload README_MACOS_BETA.md
  echo "TAR created: $TAR_PATH"
fi
