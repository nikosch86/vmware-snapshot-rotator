"""Tests for password resolution and TLS handling."""
import types

import snapshot_rotator
from snapshot_rotator import PASSWORD_ENV_VAR, connect, resolve_password


def _args(password=None, host="esx", user="root"):
    return types.SimpleNamespace(password=password, host=host, user=user)


def test_password_flag_takes_precedence():
    pw = resolve_password(_args(password="flagpw"), env={PASSWORD_ENV_VAR: "envpw"})
    assert pw == "flagpw"


def test_password_from_env_when_no_flag():
    pw = resolve_password(_args(), env={PASSWORD_ENV_VAR: "envpw"})
    assert pw == "envpw"


def test_password_prompts_as_last_resort():
    prompts = []

    def fake_prompt(message):
        prompts.append(message)
        return "promptpw"

    pw = resolve_password(_args(), env={}, prompt=fake_prompt)
    assert pw == "promptpw"
    assert prompts and "esx" in prompts[0] and "root" in prompts[0]


def test_connect_verifies_tls_by_default(monkeypatch):
    captured = {}

    def fake_smart_connect(**kwargs):
        captured.update(kwargs)
        return "si"

    monkeypatch.setattr(snapshot_rotator, "SmartConnect", fake_smart_connect)
    connect("esx", "root", "pw", 443)
    # secure default: do NOT disable verification
    assert "disableSslCertValidation" not in captured


def test_connect_insecure_disables_verification(monkeypatch):
    captured = {}

    def fake_smart_connect(**kwargs):
        captured.update(kwargs)
        return "si"

    monkeypatch.setattr(snapshot_rotator, "SmartConnect", fake_smart_connect)
    connect("esx", "root", "pw", 443, insecure=True)
    assert captured.get("disableSslCertValidation") is True
