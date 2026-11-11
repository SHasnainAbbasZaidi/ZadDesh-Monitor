"""Reset an active ZadDesh administrator password from a local terminal."""
from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
import warnings
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from zaddesh_core.security import hash_password


def reset_password(database: Path, username: str) -> None:
    uri = database.resolve(strict=True).as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        account = connection.execute(
            "SELECT id, updated_at FROM users WHERE username = ? AND role = 'admin' AND is_active = 1",
            (username,),
        ).fetchone()
        if account is None:
            raise ValueError("Active administrator not found in that database.")

        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("New password: ")
            confirmation = getpass.getpass("Confirm new password: ")
        if password != confirmation:
            raise ValueError("The passwords did not match.")
        if not 12 <= len(password) <= 1024:
            raise ValueError("Use a password between 12 and 1024 characters.")

        encoded = hash_password(password)
        versioned = any(column[1] == "session_version" for column in connection.execute("PRAGMA table_info(users)"))
        with connection:
            result = connection.execute(
                "UPDATE users SET hashed_password = ?, updated_at = ?"
                + (", session_version = session_version + 1" if versioned else "")
                + " "
                "WHERE id = ? AND role = 'admin' AND is_active = 1 AND updated_at = ?",
                (encoded, datetime.now(UTC).replace(tzinfo=None), *account),
            )
            if result.rowcount != 1:
                raise ValueError("The account changed during recovery. Run the command again.")
            connection.execute("DELETE FROM api_tokens WHERE user_id = ?", (account[0],))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Path to the live SQLite database")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Run this command in an interactive terminal.")
    try:
        reset_password(args.db, args.username)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        parser.exit(1, "Password recovery cancelled.\n")
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except (OSError, sqlite3.Error):
        parser.exit(1, "Could not open or update the specified ZadDesh SQLite database.\n")
    print(f"Password updated for {args.username}. Sign in with the new password.")


if __name__ == "__main__":
    main()
