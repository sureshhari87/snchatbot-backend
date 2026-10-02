"""One operator-confirmed staging batch. Default is read-only; no scheduler."""

import argparse
import json
from collections import Counter
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--limit", type=int, choices=range(1, 6), default=1)
    args = parser.parse_args(argv)
    try:
        if (Path(__file__).resolve().parents[1] / ".env").exists():
            raise ValueError("Use explicit staging environment, not a local dotenv file")
        from database import SessionLocal
        from models import utc_now
        from push_outbox import PushAttempt, PushEvent
        from staging_push import recipients, verify_database

        allowed = recipients()
        verify_database(SessionLocal)
        with SessionLocal() as db:
            count = db.query(PushAttempt).join(PushEvent).filter(
                PushEvent.user_id.in_(allowed), PushAttempt.state == "pending",
                PushAttempt.due_at <= utc_now(),
            ).count()
        print(json.dumps({"staging_identity": "verified", "allowlisted_due_attempts": count,
                          "send_requested": args.send, "limit": args.limit}))
        if not args.send or count == 0:
            return 0
        confirmation = input("Real staging push to allowlisted devices only. Type SEND_STAGING_PUSH: ")
        if confirmation != "SEND_STAGING_PUSH":
            print("Cancelled. No push sent.")
            return 1
        from fcm_sender import run_configured_batch
        from push_controls import delivery_enabled

        if not delivery_enabled():
            raise ValueError("Delivery is disabled")
        outcomes = run_configured_batch(SessionLocal, limit=args.limit)
        print(json.dumps({"staging_batch": dict(Counter(outcomes))}))
        return 0
    except (Exception, KeyboardInterrupt):
        print("Staging push stopped. Inspect configuration privately; no credentials displayed.")
        print("If sending had begun, provider acceptance may already have occurred. Check queue state.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
