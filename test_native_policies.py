import base64
import plistlib
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift tools are required')
class NativePoliciesTests(unittest.TestCase):
    def test_browser_duration_validates_custom_days_and_explicit_never(self):
        source=Path(__file__).parent/'native'/'ControlPolicies.swift'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=root/'main.swift';binary=root/'duration'
            main.write_text('''import Foundation
let policy = try! JSONDecoder().decode(BrowserAccessPolicy.self, from: Data("{\\"default_days\\":7,\\"max_days\\":365}".utf8))
assert(policy.default_days == 7)
for days in ["1", "7", "30", "365"] { assert(policy.duration(days:days, never:false) == Int(days)) }
for days in ["", "0", "-1", "366", "1.5", "abc", "999999999999999999999999999"] { assert(policy.duration(days:days, never:false) == nil) }
assert(policy.duration(days:"invalid", never:true) == 0)
print("Duration checks passed")
''')
            subprocess.run(['xcrun','swiftc',str(source),str(main),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
            self.assertIn('Duration checks passed',result.stdout)

    def test_embedded_local_chat_origin_and_deep_link_policy(self):
        source=Path(__file__).parent/'native'/'ControlPolicies.swift'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=root/'main.swift';binary=root/'navigation'
            main.write_text('''import Foundation
assert(CompanionWebNavigation.isTrusted(CompanionWebNavigation.localChats))
assert(CompanionWebNavigation.isTrusted(CompanionWebNavigation.conversations))
for value in ["https://127.0.0.1:8765/local", "http://127.0.0.1:8766/local", "http://127.0.0.1.evil.test:8765/local", "http://localhost:8765/local", "http://user:secret@127.0.0.1:8765/local", "file:///tmp/local", "javascript:alert(1)"] {
    let url=URL(string:value)!
    assert(!CompanionWebNavigation.isTrusted(url))
    assert(CompanionWebNavigation.embedded(url) == nil)
}
assert(CompanionWebNavigation.isLocalChat(URL(string:"http://127.0.0.1:8765/local?from=source-1")!))
assert(CompanionWebNavigation.isLocalChat(URL(string:"http://127.0.0.1:8765/local/chat-1")!))
assert(!CompanionWebNavigation.isLocalChat(URL(string:"http://127.0.0.1:8765/locality")!))
assert(CompanionWebNavigation.canOpenInBrowser(URL(string:"https://example.com/document")!))
assert(!CompanionWebNavigation.canOpenInBrowser(URL(string:"https://token@example.com")!))
assert(!CompanionWebNavigation.canOpenInBrowser(URL(string:"file:///etc/passwd")!))
assert(!CompanionWebNavigation.canOpenInBrowser(URL(string:"javascript:alert(1)")!))
let source=URL(string:"http://127.0.0.1:8765/local?from=source-1&embedded=0&embedded=2#chat")!
let embedded=CompanionWebNavigation.embedded(source)!
let embeddedItems=URLComponents(url:embedded,resolvingAgainstBaseURL:false)!.queryItems!
assert(embeddedItems.filter{$0.name == "embedded"}.map{$0.value!} == ["1"])
assert(embeddedItems.first{$0.name == "from"}?.value == "source-1")
let browser=CompanionWebNavigation.browserURL(embedded)!
assert(browser.absoluteString == "http://127.0.0.1:8765/local?from=source-1#chat")
print("Native chat navigation checks passed")
''')
            subprocess.run(['xcrun','swiftc',str(source),str(main),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
            self.assertIn('Native chat navigation checks passed',result.stdout)

    def test_timeout_defaults_validation_readback_and_stale_health(self):
        source=Path(__file__).parent/'native'/'ControlPolicies.swift'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=root/'main.swift';binary=root/'policies'
            main.write_text('''import Foundation
let settings=UnattendedSettings(keepWorking:true,batteryMinutes:UnattendedSettings.defaultBattery,adapterMinutes:UnattendedSettings.defaultAdapter)
assert(settings.command != nil)
assert(settings.matches(keepWorking:true,battery:5,adapter:10,cutoff:20,source:"battery",percent:20))
assert(settings.matches(keepWorking:true,battery:5,adapter:10,cutoff:20,source:"adapter",percent:1))
assert(settings.matches(keepWorking:false,battery:5,adapter:10,cutoff:20,source:"battery",percent:19))
assert(settings.matches(keepWorking:false,battery:5,adapter:10,cutoff:20,source:"unknown"))
assert(!settings.matches(keepWorking:true,battery:5,adapter:10,source:"battery",percent:80))
assert(!settings.matches(keepWorking:true,battery:5,adapter:10,cutoff:20,source:"battery",percent:19))
assert(!settings.matches(keepWorking:true,battery:5,adapter:10))
assert(!settings.matches(keepWorking:true,battery:1,adapter:1))
assert(!settings.matches(keepWorking:nil,battery:5,adapter:10))
for minutes in [-1,0,3,999] { assert(UnattendedSettings(keepWorking:false,batteryMinutes:minutes,adapterMinutes:10).command == nil) }
assert(UnattendedSettings(keepWorking:false,batteryMinutes:5,adapterMinutes:10).matches(keepWorking:false,battery:5,adapter:10))
assert(StatusFreshness.isFresh(checkedAt:100,failed:false,now:110))
assert(!StatusFreshness.isFresh(checkedAt:100,failed:true,now:101))
assert(!StatusFreshness.isFresh(checkedAt:100,failed:false,now:121))
assert(!StatusFreshness.isFresh(checkedAt:nil,failed:false,now:100))
assert(!StatusFreshness.isFresh(checkedAt:200,failed:false,now:100))
assert(!StatusFreshness.isFresh(checkedAt:Double.nan,failed:false,now:100))
assert(StatusRetry.delay(failures:1,random:0) == 0.5)
assert(StatusRetry.delay(failures:3,random:1) == 4)
assert(StatusRetry.delay(failures:100,random:1) == 30)
assert(StatusRetry.delay(failures:100,random:0) == 15)
print("INSTALL=" + settings.command!)
print("REMOVE=" + UnattendedSettings(keepWorking:false,batteryMinutes:5,adapterMinutes:10).command!)
print("Native policy checks passed")
''')
            subprocess.run(['xcrun','swiftc',str(source),str(main),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
            self.assertIn('Native policy checks passed',result.stdout)
            commands = dict(line.split('=', 1) for line in result.stdout.splitlines() if line.startswith(('INSTALL=', 'REMOVE=')))
            install = base64.b64decode(re.search(r"printf '%s' '([^']+)'", commands['INSTALL'])[1]).decode()
            remove = base64.b64decode(re.search(r"printf '%s' '([^']+)'", commands['REMOVE'])[1]).decode()
            payloads = re.findall(r"printf '%s' '([^']+)'", install)
            helper = base64.b64decode(payloads[0]).decode()
            definition = plistlib.loads(base64.b64decode(payloads[1]))
            self.assertEqual(definition['ProgramArguments'], ['/Library/PrivilegedHelperTools/me.sagaryadav.desktop-companion.power'])
            self.assertTrue(definition['KeepAlive'])
            self.assertTrue(definition['RunAtLoad'])
            self.assertIn('trap cleanup EXIT', helper)
            self.assertIn('test ! -L /Library/PrivilegedHelperTools/', install)
            self.assertIn('/usr/sbin/chown root:wheel', install)
            self.assertIn('/usr/bin/pmset -b displaysleep 5', install)
            self.assertIn('/usr/bin/pmset -c displaysleep 10', install)
            self.assertIn('/bin/launchctl bootout system/me.sagaryadav.desktop-companion.power', remove)
            self.assertIn('/usr/bin/pmset -a disablesleep 0', remove)
            self.assertIn('/bin/rm -f /Library/PrivilegedHelperTools/', remove)
            self.assertNotIn('/bin/launchctl bootstrap', remove)
