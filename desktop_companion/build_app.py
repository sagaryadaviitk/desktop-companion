#!/usr/bin/env python3
"""Build and register the local Spotlight-launchable Mac control app."""

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from desktop_companion.configuration import load


from desktop_companion import ROOT
BUNDLE_ID = "me.sagaryadav.desktopcompanion.control"


def check_bundle(path):
    """Refuse to replace an unrelated app or a linked installation path."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Use a regular Desktop Companion app folder; linked or unrelated paths are left untouched.")
    try:
        info = plistlib.loads((path / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise ValueError("The app's identity could not be verified. It was left untouched.") from error
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise ValueError("An unrelated app already has this name. It was left untouched.")


def install_bundle(stage, target):
    """Copy first, then replace using only renames on the destination volume."""
    stage, target = Path(stage), Path(target)
    backup = target.parent / ".Desktop Companion.previous"
    check_bundle(stage)
    for existing in (target, backup):
        if existing.exists() or existing.is_symlink():
            check_bundle(existing)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".desktop-companion-install-", dir=target.parent) as temporary:
        candidate = Path(temporary) / target.name
        # A partial copy stays in the temporary folder, away from the working app.
        shutil.copytree(stage, candidate, symlinks=True)
        check_bundle(candidate)
        had_target = target.exists() or target.is_symlink()
        if had_target:
            check_bundle(target)
            if backup.exists() or backup.is_symlink():
                check_bundle(backup)
                shutil.rmtree(backup)
            target.rename(backup)
        try:
            candidate.rename(target)
        except BaseException:
            if had_target:
                if target.exists() or target.is_symlink():
                    raise RuntimeError("App installation was interrupted. The previous app is preserved at " + str(backup))
                try:
                    backup.rename(target)
                except OSError as error:
                    raise RuntimeError("App installation stopped. Restore the previous app from " + str(backup)) from error
            raise
    return backup if had_target else None


def main():
    target = Path.home()/"Applications"/"Desktop Companion.app"
    if target.exists() or target.is_symlink():
        check_bundle(target)
    stage = ROOT/".state"/"native-build"/"Desktop Companion.app"
    if stage.exists():
        shutil.rmtree(stage)
    contents = stage/"Contents"
    executable = contents/"MacOS"/"DesktopCompanion"
    executable.parent.mkdir(parents=True)
    resources = contents/"Resources"
    resources.mkdir()
    module_cache = ROOT/".state"/"swift-cache"
    subprocess.run(["/usr/bin/xcrun","swiftc","-O","-swift-version","5","-parse-as-library","-module-cache-path",str(module_cache),str(ROOT/"native"/"CompanionApp.swift"),str(ROOT/'native'/'Appearance.swift'),str(ROOT/'native'/'SystemMap.swift'),str(ROOT/'native'/'ControlPolicies.swift'),"-o",str(executable)],check=True)
    executable.chmod(0o755)
    iconset = ROOT/".state"/"native-build"/"AppIcon.iconset"
    subprocess.run(["/usr/bin/xcrun","swift","-module-cache-path",str(module_cache),str(ROOT/"native"/"Icon.swift"),str(iconset)],check=True)
    subprocess.run(["/usr/bin/iconutil","-c","icns",str(iconset),"-o",str(resources/"AppIcon.icns")],check=True)
    info = {"CFBundleIdentifier":BUNDLE_ID,"CFBundleDisplayName":"Desktop Companion","CFBundleName":"Desktop Companion","CFBundleExecutable":"DesktopCompanion","CFBundlePackageType":"APPL","CFBundleShortVersionString":"1.0","CFBundleVersion":"1","LSMinimumSystemVersion":"13.0","NSHighResolutionCapable":True,"CompanionRoot":str(ROOT),"CompanionPython":sys.executable,"NSHumanReadableCopyright":"Local desktop companion"}
    info["CFBundleIconFile"] = "AppIcon"
    configuration=load(ROOT)
    info['CompanionDesktopApp']=str(configuration.app_path())
    info['CompanionCLIPath']=str(Path(configuration.codex_path()).parent) if configuration.codex_path() else ''
    info["NSAppTransportSecurity"] = {"NSAllowsLocalNetworking":True}
    (contents/"Info.plist").write_bytes(plistlib.dumps(info))
    subprocess.run(["/usr/bin/codesign","--force","--sign","-",str(stage)],check=True,capture_output=True)
    install_bundle(stage, target)
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister","-f",str(target)],check=True,capture_output=True)
    subprocess.run(["/usr/bin/mdimport",str(target)],capture_output=True)
    print("Installed: "+str(target))


if __name__ == "__main__":
    main()
