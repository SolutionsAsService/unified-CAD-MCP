"""Reproduce coverage from exact checked-out source pins plus live MCP discovery.

No upstream source is copied into the deliverable. AST evidence records actual
registrations, string selectors and native bridge methods (not README claims).
"""
from __future__ import annotations
import argparse
import ast
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from coverage_policy import apply_policy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'src/unified_cad_mcp/coverage.json'


def strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [s for item in node.elts for s in strings(item)]
    return []


def selectors(node, names, constants=None):
    found = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Compare) and isinstance(c.left, ast.Name) and c.left.id in names:
            for op, rhs in zip(c.ops, c.comparators):
                if isinstance(op, (ast.Eq, ast.In, ast.NotIn)):
                    values = (constants or {}).get(rhs.id, []) if isinstance(rhs, ast.Name) else strings(rhs)
                    found.update((c.left.id, s) for s in values)
    return sorted(found)


def static_inventory(source, backend):
    rows = []
    files = sorted((source / ('design_mcp' if backend == 'cad' else 'src/kicad_mcp')).rglob('*.py'))
    for path in files:
        if 'proto' in path.parts:
            continue
        tree = ast.parse(path.read_text())
        location = str(path.relative_to(source))
        constants = {}
        for assignment in tree.body:
            if isinstance(assignment, ast.Assign):
                for target in assignment.targets:
                    if isinstance(target, ast.Name): constants[target.id] = strings(assignment.value)
        def add(kind, name, node, **extra):
            rows.append(dict(kind=kind, name=name, file=location, line=node.lineno, **extra))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in {'tool','resource','prompt'}:
                        name = node.name
                        for kw in dec.keywords:
                            if kw.arg == 'name': name = ast.literal_eval(kw.value)
                        if dec.func.attr == 'resource': name = ast.literal_eval(dec.args[0])
                        add(dec.func.attr, name, node)
                        # All string-valued selectors in registered function bodies,
                        # including nested dispatch closures, not just operation.
                        args = {a.arg for a in node.args.args}
                        for parameter, value in selectors(node, args, constants):
                            add('selector', name, node, parameter=parameter, value=value)
            if backend == 'pcb' and isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Tool':
                name = next(ast.literal_eval(k.value) for k in node.keywords if k.arg == 'name')
                add('tool',name,node)
        if backend == 'pcb' and path.name == 'tools.py':
            for parameter,value in selectors(tree, {'name'}):
                add('dispatch', value, tree.body[0])
        if backend == 'pcb' and path.name == 'ipc_client.py':
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'KiCadIPCClient')
            for method in cls.body:
                if isinstance(method, ast.FunctionDef) and not method.name.startswith('_'):
                    calls = sorted({n.func.attr for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='self'})
                    constructors = sorted({n.func.id for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id[:1].isupper()})
                    add('native_method', method.name, method, calls=calls, constructors=constructors)
        if backend == 'kicad':
            # Native boundary call sites, including literal CLI subcommands and
            # generated pcbnew scripts. These are implementation paths, not tools.
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)): continue
                boundaries = []
                for call in ast.walk(fn):
                    if not isinstance(call, ast.Call): continue
                    target = ast.unparse(call.func)
                    if target in {'run_pcbnew_script', 'subprocess.run', 'subprocess.Popen', 'asyncio.create_subprocess_exec'} or target.endswith('.run_command'):
                        boundaries.append({'target':target, 'line':call.lineno,
                                           'arguments': [ast.unparse(a) for a in call.args][:2]})
                if boundaries:
                    add('native_boundary', fn.name, fn, boundaries=boundaries)
        if backend == 'cad' and path.name == 'freecad_worker.py':
            for parameter,value in selectors(tree, {'operation'}):
                add('native_command', value, tree.body[-1])
    return rows


async def wire_inventory(config):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    result={}
    for name,b in config['backends'].items():
        env={'FASTMCP_CHECK_FOR_UPDATES':'off','FASTMCP_SHOW_SERVER_BANNER':'false','KICAD_MCP_NO_TELEMETRY':'1',**b.get('env',{})}
        if name=='pcb': env['KICAD_MCP_ENABLE_SCHEMATIC']='1'
        async with Client(StdioTransport(b['command'],b['args'],env=env,cwd=b.get('cwd',config['project_root']),keep_alive=False),mode='legacy',timeout=60) as c:
            result[name]={}
            for key,method in [('tools',c.list_tools),('resources',c.list_resources),('templates',c.list_resource_templates),('prompts',c.list_prompts)]:
                try:
                    items = await method()
                except Exception as error:
                    if getattr(error, 'code', None) != -32601:
                        raise
                    items = []
                result[name][key]=[x.model_dump(mode='json',by_alias=True,exclude_none=True) for x in items]
    return result


def build(source_root, wire):
    pins=json.loads((ROOT/'sources.lock.json').read_text())['backends']
    out={'schema_version':1,'pins':pins,'dedup_policy':'Keep file-based, live IPC, and CAD provenance semantics distinct. No cross-backend tools are semantically interchangeable; native implementation aliases are not advertised as additional tools.','backends':{}}
    for name,pin in pins.items():
        src=source_root/name
        sha=subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()
        if sha!=pin['commit']: raise ValueError(f'{name}: wrong source revision {sha}')
        rows=static_inventory(src,name)
        registered={r['name'] for r in rows if r['kind']=='tool'}
        live={r['name'] for r in wire[name]['tools']}
        # Upstream contains legacy unregistered registrars: do not advertise them.
        for row in rows:
            row['source_commit']=sha
            kind=row['kind']; tool=row['name']
            if kind in {'tool','selector','dispatch'} and tool in live:
                row['canonical']=f'{name}_{tool}'
                row['disposition']='canonical'
            elif kind in {'tool','selector'}:
                row['disposition']='unregistered_source_helper'
                row['reason']='Legacy registrar is not called by the pinned server; implementation remains available through current routers where mapped below.'
            else:
                row['disposition']='native_dependency'
                row['reason']='Internal implementation, not an additional MCP tool; native application required.'
            row['source_sha256']=hashlib.sha256((src/row['file']).read_bytes()).hexdigest()
        if not live<=registered: raise ValueError(f'Unexplained wire registrations: {live-registered}')
        # Schema enums are also finite subcommands/options, including values
        # only validated inside downstream helpers. Record them separately.
        tool_rows = {r['name']:r for r in rows if r['kind']=='tool'}
        for tool in wire[name]['tools']:
            def enum_rows(schema, path):
                if not isinstance(schema, dict): return
                if 'enum' in schema:
                    for value in schema['enum']:
                        row = dict(tool_rows[tool['name']])
                        row.update(kind='schema_choice',parameter=path,value=value)
                        rows.append(row)
                for key,value in schema.items():
                    if isinstance(value,dict):
                        for child,body in value.items(): enum_rows(body,path+'.'+child)
                    elif isinstance(value,list):
                        for index,body in enumerate(value): enum_rows(body,path+'.'+key+'.'+str(index))
            enum_rows(tool['inputSchema'],'input')
        rows = apply_policy(name, rows, src)
        out['backends'][name]={'wire':wire[name],'source_entries':rows}
    return out


async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'.cache/sources')
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--check',action='store_true')
    a=p.parse_args()
    wire=await wire_inventory(json.loads(a.config.read_text()))
    result=build(a.sources,wire)
    value=json.dumps(result,indent=2,sort_keys=True)+chr(10)
    if a.check:
        if OUTPUT.read_text()!=value: raise SystemExit('Coverage drift; regenerate and review inventory')
    else: OUTPUT.write_text(value)
    print({n:{'tools':len(b['wire']['tools']),'entries':len(b['source_entries'])} for n,b in result['backends'].items()})

if __name__=='__main__': asyncio.run(main())
