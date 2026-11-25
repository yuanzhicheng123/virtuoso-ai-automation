"""Invocation-phase diagnostics shared by Python calls and MCP responses."""
import json


_PHASES = {
    'connection_setup': ('CLIENT_SETUP_FAILED', 'Configure the MCP connection/profile (including VB_REMOTE_HOST when required), then submit again.', '$'),
    'validation': ('INVALID_FUNCTION_NAME', 'Use a valid SKILL identifier.', '$.name'),
    'policy': ('POLICY_DENIED', 'Check the write policy before changing this request.', '$.allow_write'),
    'signature_probe': ('SIGNATURE_PROBE_FAILED',
                        'Inspect the arglist error and original receipt; load the required context, then use refresh_signature.', '$.name'),
    'argument_binding': ('ARGUMENT_BINDING_FAILED',
                         'Check the named arguments against the signature. After redefining/loading the function, use refresh_signature; positional args are also supported.', '$.kwargs'),
    'encoding': ('VALUE_ENCODING_FAILED',
                 'Use strings, finite numbers, booleans, null or lists; Python also supports Symbol. Objects cannot be encoded as SKILL values.', '$.args / $.kwargs'),
    'execution': ('EXECUTION_FAILED',
                  'Inspect the original errors, outcome and receipt. Do not resubmit automatically; reconcile the original operation first.', '$.name'),
}


def call_failure(stage, function, message, *, code=None, metadata=None):
    default_code, action, path = _PHASES[stage]
    if code == 'WRITE_NEEDS_EXPLICIT_ALLOW':
        action = 'If this intended write is authorized, submit with allow_write=true; otherwise use a read-only operation.'
    elif code == 'WRITE_NOT_ALLOWLISTED':
        action = 'This write is outside the generic allowlist; allow_write=true cannot enable it. Review the policy or choose a supported engineering API.'
    execution = 'not_started'
    if stage == 'execution':
        metadata = metadata or {}
        execution = ('not_started' if metadata.get('request_sent') is False
                     else metadata.get('outcome', 'unknown'))
        if execution not in ('not_started', 'completed'):
            execution = 'unknown'
        if execution == 'not_started':
            action = 'The original evidence says the request was not sent/started. Resolve the reported preflight error before submitting again.'
        elif execution == 'completed':
            action = 'The original request completed with an error. Inspect errors and side effects before deciding on another call; completion does not mean success.'
    return {'code': code or default_code, 'stage': stage, 'function': function,
            'path': path, 'message': message,
            'target_execution': execution,
            'next_action': action}


def error_response(response, detail, tool):
    """Add diagnostics without replacing original transport evidence."""
    detail = dict(detail, tool=tool)
    body = response.setdefault('structuredContent', {})
    if detail['stage'] == 'execution':
        detail.update(call_failure('execution', detail['function'], detail['message'],
                                   metadata=body.get('metadata')))
    body['fusion_error'] = detail
    if detail['stage'] in ('connection_setup', 'validation', 'policy', 'argument_binding', 'encoding'):
        body['outcome'] = 'not_started'
    response.update(isError=True, ttlMs=0)
    response['content'].append({'type': 'text', 'text': json.dumps({'fusion_error': detail}, ensure_ascii=False)})
    return response
