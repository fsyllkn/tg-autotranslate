"""
translation.py
翻译服务与引擎抽象模块
"""

import asyncio
from abc import ABC, abstractmethod

class BaseTranslator(ABC):
    """
    翻译引擎抽象基类，所有翻译器需实现 translate 和 health_check 方法
    """
    def __init__(self, config):
        self.config = config

    @abstractmethod
    async def translate(self, text, source_lang, target_lang):
        pass

    @abstractmethod
    async def health_check(self):
        pass

import aiohttp
import logging

logger = logging.getLogger(__name__)

# 全局异步ClientSession单例
_aiohttp_session = None
async def get_aiohttp_session():
    global _aiohttp_session
    if _aiohttp_session is None or _aiohttp_session.closed:
        _aiohttp_session = aiohttp.ClientSession()
    return _aiohttp_session

class DeeplxTranslator(BaseTranslator):
    """
    Deeplx 翻译引擎实现
    """
    def __init__(self, config):
        super().__init__(config)
        self.base_urls = config.get("base_urls", [])
        self.fail_count = [0] * len(self.base_urls)
        self.disabled = set()
        self.fail_threshold = int(config.get("deeplx_fail_threshold", 3)) if config else 3
        self.current_idx = 0

    async def translate(self, text, source_lang, target_lang):
        n = len(self.base_urls)
        if n == 0:
            raise Exception("deeplx base_urls 未配置")
        tried = 0
        while tried < n:
            idx = self.current_idx % n
            self.current_idx = (self.current_idx + 1) % n
            if idx in self.disabled:
                tried += 1
                continue
            base_url = self.base_urls[idx]
            try:
                payload = {
                    "text": text,
                    "source_lang": source_lang,
                    "target_lang": target_lang
                }
                session = await get_aiohttp_session()
                # 自动重试机制
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        async with session.post(base_url, json=payload, timeout=10) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data.get('code') == 200 and data.get('data'):
                                    self.fail_count[idx] = 0
                                    return data['data']
                                else:
                                    self.fail_count[idx] += 1
                                    logger.warning(f"Deeplx接口 {base_url} 返回异常: {data}")
                            else:
                                self.fail_count[idx] += 1
                                logger.warning(f"Deeplx接口 {base_url} 失败，状态码: {resp.status}")
                        break  # 非网络异常不重试
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        logger.warning(f"Deeplx接口 {base_url} 网络异常尝试第{attempt+1}次: {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                        else:
                            raise
            except Exception as e:
                self.fail_count[idx] += 1
                logger.error(f"Deeplx接口 {base_url} 网络请求异常: {e}", exc_info=True)
            if self.fail_count[idx] >= self.fail_threshold:
                self.disabled.add(idx)
                logger.error(f"已禁用第{idx+1}个deeplx接口: {base_url}，连续失败{self.fail_threshold}次，请及时检查或更新！")
            tried += 1
        raise Exception("所有Deeplx接口均已禁用或不可用")

    async def health_check(self):
        # TODO: 实现 Deeplx 健康检查
        raise NotImplementedError

import time

class OpenAITranslator(BaseTranslator):
    """
    OpenAI 翻译引擎实现
    支持多端点、禁用、健康检查
    """
    def __init__(self, config):
        super().__init__(config)
        self.model_groups = config.get('model_groups', [])
        # 兼容旧版配置
        if not self.model_groups and 'base_urls' in config and 'api_keys' in config:
            self.model_groups = [{
                "name": "DefaultGroup",
                "models": config.get('models', ['gpt-4o']),
                "endpoints": [
                    {"url": url, "api_key": key}
                    for url, key in zip(config['base_urls'], config['api_keys'])
                ]
            }]
        self.flat_endpoints = []
        for group in self.model_groups:
            models = group.get('models', ['gpt-4o'])
            endpoints = group.get('endpoints', [])
            if not isinstance(endpoints, list) or not endpoints:
                continue
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    continue
                url = endpoint.get('url')
                key = endpoint.get('api_key')
                if not url or not key:
                    continue
                self.flat_endpoints.append({
                    'url': url,
                    'api_key': key,
                    'models': models,
                    'group_name': group.get('name', 'UnnamedGroup')
                })
        self.fail_count = [0] * len(self.flat_endpoints)
        self.disabled = set()
        self.fail_threshold = int(config.get('openai_fail_threshold', 3)) if config else 3
        self.current_idx = 0

    async def translate(self, text, source_lang, target_lang):
        import random
        if not self.model_groups:
            raise Exception("openai.model_groups 未配置或配置无效")
        group = random.choice(self.model_groups)
        endpoints = group.get('endpoints', [])
        if not endpoints:
            raise Exception("选中的openai.model_group无可用端点")
        models = group.get('models', ['gpt-4o'])
        n = len(endpoints)
        if n == 0 or not models:
            raise Exception("选中的openai.model_group无可用端点或模型")
        group_name = group.get('name', 'UnnamedGroup')
        for idx in range(n):
            endpoint = endpoints[(self.current_idx + idx) % n]
            url = endpoint.get('url')
            key = endpoint.get('api_key')
            if not url or not key:
                continue
            for model_to_use in models:
                # 日志输出：OpenAI-组名-端点序号-模型名称（不显示url和apikey）
                logger.info(f"OpenAI-{group_name}-{idx+1}-{model_to_use}")
                try:
                    api_url = url.rstrip("/")
                    if api_url.endswith("/v1"):
                        api_url = api_url[:-3]
                    api_url = api_url + "/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": model_to_use,
                        "messages": [
                            {"role": "system", "content": "You are a translation engine, only returning translated answers."},
                            {"role": "user", "content": f"Translate the text to {target_lang} please do not explain my original text, do not explain the translation results, Do not explain the context.:\n{text}"}
                        ]
                    }
                    session = await get_aiohttp_session()
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            async with session.post(api_url, headers=headers, json=payload, timeout=30) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    content = data.get("choices", [{}])[0].get("message", {}).get("content")
                                    # 针对Gemini模型，去除多余markdown包装
                                    if content and "gemini" in model_to_use.lower():
                                        import re
                                        # 去除```markdown ... ```包裹
                                        content = re.sub(r"^```markdown\s*([\s\S]*?)\s*```$", r"\1", content.strip(), flags=re.IGNORECASE)
                                    if content:
                                        self.current_idx = (self.current_idx + idx + 1) % n
                                        return content
                                    else:
                                        logger.warning(f"OpenAI接口 {api_url} 返回了非预期的JSON格式或空内容: {data}")
                                else:
                                    error_text = await resp.text()
                                    logger.warning(f"OpenAI接口 {api_url} 状态码: {resp.status}, 响应: {error_text}")
                            break  # 非网络异常不重试
                        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                            logger.warning(f"OpenAI接口 {api_url} 网络异常尝试第{attempt+1}次: {e}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(0.5 * (2 ** attempt))
                            else:
                                raise
                except Exception as e:
                    logger.error(f"OpenAI接口 {url} (模型: {model_to_use}) 调用失败: {e}")
        raise Exception("所有OpenAI接口均已禁用或不可用")

    async def health_check(self):
        # 轮询所有禁用端点，尝试恢复
        for idx in list(self.disabled):
            if idx >= len(self.flat_endpoints):
                continue
            endpoint_info = self.flat_endpoints[idx]
            url = endpoint_info.get('url')
            key = endpoint_info.get('api_key')
            model_to_check = endpoint_info['models'][0]
            try:
                api_url = url.rstrip("/")
                if api_url.endswith("/v1"):
                    api_url = api_url[:-3]
                api_url = api_url + "/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": model_to_check,
                    "messages": [
                        {"role": "system", "content": "You are a translation engine, only returning translated answers."},
                        {"role": "user", "content": f"Translate the text to en: hello"}
                    ]
                }
                session = await get_aiohttp_session()
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        async with session.post(api_url, headers=headers, json=payload, timeout=15) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                                if content:
                                    self.disabled.remove(idx)
                                    self.fail_count[idx] = 0
                                    logger.info(f"[HEALTH] OpenAI端点已恢复: 第{idx+1}个 {url} (组: {endpoint_info['group_name']})")
                        break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        logger.warning(f"OpenAI健康检查 {api_url} 网络异常尝试第{attempt+1}次: {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                        else:
                            pass
            except Exception:
                pass

class TranslationService:
    """
    统一调度各翻译引擎，主备切换、健康检查、并发控制
    """
    def __init__(self, config_manager):
        self.config_manager = config_manager
        logger.info("[TranslationService] 初始化各翻译引擎")
        self.engines = {
            "deeplx": DeeplxTranslator(config_manager.get("deeplx", {})),
            "openai": OpenAITranslator(config_manager.get("openai", {})),
        }
        self.default_engine = config_manager.get("default_translate_source", "deeplx")
        # 简单内存LRU缓存
        self._cache = {}
        self._cache_order = []
        self._cache_maxsize = 1000

    def _cache_get(self, key):
        if key in self._cache:
            # LRU: 移到队尾
            self._cache_order.remove(key)
            self._cache_order.append(key)
            return self._cache[key]
        return None

    def _cache_set(self, key, value):
        if key in self._cache:
            self._cache_order.remove(key)
        elif len(self._cache_order) >= self._cache_maxsize:
            oldest = self._cache_order.pop(0)
            self._cache.pop(oldest, None)
        self._cache[key] = value
        self._cache_order.append(key)

    async def translate(self, text, source_lang, target_langs, prefer=None):
        """
        并发翻译，主备切换，带缓存
        """
        logger.info(f"[TranslationService] 翻译请求: text={text[:20]}..., source_lang={source_lang}, target_langs={target_langs}, prefer={prefer}")
        if prefer is None:
            prefer = self.default_engine
        backup_engine = "deeplx" if prefer == "openai" else "openai"
        final_results = {}
        semaphore = asyncio.Semaphore(5)

        async def translate_one(lang, engine):
            cache_key = (text, source_lang, lang, engine)
            cached = self._cache_get(cache_key)
            if cached is not None:
                logger.info(f"[TranslationService] 缓存命中: {cache_key}")
                return lang, cached
            await semaphore.acquire()
            try:
                logger.info(f"[TranslationService] 调用引擎: {engine}, 目标语言: {lang}")
                result = await self.engines[engine].translate(text, source_lang, lang)
                # 若翻译结果与原文一致，视为失败
                if result is not None and result.strip() == text.strip():
                    logger.warning(f"[TranslationService] 翻译结果与原文一致，视为未翻译，lang={lang}")
                    return lang, None
                self._cache_set(cache_key, result)
                return lang, result
            except Exception as e:
                logger.error(f"[TranslationService] 翻译失败: engine={engine}, lang={lang}, error={e}")
                return lang, None
            finally:
                semaphore.release()

        # 1. 使用主引擎进行初次翻译
        primary_tasks = [translate_one(lang, prefer) for lang in target_langs]
        primary_results = await asyncio.gather(*primary_tasks)

        failed_langs = []
        for lang, translated_text in primary_results:
            if translated_text is not None:
                final_results[lang] = translated_text
            else:
                failed_langs.append(lang)

        # 2. 如果有失败的，使用备用引擎重试
        if failed_langs:
            logger.warning(f"[TranslationService] 主引擎翻译失败，切换备用引擎: {backup_engine}，失败语言: {failed_langs}")
            backup_tasks = [translate_one(lang, backup_engine) for lang in failed_langs]
            backup_results = await asyncio.gather(*backup_tasks)
            for lang, translated_text in backup_results:
                if translated_text is not None:
                    final_results[lang] = translated_text
                else:
                    final_results[lang] = f"[翻译失败]主备引擎({prefer}, {backup_engine})均异常"

        logger.info(f"[TranslationService] 翻译结果: {final_results}")
        return {k: v for k, v in final_results.items() if v is not None and v != ""}

    async def health_check_loop(self, interval=600):
        """
        后台健康检查任务
        """
        # TODO: 定期检查各引擎健康状态
        raise NotImplementedError
