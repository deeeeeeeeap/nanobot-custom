"""Tests for URL validation helpers."""

from nanobot.security.url_validator import (
    X_STATUS_ID_MIN,
    extract_and_validate_urls,
    extract_social_accounts,
    filter_invalid_urls,
    get_max_valid_status_id,
    should_warn_about_urls,
    validate_x_url,
)


def _future_status_id() -> int:
    return get_max_valid_status_id() + 10_000_000


def _valid_status_id() -> int:
    # Pick a stable value well within current valid range.
    return max(X_STATUS_ID_MIN + 1, get_max_valid_status_id() - 10_000_000)


class TestValidateXUrl:
    def test_valid_current_status_id(self):
        url = f"https://x.com/test/status/{_valid_status_id()}"
        result = validate_x_url(url)
        assert result.is_valid
        assert result.url_type == "x"

    def test_invalid_future_status_id(self):
        url = f"https://x.com/test/status/{_future_status_id()}"
        result = validate_x_url(url)
        assert not result.is_valid
        assert result.url_type == "x"

    def test_invalid_past_status_id(self):
        url = "https://x.com/user/status/123456789012345"
        result = validate_x_url(url)
        assert not result.is_valid

    def test_non_x_url_passes(self):
        url = "https://example.com/page"
        result = validate_x_url(url)
        assert result.is_valid
        assert result.url_type == "generic"

    def test_twitter_domain_also_checked(self):
        url = f"https://twitter.com/user/status/{_future_status_id()}"
        result = validate_x_url(url)
        assert not result.is_valid


class TestExtractAndValidateUrls:
    def test_extract_multiple_urls(self):
        text = (
            f"1. https://x.com/user1/status/{_valid_status_id()}\n"
            f"2. https://x.com/user2/status/{_future_status_id()}"
        )
        results = extract_and_validate_urls(text)
        assert len(results) == 2
        assert results[0].is_valid
        assert not results[1].is_valid

    def test_no_urls(self):
        results = extract_and_validate_urls("plain text without url")
        assert len(results) == 0


class TestExtractSocialAccounts:
    def test_extract_account_with_dash(self):
        text = "@Seed_Dance_AI - official video generation account"
        accounts = extract_social_accounts(text)
        assert len(accounts) == 1
        assert accounts[0][0] == "Seed_Dance_AI"

    def test_extract_multiple_accounts(self):
        text = "@VraserX - tech blogger\n@rowancheung: AI analyst"
        accounts = extract_social_accounts(text)
        assert len(accounts) == 2


class TestFilterInvalidUrls:
    def test_filter_replaces_invalid_url(self):
        text = f"Demo: https://x.com/fake/status/{_future_status_id()}"
        filtered, removed = filter_invalid_urls(text)
        assert "[链接已移除:" in filtered
        assert len(removed) == 1

    def test_valid_url_not_filtered(self):
        sid = _valid_status_id()
        text = f"Official link: https://x.com/real/status/{sid}"
        filtered, removed = filter_invalid_urls(text)
        assert str(sid) in filtered
        assert len(removed) == 0


class TestShouldWarnAboutUrls:
    def test_warns_about_fabricated_url(self):
        text = f"Link: https://x.com/test/status/{_future_status_id()}"
        should_warn, message = should_warn_about_urls(text)
        assert should_warn
        assert "检测到可疑链接。" in message

    def test_no_warn_for_valid_urls(self):
        text = f"Real link: https://x.com/user/status/{_valid_status_id()}"
        should_warn, message = should_warn_about_urls(text)
        assert not should_warn
        assert message == ""

