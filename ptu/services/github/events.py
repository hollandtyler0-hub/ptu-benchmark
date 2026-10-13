"""Deterministic world-authored GitHub mutations."""
from ptu.common.registry import ResourceUse
from . import ops


def _execute(name, args, state, clock, ids):
    author = args.get('author')
    if not author:
        raise ValueError('E_EVENT_AUTHOR')
    previous = state.get('acting_principal')
    state['acting_principal'] = author
    try:
        ops.execute(name, args, state, clock, ids)
    finally:
        if previous is None:
            state.pop('acting_principal', None)
        else:
            state['acting_principal'] = previous
    repo = args['repo']
    if name == 'comment_on_issue':
        ref = f"github:issue/{repo}#{args['issue_number']}"
    elif name == 'comment_on_pull_request':
        ref = f"github:pr/{repo}#{args['pull_number']}"
    elif name == 'create_or_update_file':
        ref = f"github:file/{repo}@{args['branch']}:{args['path']}"
        return [ResourceUse(ref, 'write'),
                ResourceUse(f"github:branch/{repo}#{args['branch']}", 'write')]
    else:
        ref = f"github:collaborators/{repo}"
    return [ResourceUse(ref, 'write')]


def _comment_issue(args, state, clock, ids):
    return _execute('comment_on_issue', args, state, clock, ids)


def _comment_pull(args, state, clock, ids):
    return _execute('comment_on_pull_request', args, state, clock, ids)


def _update_file(args, state, clock, ids):
    return _execute('create_or_update_file', args, state, clock, ids)


def _collaborator(args, state, clock, ids):
    return _execute('add_collaborator', args, state, clock, ids)


EVENT_OPS = {'comment_on_issue': _comment_issue, 'comment_on_pull_request': _comment_pull,
             'update_file': _update_file, 'add_collaborator': _collaborator}
