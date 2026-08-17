"""Unique-visitor metrics: the daily salt, the hash built on it, and the
coarse device class that replaced the stored user agent.

No address is ever written, so what these tests check is the pair of
properties that makes counting work anyway: one salt per UTC day, so a
visitor is one hash inside that day, and a salt that gets deleted, so
nothing older can be walked back to an address.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from intake.store import Store
from intake.web import classify_device, visitor_hash

MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
          "Mobile/15E148 Safari/604.1")


def day_offset(days: int) -> str:
    """A UTC day the way the store writes it, shifted by days."""
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def test_the_salt_is_minted_once_a_day_and_reused(tmp_path):
    db = tmp_path / "t.db"
    store = Store(db)
    salt = store.visit_salt()
    assert len(salt) == 64
    assert store.visit_salt() == salt
    # Durable, not in-process: the web container restarts on every deploy,
    # and a fresh in-memory salt would split one visitor into two identities
    # halfway through the day.
    assert Store(db).visit_salt() == salt


def test_a_new_day_mints_a_new_salt_and_prunes_the_old_ones(tmp_path):
    store = Store(tmp_path / "t.db")
    yesterday = store.visit_salt()

    # Backdate the stored salt so the next call is the first visit of a new
    # day, then plant salts of every age the pruner has to judge.
    store.conn.execute("UPDATE visit_salts SET day = ?", (day_offset(-1),))
    for age in (2, 3, 40):
        store.conn.execute(
            "INSERT INTO visit_salts (day, salt) VALUES (?, ?)",
            (day_offset(-age), f"salt-{age}"),
        )
    store.conn.commit()

    assert store.visit_salt() != yesterday
    days = [r["day"] for r in
            store.conn.execute("SELECT day FROM visit_salts ORDER BY day")]
    # Two days back survives; anything older is gone, and with it any way of
    # re-deriving those hashes from an address.
    assert days == [day_offset(-2), day_offset(-1), day_offset(0)]


def test_the_hash_construction_is_pinned():
    salt, ip = "s" * 64, "203.0.113.7"
    assert visitor_hash(salt, ip, MAC) == hashlib.sha256(
        (salt + ip + MAC).encode()).hexdigest()[:16]
    assert len(visitor_hash(salt, ip, MAC)) == 16


def test_one_visitor_is_one_hash_within_the_day():
    salt, ip = "monday-salt", "203.0.113.7"
    assert visitor_hash(salt, ip, MAC) == visitor_hash(salt, ip, MAC)
    # The cost of not storing identity: a new address or a browser update
    # reads as a new person. Undercounting returning visitors is the side
    # to err on.
    assert visitor_hash(salt, ip, MAC) != visitor_hash(salt, "203.0.113.8", MAC)
    assert visitor_hash(salt, ip, MAC) != visitor_hash(salt, ip, IPHONE)


def test_the_same_visitor_is_unrelatable_across_days(tmp_path):
    ip = "203.0.113.7"
    store = Store(tmp_path / "t.db")
    monday = store.visit_salt()
    store.conn.execute("UPDATE visit_salts SET day = ?", (day_offset(-1),))
    store.conn.commit()
    tuesday = store.visit_salt()
    # Cross-day tracking is impossible by construction, not by policy.
    assert visitor_hash(monday, ip, MAC) != visitor_hash(tuesday, ip, MAC)


def test_device_classification_is_coarse_by_design():
    assert classify_device(MAC) == "desktop"
    assert classify_device(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 "
        "Firefox/127.0") == "desktop"
    assert classify_device(IPHONE) == "mobile"
    assert classify_device(
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Mobile Safari/537.36") == "mobile"


def test_crawlers_are_labelled_rather_than_dropped():
    for ua in (
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "Mozilla/5.0 (compatible; YandexBot/3.0)",
        "Mozilla/5.0 (compatible; Yahoo! Slurp)",
        "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
        "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/125.0.0.0 Safari/537.36",
        "curl/8.7.1",
        "Wget/1.21.4",
        "python-httpx/0.27.0",
        "python-requests/2.32.3",
        "Scrapy/2.11",
        "facebookexternalhit/1.1",
        "",  # every browser sends an agent, so an absent one is automation
    ):
        assert classify_device(ua) == "bot", ua


def test_a_recorded_visit_keeps_no_user_agent(tmp_path):
    store = Store(tmp_path / "t.db")
    store.record_visit("landing", "0123456789abcdef", "mobile")
    row = store.conn.execute("SELECT * FROM visits").fetchone()
    assert row["page"] == "landing"
    assert row["visitor_hash"] == "0123456789abcdef"
    assert row["device"] == "mobile"
    assert row["ua"] is None  # the column is history now; nothing writes it
    assert store.visit_count() == 1


def test_an_existing_database_gains_the_columns_and_keeps_its_history(tmp_path):
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute(
        "CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "page TEXT NOT NULL, ua TEXT, at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    old.execute("INSERT INTO visits (page, ua) VALUES ('landing', 'Mozilla/5.0')")
    old.commit()
    old.close()

    store = Store(db)  # additive column migration on open
    store.record_visit("listings", "abc0123456789def", "desktop")
    rows = store.conn.execute("SELECT * FROM visits ORDER BY id").fetchall()
    # Rows written before the change are left exactly as they were; the
    # owner decides whether to clear that agent history, not this migration.
    assert rows[0]["ua"] == "Mozilla/5.0" and rows[0]["visitor_hash"] is None
    assert rows[1]["ua"] is None and rows[1]["device"] == "desktop"
