#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import shutil
import urllib.request
import urllib.error
import subprocess
from html import unescape
from typing import Tuple, Set, Dict

DOWNLOAD_BASE = os.path.join(os.path.expanduser('~'), 'Downloads', 'VirtualBox')
VB_BASE_URL = 'https://download.virtualbox.org/virtualbox'
LATEST_STABLE_URL = f'{VB_BASE_URL}/LATEST-STABLE.TXT'
ORACLE_KEY_URL = 'https://www.virtualbox.org/download/oracle_vbox_2016.asc'
ORACLE_KEYRING = '/usr/share/keyrings/oracle-virtualbox-2016.gpg'
ORACLE_LIST = '/etc/apt/sources.list.d/virtualbox.list'
ORACLE_APT = 'https://download.virtualbox.org/virtualbox/debian'


def info(msg: str):
    print(f"[INFO] {msg}")


def warn(msg: str):
    print(f"[WARN] {msg}")


def err(msg: str):
    print(f"[ERR ] {msg}", file=sys.stderr)


def run(cmd, check=True, input_text=None):
    info(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        input=input_text,
    )


def http_get(url: str) -> bytes:
    info(f"GET {url}")
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/8 qodo-installer'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def http_get_text(url: str, encoding='utf-8') -> str:
    return http_get(url).decode(encoding, errors='replace')


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def detect_distro_codename() -> Tuple[str, str]:
    distro_id = ''
    codename = ''
    try:
        with open('/etc/os-release', 'r', encoding='utf-8') as f:
            data = f.read()
        m = re.search(r'^ID=([^\n]+)', data, re.MULTILINE)
        if m:
            distro_id = m.group(1).strip().strip('"')
        m = re.search(r'^UBUNTU_CODENAME=([^\n]+)', data, re.MULTILINE)
        if m:
            codename = m.group(1).strip()
        if not codename:
            m = re.search(r'^VERSION_CODENAME=([^\n]+)', data, re.MULTILINE)
            if m:
                codename = m.group(1).strip()
    except FileNotFoundError:
        pass
    if not codename:
        try:
            out = subprocess.check_output(['lsb_release', '-cs'], text=True).strip()
            codename = out
        except Exception:
            pass
    if not distro_id:
        try:
            out = subprocess.check_output(['lsb_release', '-is'], text=True).strip().lower()
            distro_id = out
        except Exception:
            pass
    if not codename:
        warn('Не удалось определить кодовое имя дистрибутива, использую jammy по умолчанию')
        codename = 'jammy'
    return distro_id, codename


def parse_directory_links(html: str) -> Set[str]:
    links = set()
    # Extract hrefs from HTML anchors
    for href in re.findall(r'href\s*=\s*"([^"]+)"', html, re.IGNORECASE):
        href = unescape(href).strip()
        if href and not href.endswith('/'):
            links.add(href)
    # Also try to extract plain filenames from preformatted indexes
    for m in re.finditer(r'\s([A-Za-z0-9][^\s]+\.(?:deb|run|vbox-extpack|iso))\s', html):
        links.add(m.group(1))
    return links


def parse_checksum_filenames(text: str) -> Set[str]:
    files = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # SHA256SUMS/MD5SUMS usually: "<hash> <filename>"
        parts = line.split()
        if len(parts) >= 2:
            files.add(parts[-1])
    return files


def get_latest_stable_version() -> str:
    # Try LATEST-STABLE.TXT first
    try:
        ver = http_get_text(LATEST_STABLE_URL).strip()
        if re.match(r'^\d+\.\d+\.\d+$', ver):
            return ver
        warn(f"LATEST-STABLE.TXT содержит некорректную версию: {ver}")
    except Exception as e:
        warn(f"Не удалось прочитать LATEST-STABLE.TXT: {e}")
    # Fallback: parse root listing and take max semver
    html = http_get_text(VB_BASE_URL + '/')
    dirs = re.findall(r'href\s*=\s*"(\d+\.\d+\.\d+)/"', html)
    if not dirs:
        raise RuntimeError('Не удалось определить последнюю стабильную версию VirtualBox')
    def ver_key(s: str):
        return tuple(int(x) for x in s.split('.'))
    latest = max(dirs, key=ver_key)
    info(f"Fallback: определена версия из индекса: {latest}")
    return latest


def build_file_inventory(version: str) -> Set[str]:
    base = f"{VB_BASE_URL}/{version}/"
    html = http_get_text(base)
    files = parse_directory_links(html)
    # Merge with SHA256SUMS/MD5SUMS content filenames for robustness
    for name in ('SHA256SUMS', 'MD5SUMS'):
        try:
            txt = http_get_text(base + name)
            files |= parse_checksum_filenames(txt)
        except Exception:
            pass
    return files


def find_artifacts(version: str, distro_id: str, codename: str, arch: str = 'amd64', strict_codename: bool = True) -> Dict[str, str]:
    base = f"{VB_BASE_URL}/{version}/"
    files = build_file_inventory(version)

    # Normalize to filenames
    file_set = set(f.strip('/') for f in files if f and not f.endswith('/'))

    # Extension Pack
    extpack_url = None
    ext_exact_vm = f"Oracle_VM_VirtualBox_Extension_Pack-{version}.vbox-extpack"
    ext_exact_no_vm = f"Oracle_VirtualBox_Extension_Pack-{version}.vbox-extpack"
    if ext_exact_vm in file_set:
        extpack_url = base + ext_exact_vm
    elif ext_exact_no_vm in file_set:
        extpack_url = base + ext_exact_no_vm
    else:
        ext_regex = re.compile(rf"^Oracle_(?:VM_)?VirtualBox_Extension_Pack-{re.escape(version)}[a-z]?\\.vbox-extpack$")
        cand = sorted([fn for fn in file_set if ext_regex.match(fn)])
        if cand:
            extpack_url = base + cand[-1]

    # ISO
    iso_url = None
    iso_exact = f"VBoxGuestAdditions_{version}.iso"
    if iso_exact in file_set:
        iso_url = base + iso_exact
    else:
        iso_regex = re.compile(rf"^VBoxGuestAdditions_{re.escape(version)}[a-z]?\\.iso$")
        cand = sorted([fn for fn in file_set if iso_regex.match(fn)])
        if cand:
            iso_url = base + cand[-1]

    # .deb selection
    deb_url = None
    if distro_id.lower() == 'ubuntu':
        # Strictly pick only this codename for Ubuntu by suffix match (robust to name variants)
        suffixes = [
            f"~Ubuntu~{codename}_{arch}.deb",
            f"_Ubuntu_{codename}_{arch}.deb",
        ]
        candidates = sorted([
            fn.strip()
            for fn in file_set
            if fn.endswith('.deb') and any(fn.endswith(suf) for suf in suffixes)
        ])
        if candidates:
            # Prefer canonical names starting with 'virtualbox-7.1_' or 'virtualbox-'
            preferred = [fn for fn in candidates if fn.startswith('virtualbox-7.1_') or fn.startswith('virtualbox-')]
            chosen = (sorted(preferred) or candidates)[-1]
            deb_url = base + chosen
        elif strict_codename:
            # Deep diagnostics: show what Ubuntu debs exist
            ubuntu_debs = sorted([fn for fn in file_set if fn.endswith('.deb') and 'Ubuntu' in fn and fn.endswith(f'_{arch}.deb')])
            raise RuntimeError(
                "Не найден точный пакет для Ubuntu codename='{}' среди файлов версии {}. Доступные Ubuntu deb: {}".format(
                    codename, version, ', '.join(ubuntu_debs) or 'нет'
                )
            )
    else:
        # Non-Ubuntu: do not enforce codename, choose any virtualbox amd64 deb if exists
        any_deb = sorted([fn for fn in file_set if fn.endswith('.deb') and fn.endswith(f'_{arch}.deb') and 'virtualbox' in fn.lower()])
        if any_deb:
            deb_url = base + any_deb[-1]

    return {
        'extpack_url': extpack_url,
        'iso_url': iso_url,
        'deb_url': deb_url,
    }


def download(url: str, dest_path: str):
    ensure_dir(os.path.dirname(dest_path))
    info(f"Скачивание {url} -> {dest_path}")
    tmp_path = dest_path + '.part'
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp_path, 'wb') as f:
        shutil.copyfileobj(resp, f)
    os.replace(tmp_path, dest_path)


def apt_update():
    run(['sudo', 'apt-get', 'update', '-y'])


def apt_install_local_deb(deb_path: str):
    run(['sudo', 'apt-get', 'install', '-y', deb_path])


def ensure_kernel_build_deps():
    headers = f"linux-headers-{os.uname().release}"
    run(['sudo', 'apt-get', 'install', '-y', 'dkms', 'build-essential', headers], check=False)


def add_user_to_vboxusers():
    user = os.environ.get('SUDO_USER') or os.environ.get('USER') or ''
    if not user:
        warn('Не удалось определить имя пользователя для добавления в vboxusers')
        return
    try:
        out = subprocess.check_output(['id', '-nG', user], text=True)
        if 'vboxusers' in out.split():
            info(f"Пользователь {user} уже в группе vboxusers")
            return
    except Exception:
        pass
    run(['sudo', 'usermod', '-aG', 'vboxusers', user], check=False)


def vbox_config():
    if shutil.which('/sbin/vboxconfig'):
        run(['sudo', '/sbin/vboxconfig'], check=False)


def vbox_version() -> str:
    if not shutil.which('VBoxManage'):
        return ''
    try:
        out = subprocess.check_output(['VBoxManage', '--version'], text=True).strip()
        return out
    except Exception:
        return ''


def install_extpack(extpack_path: str) -> bool:
    if not shutil.which('VBoxManage'):
        warn('VBoxManage не найден, пропускаю установку Extension Pack')
        return False
    cp = run(['sudo', 'VBoxManage', 'extpack', 'install', '--replace', extpack_path], check=False, input_text='y\n')
    return cp.returncode == 0


def extpack_installed_matches(version: str) -> bool:
    if not shutil.which('VBoxManage'):
        return False
    try:
        out = subprocess.check_output(['VBoxManage', 'list', 'extpacks'], text=True)
    except Exception:
        return False
    if not out or 'No extension packs installed' in out:
        return False
    for line in out.splitlines():
        s = line.strip()
        if s.lower().startswith('version:'):
            v = s.split(':', 1)[1].strip()
            if v.startswith(version):
                return True
    return False


def safe_unlink(path: str):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
            info(f"Удалён файл: {path}")
    except Exception as e:
        warn(f"Не удалось удалить {path}: {e}")


def main():
    ensure_dir(DOWNLOAD_BASE)

    distro_id, codename = detect_distro_codename()
    info(f"Определён дистрибутив: {distro_id or 'unknown'}, codename: {codename}")

    # Latest stable version with robust fallback
    version = get_latest_stable_version()
    info(f"Последняя стабильная версия VirtualBox: {version}")

    # Strict selection of Ubuntu jammy deb
    artifacts = find_artifacts(version, distro_id, codename, arch='amd64', strict_codename=True)

    # Sanity log
    if artifacts['deb_url']:
        info(f"Найден .deb: {artifacts['deb_url']}")
    if artifacts['extpack_url']:
        info(f"Найден Extension Pack: {artifacts['extpack_url']}")
    if artifacts['iso_url']:
        info(f"Найден Guest Additions ISO: {artifacts['iso_url']}")

    if distro_id.lower() == 'ubuntu' and not artifacts['deb_url']:
        raise RuntimeError('Не найден Ubuntu .deb для вашего codename, отмена вместо выбора других сборок.')

    # Download files
    deb_path = None
    if artifacts['deb_url']:
        deb_name = os.path.basename(artifacts['deb_url'])
        deb_path = os.path.join(DOWNLOAD_BASE, deb_name)
        download(artifacts['deb_url'], deb_path)

    extpack_path = None
    if artifacts['extpack_url']:
        extpack_name = os.path.basename(artifacts['extpack_url'])
        extpack_path = os.path.join(DOWNLOAD_BASE, extpack_name)
        download(artifacts['extpack_url'], extpack_path)

    iso_path = None
    if artifacts['iso_url']:
        iso_name = os.path.basename(artifacts['iso_url'])
        iso_path = os.path.join(DOWNLOAD_BASE, iso_name)
        download(artifacts['iso_url'], iso_path)

    # Install only from Ubuntu jammy deb (no cross-codename fallback)
    deb_installed_ok = False
    extpack_installed_ok = False
    apt_update()
    ensure_kernel_build_deps()
    if deb_path:
        apt_install_local_deb(deb_path)
        deb_installed_ok = True
    else:
        raise RuntimeError('Нет .deb для установки.')

    # Post-install
    vbox_config()
    add_user_to_vboxusers()

    vb_ver = vbox_version()
    info(f"Установленный VBoxManage --version: {vb_ver or 'не определена'}")

    if extpack_path and shutil.which('VBoxManage'):
        install_extpack(extpack_path)
        extpack_installed_ok = extpack_installed_matches(version)

    # Cleanup downloaded files that were successfully installed
    if deb_path and deb_installed_ok:
        safe_unlink(deb_path)
    if extpack_path and extpack_installed_ok:
        safe_unlink(extpack_path)

    print('\n==== Итоги ====')
    if deb_path:
        print(f"VirtualBox установлен из .deb: {deb_path}")
        if deb_installed_ok:
            print("Скачанный .deb удалён после установки")
    if extpack_path:
        print(f"Extension Pack: {extpack_path}")
        if extpack_installed_ok:
            print("Скачанный Extension Pack удалён после установки")
    else:
        print("Extension Pack: не найден/не установлен")
    if iso_path:
        print(f"Guest Additions ISO: {iso_path}")
    else:
        print("Guest Additions ISO: не найден")
    print(f"Каталог загрузок: {DOWNLOAD_BASE}")
    print("Для применения членства в группе vboxusers перелогиньтесь.")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        err('Прервано пользователем')
        sys.exit(130)
    except Exception as e:
        err(str(e))
        sys.exit(1)
