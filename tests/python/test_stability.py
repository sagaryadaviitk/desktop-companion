"""Recovery isolation and explicit installation. All processes/files are test fixtures."""
import contextlib
import errno
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

from desktop_companion import control
from desktop_companion import manage
from desktop_companion import service
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        jitter=patch('desktop_companion.service.random.random',return_value=1.0)
        jitter.start();self.addCleanup(jitter.stop)

    def test_retry_jitter_has_a_floor_and_preserves_the_failure_budget(self):
        for random_value,expected in [(0,1.5),(.5,2.25),(1,3)]:
            budget=service.RecoveryBudget(random_value=lambda:random_value)
            self.assertEqual(budget.failed(100),100+expected)
            self.assertEqual(budget.failures,1)
        budget=service.RecoveryBudget(random_value=lambda:0)
        self.assertEqual(budget.failed(100,78),float('inf'))
        self.assertTrue(budget.blocked)

    def test_backoff_opens_after_five_rapid_failures(self):
        budget=service.RecoveryBudget();now=100
        for delay in (3,6,12,24):
            budget.started(now)
            self.assertEqual(budget.failed(now+1),now+1+delay)
            self.assertFalse(budget.blocked)
            now+=delay+1
        self.assertEqual(budget.failed(now),float('inf'))
        self.assertTrue(budget.blocked)

    def test_permanent_exit_stops_retries_immediately(self):
        for code in (2,78):
            budget=service.RecoveryBudget()
            self.assertEqual(budget.failed(100,code),float('inf'))
            self.assertTrue(budget.blocked)

    def test_a_minute_of_runtime_resets_only_that_budget(self):
        budget=service.RecoveryBudget();other=service.RecoveryBudget()
        for _ in range(3):budget.failed(10);other.failed(10)
        budget.started(100)
        self.assertEqual(budget.failed(160,1),163)
        self.assertEqual(budget.failures,1);self.assertEqual(other.failures,3)

    def test_guard_crash_never_stops_web_or_tunnel(self):
        web=Mock();tunnel=Mock();failed=Mock();failed.poll.return_value=1
        replacement=Mock();replacement.poll.return_value=None
        children={'server':web,'tunnel':tunnel,'awake':failed}
        budget=service.RecoveryBudget()
        with patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.start_awake',return_value=replacement) as start,patch('desktop_companion.service.stop_children') as stop:
            retry=service.recover_awake(children,0,100,budget)
            self.assertEqual(retry,103);start.assert_not_called()
            service.recover_awake(children,retry,103,budget)
            start.assert_called_once();stop.assert_not_called()
        self.assertIs(children['server'],web);self.assertIs(children['tunnel'],tunnel)
        self.assertIs(children['awake'],replacement)
        web.send_signal.assert_not_called();tunnel.send_signal.assert_not_called()

    def test_repeated_server_spawn_failures_are_bounded(self):
        budget=service.RecoveryBudget();children={'tunnel':Mock(),'awake':Mock()};retry=0;now=100
        with patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.start_server',side_effect=OSError('fixture')) as start,patch('desktop_companion.service.stop_children') as stop:
            for expected_delay in (3,6,12,24):
                retry=service.recover_server(children,retry,now,budget=budget)
                self.assertEqual(retry,now+expected_delay);now=retry
            retry=service.recover_server(children,retry,now,budget=budget)
            self.assertTrue(budget.blocked)
            service.recover_server(children,retry,10000,budget=budget)
            self.assertEqual(start.call_count,5);stop.assert_not_called()
        self.assertEqual(set(children),{'tunnel','awake'})

    def test_explicit_web_restart_resets_only_web_budget(self):
        budget=service.RecoveryBudget();budget.failed(100,2)
        children={'tunnel':Mock(),'awake':Mock()};replacement=Mock()
        with patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.start_server',return_value=replacement),patch('desktop_companion.service.stop_children') as stop:
            service.recover_server(children,float('inf'),200,restart=True,budget=budget)
        self.assertFalse(budget.blocked);self.assertEqual(budget.failures,0)
        self.assertIs(children['server'],replacement);stop.assert_not_called()

    def test_awake_spawn_failure_is_contained_on_first_start(self):
        budget=service.RecoveryBudget();children={'server':Mock(),'tunnel':Mock()}
        with patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.start_awake',side_effect=OSError('fixture')),patch('desktop_companion.service.stop_children') as stop:
            self.assertEqual(service.recover_awake(children,0,100,budget),103)
        self.assertEqual(set(children),{'server','tunnel'});stop.assert_not_called()

    def test_tunnel_spawn_failure_is_contained(self):
        budget=service.RecoveryBudget(initial_delay=30,max_delay=300)
        children={'server':Mock(),'awake':Mock()}
        with patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.publish') as publish,patch('desktop_companion.service.start_tunnel',side_effect=OSError('fixture')),patch('desktop_companion.service.stop_children') as stop:
            self.assertEqual(service.recover_tunnel(children,0,100,budget=budget),130)
        self.assertEqual(set(children),{'server','awake'});stop.assert_not_called()
        self.assertEqual(publish.call_args.args[1],'retrying')

    def test_network_change_cannot_reactivate_permanently_failed_controller(self):
        budget=service.RecoveryBudget(initial_delay=30,max_delay=300);budget.failed(100,2)
        with patch('desktop_companion.service.start_tunnel') as start,patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.publish'):
            service.recover_tunnel({},float('inf'),200,reconnect=True,budget=budget)
        start.assert_not_called();self.assertTrue(budget.blocked)

    def test_explicit_tunnel_reconnect_resets_paused_controller(self):
        budget=service.RecoveryBudget(initial_delay=30,max_delay=300);budget.failed(100,2)
        children={'server':Mock(),'awake':Mock()}
        with patch('desktop_companion.service.start_tunnel') as start,patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.publish'),patch('desktop_companion.service.read_record',return_value={}),patch('desktop_companion.service.record_event'):
            retry=service.recover_tunnel(children,float('inf'),200,reconnect=True,reason='manual_restart',budget=budget)
            self.assertEqual(retry,203);start.assert_not_called()
            service.recover_tunnel(children,retry,203,budget=budget);start.assert_called_once()
        self.assertFalse(budget.blocked)

    def test_network_outage_does_not_exhaust_tunnel_recovery(self):
        budget=service.RecoveryBudget(initial_delay=30,max_delay=300,max_failures=None)
        now=100
        for expected in (30,60,120,240,300,300,300,300):
            budget.started(now)
            retry=budget.failed(now+1,1)
            self.assertEqual(retry,now+1+expected);self.assertFalse(budget.blocked)
            now=retry
        # Large failure counts stay numerically bounded as well.
        budget.failures=1000000
        self.assertEqual(budget.failed(now,1),now+300)

    def test_missing_executable_stops_only_the_affected_component_immediately(self):
        for name in ('server','awake','tunnel'):
            with self.subTest(component=name):
                children={'other':Mock()};budget=service.RecoveryBudget()
                with patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.stop_children') as stop:
                    retry=service.recover_process(children,name,Mock(side_effect=OSError(errno.ENOENT,'fixture')),0,100,budget)
                self.assertEqual(retry,float('inf'));self.assertTrue(budget.blocked)
                self.assertEqual(set(children),{'other'});stop.assert_not_called()

    def test_permanent_controller_exit_is_reported_and_not_retried(self):
        failed=Mock();failed.poll.return_value=2
        budget=service.RecoveryBudget(initial_delay=30,max_delay=300)
        children={'tunnel':failed,'server':Mock(),'awake':Mock()}
        with patch('desktop_companion.service.start_tunnel') as start,patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.publish') as publish,patch('desktop_companion.service.read_record',return_value={}),patch('desktop_companion.service.record_event'):
            retry=service.recover_tunnel(children,0,100,budget=budget)
            self.assertEqual(retry,float('inf'));start.assert_not_called()
        self.assertTrue(budget.blocked);self.assertEqual(publish.call_args.args[1],'failed')
        self.assertEqual(publish.call_args.kwargs['reason'],'controller_repair_required')


class InstallationTests(unittest.TestCase):
    def definition(self,root,python):
        return service.definition(root,python,root,'/fixture/codex')

    def test_launch_logs_survive_a_removed_source_directory(self):
        home=Path('/fixture/home');root=home/'Developer/deleted-project'
        value=service.definition(root,'/fixture/python',home,'/fixture/codex')
        self.assertEqual(value['WorkingDirectory'],str(home))
        for field in ('StandardOutPath','StandardErrorPath'):
            self.assertEqual(Path(value[field]).parent,home/'Library/Logs/Desktop Companion')
            self.assertNotIn(str(root),value[field])

    def test_install_rejects_symlinked_log_directory_without_stopping_existing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'keep';target.mkdir();logs=root/'logs';logs.symlink_to(target)
            definition={'StandardOutPath':str(logs/'service.log'),'StandardErrorPath':str(logs/'service-error.log')}
            with patch.object(service,'STATE',root/'state'),patch.object(service,'PLIST',root/'agents/job.plist'),patch('desktop_companion.service.definition',return_value=definition),patch('desktop_companion.service.launchctl') as launch:
                with self.assertRaisesRegex(RuntimeError,'log location is a symlink'):
                    service.install(background_install_approved=True)
            launch.assert_not_called();self.assertEqual(list(target.iterdir()),[])

    def test_missing_entrypoint_exits_successfully_without_running_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            result=subprocess.run(self.definition(root,sys.executable)['ProgramArguments'],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0)
        self.assertIn('approved repair required',result.stderr)

    def test_guard_passes_paths_with_spaces_and_shell_characters_literally(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'app space $(touch UNEXPECTED)';root.mkdir()
            (root/'service.py').write_text('fixture, never executed')
            executable=root/'python fixture'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            executable.chmod(0o700)
            value=self.definition(root,str(executable))
            result=subprocess.run(value['ProgramArguments'],cwd=directory,capture_output=True,text=True,timeout=5)
            self.assertFalse((Path(directory)/'UNEXPECTED').exists())
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout.splitlines(),['-u',str(root/'service.py'),'run'])
        self.assertTrue(value['RunAtLoad']);self.assertEqual(value['KeepAlive'],{'SuccessfulExit':False})
        self.assertEqual(value['ThrottleInterval'],60)

    def test_missing_python_does_not_create_a_restart_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'service.py').write_text('fixture')
            result=subprocess.run(self.definition(root,str(root/'missing-python'))['ProgramArguments'],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0)

    def test_install_without_explicit_opt_in_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plist=root/'LaunchAgents/job.plist';state=root/'state'
            with patch.object(service,'STATE',state),patch.object(service,'PLIST',plist),patch('desktop_companion.service.definition') as definition,patch('desktop_companion.service.launchctl') as launch:
                with self.assertRaisesRegex(RuntimeError,'Background installation is explicit'):service.install()
            self.assertFalse(state.exists());self.assertFalse(plist.exists())
            definition.assert_not_called();launch.assert_not_called()

    def test_native_start_never_silently_installs_a_missing_job(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(service,'PLIST',Path(directory)/'missing.plist'),patch('desktop_companion.service.launchctl',return_value=subprocess.CompletedProcess([],1,'')),patch('desktop_companion.service.install') as install:
            result=control.perform('start')
        self.assertFalse(result['ok']);self.assertIn('explicitly enable',result['message'])
        install.assert_not_called()

    def run_setup(self,install):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);arguments=['manage.py','setup','--without-app']+(['--install-service'] if install else [])
            with patch.object(manage,'ROOT',root),patch.object(sys,'argv',arguments),patch('desktop_companion.manage.doctor',return_value=[{'name':'fixture','ok':True,'detail':''}]),patch('desktop_companion.manage.save'),patch('desktop_companion.manage.subprocess.run') as run,contextlib.redirect_stdout(io.StringIO()):
                manage.main()
            return root,run.call_args_list

    def test_default_preparation_does_not_install_background_service(self):
        _,calls=self.run_setup(False);self.assertEqual(calls,[])

    def test_setup_opt_in_explicitly_requests_installation(self):
        root,calls=self.run_setup(True)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].args[0],[sys.executable,str(root/'service.py'),'install','--background-install-approved'])

    def isolated_checkout(self, root, with_package=True):
        source = Path(__file__).resolve().parents[2]
        shutil.copy(source / 'service.py', root / 'service.py')
        if with_package:
            shutil.copytree(source / 'desktop_companion', root / 'desktop_companion',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

    def run_isolated_service(self, root, *args):
        env = dict(os.environ, HOME=str(root), CODEX_HOME=str(root / 'codex-home'), PYTHONPATH='')
        return subprocess.run([sys.executable, str(root / 'service.py'), *args],
                              cwd=root, env=env, capture_output=True, text=True, timeout=5)

    def test_missing_package_and_dependency_stop_automatic_startup(self):
        for missing in ('package', 'dependency', 'syntax'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.isolated_checkout(root, with_package=missing != 'package')
                if missing == 'dependency':
                    (root / 'desktop_companion' / 'power.py').unlink()
                elif missing == 'syntax':
                    (root / 'desktop_companion' / 'power.py').write_text('invalid python syntax !!!')
                result = self.run_isolated_service(root, 'run')
                self.assertEqual(result.returncode, 0)
                self.assertIn('Automatic startup stopped', result.stderr)
                self.assertFalse((root / '.state').exists())
                # Only launchd's run command gets the successful-exit repair guard.
                interactive = self.run_isolated_service(root, 'status')
                self.assertNotEqual(interactive.returncode, 0)

    def test_invalid_configuration_stops_automatic_startup_without_running_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.isolated_checkout(root)
            (root / 'config.local.json').write_text('{invalid json')
            result = self.run_isolated_service(root, 'run')
            self.assertFalse((root / '.state').exists())
        self.assertEqual(result.returncode, 0)
        self.assertIn('configuration is unavailable', result.stderr)

    def test_supervisor_initial_guard_failure_does_not_relaunch_chatgpt_or_stop_web(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);stop=Mock();stop.is_set.return_value=False;stop.wait.side_effect=[False,True]
            web=Mock();web.poll.return_value=None
            response=Mock();response.__enter__=Mock(return_value=io.StringIO(json.dumps({'gateway_port':8766,'authentication_required':True})));response.__exit__=Mock(return_value=False)
            config=Mock(remote_enabled=False)
            with patch.object(service,'STATE',root),patch.object(service,'CONFIG',config),patch('desktop_companion.service.threading.Event',return_value=stop),patch('desktop_companion.service.signal.signal'),patch('desktop_companion.service.os.umask'),patch('desktop_companion.service.fcntl.flock'),patch('desktop_companion.service.start_server',return_value=web),patch('desktop_companion.service.start_awake',side_effect=OSError('fixture')),patch('desktop_companion.service.urlopen',return_value=response),patch('desktop_companion.service.write_runtime'),patch('desktop_companion.service.record_event'),patch('desktop_companion.service.consume_web_restart',return_value=False),patch('desktop_companion.service.network_state',return_value=None),patch('desktop_companion.service.subprocess.run') as run,patch('desktop_companion.service.stop_children') as cleanup:
                service.supervise()
            run.assert_not_called()
            cleanup.assert_called_once_with({'server':web})
            web.send_signal.assert_not_called()


if __name__=='__main__':unittest.main()
