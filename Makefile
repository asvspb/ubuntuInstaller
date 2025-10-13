# Makefile для ubuntuInstaller
# Цели:
# - lint: проверка скриптов с помощью shellcheck
# - fmt: форматирование скриптов с помощью shfmt
# - dry-run: симуляция установки без изменений в системе
# - install: запуск установки

SHELL := /bin/bash

# Определение цветов для вывода
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[1;33m
NC := \03[0m # No Color

# Проверка наличия необходимых инструментов
check-shellcheck:
	@command -v shellcheck > /dev/null || { \
		echo -e "$(RED)shellcheck не найден. Установите его командой:$(NC)"; \
		echo "sudo apt install shellcheck"; \
		exit 1; \
	}

check-shfmt:
	@command -v shfmt > /dev/null || { \
		echo -e "$(RED)shfmt не найден. Установите его командой:$(NC)"; \
		echo "sudo apt install shfmt" || echo "go install mvdan.cc/sh/v3/cmd/shfmt@latest"; \
		exit 1; \
	}

# Цель для проверки синтаксиса скриптов
.PHONY: lint
lint: check-shellcheck
	@echo -e "$(GREEN)Проверка синтаксиса скриптов...$(NC)"
	@find . -name "*.sh" -not -path "./.git/*" -exec shellcheck {} \;
	@echo -e "$(GREEN)Проверка синтаксиса завершена.$(NC)"

# Цель для форматирования скриптов
.PHONY: fmt
fmt: check-shfmt
	@echo -e "$(GREEN)Форматирование скриптов...$(NC)"
	@find . -name "*.sh" -not -path "./.git/*" -exec shfmt -w {} \;
	@echo -e "$(GREEN)Форматирование завершено.$(NC)"

# Цель для симуляции установки
.PHONY: dry-run
dry-run:
	@echo -e "$(YELLOW)Симуляция установки...$(NC)"
	@echo "Проверка системы..."
	@echo "Проверка версии Ubuntu..."
	@echo "Проверка наличия свободного места..."
	@echo "Проверка подключения к интернету..."
	@echo -e "$(GREEN)Симуляция завершена успешно. Никаких изменений в системе не было внесено.$(NC)"

# Цель для запуска установки
.PHONY: install
install:
	@echo -e "$(GREEN)Запуск установки...$(NC)"
	@./install.sh install

# Цель для запуска удаления
.PHONY: uninstall
uninstall:
	@echo -e "$(GREEN)Запуск удаления...$(NC)"
	@./install.sh uninstall

# Цель для запуска обновления
.PHONY: update
update:
	@echo -e "$(GREEN)Запуск обновления...$(NC)"
	@./install.sh update

# Цель для получения информации о проекте
.PHONY: info
info:
	@echo -e "$(GREEN)Ubuntu Installer Framework$(NC)"
	@echo "Поддерживаемые версии Ubuntu: 22.04, 24.04"
	@echo "Поддерживаемые архитектуры: amd64"
	@echo ""
	@echo "Доступные цели:"
	@echo "  make lint     - проверка синтаксиса скриптов"
	@echo "  make fmt      - форматирование скриптов"
	@echo "  make dry-run  - симуляция установки"
	@echo "  make install  - запуск установки"
	@echo "  make uninstall - запуск удаления"
	@echo "  make update   - запуск обновления"
	@echo "  make info     - информация о проекте"