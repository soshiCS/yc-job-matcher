"""One-time login for the authenticated YC search.

Run this once (and again whenever your session expires):

    python login.py

A browser window opens. Log in to workatastartup.com normally. Once you're in,
the session is saved locally and all future authenticated searches reuse it.
"""

from app.yc_browser import login

if __name__ == "__main__":
    login()
