"""Adapt the pinned upstream MCP server without changing its protocol engine."""
import json

from .client import FusionClient
from .diagnostics import call_failure, error_response
from .environment import discover_tools
from .generic import GenericSkill
from .physical import PhysicalVerification
from .registry import ApiRegistry
from .waveform import read_waveform
from .mcp_validation import make_dispatch
from ._upstream import mcp_server as server

server._AdapterClient = FusionClient
server.SERVER_INFO = {'name': 'virtuoso-fusion', 'version': '0.3.3',
                      'title': 'Virtuoso Fusion — engineering APIs and registered SKILL'}
_original_tool_defs = server._tool_defs
_registry = None


def _registry_data():
    global _registry
    if _registry is None:
        _registry = ApiRegistry()
    return _registry


def _registry_search(args):
    results = _registry_data().search(args['query'], limit=args.get('limit', 20))
    return server._text_result(json.dumps(results, ensure_ascii=False), structured={'matches': results}, cache_public=True)


def _registry_coverage(args):
    coverage = _registry_data().coverage()
    return server._text_result(json.dumps(coverage), structured=coverage, cache_public=True)


def _call_registered(args):
    try:
        caller = _session_callers(server._client())[args.get('allow_write', False)]
        result, stage = caller._call(args['name'], args.get('args', []),
                                     args.get('kwargs', {}), args.get('timeout', 60))
    except Exception as exc:
        return _registered_error(exc, args['name'], 'call_registered')
    response = server._bridge_result(result)
    if not result.ok:
        return error_response(response, call_failure(stage, args['name'],
                              'See the original errors and metadata in this response.'), 'call_registered')
    return response


def _refresh_signature(args):
    try:
        caller = _session_callers(server._client())[False]
        result = caller.ensure_signature(args['name'], timeout=args.get('timeout', 60), force=True)
    except Exception as exc:
        return _registered_error(exc, args['name'], 'refresh_signature')
    response = server._bridge_result(result)
    if not result.ok:
        return error_response(response, call_failure('signature_probe', args['name'],
                              'See the original arglist errors and metadata.'), 'refresh_signature')
    response['structuredContent']['signature_available'] = caller.reg.has_signature(caller.reg.get(args['name']) or {})
    return response


def _registered_error(exc, function, tool):
    detail = getattr(exc, 'fusion_error', call_failure('connection_setup', function, str(exc)))
    response = (server._exception_result(exc) if detail['stage'] in
                ('signature_probe', 'execution') else server._tool_error(str(exc)))
    return error_response(response, detail, tool)


def _session_callers(client):
    cached = getattr(client, '_fusion_mcp_callers', None)
    if cached is not None and cached[0] is client.registry:
        return cached[1]
    registry = client.registry
    # Preserve already loaded/custom state; only adopt an unused packaged registry.
    if registry.is_packaged and registry._raw is None and registry._base is None:
        client.registry = registry = _registry_data()._session_view()
        client.generic.reg = registry
    callers = {permission: GenericSkill(client.execute_skill, registry=registry,
                                        allow_write=permission)
               for permission in (False, True)}
    callers[True]._probed = callers[False]._probed
    client._fusion_mcp_callers = (registry, callers)
    return callers


def _discover_tools(args):
    result = discover_tools(**args)
    return server._text_result(json.dumps(result), structured=result)


def _run_physical(args):
    options = dict(args)
    api = PhysicalVerification(calibre=options.pop('calibre', 'calibre'))
    result = api.run(**options)
    response = server._text_result(json.dumps(result))
    # Preserve verdicts and artifact handles even beyond the text size limit.
    response.update(structuredContent=result, isError=not result['ok'], ttlMs=0)
    return response


def _tool_defs():
    readonly = {'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False}
    additions = [
        {'name': 'discover_tools', 'description': 'Read executable and explicitly supplied deck availability on the MCP server host; does not check out licenses.',
         'inputSchema': {'type': 'object', 'properties': {
             'executables': {'type': 'object', 'additionalProperties': {'type': 'string'}},
             'decks': {'type': 'array', 'items': {'type': 'string'}, 'default': []}},
             'additionalProperties': False}, 'annotations': readonly},
        {'name': 'run_physical', 'description': 'Run Calibre DRC/LVS/PEX synchronously in a new artifact directory on the MCP server host using an explicit trusted deck. This blocks other requests on the same stdio connection. timeout is per phase (PEX may use three budgets); cancellation notifications do not cancel the process. Source is required for LVS/PEX. execution_ok is process success; checks_passed is the DRC/LVS verdict. PEX extraction_complete is output completeness, with checks_passed=null, and does not certify chip signoff.',
         'inputSchema': {'type': 'object', 'properties': {
             'kind': {'type': 'string', 'enum': ['drc', 'lvs', 'pex', 'rcx']},
             'deck': {'type': 'string'}, 'layout': {'type': 'string'},
             'primary': {'type': 'string'}, 'work_dir': {'type': 'string'},
             'source': {'type': 'string'}, 'source_primary': {'type': 'string'},
             'netlist': {'type': 'string', 'default': 'net.dist'},
             'calibre': {'type': 'string', 'default': 'calibre'},
             'timeout': {'type': 'number', 'exclusiveMinimum': 0, 'default': 240}},
             'required': ['kind', 'deck', 'layout', 'primary', 'work_dir'],
             'additionalProperties': False},
         'annotations': {'readOnlyHint': False, 'idempotentHint': False, 'openWorldHint': True}},
        {'name': 'registry_search', 'description': 'Search the offline registry snapshot; existence does not prove runtime availability.',
         'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 20}}, 'required': ['query'], 'additionalProperties': False}, 'annotations': readonly},
        {'name': 'registry_coverage', 'description': 'Signature coverage of the offline snapshot, not validated engineering coverage.',
         'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'annotations': readonly},
        {'name': 'refresh_signature', 'description': 'Invalidate this session’s cached signature for one function and read its current arglist. Never invokes the target function. Use after redefining or loading SKILL code; nil/unknown means keyword binding remains unavailable.',
         'inputSchema': {'type': 'object', 'properties': {
             'name': {'type': 'string'},
             'timeout': {'type': 'number', 'exclusiveMinimum': 0, 'default': 60}},
             'required': ['name'], 'additionalProperties': False}, 'annotations': readonly},
        {'name': 'call_registered', 'description': 'Call a registered SKILL function through Fusion without automatic replay. allow_write applies to this call only; this guard does not restrict upstream evaluate or engineering tools.',
         'inputSchema': {'type': 'object', 'properties': {
             'name': {'type': 'string'}, 'args': {'type': 'array', 'default': []},
             'kwargs': {'type': 'object', 'default': {}}, 'allow_write': {'type': 'boolean', 'default': False},
             'timeout': {'type': 'number', 'exclusiveMinimum': 0, 'default': 60}},
             'required': ['name'], 'additionalProperties': False},
         'annotations': {'readOnlyHint': False, 'idempotentHint': False, 'openWorldHint': True}},
    ]
    return sorted(_original_tool_defs() + additions, key=lambda tool: tool['name'])


server._tool_defs = _tool_defs
server.TOOL_NAMES = [tool['name'] for tool in _tool_defs()]
server.IMPL.update(read_waveform=read_waveform, registry_search=_registry_search, registry_coverage=_registry_coverage,
                   call_registered=_call_registered, refresh_signature=_refresh_signature, discover_tools=_discover_tools,
                   run_physical=_run_physical)

server.dispatch_tool = make_dispatch(
    server.dispatch_tool, server._tool_defs(), server.ALIASES, server._tool_error)


def main():
    try:
        server.main()
    finally:
        client = server._CLIENT.get('obj')
        if client is not None:
            client.close()


if __name__ == '__main__':
    main()
