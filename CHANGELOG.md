# Changelog

Все значимые изменения проекта документируются в этом файле.
Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [семантического версионирования](https://semver.org/lang/ru/).

## [Unreleased]

## [0.2.0] - 2026-06-14

### Added
- Pluggable classifier backends (`CLASSIFIER_BACKEND`): OpenAI-compatible,
  native Anthropic, local Ollama, and an offline heuristic.
- i18n for bot messages (English default + Russian), switchable via `/set language`.
- `pyproject.toml` packaging with the `antispam-bot` entry point.
- ruff + mypy + pre-commit; CI runs lint, type check and tests.
- Test suite expanded to 100% coverage.

### Changed
- Package renamed to `antispam_bot` (run with `antispam-bot` or `python -m antispam_bot`).
- Provider-neutral `LLM_*` env vars (legacy `OPENROUTER_*` still accepted).
- Source comments and docs are English-first.

## [0.1.0] - 2026-06-14

### Добавлено
- Антиспам-модерация группы через OpenRouter (LLM-классификатор) с fallback по моделям.
- Статусы доверия участников, авто-доверие по числу чистых сообщений и времени.
- Реакции на спам: `ban` / `mute` / `report` (переключаются на лету).
- Inline-кнопки в отчётах админу: разбанить / подтвердить / забанить.
- Админ-команды в личке: `/stats`, `/recent`, `/test`, `/config`, `/set`, `/unban`,
  `/allow`, `/unallow`, `/whitelist`, `/resetstats`.
- Белый список пользователей и допустимые домены.
- Защита от злоупотреблений: allowlist чатов с авто-выходом, дневной лимит обращений
  к LLM, защита промпта от инъекций, проверка прав на callback-кнопки.
- Fail-safe при сбоях LLM с классификацией ошибок и алертами админу.
- Docker + docker-compose с healthcheck, rate limiter, конкурентная обработка апдейтов.
- Юнит-тесты (pipeline, settings, llm, helpers), CI.
