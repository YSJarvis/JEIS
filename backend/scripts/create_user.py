"""Create a login.  Examples:
  python -m scripts.create_user strategist.anita --role ADMIN --name "Anita Rao"
  python -m scripts.create_user mgr.hapur --role MANAGER --state UP --constituency Hapur --name "Hapur Manager"
  python -m scripts.create_user cad-up-hap-001 --role CADRE --state UP --cadre CAD-UP-HAP-001 --name "Ravi Kumar"
The password is read from the terminal (or VOTERENGINE_NEW_PASSWORD)."""
import argparse
import getpass
import os

from app import db
from app.auth import create_user

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("username")
    ap.add_argument("--role", required=True, choices=["ADMIN", "MANAGER", "CADRE"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--state")
    ap.add_argument("--constituency")
    ap.add_argument("--cadre", dest="cadre_id")
    a = ap.parse_args()
    pw = os.environ.get("VOTERENGINE_NEW_PASSWORD") or getpass.getpass("Password (8+ chars): ")
    with db.session() as c:
        db.init_schema(c)
        uid = create_user(c, a.username, pw, a.role, a.name, a.state, a.constituency, a.cadre_id)
    print("created", uid)
