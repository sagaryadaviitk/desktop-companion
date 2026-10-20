import copy
import json
import unittest
from approvals import (COMMAND,FILES,PERMISSIONS,MCP,INPUT,ApprovalChanged,
                       approval_views,response_for,pending_requests,request_key)
from companion import redact


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.request={'id':17,'method':COMMAND,'params':{'threadId':'chat','turnId':'turn','itemId':'item','command':'pwd','cwd':'/project','reason':'Check the project folder'}}
        self.state={'id':'chat','requests':[self.request],'turns':[]}

    def view(self):return approval_views(self.state,'owner',redact)[0]
    def respond(self,choice='allow',answers=None,view=None):
        v=view or self.view()
        return response_for(self.state,'owner',v['id'],v['revision'],choice,answers or {},redact)

    def test_command_displays_scope_and_exact_owner_reply_without_exposing_raw_params(self):
        v=self.view();self.assertEqual(v['details'][0]['text'],'pwd');self.assertFalse(v['desktop_only'])
        method,payload=self.respond()
        self.assertEqual(method,'thread-follower-command-approval-decision')
        self.assertEqual(payload,{'conversationId':'chat','requestId':17,'decision':'accept'})
        self.assertNotIn('owner',json.dumps(v));self.assertNotIn('params',v)
        self.assertEqual(self.respond('session')[1]['decision'],'acceptForSession')
        self.assertEqual(self.respond('cancel')[1]['decision'],'cancel')

    def test_integer_string_ids_and_completed_requests_are_distinct(self):
        self.assertNotEqual(request_key(17),request_key('17'))
        self.request['completed']=True;self.assertEqual(approval_views(self.state,'owner'),[])

    def test_revision_covers_owner_turn_content_and_file_context(self):
        for change in ('command','turnId','threadId'):
            v=self.view();old=copy.deepcopy(self.request);self.request['params'][change]='changed'
            with self.assertRaises(ApprovalChanged):self.respond(view=v)
            self.request.clear();self.request.update(old)
        v=self.view()
        with self.assertRaises(ApprovalChanged):response_for(self.state,'new-owner',v['id'],v['revision'],'allow',{},redact)
        self.state['requests']=[]
        with self.assertRaises(ApprovalChanged):self.respond(view=v)

    def test_hidden_or_truncated_command_can_only_be_denied(self):
        for command in ['echo sk-'+'a'*30,'echo '+'x'*33000,None]:
            self.request['params']['command']=command;v=self.view()
            self.assertTrue(v['desktop_only']);self.assertNotIn('allow',[c['id'] for c in v['choices']])
            with self.assertRaises(ApprovalChanged):self.respond()
            self.assertEqual(self.respond('decline')[1]['decision'],'decline')
        self.assertNotIn('sk-'+'a'*30,json.dumps(v))

    def test_available_decisions_are_not_broadened(self):
        self.request['params']['availableDecisions']=['decline','unknown-decision']
        self.assertEqual([c['id'] for c in self.view()['choices']],['decline'])
        with self.assertRaises(ApprovalChanged):self.respond('session')

    def test_network_approval_shows_host_without_displaying_proxy_command(self):
        self.request['params']['networkApprovalContext']={'host':'example.com','protocol':'https','port':443}
        self.request['params']['command']='INTERNAL_PROXY_COMMAND'
        v=self.view();self.assertEqual(v['kind'],'network');self.assertNotIn('INTERNAL_PROXY_COMMAND',json.dumps(v))

    def test_file_approval_requires_matching_full_patch_and_tracks_changes(self):
        self.request['method']=FILES;self.request['params'].pop('command')
        self.assertTrue(self.view()['desktop_only'])
        item={'type':'patch','callId':'item','changes':{'app.py':{'type':'update','unified_diff':'-old\n+new'}}}
        self.state['turns']=[{'turnId':'turn','items':[item]}]
        v=self.view();self.assertFalse(v['desktop_only']);self.assertIn('+new',v['details'][-1]['text'])
        self.assertEqual(self.respond()[0],'thread-follower-file-approval-decision')
        item['changes']['app.py']['unified_diff']='newer change'
        with self.assertRaises(ApprovalChanged):self.respond(view=v)

    def test_permissions_response_uses_only_requested_profile_not_phone_payload(self):
        permissions={'fileSystem':{'entries':[{'path':{'type':'path','path':'/one/file'},'access':'read'}]},'network':None}
        self.request.update(method=PERMISSIONS);self.request['params']['permissions']=permissions
        self.assertEqual(self.respond()[1]['response'],{'permissions':permissions,'scope':'turn'})
        self.assertEqual(self.respond('session')[1]['response']['scope'],'session')
        self.assertEqual(self.respond('decline')[1]['response'],{'permissions':{},'scope':'turn'})
        with self.assertRaises(ApprovalChanged):self.respond(answers={'permissions':{'network':True}})

    def mcp(self,schema=None,meta=None,mode='form'):
        self.request.update(method=MCP)
        self.request['params'].update(serverName='test-app',mode=mode,message='Allow this app action?',requestedSchema=schema or {'type':'object','properties':{}},_meta=meta or {})

    def test_app_confirmation_once_and_explicit_conversation_scope(self):
        self.mcp(meta={'codex_approval_kind':'mcp_tool_call','connector_id':'calendar','tool_name':'create_event','tool_params':{'title':'Meeting'},'persist':['session','always']})
        self.assertEqual(self.respond()[1]['response'],{'action':'accept','content':None})
        self.assertEqual(self.respond('session')[1]['response']['_meta'],{'persist':'session'})
        self.assertNotIn('always',[c['id'] for c in self.view()['choices']])
        self.assertNotIn('_meta',json.dumps(self.view()))

    def test_computer_use_uses_conversation_scope_and_disclosure(self):
        self.mcp(meta={'codex_approval_kind':'mcp_tool_call','connector_id':'computer-use','tool_name':'get_app','tool_params':{'app':'Notes'},'persist':['session','always']})
        self.assertIn('screenshots',self.view()['note'])
        self.assertEqual([c['id'] for c in self.view()['choices']],['session','decline','cancel'])
        self.assertEqual(self.respond('session')[1]['response']['_meta'],{'persist':'session'})

    def test_flat_app_form_validates_types_enums_required_fields_and_bounds(self):
        self.mcp({'type':'object','properties':{'name':{'type':'string','minLength':2,'maxLength':8},'count':{'type':'integer','minimum':1,'maximum':5},'yes':{'type':'boolean'},'kind':{'type':'string','enum':['one','two']}},'required':['name','count','yes','kind']})
        answer={'name':'Test','count':2,'yes':False,'kind':'one'}
        self.assertEqual(self.respond(answers=answer)[1]['response']['content'],answer)
        for bad in [{},dict(answer,count=True),dict(answer,count=6),dict(answer,name='a'),dict(answer,yes='true'),dict(answer,kind='three'),dict(answer,other='injection')]:
            with self.assertRaises(ApprovalChanged):self.respond(answers=bad)
        self.assertEqual(self.respond('decline')[1]['response']['content'],None)

    def test_secure_unsupported_and_execution_bound_requests_cannot_be_approved(self):
        cases=[('url',{},None),('openai/userVerification',{},None),('openai/form',{},None),('form',{'codex_approval_kind':'browser_auth'},None),('form',{'codex_approval_kind':'mcp_tool_call','tool_params':{'plan_token':'PRIVATE_PLAN_TOKEN','confirmation_summary':'private'}},None),('form',{}, {'type':'object','properties':{'password':{'type':'string'}}}),('form',{}, {'type':'object','properties':{'nested':{'type':'object'}}})]
        for mode,meta,schema in cases:
            self.mcp(schema,meta,mode);self.request['params']['challenge']='PRIVATE_CHALLENGE'
            v=self.view();self.assertTrue(v['desktop_only'],(mode,meta,schema))
            self.assertNotIn('PRIVATE_',json.dumps(v))
            with self.assertRaises(ApprovalChanged):self.respond()

    def test_native_input_reply_routes_to_rpc_not_a_followup_message(self):
        self.request.update(method=INPUT);self.request['params']['questions']=[{'id':'q','question':'Which project?','options':[{'label':'One'},{'label':'Two'}]}]
        self.assertEqual(self.respond('reply',{'q':'One'})[1]['response'],{'answers':{'q':{'answers':['One']}}})
        self.request['params']['questions'][0]['isSecret']=True;self.assertTrue(self.view()['desktop_only'])

    def test_unrelated_tools_and_other_threads_are_never_exposed(self):
        self.request['params']['threadId']='another-chat';self.assertEqual(approval_views(self.state,'owner'),[])
        self.state['requests']=[{'id':1,'method':'item/tool/call','params':{'arguments':'PRIVATE'}}]
        self.assertEqual(pending_requests(self.state),[])
        for value in [None,{},[None,{'method':COMMAND,'params':None}]]:
            self.state['requests']=value;self.assertEqual(approval_views(self.state,'owner'),[])


if __name__=='__main__':unittest.main()
