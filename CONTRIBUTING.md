# Contributing

Thanks for your interest! Issues and pull requests are welcome.

## Dev environment

Python 3.12+ (3.14 recommended).

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env            # fill in for a local run
```

## Before committing

```bash
ruff check antispam_bot tests
ruff format antispam_bot tests
mypy antispam_bot
pytest --cov
bandit -r antispam_bot --severity-level medium   # code security (SAST)
pip-audit                                         # dependency vulnerabilities
```

CI also runs CodeQL and a secret scan (gitleaks) on every pull request.

Or install the git hooks: `pre-commit install`.

New behaviour and bug fixes should come with tests in `tests/`. The suite is at
100% coverage — please keep it that way where reasonable.

## Style

- 4-space indent, type hints, docstrings for modules and non-trivial functions.
- Code comments and identifiers in English; user-facing strings go through
  `antispam_bot/i18n.py` (add the key to both `en` and `ru`).
- Avoid heavy dependencies.
- Secrets only via `.env`; never commit real tokens/keys.

## Adding a classifier backend

1. Implement `Classifier` in `antispam_bot/classifiers/your_backend.py`.
2. Register it in `antispam_bot/classifiers/__init__.py` (`_BACKENDS`).
3. Add tests in `tests/test_classifiers.py`.

## Pull requests

1. Branch off `main`.
2. Describe what changes and why; include test steps.
3. Make sure CI is green (ruff + mypy + tests).

## Reporting vulnerabilities

Not via public issues — see [SECURITY.md](SECURITY.md).
