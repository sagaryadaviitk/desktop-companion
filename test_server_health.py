"""Health-based recovery is bounded and cannot replay a command."""
import io
import contextlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from companion import CommandJournal, CompanionServer
import service


class ServerHealthTests(unittest.TestCase):
    def test_unknown_intent_is_not_replayed_after_journal_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'commands.sqlite'
            journal=CommandJournal(path)
            def interrupted():raise SystemExit('simulated interruption after durable intent')
            with self.assertRaises(SystemExit):journal.execute('fixture',{},interrupted)
            replacement=CommandJournal(path)
            action=Mock()
            self.assertEqual(replacement.execute('fixture',{},action)['state'],'unknown')
            action.assert_not_called()

    def test_http_health_reports_command_stall_and_recovers_without_waiting_on_lock(self):
        now=[0]
        with tempfile.TemporaryDirectory() as directory:
            journal=CommandJournal(Path(directory)/'commands.sqlite',clock=lambda:now[0])
            server=CompanionServer(('127.0.0.1',0),None,None,journal)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                for active,clock,status in [(None,0,200),(0,119,200),(0,120,503),(None,121,200)]:
                    journal.active_since=active;now[0]=clock
                    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                    connection.request('GET','/healthz');response=connection.getresponse()
                    value=json.loads(response.read());connection.close()
                    self.assertEqual(response.status,status)
                    self.assertEqual(value['ok'],status==200)
                    self.assertIsInstance(value['server_pid'],int)
            finally:server.shutdown();server.server_close();worker.join(timeout=2)

    def test_journal_health_checks_do_not_wait_for_its_active_command(self):
        now = [0]
        entered, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            journal = CommandJournal(Path(directory)/'journal.sqlite', clock=lambda:now[0])
            def action(): entered.set();release.wait(3);return {'state':'sent'}
            worker = threading.Thread(target=lambda:journal.execute('fixture', {}, action))
            worker.start();self.assertTrue(entered.wait(1))
            try:
                self.assertTrue(journal.healthy())
                now[0] = 120
                self.assertFalse(journal.healthy())
            finally: release.set();worker.join(timeout=2)
            self.assertTrue(journal.healthy())
            self.assertEqual(journal.lookup('fixture')['state'], 'sent')

    def test_health_probe_checks_both_listeners_and_expected_child(self):
        def response(port, *, pid=42, ok=True):
            handle=Mock()
            handle.__enter__=Mock(return_value=io.StringIO(json.dumps({'ok':ok,'server_pid':pid,'gateway_version':1,'authentication_required':port==8766})))
            handle.__exit__=Mock(return_value=False)
            return handle
        with patch('service.urlopen', side_effect=[response(8765),response(8766)]) as get:
            self.assertTrue(service.server_healthy(42))
            self.assertEqual(get.call_count,2)
            self.assertTrue(all(call.kwargs['timeout']==2 for call in get.call_args_list))
        for failed in [response(8765,pid=99),response(8765,ok=False),OSError('fixture')]:
            with patch('service.urlopen',side_effect=[failed]):self.assertFalse(service.server_healthy(42))

    def test_watchdog_requires_consecutive_failures_and_resets_for_new_child(self):
        child=Mock(pid=42);child.poll.return_value=None
        probe=Mock(side_effect=[False,False,True,False,False,False])
        watch=service.ServerHealthMonitor(probe=probe)
        self.assertFalse(watch.unhealthy(child,0))
        for now in [10,20,30,40,50]:self.assertFalse(watch.unhealthy(child,now))
        self.assertTrue(watch.unhealthy(child,60))
        self.assertEqual(probe.call_count,6)
        child.pid=43
        self.assertFalse(watch.unhealthy(child,61))
        self.assertFalse(watch.unhealthy(None,62))
        child.poll.return_value=1
        self.assertFalse(watch.unhealthy(child,70))

    def test_health_recovery_preserves_tunnel_and_guard_and_uses_failure_budget(self):
        old=Mock();old.poll.return_value=None
        tunnel,awake=Mock(),Mock()
        children={'server':old,'tunnel':tunnel,'awake':awake}
        budget=service.RecoveryBudget(random_value=lambda:1);budget.started(0)
        with patch('service.stop_children') as stop,patch('service.write_runtime'),patch('service.start_server') as start:
            retry=service.recover_server(children,0,30,budget=budget,unhealthy=True)
            self.assertEqual(retry,33)
            stop.assert_called_once_with({'server':old});start.assert_not_called()
            self.assertEqual(children,{'tunnel':tunnel,'awake':awake})
            service.recover_server(children,retry,33,budget=budget)
            start.assert_called_once()
            self.assertEqual(budget.failures,1)
        tunnel.send_signal.assert_not_called();awake.send_signal.assert_not_called()

    def test_supervisor_recovers_unhealthy_web_without_restarting_other_components(self):
        old,new,awake=Mock(pid=42),Mock(pid=43),Mock()
        old.poll.return_value=new.poll.return_value=awake.poll.return_value=None
        stopping=Mock();stopping.is_set.return_value=False
        stopping.wait.side_effect=[False]*5+[True]
        response=Mock()
        response.__enter__=Mock(return_value=io.StringIO(json.dumps({'gateway_port':8766,'authentication_required':True})))
        response.__exit__=Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory,contextlib.ExitStack() as stack:
            fixtures={'STATE':Path(directory),'CONFIG':Mock(remote_enabled=False),
                      'threading.Event':Mock(return_value=stopping),'signal.signal':Mock(),'os.umask':Mock(),
                      'start_server':Mock(side_effect=[old,new]),'start_awake':Mock(return_value=awake),
                      'urlopen':Mock(return_value=response),'time.monotonic':Mock(side_effect=[0,0,10,20,30,40,41,44]),
                      'read_battery_state':Mock(return_value={'source':'adapter'}),'server_healthy':Mock(return_value=False),
                      'write_runtime':Mock(),'record_event':Mock(),'consume_web_restart':Mock(return_value=False),
                      'network_state':Mock(return_value=None),'stop_children':Mock()}
            for name,value in fixtures.items():stack.enter_context(patch('service.'+name,value))
            service.supervise()
            self.assertEqual(fixtures['start_server'].call_count,2)
            fixtures['start_awake'].assert_called_once()
            self.assertEqual(fixtures['server_healthy'].call_count,3)
            self.assertEqual(fixtures['stop_children'].call_args_list[0].args[0],{'server':old})


if __name__ == '__main__': unittest.main()
