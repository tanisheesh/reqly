"""Dashboard users, passwords and sessions.

- Passwords: argon2id (argon2-cffi defaults), rehashed on login when the
  parameters change.
- Sessions: a random 256-bit token handed to the client once; the database
  keeps its SHA-256 only. Fixed lifetime (SESSION_TTL), plus a last-seen
  stamp updated at most once a minute.
- Login failures take the same time whether or not the user exists (a
  dummy hash is verified), so usernames can't be probed by timing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger("reqly.collector")

MIN_PASSWORD_LENGTH = 12
LAST_SEEN_RESOLUTION = timedelta(minutes=1)

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("reqly-dummy-password-for-constant-time-login")


class AuthError(Exception):
    """A user-facing authentication / validation failure."""


def normalize_username(username: str) -> str:
    name = (username or "").strip().lower()
    if not 1 <= len(name) <= 64:
        raise AuthError("username must be 1-64 characters")
    return name


def check_password_strength(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


# argon2 is deliberately slow (tens of ms of CPU); off the event loop, so a
# login can't stall ingest and the dashboard.
async def _hash(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def _verify(password_hash: str, password: str) -> bool:
    def verify() -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(verify)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public(user) -> dict:
    return {"id": user["id"], "username": user["username"], "is_admin": user["is_admin"]}


async def create_user(pool: asyncpg.Pool, username: str, password: str, is_admin: bool = False) -> dict:
    name = normalize_username(username)
    check_password_strength(password)
    try:
        row = await pool.fetchrow(
            "INSERT INTO users (username, password_hash, is_admin) VALUES ($1, $2, $3) "
            "RETURNING id, username, is_admin",
            name, await _hash(password), is_admin,
        )
    except asyncpg.UniqueViolationError:
        raise AuthError(f"user {name!r} already exists")
    return _public(row)


async def set_password(pool: asyncpg.Pool, username: str, password: str) -> None:
    """Sets a new password and ends every session of that user."""
    name = normalize_username(username)
    check_password_strength(password)
    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id = await conn.fetchval(
                "UPDATE users SET password_hash = $2 WHERE username = $1 RETURNING id",
                name, await _hash(password),
            )
            if user_id is None:
                raise AuthError(f"no user {name!r}")
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)


async def bootstrap_admin(pool: asyncpg.Pool, username: str, password: str | None) -> dict | None:
    """Creates the first admin from the environment when there are no users
    yet. Never touches an existing user (change passwords in the dashboard
    or with `python -m app.users set-password`)."""
    if not password:
        return None
    if await pool.fetchval("SELECT EXISTS (SELECT 1 FROM users)"):
        return None
    user = await create_user(pool, username, password, is_admin=True)
    logger.info("Reqly collector: created admin user %r from REQLY_ADMIN_PASSWORD", user["username"])
    return user


async def login(
    pool: asyncpg.Pool, username: str, password: str, ttl: timedelta,
    user_agent: str | None = None, now: datetime | None = None,
) -> tuple[str, datetime, dict]:
    """(token, expires_at, user). Raises AuthError on bad credentials."""
    now = now or datetime.now(timezone.utc)
    try:
        name = normalize_username(username)
    except AuthError:
        name = None
    user = None
    if name is not None:
        user = await pool.fetchrow(
            "SELECT id, username, password_hash, is_admin FROM users WHERE username = $1", name
        )
    valid = await _verify(user["password_hash"] if user else _DUMMY_HASH, password or "")
    if not valid or user is None:
        raise AuthError("invalid username or password")

    token = secrets.token_urlsafe(32)
    expires_at = now + ttl
    async with pool.acquire() as conn:
        async with conn.transaction():
            if _hasher.check_needs_rehash(user["password_hash"]):
                await conn.execute(
                    "UPDATE users SET password_hash = $2 WHERE id = $1", user["id"], await _hash(password)
                )
            await conn.execute("UPDATE users SET last_login_at = $2 WHERE id = $1", user["id"], now)
            await conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at, user_agent) "
                "VALUES ($1, $2, $3, $4, $3, $5)",
                _token_hash(token), user["id"], now, expires_at, (user_agent or "")[:255] or None,
            )
            # housekeeping: expired sessions of anyone
            await conn.execute("DELETE FROM sessions WHERE expires_at < $1", now)
    return token, expires_at, _public(user)


async def session_user(pool: asyncpg.Pool, token: str, now: datetime | None = None) -> dict | None:
    """The user a session token belongs to, or None (unknown or expired)."""
    if not token:
        return None
    now = now or datetime.now(timezone.utc)
    row = await pool.fetchrow(
        """
        SELECT u.id, u.username, u.is_admin, s.last_seen_at
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = $1 AND s.expires_at > $2
        """,
        _token_hash(token), now,
    )
    if row is None:
        return None
    if now - row["last_seen_at"] >= LAST_SEEN_RESOLUTION:
        await pool.execute(
            "UPDATE sessions SET last_seen_at = $2 WHERE token_hash = $1", _token_hash(token), now
        )
    return _public(row)


async def logout(pool: asyncpg.Pool, token: str) -> None:
    await pool.execute("DELETE FROM sessions WHERE token_hash = $1", _token_hash(token))


async def change_password(pool: asyncpg.Pool, user_id: int, current: str, new: str) -> None:
    check_password_strength(new)
    stored = await pool.fetchval("SELECT password_hash FROM users WHERE id = $1", user_id)
    if not await _verify(stored or _DUMMY_HASH, current or "") or stored is None:
        raise AuthError("current password is wrong")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET password_hash = $2 WHERE id = $1", user_id, await _hash(new))
            # other devices are signed out; the caller signs in again
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
