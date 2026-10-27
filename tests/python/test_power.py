import json
import plistlib
import subprocess
import unittest
from unittest.mock import Mock,patch

from desktop_companion import power
def result(stdout="",stderr="",returncode=0):
    return subprocess.CompletedProcess([],returncode,stdout,stderr)


class PowerStateTests(unittest.TestCase):
    def reader(self,source="Battery Power",percent=63,lid=False,disabled=False,timeout=1):
        return Mock(side_effect=[
            result(f"Now drawing from '{source}'\n -InternalBattery-0 (id=1)\t{percent}%; discharging; 2:00 remaining present: true\n"),
            result(plistlib.dumps([{"AppleClamshellState":lid,"SleepDisabled":disabled,"PRIVATE_DEVICE_FIELD":"DO_NOT_EXPOSE"}])),
            result(stderr="sysadminctl[1234] screenLock delay is immediate\n"),
            result(f"Battery Power:\n displaysleep {timeout}\nAC Power:\n displaysleep {timeout}\n")])

    def test_battery_lock_lid_and_sleep_state_use_an_allowlist(self):
        runner=self.reader()
        value=power.read_power_state(runner)
        self.assertEqual(value["source"],"battery")
        self.assertEqual(value["battery_percent"],63)
        self.assertIs(value['charging'],False)
        self.assertIs(value["lid_closed"],False)
        self.assertIs(value["closed_lid_enabled"],False)
        self.assertIs(value["lock_immediate"],True)
        self.assertEqual(value["display_sleep_battery_minutes"],1)
        self.assertEqual(value["display_sleep_adapter_minutes"],1)
        self.assertNotIn("DO_NOT_EXPOSE",json.dumps(value))
        self.assertNotIn("PRIVATE_DEVICE_FIELD",json.dumps(value))

    def test_closed_lid_and_enabled_sleep_override_are_separate_from_power_source(self):
        value=power.read_power_state(self.reader(source="AC Power",percent=0,lid=True,disabled=True))
        self.assertEqual(value["source"],"adapter")
        self.assertEqual(value["battery_percent"],0)
        self.assertIs(value["lid_closed"],True)
        self.assertIs(value["closed_lid_enabled"],True)

    def test_failed_or_unrecognized_hardware_reads_stay_unknown(self):
        runner=Mock(side_effect=[result(returncode=1),result(b"<?xml broken"),result(returncode=1),result(returncode=1)])
        value=power.read_power_state(runner)
        for field in ["battery_percent","charging","lid_closed","closed_lid_enabled","lock_immediate","display_sleep_battery_minutes"]:self.assertIsNone(value[field])
        self.assertEqual(value["source"],"unknown")

    def test_adapter_presence_does_not_mean_the_battery_is_charging(self):
        for status,expected in [('charging',True),('finishing charge',True),('not charging',False),('charged',False),('discharging',False),('unrecognized',None)]:
            runner=self.reader(source='AC Power');runner.side_effect=[result("Now drawing from 'AC Power'\n -InternalBattery-0\t83%; "+status+"; present: true"),result(plistlib.dumps([{}])),result(returncode=1),result(returncode=1)]
            self.assertIs(power.read_power_state(runner)['charging'],expected,status)

    def test_delayed_password_lock_is_reported_without_changing_it(self):
        # Use a separate fixed sequence so status parsing cannot silently assume immediate lock.
        runner=Mock(side_effect=[result("Now drawing from 'Battery Power'"),result(plistlib.dumps([{}])),result(stderr="screenLock delay is 30 seconds\n"),result("Battery Power:\n displaysleep 0\nAC Power:\n displaysleep 10\n")])
        value=power.read_power_state(runner)
        self.assertIs(value["lock_immediate"],False)
        self.assertEqual(value["display_sleep_battery_minutes"],0)  # Zero means never, not immediate.
        self.assertEqual(value["display_sleep_adapter_minutes"],10)
        self.assertTrue(all("status" in c.args[0] or "-g" in c.args[0] or c.args[0][0].endswith("ioreg") for c in runner.call_args_list))

    def test_cached_snapshots_do_not_share_mutable_caller_state(self):
        with patch.object(power,"_cached",None),patch.object(power,"_cached_at",0),patch.object(power.time,"monotonic",return_value=10),patch.object(power,"read_power_state",return_value={"source":"battery"}) as read:
            value=power.cached_power_state();value["source"]="changed"
            self.assertEqual(power.cached_power_state()["source"],"battery")
        self.assertEqual(read.call_count,1)


class GuardTests(unittest.TestCase):
    def test_guard_covers_battery_without_asserting_display_or_user_activity(self):
        flags=power.AWAKE_COMMAND[1]
        self.assertIn("i",flags);self.assertIn("s",flags)
        self.assertNotIn("d",flags);self.assertNotIn("u",flags)
        runner=Mock(side_effect=[result("123\n"),result("/usr/bin/caffeinate -is\n")])
        self.assertEqual(power.read_guard(42,runner),{"running":True,"battery_covered":True})

    def test_legacy_charger_only_guard_is_not_reported_as_battery_ready(self):
        runner=Mock(side_effect=[result("123\n"),result("/usr/bin/caffeinate -s\n")])
        self.assertEqual(power.read_guard(42,runner),{"running":True,"battery_covered":False})

    def test_dead_or_reused_child_pid_is_not_reported_as_a_guard(self):
        runner=Mock(side_effect=[result("123\n"),result("/usr/bin/other -is\n")])
        self.assertEqual(power.read_guard(42,runner),{"running":False,"battery_covered":False})


if __name__=="__main__":unittest.main()
