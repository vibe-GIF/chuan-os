"""Generate a self-signed TLS cert for the LAN HTTPS gateway (N46 / ADR-041).

Usage:
    python scripts/gen_https_cert.py [--dir data] [--days 825]

Writes <dir>/https_cert.pem + <dir>/https_key.pem (self-signed, SAN includes
localhost and this machine's LAN IPv4s so mobile phones on the LAN can reach it).

Strategies (first that succeeds wins):
  1. system openssl (on PATH);
  2. openssl.exe bundled with Git;
  3. Python cryptography (only if installed).
If none is available, print a clear error — the gateway silently degrades to plain
HTTP when the cert is missing, this script never fabricates a certificate.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def _lan_ips() -> list[str]:
    """Collect all LAN IPv4 addrs (incl. loopback) for the cert SAN."""
    ips: set[str] = {"127.0.0.1"}
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    kept: list[str] = []
    for ip in sorted(ips):
        try:
            ipaddress.ip_address(ip)
            kept.append(ip)
        except ValueError:
            continue
    return kept


def _find_openssl() -> str | None:
    """Prefer PATH openssl, else the openssl.exe bundled with Git."""
    found = shutil.which("openssl")
    if found:
        return found
    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parents[1]
        for rel in ("usr/bin/openssl.exe", "bin/openssl.exe"):
            cand = git_root / rel
            if cand.exists():
                return str(cand)
    return None


def _via_openssl(openssl: str, out_dir: Path, days: int, san: str) -> bool:
    cert = out_dir / "https_cert.pem"
    key = out_dir / "https_key.pem"
    cert_tmp = out_dir / "https_cert.tmp.pem"
    key_tmp = out_dir / "https_key.tmp.pem"
    cfg = out_dir / "san.cnf"
    try:
        cfg.write_text(
            "[req]\n"
            "distinguished_name = dn\n"
            "x509_extensions = v3_ext\n"
            "prompt = no\n"
            "[dn]\n"
            "CN = chuan-os.local\n"
            "[v3_ext]\n"
            f"subjectAltName = {san}\n",
            encoding="utf-8",
        )
        cmd = [
            openssl, "req", "-x509", "-newkey", "rsa:2048",
            "-sha256", "-nodes", "-days", str(days),
            "-keyout", str(key_tmp), "-out", str(cert_tmp),
            "-config", str(cfg),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        cmd2 = [
            openssl, "x509", "-in", str(cert_tmp), "-out", str(cert),
            "-extfile", str(cfg), "-extensions", "v3_ext",
        ]
        subprocess.run(cmd2, check=True, capture_output=True, timeout=60)
        os.replace(key_tmp, key)
        cert_tmp.unlink(missing_ok=True)
        cfg.unlink(missing_ok=True)
        return True
    except (subprocess.SubprocessError, OSError):
        cert_tmp.unlink(missing_ok=True)
        key_tmp.unlink(missing_ok=True)
        cfg.unlink(missing_ok=True)
        return False


def _via_cryptography(out_dir: Path, days: int, san: str) -> bool:
    try:
        import datetime as _dt
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return False
    cert = out_dir / "https_cert.pem"
    key = out_dir / "https_key.pem"
    names = [x509.DNSName("localhost"), x509.DNSName("chuan-os.local")]
    for raw in san.split(","):
        raw = raw.strip()
        if raw.startswith("IP:"):
            names.append(x509.IPAddress(ipaddress.ip_address(raw[3:])))
        elif raw.startswith("DNS:"):
            names.append(x509.DNSName(raw[4:]))
    key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chuan-os.local")])
    now = _dt.datetime.now(_dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key_obj.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + _dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    )
    cert_obj = builder.sign(key_obj, hashes.SHA256())
    cert.write_bytes(cert_obj.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(
        key_obj.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate HTTP gateway self-signed TLS cert")
    parser.add_argument("--dir", default="data", help="output dir (default: data)")
    parser.add_argument("--days", type=int, default=825, help="validity days (default: 825)")
    args = parser.parse_args(argv)

    out_dir = Path(args.dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ips = _lan_ips()
    san = "DNS:localhost" + "".join(f",IP:{ip}" for ip in ips if ip != "127.0.0.1")

    openssl = _find_openssl()
    if openssl and _via_openssl(openssl, out_dir, args.days, san):
        print(f"[OK] openssl generated self-signed cert -> {out_dir / 'https_cert.pem'}")
        return 0
    if _via_cryptography(out_dir, args.days, san):
        print(f"[OK] cryptography generated self-signed cert -> {out_dir / 'https_cert.pem'}")
        return 0

    print(
        "[WARN] no openssl / cryptography available, cert not generated. "
        "The gateway silently degrades to plain HTTP (side-effect fallback).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())