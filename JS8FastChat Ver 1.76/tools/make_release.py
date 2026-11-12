#!/usr/bin/env python3
"""make_release.py -- build the JS8FastChat release packages, safely.

    python3 tools/make_release.py [output_folder] [--platform=all|windows|linux]
                                                        (default: one folder up, all)

WHAT EACH --platform MAKES
  all      JS8FastChat_Ver_<v>_build_<tag>.zip           both systems' files.
           The backup, and the build source on either machine.
  linux    JS8FastChat_Ver_<v>_build_<tag>_Linux.tar.gz  SEND THIS TO LINUX USERS.
           Linux files only. A .tar.gz, like the other apps, because it keeps
           the "executable" mark on the .sh scripts (a zip made on Windows
           loses it).
  windows  JS8FastChat_Ver_<v>_build_<tag>_Windows.zip   Windows files only.
The program code is identical in all three.

HOW TO BUILD THE LINUX PACKAGE

  On KW3KW, in PowerShell:
    cd "C:\\Users\\KW3KW\\Documents\\Claude AI\\JS8FastChat 1.76\\Build\\Windows\\JS8FastChat Ver 1.76"
    python tools\\make_release.py "C:\\Users\\KW3KW\\Documents\\Claude AI\\JS8FastChat 1.76\\Build\\Linux" --platform=linux

  or on the Linux PC, in a terminal:
    cd ~/Applications/JS8FastChat*
    python3 tools/make_release.py ~/Downloads --platform=linux

  Expect the last line to read "Packed NN files into ..._Linux.tar.gz".

HOW A LINUX USER INSTALLS IT

  On the Linux PC, in a terminal:
    F="$(ls -t ~/Downloads/JS8FastChat_Ver_*_Linux.tar.gz | head -n 1)"
    mkdir -p ~/Applications && tar -xzf "$F" -C ~/Applications
    sh ~/Applications/"$(tar -tzf "$F" | head -n 1 | cut -d/ -f1)"/linux_install.sh
  A later version installs with the same three lines (close JS8FastChat first).

Why this script exists: zips used to be made by hand, and the 1.75.2 folder that
went to the Linux PC on 2026-10-25 carried js8fastchat_tx_history_3_3.json --
the operator's own sent-message log -- as well as build/ and a Windows .exe.
This script is now the only way a release package is made. It:

  1. checks the version agrees in __init__.py, constants.py, APP_BUILD and the
     .iss, and that shared_resolver.py still matches JS8Map's copy if that tree
     is beside this one;
  2. checks every .sh file has Linux (LF) line endings;
  3. runs the unit tests if a tests/ folder exists (skip with --no-tests);
  4. packs the folder, LEAVING OUT operator data and personal files;
  5. re-opens the package and deletes it if any of them got in anyway, or if a
     required file is missing.

The folder inside every package is named "JS8FastChat Ver <version>", so
testers unpack over the same folder and keep their settings. Standard library
only; works on Windows and Linux.
"""
import fnmatch
import glob
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import zipfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Never shipped. Matched against each path's file name AND each folder name.
EXCLUDE_NAMES = [
    # operator data
    '*.db', '*.db-wal', '*.db-shm', '*-wal', '*-shm', '*.db-journal',
    'mjs_js8fastchat_*.json', 'mjs_js8maps_*.json',
    'js8fastchat_layout_*.json', 'js8fastchat_tx_history_*.json',
    'js8fastchat_capture_*.json', 'js8fastchat_intent.json',
    'js8fastchat_alive.json', 'js8map_server.json',
    'js8map_raise_fastchat.json', 'js8map_outgoing_tx.json',
    '*.log', '*.tmp',
    # build output and caches
    '__pycache__', '*.pyc', 'build', 'dist', 'installer_output',
    '*.zip', '*.tar.gz',
    # personal: folder icon that points at the developer's own paths
    'desktop.ini', 'twin_path.txt',
]
# Left out of a per-OS zip: the OTHER system's scripts and build files.
WINDOWS_ONLY = ['Run_JS8FastChat_Debug.bat', 'Build_JS8FastChat.bat',
                'JS8FastChat.iss', 'JS8FastChat.spec', 'JS8FastChat_icon.ico',
                'Launch_JS8FastChat.vbs', 'Launch_FastChat_Popup.vbs']
LINUX_ONLY = ['debug.sh', 'linux_install.sh', 'update_linux.sh',
              'install_linux_launcher.sh']

# Must never be in a zip, whatever the list above says.
FORBIDDEN = re.compile(
    r'(^|/)(mjs_js8fastchat_[^/]*\.json|mjs_js8maps_[^/]*\.json'
    r'|js8fastchat_(layout|tx_history|capture)_[^/]*\.json'
    r'|js8fastchat_(intent|alive)\.json|[^/]*\.db(-wal|-shm)?|[^/]*\.log)$')


def _excluded(rel, extra=()):
    parts = rel.replace('\\', '/').split('/')
    pats = list(EXCLUDE_NAMES) + list(extra)
    return any(fnmatch.fnmatch(p, pat) for p in parts for pat in pats)


def _read(path, pattern):
    with open(os.path.join(APP, path), encoding='utf-8') as f:
        m = re.search(pattern, f.read(), re.M)
    if not m:
        sys.exit('STOP: could not read %s from %s' % (pattern, path))
    return m.group(1)


def _twin_check():
    """shared_resolver.py must stay byte-identical to JS8Map's copy.

    The other app's tree is read from tools/twin_path.txt when that file
    exists: one line, either the folder that holds "JS8Map Spotter V...",
    that folder itself, or the shared_resolver.py file. Without the pointer
    only folders beside this one are searched. A pointer that finds nothing is
    a STOP, not a note -- a check that quietly stopped working is worse than
    no check."""
    mine = os.path.join(APP, 'js8fastchat', 'shared_resolver.py')
    norm = lambda p: hashlib.sha256(
        open(p, 'rb').read().replace(b'\r\n', b'\n')).hexdigest()
    pats = ['shared_resolver.py',
            'JS8Map Spotter V*/shared_resolver.py',
            'JS8Map Ver */shared_resolver.py',
            '*/JS8Map Spotter V*/shared_resolver.py',
            '*/JS8Map Ver */shared_resolver.py']
    pointer = os.path.join(APP, 'tools', 'twin_path.txt')
    told, roots = None, []
    if os.path.isfile(pointer):
        with open(pointer, encoding='utf-8') as f:
            for line in f:
                line = line.strip().strip('"')
                if line and not line.startswith('#'):
                    told = line
                    break
    roots = [told] if told else [os.path.dirname(APP),
                                 os.path.dirname(os.path.dirname(APP))]
    found = []
    for root in roots:
        if os.path.isfile(root):
            found = [root]
        else:
            for pat in pats:
                found = glob.glob(os.path.join(root, pat))
                if found:
                    break
        if found:
            break
    if not found:
        if told:
            sys.exit('STOP: tools/twin_path.txt points at\n      %s\n'
                     '      but no shared_resolver.py was found there. Fix that '
                     'line or delete the file.' % told)
        print('NOTE: no JS8Map tree found beside this one, so the shared_resolver.py '
              'twin was not checked.')
        return
    other = max(found, key=os.path.getmtime)
    if norm(other) != norm(mine):
        sys.exit('STOP: shared_resolver.py differs from\n      %s\n'
                 '      The twin check exists to catch exactly this: the two apps '
                 'must carry the same file.' % other)
    print('Twin: shared_resolver.py matches %s -- OK'
          % os.path.basename(os.path.dirname(other)))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out_dir = os.path.abspath(args[0]) if args else os.path.dirname(APP)
    plat = 'all'
    for a in sys.argv[1:]:
        if a.startswith('--platform='):
            plat = a.split('=', 1)[1].lower()
    if plat not in ('all', 'windows', 'linux'):
        sys.exit('STOP: --platform must be all, windows or linux')
    extra = LINUX_ONLY if plat == 'windows' else WINDOWS_ONLY if plat == 'linux' else []
    os.makedirs(out_dir, exist_ok=True)

    version = _read('js8fastchat/__init__.py', r'^APP_VERSION\s*=\s*"([^"]+)"')
    v_const = _read('js8fastchat/constants.py', r'^APP_VERSION\s*=\s*"([^"]+)"')
    build = _read('js8fastchat/constants.py', r'^APP_BUILD\s*=\s*"([^"]+)"')
    # JS8FastChat.iss is Windows-only, so a Linux copy of this tree does not
    # have it. Check it when it is there; say so when it is not.
    _iss = os.path.join(APP, 'JS8FastChat.iss')
    v_iss = _read('JS8FastChat.iss', r'^AppVersion=(.+?)\s*$') if os.path.isfile(_iss) else None
    m = re.match(r'^(.+?)-JS8MapChat-Ver-(.+)$', build)
    if not m:
        sys.exit('STOP: APP_BUILD "%s" is not <tag>-JS8MapChat-Ver-<version>' % build)
    tag, v_build = m.group(1), m.group(2)
    bad = {'constants.py': v_const, 'APP_BUILD': v_build}
    if v_iss is not None:
        bad['JS8FastChat.iss'] = v_iss
    bad = {k: v for k, v in bad.items() if v != version}
    if bad:
        sys.exit('STOP: __init__.py says %s but %s' % (
            version, ', '.join('%s says %s' % (k, v) for k, v in sorted(bad.items()))))
    print('Version: %s   build tag: %s%s'
          % (version, tag, '' if v_iss is not None else '   (no .iss here -- Linux copy)'))

    folder = 'JS8FastChat Ver ' + version
    if os.path.basename(APP) != folder:
        print('NOTE: this folder is "%s"; the zip will still contain "%s".'
              % (os.path.basename(APP), folder))
    _twin_check()

    if '--no-tests' not in sys.argv and os.path.isdir(os.path.join(APP, 'tests')):
        r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
                           cwd=APP, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit('STOP: tests failed --\n' + r.stderr[-2000:])
        print('Tests:', (r.stderr.strip().splitlines() or ['?'])[-1])

    # Collect the files first, so the zip and the tar carry exactly the same set.
    left_out = []
    files_in = []
    for root, dirs, files in os.walk(APP):
        rel_root = os.path.relpath(root, APP)
        dirs[:] = sorted(d for d in dirs
                         if not _excluded(os.path.normpath(os.path.join(rel_root, d)), extra))
        for fn in sorted(files):
            rel = os.path.normpath(os.path.join(rel_root, fn)).replace('\\', '/')
            if _excluded(rel, extra):
                left_out.append(rel)
            else:
                files_in.append(rel)
    count = len(files_in)

    # .sh files must be LF, or Linux reports "/bin/sh^M: bad interpreter".
    crlf = [r for r in files_in if r.endswith('.sh')
            and b'\r\n' in open(os.path.join(APP, r), 'rb').read()]
    if crlf:
        sys.exit('STOP: Windows (CRLF) line endings in ' + ', '.join(crlf)
                 + '\n      Convert them to LF and run this again.')

    suffix = {'all': '', 'windows': '_Windows', 'linux': '_Linux'}[plat]
    ext = '.tar.gz' if plat == 'linux' else '.zip'
    name = 'JS8FastChat_Ver_%s_build_%s%s%s' % (version, tag, suffix, ext)
    out = os.path.join(out_dir, name)

    if ext == '.zip':
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for rel in files_in:
                z.write(os.path.join(APP, rel), folder + '/' + rel)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
    else:
        # Linux package. Set the marks here rather than trusting the disk:
        # Windows has no "executable" mark to copy. Scripts 755, the rest 644,
        # folders 755, and no owner name from the build PC.
        def _clean(ti):
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ''
            if ti.isdir() or ti.name.endswith('.sh'):
                ti.mode = 0o755
            else:
                ti.mode = 0o644
            return ti
        with tarfile.open(out, 'w:gz') as t:
            t.add(APP, folder, recursive=False, filter=_clean)
            made = set()
            for rel in files_in:
                parts = rel.split('/')[:-1]
                for i in range(1, len(parts) + 1):
                    d = '/'.join(parts[:i])
                    if d not in made:
                        made.add(d)
                        t.add(os.path.join(APP, d), folder + '/' + d,
                              recursive=False, filter=_clean)
                t.add(os.path.join(APP, rel), folder + '/' + rel, filter=_clean)
        with tarfile.open(out, 'r:gz') as t:
            members = t.getmembers()
            names = [m.name for m in members if m.isfile()]
            not_exec = [m.name for m in members
                        if m.name.endswith('.sh') and not m.mode & 0o100]
        if not_exec:
            os.remove(out)
            sys.exit('STOP: scripts not marked executable: ' + ', '.join(not_exec)
                     + ', so the package was deleted')

    # Re-opened above; now check what actually went in.
    leaked = [n for n in names if FORBIDDEN.search(n)]
    linux = ['debug.sh', 'tools/install_linux_launcher.sh', 'update_linux.sh',
             'linux_install.sh']
    win = ['Run_JS8FastChat_Debug.bat']
    need = {'windows': win, 'linux': linux, 'all': win + linux}[plat] + [
        'JS8FastChat.py', 'js8fastchat/__init__.py', 'js8fastchat/constants.py',
        'js8fastchat/shared_resolver.py', 'js8fastchat_icon.png']
    missing = [n for n in need if folder + '/' + n not in names]
    if leaked:
        os.remove(out)
        sys.exit('STOP: operator data got into the package, so it was deleted: '
                 + ', '.join(leaked))
    if missing:
        os.remove(out)
        sys.exit('STOP: the package is missing ' + ', '.join(missing)
                 + ', so it was deleted')

    print('Left out (operator data / personal):', ', '.join(left_out) or 'nothing')
    print('Packed %d files into %s' % (count, out))


if __name__ == '__main__':
    main()
