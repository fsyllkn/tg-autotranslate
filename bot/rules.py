"""
rules.py
动态规则管理模块
"""

import json
import threading
import os
import logging

logger = logging.getLogger(__name__)

class RuleManager:
    """
    负责 dynamic_rules.json 的读写、增删查改
    支持多群/多用户/多规则
    """
    def __init__(self, path='dynamic_rules.json'):
        self.path = path
        self._lock = threading.Lock()
        logger.info(f"[RuleManager] 初始化，加载规则文件: {self.path}")
        self._rules = self._load_rules()

    def _load_rules(self):
        if not os.path.exists(self.path):
            logger.warning(f"[RuleManager] 规则文件不存在: {self.path}")
            return {}
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                rules = json.load(f)
                logger.info(f"[RuleManager] 规则文件加载成功: {self.path}")
                return rules
        except Exception as e:
            logger.error(f"[RuleManager] 规则文件加载失败: {e}")
            return {}

    def _save_rules(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self._rules, f, ensure_ascii=False, indent=2)
            logger.info(f"[RuleManager] 规则文件保存成功: {self.path}")
        except Exception as e:
            logger.error(f"[RuleManager] 规则文件保存失败: {e}")

    def get_rule(self, group_id, user_id):
        with self._lock:
            return self._rules.get(str(group_id), {}).get(str(user_id))

    def set_rule(self, group_id, user_id, rule, username=None):
        with self._lock:
            gid = str(group_id)
            uid = str(user_id)
            logger.info(f"[RuleManager] 设置规则: group_id={gid}, user_id={uid}, rule={rule}, username={username}")
            if gid not in self._rules:
                self._rules[gid] = {}
            orig = self._rules[gid].get(uid, [])
            rule_list = orig if isinstance(orig, list) else [orig] if orig else []
            # 支持多对多规则展开
            srcs = rule.get("source_langs", [])
            tgts = rule.get("target_langs", [])
            if not isinstance(srcs, list):
                srcs = [srcs]
            if not isinstance(tgts, list):
                tgts = [tgts]
            for src in srcs:
                for tgt in tgts:
                    if src == tgt:
                        continue
                    r = {"source_langs": [src], "target_langs": [tgt]}
                    if username is not None:
                        r["username"] = username
                    # 覆盖同样规则
                    replaced = False
                    for i, old in enumerate(rule_list):
                        if old.get("source_langs") == [src] and old.get("target_langs") == [tgt]:
                            rule_list[i] = r
                            replaced = True
                            break
                    if not replaced:
                        rule_list.append(r)
            self._rules[gid][uid] = rule_list
            self._save_rules()

    def remove_rule(self, group_id, user_id):
        with self._lock:
            gid = str(group_id)
            uid = str(user_id)
            logger.info(f"[RuleManager] 移除规则: group_id={gid}, user_id={uid}")
            if gid in self._rules and uid in self._rules[gid]:
                del self._rules[gid][uid]
                if not self._rules[gid]:
                    del self._rules[gid]
                self._save_rules()

    def list_rules(self):
        with self._lock:
            return self._rules.copy()

    def clear_all_rules(self):
        with self._lock:
            self._rules = {}
            self._save_rules()
            logger.info("[RuleManager] 已清空所有翻译规则")
