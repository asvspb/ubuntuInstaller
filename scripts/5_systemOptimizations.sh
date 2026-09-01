#!/bin/bash
# ==============================================================================
# 5_systemOptimizations.sh — Адаптивный системный тюнинг (Hardware-Aware)
#                             Автоматически подстраивается под любое железо:
#                             объем RAM, тип накопителя (SSD/HDD), ядро и CPU.
# ==============================================================================
set -e

C_RESET="\033[0m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"
C_CYAN="\033[1;36m"
C_YELLOW="\033[1;33m"

info()    { echo -e "\n${C_BLUE}[ИНФО]${C_RESET} $*"; }
success() { echo -e "${C_GREEN}[УСПЕХ]${C_RESET} $*"; }
warn()    { echo -e "${C_YELLOW}[ВНИМАНИЕ]${C_RESET} $*"; }

# ------------------------------------------------------------------------------
# 1. Анализ характеристик текущего оборудования
# ------------------------------------------------------------------------------
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_RAM_GB=$(( (TOTAL_RAM_KB + 1048576 - 1) / 1048576 ))

HAS_SSD=0
for disk in /sys/block/nvme* /sys/block/sd* /sys/block/vd*; do
    if [[ -f "$disk/queue/rotational" ]]; then
        if [[ "$(cat "$disk/queue/rotational")" == "0" ]]; then
            HAS_SSD=1
            break
        fi
    fi
done

info "Анализ оборудования:"
echo -e " • Оперативная память : ${C_CYAN}${TOTAL_RAM_GB} GB${C_RESET}"
echo -e " • Тип накопителя     : ${C_CYAN}$([[ $HAS_SSD -eq 1 ]] && echo "SSD/NVMe обнаружен" || echo "HDD (механический диск)")${C_RESET}"

# ------------------------------------------------------------------------------
# 2. Адаптивный расчет параметров памяти (swappiness)
# ------------------------------------------------------------------------------
if (( TOTAL_RAM_GB >= 16 )); then
    TARGET_SWAPPINESS=10
    TARGET_VFS_CACHE=50
elif (( TOTAL_RAM_GB >= 8 )); then
    TARGET_SWAPPINESS=20
    TARGET_VFS_CACHE=60
else
    TARGET_SWAPPINESS=60
    TARGET_VFS_CACHE=100
fi

info "1. Применение адаптивных параметров ядра (/etc/sysctl.d/99-workstation.conf)"
sudo mkdir -p /etc/sysctl.d
cat << SYS_EOF | sudo tee /etc/sysctl.d/99-workstation.conf >/dev/null
# Адаптивный тюнинг памяти под ${TOTAL_RAM_GB} GB RAM
vm.swappiness = ${TARGET_SWAPPINESS}
vm.vfs_cache_pressure = ${TARGET_VFS_CACHE}

# Лимиты файловых вотчеров для разработки (VS Code, Webpack, Vite, Docker)
fs.inotify.max_user_watches = 524288
fs.file-max = 2097152
SYS_EOF

# Проверка поддержки TCP BBR в ядре
if sudo modprobe tcp_bbr 2>/dev/null; then
    cat << 'BBR_EOF' | sudo tee -a /etc/sysctl.d/99-workstation.conf >/dev/null
# Сетевой стек: алгоритм Google BBR для ускорения TCP/VPN/ZeroTier
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
BBR_EOF
    success "TCP BBR поддерживается и активирован!"
else
    warn "Модуль tcp_bbr недоступен в данном ядре, используется стандартный TCP."
fi

sudo sysctl --system >/dev/null 2>&1 || true
success "Параметры sysctl применены (swappiness=${TARGET_SWAPPINESS})!"

# ------------------------------------------------------------------------------
# 3. Обслуживание накопителей (fstrim только для SSD/NVMe)
# ------------------------------------------------------------------------------
if [[ $HAS_SSD -eq 1 ]]; then
    info "2. Включение автоматического обслуживания SSD (fstrim.timer)"
    sudo systemctl enable --now fstrim.timer 2>/dev/null || true
    success "Таймер fstrim.timer активирован для SSD/NVMe!"
else
    info "2. Пропуск fstrim (накопитель HDD, TRIM не требуется)."
fi

# ------------------------------------------------------------------------------
# 4. Ограничение размера системных логов (journald)
# ------------------------------------------------------------------------------
info "3. Ограничение размера системных логов (systemd-journald 250 MB)"
sudo mkdir -p /etc/systemd/journald.conf.d
cat << 'J_EOF' | sudo tee /etc/systemd/journald.conf.d/00-journal-size.conf >/dev/null
[Journal]
SystemMaxUse=250M
SystemKeepFree=1G
J_EOF
sudo systemctl restart systemd-journald 2>/dev/null || true
success "Лимит логов journald зафиксирован на 250 МБ!"

# ------------------------------------------------------------------------------
# 5. Ротация логов Docker (если Docker установлен)
# ------------------------------------------------------------------------------
info "4. Настройка ротации логов Docker (/etc/docker/daemon.json)"
sudo mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
    cat << 'DOCK_EOF' | sudo tee /etc/docker/daemon.json >/dev/null
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
DOCK_EOF
    success "Конфигурация ротации логов Docker создана!"
fi

# ------------------------------------------------------------------------------
# 6. Отключение краш-репортов
# ------------------------------------------------------------------------------
info "5. Отключение назойливых краш-репортов (apport)"
sudo systemctl disable --now apport.service 2>/dev/null || true
sudo sed -i 's/enabled=1/enabled=0/' /etc/default/apport 2>/dev/null || true
success "Служба apport отключена!"

# ------------------------------------------------------------------------------
# 7. Интерфейс GNOME
# ------------------------------------------------------------------------------
info "6. Настройка оформления интерфейса"
gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark' 2>/dev/null || true
gsettings set org.gnome.desktop.interface gtk-theme 'Yaru-dark' 2>/dev/null || true
success "Темная тема интерфейса установлена!"

echo
success "======================================================================="
success " Адаптивная оптимизация под данное оборудование успешно завершена!"
success "======================================================================="
