from __future__ import annotations

import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    JSON,
)
from zaddesh_core.database import Base


class SnmpMib(Base):
    __tablename__ = "snmp_mibs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    parsed_metadata = Column(JSON, default=dict, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class SnmpTrap(Base):
    __tablename__ = "snmp_traps"

    id = Column(Integer, primary_key=True, index=True)
    source_ip = Column(String(64), nullable=False, index=True)
    oid = Column(String(255), nullable=False, index=True)
    trap_type = Column(String(64), nullable=False)
    value = Column(String(255), nullable=True)
    payload = Column(JSON, default=dict, nullable=False)
    received_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)

