<p align="center">
  <a href="https://github.com/brunonlinespace/brunonlinespace/blob/main/linspectacles/installed-software.png">
    <img
      src="https://raw.githubusercontent.com/brunonlinespace/brunonlinespace/main/linspectacles/installed-software.png"
      alt="Installed Software screenshot"
      width="900"
    >
  </a>
</p>

# Installed Software

**Expose. Explore. Explain.**

Installed Software is a read-only, explicit-scan software inventory combining distribution packages, Flatpak applications, Snap packages and AppImages discovered in configured folders.

Current release: **0.0.9**

Repository: <https://github.com/linspectacles/installed-software>

## What Installed Software is

Installed Software answers a focused question: **what software is visible through the supported package/application sources on this system?**

It keeps acquisition and interpretation close to the Linux interfaces actually used by the applet. When a source is unavailable, restricted, or does not report a field, the README does not imply that the applet can manufacture the missing information.

## Highlights

- Distribution-package inventory using the native RPM, dpkg or pacman backend selected for the system.
- Flatpak application inventory when `flatpak` is available.
- Snap package inventory when `snap` is available.
- Recursive AppImage discovery inside user-configured search folders.
- AppImage architecture read from the ELF header where available, with conservative name/version inference from filenames.
- Scanning is explicit rather than hidden background package discovery.
- AppImage search folders are configurable and unreadable/missing folders are skipped.

## Data sources and acquisition

- `/etc/os-release` plus available native package-manager command/database information.
- `rpm -qa ...`, `dpkg-query -W ...`, or `pacman -Q` depending on the detected system.
- `flatpak list --app ...` where available.
- `snap list` where available.
- Configured folders for `.AppImage` discovery.

The applet uses those sources according to the acquisition paths implemented in this release. Optional tools or privileged helpers only affect the views/actions that explicitly use them; they are not treated as silent substitutes for unrelated sources.

## Interface

Installed Software follows the LinSpectacles inventory + **Metadata Details** pattern where applicable: an acquired inventory remains separate from the details for the selected record, and search/filter controls operate on the data the applet has actually collected.

The applet's **Options** surface provides the presentation/source/about controls implemented by this release, together with export controls where present in the source. Applet-specific actions remain explicit rather than being folded into background collection.

## Read-only and scope boundaries

Installed Software inventories software; it does not install, remove, update or modify packages or applications. AppImage discovery is intentionally folder-based because AppImage has no authoritative system-wide installed-package database.

## Running from source

The supplied source is directly runnable on Linux. The Python-side requirements are:

- Python 3.
- PyQt6 for the graphical interface.

Create a local virtual environment if you do not already have the required Python packages in the environment you want to use:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install PyQt6
python3 linspectacles_installed_software/standalone.py
```

Linux commands, procfs/sysfs interfaces, system services, or optional helpers mentioned in **Data sources and acquisition** are applet-specific runtime inputs. A missing optional source is handled by the applet's existing availability/error path; it is not replaced here with an invented dependency or capability.

No RPM installation instructions are included in this README.

## Source layout and LinSpectacles integration

The applet source is contained in:

```text
linspectacles_installed_software/
├── __init__.py
├── applet.json
├── standalone.py
├── README.md
├── LICENSE
└── installed_software/
    ├── __init__.py
    ├── applet.py
    ├── _support.py
    ├── _package_metadata.py
    └── icon.png
```

`applet.json` supplies the LinSpectacles applet identity and entrypoint metadata. `standalone.py` launches the applet directly, while the package's applet factory provides the host-loadable LinSpectacles integration used by the source.

## Configuration

Presentation and inspection preferences are persisted only through the settings mechanisms already implemented by the applet. Persisted UI state does not grant the applet permission to modify the inspected Linux subsystem.

## Scope

Installed Software is intentionally specialist. Related Linux information that belongs to another LinSpectacles subsystem remains outside this applet rather than being inferred or duplicated here.

## License

GNU General Public License v3.0 or later. See [`LICENSE`](LICENSE).

## Links

- LinSpectacles organisation: <https://github.com/linspectacles/>
- Repository: <https://github.com/linspectacles/installed-software>
- Issues: <https://github.com/linspectacles/installed-software/issues>

<p align="center">
  <a href="https://github.com/linspectacles/installed-software">
    <img
      src="installed_software/icon.png"
      alt="Installed Software"
      width="256"
    >
  </a>
</p>
