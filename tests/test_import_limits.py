"""Tests for P3 import hardening (DEC-172): the per-file size cap and the
NaN/Infinity-literal rejection in ``load_json_capped``, plus that oversized
files degrade gracefully (quarantined / friendly error, never a hang or a
multi-GB read) at the real load sites.
"""

from __future__ import annotations

import json

import pytest

from control_ofc.paths import MAX_IMPORT_BYTES, load_json_capped


class TestLoadJsonCapped:
    def test_under_cap_parses(self, tmp_path):
        p = tmp_path / "ok.json"
        p.write_text(json.dumps({"a": 1, "b": [1, 2, 3]}))
        assert load_json_capped(p) == {"a": 1, "b": [1, 2, 3]}

    def test_at_cap_is_inclusive(self, tmp_path):
        """A file exactly at the cap is accepted (boundary is inclusive)."""
        p = tmp_path / "atcap.json"
        doc = b"[1, 2, 3]"  # 9 bytes
        p.write_bytes(doc)
        assert load_json_capped(p, max_bytes=len(doc)) == [1, 2, 3]

    def test_one_over_cap_rejected(self, tmp_path):
        p = tmp_path / "over.json"
        p.write_bytes(b"[1, 2, 3]")  # 9 bytes
        with pytest.raises(ValueError, match="exceeds"):
            load_json_capped(p, max_bytes=8)

    def test_default_cap_is_4_mib(self):
        assert MAX_IMPORT_BYTES == 4 * 1024 * 1024

    def test_oversized_default_rejected_without_full_read(self, tmp_path):
        """A file far larger than the default cap is rejected — the bounded read
        never pulls more than ``cap + 1`` bytes into memory, so no OOM/hang."""
        p = tmp_path / "huge.json"
        p.write_bytes(b'{"k": "' + b"A" * (MAX_IMPORT_BYTES + 1000) + b'"}')
        with pytest.raises(ValueError, match="exceeds"):
            load_json_capped(p)

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_nonfinite_literal_rejected(self, token, tmp_path):
        """The non-standard JSON constants json accepts by default are rejected."""
        p = tmp_path / "c.json"
        p.write_text(f'{{"v": {token}}}')
        with pytest.raises(ValueError, match="non-finite"):
            load_json_capped(p)

    def test_finite_numbers_still_parse(self, tmp_path):
        p = tmp_path / "fin.json"
        p.write_text('{"v": 1.5, "n": -3, "big": 1e6}')
        assert load_json_capped(p) == {"v": 1.5, "n": -3, "big": 1e6}

    def test_malformed_json_raises_valueerror(self, tmp_path):
        """``json.JSONDecodeError`` is a ``ValueError`` subclass, so the existing
        callers' ``except ValueError`` catches a malformed file uniformly."""
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        with pytest.raises(ValueError):
            load_json_capped(p)


class TestOversizedImportIntegration:
    """Oversized files at the real load sites are handled, not fatal."""

    def test_oversized_profile_in_store_is_quarantined(self, tmp_path):
        """The profile-store scan quarantines an oversized file (records it in
        ``failed``) and still surfaces the good sibling — it does not raise."""
        from control_ofc.services.profile_service import collect_local_profiles_for_import

        store = tmp_path / "profiles"
        store.mkdir()
        good = {"id": "g1", "name": "Good", "version": 7, "controls": [], "curves": []}
        (store / "good.json").write_text(json.dumps(good))
        (store / "huge.json").write_bytes(
            b'{"id": "h", "name": "' + b"A" * (MAX_IMPORT_BYTES + 100) + b'"}'
        )

        coll = collect_local_profiles_for_import(directory=store)

        assert any(c.name == "Good" for c in coll.ready)
        assert any("huge.json" in path for path, _reason in coll.failed)

    def test_oversized_settings_import_raises_clean_valueerror(self, tmp_path):
        """``import_settings`` surfaces a clean ValueError (its callers render a
        friendly 'Import failed' message) rather than reading the file wholesale."""
        from control_ofc.services.app_settings_service import AppSettingsService

        big = tmp_path / "settings.json"
        big.write_bytes(b'{"x": "' + b"A" * (MAX_IMPORT_BYTES + 100) + b'"}')

        svc = AppSettingsService()
        with pytest.raises(ValueError, match="exceeds"):
            svc.import_settings(big)
