from nanobot.agent.hallucination_detector import detect_hallucination


def test_plan_text_is_not_blocked_as_hallucination() -> None:
    text = (
        "进度已确认：时间校准正常。\n\n"
        "下一步我会直接执行这三步：\n"
        "1) 定位文件\n"
        "2) 读取条目\n"
        "3) 输出总结\n"
        "现在就继续执行。"
    )
    result = detect_hallucination(text, tools_were_called=False, model_supports_tools=True)
    assert result.is_hallucination is False


def test_english_plan_text_is_not_blocked_as_hallucination() -> None:
    text = (
        "Progress confirmed. If you agree, I will proceed with these steps:\n"
        "1) locate files\n"
        "2) read entries\n"
        "3) summarize findings\n"
        "Now continuing."
    )
    result = detect_hallucination(text, tools_were_called=False, model_supports_tools=True)
    assert result.is_hallucination is False


def test_fake_execution_with_shell_block_is_detected() -> None:
    text = "我已经执行命令，结果如下：```bash\nls -la\n```"
    result = detect_hallucination(text, tools_were_called=False, model_supports_tools=True)
    assert result.is_hallucination is True
    assert result.pattern_name in {"fake_shell_output", "fake_command_result", "claimed_execution"}


def test_english_claimed_execution_is_detected() -> None:
    text = "I already executed the command and called the tool successfully."
    result = detect_hallucination(text, tools_were_called=False, model_supports_tools=True)
    assert result.is_hallucination is True


def test_windows_path_is_not_false_positive_for_fake_table_status() -> None:
    text = r"C:\tmp\a.txt"
    result = detect_hallucination(text, tools_were_called=False, model_supports_tools=True)
    assert result.is_hallucination is False


def test_fake_table_status_with_header_and_status_cell_is_detected() -> None:
    text = (
        "| 项目 | 状态 |\n"
        "| 服务A | running |\n"
    )
    result = detect_hallucination(text, tools_were_called=False, model_supports_tools=True)
    assert result.is_hallucination is True
    assert result.pattern_name == "fake_table_status"
