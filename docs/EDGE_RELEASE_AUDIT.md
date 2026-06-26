# Windows / Edge release audit - 病历助手

Generated: 2026-06-26 10:49:42
Version: 0.10.0

## Result

Pass. The Windows extension package is ready for Edge Add-ons beta/hidden submission preparation.

## Checks completed

- Manifest V3.
- Localized product name is `病历助手`.
- Package root contains `manifest.json`.
- Permissions are limited to `storage` and `http://127.0.0.1:8765/*`.
- No remote JavaScript, `eval()`, or `new Function()` detected in the scoped extension scan.
- Release script passed: `scripts/check_release.ps1`.
- Package regenerated: `dist/bingli-assistant-extension-v0.10.0.zip`.

## Submission package

- Folder: `E:\project\input\dist\edge-submission-v0.10.0`
- Upload file: `E:\project\input\dist\edge-submission-v0.10.0\extension-package.zip`
- SHA256: `5153390e80c3eab990aed0504ed7d02aa9f6e452e10f2fa051046693cd8da503`

## GitHub recommendation

GitHub is not required by Edge Add-ons before submission. It is recommended for version control, release history, public privacy-policy hosting, and distributing the Windows local service package when you do not yet have an official website.
