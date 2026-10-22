#!/usr/bin/env python3
"""Version-scoped fnOS OpenClaw compatibility installer. Python 3.9+."""
import argparse
import datetime
import difflib
import fcntl
import hashlib
import http.client
import json
import os
import platform
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / 'manifest.json').read_text())
APP = Path('/var/apps/trim.openclaw')
TARGET = (APP / 'target').resolve()
HOME = (APP / 'home').resolve()
DATA = HOME / 'data'
SERVER = TARGET / 'server/index.js'
WRAPPER = TARGET / 'bin/openclaw'
RUNTIME = DATA / 'runtime'
BUN = Path('/var/apps/bunjs/target/bin/bun')
NODE = RUNTIME / 'node'
SOCKET = TARGET / 'trim.openclaw.sock'
CONFIG = DATA / 'home/.openclaw/openclaw.json'
BACKUPS = HOME / 'compat-backups'
API = '/app/trim-openclaw/api'
USER = 'trim.openclaw'


def say(message):
    print(message, flush=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def apply_patch(original, patch):
    """Apply unified text hunks only when every source line matches exactly."""
    source = original.splitlines(True)
    lines = patch.splitlines(True)
    output, cursor, i = [], 0, 0
    while i < len(lines):
        match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[i])
        if not match:
            if lines[i].startswith(('--- ', '+++ ')):
                i += 1
                continue
            raise ValueError('Unexpected patch header')
        start = int(match[1]) - 1
        if start < cursor:
            raise ValueError('Overlapping patch hunks')
        output.extend(source[cursor:start])
        cursor = start
        old_count = new_count = 0
        i += 1
        while i < len(lines) and not lines[i].startswith('@@ '):
            line = lines[i]
            if line.startswith((' ', '-')):
                if cursor >= len(source) or source[cursor] != line[1:]:
                    raise ValueError('Patch source mismatch at line ' + str(cursor + 1))
                cursor += 1
                old_count += 1
            if line.startswith((' ', '+')):
                output.append(line[1:])
                new_count += 1
            if line[:1] not in (' ', '-', '+'):
                raise ValueError('Unsupported patch line')
            i += 1
        if old_count != int(match[2] or 1) or new_count != int(match[4] or 1):
            raise ValueError('Patch hunk length mismatch')
    output.extend(source[cursor:])
    return ''.join(output)


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(SOCKET))


def api(path, payload=None, timeout=180):
    connection = UnixHTTP('localhost', timeout=timeout)
    headers = {'X-Forwarded-For': '192.0.2.1'}
    body = None
    if payload is not None:
        body = json.dumps(payload)
        headers['Content-Type'] = 'application/json'
    try:
        connection.request('POST' if body is not None else 'GET', API + path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode('utf-8', errors='replace')
        if response.status != 200:
            raise RuntimeError('Management API returned HTTP ' + str(response.status))
        return json.loads(raw)
    finally:
        connection.close()


def action(name):
    connection = UnixHTTP('localhost', timeout=180)
    connection.request('POST', API + '/install', json.dumps({'action': name, 'instanceId': 'default'}),
                       {'Content-Type': 'application/json'})
    result = None
    try:
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('Management action HTTP ' + str(response.status))
        while True:
            line = response.readline()
            if not line:
                break
            if not line.startswith(b'data: '):
                continue
            try:
                item = json.loads(line[6:])
            except ValueError:
                continue
            if item.get('event') == 'error':
                raise RuntimeError('Management action failed; inspect local Gateway logs')
            if item.get('event') == 'complete':
                result = item
        if not result or not result.get('success'):
            raise RuntimeError('Management action did not report success')
        return result
    finally:
        connection.close()


def inspect():
    if platform.machine() != MANIFEST['architecture']:
        raise RuntimeError('Only x86_64 is supported')
    manifest = (APP / 'manifest').read_text()
    if not re.search(r'^version\s*=\s*0\.1\.5\s*$', manifest, re.M):
        raise RuntimeError('Only fnOS trim.openclaw 0.1.5 is supported')
    current = digest(SERVER.read_bytes())
    allowed = [MANIFEST['originalServerSha256'], MANIFEST['patchedServerSha256']]
    if current not in allowed:
        raise RuntimeError('Unknown management server build; refusing to overwrite it')
    if not BUN.is_file():
        raise RuntimeError('The fnOS Bun dependency is missing')
    package = json.loads((DATA / 'openclaw/node_modules/openclaw/package.json').read_text())
    version = package['version']
    if tuple(int(v) for v in version.split('.')[:3]) > (2026, 10, 5):
        raise RuntimeError('A newer core is installed; automatic downgrade is not supported')
    state = api('/instances')['instances']
    if len(state) != 1 or state[0].get('instanceId') != 'default' or state[0].get('systemMode'):
        raise RuntimeError('Only one default managed instance is supported')
    record = pwd.getpwnam(USER)
    pid = int((APP / 'var/app.pid').read_text())
    cmd = Path('/proc/' + str(pid) + '/cmdline').read_bytes()
    if b'/server/index.js' not in cmd or os.stat('/proc/' + str(pid)).st_uid != record.pw_uid:
        raise RuntimeError('The running monitor process could not be verified')
    env = dict(item.split('=', 1) for item in Path('/proc/' + str(pid) + '/environ').read_bytes().decode().split('\0') if '=' in item)
    return {'serverHash': current, 'core': version, 'running': bool(state[0].get('running')),
            'env': env}


def run_as_app(command, env, log, timeout=1800):
    record = pwd.getpwnam(USER)
    with open(log, 'ab') as stream:
        process = subprocess.run([str(v) for v in command], env=env, cwd=str(TARGET),
                                 user=record.pw_uid, group=record.pw_gid,
                                 extra_groups=os.getgrouplist(USER, record.pw_gid),
                                 stdout=stream, stderr=stream, timeout=timeout)
    if process.returncode:
        raise RuntimeError('Command failed; see ' + str(log))


def app_env(env):
    result = dict(env)
    result.update(HOME=str(DATA / 'home'), OPENCLAW_DATA_DIR=str(DATA),
                  OPENCLAW_CONFIG_PATH=str(CONFIG), OPENCLAW_HIDE_BANNER='1')
    result['PATH'] = ':'.join([str(RUNTIME), str(BUN.parent), '/var/apps/nodejs_v24/target/bin',
                               str(DATA / 'openclaw/node_modules/.bin'), '/usr/bin', '/bin'])
    return result


def cli(args, env, log):
    run_as_app([WRAPPER] + args, app_env(env), log)


def stop_monitor(env, log):
    run_as_app([APP / 'cmd/main', 'stop'], env, log, timeout=40)


def start_monitor(env):
    record = pwd.getpwnam(USER)
    try:
        current_pid = int((APP / 'var/app.pid').read_text())
        command = Path('/proc/' + str(current_pid) + '/cmdline').read_bytes()
        if b'/server/index.js' in command and os.stat('/proc/' + str(current_pid)).st_uid == record.pw_uid:
            api('/health', timeout=2)
            return
    except (OSError, ValueError, RuntimeError, http.client.HTTPException):
        pass
    env = app_env(env)
    env.update(STATIC_DIR=str(TARGET / 'ui'), SOUL_MD_SRC=str(APP / 'config/prompts/SOUL.md'),
               MONITOR_SOCKET_PATH=str(SOCKET), MONITOR_ACCESS_MODE='public',
               OPENCLAW_PATCHES_DIR=str(TARGET / 'vendor/openclaw-patches/dist'))
    # Preserve the original fnOS authentication policy and service user.
    with open(APP / 'var/info.log', 'ab', buffering=0) as log:
        process = subprocess.Popen([str(BUN), str(SERVER)], env=env, cwd=str(TARGET),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   user=record.pw_uid, group=record.pw_gid,
                                   extra_groups=os.getgrouplist(USER, record.pw_gid),
                                   start_new_session=True)
    (APP / 'var/app.pid').write_text(str(process.pid))
    os.chown(APP / 'var/app.pid', record.pw_uid, record.pw_gid)
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError('Management server exited; inspect info.log')
        try:
            api('/health', timeout=2)
            return
        except (OSError, ValueError, RuntimeError, http.client.HTTPException):
            time.sleep(.25)
    raise RuntimeError('Management server startup timed out')


def preserve_write(path, content):
    stat = path.stat()
    temporary = path.with_name(path.name + '.compat-tmp')
    temporary.write_bytes(content)
    os.chown(temporary, stat.st_uid, stat.st_gid)
    os.chmod(temporary, stat.st_mode & 0o777)
    os.replace(temporary, path)


def snapshot(state):
    needed = sum(p.stat().st_size for p in DATA.rglob('*') if p.is_file() and not p.is_symlink())
    if shutil.disk_usage(HOME).free < needed * 3 + 2 * 1024 ** 3:
        raise RuntimeError('Insufficient free space for backup and rollback')
    BACKUPS.mkdir(exist_ok=True, mode=0o700)
    os.chmod(BACKUPS, 0o700)
    backup = BACKUPS / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(mode=0o700)
    # Contains private local data. This directory is never part of the distributable package.
    (backup / 'state.json').write_text(json.dumps(state))
    os.chmod(backup / 'state.json', 0o600)
    for source, name in [(SERVER, 'server.js'), (WRAPPER, 'wrapper')]:
        shutil.copy2(source, backup / name)
    with tarfile.open(backup / 'data.tar', 'w') as archive:
        archive.add(DATA, arcname='data')
    os.chmod(backup / 'data.tar', 0o600)
    (backup / 'backup-complete').write_text('1\n')
    return backup


def node_runtime():
    RUNTIME.mkdir(exist_ok=True)
    record = pwd.getpwnam(USER)
    os.chown(RUNTIME, record.pw_uid, record.pw_gid)
    os.chmod(RUNTIME, 0o750)
    if NODE.exists():
        result = subprocess.run([str(NODE), '--version'], capture_output=True, text=True, check=True)
        if result.stdout.strip() == 'v' + MANIFEST['nodeVersion']:
            return
    version = MANIFEST['nodeVersion']
    for candidate in [Path('/usr/bin/node'), Path('/var/apps/nodejs_v24/target/bin/node')]:
        if candidate.is_file():
            probe = subprocess.run([str(candidate), '--version'], capture_output=True, text=True, timeout=15)
            if probe.returncode == 0 and probe.stdout.strip() == 'v' + version:
                shutil.copyfile(candidate, NODE)
                os.chown(NODE, record.pw_uid, record.pw_gid)
                NODE.chmod(0o755)
                return
    name = 'node-v' + version + '-linux-x64.tar.xz'
    base = 'https://nodejs.org/dist/v' + version + '/'
    with tempfile.TemporaryDirectory(prefix='fnos-node-') as temporary:
        archive = Path(temporary) / name
        urllib.request.urlretrieve(base + name, archive)
        sums = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=60).read().decode()
        expected = next(line.split()[0] for line in sums.splitlines() if line.endswith('  ' + name))
        if digest(archive.read_bytes()) != expected:
            raise RuntimeError('Node.js archive checksum mismatch')
        with tarfile.open(archive, 'r:xz') as tar:
            member = tar.getmember('node-v' + version + '-linux-x64/bin/node')
            NODE.write_bytes(tar.extractfile(member).read())
    record = pwd.getpwnam(USER)
    os.chown(NODE, record.pw_uid, record.pw_gid)
    NODE.chmod(0o755)


def write_wrapper():
    text = '#!/bin/bash\nset -e\n'
    values = app_env({})
    for key in ['PATH', 'HOME', 'OPENCLAW_DATA_DIR', 'OPENCLAW_CONFIG_PATH', 'OPENCLAW_HIDE_BANNER']:
        value = values[key]
        if key == 'PATH':
            value += ':$PATH'
        text += 'export ' + key + '="' + value + '"\n'
    text += 'exec "' + str(DATA / 'openclaw/node_modules/.bin/openclaw') + '" "$@"\n'
    preserve_write(WRAPPER, text.encode())


def normalize_config():
    config = json.loads(CONFIG.read_text())
    config.get('meta', {}).pop('lastTouchedAt', None)
    ui = config.get('gateway', {}).get('controlUi', {})
    ui.pop('allowInsecureAuth', None)
    ui.pop('dangerouslyDisableDeviceAuth', None)
    config.pop('cli', None)
    config.setdefault('update', {}).update(checkOnStart=False)
    config['update'].setdefault('auto', {})['enabled'] = False
    preserve_write(CONFIG, (json.dumps(config, indent=2, ensure_ascii=False) + '\n').encode())


def browser_setup(env, log):
    executable = RUNTIME / 'browsers/chromium-1248/chrome-linux64/chrome'
    if not executable.is_file():
        tools = RUNTIME / 'browser-installer'
        record = pwd.getpwnam(USER)
        tools.mkdir(exist_ok=True)
        os.chown(tools, record.pw_uid, record.pw_gid)
        install_env = app_env(env)
        install_env['PLAYWRIGHT_BROWSERS_PATH'] = str(RUNTIME / 'browsers')
        browsers = RUNTIME / 'browsers'
        browsers.mkdir(exist_ok=True)
        os.chown(browsers, record.pw_uid, record.pw_gid)
        run_as_app([BUN, 'add', '--cwd', tools, '--registry', 'https://registry.npmmirror.com',
                    'playwright@' + MANIFEST['playwrightVersion']], install_env, log)
        run_as_app([NODE, tools / 'node_modules/playwright/cli.js', 'install', 'chromium'], install_env, log)
    cli(['config', 'set', '--batch-json', json.dumps([
        {'path': 'browser.executablePath', 'value': str(executable)},
        {'path': 'browser.headless', 'value': True},
        {'path': 'browser.defaultProfile', 'value': 'openclaw'}])], env, log)


def restore(backup):
    backup = backup.resolve()
    if backup.parent != BACKUPS.resolve() or not (backup / 'backup-complete').is_file():
        raise RuntimeError('Not a completed backup in ' + str(BACKUPS))
    state = json.loads((backup / 'state.json').read_text())
    env = state['env']
    try:
        current = api('/instances')['instances'][0]
        if current.get('running'):
            action('stop')
    except (OSError, http.client.HTTPException):
        pass
    stop_monitor(env, backup / 'rollback.log')
    retained = backup / ('retained-data-' + str(time.time_ns()))
    DATA.rename(retained)
    # Locally-created trusted backup; tar preserves symlinks, modes, and numeric ownership.
    subprocess.run(['tar', '--numeric-owner', '-xpf', str(backup / 'data.tar'), '-C', str(HOME)], check=True)
    preserve_write(SERVER, (backup / 'server.js').read_bytes())
    preserve_write(WRAPPER, (backup / 'wrapper').read_bytes())
    start_monitor(env)
    if state['running']:
        action('start')
    say('Rollback complete. Data from before rollback retained at: ' + str(retained))


def verify(env, log):
    state = api('/instances')['instances'][0]
    if not state.get('running') or MANIFEST['openclawVersion'] not in (state.get('version') or ''):
        raise RuntimeError('Gateway version or running state check failed')
    cli(['health', '--json'], env, log)
    # Renderability checks: compressed headers, iframe policy, WASM policy, and real assets.
    connection = UnixHTTP('localhost', timeout=30)
    connection.request('GET', '/app/trim-openclaw/default/', headers={'X-Forwarded-For': '192.0.2.1'})
    response = connection.getresponse()
    html = response.read().decode()
    if response.status != 200 or response.getheader('Content-Encoding'):
        raise RuntimeError('Control UI encoding or HTTP status check failed')
    if response.getheader('X-Frame-Options') != 'SAMEORIGIN' or "'wasm-unsafe-eval'" not in response.getheader('Content-Security-Policy', ''):
        raise RuntimeError('Control UI iframe or terminal policy check failed')
    connection.close()
    assets = re.findall(r'(?:src|href)="([^"?#]+\.js)"', html)
    if not assets:
        raise RuntimeError('Control UI has no JavaScript entry point')
    connection = UnixHTTP('localhost', timeout=30)
    connection.request('GET', assets[0], headers={'X-Forwarded-For': '192.0.2.1'})
    response = connection.getresponse()
    if response.status != 200 or response.getheader('Content-Encoding'):
        raise RuntimeError('Control UI JavaScript download check failed')
    response.read()
    connection.close()


def install(dry_run=False, with_browser=True):
    state = inspect()
    say('Supported fnOS app: 0.1.5; current OpenClaw: ' + state['core'])
    source = SERVER.read_text()
    patched = source if state['serverHash'] == MANIFEST['patchedServerSha256'] else apply_patch(source, (HERE / 'payload/monitor.patch').read_text())
    if digest(patched.encode()) != MANIFEST['patchedServerSha256']:
        raise RuntimeError('Patched server checksum mismatch')
    if dry_run:
        say('Dry run OK. No files or services changed. Target: OpenClaw 2026.10.5, Weixin 2.4.10.')
        return
    env = state['env']
    stopped = False
    backup = None
    try:
        if state['running']:
            action('stop')
        stopped = True
        with tempfile.TemporaryDirectory(prefix='fnos-compat-stop-') as temporary:
            stop_monitor(env, Path(temporary) / 'stop.log')
        say('Creating complete private backup...')
        backup = snapshot(state)
        say('Backup: ' + str(backup))
        log = backup / 'install.log'
        log.touch(mode=0o600)
        with log.open('a') as stream: stream.write('Preparing Node.js runtime\n')
        node_runtime()
        write_wrapper()
        preserve_write(SERVER, patched.encode())
        run_as_app([BUN, 'build', SERVER, '--target', 'bun', '--external', '*', '--outfile', RUNTIME / 'monitor-syntax-check.js'], app_env(env), log)
        install_env = app_env(env)
        install_env.update(HOME=str(DATA / 'openclaw/.bun-home'), SHARP_IGNORE_GLOBAL_LIBVIPS='1',
                           BUN_INSTALL_CACHE_DIR=str(DATA / 'openclaw/.bun-home/.cache'))
        run_as_app([BUN, 'add', '--cwd', DATA / 'openclaw', '--registry', 'https://registry.npmmirror.com', 'openclaw@2026.10.5'], install_env, log)
        normalize_config()
        # Official offline repair migrates the shared and per-agent SQLite stores.
        cli(['doctor', '--fix', '--non-interactive'], env, log)
        plugin = DATA / 'home/.openclaw/npm/node_modules/@tencent-weixin/openclaw-weixin/package.json'
        if plugin.is_file() and json.loads(plugin.read_text()).get('version') != '2.4.10':
            plugin_env = app_env(env)
            plugin_env.update(npm_config_registry='https://registry.npmmirror.com', NPM_CONFIG_REGISTRY='https://registry.npmmirror.com')
            run_as_app([WRAPPER, 'plugins', 'update', '@tencent-weixin/openclaw-weixin@2.4.10'], plugin_env, log)
        if with_browser:
            browser_setup(env, log)
        normalize_config()
        start_monitor(env)
        action('start')
        verify(env, log)
        if with_browser:
            cli(['browser', 'start', '--json'], env, log)
        if not state['running']:
            action('stop')
        say('Installed and verified. Core pinned to 2026.10.5. Backup: ' + str(backup))
    except BaseException:
        if backup:
            with (backup / 'install-error.log').open('a') as stream:
                stream.write(traceback.format_exc())
        if backup and (backup / 'backup-complete').is_file():
            say('Installation failed; restoring the full backup...')
            try:
                restore(backup)
            except BaseException as error:
                say('Automatic rollback needs attention: ' + str(error))
                say('Keep this backup: ' + str(backup))
        elif stopped:
            start_monitor(env)
            if state['running']:
                action('start')
        raise


def check():
    state = inspect()
    config = json.loads(CONFIG.read_text())
    say('Management patch: ' + ('installed' if state['serverHash'] == MANIFEST['patchedServerSha256'] else 'not installed'))
    say('OpenClaw: ' + state['core'] + '; Gateway running: ' + str(state['running']))
    say('Auto update disabled: ' + str(config.get('update', {}).get('auto', {}).get('enabled') is False))
    package = json.loads((DATA / 'openclaw/package.json').read_text())
    say('Exact dependency pin: ' + str(package.get('dependencies', {}).get('openclaw') == '2026.10.5'))
    say('Browser executable configured: ' + str(bool(config.get('browser', {}).get('executablePath'))))
    if state['running'] and state['serverHash'] == MANIFEST['patchedServerSha256']:
        with tempfile.TemporaryDirectory(prefix='fnos-compat-check-') as temporary:
            verify(state['env'], Path(temporary) / 'health.log')
        say('Gateway health, Control UI assets, iframe and terminal policies: OK')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('install')
    command.add_argument('--dry-run', action='store_true')
    command.add_argument('--without-browser', action='store_true')
    commands.add_parser('check')
    command = commands.add_parser('rollback')
    command.add_argument('backup', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root on the fnOS host')
    os.umask(0o077)
    with open('/run/lock/fnos-openclaw-compat.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another compatibility installer is running')
        if args.command == 'install':
            install(args.dry_run, not args.without_browser)
        elif args.command == 'check':
            check()
        else:
            restore(args.backup)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        say('ERROR: ' + str(error))
        sys.exit(1)
