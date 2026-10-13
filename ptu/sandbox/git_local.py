"""Bounded Git worktree operations for the no-fork workstation proxy."""

from __future__ import annotations

import errno
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from ptu.common.binaries import REAL_GIT
from .runner import available


_BRANCH = re.compile(r'[A-Za-z0-9_./-]+\Z')
_SAFE_KEYS = {
    'core.repositoryformatversion', 'core.filemode', 'core.bare',
    'core.logallrefupdates', 'core.ignorecase', 'core.precomposeunicode',
    'core.abbrev', 'core.quotepath', 'core.autocrlf', 'core.eol',
    'core.safecrlf', 'user.name', 'user.email', 'remote.origin.url',
    'remote.origin.fetch', 'merge.autostash', 'merge.conflictstyle',
    'merge.renames', 'rebase.autostash', 'rerere.enabled', 'gc.auto',
}
_DRIVER_ATTRIBUTE = re.compile(r'(^|\s)[!-]?(?:filter|merge)(?:=|\s|$)')
_REBASE_TODO = re.compile(
    r'(?:pick|reword|edit|squash|fixup|drop) [0-9a-fA-F]{4,64}(?:[ \t]+[^\r\n]*)?\Z')


def host_git_profile(work: Path, private_tmp: Path) -> str:
    """Allow Git to fork while confining its file I/O to the checkout."""
    from .profile import _quoted
    work, private_tmp = Path(work), Path(private_tmp)
    if (work.is_symlink() or private_tmp.is_symlink() or not work.is_dir()
            or not private_tmp.is_dir() or work in private_tmp.parents
            or private_tmp in work.parents):
        raise ValueError('E_GIT_LOCAL')
    parents = {Path('/')}
    for path in (work, private_tmp):
        parents.update(path.parents)
    metadata = ' '.join(f'(literal {_quoted(path)})' for path in sorted(parents, key=str))
    system = (
        '/System', '/usr/lib', '/usr/share', '/private/var/db/timezone/tz',
        '/Library/Frameworks/Python.framework/Versions/3.14',
        '/Library/Developer/CommandLineTools', '/dev/fd',
        str(REAL_GIT.parent.parent),
        str(Path('/opt/homebrew/opt/pcre2').resolve()),
        str(Path('/opt/homebrew/opt/gettext').resolve()),
    )
    reads = ' '.join(f'(subpath {_quoted(Path(path))})' for path in system)
    literals = ('/', '/usr/bin/true', '/usr/bin/env', '/usr/bin/git', '/usr/bin/ps',
                '/private/etc/hosts', '/private/etc/resolv.conf',
                '/dev', '/dev/null', '/dev/zero', '/dev/random', '/dev/urandom',
                '/opt', '/opt/homebrew', '/opt/homebrew/opt/pcre2',
                '/opt/homebrew/opt/gettext', '/opt/homebrew/opt/git',
                '/opt/homebrew/etc/gitconfig', str(REAL_GIT))
    reads += ' ' + ' '.join(f'(literal {_quoted(Path(path))})' for path in literals)
    return '\n'.join((
        '(version 1)', '(deny default)',
        f'(allow file-read-metadata {metadata})',
        f'(allow file-read* {reads} (subpath {_quoted(work)}) (subpath {_quoted(private_tmp)}))',
        f'(allow file-write* (subpath {_quoted(work)}) (subpath {_quoted(private_tmp)}) (literal "/dev/null"))',
        '(deny file-link)',
        f'(allow process-exec (literal {_quoted(REAL_GIT)}) (literal "/usr/bin/true"))',
        '(allow process-fork)',
    )) + '\n'


def _host_env(private_tmp: Path) -> dict[str, str]:
    return {'PATH': '/usr/bin:/bin:/opt/homebrew/bin', 'HOME': '/dev/null',
            'TMPDIR': str(private_tmp), 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_NO_REPLACE_OBJECTS': '1',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_EDITOR': '/usr/bin/true',
            'GIT_SEQUENCE_EDITOR': '/usr/bin/true', 'LANG': 'C'}


def _host_command(profile: str, *git_args: str, identity: dict | None = None) -> list[str]:
    overrides = [] if identity is None else [
        '-c', 'user.name=' + identity['name'], '-c', 'user.email=' + identity['email']]
    return ['/usr/bin/sandbox-exec', '-p', profile, str(REAL_GIT),
            '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-c', 'core.sshCommand=/usr/bin/true',
            '-c', 'core.editor=/usr/bin/true',
            '-c', 'sequence.editor=/usr/bin/true',
            '-c', 'diff.external=', '-c', 'core.attributesFile=/dev/null',
            '-c', 'gc.auto=0', '-c', 'rerere.enabled=false',
            '-c', 'merge.autoStash=false', '-c', 'rebase.autoStash=false',
            '-c', 'commit.gpgSign=false', *overrides, *git_args]


def _validated_identity(identity: object) -> dict | None:
    if identity is None:
        return None
    if (not isinstance(identity, dict) or set(identity) != {'name', 'email'}
            or not isinstance(identity['name'], str)
            or not isinstance(identity['email'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}', identity['name'])
            or not re.fullmatch(r'[A-Za-z0-9._+-]+@[A-Za-z0-9.-]{1,100}', identity['email'])):
        raise ValueError('E_GIT_ARGS')
    return identity


def _config_rows(work: Path, private_tmp: Path, profile: str) -> list[tuple[str, str]]:
    try:
        done = subprocess.run(_host_command(profile, 'config', '--file',
                              str(work / '.git' / 'config'), '--list', '--null',
                              '--no-includes'), cwd=work, env=_host_env(private_tmp),
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('E_GIT_WORKTREE') from None
    if done.returncode or len(done.stdout) > 65536 or not done.stdout.endswith(b'\0'):
        raise ValueError('E_GIT_WORKTREE')
    try:
        rows = [row.split('\n', 1) for row in done.stdout[:-1].decode('utf-8').split('\0')]
    except UnicodeError:
        raise ValueError('E_GIT_WORKTREE') from None
    if not rows or any(len(row) != 2 for row in rows):
        raise ValueError('E_GIT_WORKTREE')
    return [(key, value) for key, value in rows]


def _validate_attributes(work: Path) -> None:
    for path in [work / '.git' / 'info' / 'attributes', *work.rglob('.gitattributes')]:
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 65536:
                raise ValueError('E_GIT_WORKTREE')
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError):
            raise ValueError('E_GIT_WORKTREE') from None
        if any(_DRIVER_ATTRIBUTE.search(line) for line in lines if not line.lstrip().startswith('#')):
            raise ValueError('E_GIT_WORKTREE')


def _validated_worktree(episode: Path, repo: str) -> Path:
    work = episode / 'ws' / 'work'
    git_dir = work / '.git'
    if (work.is_symlink() or not work.is_dir() or work.resolve() != work
            or git_dir.is_symlink() or not git_dir.is_dir()
            or any((git_dir / name).exists() for name in
                   ('commondir', 'config.worktree', 'objects/info/alternates'))):
        raise ValueError('E_GIT_WORKTREE')
    # The agent can edit its checkout but cannot make host Git follow a link
    # out of the episode or execute local config includes, filters, or drivers.
    if any(path.is_symlink() for path in work.rglob('*')):
        raise ValueError('E_GIT_WORKTREE')
    config = git_dir / 'config'
    if config.is_symlink() or not config.is_file() or config.stat().st_size > 65536:
        raise ValueError('E_GIT_WORKTREE')
    _validate_attributes(work)
    if not available():
        raise ValueError('E_GIT_LOCAL')
    with tempfile.TemporaryDirectory(prefix='.git-host-', dir=episode) as name:
        private_tmp = Path(name)
        profile = host_git_profile(work, private_tmp)
        rows = _config_rows(work, private_tmp, profile)
    origin_urls = []
    for key, value in rows:
        branch = re.fullmatch(r'branch\.(.+)\.(remote|merge)', key)
        if branch is not None:
            if not _BRANCH.fullmatch(branch[1]) or '..' in branch[1]:
                raise ValueError('E_GIT_WORKTREE')
        elif key not in _SAFE_KEYS:
            raise ValueError('E_GIT_WORKTREE')
        if key == 'remote.origin.url':
            origin_urls.append(value)
    if origin_urls != ['ptu://' + repo]:
        raise ValueError('E_GIT_WORKTREE')
    return work


def _arguments(command: str, args: object) -> list[str]:
    if (not isinstance(args, list) or len(args) > 4
            or any(not isinstance(arg, str) or len(arg) > 200 or '\x00' in arg
                   for arg in args)):
        raise ValueError('E_GIT_ARGS')
    if command == 'merge':
        if args not in (['origin/main'], ['--no-edit', 'origin/main'],
                        ['--abort'], ['--continue']):
            raise ValueError('E_GIT_ARGS')
    elif command == 'rebase':
        if args not in (['origin/main'], ['--abort'], ['--continue'], ['--skip']):
            raise ValueError('E_GIT_ARGS')
    elif command == 'stash':
        if args not in ([], ['push'], ['push', '-u'], ['push', '--include-untracked'],
                        ['pop'], ['apply'], ['list'], ['drop'], ['clear'],
                        ['show'], ['create']) and not (
                len(args) == 3 and args[:2] == ['push', '-m'] and args[2]):
            raise ValueError('E_GIT_ARGS')
    else:
        raise ValueError('E_GIT_ARGS')
    return args


def _validate_rebase_state(work: Path) -> None:
    """Reject agent-editable sequencer instructions that can execute commands."""
    git_dir = work / '.git'
    for directory in (git_dir / 'rebase-merge', git_dir / 'rebase-apply'):
        if directory.is_symlink():
            raise ValueError('E_GIT_WORKTREE')
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError('E_GIT_WORKTREE')
        for name in ('git-rebase-todo', 'done'):
            path = directory / name
            if path.is_symlink():
                raise ValueError('E_GIT_WORKTREE')
            if not path.exists():
                continue
            try:
                if not path.is_file() or path.stat().st_size > 65536:
                    raise ValueError('E_GIT_WORKTREE')
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeError):
                raise ValueError('E_GIT_WORKTREE') from None
            if any(not _REBASE_TODO.fullmatch(line) for line in lines
                   if line.strip() and not line.lstrip().startswith('#')):
                raise ValueError('E_GIT_WORKTREE')


def _kill_and_verify_group(proc: subprocess.Popen) -> None:
    """Reap Git and kill any inherited process-group children before unlock."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        raise ValueError('E_GIT_LOCAL') from None
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise ValueError('E_GIT_LOCAL') from None
    if proc.stdout is not None:
        proc.stdout.close()
    # An orphaned group child can remain as a zombie while launchd/init reaps
    # it under load. Keep the episode lock until the group is truly gone.
    deadline = time.monotonic() + 15
    while True:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return
        except OSError as error:
            if error.errno == errno.ESRCH:
                return
            # Darwin can briefly report EPERM for a just-killed group while
            # its orphaned child is being reaped. It is not proof of exit:
            # keep the lock, retry the probe, and fail closed at the deadline.
            if error.errno != errno.EPERM:
                raise ValueError('E_GIT_LOCAL') from None
        if time.monotonic() >= deadline:
            raise ValueError('E_GIT_LOCAL')
        time.sleep(0.02)


def run_local_git(episode: Path, repo: str, command: str, args: object,
                  cancelled=None, identity=None) -> dict:
    work = _validated_worktree(episode, repo)
    arguments = _arguments(command, args)
    identity = _validated_identity(identity)
    if command == 'rebase':
        _validate_rebase_state(work)
    with tempfile.TemporaryDirectory(prefix='.git-host-', dir=episode) as name:
        private_tmp = Path(name)
        profile = host_git_profile(work, private_tmp)
        git = _host_command(profile, command, *arguments, identity=identity)
        try:
            proc = subprocess.Popen(git, cwd=work, env=_host_env(private_tmp),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        except OSError:
            raise ValueError('E_GIT_LOCAL') from None
        deadline = time.monotonic() + 30
        try:
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise ValueError('E_GIT_LOCAL')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('E_GIT_LOCAL')
                try:
                    output, _ = proc.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    if proc.poll() is not None:
                        # A child inherited the output pipe after Git exited.
                        raise ValueError('E_GIT_LOCAL')
                    continue
            return {'ok': True, 'exit_code': proc.returncode,
                    'output': output[:24000].decode('utf-8', 'replace')}
        finally:
            _kill_and_verify_group(proc)
