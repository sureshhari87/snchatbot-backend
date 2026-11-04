"""Contact changes do not change login credentials or link accounts."""

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

import customer_profile as profile


async def body(request):
    raw = await request.body()
    profile.require(len(raw) <= 2048, "Profile request too large", 413)
    try:
        data = await request.json()
        profile.require(isinstance(data, dict), "Invalid profile request", 422)
        return data
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Invalid profile request") from None


def install(app, get_db, get_user, mailer):
    def available(response: Response):
        response.headers["Cache-Control"] = "no-store"
        profile.require(profile.enabled(), "Customer details are not enabled", 503)

    def mutate(db, fn):
        try:
            result = fn()
            db.commit()
            return result
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Profile changed. Reload before retrying") from None
        except Exception:
            db.rollback()
            raise

    @app.get("/users/me/customer-profile", dependencies=[Depends(available)])
    def read(user=Depends(get_user), db=Depends(get_db)):
        return profile.read(db, user)

    @app.patch("/users/me/customer-profile", dependencies=[Depends(available)])
    async def update(request: Request, user=Depends(get_user), db=Depends(get_db)):
        data = await body(request)
        return await run_in_threadpool(mutate, db, lambda: profile.save_name(db, user, data))

    @app.post("/users/me/customer-profile/email/request", dependencies=[Depends(available)])
    async def email_request(request: Request, user=Depends(get_user), db=Depends(get_db)):
        data = await body(request)

        def operation():
            row, code = mutate(db, lambda: profile.request_email(db, user, data))
            # Durable reservation before mail; never automatically resend an uncertain request.
            if code is not None:
                try:
                    sent = (
                        mailer(
                            row.email,
                            "Verify your Sona contact email",
                            "Your Sona contact email verification code is "
                            + code
                            + ". It expires in 10 minutes. This does not change your login.",
                        )
                        is True
                    )
                except Exception:
                    sent = False
                code = None
                if sent:
                    db.query(profile.ContactChallenge).filter_by(
                        id=row.id, state="delivery_unknown"
                    ).update({"state": "sent"})
                    db.commit()
                    db.refresh(row)
            return dict(challenge_id=row.id, state=row.state)

        return await run_in_threadpool(operation)

    @app.post("/users/me/customer-profile/email/verify", dependencies=[Depends(available)])
    async def email_verify(
        request: Request, response: Response, user=Depends(get_user), db=Depends(get_db)
    ):
        data = await body(request)
        result, ok = await run_in_threadpool(
            mutate, db, lambda: profile.verify_email(db, user, data)
        )
        if not ok:
            response.status_code = 400
        return result
