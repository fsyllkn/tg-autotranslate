import asyncio
import json
import yaml
import logging
import os
import re
import time
import aiohttp
from telethon import events
from telethon.sync import TelegramClient
import threading

# ========== 配置加载 ==========
def load_yaml_config(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        msg = f"配置文件格式错误（YAML语法错误）: {path}, 错误: {e}"
        print(msg)
        logging.error(msg, exc_info=True)
        return {}
    except Exception as e:
        msg = f"加载YAML配置文件失败: {path}, 错误: {e}"
        print(msg)
        logging.error(msg, exc_info=True)
        return {}

def reload_config():
    global config, self_default_rule, other_default_rule, translate_rules, ignore_words
    global DEEPLX_FAIL_THRESHOLD, OPENAI_FAIL_THRESHOLD
    config = load_yaml_config('config.yaml')
    if not config:
        msg = "配置文件加载失败或格式错误，请检查config.yaml！"
        print(msg)
        logging.error(msg)
        return
    self_default_rule = str(config.get("self_default_rule", "zh,en"))
    other_default_rule = str(config.get("other_default_rule", "en,zh"))
    translate_rules = {str(k): v for k, v in config.get("translate_rules", {}).items()} if "translate_rules" in config else {}
    ignore_words = set(config.get('ignore_words', []))
    # 失败阈值参数支持热加载
    DEEPLX_FAIL_THRESHOLD = int(config.get('deeplx_fail_threshold', 3))
    OPENAI_FAIL_THRESHOLD = int(config.get('openai_fail_threshold', 3))
    # 重新初始化openai_client和其他依赖配置
    try:
        from openai import OpenAI
        global openai_client
        openai_client = OpenAI(
            api_key=config['openai']['api_keys'][0] if config['openai'].get('api_keys') else "",
            base_url=config['openai']['base_urls'][0] if config['openai'].get('base_urls') else ""
        )
    except Exception as e:
        logging.error(f"OpenAI客户端初始化失败: {e}")

def load_json_config(path):
    try:
        if not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"加载JSON配置文件失败: {path}, 错误: {e}", exc_info=True)
        return {}

def save_json_config(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存JSON配置文件失败: {path}, 错误: {e}", exc_info=True)

config = load_yaml_config('config.yaml')
rules_path = 'dynamic_rules.json'
rules = load_json_config(rules_path)

# ========== 失败阈值参数支持配置文件自定义 ==========
DEEPLX_FAIL_THRESHOLD = int(config.get('deeplx_fail_threshold', 3))
OPENAI_FAIL_THRESHOLD = int(config.get('openai_fail_threshold', 3))

# ======== 加载翻译方向规则配置 ==========
self_default_rule = str(config.get("self_default_rule", "zh,en"))
other_default_rule = str(config.get("other_default_rule", "en,zh"))
translate_rules = {str(k): v for k, v in config.get("translate_rules", {}).items()} if "translate_rules" in config else {}

# ========== 热加载定时器 ==========
async def config_hot_reload_loop(interval=60):
    last_mtime = None
    config_path = os.path.abspath('config.yaml')
    logging.info("[HOT-RELOAD] 异步热加载任务已启动。")
    while True:
        try:
            if os.path.exists(config_path):
                mtime = os.path.getmtime(config_path)
                if last_mtime is None:
                    last_mtime = mtime
                elif mtime != last_mtime:
                    reload_config()
                    logging.info("[HOT-RELOAD] config.yaml 检测到变更，已自动热加载")
                    last_mtime = mtime
        except Exception as e:
            logging.error(f"[HOT-RELOAD] config.yaml 热加载检测失败: {e}")
        await asyncio.sleep(interval)

# ========== 禁用接口定期健康检查与自动恢复 ==========
async def disabled_engine_health_check_loop(interval=300):
    import random
    while True:
        # Deeplx禁用检测
        try:
            if deeplx_disabled:
                deeplx_cfg = config.get('deeplx', {})
                base_urls = deeplx_cfg.get('base_urls', [])
                for idx in list(deeplx_disabled):
                    if idx >= len(base_urls):
                        continue
                    base_url = base_urls[idx]
                    payload = {
                        "text": "hello",
                        "source_lang": "en",
                        "target_lang": "zh"
                    }
                    async with aiohttp.ClientSession() as session:
                        try:
                            async with session.post(base_url, json=payload, timeout=8) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    if data.get('code') == 200:
                                        deeplx_disabled.remove(idx)
                                        deeplx_fail_count[idx] = 0
                                        logging.info(f"[HEALTH] Deeplx接口已恢复: 第{idx+1}个 {base_url}")
                        except Exception:
                            pass
        except Exception as e:
            logging.warning(f"[HEALTH] Deeplx禁用检测异常: {e}")

        # OpenAI禁用检测
        try:
            if openai_disabled:
                openai_cfg = config.get('openai', {})
                urls = openai_cfg.get('base_urls', [])
                keys = openai_cfg.get('api_keys', [])
                min_count = min(len(urls), len(keys))
                for idx in list(openai_disabled):
                    if idx >= min_count:
                        continue
                    try:
                        from openai import OpenAI
                        client = OpenAI(api_key=keys[idx], base_url=urls[idx])
                        def do_translate():
                            try:
                                response = client.chat.completions.create(
                                    model="gpt-4o",
                                    messages=[
                                        {"role": "system", "content": "You are a translation engine, only returning translated answers."},
                                        {"role": "user", "content": "hello"}
                                    ]
                                )
                                return response.choices[0].message.content
                            except Exception:
                                return None
                        import asyncio
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(None, do_translate)
                        if result:
                            openai_disabled.remove(idx)
                            openai_fail_count[idx] = 0
                            logging.info(f"[HEALTH] OpenAI接口已恢复: 第{idx+1}个 {urls[idx]}，key序号: {idx}")
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[HEALTH] OpenAI禁用检测异常: {e}")
        await asyncio.sleep(interval)

def parse_rule_pair(rule_key, fallback):
    """parse rule pair, 支持一对多、多对多语种，返回([src_list],[tgt_list])"""
    def split_langs(text):
        if "|" in text:
            return [lng.strip() for lng in text.split("|") if lng.strip()]
        if "," in text:
            return [lng.strip() for lng in text.split(",") if lng.strip()]
        return [text.strip()]
    if "," in str(rule_key):
        pair = [w.strip() for w in rule_key.split(",")]
        if len(pair) == 2:
            srcs = split_langs(pair[0])
            tgts = split_langs(pair[1])
            return srcs, tgts
    if "," in str(fallback):
        pair = [w.strip() for w in str(fallback).split(",")]
        if len(pair) == 2:
            return split_langs(pair[0]), split_langs(pair[1])
    rule_val2 = translate_rules.get(str(fallback))
    if rule_val2 and "," in rule_val2:
        pair = [w.strip() for w in rule_val2.split(",")]
        if len(pair) == 2:
            return split_langs(pair[0]), split_langs(pair[1])
    return ["zh"], ["en"]

# ========== 日志设置 ==========
import logging.handlers
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
# 日志文件按天分割，保留7天
file_handler = logging.handlers.TimedRotatingFileHandler(LOG_FILE, when='midnight', backupCount=7, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
# 控制台输出
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
logging = logger  # 兼容原有logging调用

# ========== ignore_words 处理 ==========
ignore_words = set(config.get('ignore_words', []))

# 编译ignore_words为正则模式列表，支持更复杂的前缀、空格、数字等场景
def build_ignore_patterns(words):
    patterns = []
    for word in words:
        w = word.strip()
        if not w:
            continue
        # 中文关键词：以该词开头+数字/空格/标点/结尾
        if re.search(r'[\u4e00-\u9fff]', w):
            patterns.append(re.compile(rf"^{re.escape(w)}([\d\s/，,。.!！、：:；;（）()\[\]\-—_]|$)", re.IGNORECASE))
        else:
            # 英文关键词：以w为单词边界开头（如".de", ".de ", ".de3"等），忽略大小写
            patterns.append(re.compile(rf"^{re.escape(w)}(\s|$|\d+)", re.IGNORECASE))
    return patterns

ignore_patterns = build_ignore_patterns(ignore_words)

def should_ignore(text):
    # 单行或多行，只要任一行以ignore_words关键词为前缀（严格正则）则整体忽略
    lines = text.strip().splitlines()
    for line in lines:
        l = line.strip()
        for pat in ignore_patterns:
            if pat.match(l):
                logging.info(f"[DEBUG] should_ignore命中: '{l}' 被 {pat.pattern} 拦截")
                return True
    logging.info(f"[DEBUG] should_ignore未命中: '{text}'")
    return False

# ========== Telegram 客户端初始化 ==========
tg_cfg = config['telegram']
api_id = tg_cfg['api_id']
api_hash = tg_cfg['api_hash']
session_name = tg_cfg['session_name']
my_tg_id = tg_cfg['my_tg_id']

client = TelegramClient(session_name, api_id, api_hash)

# ========== 动态规则操作 ==========
def get_rule(group_id, user_id):
    group_id = str(group_id)
    user_id = str(user_id)
    return rules.get(group_id, {}).get(user_id)

def set_rule(group_id, user_id, rule):
    group_id = str(group_id)
    user_id = str(user_id)
    if group_id not in rules:
        rules[group_id] = {}
    orig = rules[group_id].get(user_id, [])
    # always as list for merge
    rule_list = orig if isinstance(orig, list) else [orig] if orig else []
    # 判断是否源=目标完全一致，有则覆盖，否则append
    matched = False
    for i, old in enumerate(rule_list):
        if old.get("source_langs") == rule.get("source_langs") and old.get("target_langs") == rule.get("target_langs"):
            rule_list[i] = rule  # 覆盖
            matched = True
            break
    if not matched:
        rule_list.append(rule)
    rules[group_id][user_id] = rule_list
    save_json_config(rules_path, rules)

def remove_rule(group_id, user_id):
    group_id = str(group_id)
    user_id = str(user_id)
    if group_id in rules and user_id in rules[group_id]:
        del rules[group_id][user_id]
        if not rules[group_id]:
            del rules[group_id]
        save_json_config(rules_path, rules)

def list_rules():
    return rules

# ========== 命令解析与处理 ==========
def parse_on_command(args):
    # 支持顺序无关、批量用户id
    # args: [id1, id2, ...]
    group_id = None
    user_ids = []
    for i in range(2):
        if str(args[i]).startswith('-'):
            group_id = str(args[i])
        else:
            user_ids = args[i].split('|')
    if not group_id or not user_ids:
        return None, None
    # 解析源语言和目标语言
    source_langs = args[2].split('|') if len(args) > 2 else ['en']
    target_langs = args[3].split('|') if len(args) > 3 else ['zh']
    return group_id, user_ids, source_langs, target_langs

async def send_ephemeral_reply(event, reply_text):
    """发送指令回复，15秒后自动删除（指令消息和回复消息），并写日志"""
    try:
        rep_msg = await event.reply(reply_text)
        chat_id = event.chat_id
        operator_id = event.sender_id
        logging.info(f"[CMD-EPHEMERAL] user {operator_id} sent command: {event.text!r}, reply: {reply_text!r}")
        await asyncio.sleep(15)
        await event.client.delete_messages(chat_id, [event.id, rep_msg.id])
    except Exception as e:
        logging.warning(f"Failed to delete command/reply message: {e}")

@client.on(events.NewMessage)
async def handle_command(event):
    if not event.message.text:
        return
    text = event.message.text.strip()
    # 优先判断命令前缀，仅处理 .fy- 开头指令
    if not text.startswith('.fy-'):
        return
    parts = [p.strip() for p in text.split(',')]
    cmd = parts[0].lower()
    args = parts[1:]

    # 仅允许本人使用
    if event.sender_id != my_tg_id:
        return  # 非本人命令静默忽略，不做任何回复

    is_group = event.is_group or getattr(event, "is_group", False)
    is_private = event.is_private or getattr(event, "is_private", False)
    self_id = str(event.sender_id)
    chat_id = str(event.chat_id)

    try:
        ## ========= .fy-reload 配置热加载 ========= ##
        if cmd == ".fy-reload":
            reload_config()
            await send_ephemeral_reply(event, "config.yaml 已热加载，配置已更新。")
            return

        ## ========= .fy-list 展示所有翻译规则 ========= ##
        if cmd == ".fy-list":
            msg_lines = []
            def flatten(val):
                if isinstance(val, list):
                    return [str(item) for item in val]
                elif val is None:
                    return []
                else:
                    return [str(val)]
            for gid, usr_map in rules.items():
                for uid, rule_obj in usr_map.items():
                    # 兼容rule_obj为list或dict
                    rule_list = rule_obj if isinstance(rule_obj, list) else [rule_obj]
                    for rule in rule_list:
                        srcs = "|".join(flatten(rule.get("source_langs", [])))
                        tgts = "|".join(flatten(rule.get("target_langs", [])))
                        msg_lines.append(f"chat:{gid}  user:{uid}  {srcs}→{tgts}")
            msg = "当前无任何翻译规则。" if not msg_lines else "已保存的所有翻译规则:\n" + "\n".join(msg_lines)
            await send_ephemeral_reply(event, msg)
            return

        ## ========= .fy-clear 一键清除所有翻译规则 ========= ##
        if cmd == ".fy-clear":
            rules.clear()
            save_json_config(rules_path, {})
            await send_ephemeral_reply(event, "已清空所有会话、成员的翻译规则（dynamic_rules.json 已重置）。")
            return

        ## ========= .fy-help 指令说明 ========= ##
        if cmd == ".fy-help":
            help_msg = (
                "指令帮助：\n"
                "- `.fy-on` 私聊、群聊-开启自己中译英；\n"
                "- `.fy-off` 私聊、群聊-关闭自己翻译功能；\n"
                "- `.fy-on,fr|en,zh` 私聊、群聊-指定自己英或法译中；\n"
                "- `.fy-add` 私聊-为对方开默认英译中；\n"
                "- `.fy-del` 私聊-关闭翻译对方消息功能；\n"
                "- `.fy-del` 群聊-关闭翻译所有成员消息功能；\n"
                "- `.fy-add,zh|ru,en|fr` 私聊-为对方开中或者法语译英、法；\n"
                "- `.fy-add,成员id` 群组-对某个成员英译中；\n"
                "- `.fy-add,成员id,源语言,目标语言` 群聊-为指定成员开启方向翻译；\n"
                "- `.fy-del,成员id` 群组-关闭翻译指定成员消息功能；\n"
                "- `.fy-clear` 一键关闭所有翻译规则；\n"
                "- `.fy-list` 查看用户开启翻译功能的规则；\n"
                "- `.fy-help` 查看指令与用法说明。"
            )
            await send_ephemeral_reply(event, help_msg)
            return
        ## ========= .fy-on 开启自己翻译 ========= ##
        if cmd == ".fy-on":
            # 支持 .fy-on或 .fy-on,src,tgt/方向 任意场景，仅操作self
            argstr = ",".join(args) if args else ""
            rule_key = argstr if argstr else self_default_rule
            src_list, tgt_list = parse_rule_pair(rule_key, self_default_rule)
            saved_rules = []
            for src in src_list:
                filtered_tgts = [tgt for tgt in tgt_list if tgt != src]
                if filtered_tgts:
                    set_rule(chat_id, self_id, {"source_langs": [src], "target_langs": filtered_tgts})
                    saved_rules.append(f"{src}→{'|'.join(filtered_tgts)}")
            if saved_rules:
                await send_ephemeral_reply(
                    event,
                    f"已为 [自己] 在本{'群组' if is_group else '私聊'}开启翻译：\n" + "\n".join(saved_rules)
                )
            else:
                await send_ephemeral_reply(
                    event,
                    "源语言和目标语言完全重叠，无需翻译，规则未保存。"
                )
            return

        ## ========= .fy-off 关闭自身翻译 ========= ##
        if cmd == ".fy-off":
            remove_rule(chat_id, self_id)
            await send_ephemeral_reply(event, "已关闭自己在本会话（群聊/私聊）的翻译。")
            return

        ## ========= .fy-add 添加成员/对方翻译规则 ========= ##
        if cmd == ".fy-add":
            if is_group:
                # .fy-add,成员id,src,tgt
                # .fy-add,成员id,方向
                # .fy-add,成员id
                if args and str(args[0]).isdigit():
                    mem_id = args[0]
                    if len(args) == 1:
                        src, tgt = parse_rule_pair(other_default_rule, "en,zh")
                    elif len(args) == 3:
                        src, tgt = parse_rule_pair(args[1] + "," + args[2], other_default_rule)
                    elif len(args) == 2:
                        src, tgt = parse_rule_pair(args[1], other_default_rule)
                    else:
                        await send_ephemeral_reply(event, "参数格式: .fy-add,成员id 或 .fy-add,成员id,源,目标")
                        return
                    saved_rules = []
                    for s in src:
                        filtered_tgts = [t for t in tgt if t != s]
                        if filtered_tgts:
                            set_rule(chat_id, mem_id, {"source_langs": [s], "target_langs": filtered_tgts})
                            saved_rules.append(f"{s}→{'|'.join(filtered_tgts)}")
                    if saved_rules:
                        await send_ephemeral_reply(
                            event, f"已为群组成员{mem_id}启用翻译：\n" + "\n".join(saved_rules)
                        )
                    else:
                        await send_ephemeral_reply(
                            event, f"源语言和目标语言完全重叠，无需翻译，规则未保存。"
                        )
                    return
                else:
                    await send_ephemeral_reply(event, "请指定成员id作为第一个参数，如.fy-add,12345,zh,en")
                    return
            elif is_private:
                # .fy-add  对方默认（无参数）或指定方向
                if not args:
                    src, tgt = parse_rule_pair(other_default_rule, "en,zh")
                elif len(args) == 2:
                    src, tgt = parse_rule_pair(args[0] + "," + args[1], other_default_rule)
                elif len(args) == 1:
                    src, tgt = parse_rule_pair(args[0], other_default_rule)
                else:
                    await send_ephemeral_reply(event, "参数格式: .fy-add 或 .fy-add,源,目标")
                    return
                saved_rules = []
                for s in src:
                    filtered_tgts = [t for t in tgt if t != s]
                    if filtered_tgts:
                        set_rule(chat_id, chat_id, {"source_langs": [s], "target_langs": filtered_tgts})
                        saved_rules.append(f"{s}→{'|'.join(filtered_tgts)}")
                if saved_rules:
                    await send_ephemeral_reply(
                        event, f"已为对方（此私聊）启用翻译：\n" + "\n".join(saved_rules)
                    )
                else:
                    await send_ephemeral_reply(
                        event, "源语言和目标语言完全重叠，无需翻译，规则未保存。"
                    )
                return
            else:
                await send_ephemeral_reply(event, "请在私聊或群聊中使用 .fy-add")
                return

        ## ========= .fy-del 删除规则 ========= ##
        if cmd == ".fy-del":
            if is_group:
                # .fy-del：无参数→删除所有成员；有参数删除指定成员
                if not args:
                    if chat_id in rules:
                        # 删除全体成员
                        rules[chat_id] = {}
                        save_json_config(rules_path, rules)
                        await send_ephemeral_reply(event, "已移除当前群所有成员翻译规则")
                    else:
                        await send_ephemeral_reply(event, "本群无可删除成员规则")
                    return
                if args and str(args[0]).isdigit():
                    mem_id = args[0]
                    remove_rule(chat_id, mem_id)
                    await send_ephemeral_reply(event, f"已移除成员{mem_id}在本群的翻译规则")
                    return
                await send_ephemeral_reply(event, ".fy-del,成员id：指定成员，或无参数删除全群成员规则")
                return
            elif is_private:
                # 私聊无参数即删除对方翻译
                if not args:
                    remove_rule(chat_id, chat_id)
                    await send_ephemeral_reply(event, "已移除对方翻译规则")
                else:
                    await send_ephemeral_reply(event, ".fy-del：私聊无参数为删除对方翻译规则")
                return
            return

        # ...（其余命令逻辑保持不变）
    except Exception as e:
        logging.error(f"命令处理异常: {e}")
        await event.reply(f"命令处理异常: {e}")
    except Exception as e:
        logging.error(f"命令处理异常: {e}")
        await event.reply(f"命令处理异常: {e}")

# ========== 翻译API实现（OpenAI/Deeplx主备机制） ==========
from openai import OpenAI

# 在读取配置后，初始化 OpenAI 客户端
openai_client = OpenAI(
    api_key=config['openai']['api_keys'][0] if config['openai'].get('api_keys') else "",
    base_url=config['openai']['base_urls'][0] if config['openai'].get('base_urls') else ""
)

# ========== Deeplx/OpenAI 顺序轮询+禁用+健康检查机制 ==========
from collections import defaultdict

deeplx_fail_count = defaultdict(int)
deeplx_disabled = set()
current_deeplx_idx = 0

openai_fail_count = defaultdict(int)
openai_disabled = set()
current_openai_idx = 0

DEEPLX_FAIL_THRESHOLD = int(config.get('deeplx_fail_threshold', 3))
OPENAI_FAIL_THRESHOLD = int(config.get('openai_fail_threshold', 3))

async def translate_with_deeplx(text, source_lang, target_lang):
    global current_deeplx_idx
    deeplx_cfg = config['deeplx']
    base_urls = deeplx_cfg.get('base_urls', [])
    n = len(base_urls)
    if n == 0:
        raise Exception("deeplx base_urls 未配置")
    tried = 0
    while tried < n:
        idx = current_deeplx_idx % n
        current_deeplx_idx = (current_deeplx_idx + 1) % n
        if idx in deeplx_disabled:
            tried += 1
            continue
        base_url = base_urls[idx]
        try:
            payload = {
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang
            }
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(base_url, json=payload, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get('code') == 200:
                                deeplx_fail_count[idx] = 0  # 成功清零
                                return data['data']
                            else:
                                deeplx_fail_count[idx] += 1
                                logging.warning(f"Deeplx接口 {base_url} 返回异常: {data}")
                        else:
                            deeplx_fail_count[idx] += 1
                            logging.warning(f"Deeplx接口 {base_url} 失败，状态码: {resp.status}")
                except Exception as e:
                    deeplx_fail_count[idx] += 1
                    logging.error(f"Deeplx接口 {base_url} 网络请求异常: {e}", exc_info=True)
        except Exception as e:
            deeplx_fail_count[idx] += 1
            logging.warning(f"Deeplx接口 {base_url} 失败，尝试下一个: {e}", exc_info=True)
        # 超阈值禁用
        if deeplx_fail_count[idx] >= DEEPLX_FAIL_THRESHOLD:
            deeplx_disabled.add(idx)
            logging.error(f"已禁用第{idx+1}个deeplx接口: {base_url}，连续失败{DEEPLX_FAIL_THRESHOLD}次，请及时检查或更新！")
            # 启动健康检查任务（如未启动）
            if not getattr(translate_with_deeplx, "_health_check_started", False):
                loop = asyncio.get_event_loop()
                loop.create_task(disabled_engine_health_check_loop())
                translate_with_deeplx._health_check_started = True
        tried += 1
    raise Exception("所有Deeplx接口均已禁用或不可用")

async def translate_with_openai(text, target_language):
    global current_openai_idx
    import asyncio
    openai_cfg = config['openai']
    urls = openai_cfg.get('base_urls', [])
    keys = openai_cfg.get('api_keys', [])
    min_count = min(len(urls), len(keys))
    if min_count == 0:
        raise Exception("openai base_urls 或 api_keys 未配置")
    tried = 0
    loop = asyncio.get_event_loop()
    while tried < min_count:
        idx = current_openai_idx % min_count
        current_openai_idx = (current_openai_idx + 1) % min_count
        if idx in openai_disabled:
            tried += 1
            continue
        try:
            from openai import OpenAI
            client = OpenAI(api_key=keys[idx], base_url=urls[idx])
            def do_translate():
                try:
                    models = openai_cfg.get('models', ['gpt-4o'])
                    model_name = models[0] if models else 'gpt-4o'
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "You are a translation engine, only returning translated answers."},
                            {"role": "user", "content": f"Translate the text to {target_language} please do not explain my original text, do not explain the translation results, Do not explain the context.:\n{text}"}
                        ]
                    )
                    return response.choices[0].message.content
                except Exception as e:
                    logging.error(f"OpenAI do_translate异常: {e}", exc_info=True)
                    raise
            result = await loop.run_in_executor(None, do_translate)
            openai_fail_count[idx] = 0  # 成功清零
            return result
        except Exception as e:
            openai_fail_count[idx] += 1
            logging.warning(f"OpenAI接口 SDK {urls[idx]}+key[{idx}]调用失败: {e}", exc_info=True)
            if openai_fail_count[idx] >= OPENAI_FAIL_THRESHOLD:
                openai_disabled.add(idx)
                logging.error(f"已禁用第{idx+1}个OpenAI接口: {urls[idx]}，key序号: {idx}，连续失败{OPENAI_FAIL_THRESHOLD}次，请及时检查或更新！")
                # 启动健康检查任务（如未启动）
                if not getattr(translate_with_openai, "_health_check_started", False):
                    loop = asyncio.get_event_loop()
                    loop.create_task(disabled_engine_health_check_loop())
                    translate_with_openai._health_check_started = True
            tried += 1
            continue
    raise Exception("所有OpenAI接口均已禁用或不可用")

# ========== 禁用接口定期健康检查与自动恢复 ==========
async def disabled_engine_health_check_loop(interval=600):
    import random
    while deeplx_disabled or openai_disabled:
        # Deeplx禁用检测
        try:
            if deeplx_disabled:
                deeplx_cfg = config.get('deeplx', {})
                base_urls = deeplx_cfg.get('base_urls', [])
                for idx in list(deeplx_disabled):
                    if idx >= len(base_urls):
                        continue
                    base_url = base_urls[idx]
                    payload = {
                        "text": "hello",
                        "source_lang": "en",
                        "target_lang": "zh"
                    }
                    async with aiohttp.ClientSession() as session:
                        try:
                            async with session.post(base_url, json=payload, timeout=8) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    if data.get('code') == 200:
                                        deeplx_disabled.remove(idx)
                                        deeplx_fail_count[idx] = 0
                                        logging.info(f"[HEALTH] Deeplx接口已恢复: 第{idx+1}个 {base_url}")
                        except Exception:
                            pass
        except Exception as e:
            logging.warning(f"[HEALTH] Deeplx禁用检测异常: {e}")

        # OpenAI禁用检测
        try:
            if openai_disabled:
                openai_cfg = config.get('openai', {})
                urls = openai_cfg.get('base_urls', [])
                keys = openai_cfg.get('api_keys', [])
                min_count = min(len(urls), len(keys))
                for idx in list(openai_disabled):
                    if idx >= min_count:
                        continue
                    try:
                        from openai import OpenAI
                        client = OpenAI(api_key=keys[idx], base_url=urls[idx])
                        def do_translate():
                            try:
                                models = openai_cfg.get('models', ['gpt-4o'])
                                model_name = models[0] if models else 'gpt-4o'
                                response = client.chat.completions.create(
                                    model=model_name,
                                    messages=[
                                        {"role": "system", "content": "You are a translation engine, only returning translated answers."},
                                        {"role": "user", "content": "hello"}
                                    ]
                                )
                                return response.choices[0].message.content
                            except Exception:
                                return None
                        import asyncio
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(None, do_translate)
                        if result:
                            openai_disabled.remove(idx)
                            openai_fail_count[idx] = 0
                            logging.info(f"[HEALTH] OpenAI接口已恢复: 第{idx+1}个 {urls[idx]}，key序号: {idx}")
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[HEALTH] OpenAI禁用检测异常: {e}")
        await asyncio.sleep(interval)

async def translate_text(text, source_lang, target_langs, prefer=None):
    # 主备引擎参数
    if prefer is None:
        prefer = config.get('default_translate_source', 'deeplx')
    # 自动推断备选引擎
    backup = 'deeplx' if prefer == 'openai' else 'openai'
    result = {}
    all_primary_failed = True
    # 主引擎优先尝试全部目标
    for lang in target_langs:
        try:
            if prefer == 'deeplx':
                result[lang] = await translate_with_deeplx(text, source_lang, lang)
            else:
                result[lang] = await translate_with_openai(text, lang)
            all_primary_failed = False
        except Exception as e:
            logging.warning(f"{prefer}翻译{lang}失败: {e}")
            result[lang] = None
    # 若全部主引擎失败才整体 fallback
    if all_primary_failed:
        for lang in target_langs:
            try:
                if backup == 'deeplx':
                    result[lang] = await translate_with_deeplx(text, source_lang, lang)
                else:
                    result[lang] = await translate_with_openai(text, lang)
            except Exception as e:
                result[lang] = f"[翻译失败]{e}"
    # 删除翻译失败的 None 项
    return {k: v for k, v in result.items() if v is not None and v != ""}

# ========== 消息处理主流程 ==========
def contains_chinese(text):
    # 仅当文本中有明显中文汉字时才返回True
    for character in text:
        if '\u4e00' <= character <= '\u9fff':
            return True
    return False

import unicodedata
import string
import re

def remove_emoji_and_punct(text):
    # 去除emoji和常见标点
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE,
    )
    # 去除emoji
    text = emoji_pattern.sub(r'', text)
    # 去除标点
    text = ''.join(ch for ch in text if ch not in string.punctuation and not unicodedata.category(ch).startswith('P'))
    return text

def contains_non_chinese(text):
    # 先去除emoji和标点
    clean = remove_emoji_and_punct(text)
    # 检查是否含有拉丁字母（欧美语言）
    if re.search(r'[A-Za-z]', clean):
        return True
    # 检查常见世界大语种（阿拉伯文、波斯文、印地文、乌尔都文、土耳其文、西里尔文等）
    # 阿拉伯文: \u0600-\u06FF，波斯文: \u0600-\u06FF，印地文: \u0900-\u097F，乌尔都文: \u0600-\u06FF
    # 土耳其文主要用拉丁字母，西里尔文: \u0400-\u04FF
    # 韩文: \uAC00-\uD7AF，日文假名: \u3040-\u30FF
    if re.search(r'[\u0600-\u06FF]', clean):  # 阿拉伯/波斯/乌尔都
        return True
    if re.search(r'[\u0900-\u097F]', clean):  # 印地文
        return True
    if re.search(r'[\u0400-\u04FF]', clean):  # 西里尔文（俄语等）
        return True
    if re.search(r'[\uAC00-\uD7AF]', clean):  # 韩文
        return True
    if re.search(r'[\u3040-\u30FF]', clean):  # 日文假名
        return True
    # 只剩下的字符如果全是中文或空，则不算“非中文”
    return False

def is_pure_url(text):
    url_pattern = r'^\s*http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+\s*$'
    return re.match(url_pattern, text) is not None

@client.on(events.NewMessage)
async def handle_message(event):
    # 只处理非命令消息，且消息未被删除
    if not event.message or not event.message.text:
        return
    # 检查消息是否已被删除（如被其他bot删除）
    try:
        # 若消息已被删除，访问event.message会抛异常或event.message.deleted为True
        if getattr(event.message, "deleted", False):
            return
    except Exception:
        return
    text = event.message.text.strip()
    # 优先判断是否为忽略关键词，命中则直接return
    if should_ignore(text):
        return
    if text.startswith('.fy-'):
        return  # 命令消息交由handle_command处理
    if is_pure_url(text):
        return

    group_id = str(event.chat_id)
    user_id = str(event.sender_id)
    rule_raw = get_rule(group_id, user_id)
    logging.info(f"[DEBUG] handle_message in chat_id={group_id}, user_id={user_id}, rule_found={bool(rule_raw)}")
    if not rule_raw:
        return

    rule_list = rule_raw if isinstance(rule_raw, list) else [rule_raw]
    reply_text = ""
    prefer = config.get('default_translate_source', 'deeplx')
    hit = False
    # 语言代码到中文名称映射
    lang_map = {
        "en": "英语",
        "zh": "中文",
        "fr": "法语",
        "de": "德语",
        "ru": "俄语",
        "ja": "日语",
        "ko": "韩语",
        "ar": "阿拉伯语",
        "hi": "印地语",
        "tr": "土耳其语",
        "fa": "波斯语",      # 伊朗
        "uk": "乌克兰语",    # 乌克兰
        "es": "西班牙语",    # 西班牙
        "it": "意大利语",    # 意大利、瑞士
        "rm": "罗曼什语",    # 瑞士
        "pt": "葡萄牙语",
        "pl": "波兰语",
        "nl": "荷兰语",
        "sv": "瑞典语",
        "ro": "罗马尼亚语",
        "cs": "捷克语",
        "el": "希腊语",
        "da": "丹麦语",
        "fi": "芬兰语",
        "hu": "匈牙利语",
        "he": "希伯来语",
        "bg": "保加利亚语",
        "sr": "塞尔维亚语",
        "hr": "克罗地亚语",
        "sk": "斯洛伐克语",
        "sl": "斯洛文尼亚语",
        "no": "挪威语",
        # 可根据需要继续扩展
    }
    # 新增：字符区间法语言识别，完全替换 langdetect
    def detect_language(text):
        pure_text = remove_emoji_and_punct(text)
        # 中文
        if re.search(r'[\u4e00-\u9fff]', pure_text):
            return 'zh'
        # 俄语
        elif re.search(r'[\u0400-\u04FF]', pure_text):
            return 'ru'
        # 日语
        elif re.search(r'[\u3040-\u30FF]', pure_text):
            return 'ja'
        # 韩语
        elif re.search(r'[\uAC00-\uD7AF]', pure_text):
            return 'ko'
        # 阿拉伯语
        elif re.search(r'[\u0600-\u06FF]', pure_text):
            return 'ar'
        # 拉丁语系（含所有拉丁字母及变体，排除特殊区间）
        elif re.search(r'[A-Za-zÀ-ÿ]', pure_text) and not re.search(r'[\u4e00-\u9fff\u0400-\u04FF\u3040-\u30FF\uAC00-\uD7AF\u0600-\u06FF]', pure_text):
            return 'latin'
        else:
            return None

    detected_lang = detect_language(text)

    def normalize_punct(s):
        # 全角转半角（严格一一对应，长度一致）
        full = "，。！？：；“”‘’（）【】《》、．·？！（）"
        half = ",.!?:;\"\"''()[]<>/,..?!()"  # 与full等长
        table = str.maketrans(full, half)
        s = s.translate(table)
        # 统一空格
        s = s.replace('\u3000', ' ')
        return s

    for rule in rule_list:
        source_langs = rule.get('source_langs', ['en'])
        target_langs = rule.get('target_langs', ['zh'])
        # 智能识别源语言，找到适合本条消息的语言组
        active_source = None
        # 仅用于判断的纯文本（去除emoji和所有标点符号）
        pure_text = remove_emoji_and_punct(text)
        logging.info(f"[DEBUG] 检测到的源语言: detected_lang={detected_lang}, source_langs={source_langs}")
        for sl in source_langs:
            if sl == "zh" and contains_chinese(pure_text):
                active_source = sl
                break
            # 支持拉丁语系泛匹配：source_langs 里有 latin 时，所有拉丁语种都可触发
            elif detected_lang == "latin" and (sl.lower() == "latin" or sl.lower() in ["fr", "it", "de", "es", "pt", "nl", "sv", "fi", "da", "no", "pl", "cs", "ro", "sk", "sl", "hr", "hu", "tr", "bg", "el", "lt", "lv", "et", "mt", "ga", "rm"]):
                active_source = sl
                break
            elif sl != "zh" and detected_lang and sl.lower() == detected_lang.lower():
                active_source = sl
                break
        # 优化fallback: langdetect失败时，优先用正则判断是否为纯英文或纯中文，并提升英文判定准确率
        if not active_source and (not detected_lang or detected_lang == "None"):
            # 只含汉字
            if re.fullmatch(r'[\u4e00-\u9fff\s]*', pure_text):
                if "zh" in source_langs:
                    active_source = "zh"
                    logging.info(f"[DEBUG] fallback: 纯汉字正则命中，假定active_source为'zh'")
            # 只含拉丁字母、空格，且字母占比>80%，且 source_langs 只包含 en 时才判为英文
            elif re.fullmatch(r'[A-Za-z\s]*', pure_text) and pure_text:
                letter_count = sum(1 for c in pure_text if c.isalpha())
                ratio = letter_count / len(pure_text.replace(" ", "")) if pure_text.replace(" ", "") else 0
                if ratio > 0.8 and len(source_langs) == 1 and source_langs[0].lower() == "en":
                    active_source = "en"
                    logging.info(f"[DEBUG] fallback: 纯英文正则+字母占比命中，且仅有en，假定active_source为'en'")
            # 其它情况不再 fallback 为英文，避免多语种规则误判
        if not active_source:
            logging.info(f"[DEBUG] 未找到匹配的源语言: detected_lang={detected_lang}, source_langs={source_langs}")
            continue
        if not active_source:
            logging.info(f"[DEBUG] 未找到匹配的源语言: detected_lang={detected_lang}, source_langs={source_langs}")
            continue
        try:
            start_time = time.time()
            translated = await translate_text(text, active_source, target_langs, prefer=prefer)
            logging.info(f"翻译耗时: {time.time() - start_time:.2f}s")
            for lang in target_langs:
                reply = translated.get(lang, "")
                if reply:
                    lang_name = lang_map.get(lang, lang)
                    reply_text += f"{lang_name}：`{reply}`\n"
            hit = True
        except Exception as e:
            logging.error(f"消息翻译异常: {e}")
    if hit and reply_text.strip():
        try:
            await event.reply(reply_text.strip())
        except Exception as e:
            logging.error(f"Reply error (chat_id={group_id}, user_id={user_id}): {type(e).__name__} - {e}")

import sys
import time

# ========== 全局异常处理 ==========
def global_exception_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("未捕获异常", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = global_exception_handler

def handle_async_exception(loop, context):
    msg = context.get("exception", context["message"])
    logging.error(f"Asyncio全局异常: {msg}")

if __name__ == "__main__":
    # 首次加载配置，若格式错误则立即报错并退出
    config = load_yaml_config('config.yaml')
    if not config:
        msg = "配置文件加载失败或格式错误，请检查config.yaml！"
        print(msg)
        logging.error(msg)
        sys.exit(1)
    print("配置和规则加载完毕，主程序入口。")
    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(handle_async_exception)
        # 启动异步热加载任务
        loop.create_task(config_hot_reload_loop(interval=60))
        # 不再启动禁用接口健康检查任务
        client.start()  # 会自动检测 session，不存在或无效则弹出登录提示（二维码或手机号认证）
        print("Telegram 客户端已启动，等待消息...")
        client.run_until_disconnected()
    except (KeyboardInterrupt, SystemExit):
        print("\n[INFO] 收到退出信号（Ctrl+C），正在断开 Telegram 连接...")
        logging.info("收到退出信号（Ctrl+C），正在断开 Telegram 连接...")
    except Exception as e:
        print(f"[ERROR] 程序异常退出: {e}")
        logging.error(f"程序异常退出: {e}", exc_info=True)
    finally:
        try:
            client.disconnect()
            print("[INFO] Telegram 客户端连接已优雅断开，SESSION 状态已落盘。")
            logging.info("Telegram 客户端连接已优雅断开，SESSION 状态已落盘。")
            time.sleep(0.5)  # 短暂等待确保 session 全部写入
        except Exception as e:
            print(f"[WARN] 断开连接出错: {e}")
            logging.warning(f"断开连接出错: {e}", exc_info=True)
        sys.exit(0)
