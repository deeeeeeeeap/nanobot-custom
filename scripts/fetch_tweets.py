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
DELAY_BETWEEN_USERS = 3  # 秒，bird-cli 自带 rate limit
BIRD_TIMEOUT = 60  # 单个 bird 命令超时
TOP_N = 15  # 最终保留的热门推文数量


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


def fetch_user_tweets(username: str, env: dict) -> list[dict]:
    """调用 bird-cli 获取用户推文。"""
    try:
        result = subprocess.run(
            ["bird", "user-tweets", username, "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=BIRD_TIMEOUT,
        )
        
        if result.returncode != 0:
            print(f"  ⚠ @{username} 获取失败: {result.stderr[:100]}")
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


def is_original_tweet(tweet: dict) -> bool:
    """判断是否为原创推文（排除转推、回复、引用）。"""
    # 转推
    if tweet.get("retweeted_status"):
        return False
    # 文字转推
    text = tweet.get("full_text") or tweet.get("text", "")
    if re.match(r"^RT @\w+:", text):
        return False
    # 回复
    if tweet.get("in_reply_to_status_id_str"):
        return False
    return True


def score_tweet(tweet: dict) -> float:
    """计算推文热度分。"""
    metrics = tweet.get("public_metrics", {})
    likes = metrics.get("like_count", 0)
    retweets = metrics.get("retweet_count", 0)
    replies = metrics.get("reply_count", 0)
    return likes + (retweets * 2) + (replies * 0.5)


def format_summary(ranked_tweets: list[dict]) -> str:
    """生成 Markdown 格式的摘要。"""
    now = datetime.now(timezone(timedelta(hours=8)))
    lines = [
        f"# 🐦 推特 AI 热点日报",
        f"",
        f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)",
        f"**监控用户数**: {len(load_watchlist())}",
        f"**热门推文 Top {len(ranked_tweets)}**:",
        f"",
        f"---",
        f"",
    ]
    
    for i, tweet in enumerate(ranked_tweets, 1):
        user = tweet.get("user", {})
        username = user.get("screen_name", "unknown")
        name = user.get("name", username)
        text = (tweet.get("full_text") or tweet.get("text", "")).replace("\n", " ").strip()
        # 截断到 150 字符
        if len(text) > 150:
            text = text[:147] + "..."
        
        metrics = tweet.get("public_metrics", {})
        likes = metrics.get("like_count", 0)
        rts = metrics.get("retweet_count", 0)
        replies = metrics.get("reply_count", 0)
        score = tweet.get("_score", 0)
        tweet_id = tweet.get("id_str", "")
        url = f"https://twitter.com/{username}/status/{tweet_id}"
        
        lines.append(f"### {i}. @{username} ({name})")
        lines.append(f"")
        lines.append(f"> {text}")
        lines.append(f"")
        lines.append(f"❤️ {likes} | 🔁 {rts} | 💬 {replies} | 🔥 热度: {score:.0f}")
        lines.append(f"🔗 {url}")
        lines.append(f"")
    
    if not ranked_tweets:
        lines.append("今日未抓取到有效的原创推文。")
    
    return "\n".join(lines)


def main():
    print(f"{'='*50}")
    print(f"推特日报抓取 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    
    # 检查环境变量
    auth_token = os.getenv("AUTH_TOKEN")
    ct0 = os.getenv("CT0")
    if not auth_token or not ct0:
        print("错误: 请设置 AUTH_TOKEN 和 CT0 环境变量")
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
            
            # 过滤原创推文
            original = [t for t in tweets if is_original_tweet(t)]
            
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
    
    print(f"\n抓取完成: {success_count}/{len(users)} 个用户成功")
    
    # 排序取 Top N
    all_tweets.sort(key=lambda t: t.get("_score", 0), reverse=True)
    top_tweets = all_tweets[:TOP_N]
    
    # 生成摘要
    summary = format_summary(top_tweets)
    OUTPUT_PATH.write_text(summary, encoding="utf-8")
    print(f"摘要已写入: {OUTPUT_PATH}")
    print(f"Top {len(top_tweets)} 推文已保存")


if __name__ == "__main__":
    main()
