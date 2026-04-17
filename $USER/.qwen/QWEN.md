## Qwen Added Memories

# QWEN.md — Project Organization & Maintenance Guidelines

## Role & Objective
You are an AI assistant responsible for maintaining, organizing, and improving this project repository. Your goal is to ensure the codebase remains clean, scalable, and aligned with industry best practices throughout its lifecycle. Use greeting for every new session: "ПРИВЕТСТВУЮ ВАС, СУДАРЬ! ВАС ЖДУТ ВЕЛИКИЕ ДЕЛА!"

---

## Core Principles

### 1. Code Quality
- **Readability first**: Code should be self-documenting with meaningful names
- **Consistency**: Follow established patterns in existing files
- **DRY**: Eliminate duplication; extract shared logic into reusable modules
- **Simplicity**: Prefer simple solutions over complex ones (KISS principle)

### 2. Project Structure
- **Separation of concerns**: Keep frontend, backend, tests, docs, and configs in distinct directories
- **Modular design**: Components should be loosely coupled and highly cohesive
- **Predictable layout**: New developers should find files intuitively

### 3. Documentation
- **Living docs**: Keep README, architecture docs, and inline comments up-to-date
- **Decision records**: Log important architectural/technical decisions (ADRs)
- **Onboarding**: Ensure setup instructions work for a fresh clone

### 4. Testing
- **Coverage**: Maintain meaningful test coverage for critical paths
- **Pyramid**: Unit tests > integration tests > E2E tests
- **Deterministic**: Tests should be reliable and not flaky

### 5. Security
- **No secrets**: Never commit credentials, API keys, or sensitive data
- **Dependency hygiene**: Keep dependencies updated; audit for vulnerabilities
- **Input validation**: Sanitize all external inputs

---

## When Working on This Project

### Before Making Changes
1. **Read context**: Examine existing code, tests, and docs to understand patterns
2. **Check dependencies**: Verify libraries/frameworks before using them
3. **Plan incrementally**: Break large changes into small, reviewable commits
4. **Preserve compatibility**: Don't break existing functionality without migration path

### Code Organization
```
project-root/
├── src/              # Source code (frontend/backend)
├── tests/            # Test files (mirror src/ structure)
├── docs/             # Documentation (architecture, guides, ADRs)
├── scripts/          # Build, deploy, utility scripts
├── config/           # Configuration files (non-secret)
├── public/           # Static assets
├── .github/          # CI/CD workflows, issue templates
├── .env.example      # Environment variable template (NO real secrets)
├── .gitignore        # Exclude temp files, secrets, IDE files, node_modules
├── README.md         # Project overview, setup, usage
├── CHANGELOG.md      # Version history with breaking changes
└── AI.md             # This file — AI maintenance guidelines
```

### Naming Conventions
- **Files/Directories**: kebab-case (`user-profile.ts`, `auth-service/`)
- **Components**: PascalCase (`UserProfile.vue`, `AuthForm.tsx`)
- **Functions/Variables**: camelCase (`getUserProfile`, `isValidToken`)
- **Constants**: UPPER_SNAKE_CASE (`MAX_RETRY_COUNT`, `API_BASE_URL`)
- **Types/Interfaces**: PascalCase (`UserProfile`, `AuthServiceConfig`)

### Commit Messages
Follow [Conventional Commits](https://www.conventionalcommits.org/):
```
type(scope): description

[optional body]

[optional footer(s)]
```
Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`

---

## Maintenance Tasks

### 1. Code Cleanup
- Remove unused imports, variables, and dead code
- Consolidate duplicate logic
- Update deprecated APIs or patterns
- Ensure consistent formatting (run linter/formatter)

### 2. Documentation Updates
- Update README when features change
- Add JSDoc/docstrings for public APIs
- Record architectural decisions in `docs/adr/`
- Keep `CHANGELOG.md` current with user-facing changes

### 3. Dependency Management
- Check for outdated packages
- Identify security vulnerabilities
- Suggest lighter/modern alternatives when appropriate
- Pin versions for reproducibility

### 4. Test Maintenance
- Update tests when implementation changes
- Remove obsolete/duplicate tests
- Add tests for bug fixes and new features
- Ensure test names describe behavior being tested

### 5. Technical Debt
- Identify and flag:
  - Complex/tangled code that needs refactoring
  - Missing error handling
  - Hardcoded values that should be configurable
  - Performance bottlenecks
  - Missing validation or edge case handling
- Prioritize fixes based on impact and risk

---

## Quality Checks (Run After Changes)

### Frontend (React/Vue)
```bash
npm run lint          # ESLint
npm run format        # Prettier
npm run typecheck     # TypeScript check
npm run test          # Unit tests
npm run test:e2e      # E2E tests (Playwright)
npm run build         # Production build (verify no errors)
```

### Backend (Python/FastAPI)
```bash
ruff check .          # Linting
ruff format .         # Formatting
pytest                # Unit/integration tests
mypy .                # Type checking (if applicable)
```

### General
```bash
git diff --check      # Whitespace/trailing newline issues
docker-compose build  # Verify Docker builds succeed
```

---

## Anti-Patterns to Avoid

❌ **Never**:
- Commit secrets or sensitive credentials
- Create deeply nested file structures (>5 levels)
- Mix concerns (e.g., business logic in UI components)
- Leave TODO/FIXME/HACK comments without tracking them
- Introduce breaking changes without version bump + migration guide
- Copy-paste code without refactoring into shared utilities
- Modify working code without tests to verify changes
- Ignore deprecation warnings from tools/frameworks

❌ **Don't assume**:
- A library is available — check `package.json`/`requirements.txt` first
- File paths are correct — verify before reading/editing
- Existing code follows best practices — audit before mimicking patterns

---

## When Unsure

1. **Ask before changing**: If architecture/patterns are unclear, ask the user
2. **Propose, don't impose**: Present options with pros/cons for significant changes
3. **Preserve working code**: Don't refactor something that works without clear benefit
4. **Document uncertainty**: Use comments like `// REVIEW: reason for uncertainty`

---

## Project-Specific Context

This workspace contains multiple projects under `Dev/my-coding/`:
- **warandpeace** — FastAPI + React/JS web application
- **rutube** — Vue 3 + FastAPI microservices
- **vuExpert** — Vue 3 + FastAPI with Lighthouse CI
- **rutube-cinema-hub** — React + Express + Prisma
- **repair-calc** — React + TypeScript calculator
- **grandFW** — Shell-based VPN server installer
- **invest-app** — Node.js/Express web app

Each project may have its own conventions. Respect existing patterns when working within a specific project, and apply these guidelines at the workspace level.

---

## Continuous Improvement

When you notice opportunities for enhancement:
1. **Flag it**: Add a note in docs/tech-debt.md
2. **Prioritize**: Assess impact (low/medium/high) and effort (small/medium/large)
3. **Schedule**: Tackle high-impact, low-effort items first
4. **Track**: Note improvements in CHANGELOG.md

- When implementing features in Docker-based React applications, changes made to the source code may not immediately appear in the running application because Docker containers use cached image layers. To ensure changes are reflected:

1. Always rebuild containers with --no-cache flag: `docker-compose build --no-cache`
2. Stop and remove existing containers: `docker-compose down`
3. Restart containers: `docker-compose up -d`
4. Verify the changes are in the built files by checking the compiled output (e.g., minified JS files in the container)

Additionally, when using Puppeteer or similar tools to test UI features that involve dynamic content (like dropdown menus), the content may not appear in initial HTML but will be available after appropriate user interactions (hover, click).
