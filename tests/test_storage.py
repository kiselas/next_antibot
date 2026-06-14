async def test_user_lifecycle(storage):
    assert await storage.get_user(-1, 42) is None
    row = await storage.ensure_user(-1, 42, "vasya")
    assert row["status"] == "untrusted"
    assert row["clean_count"] == 0
    # ensure_user updates username, keeps row
    await storage.ensure_user(-1, 42, "vasya2")
    row = await storage.get_user(-1, 42)
    assert row["username"] == "vasya2"
    assert await storage.increment_clean(-1, 42) == 1
    assert await storage.increment_clean(-1, 42) == 2
    await storage.set_status(-1, 42, "trusted")
    assert (await storage.get_user(-1, 42))["status"] == "trusted"


async def test_user_counts(storage):
    await storage.ensure_user(-1, 1, None)
    await storage.ensure_user(-1, 2, None)
    await storage.set_status(-1, 2, "trusted")
    counts = await storage.user_counts()
    assert counts["total"] == 2
    assert counts["trusted"] == 1
    assert counts["untrusted"] == 1


async def test_bans_log(storage):
    await storage.record_ban(-1, 7, "spammer", "ban", "scam", 0.99, "m/x", "buy now")
    assert await storage.bans_since(0) == 1
    rows = await storage.recent_bans(5)
    assert len(rows) == 1
    assert rows[0]["action"] == "ban"
    assert await storage.last_ban_chat(7) == -1
    assert await storage.last_ban_chat(999) is None


async def test_stats(storage):
    await storage.incr_stat("spam_detected")
    await storage.incr_stat("spam_detected", 2)
    assert (await storage.all_stats())["spam_detected"] == 3
    await storage.clear_stats()
    assert await storage.all_stats() == {}


async def test_settings_store(storage):
    assert await storage.get_setting(0, "x") is None
    await storage.set_setting(0, "x", "1")
    await storage.set_setting(0, "x", "2")  # upsert
    assert await storage.get_setting(0, "x") == "2"
    # per-chat values are isolated from the global (chat_id 0) row
    await storage.set_setting(-100, "x", "chat")
    assert await storage.get_setting(-100, "x") == "chat"
    assert await storage.all_settings(0) == {"x": "2"}
    assert await storage.all_settings(-100) == {"x": "chat"}


async def test_whitelist(storage):
    assert await storage.add_whitelist(5, None) is True
    assert await storage.add_whitelist(5, None) is False  # dedup by id
    assert await storage.add_whitelist(None, "@Bob") is True
    assert await storage.add_whitelist(None, "bob") is False  # dedup by name
    assert await storage.is_whitelisted(5, None) is True
    assert await storage.is_whitelisted(999, "BOB") is True  # case-insensitive
    assert await storage.is_whitelisted(999, "nobody") is False
    assert len(await storage.list_whitelist()) == 2
    assert await storage.remove_whitelist(5, None) == 1
    assert await storage.remove_whitelist(None, "bob") == 1
    assert await storage.remove_whitelist(None, None) == 0
    assert await storage.is_whitelisted(5, "bob") is False
