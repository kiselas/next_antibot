import pytest

from antispam_bot.i18n import _CATALOG, SUPPORTED_LANGS, normalize_lang, t


@pytest.mark.parametrize(
    "key, lang, expected",
    [
        ("cb_done", "en", "Done"),
        ("cb_done", "ru", "Готово"),
        ("cb_done", "fr", "Done"),  # unknown lang -> English fallback
        ("cb_done", None, "Done"),
        ("no_such_key", "en", "no_such_key"),  # missing key -> key itself
    ],
)
def test_t(key, lang, expected):
    assert t(key, lang) == expected


def test_formatting():
    out = t("set_ok", "en", key="model", value="x")
    assert "model" in out and "x" in out


@pytest.mark.parametrize("lang, expected", [("ru", "ru"), ("en", "en"), ("fr", "en"), (None, "en")])
def test_normalize_lang(lang, expected):
    assert normalize_lang(lang) == expected


def test_catalogs_consistent():
    assert set(_CATALOG["en"]) == set(_CATALOG["ru"])
    assert set(SUPPORTED_LANGS) == set(_CATALOG)
