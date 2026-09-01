"""Validation of environment-variable parsing in config.py."""

import pytest

from osb_opengauss.config import Settings


def test_tablespace_prefix_must_be_single_segment(monkeypatch):
    # openGauss RELATIVE LOCATION allows at most two path levels.
    monkeypatch.setenv("GAUSSDB_TABLESPACE_LOCATION_PREFIX", "a/b")
    with pytest.raises(ValueError, match="single path segment"):
        Settings.from_env()


def test_tablespace_prefix_slashes_are_stripped(monkeypatch):
    monkeypatch.setenv("GAUSSDB_TABLESPACE_LOCATION_PREFIX", "/tenants/")
    assert Settings.from_env().tablespace_location_prefix == "tenants"


def test_storage_mode_vocabulary_enforced(monkeypatch):
    monkeypatch.setenv("GAUSSDB_STORAGE_MODE", "bogus")
    with pytest.raises(ValueError, match="GAUSSDB_STORAGE_MODE"):
        Settings.from_env()


def test_tablespaces_allowlist_is_parsed(monkeypatch):
    monkeypatch.setenv("GAUSSDB_TABLESPACES", " ts_ssd , ts_hdd ,")
    assert Settings.from_env().tablespaces == ("ts_ssd", "ts_hdd")


def test_platform_guid_check_flag_parsed(monkeypatch):
    monkeypatch.setenv("DISABLE_SPACE_ORG_GUID_CHECK", "false")
    assert Settings.from_env().disable_space_org_guid_check is False
    monkeypatch.setenv("DISABLE_SPACE_ORG_GUID_CHECK", "true")
    assert Settings.from_env().disable_space_org_guid_check is True


def test_app_applies_platform_guid_check_setting(tmp_path):
    import dataclasses

    import openbrokerapi.settings
    from conftest import make_env

    from osb_opengauss.app import create_app

    original = openbrokerapi.settings.DISABLE_SPACE_ORG_GUID_CHECK
    try:
        base = make_env(tmp_path).settings
        create_app(dataclasses.replace(base, disable_space_org_guid_check=False))
        assert openbrokerapi.settings.DISABLE_SPACE_ORG_GUID_CHECK is False
        create_app(base)
        assert openbrokerapi.settings.DISABLE_SPACE_ORG_GUID_CHECK is True
    finally:
        openbrokerapi.settings.DISABLE_SPACE_ORG_GUID_CHECK = original
