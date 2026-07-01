"""Remove jobs that are no longer live on YC (removed/filled) from the database.

Run periodically:

    python prune.py            # check every saved job and delete the gone ones
    python prune.py --dry-run  # show what would be removed, delete nothing
"""

import argparse

from app.prune import prune_removed_jobs

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prune removed/filled jobs from the DB.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only; don't delete.")
    args = parser.parse_args()

    r = prune_removed_jobs(dry_run=args.dry_run)
    print(
        f"Checked {r['checked']} jobs: {r['alive']} alive, "
        f"{len(r['gone'])} gone, {r['unknown']} unknown (kept)."
    )
    if r["gone"]:
        verb = "Would remove" if args.dry_run else "Removed"
        print(f"{verb} {len(r['gone'])} job(s): {', '.join(r['gone'])}")
    else:
        print("Nothing to remove — all saved jobs are still live.")
