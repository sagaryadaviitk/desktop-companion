import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from urllib.error import URLError

import service
from remote_access import monitor_tunnel


class HealthMonitorTests(unittest.TestCase):
    def test_stuck_tunnel_without_edges_recovers_only_after_failure_grace(self):
        process = Mock()
        process.poll.return_value = None
        clock=[0]
        def wait(_):clock[0]+=20
        with patch("remote_access.public_status",return_value=530) as probe:
            with self.assertRaisesRegex(RuntimeError,"No registered tunnel connections"):
                monitor_tunnel(process,"https://phone.example",wait=wait,now=lambda:clock[0])
        self.assertEqual(probe.call_count,16)

    def test_network_failures_trigger_recovery_without_disabling_verification(self):
        process = Mock()
        process.poll.return_value = None
        clock=[0]
        def wait(_):clock[0]+=100
        with patch("remote_access.public_status",side_effect=URLError("TLS verification failed")):
            with self.assertRaisesRegex(RuntimeError,"No registered tunnel connections"):
                monitor_tunnel(process,"https://phone.example",wait=wait,now=lambda:clock[0])

    def test_failed_public_probe_never_discards_registered_edges(self):
        process=Mock();process.poll.side_effect=[None]*40+[0]
        reporter=Mock();reporter.value={'connections':2}
        clock=[0]
        def wait(_):clock[0]+=20
        with patch('remote_access.public_status',return_value=530) as probe:
            monitor_tunnel(process,'https://phone.example',wait=wait,now=lambda:clock[0],reporter=reporter)
        self.assertEqual(probe.call_count,20)
        process.terminate.assert_not_called()

    def test_short_probe_outage_with_no_edges_is_given_time_to_recover(self):
        process=Mock();process.poll.side_effect=[None]*10+[0]
        clock=[0]
        def wait(_):clock[0]+=20
        with patch('remote_access.public_status',return_value=530) as probe:
            monitor_tunnel(process,'https://phone.example',wait=wait,now=lambda:clock[0])
        self.assertEqual(probe.call_count,5)

    def test_a_successful_check_resets_the_failure_counter(self):
        process = Mock()
        process.poll.side_effect = [None]*10+[0]
        with patch("remote_access.public_status",side_effect=[530,530,401,530,530]) as probe:
            monitor_tunnel(process,"https://phone.example",wait=lambda _:None)
        self.assertEqual(probe.call_count,5)

    def test_authentication_bypass_stops_immediately(self):
        process = Mock()
        process.poll.return_value = None
        with patch("remote_access.public_status",return_value=200) as probe:
            with self.assertRaisesRegex(RuntimeError,"bypassed authentication"):
                monitor_tunnel(process,"https://phone.example",wait=lambda _:None)
        self.assertEqual(probe.call_count,1)


class LaunchAgentTests(unittest.TestCase):
    def setUp(self):
        events=patch('service.record_event');self.events=events.start();self.addCleanup(events.stop)

    def test_web_deploy_restarts_only_server_and_preserves_tunnel_and_guard(self):
        server=Mock();server.poll.return_value=None
        tunnel=Mock();awake=Mock();replacement=Mock()
        children={'server':server,'tunnel':tunnel,'awake':awake}
        with patch('service.stop_children') as stop,patch('service.start_server',return_value=replacement),patch('service.write_runtime'):
            service.recover_server(children,0,100,restart=True)
        stop.assert_called_once_with({'server':server})
        self.assertIs(children['server'],replacement)
        self.assertIs(children['tunnel'],tunnel);self.assertIs(children['awake'],awake)
        tunnel.send_signal.assert_not_called();awake.send_signal.assert_not_called()

    def test_crashed_server_recovers_after_backoff_without_restarting_tunnel(self):
        failed=Mock();failed.poll.return_value=1;tunnel=Mock()
        children={'server':failed,'tunnel':tunnel}
        with patch('service.start_server') as start,patch('service.write_runtime'),patch('service.stop_children') as stop:
            retry=service.recover_server(children,0,100)
            self.assertEqual(retry,103);start.assert_not_called()
            service.recover_server(children,retry,102);start.assert_not_called()
            service.recover_server(children,retry,103);start.assert_called_once()
            stop.assert_not_called();self.assertIs(children['tunnel'],tunnel)

    def test_web_restart_request_is_private_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(service,'STATE',Path(directory)):
            service.request_web_restart();path=Path(directory)/'restart-web.request'
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertTrue(service.consume_web_restart());self.assertFalse(service.consume_web_restart())
            target=Path(directory)/'other';target.write_text('Preserve');path.symlink_to(target)
            with self.assertRaises(RuntimeError):service.request_web_restart()
            self.assertFalse(service.consume_web_restart());self.assertEqual(target.read_text(),'Preserve')

    def test_tunnel_failure_keeps_web_and_awake_running_and_retries_after_backoff(self):
        server=Mock();awake=Mock();failed=Mock();failed.poll.return_value=1
        children={'server':server,'awake':awake,'tunnel':failed}
        with patch('service.write_runtime'),patch('service.publish'),patch('service.read_record',return_value={}),patch('service.start_tunnel') as start:
            retry=service.recover_tunnel(children,0,100)
            self.assertEqual(retry,130);self.assertNotIn('tunnel',children)
            service.recover_tunnel(children,retry,129);start.assert_not_called()
            service.recover_tunnel(children,retry,130);start.assert_called_once()
        server.send_signal.assert_not_called();awake.send_signal.assert_not_called()

    def test_network_recovery_is_debounced_and_does_not_restart_while_offline(self):
        recovery=service.NetworkRecovery()
        self.assertFalse(recovery.observe((True,'office'),0))
        self.assertFalse(recovery.observe((False,'offline'),5))
        self.assertFalse(recovery.observe((False,'offline'),50))
        self.assertFalse(recovery.observe((True,'home'),60))
        self.assertFalse(recovery.observe((True,'home'),62))
        self.assertTrue(recovery.observe((True,'home'),63))
        self.assertFalse(recovery.observe((True,'home'),100))

    def test_rapid_network_changes_have_a_reconnect_cooldown(self):
        recovery=service.NetworkRecovery();recovery.observe((True,'one'),0)
        recovery.observe((True,'two'),1);self.assertTrue(recovery.observe((True,'two'),4))
        recovery.observe((True,'three'),5);self.assertFalse(recovery.observe((True,'three'),8))
        self.assertTrue(recovery.observe((True,'three'),34))

    def test_reconnect_only_restarts_tunnel_and_preserves_server_and_guard(self):
        server=Mock();awake=Mock();tunnel=Mock();tunnel.poll.return_value=None
        children={'server':server,'awake':awake,'tunnel':tunnel}
        with patch('service.stop_children') as stop,patch('service.write_runtime'),patch('service.publish'),patch('service.read_record',return_value={}),patch('service.start_tunnel') as start:
            retry=service.recover_tunnel(children,130,100,reconnect=True,reason='manual_restart')
            stop.assert_called_once_with({'tunnel':tunnel});self.assertEqual(retry,103)
            self.assertIs(children['server'],server);self.assertIs(children['awake'],awake)
            service.recover_tunnel(children,retry,103);start.assert_called_once()
        server.send_signal.assert_not_called();awake.send_signal.assert_not_called()

    def test_network_metadata_change_preserves_live_tunnel_instead_of_forcing_a_handshake(self):
        tunnel=Mock(pid=45);tunnel.poll.return_value=None
        children={'tunnel':tunnel,'server':Mock(),'awake':Mock()}
        with patch('service.stop_children') as stop,patch('service.start_tunnel') as start:
            retry=service.recover_tunnel(children,130,100,reconnect=True)
        stop.assert_not_called();start.assert_not_called();self.assertIs(children['tunnel'],tunnel)
        self.assertEqual(retry,130);self.assertEqual(self.events.call_args.args[1],'network_change_preserved')

    def test_network_detector_returns_no_network_details(self):
        raw='IPv4 network interface information\n en0: flags : Reachable\n address: PRIVATE_IP\nNetwork interfaces: en0\n'
        value=service.network_state(Mock(return_value=subprocess.CompletedProcess([],0,raw)))
        self.assertTrue(value[0]);self.assertEqual(len(value[1]),64);self.assertNotIn('PRIVATE_IP',str(value))
        self.assertIsNone(service.network_state(Mock(side_effect=subprocess.TimeoutExpired('scutil',2))))

    def test_restart_request_is_private_consumed_once_and_does_not_accept_symlinks(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(service,'STATE',Path(directory)):
            service.request_tunnel_restart();path=Path(directory)/'restart-tunnel.request'
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertTrue(service.consume_tunnel_restart());self.assertFalse(service.consume_tunnel_restart())
            target=Path(directory)/'other';target.write_text('Preserve');path.symlink_to(target)
            with self.assertRaises(RuntimeError):service.request_tunnel_restart()
            self.assertFalse(service.consume_tunnel_restart());self.assertEqual(target.read_text(),'Preserve')

    def test_running_desktop_app_is_not_reopened(self):
        with patch('service.subprocess.run',return_value=subprocess.CompletedProcess([],0,'/Applications/ChatGPT.app/Contents/MacOS/ChatGPT\n')) as run:
            service.ensure_desktop_app()
        self.assertEqual(run.call_count,1)

    def test_missing_desktop_process_is_opened_using_the_installed_app(self):
        with patch('service.subprocess.run',side_effect=[subprocess.CompletedProcess([],1),subprocess.CompletedProcess([],0)]) as run,patch('service.Path.is_dir',return_value=True):
            service.ensure_desktop_app()
        self.assertEqual(run.call_args.args[0],['/usr/bin/open','-g','-j','/Applications/ChatGPT.app'])

    def test_child_process_cleanup_and_environment_do_not_leak_session_credentials(self):
        with patch.dict("os.environ",{"CF_API_TOKEN":"private-fixture","COMPANION_TOKEN":"private-fixture"}):
            value = service.definition(Path('/test/app'),'/python',Path('/test/user'),'/test/bin/codex')
        self.assertTrue(value['KeepAlive'])
        self.assertFalse(value['AbandonProcessGroup'])
        self.assertEqual(set(value['EnvironmentVariables']),{'HOME','PATH','PYTHONUNBUFFERED'})
        self.assertNotIn('private-fixture',json.dumps(value))
        self.assertEqual(value['Umask'],0o077)

    def test_install_refuses_to_replace_an_unrelated_launchagent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plist = root / 'existing.plist'
            original=plistlib.dumps({'ProgramArguments':['/unrelated/program']})
            plist.write_bytes(original)
            with patch.object(service,'STATE',root/'state'),patch.object(service,'PLIST',plist),patch.object(service,'launchctl') as launchctl:
                with self.assertRaisesRegex(RuntimeError,'unrelated LaunchAgent'):
                    service.install()
            self.assertEqual(plist.read_bytes(),original)
            launchctl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
