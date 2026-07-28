#!/usr/bin/env python3
"""Account registry — one Instagram account per accounts/<slug>/ directory.

Every script in this repo operates on exactly one account per invocation,
selected by the ACCOUNT environment variable (or --account where a CLI exposes
it). With a single enabled account, selection is automatic, so the original
one-account behaviour needs no flags anywhere.

accounts/<slug>/account.json holds everything that distinguishes an account:
identity, the *names* of its GitHub secrets (never values), brand voice for the
generator, and optional palette overrides for the renderer. Alongside it live
the account's queue/, specs/, strategy.md, token_status.json and
performance.json — the complete state of one account in one directory.

The wrong-account hazard (see CLAUDE.md) is why account.json records both the
expected username AND the expected IG user id: publishers and monitors assert
the live token against both before touching anything. More accounts means more
ways to cross wires, so the guard is config-driven but never optional.

    python accounts.py                  # human-readable list
    python accounts.py --list-json      # workflow matrix (enabled only)
"""
import argparse, glob, io, json, os, sys

ROOT = "accounts"

REQUIRED = ("slug", "username", "ig_user_id", "token_secret", "user_id_secret")


class Account(dict):
    """Account config plus path helpers rooted at accounts/<slug>/."""

    @property
    def root(self):
        return f"{ROOT}/{self['slug']}"

    def path(self, *parts):
        return "/".join((self.root,) + parts)

    @property
    def queue(self):
        return self.path("queue", "schedule.json")

    @property
    def queue_dir(self):
        return self.path("queue")

    @property
    def spec_dir(self):
        return self.path("specs")

    @property
    def strategy(self):
        return self.path("strategy.md")

    @property
    def token_status(self):
        return self.path("token_status.json")

    @property
    def performance(self):
        return self.path("performance.json")

    @property
    def handle(self):
        return "@" + self["username"]


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        raise SystemExit(f"{path}: missing required fields {missing}")
    expected = os.path.basename(os.path.dirname(path))
    if cfg["slug"] != expected:
        raise SystemExit(f"{path}: slug {cfg['slug']!r} does not match its "
                         f"directory {expected!r}")
    return Account(cfg)


def list_accounts(include_disabled=False):
    accts = [_read(p) for p in sorted(glob.glob(f"{ROOT}/*/account.json"))]
    if not include_disabled:
        accts = [a for a in accts if a.get("enabled", True)]
    return accts


def get(slug=None):
    """Resolve one account: explicit slug > ACCOUNT env > the single account.

    Naming a slug explicitly also finds DISABLED accounts — setup work (filling
    a queue, testing sources, dry-running generation) happens before an account
    is enabled. Only the unnamed fallback and the workflow matrices are limited
    to enabled accounts, so a disabled account still can't be reached by cron.
    """
    slug = slug or os.environ.get("ACCOUNT")
    if slug:
        for a in list_accounts(include_disabled=True):
            if a["slug"] == slug:
                return a
        known = ", ".join(a["slug"] for a in list_accounts(include_disabled=True))
        raise SystemExit(f"unknown account {slug!r} (have: {known})")
    accts = list_accounts()
    if not accts:
        raise SystemExit("no enabled accounts under accounts/*/account.json")
    if len(accts) == 1:
        return accts[0]
    known = ", ".join(a["slug"] for a in accts)
    raise SystemExit(f"multiple accounts exist ({known}) — set ACCOUNT or pass "
                     f"--account to say which one you mean")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-json", action="store_true",
                    help="compact JSON for the workflow matrix (enabled only)")
    a = ap.parse_args()

    accts = list_accounts()
    if a.list_json:
        print(json.dumps([
            {"slug": x["slug"], "username": x["username"],
             "token_secret": x["token_secret"],
             "user_id_secret": x["user_id_secret"],
             "api": x.get("api", "instagram_login"),
             "fb_app_id": x.get("fb_app_id", ""),
             # Empty-string fallbacks: secrets[''] resolves to nothing in a
             # workflow expression, so unconfigured accounts just skip Threads.
             "threads_token_secret": (x.get("threads") or {}).get("token_secret", ""),
             "threads_user_id_secret": (x.get("threads") or {}).get("user_id_secret", "")}
            for x in accts
        ], separators=(",", ":")))
        return

    for x in list_accounts(include_disabled=True):
        state = "enabled" if x.get("enabled", True) else "DISABLED"
        print(f"{x['slug']:<16} @{x['username']:<24} {state}")
    if not list_accounts(include_disabled=True):
        print("no accounts yet — run new_account.py")


if __name__ == "__main__":
    sys.exit(main())
