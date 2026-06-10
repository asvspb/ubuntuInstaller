#!/usr/bin/env python3
import typer
from typing import Optional

from src.core.logger import setup_logger

setup_logger("src")

app = typer.Typer(
    name="vds",
    help="VDS Management & ZeroTier Orchestrator",
    add_completion=False,
    invoke_without_command=True,
)

zerotier_app = typer.Typer(help="ZeroTier VPN management")
wireguard_app = typer.Typer(help="WireGuard VPN management")
coturn_app = typer.Typer(help="Coturn TURN server management")
app.add_typer(zerotier_app, name="zerotier")
app.add_typer(wireguard_app, name="wireguard")
app.add_typer(coturn_app, name="coturn")

@app.callback(invoke_without_command=True)
def main_menu(ctx: typer.Context):
    if ctx.invoked_subcommand is not None:
        return
    import questionary
    from rich.console import Console
    console = Console()
    
    while True:
        console.clear()
        console.print("[bold cyan]═══ VDS Management Menu ═══[/bold cyan]\n")
        
        choice = questionary.select(
            "Что вы хотите сделать?",
            choices=[
                questionary.Choice("Системный статус (Dashboard)", "sysinfo"),
                questionary.Choice("Управление ZeroTier", "zerotier"),
                questionary.Choice("Управление WireGuard", "wireguard"),
                questionary.Choice("Управление Coturn (TURN)", "coturn"),
                questionary.Choice("Базовая настройка сервера (Setup)", "server_setup"),
                questionary.Choice("Очистка системы (Cleanup)", "cleanup"),
                questionary.Choice("Выход", "exit"),
            ]
        ).ask()
        
        if choice == "sysinfo":
            sysinfo()
            questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
        elif choice == "server_setup":
            try:
                server_setup()
            except typer.Exit:
                pass
            questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
        elif choice == "cleanup":
            try:
                cleanup(dry_run=False, aggressive=False)
            except typer.Exit:
                pass
            questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
        elif choice == "wireguard":
            wg_choice = questionary.select(
                "Меню WireGuard:",
                choices=[
                    questionary.Choice("Установка сервера (install)", "install"),
                    questionary.Choice("Добавить клиента (add-client)", "add_client"),
                    questionary.Choice("Удалить WireGuard (remove)", "remove"),
                    questionary.Choice("Назад", "back"),
                ]
            ).ask()
            
            if wg_choice == "install":
                try:
                    wg_install(port=51820, client="client", dns="8.8.8.8, 8.8.4.4")
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif wg_choice == "add_client":
                name = questionary.text("Имя нового клиента:", default="client2").ask()
                if name:
                    try:
                        wg_add_client(name=name)
                    except typer.Exit:
                        pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif wg_choice == "remove":
                if questionary.confirm("Удалить WireGuard?").ask():
                    try:
                        wg_remove()
                    except typer.Exit:
                        pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()

        elif choice == "coturn":
            ct_choice = questionary.select(
                "Меню Coturn:",
                choices=[
                    questionary.Choice("Установка TURN-сервера (install)", "install"),
                    questionary.Choice("Статус и конфигурация (status)", "status"),
                    questionary.Choice("Удалить Coturn (remove)", "remove"),
                    questionary.Choice("Назад", "back"),
                ]
            ).ask()

            if ct_choice == "install":
                user = questionary.text("Логин для TURN:", default="admin").ask() or "admin"
                pwd = questionary.text("Пароль (пусто = автогенерация):", default="").ask()
                try:
                    coturn_install(username=user, password=pwd)
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif ct_choice == "status":
                try:
                    coturn_status()
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif ct_choice == "remove":
                if questionary.confirm("Удалить Coturn?").ask():
                    try:
                        coturn_remove()
                    except typer.Exit:
                        pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()

        elif choice == "zerotier":
            zt_choice = questionary.select(
                "Меню ZeroTier:",
                choices=[
                    questionary.Choice("Диагностика (status/diagnose)", "diagnose"),
                    questionary.Choice("Синхронизация сетей (reconcile)", "reconcile"),
                    questionary.Choice("Подключить новую сеть (add-network)", "add_network"),
                    questionary.Choice("Восстановление NAT (nat)", "nat"),
                    questionary.Choice("Запуск Watchdog (watchdog)", "watchdog"),
                    questionary.Choice("Установка (install)", "install"),
                    questionary.Choice("Полное удаление (cleanup)", "cleanup"),
                    questionary.Choice("Назад", "back"),
                ]
            ).ask()
            
            if zt_choice == "diagnose":
                try:
                    zt_diagnose(fix=False, yes=False)
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif zt_choice == "reconcile":
                import os
                if not os.path.exists("/opt/ztnet/topology.json"):
                    if questionary.confirm("Файл topology.json не найден. Сгенерировать его сейчас?").ask():
                        try:
                            zt_reconcile(apply=False, init=True, validate=False)
                        except typer.Exit:
                            pass
                    else:
                        questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
                        continue
                
                rec_action = questionary.select(
                    "Действие Reconcile:",
                    choices=[
                        questionary.Choice("Проверка (Dry Run)", "dry"),
                        questionary.Choice("Применить изменения (Apply)", "apply"),
                        questionary.Choice("Обновить файл из текущего состояния (Init)", "init"),
                    ]
                ).ask()
                
                try:
                    if rec_action == "dry":
                        zt_reconcile(apply=False, init=False, validate=False)
                    elif rec_action == "apply":
                        zt_reconcile(apply=True, init=False, validate=False)
                    elif rec_action == "init":
                        if questionary.confirm("ВНИМАНИЕ: Это перезапишет topology.json. Продолжить?").ask():
                            zt_reconcile(apply=False, init=True, validate=False)
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif zt_choice == "add_network":
                try:
                    zt_add_network()
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif zt_choice == "nat":
                try:
                    zt_nat()
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif zt_choice == "watchdog":
                try:
                    zt_watchdog()
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif zt_choice == "install":
                try:
                    zt_install(port=3000)
                except typer.Exit:
                    pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
            elif zt_choice == "cleanup":
                if questionary.confirm("Удалить ZeroTier?").ask():
                    try:
                        zt_cleanup()
                    except typer.Exit:
                        pass
                questionary.press_any_key_to_continue("Нажмите любую клавишу для возврата...").ask()
        elif choice == "exit" or choice is None:
            break


@app.command()
def sysinfo():
    """Показать статус сервера (CPU, RAM, Docker, Disk)"""
    from src.sysinfo.dashboard import show_dashboard
    show_dashboard()


@app.command()
def cleanup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Показать что будет удалено без удаления"),
    aggressive: bool = typer.Option(False, "--aggressive", help="Агрессивная очистка Docker"),
):
    """Системная очистка сервера (apt, Docker, логи, /tmp)"""
    from src.system.cleanup import run_cleanup
    raise typer.Exit(run_cleanup(dry_run=dry_run, safe_docker=not aggressive))


@app.command()
def server_setup():
    """Базовая первичная настройка сервера (Python)"""
    from src.system.setup import run_setup
    raise typer.Exit(run_setup())


@zerotier_app.command("install")
def zt_install(
    port: int = typer.Option(3000, "--port", help="Порт ZTNET Panel"),
):
    """Установить ZeroTier + ZTNET Panel + Internet Gateway"""
    from src.zerotier.install import run_install
    raise typer.Exit(run_install(ztnet_port=port))


@zerotier_app.command("status")
def zt_status():
    """Показать статус ZeroTier (сети, пиры, NAT)"""
    from src.zerotier.diagnose import run_diagnose
    raise typer.Exit(run_diagnose(fix=False))


@zerotier_app.command("diagnose")
def zt_diagnose(
    fix: bool = typer.Option(False, "--fix", help="Интерактивное исправление проблем"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Автоисправление без подтверждения"),
):
    """Диагностика ZeroTier + ZTNET с возможностью исправления"""
    from src.zerotier.diagnose import run_diagnose
    raise typer.Exit(run_diagnose(fix=fix, auto_yes=yes))


@zerotier_app.command("reconcile")
def zt_reconcile(
    apply: bool = typer.Option(False, "--apply", help="Применить изменения (по умолчанию dry-run)"),
    init: bool = typer.Option(False, "--init", help="Сгенерировать topology.json из текущего состояния"),
    validate: bool = typer.Option(False, "--validate", help="Проверить корректность topology.json"),
):
    """Синхронизировать состояние ZeroTier с topology.json"""
    from src.zerotier.reconcile import run_reconcile
    raise typer.Exit(run_reconcile(apply=apply, init=init, validate=validate))


@zerotier_app.command("watchdog")
def zt_watchdog():
    """Фоновый мониторинг и восстановление ZeroTier"""
    from src.zerotier.watchdog import run_watchdog
    raise typer.Exit(run_watchdog())


@zerotier_app.command("nat")
def zt_nat():
    """Восстановить NAT правила для всех ZeroTier сетей"""
    from src.zerotier.nat import setup_nat_all
    raise typer.Exit(setup_nat_all())


@zerotier_app.command("add-network")
def zt_add_network():
    """Добавление новой сети к существующей установке ZTNET"""
    from src.zerotier.add_network import run_add_network
    raise typer.Exit(run_add_network())


@zerotier_app.command("cleanup")
def zt_cleanup():
    """Полное удаление ZeroTier + ZTNET"""
    from src.zerotier.cleanup import run_cleanup
    raise typer.Exit(run_cleanup())


@wireguard_app.command("install")
def wg_install(
    port: int = typer.Option(51820, "--port", help="Порт WireGuard"),
    client: str = typer.Option("client", "--client", help="Имя первого клиента"),
    dns: str = typer.Option("8.8.8.8, 8.8.4.4", "--dns", help="DNS сервер для клиента"),
):
    """Установить WireGuard VPN сервер"""
    from src.wireguard.install import run_install
    raise typer.Exit(run_install(port=port, client_name=client, dns=dns))


@wireguard_app.command("add-client")
def wg_add_client(
    name: str = typer.Option("client", "--name", help="Имя нового клиента"),
):
    """Добавить нового клиента WireGuard"""
    from src.wireguard.install import add_client
    raise typer.Exit(add_client(client_name=name))


@wireguard_app.command("remove")
def wg_remove():
    """Удалить WireGuard"""
    from src.wireguard.install import remove
    raise typer.Exit(remove())


@coturn_app.command("install")
def coturn_install(
    username: str = typer.Option("admin", "--user", "-u", help="Логин для TURN"),
    password: str = typer.Option("", "--password", "-p", help="Пароль (пусто = автогенерация)"),
    realm: str = typer.Option("private-cinema.local", "--realm", help="Realm"),
    port: int = typer.Option(3478, "--port", help="Порт TURN"),
):
    """Установить Coturn TURN сервер"""
    from src.coturn.install import run_install
    raise typer.Exit(run_install(username=username, password=password, realm=realm, port=port))


@coturn_app.command("status")
def coturn_status():
    """Показать статус и конфигурацию Coturn"""
    from src.coturn.install import run_status
    raise typer.Exit(run_status())


@coturn_app.command("remove")
def coturn_remove():
    """Удалить Coturn"""
    from src.coturn.install import run_remove
    raise typer.Exit(run_remove())


if __name__ == "__main__":
    app()
