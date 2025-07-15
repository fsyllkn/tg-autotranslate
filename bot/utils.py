"""
utils.py
通用工具/辅助函数模块
"""

import re

def build_ignore_patterns(words):
    """
    根据 ignore_words 列表构建正则模式列表
    """
    patterns = []
    for word in words:
        w = word.strip()
        if not w:
            continue
        if re.search(r'[\u4e00-\u9fff]', w):
            patterns.append(re.compile(rf"^{re.escape(w)}([\d\s/，,。.!！、：:；;（）()\[\]\-—_]|$)", re.IGNORECASE))
        else:
            patterns.append(re.compile(rf"^{re.escape(w)}(\s|$|\d+)", re.IGNORECASE))
    return patterns

def should_ignore(text, ignore_patterns):
    """
    判断文本是否应被忽略
    """
    lines = text.strip().splitlines()
    for line in lines:
        l = line.strip()
        for pat in ignore_patterns:
            if pat.match(l):
                return True
    return False

import asyncio

async def send_ephemeral_reply(event, reply_text, delay=15):
    """
    发送临时回复，延迟 delay 秒后自动删除命令和回复消息
    """
    try:
        rep_msg = await event.reply(reply_text)
        chat_id = event.chat_id
        operator_id = event.sender_id
        import logging
        logging.info(f"[CMD-EPHEMERAL] user {operator_id} sent command: {getattr(event, 'text', '')!r}, reply: {reply_text!r}")
        await asyncio.sleep(delay)
        msg_ids = []
        try:
            if hasattr(event, "id") and isinstance(event.id, int):
                msg_ids.append(event.id)
            if hasattr(rep_msg, "id") and isinstance(rep_msg.id, int):
                msg_ids.append(rep_msg.id)
        except Exception as e:
            logging.warning(f"[CMD-EPHEMERAL] 获取消息id异常: {e}")
        logging.info(f"[CMD-EPHEMERAL] 尝试删除消息: {msg_ids} in chat_id={chat_id}")
        if msg_ids:
            await event.client.delete_messages(chat_id, msg_ids)
    except Exception as e:
        import logging
        logging.warning(f"Failed to delete command/reply message: {e}")

# 其他通用工具函数可在此扩展
