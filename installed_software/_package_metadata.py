"""Read-only, on-demand metadata retrieval for an existing software inventory row.

Only consulted after explicit row selection.  Never invoked during the inventory
scan, never runs package executables, never uses a shell or a network query.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path


# These package-manager commands inspect *installed/local* records only.
_TIMEOUT_SECONDS = 12


def _run_local(argv):
    if not shutil.which(argv[0]):
        raise RuntimeError(f"{argv[0]} is not installed")
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, errors="replace",
            check=False, timeout=_TIMEOUT_SECONDS, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{argv[0]} metadata query timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"{argv[0]} metadata query failed: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"{argv[0]}: {message}")
    return result.stdout


def _safe_identifier(value):
    """Prevent an inventory string becoming an option or unintended path."""
    value = str(value or "")
    if not value or value.startswith("-") or any(c in value for c in "\x00\n\r"):
        raise RuntimeError("Package identifier is invalid")
    return value


def _colon_fields(text, *, rpm=False):
    """Parse local rpm -qi and pacman -Qi labels, preserving multiline values."""
    fields = []
    rpm_description = False
    for line in text.splitlines():
        if rpm_description:
            label, value = fields[-1]
            fields[-1] = (label, value + "\n" + line)
            continue
        match = re.match(r"^([A-Za-z][\w /()+.-]{0,70}?)\s*:\s*(.*)$", line)
        if match:
            label, value = match.group(1).strip(), match.group(2)
            fields.append((label, value))
            # rpm -qi's Description is its final field and may contain
            # unindented text with colons. Preserve all of it unchanged.
            if rpm and label == "Description":
                rpm_description = True
        elif fields:
            label, value = fields[-1]
            fields[-1] = (label, value + "\n" + line)
    return [(key, value.rstrip()) for key, value in fields if value.strip()]


def _dpkg_fields(text):
    """Parse dpkg's Debian-control paragraphs without losing folded fields."""
    fields = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and fields:
            key, value = fields[-1]
            fields[-1] = (key, value + "\n" + line[1:])
            continue
        if ":" in line and line[:1] not in (" ", "\t"):
            key, value = line.split(":", 1)
            if key and " " not in key:
                fields.append((key, value.lstrip()))
                continue
        if fields:
            key, value = fields[-1]
            fields[-1] = (key, value + "\n" + line)
    return [(key, value.rstrip()) for key, value in fields if value.strip()]


def _checked_identity(fields, record, keys):
    values = {key: value for key, value in fields}
    for field_name, record_name in keys:
        wanted = str(record.get(record_name, "") or "")
        if wanted not in ("", "-") and values.get(field_name) != wanted:
            raise RuntimeError(f"Package metadata no longer matches the scanned {record_name}; run Scan Again")


def _rpm_metadata(record):
    name = _safe_identifier(record["name"])
    version = _safe_identifier(record["version"])
    architecture = _safe_identifier(record["architecture"])
    # Using full NEVRA-style package specification prevents ambiguity for
    # multiple installed kernel versions or multilib packages.
    package = f"{name}-{version}.{architecture}"
    fields = _colon_fields(_run_local(["rpm", "-qi", "--", package]), rpm=True)
    _checked_identity(fields, record, (("Name", "name"), ("Architecture", "architecture")))
    values = dict(fields)
    if values.get("Version", "") + "-" + values.get("Release", "") != version:
        raise RuntimeError("RPM package version changed since the scan; run Scan Again")
    for flag, heading in (("--requires", "Requires"), ("--provides", "Provides")):
        output = _run_local(["rpm", "-q", flag, "--", package]).strip()
        if output:
            fields.append((heading, output))
    return fields


def _dpkg_metadata(record):
    name = _safe_identifier(record["name"])
    arch = str(record.get("architecture", "") or "")
    if arch not in ("", "-", "all") and ":" not in name:
        name = f"{name}:{_safe_identifier(arch)}"
    fields = _dpkg_fields(_run_local(["dpkg-query", "-s", "--", name]))
    # The Package field may omit the architecture qualifier.
    values = dict(fields)
    if values.get("Version") != str(record.get("version", "")):
        raise RuntimeError("dpkg package version changed since the scan; run Scan Again")
    if arch not in ("", "-") and values.get("Architecture") != arch:
        raise RuntimeError("dpkg package architecture changed since the scan; run Scan Again")
    if values.get("Status") != "install ok installed":
        raise RuntimeError("dpkg package installation status changed; run Scan Again")
    return fields


def _pacman_metadata(record):
    name = _safe_identifier(record["name"])
    fields = _colon_fields(_run_local(["pacman", "-Qi", "--", name]))
    _checked_identity(fields, record, (("Name", "name"), ("Version", "version")))
    return fields


def _flatpak_metadata(record):
    app_id = _safe_identifier(record.get("application_id", ""))
    cmd = ["flatpak", "info"]
    installation = str(record.get("installation", "") or "")
    if installation.lower() == "user":
        cmd.append("--user")
    elif installation.lower() == "system":
        cmd.append("--system")
    elif installation not in ("", "-"):
        cmd.append(f"--installation={_safe_identifier(installation)}")
    arch = str(record.get("architecture", "") or "")
    if arch not in ("", "-"):
        cmd.append(f"--arch={_safe_identifier(arch)}")
    cmd.extend(["--", app_id])
    branch = str(record.get("branch", "") or "")
    if branch not in ("", "-"):
        cmd.append(_safe_identifier(branch))
    output = _run_local(cmd).strip()
    fields = _colon_fields(output)
    return fields or [("Flatpak Information", output)] if output else []


def _snap_metadata(record):
    """Read installed snap metadata without asking the store/snapd for updates."""
    name = _safe_identifier(record["name"])
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise RuntimeError("Snap name is invalid")
    metadata_path = Path("/snap") / name / "current" / "meta" / "snap.yaml"
    try:
        # This is the existing local snap manifest, not a remote store lookup.
        with metadata_path.open("r", encoding="utf-8", errors="replace") as stream:
            contents = stream.read(262145)
    except OSError as exc:
        raise RuntimeError(f"Local snap metadata unavailable: {exc}") from exc
    if len(contents) > 262144:
        raise RuntimeError("Local snap metadata exceeds 256 KiB")
    return [("Installed snap manifest (snap.yaml)", contents.rstrip())]


def lookup_package_metadata(record):
    """Return (source label, extra metadata fields) for a selected inventory row.

    Failures are displayed as an unavailable reason, never a misleadingly
    successful lookup. The caller caches the result for this inventory scan.
    """
    category = record.get("category")
    source = record.get("source")
    if category == "AppImage":
        return "AppImage", []
    try:
        if category == "System Package" and source == "RPM":
            return "RPM", _rpm_metadata(record)
        if category == "System Package" and source == "dpkg":
            return "dpkg", _dpkg_metadata(record)
        if category == "System Package" and source == "pacman":
            return "pacman", _pacman_metadata(record)
        if category == "Flatpak":
            return "Flatpak", _flatpak_metadata(record)
        if category == "Snap":
            return "Snap", _snap_metadata(record)
        return "Package", [("Detail lookup", "No local metadata backend for this entry")]
    except (RuntimeError, KeyError, ValueError) as exc:
        return str(source or category or "Package"), [("Detail lookup", f"Unavailable: {exc}")]
