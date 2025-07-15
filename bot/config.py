"""
config.py
配置加载与热重载模块
"""

import yaml
import threading
import logging

logger = logging.getLogger(__name__)

class ConfigManager:
    """
    负责加载和热重载 config.yaml，提供配置访问接口
    """
    def __init__(self, path='config.yaml'):
        self.path = path
        self._lock = threading.Lock()
        logger.info(f"[ConfigManager] 初始化，加载配置文件: {self.path}")
        self._config = self._load_config()

    def _load_config(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                logger.info(f"[ConfigManager] 配置文件加载成功: {self.path}")
                return config
        except Exception as e:
            logger.error(f"[ConfigManager] 配置文件加载失败: {e}")
            return {}

    def reload(self):
        """
        重新加载配置文件
        """
        with self._lock:
            logger.info("[ConfigManager] 重新加载配置文件")
            self._config = self._load_config()

    def get(self, key, default=None):
        """
        获取配置项
        """
        with self._lock:
            value = self._config.get(key, default)
            logger.debug(f"[ConfigManager] 获取配置项: {key} -> {value}")
            return value

    @property
    def config(self):
        with self._lock:
            return self._config.copy()
