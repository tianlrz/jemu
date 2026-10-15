"""Bounded local UI command mailbox. No browser automation or arbitrary script execution."""
import json
import secrets
import time

CLIENTS = {}
COMMANDS = {}
ACTIONS = ('open','next','previous','goto','reset','edit','select','text','format','move','delete','copy','paste','undo','redo','save','present','exit','presenter','timer','settings')


def prune():
    now=time.monotonic()
    for key in list(CLIENTS):
        if now-CLIENTS[key]['seen']>90:del CLIENTS[key]
    for key in list(COMMANDS):
        if now-COMMANDS[key]['created']>120:del COMMANDS[key]


def clients():
    prune()
    return [{'client':key,'ageSeconds':round(time.monotonic()-value['seen'],1),**value['state']} for key,value in CLIENTS.items()]


def sync(data):
    prune();client=data.get('client');state=data.get('state')
    if not isinstance(client,str) or not 1<=len(client)<=64 or not all(c.isalnum() or c=='-' for c in client):raise ValueError('界面会话标识无效')
    if not isinstance(state,dict) or len(json.dumps(state).encode())>16384:raise ValueError('界面状态无效或过大')
    state={key:state.get(key) for key in ('deck','revision','page','pages','mode','dirty','busy','editing','presenting','fullscreen','desktop','window','selection','timer')}
    if client not in CLIENTS and len(CLIENTS)>=4:
        oldest=min(CLIENTS,key=lambda key:CLIENTS[key]['seen'])
        del CLIENTS[oldest]
    CLIENTS[client]={'seen':time.monotonic(),'state':state}
    acknowledgements=data.get('results',[])
    if not isinstance(acknowledgements,list) or any(not isinstance(ack,dict) for ack in acknowledgements):raise ValueError('命令回执格式无效')
    for ack in acknowledgements[:16]:
        command=COMMANDS.get(ack.get('id'))
        if command and command['client']==client and command['status']=='delivered':
            command.update(status='completed' if ack.get('ok') else 'failed',result={'ok':bool(ack.get('ok')),'message':str(ack.get('message',''))[:1000]})
            command.pop('args',None)
    commands=[]
    for command in COMMANDS.values():
        if command['client']==client and command['status']=='queued':
            if time.monotonic()-command['created']>30:
                command.update(status='failed',result={'ok':False,'message':'界面命令已过期，未执行'})
            else:
                commands.append({k:command[k] for k in ('id','action','args')});command['status']='delivered'
    return {'commands':commands}


def enqueue(data):
    prune();action=data.get('action');args=data.get('args',{})
    if action not in ACTIONS:raise ValueError('不支持的界面命令')
    if not isinstance(args,dict) or len(json.dumps(args).encode())>65536:raise ValueError('命令参数过大或无效')
    client=data.get('client')
    active=[key for key,value in CLIENTS.items() if time.monotonic()-value['seen']<20]
    if not client:
        if len(active)!=1:raise ValueError('没有活动窗口或有多个窗口，请先 ui status 并指定 --client')
        client=active[0]
    if client not in active:raise ValueError('目标窗口未连接，请打开或刷新简幕窗口')
    if len(COMMANDS)>=64:
        for key in list(COMMANDS):
            if COMMANDS[key]['status'] in ('completed','failed'):del COMMANDS[key];break
        else:raise ValueError('待处理命令过多，请等待执行完成')
    if sum(c['client']==client and c['status'] in ('queued','delivered') for c in COMMANDS.values())>=16:raise ValueError('此窗口待处理命令已达上限')
    key=secrets.token_hex(8)
    COMMANDS[key]={'id':key,'client':client,'action':action,'args':args,'created':time.monotonic(),'status':'queued'}
    return result(key)


def result(key):
    prune()
    if key not in COMMANDS:raise ValueError('命令不存在或结果已过期，请先检查界面状态再重试')
    return {k:v for k,v in COMMANDS[key].items() if k not in ('created','args')}


def capabilities():
    return {'apiVersion':1,'transport':'loopback-http','mcp':{'transport':'stdio','entry':'jemu_mcp.py'},'pages':{'workspace':'/','settings':'/settings'},'cli':'jemu.cmd / python jemu.py',
        'settings':{'get':'GET /api/settings','backup':'GET|POST /api/settings/backup','test':'POST /api/cloud/test','disconnect':'POST /api/cloud/disconnect'},
        'operations':['start','stop','status','settings','decks','files','versions','export','backup','ui','request'],
        'fileAPI':{'list':'GET /api/decks/{id}/files','read':'GET /api/decks/{id}/file?path=...','write':'POST /api/decks/{id}/file?path=...&baseRevision=... (binary body)','delete':'POST /api/decks/{id}/delete-file (JSON path,baseRevision)'},
        'projectPreparation':{'endpoint':'POST /api/decks/{id}/prepare','fields':['baseRevision','entry','html (optional normalized HTML)'],'result':'source snapshot + persistent Jemu working version; no-op when already prepared','rendering':'dynamic materialization requires an open app; CLI imports are prepared on first app open'},
        'renderExport':{'capabilities':'GET /api/render-capabilities','start':'POST /api/decks/{id}/render-start (kind pdf|pptx, baseRevision; optional static html,pages,width,height,entry)','status':'GET /api/decks/{id}/render-jobs/{job}','download':'GET /api/decks/{id}/render-jobs/{job}/download','cli':'export --kind pdf|pptx --out FILE','pptx':'PDF pages as full-slide images; no editable text; optional pypdfium2/Pillow'},
        'desktopWindow':{'open':'POST /api/open-desktop (optional deck)','cli':'desktop --deck ID','mcp':'jemu_service operation desktop','attach':'POST /api/window/attach (launcher window key)','fullscreen':'POST /api/window/fullscreen (window key, enabled)','status':'GET /api/window/status?window=KEY','scope':'Only launcher-registered Jemu Edge windows on Windows; no arbitrary PID/HWND control'},
        'uiActions':list(ACTIONS),'limits':{'fileMiB':100,'documentMiB':100,'jsonMiB':16,'uiClients':4,'uiCommands':64,'commandsPerClient':16,'resultSeconds':120},
        'notes':['写文件和恢复版本需 baseRevision；冲突返回 HTTP 409','文件操作与已保存页组的 PDF/PPT 导出无须打开浏览器；ui 命令需要活动窗口','界面状态每秒更新；隐藏窗口降低为每五秒，浏览器仍可能节流','全屏与弹窗受浏览器限制；自由网页仍用原生打印；未接入 GitHub 发布','界面命令不自动重试，超时不代表未执行；先 ui status / ui result 检查']}
