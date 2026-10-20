"""Expose an allowlisted local verification record, never unlock or authorize the Mac."""

import json
from pathlib import Path
import plistlib
import subprocess
import time


def locked_use_versions(home=None, installed=None):
    home=Path(home) if home else Path.home()
    installer=home/'.codex/computer-use/Codex Computer Use.app/Contents/SharedSupport/Codex Computer Use Installer.app'
    installed=Path(installed) if installed else Path('/Library/Security/SecurityAgentPlugins/CodexComputerUseAuthorizationPlugin.bundle')
    bundled=installer/'Contents/Resources/CodexComputerUseAuthorizationPlugin.bundle'
    def version(bundle):
        try:
            info=plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
            build=info.get('CFBundleVersion');label=info.get('CFBundleShortVersionString')
            if not isinstance(build,str) or not build.isdigit():return None,None
            return int(build),label if isinstance(label,str) and len(label)<100 else build
        except (OSError,ValueError,TypeError,plistlib.InvalidFileException):return None,None
    current,current_label=version(installed);available,available_label=version(bundled)
    return {'installed_version':current_label,'available_version':available_label,
            'update_available':available is not None and (current is None or available>current)}


def update_locked_use(home=None, runner=subprocess.run):
    """Only called by the native user's button; the signed installer owns authentication."""
    home=Path(home) if home else Path.home()
    bundle=home/'.codex/computer-use/Codex Computer Use.app/Contents/SharedSupport/Codex Computer Use Installer.app'
    # codesign interprets a requirement without '=' as a path to a requirement file.
    requirement='=anchor apple generic and certificate leaf[subject.OU] = "2DC432GLL2" and identifier "com.openai.sky.CUAService.AuthorizationPluginInstaller"'
    verified=runner(['/usr/bin/codesign','--verify','--deep','--strict','-R',requirement,str(bundle)],capture_output=True,timeout=10)
    if verified.returncode!=0:raise RuntimeError('The OpenAI installer signature could not be verified. Update ChatGPT before trying again.')
    executable=bundle/'Contents/MacOS/Codex Computer Use Installer'
    result=runner([str(executable),'install'],capture_output=True,text=True,timeout=120)
    if result.returncode!=0:raise RuntimeError('The Locked Use installer did not complete. Complete its macOS authentication prompt on your Mac.')
    # Installation is not proof of a successful locked-screen Computer Use turn.
    if locked_use_versions(home)['update_available']:
        raise RuntimeError('The installed Locked Use version is still older. Open ChatGPT Settings → Computer Use and update Locked use there.')
    return {'ok':True,'message':'Locked Use installer completed. Close the lid and test a native app from your phone to verify access.'}


def computer_use_snapshot(path):
    result={'state':'not_tested','checked_at':None,'locked_use_installed':None,
            'lid_closed':None,'screen_locked':None,'stale':False,**locked_use_versions()}
    try:
        path=Path(path)
        if path.stat().st_size>4096:return result
        value=json.loads(path.read_text())
        if not isinstance(value,dict) or value.get('state') not in ('passed','blocked'):
            return result
        checked=value.get('checked_at')
        if isinstance(checked,bool) or not isinstance(checked,(int,float)) or checked>time.time()+60:
            return result
        result.update(state=value['state'],checked_at=checked,stale=time.time()-checked>600)
        for name in ('locked_use_installed','lid_closed','screen_locked'):
            if type(value.get(name)) is bool:result[name]=value[name]
    except (OSError,ValueError,TypeError):pass
    return result
