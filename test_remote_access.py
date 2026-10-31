import subprocess
import contextlib
import errno
import io
import runpy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import remote_access
from remote_access import inspected_tunnel_reason, LocalRepairRequired
from service import RecoveryBudget


class RepairClassificationTests(unittest.TestCase):
    def test_script_help_entrypoint_is_read_only(self):
        with patch('sys.argv',['remote_access.py','--help']),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as raised:
            runpy.run_path(remote_access.__file__,run_name='__main__')
        self.assertEqual(raised.exception.code,0)

    def test_named_tunnel_repair_and_transient_spawn_failures_are_classified(self):
        for broken in [True,False]:
            with self.subTest(broken=broken),tempfile.TemporaryDirectory() as directory,patch.object(remote_access,'ROOT',Path(directory)),patch('sys.argv',['remote_access.py','start','--hostname','phone.example','--tunnel-id','11111111-2222-3333-4444-555555555555']),patch('remote_access.preflight',return_value=8766),patch('remote_access.shutil.which',return_value='/fake/cloudflared'),patch('remote_access.named_configuration',side_effect=LocalRepairRequired('fixture') if broken else None,return_value=Path(directory)/'fixture.json'),patch('remote_access.subprocess.Popen',side_effect=OSError(errno.EAGAIN,'fixture')) as spawn:
                with self.assertRaises(LocalRepairRequired if broken else OSError):remote_access.main()
                if broken:spawn.assert_not_called()
    def test_missing_connector_and_inaccessible_binary_require_repair(self):
        for error in [None,OSError(errno.ENOENT,'fixture'),OSError(errno.EACCES,'fixture'),OSError(errno.ENOEXEC,'fixture')]:
            with self.subTest(error=error),tempfile.TemporaryDirectory() as directory,patch.object(remote_access,'ROOT',Path(directory)),patch('sys.argv',['remote_access.py','start']),patch('remote_access.preflight',return_value=8766),patch('remote_access.shutil.which',return_value='/fake/cloudflared' if error else None),patch('remote_access.subprocess.Popen',side_effect=error) as spawn:
                with self.assertRaises(LocalRepairRequired):remote_access.main()
                if error is None:spawn.assert_not_called()

    def test_entrypoint_returns_repair_code_and_budget_pauses_immediately(self):
        with patch('remote_access.main',side_effect=LocalRepairRequired('Repair the local client.')),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as raised:
            remote_access.run()
        self.assertEqual(raised.exception.code,78)
        budget=RecoveryBudget(max_failures=None)
        self.assertEqual(budget.failed(1,raised.exception.code),float('inf'))
        self.assertTrue(budget.blocked)

    def test_transient_resource_errors_retain_network_retry_semantics(self):
        with patch('remote_access.main',side_effect=OSError(errno.EAGAIN,'fixture')),self.assertRaises(SystemExit) as raised:
            remote_access.run()
        self.assertIsInstance(raised.exception.code,str)
        budget=RecoveryBudget(max_failures=None)
        self.assertNotEqual(budget.failed(1,1),float('inf'))


class EdgeDiagnosticTests(unittest.TestCase):
    def test_reports_only_known_certificate_issuer_without_returning_raw_output(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,'subject=/CN=CloudFlare Origin Certificate\nissuer=/O=Zscaler Inc./CN=Zscaler Intermediate Root CA\nPRIVATE_IP',''))
        self.assertEqual(inspected_tunnel_reason(run),'edge_tls_inspection')
        args,kwargs=run.call_args
        self.assertEqual(kwargs['input'],'')
        self.assertEqual(kwargs['timeout'],4)
        self.assertNotIn('PRIVATE_IP',str(args))

    def test_does_not_infer_inspection_from_unrelated_text(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,'subject=/CN=Zscaler example\nissuer=/O=Google Trust Services',''))
        self.assertIsNone(inspected_tunnel_reason(run))

    def test_failed_or_slow_diagnostics_leave_existing_failure_intact(self):
        for error in (OSError('unavailable'),subprocess.TimeoutExpired('openssl',4)):
            self.assertIsNone(inspected_tunnel_reason(Mock(side_effect=error)))


if __name__=='__main__':unittest.main()
