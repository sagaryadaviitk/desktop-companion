from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift tools are required')
class NativePoliciesTests(unittest.TestCase):
    def test_timeout_defaults_validation_readback_and_stale_health(self):
        source=Path(__file__).parent/'native'/'ControlPolicies.swift'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=root/'main.swift';binary=root/'policies'
            main.write_text('''import Foundation
let settings=UnattendedSettings(keepWorking:true,batteryMinutes:UnattendedSettings.defaultBattery,adapterMinutes:UnattendedSettings.defaultAdapter)
assert(settings.command == "/usr/bin/pmset -b displaysleep 5 && /usr/bin/pmset -c displaysleep 10 && /usr/bin/pmset -a disablesleep 1")
assert(settings.matches(keepWorking:true,battery:5,adapter:10))
assert(!settings.matches(keepWorking:true,battery:1,adapter:1))
assert(!settings.matches(keepWorking:nil,battery:5,adapter:10))
for minutes in [-1,0,3,999] { assert(UnattendedSettings(keepWorking:false,batteryMinutes:minutes,adapterMinutes:10).command == nil) }
assert(UnattendedSettings(keepWorking:false,batteryMinutes:5,adapterMinutes:10).command!.hasSuffix("disablesleep 0"))
assert(StatusFreshness.isFresh(checkedAt:100,failed:false,now:110))
assert(!StatusFreshness.isFresh(checkedAt:100,failed:true,now:101))
assert(!StatusFreshness.isFresh(checkedAt:100,failed:false,now:121))
assert(!StatusFreshness.isFresh(checkedAt:nil,failed:false,now:100))
assert(!StatusFreshness.isFresh(checkedAt:200,failed:false,now:100))
assert(!StatusFreshness.isFresh(checkedAt:Double.nan,failed:false,now:100))
print("Native policy checks passed")
''')
            subprocess.run(['xcrun','swiftc',str(source),str(main),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
            self.assertIn('Native policy checks passed',result.stdout)
