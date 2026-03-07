#!/usr/bin/env python3
"""
SQLite database module for morpheme dictionaries.

Single source of truth for all dictionary data. Use get_db() + init_schema()
to open/create the database, then use the helper functions for all CRUD ops.

Default database path: morpheme_dicts.db in the project root.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "morpheme_dicts.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    target_lang TEXT NOT NULL,
    home_lang TEXT NOT NULL,
    word_id TEXT NOT NULL,
    article TEXT NOT NULL DEFAULT '',
    display_prefix TEXT,
    translation_short TEXT NOT NULL DEFAULT '',
    translation_long TEXT NOT NULL DEFAULT '',
    example_sentence TEXT NOT NULL DEFAULT '',
    example_translation TEXT NOT NULL DEFAULT '',
    flag TEXT,
    review_status TEXT,   -- NULL = unreviewed, 'passed' = approved, 'needs_work' = flagged for revision
    imported_from TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (target_lang, home_lang, word_id)
);

CREATE TABLE IF NOT EXISTS parts (
    target_lang TEXT NOT NULL,
    home_lang TEXT NOT NULL,
    word_id TEXT NOT NULL,
    part_index INTEGER NOT NULL,
    target_lang_text TEXT NOT NULL,
    home_lang_text TEXT NOT NULL DEFAULT '',
    home_lang_details TEXT,
    PRIMARY KEY (target_lang, home_lang, word_id, part_index),
    FOREIGN KEY (target_lang, home_lang, word_id)
        REFERENCES entries(target_lang, home_lang, word_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verification_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_lang TEXT NOT NULL,
    home_lang TEXT NOT NULL,
    word_id TEXT NOT NULL,
    category TEXT,
    field TEXT,
    issue TEXT,
    suggestion TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS known_discrepancies (
    target_lang TEXT NOT NULL DEFAULT '',
    home_lang TEXT NOT NULL DEFAULT '',
    word_id TEXT NOT NULL,
    category TEXT,
    field TEXT,
    issue TEXT,
    correction TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    PRIMARY KEY (target_lang, home_lang, word_id)
);

CREATE TABLE IF NOT EXISTS morphemes (
    target_lang TEXT NOT NULL,
    home_lang TEXT NOT NULL,
    morpheme TEXT NOT NULL,
    short_gloss TEXT NOT NULL,
    home_lang_details TEXT,
    PRIMARY KEY (target_lang, home_lang, morpheme)
);

CREATE TABLE IF NOT EXISTS wordlist_words (
    target_lang TEXT NOT NULL,
    home_lang TEXT NOT NULL,
    word TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    source_file TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT,
    PRIMARY KEY (target_lang, home_lang, word)
);

-- Per-language-pair goals, status, and notes
CREATE TABLE IF NOT EXISTS lang_pair_meta (
    target_lang  TEXT NOT NULL,
    home_lang    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',  -- active, parked, shipped
    priority     INTEGER,                          -- 1 = highest
    target_count INTEGER,                          -- goal word count to ship
    notes        TEXT,
    PRIMARY KEY (target_lang, home_lang)
);

-- Convenience view: one row per entry with morpheme breakdown collapsed to a
-- readable string, e.g.  "ver(for-/away) + steh(stand) + en(infinitive)"
-- Open in DB Browser → Browse Data → entry_overview to scan for problems.
-- (Recreated by migrate_db() whenever the schema changes.)
CREATE VIEW IF NOT EXISTS entry_overview AS
SELECT
    e.target_lang || '-' || e.home_lang                        AS pair,
    e.word_id,
    e.article,
    GROUP_CONCAT(
        p.target_lang_text || '(' || p.home_lang_text || ')',
        ' + '
    )                                                           AS breakdown,
    e.translation_short,
    e.translation_long,
    e.example_sentence,
    e.flag,
    e.review_status,
    e.imported_from
FROM entries e
LEFT JOIN parts p USING (target_lang, home_lang, word_id)
GROUP BY e.target_lang, e.home_lang, e.word_id
ORDER BY e.target_lang, e.home_lang, e.rowid;
"""


def get_db(path=None) -> sqlite3.Connection:
    """Open (and initialise) the SQLite database, returning a connection."""
    db_path = Path(path) if path else DEFAULT_DB_PATH
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    migrate_db(conn)
    return conn


def init_schema(conn: sqlite3.Connection):
    """Create all tables if they don't exist."""
    conn.executescript(_SCHEMA)
    conn.commit()


def split_pair(pair: str) -> tuple:
    """Split 'tr-en' into ('tr', 'en') — target_lang, home_lang."""
    parts = pair.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid lang pair: {pair!r} (expected 'xx-yy')")
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# Entry CRUD
# ---------------------------------------------------------------------------

def insert_entry(
    conn: sqlite3.Connection,
    target_lang: str,
    home_lang: str,
    entry_dict: dict,
    source: str = None,
    replace: bool = False,
) -> bool:
    """Insert or update an entry and its parts.

    Returns True if the entry was inserted/updated, False if skipped (conflict
    and replace=False).
    """
    word_id = entry_dict["id"]

    existing = conn.execute(
        "SELECT word_id FROM entries WHERE target_lang=? AND home_lang=? AND word_id=?",
        (target_lang, home_lang, word_id),
    ).fetchone()

    if existing and not replace:
        return False

    conn.execute(
        """
        INSERT OR REPLACE INTO entries
            (target_lang, home_lang, word_id, article, display_prefix,
             translation_short, translation_long, example_sentence,
             example_translation, flag, imported_from, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            target_lang,
            home_lang,
            word_id,
            entry_dict.get("article", ""),
            entry_dict.get("displayPrefix"),
            entry_dict.get("translationShort", ""),
            entry_dict.get("translationLong", ""),
            entry_dict.get("exampleSentence", ""),
            entry_dict.get("exampleTranslation", ""),
            entry_dict.get("flag"),
            source,
        ),
    )

    # Replace parts (delete then re-insert)
    conn.execute(
        "DELETE FROM parts WHERE target_lang=? AND home_lang=? AND word_id=?",
        (target_lang, home_lang, word_id),
    )
    parts_list = entry_dict.get("parts", [])
    for i, part in enumerate(parts_list):
        conn.execute(
            """
            INSERT INTO parts
                (target_lang, home_lang, word_id, part_index,
                 target_lang_text, home_lang_text, home_lang_details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_lang,
                home_lang,
                word_id,
                i,
                part.get("targetLang", ""),
                part.get("homeLang", ""),
                part.get("homeLangDetails"),
            ),
        )

    conn.execute(
        "UPDATE entries SET part_count=? WHERE target_lang=? AND home_lang=? AND word_id=?",
        (len(parts_list), target_lang, home_lang, word_id),
    )

    conn.commit()
    return True


def get_done_ids(conn: sqlite3.Connection, target_lang: str, home_lang: str) -> set:
    """Return lowercased word_ids already in the DB for this lang pair."""
    rows = conn.execute(
        "SELECT word_id FROM entries WHERE target_lang=? AND home_lang=?",
        (target_lang, home_lang),
    ).fetchall()
    return {row["word_id"].lower() for row in rows}


def get_entries(conn: sqlite3.Connection, target_lang: str, home_lang: str, to_verify: bool = False) -> list:
    """Return entries for a lang pair as JSON-shaped dicts (insertion order).

    If to_verify=True, returns entries with to_verify=1 regardless of import status.
    Otherwise returns entries with import=1.
    """
    filter_col = "to_verify" if to_verify else "import"
    rows = conn.execute(
        f"SELECT * FROM entries WHERE target_lang=? AND home_lang=? AND {filter_col}=1 ORDER BY rowid",
        (target_lang, home_lang),
    ).fetchall()
    result = []
    for row in rows:
        parts_rows = conn.execute(
            """SELECT * FROM parts
               WHERE target_lang=? AND home_lang=? AND word_id=?
               ORDER BY part_index""",
            (target_lang, home_lang, row["word_id"]),
        ).fetchall()
        result.append(entry_to_dict(row, parts_rows))
    return result


def get_entry(conn: sqlite3.Connection, target_lang: str, home_lang: str, word_id: str):
    """Return a single entry dict, or None if not found."""
    row = conn.execute(
        "SELECT * FROM entries WHERE target_lang=? AND home_lang=? AND word_id=? AND import=1 ORDER BY rowid",
        (target_lang, home_lang, word_id),
    ).fetchone()
    if row is None:
        return None
    parts_rows = conn.execute(
        """SELECT * FROM parts
           WHERE target_lang=? AND home_lang=? AND word_id=?
           ORDER BY part_index""",
        (target_lang, home_lang, word_id),
    ).fetchall()
    return entry_to_dict(row, parts_rows)


def update_entry(
    conn: sqlite3.Connection,
    target_lang: str,
    home_lang: str,
    word_id: str,
    fields: dict,
):
    """Patch scalar fields on an existing entry.

    ``fields`` maps JSON-style field names (e.g. 'translationShort') to new values.
    For updating parts, pass 'parts' as a list of part dicts.
    """
    column_map = {
        "article": "article",
        "displayPrefix": "display_prefix",
        "translationShort": "translation_short",
        "translationLong": "translation_long",
        "exampleSentence": "example_sentence",
        "exampleTranslation": "example_translation",
        "flag": "flag",
        "reviewStatus": "review_status",
    }
    scalar_updates = {column_map[k]: v for k, v in fields.items() if k in column_map}
    if scalar_updates:
        set_clause = ", ".join(f"{col}=?" for col in scalar_updates)
        set_clause += ", updated_at=datetime('now')"
        conn.execute(
            f"UPDATE entries SET {set_clause} WHERE target_lang=? AND home_lang=? AND word_id=?",
            (*scalar_updates.values(), target_lang, home_lang, word_id),
        )

    if "parts" in fields:
        conn.execute(
            "DELETE FROM parts WHERE target_lang=? AND home_lang=? AND word_id=?",
            (target_lang, home_lang, word_id),
        )
        for i, part in enumerate(fields["parts"]):
            conn.execute(
                """
                INSERT INTO parts
                    (target_lang, home_lang, word_id, part_index,
                     target_lang_text, home_lang_text, home_lang_details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_lang,
                    home_lang,
                    word_id,
                    i,
                    part.get("targetLang", ""),
                    part.get("homeLang", ""),
                    part.get("homeLangDetails"),
                ),
            )

    conn.commit()


def entry_to_dict(entry_row, parts_rows) -> dict:
    """Reconstruct the original JSON-shaped entry dict from DB rows."""
    d = {
        "id": entry_row["word_id"],
        "article": entry_row["article"],
        "parts": [],
        "translationShort": entry_row["translation_short"],
        "translationLong": entry_row["translation_long"],
        "exampleSentence": entry_row["example_sentence"],
        "exampleTranslation": entry_row["example_translation"],
    }
    if entry_row["display_prefix"] is not None:
        d["displayPrefix"] = entry_row["display_prefix"]
    if entry_row["flag"] is not None:
        d["flag"] = entry_row["flag"]
    for part in parts_rows:
        p = {
            "targetLang": part["target_lang_text"],
            "homeLang": part["home_lang_text"],
        }
        if part["home_lang_details"] is not None:
            p["homeLangDetails"] = part["home_lang_details"]
        d["parts"].append(p)
    return d


# ---------------------------------------------------------------------------
# Verification flags
# ---------------------------------------------------------------------------

def insert_flag(
    conn: sqlite3.Connection,
    target_lang: str,
    home_lang: str,
    word_id: str,
    flag_dict: dict,
):
    """Insert a verification flag."""
    conn.execute(
        """
        INSERT INTO verification_flags
            (target_lang, home_lang, word_id, category, field, issue, suggestion)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_lang,
            home_lang,
            word_id,
            flag_dict.get("category"),
            flag_dict.get("field"),
            flag_dict.get("issue"),
            flag_dict.get("suggestion"),
        ),
    )
    conn.commit()


def get_open_flags(conn: sqlite3.Connection, target_lang: str, home_lang: str) -> list:
    """Return all open verification flags for a lang pair."""
    rows = conn.execute(
        """SELECT * FROM verification_flags
           WHERE target_lang=? AND home_lang=? AND status='open'
           ORDER BY id""",
        (target_lang, home_lang),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_flag(conn: sqlite3.Connection, flag_id: int, status: str = "fixed"):
    """Mark a verification flag as resolved."""
    conn.execute(
        "UPDATE verification_flags SET status=?, resolved_at=datetime('now') WHERE id=?",
        (status, flag_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Known discrepancies
# ---------------------------------------------------------------------------

def get_known_issues_text(conn: sqlite3.Connection) -> str:
    """Return a formatted 'Known errors to avoid' string for generate_claude prompts."""
    rows = conn.execute(
        "SELECT word_id, issue FROM known_discrepancies WHERE status='confirmed' ORDER BY rowid",
    ).fetchall()
    if not rows:
        return ""
    lines = [f"- {row['word_id']}: {row['issue']}" for row in rows]
    return "Known errors to avoid:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Morphemes (glossary)
# ---------------------------------------------------------------------------

def get_morphemes(conn: sqlite3.Connection, target_lang: str, home_lang: str) -> list:
    """Return morpheme dicts for a lang pair."""
    rows = conn.execute(
        """SELECT morpheme, short_gloss, home_lang_details FROM morphemes
           WHERE target_lang=? AND home_lang=? ORDER BY morpheme""",
        (target_lang, home_lang),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Word list tracking
# ---------------------------------------------------------------------------

def get_wordlist_pending(conn: sqlite3.Connection, target_lang: str, home_lang: str) -> list:
    """Return words with status='pending' for a lang pair."""
    rows = conn.execute(
        """SELECT word FROM wordlist_words
           WHERE target_lang=? AND home_lang=? AND status='pending'
           ORDER BY added_at""",
        (target_lang, home_lang),
    ).fetchall()
    return [row["word"] for row in rows]


def mark_word_done(conn: sqlite3.Connection, target_lang: str, home_lang: str, word: str):
    """Mark a wordlist word as processed (status='done')."""
    conn.execute(
        """UPDATE wordlist_words
           SET status='done', processed_at=datetime('now')
           WHERE target_lang=? AND home_lang=? AND LOWER(word)=LOWER(?)""",
        (target_lang, home_lang, word),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Review status
# ---------------------------------------------------------------------------

def set_curated(conn: sqlite3.Connection, target_lang: str, home_lang: str, word_id: str, value: int = 1):
    """Mark an entry as curated (value=1) or uncurated (value=0)."""
    conn.execute(
        "UPDATE entries SET curated=?, updated_at=datetime('now') WHERE target_lang=? AND home_lang=? AND word_id=?",
        (value, target_lang, home_lang, word_id),
    )
    conn.commit()


def mark_passed(conn: sqlite3.Connection, target_lang: str, home_lang: str, word_id: str):
    """Mark an entry as reviewed and approved."""
    conn.execute(
        """UPDATE entries SET review_status='passed', updated_at=datetime('now')
           WHERE target_lang=? AND home_lang=? AND word_id=?""",
        (target_lang, home_lang, word_id),
    )
    conn.commit()


def mark_needs_work(conn: sqlite3.Connection, target_lang: str, home_lang: str, word_id: str):
    """Mark an entry as needing revision."""
    conn.execute(
        """UPDATE entries SET review_status='needs_work', updated_at=datetime('now')
           WHERE target_lang=? AND home_lang=? AND word_id=?""",
        (target_lang, home_lang, word_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Utility: list all lang pairs in the DB
# ---------------------------------------------------------------------------

def get_all_pairs(conn: sqlite3.Connection) -> list:
    """Return sorted list of (target_lang, home_lang) tuples that have entries."""
    rows = conn.execute(
        "SELECT DISTINCT target_lang, home_lang FROM entries ORDER BY target_lang, home_lang"
    ).fetchall()
    return [(row["target_lang"], row["home_lang"]) for row in rows]


def get_active_pairs(conn: sqlite3.Connection) -> list:
    """Return (target_lang, home_lang) tuples with status != 'parked', ordered by priority."""
    rows = conn.execute(
        """SELECT target_lang, home_lang FROM lang_pair_meta
           WHERE status != 'parked'
           ORDER BY priority NULLS LAST, target_lang, home_lang"""
    ).fetchall()
    return [(row["target_lang"], row["home_lang"]) for row in rows]


def get_pair_meta(conn: sqlite3.Connection, target_lang: str, home_lang: str) -> dict:
    """Return the lang_pair_meta row for a pair, or None if not found."""
    row = conn.execute(
        "SELECT * FROM lang_pair_meta WHERE target_lang=? AND home_lang=?",
        (target_lang, home_lang),
    ).fetchone()
    return dict(row) if row else None


def set_pair_meta(conn: sqlite3.Connection, target_lang: str, home_lang: str, **kwargs):
    """Upsert fields on a lang_pair_meta row.

    Accepted kwargs: status, priority, target_count, notes.
    """
    allowed = {"status", "priority", "target_count", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    conn.execute(
        "INSERT OR IGNORE INTO lang_pair_meta (target_lang, home_lang) VALUES (?, ?)",
        (target_lang, home_lang),
    )
    set_clause = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE lang_pair_meta SET {set_clause} WHERE target_lang=? AND home_lang=?",
        (*fields.values(), target_lang, home_lang),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Migrations: apply schema changes to existing DBs
# ---------------------------------------------------------------------------

def migrate_db(conn: sqlite3.Connection):
    """Apply any schema migrations needed for existing databases.

    Safe to call on every open — each migration is guarded by a check.
    """
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(entries)").fetchall()
    }

    # Migration 1: add review_status column
    if "review_status" not in existing_cols:
        conn.execute("ALTER TABLE entries ADD COLUMN review_status TEXT")
        conn.commit()

    # Migration 2: recreate entry_overview view to include review_status + meta
    # (DROP + CREATE because SQLite doesn't support CREATE OR REPLACE VIEW)
    conn.execute("DROP VIEW IF EXISTS entry_overview")
    conn.executescript("""
        CREATE VIEW entry_overview AS
        SELECT
            e.target_lang || '-' || e.home_lang                        AS pair,
            m.priority                                                  AS pair_priority,
            e.word_id,
            e.article,
            COUNT(p.part_index)                                         AS part_count,
            GROUP_CONCAT(
                p.target_lang_text || '(' || p.home_lang_text || ')',
                ' + '
            )                                                           AS breakdown,
            e.translation_short,
            e.translation_long,
            e.example_sentence,
            e.flag,
            e.curated,
            e.review_status,
            e.import,
            m.status                                                    AS pair_status,
            e.imported_from
        FROM entries e
        LEFT JOIN parts p USING (target_lang, home_lang, word_id)
        LEFT JOIN lang_pair_meta m USING (target_lang, home_lang)
        GROUP BY e.target_lang, e.home_lang, e.word_id
        ORDER BY e.target_lang, e.home_lang, e.rowid;
    """)
    conn.commit()

    # Migration 4: add curated column
    if "curated" not in existing_cols:
        conn.execute("ALTER TABLE entries ADD COLUMN curated INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    # Migration 5: add part_count column and populate it
    if "part_count" not in existing_cols:
        conn.execute("ALTER TABLE entries ADD COLUMN part_count INTEGER NOT NULL DEFAULT 0")
        conn.execute("""
            UPDATE entries SET part_count = (
                SELECT COUNT(*) FROM parts p
                WHERE p.target_lang = entries.target_lang
                  AND p.home_lang = entries.home_lang
                  AND p.word_id = entries.word_id
            )
        """)
        conn.commit()

    # Migration 3: ensure lang_pair_meta has a row for every pair in entries.
    # Uses INSERT OR IGNORE so existing rows (with user-set priority/notes) are untouched.
    # Arabic pairs default to 'parked'; everything else defaults to 'active'.
    pairs = conn.execute(
        "SELECT DISTINCT target_lang, home_lang FROM entries ORDER BY target_lang, home_lang"
    ).fetchall()
    for row in pairs:
        tl, hl = row[0], row[1]
        status = 'parked' if (tl == 'ar' or hl == 'ar') else 'active'
        conn.execute(
            """INSERT OR IGNORE INTO lang_pair_meta (target_lang, home_lang, status)
               VALUES (?, ?, ?)""",
            (tl, hl, status),
        )
    conn.commit()
