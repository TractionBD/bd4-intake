#!/usr/bin/env python3
"""Full wipe a BD4 user from one or more environments.

Mirrors ProfileResetService.reset_full() — deletes all user data except
the auth account and the users row itself. Resets onboarding flags.

Usage:
    python scripts/wipebd4user.py <env> <email> [<email2>]
    python scripts/wipebd4user.py prod justin@bd4pros.com justin@tractionbd.com
    python scripts/wipebd4user.py staging,prod justin@tractionbd.com
    python scripts/wipebd4user.py all justin@bd4pros.com justin@tractionbd.com

Envs: dev (hosteddev), staging, prod, all
Email2: fallback tried if email1 not found in a given env.
"""

from __future__ import annotations

import json
import os
import sys

# ── Railway / env config ──────────────────────────────────────────────────────

RAILWAY_PROJECT_ID = "00a52870-d271-492f-a5ff-a73f8f528728"
RAILWAY_API_SERVICE_ID = "9e49afd9-6b65-4558-8606-0c5de24e0602"

ENV_IDS = {
    "dev": "978c97cf-60ab-4b7a-ace2-388809e59d66",
    "staging": "0dcf8997-dada-4127-9780-63eae2a5bb21",
    "prod": "0a952544-1b5a-4779-a441-a9cf747b38bf",
}

ENV_ALIASES = {
    "hosteddev": "dev",
    "development": "dev",
    "production": "prod",
}

# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_railway_token() -> str:
    # 1. Railway CLI session token (preferred — always current)
    config_path = os.path.expanduser("~/.railway/config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        token = cfg.get("user", {}).get("token", "")
        if token:
            return token

    # 2. Explicit env var
    token = os.environ.get("RAILWAY_API_TOKEN", "")
    if token:
        return token

    # 3. .env file
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("RAILWAY_API_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if token:
                        return token

    print("ERROR: no Railway token found (~/.railway/config.json or RAILWAY_API_TOKEN)", file=sys.stderr)
    sys.exit(1)


def _fetch_database_url(env_name: str, token: str) -> str:
    import urllib.request

    env_id = ENV_IDS[env_name]
    query = {
        "query": f"""query {{
            variables(
                projectId: "{RAILWAY_PROJECT_ID}",
                environmentId: "{env_id}",
                serviceId: "{RAILWAY_API_SERVICE_ID}"
            )
        }}"""
    }
    req = urllib.request.Request(
        "https://backboard.railway.com/graphql/v2",
        data=json.dumps(query).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (railway-cli)",  # Cloudflare blocks default Python UA
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    db_url = data["data"]["variables"].get("DATABASE_URL", "")
    if not db_url:
        print(f"ERROR: DATABASE_URL not found for {env_name}", file=sys.stderr)
        sys.exit(1)
    # Switch to direct port 5432 (not pgbouncer 6543) for DELETE transactions
    return db_url.replace(":6543/", ":5432/")


def _psycopg2():
    import psycopg2  # noqa: PLC0415
    return psycopg2


def _pg_sql():
    from psycopg2 import sql  # noqa: PLC0415
    return sql


# ── Wipe logic ────────────────────────────────────────────────────────────────

_PROFILE_TREE_TABLES = [
    "voice_profiles",
    "personal_profiles",
    "career_profiles",
]

_USER_SCOPED_TABLES = [
    "target_profiles",
    "contacts",
    "action_preferences",
    "integration_accounts",
    "email_metadata",
]


def _count(cur, table: str, col: str, val: str) -> int:
    psql = _pg_sql()
    cur.execute(
        psql.SQL("SELECT COUNT(*) FROM {} WHERE {} = %s").format(
            psql.Identifier(table), psql.Identifier(col)
        ),
        (val,),
    )
    return cur.fetchone()[0]


def wipe_user(env_name: str, emails: list[str], db_url: str) -> None:
    psycopg2 = _psycopg2()

    print(f"\n{'='*60}")
    print(f"  {env_name.upper()}")
    print(f"{'='*60}")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # 1. Resolve user
        user_id = user_email = user_name = None
        for email in emails:
            cur.execute(
                "SELECT id, email, name FROM users WHERE LOWER(email) = LOWER(%s)",
                (email,),
            )
            row = cur.fetchone()
            if row:
                user_id, user_email, user_name = str(row[0]), row[1], row[2]
                break

        if not user_id:
            print(f"  NOT FOUND — tried: {', '.join(emails)}. Skipping.")
            conn.rollback()
            return

        print(f"  user:      {user_email} ({user_name})")
        print(f"  user_id:   {user_id}")

        # 2. Resolve user_profile
        cur.execute("SELECT id FROM user_profiles WHERE user_id = %s", (user_id,))
        up_row = cur.fetchone()
        user_profile_id = str(up_row[0]) if up_row else None
        print(f"  profile_id: {user_profile_id or '(none)'}")

        # 3. Capture before-flags
        cur.execute(
            "SELECT onboarding_complete, onboarding_completed_at, session_capacity "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        flags_before = cur.fetchone()

        # 4. Begin deletions — print per-table counts
        print()
        print(f"  {'Table':<28} {'Before':>6}  {'Deleted':>7}  {'After':>6}")
        print(f"  {'-'*28} {'-'*6}  {'-'*7}  {'-'*6}")

        psql = _pg_sql()

        if user_profile_id:
            for tbl in _PROFILE_TREE_TABLES:
                before = _count(cur, tbl, "user_profile_id", user_profile_id)
                cur.execute(
                    psql.SQL("DELETE FROM {} WHERE user_profile_id = %s").format(psql.Identifier(tbl)),
                    (user_profile_id,),
                )
                deleted = cur.rowcount
                print(f"  {tbl:<28} {before:>6}  {deleted:>7}  {before - deleted:>6}")

            before = _count(cur, "user_profiles", "id", user_profile_id)
            cur.execute("DELETE FROM user_profiles WHERE id = %s", (user_profile_id,))
            deleted = cur.rowcount
            print(f"  {'user_profiles':<28} {before:>6}  {deleted:>7}  {before - deleted:>6}")
        else:
            for tbl in _PROFILE_TREE_TABLES + ["user_profiles"]:
                print(f"  {tbl:<28} {'0':>6}  {'0':>7}  {'0':>6}")

        for tbl in _USER_SCOPED_TABLES:
            before = _count(cur, tbl, "user_id", user_id)
            cur.execute(
                psql.SQL("DELETE FROM {} WHERE user_id = %s").format(psql.Identifier(tbl)),
                (user_id,),
            )
            deleted = cur.rowcount
            print(f"  {tbl:<28} {before:>6}  {deleted:>7}  {before - deleted:>6}")

        # 5. Reset flags
        cur.execute(
            """
            UPDATE users
            SET onboarding_complete = false,
                onboarding_completed_at = NULL,
                session_capacity = 5,
                updated_at = NOW()
            WHERE id = %s
            """,
            (user_id,),
        )

        conn.commit()

        # 6. Report flag change
        cur.execute(
            "SELECT onboarding_complete, onboarding_completed_at, session_capacity "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        flags_after = cur.fetchone()

        print()
        print(f"  users flags:")
        print(f"    onboarding_complete:    {flags_before[0]} → {flags_after[0]}")
        print(f"    onboarding_completed_at:{flags_before[1]} → {flags_after[1]}")
        print(f"    session_capacity:       {flags_before[2]} → {flags_after[2]}")
        print()
        print("  ✓ COMMITTED")

    except Exception as e:
        conn.rollback()
        print(f"\n  ✗ ROLLED BACK — {e}", file=sys.stderr)
        raise
    finally:
        cur.close()
        conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    env_arg = sys.argv[1].lower()
    emails = sys.argv[2:]

    # Resolve env aliases
    env_arg = ENV_ALIASES.get(env_arg, env_arg)

    if env_arg == "all":
        envs = list(ENV_IDS.keys())
    else:
        envs = [ENV_ALIASES.get(e, e) for e in env_arg.split(",")]
        unknown = [e for e in envs if e not in ENV_IDS]
        if unknown:
            print(f"ERROR: unknown env(s): {', '.join(unknown)}")
            print(f"Valid: {', '.join(ENV_IDS)} (or 'all')")
            sys.exit(1)

    for env_name in envs:
        # CI: accept pre-set DATABASE_URL env vars to skip Railway lookup
        ci_var = f"BD4_DB_URL_{env_name.upper()}"
        db_url = os.environ.get(ci_var, "")
        if not db_url:
            token = _get_railway_token()
            db_url = _fetch_database_url(env_name, token)
        else:
            db_url = db_url.replace(":6543/", ":5432/")
        wipe_user(env_name, emails, db_url)

    print("\nDone.")


if __name__ == "__main__":
    main()
