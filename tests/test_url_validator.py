"""URL 验证器测试"""

import pytest
from nanobot.security.url_validator import (
    validate_x_url,
    extract_and_validate_urls,
    extract_social_accounts,
    filter_invalid_urls,
    should_warn_about_urls,
)


class TestValidateXUrl:
    """测试 X URL 验证"""
    
    def test_valid_current_status_id(self):
        """测试当前有效范围内的 ID"""
        url = "https://x.com/test/status/1885900762145300643"
        result = validate_x_url(url)
        assert result.is_valid
        assert result.url_type == "x"
    
    def test_invalid_future_status_id(self):
        """测试过大的 Status ID（未来日期）被检测"""
        # 这个 ID 来自用户案例 - 模型编造的链接
        url = "https://x.com/VraserX/status/2020403704298422376"
        result = validate_x_url(url)
        assert not result.is_valid
        assert "过大" in result.reason
        assert result.url_type == "x"
    
    def test_invalid_past_status_id(self):
        """测试过小的 Status ID（过早日期）被检测"""
        url = "https://x.com/user/status/123456789012345"
        result = validate_x_url(url)
        assert not result.is_valid
        assert "过小" in result.reason
    
    def test_non_x_url_passes(self):
        """测试非 X 链接跳过验证"""
        url = "https://example.com/page"
        result = validate_x_url(url)
        assert result.is_valid
        assert result.url_type == "generic"
    
    def test_twitter_domain_also_checked(self):
        """测试 twitter.com 域名也被检查"""
        url = "https://twitter.com/user/status/2020403704298422376"
        result = validate_x_url(url)
        assert not result.is_valid


class TestExtractAndValidateUrls:
    """测试从文本中提取并验证 URL"""
    
    def test_extract_multiple_urls(self):
        """测试提取多个 URL"""
        text = """
        这里有两个链接：
        1. https://x.com/user1/status/1885900762145300643
        2. https://x.com/user2/status/2020403704298422376
        """
        results = extract_and_validate_urls(text)
        assert len(results) == 2
        assert results[0].is_valid  # 第一个有效
        assert not results[1].is_valid  # 第二个无效（未来 ID）
    
    def test_no_urls(self):
        """测试没有 URL 的文本"""
        text = "这是一段普通文本，没有链接。"
        results = extract_and_validate_urls(text)
        assert len(results) == 0


class TestExtractSocialAccounts:
    """测试提取社交媒体账号"""
    
    def test_extract_account_with_dash(self):
        """测试提取 @username - 描述 格式"""
        text = "@Seed_Dance_AI - 官方视频生成模型账号"
        accounts = extract_social_accounts(text)
        assert len(accounts) == 1
        assert accounts[0][0] == "Seed_Dance_AI"
    
    def test_extract_multiple_accounts(self):
        """测试提取多个账号"""
        text = """
        @VraserX - 技术博主
        @rowancheung: AI 领域分析师
        """
        accounts = extract_social_accounts(text)
        assert len(accounts) == 2


class TestFilterInvalidUrls:
    """测试过滤无效 URL"""
    
    def test_filter_replaces_invalid_url(self):
        """测试无效 URL 被替换为警告"""
        text = "点击这个链接查看演示：https://x.com/fake/status/2020403704298422376"
        filtered, removed = filter_invalid_urls(text)
        assert "⚠️ 链接已移除" in filtered
        assert len(removed) == 1
    
    def test_valid_url_not_filtered(self):
        """测试有效 URL 不被过滤"""
        text = "官方链接：https://x.com/real/status/1885900762145300643"
        filtered, removed = filter_invalid_urls(text)
        assert "1885900762145300643" in filtered
        assert len(removed) == 0


class TestShouldWarnAboutUrls:
    """测试 URL 警告检测"""
    
    def test_warns_about_fabricated_url(self):
        """测试对编造的 URL 发出警告"""
        text = "这是链接：https://x.com/VraserX/status/2020403704298422376"
        should_warn, message = should_warn_about_urls(text)
        assert should_warn
        assert "可疑链接" in message
    
    def test_no_warn_for_valid_urls(self):
        """测试有效 URL 不发出警告"""
        text = "真实链接：https://x.com/user/status/1885900762145300643"
        should_warn, message = should_warn_about_urls(text)
        assert not should_warn
        assert message == ""
