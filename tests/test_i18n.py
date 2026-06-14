from antispam_bot.i18n import SUPPORTED_LANGS, normalize_lang, t


def test_lookup_en_ru():
    assert t("cb_done", "en") == "Done"
    assert t("cb_done", "ru") == "Готово"


def test_fallback_unknown_lang():
    assert normalize_lang("fr") == "en"
    assert t("cb_done", "fr") == "Done"  # falls back to English
    assert t("cb_done", None) == "Done"


def test_missing_key_returns_key():
    assert t("no_such_key", "en") == "no_such_key"


def test_formatting():
    out = t("set_ok", "en", key="model", value="x")
    assert "model" in out and "x" in out


def test_all_keys_present_in_both_langs():
    from antispam_bot.i18n import _CATALOG

    assert set(_CATALOG["en"]) == set(_CATALOG["ru"])
    assert set(SUPPORTED_LANGS) == {"en", "ru"}
