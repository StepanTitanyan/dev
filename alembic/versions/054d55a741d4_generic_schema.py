"""generic_schema

Revision ID: 054d55a741d4
Revises:
Create Date: 2026-04-04

Changes:
  - applications: add worker_type, rename external_application_id -> external_id
  - fc_submission_data: new table for FC-specific data (backfilled from applications)
  - worker_sessions: new generic table replaces worker_auth_state
  - worker_auth_state: dropped
  - otp_messages: add service column (backfilled to 'funding_circle')
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '054d55a741d4'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # applications — add worker_type, rename external_application_id      #
    # ------------------------------------------------------------------ #
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='applications' AND column_name='worker_type'
            ) THEN
                ALTER TABLE applications ADD COLUMN worker_type VARCHAR;
            END IF;
        END $$
    """)

    op.execute("UPDATE applications SET worker_type = 'funding_circle' WHERE worker_type IS NULL")
    op.execute("ALTER TABLE applications ALTER COLUMN worker_type SET NOT NULL")

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename='applications' AND indexname='ix_applications_worker_type'
            ) THEN
                CREATE INDEX ix_applications_worker_type ON applications (worker_type);
            END IF;
        END $$
    """)

    # Rename external_application_id -> external_id (skip if already renamed)
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='applications' AND column_name='external_application_id'
            ) THEN
                ALTER TABLE applications RENAME COLUMN external_application_id TO external_id;
            END IF;
        END $$
    """)

    # ------------------------------------------------------------------ #
    # fc_submission_data — new table, backfill from applications          #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS fc_submission_data (
            id SERIAL PRIMARY KEY,
            application_id INTEGER NOT NULL UNIQUE REFERENCES applications(id),
            fc_application_id VARCHAR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename='fc_submission_data' AND indexname='ix_fc_submission_data_application_id'
            ) THEN
                CREATE INDEX ix_fc_submission_data_application_id ON fc_submission_data (application_id);
            END IF;
        END $$
    """)

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename='fc_submission_data' AND indexname='ix_fc_submission_data_fc_application_id'
            ) THEN
                CREATE INDEX ix_fc_submission_data_fc_application_id ON fc_submission_data (fc_application_id);
            END IF;
        END $$
    """)

    op.execute("""
        INSERT INTO fc_submission_data (application_id, fc_application_id)
        SELECT id, external_id
        FROM applications
        WHERE worker_type = 'funding_circle'
        ON CONFLICT (application_id) DO NOTHING
    """)

    # ------------------------------------------------------------------ #
    # worker_sessions — replaces worker_auth_state                        #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS worker_sessions (
            id SERIAL PRIMARY KEY,
            worker_type VARCHAR NOT NULL UNIQUE,
            status VARCHAR NOT NULL DEFAULT 'logged_out',
            is_authenticated BOOLEAN NOT NULL DEFAULT false,
            session_data JSONB,
            waiting_for_otp_since TIMESTAMPTZ,
            otp_received_at TIMESTAMPTZ,
            last_login_at TIMESTAMPTZ,
            last_error TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename='worker_sessions' AND indexname='ix_worker_sessions_worker_type'
            ) THEN
                CREATE INDEX ix_worker_sessions_worker_type ON worker_sessions (worker_type);
            END IF;
        END $$
    """)

    # Migrate from worker_auth_state if it still exists
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name='worker_auth_state'
            ) THEN
                INSERT INTO worker_sessions (
                    worker_type, status, is_authenticated,
                    session_data,
                    waiting_for_otp_since, otp_received_at,
                    last_login_at, last_error, updated_at)
                SELECT
                    'funding_circle',
                    status,
                    is_authenticated,
                    CASE WHEN auth_session_token IS NOT NULL
                         THEN jsonb_build_object('auth_session_token', auth_session_token)
                         ELSE NULL END,
                    waiting_for_otp_since,
                    otp_received_at,
                    last_login_at,
                    last_error,
                    updated_at
                FROM worker_auth_state
                LIMIT 1
                ON CONFLICT (worker_type) DO NOTHING;

                DROP TABLE worker_auth_state;
            END IF;
        END $$
    """)

    # ------------------------------------------------------------------ #
    # otp_messages — add service column                                   #
    # ------------------------------------------------------------------ #
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='otp_messages' AND column_name='service'
            ) THEN
                ALTER TABLE otp_messages ADD COLUMN service VARCHAR;
            END IF;
        END $$
    """)

    op.execute("UPDATE otp_messages SET service = 'funding_circle' WHERE service IS NULL")
    op.execute("ALTER TABLE otp_messages ALTER COLUMN service SET NOT NULL")

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename='otp_messages' AND indexname='ix_otp_messages_service'
            ) THEN
                CREATE INDEX ix_otp_messages_service ON otp_messages (service);
            END IF;
        END $$
    """)


def downgrade() -> None:
    # otp_messages
    op.drop_index("ix_otp_messages_service", table_name="otp_messages")
    op.drop_column("otp_messages", "service")

    # Recreate worker_auth_state
    op.create_table(
        "worker_auth_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("auth_session_token", sa.String(), nullable=True),
        sa.Column("latest_otp_code", sa.String(), nullable=True),
        sa.Column("otp_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waiting_for_otp_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("is_authenticated", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"))

    op.drop_table("worker_sessions")

    op.drop_table("fc_submission_data")

    op.alter_column("applications", "external_id", new_column_name="external_application_id")

    op.drop_index("ix_applications_worker_type", table_name="applications")
    op.drop_column("applications", "worker_type")
