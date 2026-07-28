from __future__ import annotations

import pytest

from backend.common.exception import errors
from backend.app.hasn.service.owner_storage_names import (
    display_name_for_upload,
    normalize_storage_name,
    suffixed_name,
)


def test_normalization_is_nfkc_trimmed_space_folded_and_casefolded() -> None:
    assert normalize_storage_name('  Ｆｏｏ　 BAR.txt. ') == 'foo bar.txt'
    assert normalize_storage_name('Straße.TXT') == 'strasse.txt'


@pytest.mark.parametrize('name', ['a/b.txt', r'a\b.txt', 'bad\x00.txt', 'bad\x7f.txt'])
def test_path_and_control_characters_are_rejected(name: str) -> None:
    with pytest.raises(errors.RequestError, match='STORAGE_NAME_INVALID'):
        normalize_storage_name(name)


@pytest.mark.parametrize('name', ['CON', 'con.txt', 'LPT9.log', 'aux.JPG'])
def test_windows_reserved_names_are_rejected(name: str) -> None:
    with pytest.raises(errors.RequestError, match='STORAGE_NAME_INVALID'):
        normalize_storage_name(name)


def test_upload_display_name_is_truncated_without_losing_extension() -> None:
    display = display_name_for_upload('a' * 300 + '.pdf')
    assert len(display) == 255
    assert display.endswith('.pdf')


def test_conflict_suffix_is_inserted_before_extension() -> None:
    assert suffixed_name('报告.pdf', 2) == '报告 (2).pdf'
    assert suffixed_name('.env', 3) == '.env (3)'
    assert len(suffixed_name('a' * 255 + '.pdf', 999)) <= 255
