"""Inventory regressions; native method availability is not simulated as success."""
import ast
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from unified_cad_mcp.config import load_config, BackendConfig
from unified_cad_mcp.structured import GENERIC_OUTPUT_SCHEMA, STL_TOOL, UNITS_PARAMETER
from unified_cad_mcp.pcb_adapter import adapted_client
from unified_cad_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_sources import build, selectors

INVENTORY = json.loads((ROOT/'src/unified_cad_mcp/coverage.json').read_text())


def test_complete_canonical_inventory_and_semantic_duplicates():
    names=[]
    for backend, data in INVENTORY['backends'].items():
        names.extend(backend+'_'+t['name'] for t in data['wire']['tools'])
        rows=data['source_entries']
        assert all(r['source_commit']==INVENTORY['pins'][backend]['commit'] for r in rows)
        assert all(r['disposition'] in {'canonical','semantic_duplicate','implementation_of','unavailable'} for r in rows)
        tools={r['name'] for r in rows if r['kind']=='tool'}
        assert tools=={t['name'] for t in data['wire']['tools']}
        if backend=='freecad':
            assert tools=={r['name'] for r in rows if r['kind']=='dispatch'}
            assert sum(r.get('default_advertised',False) for r in rows if r['kind']=='tool')==2
            assert data['wire']['resources']==data['wire']['prompts']==[]
        if backend=='pcb':
            assert tools=={r['name'] for r in rows if r['kind']=='dispatch'}
            assert sum(r.get('default_advertised',False) for r in rows if r['kind']=='tool')==19
    assert len(names)==len(set(names))==102
    aliases=[r for r in INVENTORY['backends']['kicad']['source_entries'] if r['disposition']=='semantic_duplicate']
    assert len(aliases)==2
    assert all(r['reason'] and r['compatibility'] for r in aliases)
    # These similar operations MUST remain distinct (live GUI vs saved file).
    assert {'pcb_place_track','kicad_pcb','pcb_get_nets','kicad_schematic'}<=set(names)
    methods={r['name']:r for r in INVENTORY['backends']['pcb']['source_entries'] if r['kind']=='native_method'}
    assert 'pcb_route_pads' in methods['start_route']['canonical_capabilities']
    assert methods['toggle_route_via']['disposition']=='unavailable'


def test_source_extractor_covers_guarded_and_fallback_selectors():
    tree=ast.parse('''
if operation == 'run': pass
elif operation in ('start','poll'): pass
if query not in ALLOWED: raise ValueError()
''')
    assert selectors(tree,{'operation','query'},{'ALLOWED':['one','two']})==[
        ('operation','poll'),('operation','run'),('operation','start'),('query','one'),('query','two')]


@pytest.mark.parametrize('token', [None, 'synthetic-token'])
def test_connection_adapter_separates_token_from_socket(monkeypatch,tmp_path,token):
    # Fake boundary only; does not claim an actual KiCad connection.
    class Boundary:
        def connect(self,socket_path=None):
            self.observed=(self._kicad_token,socket_path)
            return False
    socket = tmp_path/'api.sock'
    socket.touch()
    monkeypatch.setenv('UNIFIED_PCB_SOCKET',str(socket))
    client=adapted_client(Boundary)()
    assert client.connect(token) is False
    assert client.observed==(token,str(socket))


async def test_pcb_failure_isolated_from_existing_backends(config,monkeypatch):
    # The native KiCad probe is not under test here: on a host with KiCad installed it runs real
    # programs at startup, which made this test machine-dependent (and could hang). Bound the whole test.
    monkeypatch.setattr('unified_cad_mcp.server.detect_kicad',lambda: {'status':'missing'})
    config.backends['pcb']=BackendConfig(command='nonexistent-pcb-test-runtime',init_timeout_seconds=5)
    async with asyncio.timeout(60):
        async with Client(create_server(config)) as c:
            assert 'cad_identify' in {t.name for t in await c.list_tools()}
            assert (await c.call_tool('unified_get_context')).data['backends']['pcb']['state']=='unavailable'
            assert (await c.call_tool('kicad_identify')).data['domain']=='kicad'


def test_kicad_probe_timeout_kills_helpers():
    from unified_cad_mcp.kicad_native import _run
    # A child that leaves a grandchild holding stdout: subprocess.run(timeout=) used to wait for it.
    script='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); time.sleep(30)'
    start=time.monotonic()
    assert _run([sys.executable,'-c',script],timeout=1)==(None,'TimeoutExpired')
    assert time.monotonic()-start<10


@pytest.mark.backends
async def test_every_upstream_schema_matches_unified_wire(tmp_path):
    selected=os.environ.get('UNIFIED_CAD_BACKEND_CONFIG')
    if not selected: pytest.skip('Install pinned backends and set configuration')
    config=load_config(Path(selected))
    if 'pcb' not in config.backends: pytest.skip('PCB backend required')
    config.backends['pcb'].env['KICAD_MCP_ENABLE_SCHEMATIC']='1'
    config.backends['freecad'].env['UNIFIED_FREECAD_EXPERIMENTAL']='1'
    config.backends['freecad'].enabled=True
    config.drone_tools=False
    config.backends['pcb'].env['UNIFIED_PCB_SOCKET']=str(tmp_path/'missing.sock')
    path=tmp_path/'config.json';path.write_text(config.model_dump_json())
    async with Client(StdioTransport(sys.executable,['-m','unified_cad_mcp.server','--config',str(path)],keep_alive=False),mode='legacy',timeout=60) as c:
        tools={t.name:t.model_dump(mode='json',by_alias=True,exclude_none=True) for t in await c.list_tools()}
        assert len(tools)==103
        for backend,data in INVENTORY['backends'].items():
            for original in data['wire']['tools']:
                proxied=tools[backend+'_'+original['name']]
                for field in ['inputSchema','outputSchema','annotations','description']:
                    want,got=original.get(field),proxied.get(field)
                    # structured.py's documented wire extensions: schema-less upstream tools get the generic
                    # object outputSchema, and cad_inspect_stl_geometry gains an optional 'units' argument.
                    allowed=[want,GENERIC_OUTPUT_SCHEMA] if field=='outputSchema' and want is None else [want]
                    if (backend,original['name'],field)==('cad',STL_TOOL,'inputSchema'):
                        assert got['properties'].pop('units')==UNITS_PARAMETER
                    assert got in allowed, (backend,original['name'],field)
        # Actual upstream error payload, without native connection or mocks.
        unavailable=await c.call_tool('pcb_get_nets',raise_on_error=False)
        assert 'not connected' in unavailable.content[0].text.lower()
        # Upstream encodes this application failure as normal text, not isError.
        assert not unavailable.is_error
        guidance=await c.call_tool('kicad_get_usage_guidance',{'operation':'strategy'})
        assert not guidance.is_error
        assert len(await c.list_resources())==8
        assert await c.list_prompts()==[]
        assert await c.list_resource_templates()==[]
        inventory=await c.read_resource('unified://capabilities/inventory')
        assert json.loads(inventory[0].text)['pins']==INVENTORY['pins']


@pytest.mark.backends
async def test_pcb_default_gate_and_real_missing_connection(tmp_path):
    selected=os.environ.get('UNIFIED_CAD_BACKEND_CONFIG')
    if not selected: pytest.skip('Install pinned backends')
    config=load_config(Path(selected))
    if 'pcb' not in config.backends: pytest.skip('PCB backend required')
    config.backends={'pcb':config.backends['pcb']}
    config.backends['pcb'].env.update(KICAD_MCP_ENABLE_SCHEMATIC='0',UNIFIED_PCB_SOCKET=str(tmp_path/'missing.sock'))
    async with Client(create_server(config),timeout=60) as c:
        names={t.name for t in await c.list_tools()}
        assert len(names)==20
        assert 'pcb_get_schematic_symbols' not in names
        result=await c.call_tool('pcb_connect_to_kicad',{},raise_on_error=False)
        assert json.loads(result.content[0].text)['connected'] is False


@pytest.mark.backends
def test_source_checkout_exhaustiveness():
    source=Path(os.environ.get('UNIFIED_CAD_SOURCE_ROOT',ROOT/'.cache/sources'))
    if not all((source/n/'.git').exists() for n in INVENTORY['pins']):
        pytest.skip('Exact source checkouts required')
    wire={n:b['wire'] for n,b in INVENTORY['backends'].items()}
    assert build(source,wire)==INVENTORY


def test_adapter_bounds_send_without_replay(monkeypatch):
    import types
    from unified_cad_mcp import pcb_adapter
    calls=[]
    def req(*args,**kwargs):
        calls.append(kwargs)
        return object()
    class Base: pass
    native=types.SimpleNamespace(pynng=types.SimpleNamespace(Req0=req))
    server=types.SimpleNamespace(KiCadIPCClient=Base)
    server.main=lambda: native.pynng.Req0(recv_timeout=30000)
    monkeypatch.setitem(sys.modules,'kicad_mcp',types.SimpleNamespace(server=server,ipc_client=native))
    pcb_adapter.main()
    assert calls==[{'recv_timeout':30000,'send_timeout':30000}]
    assert issubclass(server.KiCadIPCClient,Base)


@pytest.mark.backends
async def test_real_freecad_missing_configuration(tmp_path):
    selected=os.environ.get('UNIFIED_CAD_BACKEND_CONFIG')
    if not selected: pytest.skip('Install pinned backends')
    config=load_config(Path(selected))
    config.backends={'cad':config.backends['cad']}
    config.backends['cad'].env={'DESIGN_MCP_ASSET_ROOT':str(tmp_path)}
    (tmp_path/'placeholder.step').write_text('not opened without configured runtime')
    async with Client(create_server(config)) as c:
        await c.list_tools()
        result=await c.call_tool('cad_inspect_freecad_model',{'relative_path':'placeholder.step'},raise_on_error=False)
        assert result.is_error
        assert 'DESIGN_MCP_FREECAD_PYTHON' in result.content[0].text
