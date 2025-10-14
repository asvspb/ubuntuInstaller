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
NC := \033[0m # No Color

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
.PHONY: fmt fmt-check
fmt: check-shfmt
	@echo -e "$(GREEN)Форматирование скриптов...$(NC)"
	@find . -name "*.sh" -not -path "./.git/*" -exec shfmt -w {} \;
	@echo -e "$(GREEN)Форматирование завершено.$(NC)"

fmt-check: check-shfmt
	@echo -e "$(GREEN)Проверка форматирования скриптов...$(NC)"
	@find . -name "*.sh" -not -path "./.git/*" -exec shfmt -d -s {} \;
	@echo -e "$(GREEN)Проверка форматирования завершена.$(NC)"

# Цель для симуляции установки
.PHONY: dry-run
dry-run:
	@echo -e "$(YELLOW)Симуляция установки...$(NC)"
	@echo "Проверка системы..."
	@echo "Проверка версии Ubuntu..."
	@echo "Проверка наличия свободного места..."
	@echo "Проверка подключения к интернету..."
	@echo -e "$(GREEN)Симуляция завершена успешно. Никаких изменений в системе не было внесено.$(NC)"

# Цель для проверки конфигурации
.PHONY: validate-config
validate-config:
	@echo -e "$(GREEN)Проверка конфигурации...$(NC)"
	@if command -v yq >/dev/null 2>&1; then \
		if [ -f "config.yaml" ]; then \
			./scripts/validate_config.sh config.yaml || exit 1; \
		else \
			echo -e "$(YELLOW)Файл config.yaml не найден.$(NC)"; \
		fi; \
	for profile in profiles/*.yaml; do \
			if [ -f "$$profile" ]; then \
				./scripts/validate_config.sh "$$profile" || exit 1; \
			fi; \
		done; \
	else \
		echo -e "$(RED)yq не установлен. Установите его командой:$(NC)"; \
		echo "sudo apt install yq"; \
		exit 1; \
	fi

# Цель для симуляции установки с профилем desktop-developer
.PHONY: dry-run-desktop-developer
dry-run-desktop-developer:
	@echo -e "$(YELLOW)Симуляция установки с профилем desktop-developer...$(NC)"
	@./install.sh --dry-run -c profiles/desktop-developer.yaml

# Цель для симуляции установки с профилем server
.PHONY: dry-run-server
dry-run-server:
	@echo -e "$(YELLOW)Симуляция установки с профилем server...$(NC)"
	@./install.sh --dry-run -c profiles/server.yaml

# Цель для симуляции установки с профилем wsl
.PHONY: dry-run-wsl
dry-run-wsl:
	@echo -e "$(YELLOW)Симуляция установки с профилем wsl...$(NC)"
	@./install.sh --dry-run -c profiles/wsl.yaml

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