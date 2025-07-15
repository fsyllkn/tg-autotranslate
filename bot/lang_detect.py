"""
lang_detect.py
语言检测与文本分析模块
"""

import re
import logging

logger = logging.getLogger(__name__)

class LanguageDetector:
    """
    多策略分层语言检测，支持结构规则、fasttext、关键词、字符区间等
    """
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.fasttext_model = None
        logger.info("[LanguageDetector] 初始化，加载 fasttext 模型")
        self._init_fasttext()
        self.lang_keywords = {
            "fr": ["pas", "est", "le", "la", "un", "une", "je", "tu", "vous", "nous", "avec", "pour", "mais", "sur", "dans", "des", "du", "au", "aux", "ce", "cette", "ces", "mon", "ton", "son", "leur", "qui", "que", "quoi", "où", "comment", "parce", "bien", "mal", "très", "plus", "moins", "aussi", "comme", "si", "non", "oui"],
            "en": ["the", "is", "are", "you", "he", "she", "it", "and", "but", "not", "with", "for", "on", "in", "at", "by", "to", "of", "from", "as", "that", "this", "these", "those", "my", "your", "his", "her", "their", "who", "what", "where", "how", "because", "very", "well", "bad", "good", "no", "yes"],
            "de": ["nicht", "und", "ist", "ich", "du", "sie", "wir", "ihr", "mein", "dein", "sein", "ihr", "unser", "euer", "kein", "ja", "nein", "bitte", "danke", "gut", "schlecht", "sehr", "auch", "aber", "oder", "wenn", "weil", "was", "wer", "wie", "wo", "warum", "dass", "dies", "das", "ein", "eine", "mit", "für", "auf", "im", "am", "aus", "bei", "nach", "vor", "über", "unter", "zwischen"],
            "es": ["no", "sí", "pero", "muy", "también", "como", "más", "menos", "por", "para", "con", "sin", "sobre", "entre", "cuando", "porque", "qué", "quién", "dónde", "cómo", "cuándo", "yo", "tú", "él", "ella", "nosotros", "vosotros", "ellos", "ellas", "mi", "tu", "su", "nuestro", "vuestro", "este", "ese", "aquel", "uno", "una", "unos", "unas"],
            "it": ["non", "sì", "ma", "molto", "anche", "come", "più", "meno", "per", "con", "senza", "su", "tra", "quando", "perché", "che", "chi", "dove", "come", "quando", "io", "tu", "lui", "lei", "noi", "voi", "loro", "mio", "tuo", "suo", "nostro", "vostro", "questo", "quello", "uno", "una", "alcuni", "alcune"],
            "pt": ["não", "sim", "mas", "muito", "também", "como", "mais", "menos", "por", "para", "com", "sem", "sobre", "entre", "quando", "porque", "que", "quem", "onde", "como", "quando", "eu", "tu", "ele", "ela", "nós", "vós", "eles", "elas", "meu", "teu", "seu", "nosso", "vosso", "este", "esse", "aquele", "um", "uma", "uns", "umas"],
            "nl": ["niet", "en", "is", "ik", "jij", "hij", "zij", "wij", "jullie", "mijn", "jouw", "zijn", "haar", "ons", "onze", "geen", "ja", "nee", "alstublieft", "dank", "goed", "slecht", "zeer", "也", "maar", "of", "als", "omdat", "wat", "wie", "hoe", "waar", "waarom", "dat", "deze", "dit", "een", "met", "voor", "op", "in", "uit", "bij", "naar", "voor", "over", "onder", "tussen"]
        }

    def _init_fasttext(self):
        try:
            import fasttext
            model_path = self.config_manager.get("fasttext", {}).get("model_path", "lid.176.bin")
            import os
            if not os.path.exists(model_path):
                logger.warning(f"[LanguageDetector] fasttext 模型文件不存在: {model_path}，尝试自动下载...")
                try:
                    import requests
                    url = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
                    with requests.get(url, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        with open(model_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                    logger.info(f"[LanguageDetector] fasttext 模型已自动下载到: {model_path}")
                except Exception as e:
                    logger.error(f"[LanguageDetector] fasttext 模型自动下载失败: {e}")
            if os.path.exists(model_path):
                self.fasttext_model = fasttext.load_model(model_path)
                logger.info(f"[LanguageDetector] fasttext 模型加载成功: {model_path}")
            else:
                logger.warning(f"[LanguageDetector] fasttext 模型文件仍不存在: {model_path}")
        except Exception as e:
            self.fasttext_model = None
            logger.error(f"[LanguageDetector] fasttext 模型加载失败: {e}")

    def detect(self, text):
        """
        检测文本主语言，返回语言代码或列表
        """
        logger.info(f"[LanguageDetector] 检测文本语言: {text[:20]}...")
        text_stripped = text.strip()
        if not text_stripped:
            logger.warning("[LanguageDetector] 空文本，返回 unknown")
            return 'unknown'
        # 新增：首行/首句为中文优先判定
        lines = text_stripped.splitlines()
        if lines:
            first_line = lines[0].strip()
            if re.search(r'[\u4e00-\u9fff]', first_line):
                logger.info("[LanguageDetector] 首行含中文，整体判定为中文")
                return 'zh'
        chinese_chars_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        english_words = re.findall(r'[a-zA-Z]+', text)
        english_words_count = len(english_words)
        starts_with_chinese = bool(re.match(r'[\u4e00-\u9fff]', text_stripped))
        starts_with_english = bool(re.match(r'[a-zA-Z]', text_stripped))
        full_width_punct_pattern = r'[，。！？；：]'
        has_full_width_punct = bool(re.search(full_width_punct_pattern, text))
        # 结构优先判定
        if starts_with_chinese and has_full_width_punct:
            logger.info("[LanguageDetector] 结构判定为中文")
            return 'zh'
        if starts_with_english and english_words_count >= chinese_chars_count:
            logger.info("[LanguageDetector] 结构判定为英文")
            return 'en'
        # 分句主导语言投票
        def phrase_main_lang(phrase):
            zh_count = len(re.findall(r'[\u4e00-\u9fff]', phrase))
            en_words_list = re.findall(r'[a-zA-Z]+', phrase)
            en_word_count = len(en_words_list)
            is_full_en_sentence = (
                en_word_count >= 4 and
                re.match(r'^[A-Z]', phrase.strip()) and
                re.search(r'[.!?]$', phrase.strip())
            )
            if is_full_en_sentence:
                return 'en'
            if zh_count > en_word_count:
                return 'zh'
            elif en_word_count > zh_count:
                return 'en'
            else:
                return None
        split_phrases = re.split(r'[，,。！？!?.；;、\s]+', text)
        phrase_langs = [phrase_main_lang(p) for p in split_phrases if p.strip()]
        if len(phrase_langs) >= 2:
            zh_votes = phrase_langs.count('zh')
            en_votes = phrase_langs.count('en')
            if zh_votes > en_votes:
                logger.info("[LanguageDetector] 分句投票判定为中文")
                return 'zh'
            if en_votes > zh_votes:
                logger.info("[LanguageDetector] 分句投票判定为英文")
                return 'en'
        # 短文本含中文优先判中文
        if len(text_stripped) <= 6 and chinese_chars_count > 0:
            logger.info("[LanguageDetector] 短文本含中文，判定为中文")
            return 'zh'
        # 字数比例
        if chinese_chars_count > english_words_count:
            logger.info("[LanguageDetector] 字数比例判定为中文")
            return 'zh'
        if english_words_count > chinese_chars_count:
            logger.info("[LanguageDetector] 字数比例判定为英文")
            return 'en'
        # 高频词法
        lang_hits = {}
        text_lower = text.lower()
        for lang, keywords in self.lang_keywords.items():
            count = 0
            for kw in keywords:
                if re.search(rf"\b{re.escape(kw)}\b", text_lower):
                    count += 1
            lang_hits[lang] = count
        if lang_hits:
            max_count = max(lang_hits.values())
            if max_count > 0:
                candidates = [lang for lang, cnt in lang_hits.items() if cnt == max_count]
                if len(candidates) == 1:
                    logger.info(f"[LanguageDetector] 高频词法判定为: {candidates[0]}")
                    return candidates[0]
        # fastText
        ft_cfg = self.config_manager.get('fasttext', {})
        enabled = ft_cfg.get('enabled', True)
        threshold = float(ft_cfg.get('confidence_threshold', 0.8))
        if enabled and self.fasttext_model:
            try:
                pred = self.fasttext_model.predict(text.replace("\n", " ")[:512])
                lang = pred[0][0].replace("__label__", "")
                prob = float(pred[1][0]) if pred and len(pred) > 1 and len(pred[1]) > 0 else 0.0
                logger.info(f"[LanguageDetector] fasttext 预测: lang={lang}, prob={prob}")
                if prob >= threshold:
                    return lang
                if lang == 'en' and prob < 0.9 and chinese_chars_count > 0:
                    logger.info("[LanguageDetector] fasttext 低置信度英文+含中文，判定为中文")
                    return 'zh'
            except Exception as e:
                logger.error(f"[LanguageDetector] fasttext 检测异常: {e}")
                pass
        # fallback
        if chinese_chars_count > 0 and english_words_count > 0:
            logger.info("[LanguageDetector] 中英混合，返回 ['zh', 'en']")
            return ['zh', 'en']
        if chinese_chars_count > 0:
            logger.info("[LanguageDetector] 仅含中文，返回 zh")
            return 'zh'
        if english_words_count > 0:
            logger.info("[LanguageDetector] 仅含英文，返回 en")
            return 'en'
        if re.search(r'[\u0400-\u04FF]', text):
            logger.info("[LanguageDetector] 检测为俄语")
            return 'ru'
        elif re.search(r'[\u3040-\u30FF]', text):
            logger.info("[LanguageDetector] 检测为日语")
            return 'ja'
        elif re.search(r'[\uAC00-\uD7AF]', text):
            logger.info("[LanguageDetector] 检测为韩语")
            return 'ko'
        elif re.search(r'[\u0600-\u06FF]', text):
            logger.info("[LanguageDetector] 检测为阿拉伯语")
            return 'ar'
        elif re.search(r'[A-Za-zÀ-ÿ]', text) and not re.search(r'[\u4e00-\u9fff\u0400-\u04FF\u3040-\u30FF\uAC00-\uD7AF\u0600-\u06FF]', text):
            logger.info("[LanguageDetector] 检测为拉丁语系，返回 en")
            return 'en'
        logger.warning("[LanguageDetector] 未能检测出语言，返回 unknown")
        return 'unknown'
