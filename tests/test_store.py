"""Store parsing and privacy checks with synthetic URLs, without a real account."""
import traceback
from synty_mcp.store import SyntyStore, StoreUnavailable, parse_file_blocks, file_kind
from synty_mcp.service import _version_key


def test_real_layout_parser_and_version_order():
    files = parse_file_blocks('POLYGON Test\nUnity_2022_3 | v1_0_1\n(1.5 GB)\nDownload\nPOLYGON Test\nSourceFiles | v1_0_2\n(64 MB)\nDownload')
    assert len(files) == 2
    assert files[0]['size'] == int(1.5 * 1024**3)
    assert file_kind(files[0]['name'], files[0]['variant']) == 'unity'
    assert file_kind(files[1]['name'], files[1]['variant']) == 'source'
    assert _version_key(files[1]['variant']) > _version_key(files[0]['variant'])


def test_listing_error_hides_private_urls(monkeypatch):
    secret = 'https://example.test/private?email=owner@example.test&token=private-token'
    def fail(*args, **kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(SyntyStore, '_list_purchases', fail)
    store = object.__new__(SyntyStore)
    try:
        store.list_purchases()
    except StoreUnavailable:
        text = traceback.format_exc()
        assert secret not in text
        assert 'private-token' not in text
    else:
        raise AssertionError('expected a sanitized failure')
