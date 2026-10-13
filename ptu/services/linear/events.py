"""Deterministic world-authored Linear mutations."""
from ptu.common.registry import ResourceUse
from . import ops


def _comment(args, state, clock, ids):
    if not args.get('author'):
        raise ValueError('E_EVENT_AUTHOR')
    ops.comment_on_issue({'identifier': args['identifier'], 'body': args['body']},
                         state, clock, ids, args['author'])
    return [ResourceUse(f"linear:issue/{args['identifier']}", 'write')]


def _update(args, state, clock, ids):
    ops.update_issue({key: value for key, value in args.items() if key != 'author'},
                     state, clock, ids, args.get('author', 'world'))
    return [ResourceUse(f"linear:issue/{args['identifier']}", 'write')]


EVENT_OPS = {'comment_on_issue': _comment, 'update_issue': _update}
