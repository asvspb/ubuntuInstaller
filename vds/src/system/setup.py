import os
import subprocess
import pwd
import socket
from pathlib import Path

import questionary
from rich.console import Console

from src.core.shell import run

console = Console()

def step(num: str, title: str):
    console.print(f"\n[cyan][{num}/9][/cyan] {title}")

def ok(msg: str):
    console.print(f"[green]✔[/green] {msg}")

def warn(msg: str):
    console.print(f"[yellow]⚠[/yellow] {msg}")

def die(msg: str):
    console.print(f"[red]✘ Ошибка:[/red] {msg}")
    raise SystemExit(1)


def clean_system():
    step("0", "🩺 Очистка системы от зависших пакетов...")
    run("dpkg --configure -a")
    run("apt-get --fix-broken install -y")
    run("apt-get remove -y --purge libnode*")
    run("apt-get autoremove -y")
    run("apt-get clean")
    ok("Система очищена")


def fix_tzdata():
    step("1", "⏱️  Превентивное исправление часовых поясов (tzdata)...")
    run("rm -f /etc/localtime /etc/timezone")
    with open("/etc/timezone", "w") as f:
        f.write("Etc/UTC\n")
    run("ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime")
    run("dpkg-reconfigure -f noninteractive tzdata")
    ok("Часовой пояс (UTC) установлен, ошибка tzdata предотвращена")


def update_system():
    step("2", "📦 Обновление списка пакетов и системы...")
    run("apt-get update -y")
    run("DEBIAN_FRONTEND=noninteractive apt-get upgrade -y")
    packages = [
        "git", "python3", "python3-pip", "python3-venv",
        "curl", "wget", "ca-certificates",
        "build-essential", "qrencode", "iptables", "iproute2", "ufw"
    ]
    run(f"DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(packages)}")
    ok("Базовые пакеты установлены")


def create_user():
    step("3", "👤 Создание обычного пользователя (ввод данных)...")
    
    while True:
        new_user = questionary.text("Введите имя нового пользователя (или Enter для пропуска):").ask()
        if not new_user:
            warn("Пропуск создания пользователя.")
            return
        
        # Check if contains spaces or invalid characters
        if not new_user.isalnum() and "_" not in new_user and "-" not in new_user:
            warn("Имя содержит недопустимые символы. Используйте только латиницу, цифры, - и _")
            continue
            
        try:
            pwd.getpwnam(new_user)
            warn(f"Пользователь {new_user} уже существует.")
            continue
        except KeyError:
            break

    password = questionary.password("Задайте пароль:").ask()
    confirm = questionary.password("Подтвердите пароль:").ask()
    while password != confirm or len(password) < 6:
        warn("Пароли не совпадают или слишком короткие (минимум 6 символов).")
        password = questionary.password("Задайте пароль:").ask()
        confirm = questionary.password("Подтвердите пароль:").ask()

    groups = questionary.text("Дополнительные группы (через запятую):", default="sudo,docker").ask()
    copy_ssh = questionary.confirm("Копировать SSH-ключи от root?").ask()

    console.print(f"[yellow]   ⚙️  Создаю пользователя {new_user}...[/yellow]")
    run(f"useradd -m -s /bin/bash {new_user}")
    
    # Set password securely
    proc = subprocess.Popen(["chpasswd"], stdin=subprocess.PIPE, text=True)
    proc.communicate(f"{new_user}:{password}")
    if proc.returncode == 0:
        ok("Пароль установлен")
    
    group_list = [g.strip() for g in groups.split(",") if g.strip()]
    for grp in group_list:
        run(f"usermod -aG {grp} {new_user}")
    ok(f"Пользователь добавлен в группы: {', '.join(group_list)}")

    ssh_dir = Path(f"/home/{new_user}/.ssh")
    ssh_dir.mkdir(parents=True, exist_ok=True)
    auth_keys = ssh_dir / "authorized_keys"
    auth_keys.touch(exist_ok=True)

    root_keys = Path("/root/.ssh/authorized_keys")
    if copy_ssh and root_keys.exists():
        with open(root_keys) as f:
            keys = f.read()
        with open(auth_keys, "a") as f:
            f.write(keys)
        ok("SSH-ключи скопированы от root")

    key_count = sum(1 for _ in open(auth_keys)) if auth_keys.exists() else 0
    if key_count == 0:
        server_ip = run("hostname -I").output.split()[0]
        console.print(f"\n[yellow]⚠️  SSH-ключи не найдены. Выполните на своей машине:[/yellow]")
        console.print(f"[cyan]ssh-copy-id -i ~/.ssh/id_rsa.pub {new_user}@{server_ip}[/cyan]\n")
        questionary.press_any_key_to_continue("После копирования нажмите любую клавишу...").ask()
        if root_keys.exists():
            with open(root_keys) as f:
                keys = f.read()
            with open(auth_keys, "a") as f:
                f.write(keys)
        key_count = sum(1 for _ in open(auth_keys)) if auth_keys.exists() else 0

    run(f"chown -R {new_user}:{new_user} {ssh_dir}")
    run(f"chmod 700 {ssh_dir}")
    run(f"chmod 600 {auth_keys}")
    ok(f"Пользователь {new_user} создан (ключей: {key_count})")

    if key_count > 0:
        nopasswd = questionary.confirm(f"Отключить запрос пароля для sudo у {new_user}?").ask()
        if nopasswd:
            with open(f"/etc/sudoers.d/{new_user}", "w") as f:
                f.write(f"{new_user} ALL=(ALL) NOPASSWD:ALL\n")
            run(f"chmod 440 /etc/sudoers.d/{new_user}")
            ok(f"sudo без пароля включён для {new_user}")


def secure_ssh():
    step("4", "🔐 Отключение входа по паролю (только SSH-ключи)...")
    os.makedirs("/etc/ssh/sshd_config.d", exist_ok=True)
    with open("/etc/ssh/sshd_config.d/99-disable-passwords.conf", "w") as f:
        f.write("PasswordAuthentication no\nPermitRootLogin prohibit-password\n")
    run("systemctl restart ssh || systemctl restart sshd")
    ok("SSH-пароли отключены")


def setup_swap():
    step("5", "💾 Настройка файла подкачки (Swap) на 2 GB...")
    with open("/etc/fstab") as f:
        if "swapfile" in f.read():
            warn("Swap уже настроен — пропускаем")
            return
            
    run("fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none")
    run("chmod 600 /swapfile")
    run("mkswap /swapfile")
    
    res = run("swapon /swapfile")
    if res.ok:
        with open("/etc/fstab", "a") as f:
            f.write("/swapfile none swap sw 0 0\n")
        ok("Swap успешно создан и активирован")
    else:
        warn("Контейнерная виртуализация не поддерживает Swap. Удаляем файл...")
        run("rm -f /swapfile")


def setup_unattended_upgrades():
    step("6", "🛡️  Включение автообновлений безопасности...")
    run("DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades update-notifier-common")
    subprocess.run(
        'echo "unattended-upgrades unattended-upgrades/enable_auto_updates boolean true" | debconf-set-selections', 
        shell=True
    )
    run("dpkg-reconfigure -f noninteractive unattended-upgrades")
    ok("Автообновления включены")


def setup_firewall():
    step("7", "🔥 Настройка файрвола (ufw)...")
    run("ufw --force reset")
    run("ufw default deny incoming")
    run("ufw default allow outgoing")
    for port in ["ssh", "80/tcp", "443/tcp", "51820/udp", "9993/udp"]:
        run(f"ufw allow {port}")
    run("ufw --force enable")
    ok("Файрвол настроен")


def install_docker():
    step("8", "🐳 Установка Docker...")
    if run("command -v docker").ok:
        warn(f"Docker уже установлен — пропускаем")
    else:
        run("curl -fsSL https://get.docker.com -o /tmp/get-docker.sh")
        run("sh /tmp/get-docker.sh")
        run("rm -f /tmp/get-docker.sh")
        run("systemctl enable --now docker")
        ok("Docker установлен и запущен")




def setup_sysinfo():
    step("9", "📊 Настройка вывода системного монитора при входе...")
    dest = Path("/etc/profile.d/vds-sysinfo.sh")
    
    # Мы больше не используем старый bash-скрипт. 
    # Теперь просто вызываем Python-дашборд из нашего CLI
    content = "#!/bin/bash\n/usr/local/bin/vds sysinfo\n"
    dest.write_text(content)
    run(f"chmod +x {dest}")
    
    # Удаляем старый скрипт, если остался от предыдущих установок
    run("rm -f /etc/profile.d/sysinfo.sh")
    ok("Дашборд (vds sysinfo) добавлен в автозапуск при SSH-входе")

def run_setup():
    if os.geteuid() != 0:
        die("Запустите скрипт от имени root")
        
    console.print("=======================================================")
    console.print("   🚀 Инициализация базовой настройки Ubuntu 24.04     ")
    console.print("=======================================================")
    
    clean_system()
    fix_tzdata()
    update_system()
    create_user()
    secure_ssh()
    setup_swap()
    setup_unattended_upgrades()
    setup_firewall()
    install_docker()
    setup_sysinfo()
    
    console.print("\n=======================================================")
    console.print(" 🎉 Настройка VDS успешно завершена!                  ")
    console.print("=======================================================\n")
    return 0
