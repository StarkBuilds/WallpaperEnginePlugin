"""
mount.py — Auto-mount NTFS drives via udisksctl (Stage 2).

## What problem does this solve?

Your Wallpaper Engine content lives on a Windows NTFS partition. On Linux,
external/non-root partitions aren't automatically mounted at boot (unlike
Windows, which mounts all its drives automatically). You *could* edit
/etc/fstab to auto-mount it, but that requires root and can break things
if the drive isn't connected.

Instead, we use **udisks2** — a system service (D-Bus daemon) that lets
regular users mount/unmount drives without sudo or fstab edits.

## Key concepts for a Java dev

- **udisks2** — a system daemon (think: a Spring-Boot-style background service)
  that manages storage devices. It listens on D-Bus (Linux's IPC bus, like
  Java RMI but system-wide).

- **udisksctl** — the CLI client that talks to udisks2. Like using `curl` to
  hit a REST API, but it's talking to a local D-Bus service.

- **UUID** — every partition has a unique identifier. Unlike device paths
  (/dev/nvme0n1p3) which can change between boots, UUIDs are burned into
  the partition's metadata and never change. We use UUID to find the right
  drive reliably.

- **lsblk** — lists block devices (disks, partitions). We use its `-J` flag
  for JSON output, which we parse to find the device path for a given UUID.

- **Mount point** — where the filesystem appears in the directory tree.
  udisksctl mounts to /run/media/<username>/<label>/ by default. So a drive
  labeled "Windows" appears at /run/media/arghadeep/Windows/.

## Flow

1. Run `lsblk -J` to get all partitions with their UUIDs and mount status
2. Find the partition matching our configured UUID
3. If already mounted → return the mount point (nothing to do)
4. If not mounted → run `udisksctl mount -b /dev/<device>` to mount it
5. If UUID not found → the drive isn't connected; fail gracefully
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DriveInfo:
    """Information about a discovered partition."""

    device: str
    """Device path like '/dev/nvme0n1p3'."""

    uuid: str
    """Partition UUID."""

    fstype: str
    """Filesystem type (e.g. 'ntfs', 'ext4')."""

    label: str
    """Partition label (e.g. 'Windows', 'New Volume')."""

    mount_point: str | None
    """Where it's currently mounted, or None if not mounted."""

    @property
    def is_mounted(self) -> bool:
        return self.mount_point is not None


class MountError(Exception):
    """Raised when a drive mount operation fails."""

    pass


class DriveNotFoundError(MountError):
    """Raised when no drive matching the configured UUID is found."""

    pass


def _run_lsblk() -> list[DriveInfo]:
    """
    Query lsblk for all partitions and their mount status.

    `lsblk -J` outputs JSON like:
        {
            "blockdevices": [
                {
                    "name": "nvme0n1",
                    "children": [
                        {
                            "name": "nvme0n1p3",
                            "uuid": "165A5F9D5A5F7887",
                            "fstype": "ntfs",
                            "mountpoint": "/run/media/arghadeep/Windows",
                            "label": "Windows"
                        },
                        ...
                    ]
                }
            ]
        }

    We flatten this into a list of DriveInfo objects.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,UUID,FSTYPE,MOUNTPOINT,LABEL", "-J"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise MountError(
            "lsblk not found. It should be installed by default on Arch/CachyOS."
        )
    except subprocess.TimeoutExpired:
        raise MountError("lsblk timed out — is the system under heavy I/O load?")

    if result.returncode != 0:
        raise MountError(f"lsblk failed: {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise MountError(f"Failed to parse lsblk JSON output: {result.stdout[:200]}")

    drives: list[DriveInfo] = []

    def _collect(devices: list[dict]) -> None:
        """Recursively collect partitions from lsblk's nested structure."""
        for dev in devices:
            uuid = dev.get("uuid")
            if uuid:
                drives.append(
                    DriveInfo(
                        device=f"/dev/{dev['name']}",
                        uuid=uuid,
                        fstype=dev.get("fstype", ""),
                        label=dev.get("label", "") or "",
                        mount_point=dev.get("mountpoint"),
                    )
                )
            # Recurse into children (partitions of a disk)
            children = dev.get("children", [])
            if children:
                _collect(children)

    _collect(data.get("blockdevices", []))
    return drives


def find_drive_by_uuid(uuid: str) -> DriveInfo | None:
    """
    Search all block devices for one matching the given UUID.

    UUIDs are case-insensitive on NTFS (Windows stores them uppercase,
    but Linux tools sometimes report differently), so we compare
    case-insensitively.

    Returns None if no matching drive is found (i.e. the drive is
    physically disconnected or the UUID is wrong).
    """
    uuid_upper = uuid.upper()
    for drive in _run_lsblk():
        if drive.uuid.upper() == uuid_upper:
            return drive
    return None


def _parse_mount_point(udisksctl_stdout: str) -> str:
    """
    Extract the mount point from udisksctl's output.

    udisksctl mount prints something like:
        Mounted /dev/nvme0n1p3 at /run/media/arghadeep/Windows

    We parse the "at <path>" part.
    """
    # Look for " at " in the output
    marker = " at "
    idx = udisksctl_stdout.find(marker)
    if idx != -1:
        # Everything after " at " (strip trailing whitespace/period)
        return udisksctl_stdout[idx + len(marker) :].rstrip().rstrip(".")
    return ""


def mount_drive(uuid: str) -> str:
    """
    Ensure the drive with the given UUID is mounted.

    If already mounted, returns the existing mount point.
    If not mounted, mounts it via udisksctl and returns the new mount point.

    This is the main function called by the rest of the application.

    Parameters
    ----------
    uuid : str
        The partition UUID to find and mount (from your TOML config).

    Returns
    -------
    str
        The mount point path (e.g. "/run/media/arghadeep/Windows").

    Raises
    ------
    DriveNotFoundError
        If no partition with this UUID exists (drive not connected).
    MountError
        If the mount operation fails.
    """
    if not uuid:
        logger.debug("No drive UUID configured — skipping mount step.")
        return ""

    logger.info("Checking if drive UUID=%s is mounted...", uuid)

    # Step 1: Find the drive
    drive = find_drive_by_uuid(uuid)

    if drive is None:
        raise DriveNotFoundError(
            f"No drive found with UUID '{uuid}'. "
            f"Is the drive connected? Check with: lsblk -o NAME,UUID,LABEL"
        )

    logger.info(
        "Found drive: %s (label=%r, fstype=%s)",
        drive.device,
        drive.label,
        drive.fstype,
    )

    # Step 2: If already mounted, we're done
    if drive.is_mounted:
        logger.info(
            "Drive is already mounted at: %s",
            drive.mount_point,
        )
        return drive.mount_point

    # Step 3: Mount it via udisksctl
    logger.info("Drive is not mounted — mounting via udisksctl...")

    try:
        result = subprocess.run(
            [
                "udisksctl",
                "mount",
                "-b",              # --block-device
                drive.device,
                # NOTE: We intentionally do NOT pass --no-user-interaction.
                # Internal drives (like an NVMe NTFS partition) require Polkit
                # authorization. Without --no-user-interaction, udisksctl will
                # trigger the native desktop password prompt (KDE's polkit agent)
                # to let the user authorize the mount. This follows Linux
                # community security standards — we don't bypass auth or ask
                # users to install custom polkit rules.
            ],
            capture_output=True,
            text=True,
            timeout=120,  # generous timeout — user may need to type a password
        )
    except FileNotFoundError:
        raise MountError(
            "udisksctl not found. Install udisks2:\n"
            "  sudo pacman -S udisks2"
        )
    except subprocess.TimeoutExpired:
        raise MountError(
            f"udisksctl mount timed out after 120s for {drive.device}. "
            f"Did you dismiss the password prompt? Try again or mount manually:\n"
            f"  udisksctl mount -b {drive.device}"
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()

        # Common error: drive is in use by Windows (hibernation)
        if "hibernate" in stderr.lower() or "windows is hibernated" in stderr.lower():
            raise MountError(
                f"Cannot mount {drive.device}: Windows is hibernated on this drive.\n"
                f"Boot into Windows, disable Fast Startup, and shut down (not restart).\n"
                f"Or mount with: udisksctl mount -b {drive.device} -o ro\n"
                f"Detail: {stderr}"
            )

        raise MountError(
            f"Failed to mount {drive.device}:\n"
            f"  Command: udisksctl mount -b {drive.device}\n"
            f"  Error: {stderr}"
        )

    # Parse the mount point from udisksctl's output
    mount_point = _parse_mount_point(result.stdout)

    if not mount_point:
        # Fallback: re-query lsblk to find where it got mounted
        drive_after = find_drive_by_uuid(uuid)
        if drive_after and drive_after.is_mounted:
            mount_point = drive_after.mount_point
        else:
            raise MountError(
                f"udisksctl reported success but couldn't determine mount point.\n"
                f"stdout: {result.stdout.strip()}"
            )

    logger.info("Drive mounted successfully at: %s", mount_point)
    return mount_point
