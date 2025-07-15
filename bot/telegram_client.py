"""
telegram_client.py
Telegram 客户端封装模块
"""

from telethon import TelegramClient, events
import logging

logger = logging.getLogger(__name__)

import time
import asyncio

class TelegramBot:
    """
    封装 Telethon 客户端，注册消息/命令处理器，集成所有业务模块
    """
    def __init__(self, config_manager, rule_manager, translation_service, lang_detector, command_dispatcher):
        self.config_manager = config_manager
        self.rule_manager = rule_manager
        self.translation_service = translation_service
        self.lang_detector = lang_detector
        self.command_dispatcher = command_dispatcher

        tg_cfg = self.config_manager.get("telegram", {})
        self.api_id = tg_cfg.get("api_id")
        self.api_hash = tg_cfg.get("api_hash")
        self.session_name = tg_cfg.get("session_name")
        logger.info(f"[TelegramBot] 初始化，session={self.session_name}, api_id={self.api_id}")
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)

        # 消息速率限制
        self._group_msg_times = {}  # group_id: [timestamps]
        self._global_msg_times = []  # [timestamps]
        self._rate_lock = asyncio.Lock()
        self._group_limit = 19  # 每群每分钟
        self._group_window = 60  # 秒
        self._global_limit = 29  # 全局每秒
        self._global_window = 1  # 秒

    async def send_reply(self, event, text):
        """
        速率限制下安全发送消息
        """
        group_id = str(getattr(event, "chat_id", ""))
        now = time.time()
        while True:
            async with self._rate_lock:
                # group限制
                group_times = self._group_msg_times.setdefault(group_id, [])
                group_times = [t for t in group_times if now - t < self._group_window]
                if len(group_times) < self._group_limit:
                    group_times.append(now)
                    self._group_msg_times[group_id] = group_times
                    # global限制
                    self._global_msg_times = [t for t in self._global_msg_times if now - t < self._global_window]
                    if len(self._global_msg_times) < self._global_limit:
                        self._global_msg_times.append(now)
                        break  # 可以发
            await asyncio.sleep(0.1)
            now = time.time()
        try:
            await event.reply(text)
            logger.info("[TelegramBot] 回复消息成功（速率限制已检查）")
        except Exception as e:
            logger.error(f"[TelegramBot] 回复消息失败: {e}")

    def register_handlers(self):
        """
        注册消息和命令处理器
        """
        logger.info("[TelegramBot] 注册消息和命令处理器")
        @self.client.on(events.NewMessage)
        async def on_new_message(event):
            text = getattr(event.message, "text", "")
            # 兼容全角句号，统一转换为半角
            text_check = text.replace("。", ".") if text else text
            # 判断是否为命令
            if text_check and text_check.strip().startswith(".fy-"):
                logger.info("[TelegramBot] 识别为命令，分发处理")
                await self.command_dispatcher.dispatch(event)
            else:
                # 只有有规则时才输出日志
                await self.handle_message(event)

    async def handle_message(self, event):
        """
        普通消息的自动翻译主流程
        """
        text = getattr(event.message, "text", "")
        if not text or text.strip().startswith(".fy-"):
            return
        from .utils import should_ignore, build_ignore_patterns
        ignore_words = self.config_manager.get("ignore_words", [])
        ignore_patterns = build_ignore_patterns(ignore_words)
        if should_ignore(text, ignore_patterns):
            return
        group_id = str(event.chat_id)
        user_id = str(event.sender_id)
        rule_raw = self.rule_manager.get_rule(group_id, user_id)
        if not rule_raw:
            return
        # 只有有规则时才输出日志
        logger.info(f"[TelegramBot] 收到新消息: {text[:20]}...")
        logger.info(f"[TelegramBot] 自动翻译流程启动，消息内容: {text[:20]}...")
        rule_list = rule_raw if isinstance(rule_raw, list) else [rule_raw]
        prefer = self.config_manager.get("default_translate_source", "deeplx")
        detected_lang = self.lang_detector.detect(text)
        detected_langs = detected_lang if isinstance(detected_lang, list) else [detected_lang]
        all_targets = set()
        for dlang in detected_langs:
            for rule in rule_list:
                source_langs = rule.get('source_langs', ['en'])
                target_langs = rule.get('target_langs', ['zh'])
                if dlang in source_langs or (dlang == "zh" and "zh" in source_langs):
                    for lang in target_langs:
                        if lang != dlang:
                            all_targets.add((dlang, lang))
        if not all_targets:
            logger.info("[TelegramBot] 未匹配到目标语言，跳过")
            return
        src2tgts = {}
        for src, tgt in all_targets:
            src2tgts.setdefault(src, set()).add(tgt)
        reply_text = ""
        lang_map = {
            "en": "英语", "zh": "中文", "fr": "法语", "de": "德语", "ru": "俄语", "ja": "日语", "ko": "韩语", "ar": "阿拉伯语",
            "hi": "印地语", "tr": "土耳其语", "fa": "波斯语", "uk": "乌克兰语", "es": "西班牙语", "it": "意大利语", "rm": "罗曼什语",
            "pt": "葡萄牙语", "pl": "波兰语", "nl": "荷兰语", "sv": "瑞典语", "ro": "罗马尼亚语", "cs": "捷克语", "el": "希腊语",
            "da": "丹麦语", "fi": "芬兰语", "hu": "匈牙利语", "he": "希伯来语", "bg": "保加利亚语", "sr": "塞尔维亚语",
            "hr": "克罗地亚语", "sk": "斯洛伐克语", "sl": "斯洛文尼亚语", "no": "挪威语"
        }
        import asyncio
        for src, tgts in src2tgts.items():
            translated = await self.translation_service.translate(text, src, list(tgts), prefer=prefer)
            for lang in tgts:
                reply = translated.get(lang, "")
                if not reply or reply.strip() == text.strip():
                    continue
                lang_name = lang_map.get(lang, lang)
                # 判断是否多行
                if "\n" in reply:
                    reply_text += f"{lang_name}：\n```\n{reply}\n```\n"
                else:
                    reply_text += f"{lang_name}：`{reply}`\n"
        if reply_text:
            # 仅当唯一一条回复且内容本身为多行时，整体包裹代码块
            lines = [line for line in reply_text.strip().split("\n") if line]
            if (
                len(lines) == 1
                and "```" not in reply_text
                and "\n" in reply_text.strip().split("：", 1)[-1]
            ):
                # 单条多行，整体用代码块
                reply_text = f"```\n{reply_text.strip()}\n```"
            try:
                await self.send_reply(event, reply_text.strip())
                logger.info("[TelegramBot] 回复翻译结果成功")
            except Exception as e:
                logger.error(f"[TelegramBot] 回复翻译结果失败: {e}")

    def run(self):
        """
        启动客户端，注册异步任务（如热重载、健康检查），并运行主循环
        """
        import asyncio
        loop = asyncio.get_event_loop()
        logger.info("[TelegramBot] 启动 Telegram 客户端")
        # 启动配置热重载和健康检查任务（如有实现）
        # loop.create_task(self.config_manager.hot_reload_loop())
        # loop.create_task(self.translation_service.health_check_loop())
        self.client.start()
        logger.info("Telegram 客户端已启动，等待消息...")
        self.client.run_until_disconnected()
