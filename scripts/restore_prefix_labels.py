#!/usr/bin/env python3
"""Restore prefix home_lang_text values from git HEAD for all pairs except ru-de."""
import subprocess
import sqlite3
import tempfile
import os
from pathlib import Path

DB = Path(__file__).parent.parent / "morpheme_dicts.db"

# Extract git HEAD version of the DB as binary
result = subprocess.run(
    ["git", "show", "HEAD:morpheme_dicts.db"],
    capture_output=True,
    cwd=Path(__file__).parent.parent,
)
if result.returncode != 0:
    print(f"git show failed: {result.stderr.decode()}")
    exit(1)

# Write to a temp file in binary mode
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    f.write(result.stdout)
    tmp_path = f.name

print(f"Extracted git HEAD DB to {tmp_path} ({len(result.stdout):,} bytes)")

# Verify it's a valid SQLite file
try:
    backup_conn = sqlite3.connect(tmp_path)
    count = backup_conn.execute("SELECT COUNT(*) FROM parts WHERE morpheme_type='prefix'").fetchone()[0]
    print(f"Backup has {count:,} prefix parts")
    backup_conn.close()
except Exception as e:
    print(f"Backup DB invalid: {e}")
    os.unlink(tmp_path)
    exit(1)

# Restore into current DB
conn = sqlite3.connect(str(DB))
conn.execute(f"ATTACH '{tmp_path}' AS backup")

cur = conn.execute("""
    UPDATE parts
    SET home_lang_text = (
        SELECT backup.parts.home_lang_text FROM backup.parts
        WHERE backup.parts.target_lang = parts.target_lang
          AND backup.parts.home_lang = parts.home_lang
          AND backup.parts.word_id = parts.word_id
          AND backup.parts.part_index = parts.part_index
    )
    WHERE morpheme_type = 'prefix'
      AND NOT (target_lang = 'ru' AND home_lang = 'de')
""")
restored = cur.rowcount
conn.execute("DETACH backup")
conn.commit()
conn.close()

os.unlink(tmp_path)
print(f"{restored} rows restored.")
