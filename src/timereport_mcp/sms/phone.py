"""Telephone normalization to E.164."""

from __future__ import annotations

import hashlib

import phonenumbers
from phonenumbers import NumberParseException


def normalize_e164(raw: str, *, region: str = "SE") -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = phonenumbers.parse(text, region)
    except NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def telephone_hash(e164: str) -> str:
    return hashlib.sha256(e164.encode("utf-8")).hexdigest()
