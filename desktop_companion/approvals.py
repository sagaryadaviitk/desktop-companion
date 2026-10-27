"""Bounded views of live desktop requests; no permission policy or history writes.

Only choices constructed here from a fresh owner snapshot can be submitted. Raw
request metadata, verification challenges and execution tokens never reach HTTP.
"""
import hashlib
import json
import math

COMMAND = 'item/commandExecution/requestApproval'
FILES = 'item/fileChange/requestApproval'
PERMISSIONS = 'item/permissions/requestApproval'
MCP = 'mcpServer/elicitation/request'
INPUT = 'item/tool/requestUserInput'
METHODS = {COMMAND:'thread-follower-command-approval-decision',
           FILES:'thread-follower-file-approval-decision',
           PERMISSIONS:'thread-follower-permissions-request-approval-response',
           MCP:'thread-follower-submit-mcp-server-elicitation-response',
           INPUT:'thread-follower-submit-user-input'}


class ApprovalChanged(Exception):
    """No response was sent; refresh and review the current request."""


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def request_key(request_id):
    # JSON-RPC integer 1 and string "1" identify different requests.
    return digest([type(request_id).__name__,request_id])


def pending_requests(state):
    requests=state.get('requests',[])
    if not isinstance(requests,list):return []
    return [r for r in requests if isinstance(r,dict) and r.get('completed') is not True and
            isinstance(r.get('params'),dict) and
            (r.get('method') in METHODS or r.get('method')=='item/tool/requestOptionPicker')]


def file_context(state,params):
    turns=state.get('turns',[])
    history=state.get('turnHistory') or {}
    if history.get('kind')=='canonical':turns=list(history.get('history',{}).get('entitiesByKey',{}).values())
    for turn in turns:
        if not isinstance(turn,dict) or turn.get('turnId',turn.get('id'))!=params.get('turnId'):continue
        for item in turn.get('items',[]):
            if isinstance(item,dict) and item.get('callId',item.get('id'))==params.get('itemId') and item.get('type') in ('patch','fileChange'):
                return item.get('changes')
    return None


def simple_fields(schema):
    """Support the flat MCP form contract; unfamiliar or secret forms stay on Mac."""
    if not isinstance(schema,dict) or schema.get('type')!='object' or set(schema)-{'type','properties','required','additionalProperties','title','description'}:return None
    properties=schema.get('properties');required=schema.get('required',[])
    if not isinstance(properties,dict) or len(properties)>20 or not isinstance(required,list) or any(k not in properties for k in required):return None
    fields=[]
    for name,s in properties.items():
        if not isinstance(name,str) or not 1<=len(name)<=100 or not isinstance(s,dict):return None
        if set(s)-{'type','title','description','enum','enumNames','default','minLength','maxLength','minimum','maximum'}:return None
        if s.get('type') not in ('string','boolean','integer','number'):return None
        if any(word in name.lower() for word in ('password','secret','token','credential','otp')):return None
        if 'enum' in s and (not isinstance(s['enum'],list) or not 1<=len(s['enum'])<=30 or any(not isinstance(v,str) or len(v)>500 for v in s['enum']) or s['type']!='string'):return None
        for limit in ('minLength','maxLength','minimum','maximum'):
            if limit in s and (type(s[limit]) not in (int,float) or not math.isfinite(s[limit])):return None
        fields.append({'id':name,'label':s.get('title') or name,'description':s.get('description') or '',
                       'type':s['type'],'required':name in required,
                       **{k:s[k] for k in ('enum','minLength','maxLength','minimum','maximum') if k in s}})
    return fields


def build_request(request,state,owner,redact=lambda s:s):
    params=request.get('params');rid=request.get('id');method=request.get('method')
    if not isinstance(params,dict) or type(rid) not in (str,int) or isinstance(rid,str) and not 1<=len(rid)<=500:return None
    if params.get('threadId')!=state.get('id'):return None
    context=file_context(state,params) if method==FILES else None
    revision=digest([owner,request,context])
    view={'id':request_key(rid),'revision':revision,'kind':'desktop','title':'Desktop request',
          'message':'','details':[],'choices':[],'fields':[], 'desktop_only':True,
          'note':'Open this chat on your Mac to complete this request.'}
    operations={};complete=True
    def text(value):
        nonlocal complete
        raw=value if isinstance(value,str) else json.dumps(value,indent=2,ensure_ascii=False)
        clean=redact(raw)
        if clean!=raw or len(clean)>32000:complete=False
        return clean[:32000]
    def detail(label,value):
        if value is not None:view['details'].append({'label':label,'text':text(value)})
    def choice(key,label,payload,scope='',danger=False):
        operations[key]=payload
        view['choices'].append({'id':key,'label':label,'scope':scope,'danger':danger})
    def decision_choices(defaults):
        offered=params.get('availableDecisions',defaults)
        if not isinstance(offered,list):return
        known={'accept':('allow','Allow once','This request only'),
               'acceptForSession':('session','Allow for this session','Future matching requests in this session'),
               'decline':('decline','Decline','The assistant can continue without this action'),
               'cancel':('cancel','Stop turn','Decline and interrupt the current turn')}
        for decision in offered:
            if not isinstance(decision,str) or decision not in known:continue
            key,label,scope=known[decision]
            if decision.startswith('accept') and not complete:continue
            choice(key,label,decision,scope,decision=='cancel')
    view['message']=text(params.get('reason') or params.get('message') or params.get('description') or '')
    if method==COMMAND:
        view.update(kind='command',title='Approve command')
        network=params.get('networkApprovalContext')
        if network:
            view.update(kind='network',title='Approve network access');detail('Network request',network)
        else:detail('Command' if params.get('kind','command')=='command' else 'Input to running command',params.get('command'))
        detail('Working folder',params.get('cwd'));detail('Environment',params.get('environmentId'))
        detail('Proposed command rule',params.get('proposedExecpolicyAmendment'))
        detail('Proposed network rules',params.get('proposedNetworkPolicyAmendments'))
        if not network and not params.get('command'):complete=False
        decision_choices(['accept','acceptForSession','decline','cancel'])
    elif method==FILES:
        view.update(kind='files',title='Approve file changes')
        detail('Requested write root',params.get('grantRoot'));detail('Proposed changes',context)
        if not context:complete=False
        decision_choices(['accept','acceptForSession','decline','cancel'])
    elif method==PERMISSIONS:
        view.update(kind='permissions',title='Approve extra permissions')
        permissions=params.get('permissions');detail('Requested permissions',permissions);detail('Working folder',params.get('cwd'))
        # Copy only the exact requested profile, never a browser-supplied profile.
        if isinstance(permissions,dict) and permissions and not set(permissions)-{'network','fileSystem'} and complete:
            choice('allow','Allow for this turn',{'permissions':permissions,'scope':'turn'},'Only the permissions shown, until this turn ends')
            choice('session','Allow for this session',{'permissions':permissions,'scope':'session'},'Only the permissions shown, for this session')
        choice('decline','Decline',{'permissions':{},'scope':'turn'},'Grant no extra permissions')
    elif method==MCP:
        view.update(kind='app',title='App approval · '+text(params.get('serverName','App')))
        mode=params.get('mode');meta=params.get('_meta') or {}
        fields=simple_fields(params.get('requestedSchema')) if mode=='form' else None
        kind=meta.get('codex_approval_kind') if isinstance(meta,dict) else None
        if isinstance(meta,dict) and kind=='mcp_tool_call':
            detail('App',meta.get('connector_name') or meta.get('connector_id'));detail('Tool',meta.get('tool_name'))
            tool_params=meta.get('tool_params')
            # Execution-bound summaries contain private plan tokens. Keep these on Mac.
            if isinstance(tool_params,dict) and ('plan_token' in tool_params or 'confirmation_summary' in tool_params):fields=None
            else:detail('Action details',tool_params)
            detail('Action summary',meta.get('tool_params_display'))
        if isinstance(meta,dict):
            detail('Risk',meta.get('risk_level'));detail('Additional context',meta.get('subtitle'))
            if meta.get('origin'):detail('Website',meta['origin'])
        if kind not in (None,'mcp_tool_call'):fields=None
        if mode=='openai/userVerification':view['title']='Verify on your Mac'
        if mode=='url' or kind in ('browser_auth','browser_email_otp','tool_suggestion') or isinstance(meta,dict) and ('openai/confirmation' in meta or '_codex_apps' in meta):fields=None
        if isinstance(meta,dict) and meta.get('approveDisabled') is True:fields=None
        if fields is not None and complete:
            view['fields']=[{k:text(v) if k in ('label','description') else [text(item) for item in v] if k=='enum' else v for k,v in f.items()} for f in fields]
            connector=str(meta.get('connector_id','')) if isinstance(meta,dict) else ''
            computer=connector=='computer-use' or connector.startswith('computer-use-')
            modes=meta.get('persist',[]) if isinstance(meta,dict) else []
            if isinstance(modes,str):modes=[modes]
            if complete and not computer:choice('allow','Approve once',{'action':'accept','content':None},'This request only; no persistent approval')
            if complete and not fields and isinstance(modes,list) and 'session' in modes:
                choice('session','Allow this conversation',{'action':'accept','content':None,'_meta':{'persist':'session'}},'Access for this conversation; no permanent grant')
            if computer:
                view['note']='Computer Use can read app contents and capture screenshots while working. Review the app and requested scope.'
        if mode in ('form','openai/form','openaiForm','url'):
            choice('decline','Decline',{'action':'decline','content':None})
            choice('cancel','Cancel request',{'action':'cancel','content':None})
    elif method==INPUT:
        view.update(kind='input',title='Question from your Mac')
        questions=params.get('questions',[])
        valid=isinstance(questions,list) and 1<=len(questions)<=10
        fields=[]
        for q in questions if valid else []:
            if not isinstance(q,dict) or not isinstance(q.get('id'),str) or q.get('isSecret') or q.get('is_secret'):valid=False;break
            field={'id':q['id'],'label':text(q.get('question') or q.get('header') or 'Your answer'),
                   'type':'string','required':True,'maxLength':4000,'description':''}
            options=q.get('options')
            if options:
                if not isinstance(options,list) or len(options)>30 or any(not isinstance(o,dict) or not isinstance(o.get('label'),str) for o in options):valid=False;break
                field['suggestions']=[text(o['label']) for o in options]
            fields.append(field)
        if valid and complete:
            view['fields']=fields;choice('reply','Send answer',{},'Directly to the pending desktop request')
    if any(c['id'] in ('allow','session','reply') for c in view['choices']):
        view['desktop_only']=False
        if not view['note'].startswith('Computer Use'):view['note']='Review the exact action and scope before responding. Approvals are never queued or sent automatically.'
    elif not complete:view['note']='Some details are hidden, too large, or unavailable. Review the complete request on your Mac before approving.'
    return view,operations


def approval_views(state,owner,redact=lambda s:s):
    values=[]
    for request in pending_requests(state):
        built=build_request(request,state,owner,redact)
        if built:values.append(built[0])
    return values


def response_for(state,owner,identity,revision,choice,answers,redact=lambda s:s):
    request=next((r for r in pending_requests(state) if request_key(r.get('id'))==identity),None)
    built=build_request(request,state,owner,redact) if request else None
    if not built or built[0]['revision']!=revision:raise ApprovalChanged('This request was answered or changed. Refresh and review the current prompt.')
    view,operations=built
    if choice not in operations:raise ApprovalChanged('This choice is unavailable. Review the request on your Mac.')
    fields=view['fields'];content={}
    if not isinstance(answers,dict) or len(answers)>20:raise ApprovalChanged('Invalid form response.')
    needs_fields=choice in ('allow','reply')
    expected={f['id'] for f in fields} if needs_fields else set()
    if set(answers)-expected:raise ApprovalChanged('Unexpected form fields. Refresh before replying.')
    for f in fields if needs_fields else []:
        if f['id'] not in answers:
            if f['required']:raise ApprovalChanged('Complete every required field.')
            continue
        value=answers[f['id']];kind=f['type']
        valid=(isinstance(value,str) and len(value)<=min(4000,f.get('maxLength',4000)) and len(value)>=f.get('minLength',0) and (not f['required'] or bool(value.strip()))) if kind=='string' else type(value)==bool if kind=='boolean' else type(value) in (int,float) and math.isfinite(value) and (kind!='integer' or type(value)==int)
        if not valid or 'enum' in f and value not in f['enum'] or kind in ('integer','number') and (value<f.get('minimum',-math.inf) or value>f.get('maximum',math.inf)):
            raise ApprovalChanged('Check the response for '+f['label']+'.')
        content[f['id']]=value
    method=request['method'];payload=operations[choice]
    if method==MCP and choice=='allow':payload={'action':'accept','content':content or None}
    if method==INPUT:payload={'answers':{key:{'answers':[value]} for key,value in content.items()}}
    return METHODS[method],{'conversationId':state['id'],'requestId':request['id'],
                            'decision' if method in (COMMAND,FILES) else 'response':payload}
