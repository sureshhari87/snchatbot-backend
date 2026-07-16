import argparse
import getpass
import re

from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError

from config import PASSWORD_MIN_LENGTH
from database import SessionLocal
from models import User


password_hash = PasswordHash.recommended()


def password_strength_error(password: str) -> str | None:
    if (
        len(password) < PASSWORD_MIN_LENGTH
        or not re.search(r"[A-Za-z]", password)
        or not re.search(r"\d", password)
    ):
        return (
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters "
            "and include at least one letter and one number"
        )
    return None


def default_username(email: str) -> str:
    local_part = email.split("@", 1)[0].lower()
    username = re.sub(r"[^a-z0-9_]+", "_", local_part).strip("_")
    return username or "admin"


def prompt_password() -> str:
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm admin password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    return password


def create_or_promote_admin(
    email: str,
    username: str,
    password: str,
    promote_existing: bool,
) -> str:
    normalized_email = email.lower().strip()
    db = SessionLocal()
    try:
        existing_user = db.query(User).filter(User.email == normalized_email).first()
        username_owner = db.query(User).filter(User.username == username).first()

        if existing_user:
            if not promote_existing:
                raise SystemExit(
                    "User already exists. Re-run with --promote-existing to make it admin."
                )
            if username_owner and username_owner.id != existing_user.id:
                raise SystemExit("Username is already used by another account.")
            existing_user.is_admin = True
            existing_user.is_verified = True
            existing_user.username = username
            db.commit()
            return f"Promoted existing user to admin: {normalized_email}"

        if username_owner:
            raise SystemExit("Username is already used by another account.")

        admin_user = User(
            username=username,
            email=normalized_email,
            hashed_password=password_hash.hash(password),
            is_verified=True,
            is_admin=True,
        )
        db.add(admin_user)
        db.commit()
        return f"Created admin user: {normalized_email}"
    except IntegrityError as exc:
        db.rollback()
        raise SystemExit(f"Could not create admin user because of a database constraint: {exc}") from exc
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or promote a real admin user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--promote-existing", action="store_true")
    args = parser.parse_args()

    password = args.password or prompt_password()
    password_error = password_strength_error(password)
    if password_error:
        raise SystemExit(password_error)

    username = args.username or default_username(args.email)
    message = create_or_promote_admin(
        email=args.email,
        username=username,
        password=password,
        promote_existing=args.promote_existing,
    )
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
