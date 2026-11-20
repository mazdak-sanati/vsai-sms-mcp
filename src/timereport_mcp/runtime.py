"""Runtime wiring for DB sessions and SMS gateway."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from timereport_mcp.config import AppConfig
from timereport_mcp.persistence.db import (
    create_db_engine,
    ensure_sqlite_parent,
    init_db,
    make_session_factory,
)
from timereport_mcp.sms.elks import ElksSmsGateway
from timereport_mcp.sms.gateway import MockSmsGateway, SmsGateway


@dataclass
class Runtime:
    config: AppConfig
    session_factory: sessionmaker[Session]
    gateway: SmsGateway


def build_runtime(
    config: AppConfig,
    *,
    gateway: SmsGateway | None = None,
) -> Runtime:
    ensure_sqlite_parent(config.settings.database_url)
    engine = create_db_engine(config.settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    if gateway is None:
        sms = config.settings.sms
        if sms.provider == "mock":
            gateway = MockSmsGateway()
        elif sms.provider == "elks" and sms.live_send:
            assert sms.elks_username and sms.elks_password and sms.elks_from
            gateway = ElksSmsGateway(
                username=sms.elks_username,
                password=sms.elks_password,
                sender=sms.elks_from,
            )
        else:
            raise RuntimeError(
                "SMS provider configuration is invalid; use SMS_PROVIDER=mock "
                "or SMS_PROVIDER=elks with SMS_LIVE_SEND=true and credentials."
            )
    return Runtime(config=config, session_factory=factory, gateway=gateway)
