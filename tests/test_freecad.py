"""Protocol tests use fake TCP only; backend tests execute real pinned stdio source.
No test claims native shape/GUI verification.
"""
import json
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from unified_cad_mcp.freecad_adapter import LocalClient, MAX_FRAME, validate_arguments, load_upstream, install_adapter
from unified_cad_mcp.config import load_config, BackendConfig
from unified_cad_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]


def fake_peer(monkeypatch, response, delay=0):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1)
    port = listener.getsockname()[1]
    monkeypatch.setenv('UNIFIED_FREECAD_PORT', str(port))
    requests = []
    def serve():
        with listener:
            connection, _ = listener.accept()
            with connection:
                def read(n):
                    data = b''
                    while len(data) < n:
                        chunk = connection.recv(n-len(data))
                        if not chunk: break
                        data += chunk
                    return data
                size = struct.unpack('>I', read(4))[0]
                requests.append(json.loads(read(size)))
                time.sleep(delay)
                if response is not None:
                    try:
                        # Fragmented length/header/data on purpose.
                        for part in [response[:2], response[2:5], response[5:]]:
                            connection.sendall(part)
                    except OSError: pass
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    return port, requests, thread


def frame(result):
    data = json.dumps(result).encode()
    return struct.pack('>I', len(data)) + data


def test_fragmented_framing_preserves_output(monkeypatch):
    expected = {'ok': True, 'output': 'hello', 'result': "('1', '0')", 'error': None}
    port, requests, thread = fake_peer(monkeypatch, frame(expected))
    client = LocalClient(port=port); client.connect()
    assert client.execute('FreeCAD.Version()') == expected
    client.disconnect(); thread.join(1)
    assert requests == [{'code': 'FreeCAD.Version()'}]


@pytest.mark.parametrize('response,delay', [(None,0), (b'bad!',0), (struct.pack('>I', MAX_FRAME+1),0), (frame({'bad': 1}),0), (frame({'ok':True}),0.15)])
def test_transport_failure_disconnects_without_retry(monkeypatch,response,delay):
    monkeypatch.setenv('UNIFIED_FREECAD_TIMEOUT','0.05')
    port, requests, thread = fake_peer(monkeypatch,response,delay)
    client = LocalClient(port=port); client.connect()
    with pytest.raises(ConnectionError, match='UNKNOWN'):
        client.execute('mutate()')
    assert not client.is_connected()
    with pytest.raises(ConnectionError, match='Not connected'):
        client.execute('mutate()')
    thread.join(1)
    assert len(requests) == 1


def test_native_error_is_not_reported_as_success(monkeypatch):
    response={'ok':False,'error':'Native failure','output':'partial change','result':None}
    port, _, thread=fake_peer(monkeypatch,frame(response))
    client=LocalClient(port=port);client.connect()
    with pytest.raises(RuntimeError,match='partial change'): client.execute('mutate()')
    client.disconnect();thread.join(1)


@pytest.mark.parametrize('host', ['0.0.0.0','192.168.1.1','example.com','127.0.0.2','::1'])
def test_remote_endpoints_rejected(host):
    with pytest.raises(ValueError,match='localhost'): LocalClient(host)


@pytest.mark.parametrize('value', ['nan','inf','0','61'])
def test_deadline_configuration_rejected(monkeypatch,value):
    monkeypatch.setenv('UNIFIED_FREECAD_TIMEOUT',value)
    with pytest.raises(ValueError): LocalClient()


def test_path_and_schema_boundary():
    tool={'name':'export_step','inputSchema':{'properties':{'filepath':{'type':'string'}},'required':['filepath']}}
    for value in ['relative.step','/tmp/a".step','/tmp/a'+chr(10)+'.step']:
        with pytest.raises(ValueError): validate_arguments(tool,{'filepath':value})
    validate_arguments(tool,{'filepath':'C:/Users/operator/model.step'})
    with pytest.raises(ValueError): validate_arguments(tool,{'filepath':'/tmp/a.step','other':1})
    with pytest.raises(ValueError): validate_arguments(tool,{})
    validate_arguments({'name':'execute_python','inputSchema':{'properties':{'code':{'type':'string'}}}}, {'code':'print("hello")'+chr(10)})


async def test_failed_freecad_isolated(config):
    config.backends['freecad']=BackendConfig(command='missing-freecad-adapter-test')
    async with Client(create_server(config)) as c:
        assert 'cad_identify' in {t.name for t in await c.list_tools()}
        assert (await c.call_tool('unified_get_context')).data['backends']['freecad']['state']=='unavailable'


@pytest.mark.backends
async def test_real_upstream_default_and_missing_native(tmp_path):
    selected=os.environ.get('UNIFIED_CAD_BACKEND_CONFIG')
    if not selected: pytest.skip('Install pinned backends')
    config=load_config(Path(selected))
    if 'freecad' not in config.backends: pytest.skip('FreeCAD backend required')
    config.backends={'freecad':config.backends['freecad']}
    config.backends['freecad'].env['UNIFIED_FREECAD_EXPERIMENTAL']='0'
    # Reserve an unlistened local port so this test cannot touch a real GUI.
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1',0)); port=reserved.getsockname()[1]
        config.backends['freecad'].env['UNIFIED_FREECAD_PORT']=str(port)
        async with Client(create_server(config),timeout=60) as c:
            assert {t.name for t in await c.list_tools()}=={'unified_get_context','freecad_connect_to_freecad','freecad_execute_python'}
            missing=await c.call_tool('freecad_connect_to_freecad',{'port':port},raise_on_error=False)
            assert missing.is_error and 'listener unavailable' in missing.content[0].text
            disconnected=await c.call_tool('freecad_execute_python',{'code':'1+1'},raise_on_error=False)
            assert disconnected.is_error and 'Not connected' in disconnected.content[0].text


@pytest.mark.backends
async def test_real_upstream_schema_and_dispatch_with_fake_native(monkeypatch):
    source=ROOT/'.cache/sources/freecad'
    if not source.exists(): pytest.skip('Fetch pinned source')
    module=load_upstream(source)
    original=[];module.send_result=lambda _id,result:original.append(result)
    module.handle_tools_list(1,{})
    tools=original[-1]['tools']; assert len(tools)==16
    module=install_adapter(module,experimental=True)
    results=[];module.send_result=lambda _id,result:results.append(result)
    class Boundary:
        def is_connected(self): return True
        def execute(self,code): raise RuntimeError('synthetic native rejection')
    module.client=Boundary()
    for tool in tools:
        if tool['name']=='connect_to_freecad':continue
        args={}
        for name,rule in tool['inputSchema']['properties'].items():
            if name not in tool['inputSchema'].get('required',[]): continue
            args[name]={'number':1,'string':'Sketch','array':['Edge1']}[rule['type']]
            if name=='filepath':args[name]='/tmp/test.step'
        module.handle_tool_call(1,{'name':tool['name'],'arguments':args})
        assert results[-1]['isError'] is True,(tool['name'],results[-1])
        assert 'synthetic native rejection' in results[-1]['content'][0]['text']
    # Real stdio upstream + unified FastMCP, fake framed TCP only.
    port,requests,thread=fake_peer(monkeypatch,frame({'ok':True,'result':"('fake-version',)",'output':'','error':None}))
    transport=StdioTransport(sys.executable,[str(ROOT/'src/unified_cad_mcp/freecad_adapter.py')],env={'UNIFIED_FREECAD_SOURCE':str(source),'UNIFIED_FREECAD_PORT':str(port),'UNIFIED_FREECAD_EXPERIMENTAL':'1'},keep_alive=False)
    async with Client(transport,mode='legacy') as c:
        actual=await c.list_tools()
        assert {t.name:t.input_schema for t in actual}=={t['name']:t['inputSchema'] for t in tools}
        result=await c.call_tool('connect_to_freecad',{'port':port})
        assert not result.is_error and 'fake-version' in result.content[0].text
    thread.join(1);assert requests==[{'code':'FreeCAD.Version()'}]


def test_source_tampering_rejected(tmp_path):
    (tmp_path/'server').mkdir()
    (tmp_path/'server/nirulink_mcp_server.py').write_text('raise RuntimeError("must not execute")')
    with pytest.raises(ValueError,match='mismatch'):load_upstream(tmp_path)


def test_port_types_and_finite_numeric_arguments(monkeypatch):
    monkeypatch.setenv('UNIFIED_FREECAD_PORT','9876')
    for port in [True,0,65536,9877,'9876']:
        with pytest.raises(ValueError): LocalClient(port=port)
    tool={'name':'pad','inputSchema':{'properties':{'length':{'type':'number'},'symmetric':{'type':'boolean'},'edges':{'type':'array'}}}}
    for args in [{'length':True},{'length':float('nan')},{'length':float('inf')},{'symmetric':1},{'edges':[1]}]:
        with pytest.raises(ValueError):validate_arguments(tool,args)


@pytest.mark.backends
def test_freecad_gate_blocks_direct_calls_and_suppresses_logging(capsys):
    source=ROOT/'.cache/sources/freecad'
    if not source.exists():pytest.skip('Fetch pinned source')
    module=install_adapter(load_upstream(source))
    module.log('private project contents')
    assert capsys.readouterr().err==''
    results=[];module.send_result=lambda _id,result:results.append(result)
    module.handle_tool_call(1,{'name':'new_document','arguments':{}})
    assert results[-1]['isError'] and 'disabled' in results[-1]['content'][0]['text']
