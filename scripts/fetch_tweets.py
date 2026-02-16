#!/usr/bin/env python3
"""
推特每日抓取脚本 - 独立运行，无需 AI 参与。

用法：
    AUTH_TOKEN='xxx' CT0='xxx' python3 fetch_tweets.py

输出：
    /root/.nanobot/workspace/twitter_daily_summary.md
    供 nanobot cron 的 AI 读取并格式化发送。
"""

import json
import subprocess
import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


# === 配置 ===
WATCHLIST_PATH = Path("/root/.nanobot/workspace/twitter_watchlist.txt")
OUTPUT_PATH = Path("/root/.nanobot/workspace/twitter_daily_summary.md")
RAW_DIR = Path("/root/.nanobot/workspace/twitter_raw")
DELAY_BETWEEN_USERS = 8  # 秒，避免 HTTP 429 rate limit
BIRD_TIMEOUT = 60  # 单个 bird 命令超时
TOP_N = 20  # 最终保留的热门推文总量
USER_TOP_N = 10  # 关注用户推文展示数量
SEARCH_TOP_N = 5  # 搜索推文展示数量
SEARCH_SCORE_BOOST = 1.5  # 搜索推文热度加权系数
MAX_AGE_HOURS = 48  # 只保留最近 N 小时的推文

# 搜索关键词文件（通过 Web 面板管理）
SEARCH_QUERIES_PATH = Path("/root/.nanobot/workspace/twitter_search_queries.txt")
DEFAULT_SEARCH_QUERIES = [
    "AI breakthrough OR AGI OR artificial intelligence",
    "LLM OR GPT OR Claude OR Gemini",
    "AI agent OR AI coding OR AI model",
    "AI 人工智能 OR 大模型 OR 深度学习",
]


def load_search_queries() -> list[str]:
    """从文件加载搜索关键词，不存在则初始化默认值。"""
    if not SEARCH_QUERIES_PATH.exists():
        SEARCH_QUERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEARCH_QUERIES_PATH.write_text("\n".join(DEFAULT_SEARCH_QUERIES) + "\n")
        return DEFAULT_SEARCH_QUERIES[:]
    
    queries = []
    for line in SEARCH_QUERIES_PATH.read_text().strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            queries.append(line)
    return queries


def load_watchlist() -> list[str]:
    """从文件加载用户名列表。"""
    if not WATCHLIST_PATH.exists():
        print(f"错误: 关注列表不存在: {WATCHLIST_PATH}")
        sys.exit(1)
    
    users = []
    for line in WATCHLIST_PATH.read_text().strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # 提取 @用户名，支持格式："Name (@username)" 或 "@username" 或 "username"
        match = re.search(r"@(\w+)", line)
        if match:
            users.append(match.group(1))
        else:
            # 没有 @ 符号，整行作为用户名
            users.append(line.split("(")[0].strip())
    return users


def fetch_user_tweets(username: str, env: dict, retry_on_429: bool = True) -> list[dict]:
    """调用 bird-cli 获取用户推文。遇到 429 rate limit 等 30 秒重试一次。"""
    try:
        result = subprocess.run(
            ["bird", "user-tweets", username, "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=BIRD_TIMEOUT,
        )
        
        if result.returncode != 0:
            stderr = result.stderr
            # 遇到 429 rate limit，等 30 秒重试一次
            if "429" in stderr and retry_on_429:
                print(f"  ⏳ @{username} 触发 rate limit，等待 30 秒重试...", end=" ")
                time.sleep(30)
                return fetch_user_tweets(username, env, retry_on_429=False)
            print(f"  ⚠ @{username} 获取失败: {stderr[:100]}")
            return []
        
        stdout = result.stdout.strip()
        if not stdout:
            return []
        
        # bird-cli 输出完整 JSON 数组
        try:
            data = json.loads(stdout)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return [data]
            return []
        except json.JSONDecodeError:
            # 兼容逐行 JSON 格式
            tweets = []
            for line in stdout.split("\n"):
                line = line.strip()
                if line and line.startswith("{"):
                    try:
                        tweets.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return tweets
    
    except subprocess.TimeoutExpired:
        print(f"  ⚠ @{username} 超时 ({BIRD_TIMEOUT}s)")
        return []
    except Exception as e:
        print(f"  ⚠ @{username} 异常: {e}")
        return []


def search_tweets(query: str, env: dict) -> list[dict]:
    """搜索推文。"""
    try:
        result = subprocess.run(
            ["bird", "search", query, "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=BIRD_TIMEOUT,
        )
        
        if result.returncode != 0:
            stderr = result.stderr
            if "429" in stderr:
                print("  ⏳ 触发 rate limit，等待 30 秒...", end=" ")
                time.sleep(30)
                # 重试一次
                result = subprocess.run(
                    ["bird", "search", query, "--json"],
                    capture_output=True, text=True, env=env, timeout=BIRD_TIMEOUT,
                )
                if result.returncode != 0:
                    print("⚠ 重试失败")
                    return []
            else:
                print(f"  ⚠ 搜索失败: {stderr[:80]}")
                return []
        
        stdout = result.stdout.strip()
        if not stdout:
            return []
        
        try:
            data = json.loads(stdout)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return [data]
            return []
        except json.JSONDecodeError:
            return []
    
    except subprocess.TimeoutExpired:
        print("  ⚠ 搜索超时")
        return []
    except Exception as e:
        print(f"  ⚠ 搜索异常: {e}")
        return []


def is_original_tweet(tweet: dict) -> bool:
    """判断是否为原创推文（排除转推、回复）。"""
    text = tweet.get("text", "")
    # 文字转推
    if re.match(r"^RT @\w+:", text):
        return False
    # 回复：conversationId 不等于自身 id 说明是回复链
    if tweet.get("conversationId") and tweet.get("id"):
        if tweet["conversationId"] != tweet["id"]:
            return False
    return True


def is_recent_tweet(tweet: dict, max_hours: int = MAX_AGE_HOURS) -> bool:
    """判断推文是否在最近 max_hours 小时内。"""
    created_at = tweet.get("createdAt", "")
    if not created_at:
        return False
    try:
        # bird-cli 格式: "Thu Feb 12 18:15:54 +0000 2026"
        tweet_time = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        now = datetime.now(timezone.utc)
        age = now - tweet_time
        return age.total_seconds() < max_hours * 3600
    except (ValueError, TypeError):
        return False


def score_tweet(tweet: dict) -> float:
    """计算推文热度分。"""
    likes = tweet.get("likeCount", 0) or 0
    retweets = tweet.get("retweetCount", 0) or 0
    replies = tweet.get("replyCount", 0) or 0
    return likes + (retweets * 2) + (replies * 0.5)


def _format_tweet_block(tweet: dict, index: int) -> list[str]:
    """格式化单条推文为 Markdown 行列表。"""
    author = tweet.get("author", {})
    username = author.get("username", "unknown")
    name = author.get("name", username)
    text = tweet.get("text", "").replace("\n", " ").strip()
    # 截断到 150 字符
    if len(text) > 150:
        text = text[:147] + "..."
    
    likes = tweet.get("likeCount", 0) or 0
    rts = tweet.get("retweetCount", 0) or 0
    replies = tweet.get("replyCount", 0) or 0
    score = tweet.get("_score", 0)
    tweet_id = tweet.get("id", "")
    url = f"https://twitter.com/{username}/status/{tweet_id}"
    source = "🔎" if tweet.get("_source") == "search" else "👤"
    
    return [
        f"### {index}. {source} @{username} ({name})",
        "",
        f"> {text}",
        "",
        f"❤️ {likes} | 🔁 {rts} | 💬 {replies} | 🔥 热度: {score:.0f}",
        f"🔗 {url}",
        "",
    ]


def format_summary(user_tweets: list[dict], search_tweets: list[dict]) -> str:
    """生成分区 Markdown 摘要：关注用户 + 热点搜索两个独立区块。"""
    now = datetime.now(timezone(timedelta(hours=8)))
    query_count = len(load_search_queries())
    total = len(user_tweets) + len(search_tweets)
    lines = [
        "# 🐦 推特热点日报",
        "",
        f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)",
        f"**监控用户数**: {len(load_watchlist())} | **搜索关键词**: {query_count} 组",
        f"**收录推文**: {total} 条（关注用户 {len(user_tweets)} + 热点搜索 {len(search_tweets)}）",
        "",
    ]
    
    # === 第一区块：关注用户 ===
    lines.append("---")
    lines.append("")
    lines.append(f"## 👤 关注用户 Top {len(user_tweets)}")
    lines.append("")
    
    if user_tweets:
        for i, tweet in enumerate(user_tweets, 1):
            lines.extend(_format_tweet_block(tweet, i))
    else:
        lines.append("今日未抓取到关注用户的有效原创推文。")
        lines.append("")
    
    # === 第二区块：热点搜索 ===
    lines.append("---")
    lines.append("")
    lines.append(f"## 🔎 热点搜索 Top {len(search_tweets)}")
    lines.append("")
    
    if search_tweets:
        for i, tweet in enumerate(search_tweets, 1):
            lines.extend(_format_tweet_block(tweet, i))
    else:
        lines.append("今日热点搜索未返回有效结果。")
        lines.append("")
    
    return "\n".join(lines)


def main():
    print(f"{'='*50}")
    print(f"推特日报抓取 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    
    # 检查环境变量
    # 加载凭证：优先从 .twitter_credentials 文件读取，其次环境变量
    auth_token = None
    ct0 = None
    cred_file = Path("/root/.nanobot/workspace/.twitter_credentials")
    if cred_file.exists():
        try:
            creds = json.loads(cred_file.read_text())
            auth_token = creds.get("auth_token")
            ct0 = creds.get("ct0")
            if auth_token and ct0:
                print("✓ 凭证已从 .twitter_credentials 文件加载")
        except Exception:
            pass
    # 环境变量后备
    if not auth_token:
        auth_token = os.getenv("AUTH_TOKEN")
    if not ct0:
        ct0 = os.getenv("CT0")
    if not auth_token or not ct0:
        print("错误: 请通过面板设置凭证或设置 AUTH_TOKEN / CT0 环境变量")
        sys.exit(1)
    
    env = os.environ.copy()
    env["AUTH_TOKEN"] = auth_token
    env["CT0"] = ct0
    
    # 加载用户列表
    users = load_watchlist()
    print(f"共 {len(users)} 个用户")
    
    # 确保原始数据目录存在
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    
    # 逐个抓取
    all_tweets = []
    success_count = 0
    
    for i, username in enumerate(users, 1):
        print(f"[{i}/{len(users)}] 抓取 @{username}...", end=" ")
        
        tweets = fetch_user_tweets(username, env)
        
        if tweets:
            # 保存原始数据（可选，用于调试）
            raw_file = RAW_DIR / f"{username}.json"
            raw_file.write_text(json.dumps(tweets, ensure_ascii=False, indent=2))
            
            # 过滤原创推文 + 时间范围
            original = [t for t in tweets if is_original_tweet(t) and is_recent_tweet(t)]
            
            # 打分
            for t in original:
                t["_score"] = score_tweet(t)
            
            all_tweets.extend(original)
            success_count += 1
            print(f"✓ {len(tweets)} 条推文, {len(original)} 条原创")
        else:
            print("✗ 无数据")
        
        # 间隔
        if i < len(users):
            time.sleep(DELAY_BETWEEN_USERS)
    
    print(f"\n用户抓取完成: {success_count}/{len(users)} 个用户成功")
    
    # === 第二阶段：热点搜索 ===
    search_queries = load_search_queries()
    print(f"\n{'='*50}")
    print(f"热点搜索 - {len(search_queries)} 组关键词")
    print(f"{'='*50}")
    
    # 收集已有推文 ID，用于去重
    existing_ids = {t.get("id") for t in all_tweets if t.get("id")}
    search_count = 0
    
    for i, query in enumerate(search_queries, 1):
        print(f"[{i}/{len(search_queries)}] 搜索: {query[:40]}...", end=" ")
        
        results = search_tweets(query, env)
        
        if results:
            # 过滤原创 + 时间 + 去重
            fresh = [
                t for t in results
                if is_original_tweet(t) and is_recent_tweet(t) and t.get("id") not in existing_ids
            ]
            for t in fresh:
                t["_score"] = score_tweet(t) * SEARCH_SCORE_BOOST  # 搜索加权
                t["_source"] = "search"
                existing_ids.add(t.get("id"))
            all_tweets.extend(fresh)
            search_count += len(fresh)
            print(f"✓ {len(results)} 结果, {len(fresh)} 条有效")
        else:
            print("✗ 无结果")
        
        if i < len(search_queries):
            time.sleep(DELAY_BETWEEN_USERS)
    
    print(f"\n搜索完成: 新增 {search_count} 条推文")
    
    # 分开排序：用户推文 Top N 和搜索推文 Top N
    user_tweets = [t for t in all_tweets if t.get("_source") != "search"]
    search_results = [t for t in all_tweets if t.get("_source") == "search"]
    
    user_tweets.sort(key=lambda t: t.get("_score", 0), reverse=True)
    search_results.sort(key=lambda t: t.get("_score", 0), reverse=True)
    
    top_user = user_tweets[:USER_TOP_N]
    top_search = search_results[:SEARCH_TOP_N]
    
    # 生成分区摘要
    summary = format_summary(top_user, top_search)
    OUTPUT_PATH.write_text(summary, encoding="utf-8")
    print(f"\n摘要已写入: {OUTPUT_PATH}")
    print(f"关注用户 Top {len(top_user)} + 热点搜索 Top {len(top_search)} 推文已保存")


if __name__ == "__main__":
    main()
