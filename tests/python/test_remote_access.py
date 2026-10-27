import subprocess
import unittest
from unittest.mock import Mock

from desktop_companion.remote_access import inspected_tunnel_reason


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
