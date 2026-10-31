"""Power policy checks use fake utilities and never alter host power settings."""
import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import power
import service


class PolicyTests(unittest.TestCase):
    def test_cutoff_boundary_adapter_and_invalid_telemetry(self):
        for state, expected in [
            ({'source': 'adapter', 'battery_percent': 0}, True),
            ({'source': 'adapter', 'battery_percent': None}, True),
            ({'source': 'battery', 'battery_percent': 100}, True),
            ({'source': 'battery', 'battery_percent': 20}, True),
            ({'source': 'battery', 'battery_percent': 19}, False),
            ({'source': 'battery', 'battery_percent': 0}, False),
            ({'source': 'battery', 'battery_percent': -1}, False),
            ({'source': 'battery', 'battery_percent': 101}, False),
            ({'source': 'battery', 'battery_percent': True}, False),
            ({'source': 'battery', 'battery_percent': None}, False),
            ({'source': 'unknown', 'battery_percent': 90}, False),
        ]:
            with self.subTest(state=state):
                self.assertEqual(power.should_keep_awake(state), expected)

    def test_failed_battery_reads_and_foreign_headers_never_request_keep_awake(self):
        for failure in [OSError("unavailable"), subprocess.TimeoutExpired("pmset", 3)]:
            state = power.read_battery_state(Mock(side_effect=failure))
            self.assertFalse(power.should_keep_awake(state))
        for output in ["", "Foreign 'AC Power' header\n 90%; charging;", "Now drawing from 'Battery Power'\n 101%; discharging;"]:
            state = power.read_battery_state(Mock(return_value=subprocess.CompletedProcess([], 0, output, "")))
            self.assertFalse(power.should_keep_awake(state))

    def test_sleep_transition_reaps_an_already_exited_guard(self):
        process = Mock()
        process.poll.return_value = 1
        children = {'awake': process}
        with patch('service.stop_children') as stop, patch('service.write_runtime'):
            service.recover_awake(children, 100, 10, service.RecoveryBudget(), {'source': 'battery', 'battery_percent': 19})
            stop.assert_not_called()
        process.wait.assert_called_once()
        self.assertNotIn('awake', children)

    def test_guard_releases_only_its_own_process_and_resumes_after_charging(self):
        awake, web, tunnel = Mock(), Mock(), Mock()
        awake.poll.return_value = None
        children = {'awake': awake, 'server': web, 'tunnel': tunnel}
        budget = service.RecoveryBudget()
        with patch('service.stop_children') as stop, patch('service.write_runtime'), patch('service.start_awake') as start:
            for state in [{'source': 'battery', 'battery_percent': 19}, {'source': 'unknown'}]:
                self.assertEqual(service.recover_awake(children, 1000, 10, budget, state), 0)
            stop.assert_called_once_with({'awake': awake})
            start.assert_not_called()
            self.assertEqual(children, {'server': web, 'tunnel': tunnel})
            service.recover_awake(children, 0, 11, budget, {'source': 'adapter'})
            start.assert_called_once()
        web.send_signal.assert_not_called()
        tunnel.send_signal.assert_not_called()

    def test_running_supervisor_polls_battery_releases_guard_then_recovers_on_ac(self):
        web, first, second = Mock(), Mock(), Mock()
        for process in (web, first, second): process.poll.return_value = None
        stopping = Mock()
        stopping.is_set.return_value = False
        stopping.wait.side_effect = [False, False, True]
        response = Mock()
        response.__enter__ = Mock(return_value=io.StringIO(json.dumps({'gateway_port': 8766, 'authentication_required': True})))
        response.__exit__ = Mock(return_value=False)
        samples = [{'source': 'battery', 'battery_percent': 20}, {'source': 'battery', 'battery_percent': 19}, {'source': 'adapter'}]
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            fixtures = {
                'STATE': Path(directory), 'CONFIG': Mock(remote_enabled=False),
                'threading.Event': Mock(return_value=stopping), 'signal.signal': Mock(),
                'os.umask': Mock(), 'fcntl.flock': Mock(), 'start_server': Mock(return_value=web),
                'start_awake': Mock(side_effect=[first, second]), 'urlopen': Mock(return_value=response),
                'time.monotonic': Mock(side_effect=[0, 0, 6, 12]), 'read_battery_state': Mock(side_effect=samples),
                'write_runtime': Mock(), 'record_event': Mock(), 'consume_web_restart': Mock(return_value=False),
                'network_state': Mock(return_value=None), 'stop_children': Mock(),
            }
            for name, value in fixtures.items(): stack.enter_context(patch('service.' + name, value))
            service.supervise()
            self.assertEqual(fixtures['read_battery_state'].call_count, 3)
            self.assertEqual(fixtures['start_awake'].call_count, 2)
            self.assertEqual(fixtures['stop_children'].call_args_list[0].args[0], {'awake': first})
            self.assertEqual(fixtures['stop_children'].call_args_list[1].args[0], {'server': web, 'awake': second})
        web.send_signal.assert_not_called()

    def test_only_fresh_root_owned_heartbeat_verifies_a_managed_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heartbeat'
            path.write_text('100 1\n')
            path.chmod(0o644)
            metadata = path.stat()
            root_owned = Mock(st_uid=0, st_mode=metadata.st_mode)
            with patch.object(Path, 'lstat', return_value=root_owned):
                self.assertEqual(power.managed_cutoff(path, now=130), 20)
                for now in [99, 146]:
                    self.assertIsNone(power.managed_cutoff(path, now=now))
                for contents in ['100 2', 'not a heartbeat', '100 1 extra']:
                    path.write_text(contents)
                    self.assertIsNone(power.managed_cutoff(path, now=130))
            path.write_text('100 1')
            with patch.object(Path, 'lstat', return_value=Mock(st_uid=502, st_mode=metadata.st_mode)):
                self.assertIsNone(power.managed_cutoff(path, now=130))
            with patch.object(Path, 'lstat', return_value=Mock(st_uid=0, st_mode=0o100666)):
                self.assertIsNone(power.managed_cutoff(path, now=130))
            link = Path(directory) / 'link'
            link.symlink_to(path)
            self.assertIsNone(power.managed_cutoff(link, now=130))
            self.assertIsNone(power.managed_cutoff(Path(directory) / 'missing', now=130))


class HelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).parent / 'native' / 'ControlPolicies.swift').read_text()
        cls.script = re.search(r'static let script = #"""\n(.*?)\n"""#', source, re.S)[1] + '\n'

    def test_privileged_helper_decisions_match_service_policy(self):
        for source in ['adapter', 'battery', 'unknown']:
            for percent in ['', '0', '19', '20', '21', '100', '101', '-1', 'NaN', '9999', '1;echo bad']:
                with self.subTest(source=source, percent=percent):
                    result = subprocess.run(['/bin/sh', '-s', '--', '--decide', source, percent], input=self.script,
                                            text=True, capture_output=True, check=True, timeout=5)
                    expected = power.should_keep_awake({'source': source, 'battery_percent': int(percent) if percent.isdigit() else None})
                    self.assertEqual(result.stdout.strip(), '1' if expected else '0')

    def test_helper_transitions_low_battery_reconnect_and_read_failure_then_cleans_up(self):
        samples = ["Now drawing from 'Battery Power'\n -InternalBattery-0\t20%; discharging;",
                   "Now drawing from 'Battery Power'\n -InternalBattery-0\t19%; discharging;",
                   "Now drawing from 'AC Power'\n -InternalBattery-0\t1%; charging;", 'unreadable']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'state').write_text(json.dumps({'samples': samples, 'index': 0, 'disabled': 0, 'writes': [], 'heartbeats': []}))
            fake = root / 'utility'
            fake.write_text('#!' + sys.executable + '\n' + '''import json, pathlib, sys
root=pathlib.Path(__file__).parent
state=json.loads((root/'state').read_text())
args=sys.argv[1:]
code=0
if args == ['-g','batt']:
    print(state['samples'][state['index']])
elif args == ['-g']:
    print('System-wide power settings:\\n SleepDisabled '+str(state['disabled']))
elif args[:2] == ['-a','disablesleep']:
    state['disabled']=int(args[2]);state['writes'].append(int(args[2]))
elif args == ['15']:
    state['heartbeats'].append((root/'heartbeat').read_text().split()[1])
    state['index']+=1
    if state['index']>=len(state['samples']):code=1
else:raise ValueError(args)
(root/'state').write_text(json.dumps(state))
sys.exit(code)
''')
            fake.chmod(0o755)
            script = self.script.replace('/usr/bin/pmset', str(fake)).replace('/bin/sleep', str(fake)).replace('/var/run/me.sagaryadav.desktop-companion.power.status', str(root / 'heartbeat'))
            result = subprocess.run(['/bin/sh'], input=script, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stderr)
            state = json.loads((root / 'state').read_text())
            self.assertEqual(state['heartbeats'], ['1', '0', '1', '0'])
            self.assertEqual(state['writes'], [1, 0, 1, 0, 0])
            self.assertFalse((root / 'heartbeat').exists())
            self.assertFalse(list(root.glob('heartbeat.*')))


if __name__ == '__main__':
    unittest.main()
