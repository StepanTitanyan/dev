from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from rise.db.session import Base


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    tracking_id = Column(String, unique=True, nullable=False, index=True)
    salesforce_record_id = Column(String, nullable=True, index=True)

    # Which worker/lender handles this application e.g. "funding_circle"
    worker_type = Column(String, nullable=False, index=True)

    # Generic external reference — the ID assigned by the lender after submission
    external_id = Column(String, nullable=True, index=True)

    status = Column(String, nullable=False, index=True)
    current_step = Column(String, nullable=True)

    raw_input_json = Column(JSONB, nullable=False)
    working_payload_json = Column(JSONB, nullable=True)

    last_error = Column(Text, nullable=True)

    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    steps = relationship(
        "ApplicationStep",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationStep.step_order")

    events = relationship(
        "ApplicationEvent",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationEvent.created_at")

    fc_data = relationship(
        "FcSubmissionData",
        back_populates="application",
        uselist=False,
        cascade="all, delete-orphan")


class ApplicationStep(Base):
    __tablename__ = "application_steps"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False, index=True)

    step_name = Column(String, nullable=False)
    step_order = Column(Integer, nullable=False)
    status = Column(String, nullable=False)

    request_json = Column(JSONB, nullable=True)
    response_json = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    application = relationship("Application", back_populates="steps")


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False, index=True)

    event_type = Column(String, nullable=False, index=True)
    message = Column(Text, nullable=True)
    data = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    application = relationship("Application", back_populates="events")


class FcSubmissionData(Base):
    """FundingCircle-specific submission data linked to a generic Application."""
    __tablename__ = "fc_submission_data"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False, unique=True, index=True)

    fc_application_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    application = relationship("Application", back_populates="fc_data")


class WorkerSession(Base):
    """
    Generic worker authentication state.
    One row per worker_type — each lender worker manages its own session here.
    """
    __tablename__ = "worker_sessions"

    id = Column(Integer, primary_key=True, index=True)
    worker_type = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, nullable=False, default="logged_out")
    is_authenticated = Column(Boolean, nullable=False, default=False)

    # Flexible JSON — stores whatever the worker needs (tokens, session IDs, etc.)
    session_data = Column(JSONB, nullable=True)

    waiting_for_otp_since = Column(DateTime(timezone=True), nullable=True)
    otp_received_at = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OtpMessage(Base):
    __tablename__ = "otp_messages"

    id = Column(Integer, primary_key=True, index=True)

    # Which service/worker this OTP belongs to e.g. "funding_circle"
    service = Column(String, nullable=False, index=True)

    phone_from = Column(String, nullable=True)
    phone_to = Column(String, nullable=True)
    message_body = Column(Text, nullable=True)
    otp_code = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="received")
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
