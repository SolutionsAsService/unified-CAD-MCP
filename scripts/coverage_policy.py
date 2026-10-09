"""Reviewed semantic policy, separate from mechanically extracted evidence."""
import ast

ALIASES = {
    ('pcb', 'get_constraints'): ('audit', 'constraints', 'Both dispatch to pcb_keepout._op_constraints(pcb_path).'),
    ('pcb', 'check_silkscreen_overlaps'): ('audit', 'check_silkscreen_overlaps', 'Both dispatch to pcb_silkscreen._op_check_silkscreen_overlaps(pcb_path).'),
}
SCHEMATIC = {
    'get_schematic_wires','get_schematic_labels','get_schematic_symbols','get_schematic_sheets',
    'get_schematic_junctions','get_schematic_no_connects','draw_schematic_wire',
    'add_schematic_label','get_symbol_libraries','search_symbols','place_symbol',
    'get_symbol_pins','update_symbol','annotate_symbols','add_schematic_symbol','delete_schematic_item',
}
CAD_WORKER = {
    'inspect':['cad_inspect_freecad_model','cad_inspect_cad_envelopes'],
    'distance':['cad_measure_freecad_distance'], 'entities':['cad_get_freecad_entities'],
    'preview':['cad_preview_cad_revision'], 'create_box':['cad_create_box_revision'],
    'revise_box':['cad_revise_box_parameters'],
}


def apply_policy(name, rows, source):
    if name == 'pcb':
        tree=ast.parse((source/'src/kicad_mcp/tools.py').read_text())
        dispatcher=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_execute_tool')
        direct={}
        for branch in dispatcher.body:
            if not isinstance(branch,ast.If) or not isinstance(branch.test,ast.Compare): continue
            test=branch.test
            if not isinstance(test.left,ast.Name) or test.left.id!='name': continue
            tool=ast.literal_eval(test.comparators[0])
            direct[tool]={n.func.attr for statement in branch.body for n in ast.walk(statement) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='kicad'}
        methods={r['name']:r for r in rows if r['kind']=='native_method'}
        for tool,calls in direct.items():
            pending=list(calls)
            while pending:
                method=pending.pop()
                if method not in methods: continue
                row=methods[method]
                mapped=row.setdefault('canonical_capabilities',[])
                if 'pcb_'+tool in mapped: continue
                mapped.append('pcb_'+tool)
                pending.extend(row['calls'])
        for row in rows:
            if row['kind']=='native_method':
                mapped=row.get('canonical_capabilities',[])
                if mapped:
                    row['disposition']='implementation_of'
                    row['canonical_capabilities']=sorted(mapped)
                    row['reason']='Reachable native/client implementation of these upstream MCP tools; not advertised twice.'
                else:
                    row['disposition']='unavailable'
                    row['reason']='No registered upstream MCP dispatch reaches this client method. Requires a compatible native IPC implementation and a separately reviewed MCP interface; no fabricated wrapper is advertised.'
            if row['kind'] in {'tool','dispatch'}:
                row['preconditions']=['Running compatible KiCad GUI with IPC enabled','connect_to_kicad in this backend session; explicit document selection recommended']
                row['default_advertised']=row['name'] not in SCHEMATIC
                if row['name'] in SCHEMATIC:
                    row['preconditions'].append('KICAD_MCP_ENABLE_SCHEMATIC=1; experimental schematic native command support')
                if row['name']=='route_pads':
                    row['preconditions'].append('Native StartRoute/SetRouteEnd/FinishRoute extensions; NOT established on stock KiCad')
                    row['warning']='Upstream commits even when has_violations is true; success is not DRC approval.'
    for row in rows:
        if name=='kicad' and row['kind']=='selector':
            key=(row['name'],row['value'])
            if key in ALIASES:
                tool,action,reason=ALIASES[key]
                row.update(disposition='semantic_duplicate',canonical='kicad_'+tool,canonical_arguments={'operation':action},reason=reason,compatibility='Original router operation remains callable; no extra tool is advertised.')
        if row['kind']=='resource':
            scheme,rest=row['name'].split('://',1)
            row.update(disposition='canonical',canonical=scheme+'://'+name+'/'+rest)
        if name=='cad' and row['kind']=='native_command':
            row.update(disposition='implementation_of',canonical_capabilities=CAD_WORKER[row['name']],reason='FreeCAD worker command, not a second MCP tool; requires operator-configured FreeCAD bundled Python.')
    for row in rows:
        if row['kind']=='native_boundary':
            row.update(disposition='unavailable', reason='Native boundary requires pcbnew/kicad-cli, Java+Freerouting or KiKit as identified by source callsite. Not a registered independent MCP tool; native dependencies are absent in the verification host.', preconditions=['Inspect the pinned callsite and corresponding router schema; no generic native command executor is exposed.'])
    return rows
