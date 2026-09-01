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
