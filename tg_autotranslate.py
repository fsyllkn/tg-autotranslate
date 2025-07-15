"""
main.py
tg_autotranslate 项目启动入口
"""

from bot.config import ConfigManager
from bot.rules import RuleManager
from bot.translation import TranslationService
from bot.lang_detect import LanguageDetector
from bot.commands import CommandDispatcher
from bot.telegram_client import TelegramBot

import logging

def main():
    # 全局日志配置
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # 初始化各业务模块
    config_manager = ConfigManager("config.yaml")
    rule_manager = RuleManager("dynamic_rules.json")
    translation_service = TranslationService(config_manager)
    lang_detector = LanguageDetector(config_manager)
    command_dispatcher = CommandDispatcher(None)  # 先传 None，稍后注入 bot 实例

    # 实例化 TelegramBot
    bot = TelegramBot(
        config_manager,
        rule_manager,
        translation_service,
        lang_detector,
        command_dispatcher
    )
    command_dispatcher.bot = bot  # 注入 bot 实例

    bot.register_handlers()
    try:
        bot.run()
    except KeyboardInterrupt:
        print("收到退出信号，正在关闭...")
    except Exception as e:
        import traceback
        logging.error(f"主程序异常: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
