"""Explicit inbox broadcast snapshot/audit. Local, unapplied migration."""

import sqlalchemy as sa

from alembic import op

revision = "0031_inbox_broadcast"
down_revision = "0030_catalogue_extras"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inbox_broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_key", sa.String(100), nullable=False, unique=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("recipient_ids", sa.JSON(), nullable=False),
        sa.Column("notification_ids", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "recipient_count >= 1 AND recipient_count <= 100", name="ck_broadcast_recipients"
        ),
        sa.CheckConstraint(
            "json_array_length(recipient_ids) = recipient_count AND json_array_length(notification_ids) = recipient_count",
            name="ck_broadcast_snapshot_count",
        ),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_broadcast_digest"),
        sa.CheckConstraint("channel = 'inbox'", name="ck_broadcast_inbox_only"),
    )
    immutable_guards()


def immutable_guards():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION sona_inbox_broadcast_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Inbox broadcast audit is immutable'; END; $$"
        )
        op.execute(
            "CREATE TRIGGER inbox_broadcast_immutable BEFORE UPDATE OR DELETE ON inbox_broadcasts FOR EACH ROW EXECUTE FUNCTION sona_inbox_broadcast_immutable()"
        )
    elif op.get_bind().dialect.name == "sqlite":
        for action in ["UPDATE", "DELETE"]:
            op.execute(
                f"CREATE TRIGGER inbox_broadcast_{action.lower()} BEFORE {action} ON inbox_broadcasts BEGIN SELECT RAISE(ABORT,'Inbox broadcast audit is immutable'); END"
            )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM inbox_broadcasts")).scalar():
        raise RuntimeError("Broadcast audits exist; schema downgrade would discard history")
    op.drop_table("inbox_broadcasts")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION sona_inbox_broadcast_immutable()")
