"""Recent product history and audited Home content. Local, unapplied migration."""

import sqlalchemy as sa

from alembic import op

revision = "0030_catalogue_extras"
down_revision = "0029_voucher_funding"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recently_viewed_products",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("viewed_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "catalogue_home_layout",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_catalogue_home_layout_singleton"),
        sa.CheckConstraint("version >= 1", name="ck_catalogue_home_layout_version"),
    )
    op.create_table(
        "catalogue_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("audience", sa.String(20), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_catalogue_campaign_version"),
        sa.CheckConstraint(
            "audience IN ('global','customer')", name="ck_catalogue_campaign_audience"
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_catalogue_campaign_dates"),
    )
    op.create_table(
        "catalogue_campaign_recipients",
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("catalogue_campaigns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
    )
    op.create_table(
        "catalogue_content_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("resource", sa.String(20), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION sona_catalogue_audit_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Catalogue content audit is immutable'; END;
            $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER catalogue_audit_immutable BEFORE UPDATE OR DELETE ON catalogue_content_audit FOR EACH ROW EXECUTE FUNCTION sona_catalogue_audit_immutable()"
        )
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER catalogue_audit_{action.lower()} BEFORE {action} ON catalogue_content_audit BEGIN SELECT RAISE(ABORT, 'Catalogue content audit is immutable'); END"
            )


def downgrade():
    bind = op.get_bind()
    tables = (
        "recently_viewed_products",
        "catalogue_home_layout",
        "catalogue_campaigns",
        "catalogue_campaign_recipients",
        "catalogue_content_audit",
    )
    if any(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() for table in tables):
        raise RuntimeError("Catalogue extras contain data; archive and reconcile before downgrade")
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION sona_catalogue_audit_immutable() CASCADE")
    for table in reversed(tables):
        op.drop_table(table)
