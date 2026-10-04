"""Fixed browser-extension ID.

An extension loaded with "load unpacked" gets an ID derived from its folder path, so the ID changes when the
folder moves. A `key` (the public half of an RSA key) in manifest.json makes the ID depend on the key instead:
the same on every computer, in Edge and in Chrome. The service uses that ID to accept only this extension
(server/data/allowed_extensions.json, server/security.py).

    python scripts/extension_id.py show                 # ID that manifest.json's key produces
    python scripts/extension_id.py generate [--key-file PATH]
        creates a new key pair unless the private key file already exists, writes the public key into
        manifest.json and the ID into server/data/allowed_extensions.json

The PRIVATE key never goes into the repository. Keep a backup: it is what lets you keep the same ID later
(for example when packaging for a store). Anyone who has it can publish an extension with this ID.
"""
import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "extension" / "manifest.json"
ALLOWED = ROOT / "server" / "data" / "allowed_extensions.json"
DEFAULT_KEY_FILE = Path.home() / ".bingli" / "extension_key.pem"


def extension_id_from_public_key(public_key_der: bytes) -> str:
    """Chromium's rule: SHA-256 of the DER SubjectPublicKeyInfo, first 16 bytes, each hex digit shown as a-p."""
    digest = hashlib.sha256(public_key_der).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def extension_id_from_manifest_key(key_base64: str) -> str:
    return extension_id_from_public_key(base64.b64decode(key_base64))


def manifest_key() -> str | None:
    return json.loads(MANIFEST.read_text(encoding="utf-8")).get("key")


def public_key_der_from_private_pem(pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization
    private = serialization.load_pem_private_key(pem, password=None)
    return private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def generate(key_file: Path) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if key_file.exists():
        der = public_key_der_from_private_pem(key_file.read_bytes())
        print(f"using the existing private key {key_file}")
    else:
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
        der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        print(f"created the private key {key_file}  <-- keep a backup, never commit it")
    key_b64 = base64.b64encode(der).decode("ascii")
    ext_id = extension_id_from_public_key(der)

    manifest_text = MANIFEST.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    manifest["key"] = key_b64
    ordered = {k: manifest[k] for k in ("manifest_version", "name", "version", "key") if k in manifest}
    ordered.update({k: v for k, v in manifest.items() if k not in ordered})
    MANIFEST.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    allowed = {"ids": [ext_id],
               "note": "Browser extensions allowed to change data and use dictation (server/security.py). "
                       "An empty list allows every extension. After publishing to a store, add the store's ID here. "
                       "Environment variable BINGLI_ALLOWED_EXTENSIONS overrides this file ('*' allows all)."}
    ALLOWED.write_text(json.dumps(allowed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ext_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    gen = sub.add_parser("generate")
    gen.add_argument("--key-file", default=str(DEFAULT_KEY_FILE))
    args = parser.parse_args(argv)
    if args.command == "show":
        key = manifest_key()
        if not key:
            print("manifest.json has no key")
            return 1
        print(extension_id_from_manifest_key(key))
        return 0
    print("extension ID:", generate(Path(args.key_file)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
