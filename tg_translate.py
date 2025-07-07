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
from collections import defaultdict

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
    global DEEPLX_FAIL_THRESHOLD, OPENAI_FAIL_THRESHOLD, fasttext_model_path, openai_cfg, tg_white_ids
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
    DEEPLX_FAIL_THRESHOLD = int(config.get('deeplx_fail_threshold', 3))
    OPENAI_FAIL_THRESHOLD = int(config.get('openai_fail_threshold', 3))
    fasttext_model_path = config.get('fasttext', {}).get('model_path', "lid.176.bin")
    openai_cfg = config.get('openai', {})
    # 支持多用户白名单
    tg_white_ids = set(config.get('telegram', {}).get('my_tg_ids', []))
    if not tg_white_ids:
        tg_white_ids = {config.get('telegram', {}).get('my_tg_id')}
    # 兼容单个id
    tg_white_ids = {int(i) for i in tg_white_ids if i}

def load_json_config(path):
    try:
        if not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"加载JSON配置文件失败: {path}, 错误: {e}", exc_info=True)
        return {}

# ========== 配置热重载任务 ==========
import hashlib

async def config_hot_reload_loop(interval=60):
    config_path = 'config.yaml'
    last_hash = None
    while True:
        try:
            with open(config_path, 'rb') as f:
                content = f.read()
                curr_hash = hashlib.md5(content).hexdigest()
            if last_hash is None:
                last_hash = curr_hash
            elif curr_hash != last_hash:
                reload_config()
                logging.info("[HOT-RELOAD] 检测到 config.yaml 变更，已自动热重载配置。")
                print("[HOT-RELOAD] 检测到 config.yaml 变更，已自动热重载配置。")
                last_hash = curr_hash
        except Exception as e:
            logging.warning(f"[HOT-RELOAD] 配置热重载检测异常: {e}")
        await asyncio.sleep(interval)

def save_json_config(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存JSON配置文件失败: {path}, 错误: {e}", exc_info=True)

config = load_yaml_config('config.yaml')
rules_path = 'dynamic_rules.json'
rules = load_json_config(rules_path)
fasttext_model_path = config.get('fasttext', {}).get('model_path', "lid.176.bin")
openai_cfg = config.get('openai', {})

# ========== 日志设置 ==========
import logging.handlers
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.handlers.TimedRotatingFileHandler(LOG_FILE, when='midnight', backupCount=7, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
logging = logger

# ========== ignore_words 处理 ==========
ignore_words = set(config.get('ignore_words', []))
def build_ignore_patterns(words):
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
ignore_patterns = build_ignore_patterns(ignore_words)
def should_ignore(text):
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
tg_white_ids = set(tg_cfg.get('my_tg_ids', []))
if not tg_white_ids:
    tg_white_ids = {tg_cfg.get('my_tg_id')}
tg_white_ids = {int(i) for i in tg_white_ids if i}
client = TelegramClient(session_name, api_id, api_hash)

# ========== 动态规则操作 ==========
def get_rule(group_id, user_id):
    group_id = str(group_id)
    user_id = str(user_id)
    return rules.get(group_id, {}).get(user_id)

def set_rule(group_id, user_id, rule, username=None):
    group_id = str(group_id)
    user_id = str(user_id)
    if group_id not in rules:
        rules[group_id] = {}
    orig = rules[group_id].get(user_id, [])
    rule_list = orig if isinstance(orig, list) else [orig] if orig else []
    # 拆分多对多为多条单一规则
    srcs = rule.get("source_langs", [])
    tgts = rule.get("target_langs", [])
    if not isinstance(srcs, list):
        srcs = [srcs]
    if not isinstance(tgts, list):
        tgts = [tgts]
    new_rules = []
    for src in srcs:
        for tgt in tgts:
            if src == tgt:
                continue
            r = {"source_langs": [src], "target_langs": [tgt]}
            if username is not None:
                r["username"] = username
            # 检查是否已存在同样的规则，存在则覆盖
            replaced = False
            for i, old in enumerate(rule_list):
                if old.get("source_langs") == [src] and old.get("target_langs") == [tgt]:
                    rule_list[i] = r
                    replaced = True
                    break
            if not replaced:
                rule_list.append(r)
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

# ========== 命令参数解析与校验 ==========
def parse_langs(text):
    if "|" in text:
        return [lng.strip() for lng in text.split("|") if lng.strip()]
    if "," in text:
        return [lng.strip() for lng in text.split(",") if lng.strip()]
    return [text.strip()]

def is_valid_lang_code(code):
    # 支持的语言代码列表，可扩展
    valid_codes = {
        "en","zh","fr","de","ru","ja","ko","ar","hi","tr","fa","uk","es","it","rm","pt","pl","nl","sv","ro","cs","el","da","fi","hu","he","bg","sr","hr","sk","sl","no"
    }
    return code in valid_codes

def validate_langs(lang_list):
    return all(is_valid_lang_code(l) for l in lang_list)

def parse_on_command(args):
    # 支持顺序无关、批量用户id
    group_id = None
    user_ids = []
    for i in range(2):
        if str(args[i]).startswith('-'):
            group_id = str(args[i])
        else:
            user_ids = args[i].split('|')
    if not group_id or not user_ids:
        return None, None, None, None
    source_langs = parse_langs(args[2]) if len(args) > 2 else ['en']
    target_langs = parse_langs(args[3]) if len(args) > 3 else ['zh']
    if not validate_langs(source_langs) or not validate_langs(target_langs):
        return None, None, None, None
    return group_id, user_ids, source_langs, target_langs

async def send_ephemeral_reply(event, reply_text):
    try:
        rep_msg = await event.reply(reply_text)
        chat_id = event.chat_id
        operator_id = event.sender_id
        logging.info(f"[CMD-EPHEMERAL] user {operator_id} sent command: {event.text!r}, reply: {reply_text!r}")
        await asyncio.sleep(15)
        # 确保 id 为 int
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
        logging.warning(f"Failed to delete command/reply message: {e}")

# ========== 命令处理 ==========
@client.on(events.NewMessage)
async def handle_command(event):
    if not event.message.text:
        return
    text = event.message.text.strip()
    if not text.startswith('.fy-'):
        return
    parts = [p.strip() for p in text.split(',')]
    cmd = parts[0].lower()
    args = parts[1:]

    # 仅允许白名单用户
    if event.sender_id not in tg_white_ids:
        return

    is_group = event.is_group or getattr(event, "is_group", False)
    is_private = event.is_private or getattr(event, "is_private", False)
    self_id = str(event.sender_id)
    chat_id = str(event.chat_id)

    try:
        if cmd == ".fy-reload":
            reload_config()
            await send_ephemeral_reply(event, "config.yaml 已热加载，配置已更新。")
            return

        if cmd == ".fy-list":
            msg_lines = []
            def flatten(val):
                if isinstance(val, list):
                    return [str(item) for item in val]
                elif val is None:
                    return []
                else:
                    return [str(val)]
            # 缓存已查到的用户信息，避免重复请求
            user_name_cache = {}
            chat_name_cache = {}
            async def get_display_name(uid):
                if uid in user_name_cache:
                    return user_name_cache[uid]
                try:
                    entity = await event.client.get_entity(int(uid))
                    if getattr(entity, "username", None):
                        name = f"@{entity.username}"
                    elif getattr(entity, "first_name", None):
                        name = entity.first_name
                    else:
                        name = str(entity.id)
                except Exception:
                    name = "none"
                user_name_cache[uid] = name
                return name

            async def get_chat_display_name(gid):
                if gid in chat_name_cache:
                    return chat_name_cache[gid]
                try:
                    entity = await event.client.get_entity(int(gid))
                    if getattr(entity, "username", None):
                        name = f"@{entity.username}"
                    elif getattr(entity, "title", None):
                        name = entity.title
                    else:
                        name = str(entity.id)
                except Exception:
                    name = str(gid)
                chat_name_cache[gid] = name
                return name

            # 异步收集所有 msg_lines
            async def build_msg_lines():
                lines = []
                for gid, usr_map in rules.items():
                    chat_disp = await get_chat_display_name(gid)
                    # chat_disp: 优先@username/群名，否则id
                    chat_show = chat_disp if chat_disp and chat_disp != str(gid) else str(gid)
                    for uid, rule_obj in usr_map.items():
                        rule_list = rule_obj if isinstance(rule_obj, list) else [rule_obj]
                        for rule in rule_list:
                            srcs = "|".join(flatten(rule.get("source_langs", [])))
                            tgts = "|".join(flatten(rule.get("target_langs", [])))
                            uname = rule.get("username", "none")
                            # 自己的规则特殊显示
                            if uid == str(event.sender_id):
                                if uname == "self" or uname == "本人":
                                    uname_disp = "本人/self"
                                elif uname.startswith("@"):
                                    uname_disp = uname
                                else:
                                    uname_disp = "本人/self"
                            else:
                                # 若无有效用户名，动态查找
                                if uname == "none" or not uname or uname == str(uid):
                                    uname_disp = await get_display_name(uid)
                                else:
                                    uname_disp = uname
                            # uname_disp: 优先@username/first_name，否则id
                            uname_show = uname_disp if uname_disp and uname_disp != str(uid) else str(uid)
                            lines.append(f"chat:{chat_show}  user:{uname_show}  {srcs}→{tgts}")
                return lines

            msg_lines = await build_msg_lines()
            msg = "当前无任何翻译规则。" if not msg_lines else "已保存的所有翻译规则:\n" + "\n".join(msg_lines)
            await send_ephemeral_reply(event, msg)
            return

        if cmd == ".fy-clear":
            rules.clear()
            save_json_config(rules_path, {})
            await send_ephemeral_reply(event, "已清空所有会话、成员的翻译规则（dynamic_rules.json 已重置）。")
            return

        if cmd == ".fy-help":
            help_msg = (
                "指令帮助：\n"
                "- `.fy-on` 私聊、群聊-开启对自己消息的翻译（规则默认）；\n"
                "- `.fy-off` 私聊、群聊-关闭自己翻译功能；\n"
                "- `.fy-on,fr|en,zh` 私聊、群聊-指定自己英或法译中；\n"
                "- `.fy-add` 私聊-翻译对方消息（规则默认）；\n"
                "- `.fy-del` 私聊-关闭翻译对方消息功能；\n"
                "- `.fy-del` 群聊-关闭翻译所有成员消息功能；\n"
                "- `.fy-add,zh|ru,en|fr` 私聊-为对方开中或者法语译英、法；\n"
                "- `.fy-add,成员id或用户名` 群聊-对某个成员英译中；\n"
                "- `.fy-add,成员id或用户名,源语言,目标语言` 群聊-为指定成员开启方向翻译；\n"
                "- `.fy-del,成员id或用户名` 群聊-关闭翻译指定成员消息功能；\n"
                "- `.fy-add,成员id或用户名,源语言,目标语言` 群聊-为指定成员开启方向翻译；\n"
                "- `.fy-del,成员id或用户名,*,ar|fr` 群聊-删除翻译指定成员消息部分规则功能；\n"
                "- `.fy-clear` 一键清空所有翻译规则；\n"
                "- `.fy-list` 查看用户开启翻译功能的规则；\n"
                "- `.fy-help` 查看指令与用法说明。"
            )
            await send_ephemeral_reply(event, help_msg)
            return

        if cmd == ".fy-on":
            argstr = ",".join(args) if args else ""
            # 默认开启中英互译
            if not argstr:
                src_list = ["zh", "en"]
                tgt_list = ["en", "zh"]
            else:
                rule_key = argstr if argstr else self_default_rule
                src_list = parse_langs(rule_key.split(",")[0]) if "," in rule_key else parse_langs(rule_key)
                tgt_list = parse_langs(rule_key.split(",")[1]) if "," in rule_key and len(rule_key.split(",")) > 1 else ["en"]
            if not validate_langs(src_list) or not validate_langs(tgt_list):
                await send_ephemeral_reply(event, "语言代码不合法，请检查输入。")
                return
            # 获取自己用户名
            try:
                username = f"@{event.sender.username}" if getattr(event.sender, "username", None) else "self"
            except Exception:
                username = "self"
            saved_rules = []
            for src in src_list:
                filtered_tgts = [tgt for tgt in tgt_list if tgt != src]
                if filtered_tgts:
                    set_rule(chat_id, self_id, {"source_langs": [src], "target_langs": filtered_tgts}, username=username)
                    saved_rules.append(f"{src}→{'|'.join(filtered_tgts)}")
            if saved_rules:
                # 规则内容优先用指令参数，否则用配置文件默认self规则
                if argstr:
                    if "," in argstr:
                        rule_desc = argstr
                    else:
                        rule_desc = f"{argstr}→en"
                else:
                    rule_desc = config.get("self_default_rule", "zh|en,en|zh")
                await send_ephemeral_reply(
                    event,
                    f"已为 [自己] 在本{'群组' if is_group else '私聊'}开启翻译，规则为：{rule_desc}"
                )
            else:
                await send_ephemeral_reply(
                    event,
                    "源语言和目标语言完全重叠，无需翻译，规则未保存。"
                )
            return

        if cmd == ".fy-off":
            # 支持 .fy-off,源,目标 只删除指定规则
            if not args:
                remove_rule(chat_id, self_id)
                await send_ephemeral_reply(event, "已关闭自己在本会话的翻译。")
                return
            src = parse_langs(args[0]) if len(args) >= 1 else []
            tgt = parse_langs(args[1]) if len(args) >= 2 else []
            rule_raw = get_rule(chat_id, self_id)
            if not rule_raw:
                await send_ephemeral_reply(event, "当前无可删除的翻译规则。")
                return
            rule_list = rule_raw if isinstance(rule_raw, list) else [rule_raw]
            new_rules = []
            # 拆分 src/tgt 多对多为一对一组合，精确删除
            if src and tgt:
                to_del = set()
                for s in src:
                    for t in tgt:
                        if s == t:
                            continue
                        to_del.add((s, t))
                for rule in rule_list:
                    rule_src = rule.get("source_langs", [])
                    rule_tgt = rule.get("target_langs", [])
                    if len(rule_src) == 1 and len(rule_tgt) == 1 and (rule_src[0], rule_tgt[0]) in to_del:
                        continue  # 删除
                    new_rules.append(rule)
            else:
                # 只写 src 或 tgt，保持批量删除能力
                for rule in rule_list:
                    rule_src = set(rule.get("source_langs", []))
                    rule_tgt = set(rule.get("target_langs", []))
                    src_match = not src or any(s in rule_src for s in src)
                    tgt_match = not tgt or any(t in rule_tgt for t in tgt)
                    if not (src_match and tgt_match):
                        new_rules.append(rule)
            if not new_rules:
                remove_rule(chat_id, self_id)
                await send_ephemeral_reply(event, f"已移除指定源/目标语言的翻译规则。")
            else:
                rules[str(chat_id)][str(self_id)] = new_rules
                save_json_config(rules_path, rules)
                await send_ephemeral_reply(event, f"已移除部分规则，其余规则仍保留。")
            return

        if cmd == ".fy-add":
            if is_group:
                if args and args[0]:
                    mem_arg = args[0].strip()
                    # 支持id或@username
                    if mem_arg.isdigit():
                        mem_id = mem_arg
                        try:
                            entity = await event.client.get_entity(int(mem_id))
                            if getattr(entity, "username", None):
                                username = f"@{entity.username}"
                            elif getattr(entity, "first_name", None):
                                username = entity.first_name
                            else:
                                username = str(entity.id)
                        except Exception:
                            username = "none"
                    else:
                        uname = mem_arg.lstrip("@")
                        try:
                            entity = await event.client.get_entity(uname)
                            mem_id = str(entity.id)
                            if getattr(entity, "username", None):
                                username = f"@{entity.username}"
                            elif getattr(entity, "first_name", None):
                                username = entity.first_name
                            else:
                                username = str(entity.id)
                        except Exception:
                            await send_ephemeral_reply(event, f"未找到成员 {mem_arg}，请检查用户名或id是否正确。")
                            return
                    # 默认开启中英互译
                    if len(args) == 1:
                        src, tgt = ["zh", "en"], ["en", "zh"]
                    elif len(args) == 3:
                        src, tgt = parse_langs(args[1]), parse_langs(args[2])
                    elif len(args) == 2:
                        src, tgt = parse_langs(args[1]), ["zh"]
                    else:
                        await send_ephemeral_reply(event, "参数格式: .fy-add,成员id/@用户名 或 .fy-add,成员id/@用户名,源,目标")
                        return
                    if not validate_langs(src) or not validate_langs(tgt):
                        await send_ephemeral_reply(event, "语言代码不合法，请检查输入。")
                        return
                    saved_rules = []
                    for s in src:
                        filtered_tgts = [t for t in tgt if t != s]
                        if filtered_tgts:
                            set_rule(chat_id, mem_id, {"source_langs": [s], "target_langs": filtered_tgts}, username=username)
                            saved_rules.append(f"{s}→{'|'.join(filtered_tgts)}")
                    if saved_rules:
                        # 规则内容优先用指令参数，否则用配置文件默认other规则
                        if len(args) == 3:
                            rule_desc = f"{args[1]}→{args[2]}"
                        elif len(args) == 2:
                            rule_desc = f"{args[1]}→zh"
                        else:
                            rule_desc = config.get("other_default_rule", "zh|en,en|zh")
                        await send_ephemeral_reply(
                            event, f"已为群组成员{mem_id}({username})启用翻译，规则为：{rule_desc}"
                        )
                    else:
                        await send_ephemeral_reply(
                            event, f"源语言和目标语言完全重叠，无需翻译，规则未保存。"
                        )
                    return
                else:
                    await send_ephemeral_reply(event, "请指定成员id或@用户名作为第一个参数，如.fy-add,12345,zh,en 或 .fy-add,@user,zh,en")
                    return
            elif is_private:
                if not args:
                    src, tgt = ["zh", "en"], ["en", "zh"]
                elif len(args) == 2:
                    src, tgt = parse_langs(args[0]), parse_langs(args[1])
                elif len(args) == 1:
                    src, tgt = parse_langs(args[0]), ["zh"]
                else:
                    await send_ephemeral_reply(event, "参数格式: .fy-add 或 .fy-add,源,目标")
                    return
                if not validate_langs(src) or not validate_langs(tgt):
                    await send_ephemeral_reply(event, "语言代码不合法，请检查输入。")
                    return
                saved_rules = []
                for s in src:
                    filtered_tgts = [t for t in tgt if t != s]
                    if filtered_tgts:
                        set_rule(chat_id, chat_id, {"source_langs": [s], "target_langs": filtered_tgts})
                        saved_rules.append(f"{s}→{'|'.join(filtered_tgts)}")
                if saved_rules:
                    # 规则内容优先用指令参数，否则用配置文件默认other规则
                    if len(args) == 2:
                        rule_desc = f"{args[0]}→{args[1]}"
                    elif len(args) == 1:
                        rule_desc = f"{args[0]}→zh"
                    else:
                        rule_desc = config.get("other_default_rule", "zh|en,en|zh")
                    await send_ephemeral_reply(
                        event, f"已为对方（此私聊）启用翻译，规则为：{rule_desc}"
                    )
                else:
                    await send_ephemeral_reply(
                        event, "源语言和目标语言完全重叠，无需翻译，规则未保存。"
                    )
                return
            else:
                await send_ephemeral_reply(event, "请在私聊或群聊中使用 .fy-add")
                return

        if cmd == ".fy-del":
            if is_group:
                if not args:
                    if chat_id in rules:
                        rules[chat_id] = {}
                        save_json_config(rules_path, rules)
                        await send_ephemeral_reply(event, "已移除当前群所有成员翻译规则")
                    else:
                        await send_ephemeral_reply(event, "本群无可删除成员翻译规则")
                    return
                if args and args[0]:
                    mem_arg = args[0].strip()
                    mem_ids = set()
                    mem_usernames = set()
                    if mem_arg.isdigit():
                        mem_ids.add(mem_arg)
                    else:
                        uname = mem_arg.lstrip("@")
                        try:
                            entity = await event.client.get_entity(uname)
                            mem_ids.add(str(entity.id))
                            if getattr(entity, "username", None):
                                mem_usernames.add(f"@{entity.username}")
                        except Exception:
                            await send_ephemeral_reply(event, f"未找到成员 {mem_arg}，请检查用户名或id是否正确。")
                            return
                    # 支持 .fy-del,成员id,src,tgt 及 * 通配符批量删除
                    src = parse_langs(args[1]) if len(args) >= 2 else []
                    tgt = parse_langs(args[2]) if len(args) >= 3 else []
                    # 遍历 chat_id 下所有 user_id，匹配 id 或 username
                    found = False
                    to_remove = []
                    for uid, rule_obj in list(rules.get(str(chat_id), {}).items()):
                        # 匹配 id
                        if uid in mem_ids:
                            matched = True
                        else:
                            # 匹配 username
                            matched = False
                            rule_list = rule_obj if isinstance(rule_obj, list) else [rule_obj]
                            for rule in rule_list:
                                uname = rule.get("username", "")
                                if uname in mem_usernames:
                                    matched = True
                                    break
                        if not matched:
                            continue
                        # 删除规则
                        rule_list = rule_obj if isinstance(rule_obj, list) else [rule_obj]
                        new_rules = []
                        src_is_any = len(src) == 1 and src[0] == "*"
                        tgt_is_any = len(tgt) == 1 and tgt[0] == "*"
                        if src and tgt:
                            to_del = set()
                            for s in src:
                                for t in tgt:
                                    if s == t and s != "*":
                                        continue
                                    to_del.add((s, t))
                            for rule in rule_list:
                                rule_src = rule.get("source_langs", [])
                                rule_tgt = rule.get("target_langs", [])
                                s = rule_src[0] if rule_src else ""
                                t = rule_tgt[0] if rule_tgt else ""
                                match_src = src_is_any or s in src
                                match_tgt = tgt_is_any or t in tgt
                                if match_src and match_tgt:
                                    continue  # 删除
                                new_rules.append(rule)
                        else:
                            for rule in rule_list:
                                rule_src = set(rule.get("source_langs", []))
                                rule_tgt = set(rule.get("target_langs", []))
                                src_match = src_is_any or not src or any(s in rule_src for s in src)
                                tgt_match = tgt_is_any or not tgt or any(t in rule_tgt for t in tgt)
                                if not (src_match and tgt_match):
                                    new_rules.append(rule)
                        if not new_rules:
                            to_remove.append(uid)
                        else:
                            rules[str(chat_id)][uid] = new_rules
                        found = True
                    for uid in to_remove:
                        remove_rule(chat_id, uid)
                    if found:
                        await send_ephemeral_reply(event, f"已移除成员{mem_arg}在本群的指定源/目标语言翻译规则。")
                    else:
                        await send_ephemeral_reply(event, f"成员{mem_arg}在本群无可删除的翻译规则")
                    return
                await send_ephemeral_reply(event, ".fy-del,成员id/@用户名：指定成员，或无参数删除全群成员规则")
                return
            elif is_private:
                if not args:
                    remove_rule(chat_id, chat_id)
                    await send_ephemeral_reply(event, "已移除对方翻译规则")
                else:
                    # 支持私聊下 .fy-del,src,tgt 拆分删除
                    src = parse_langs(args[0]) if len(args) >= 1 else []
                    tgt = parse_langs(args[1]) if len(args) >= 2 else []
                    rule_raw = get_rule(chat_id, chat_id)
                    if not rule_raw:
                        await send_ephemeral_reply(event, "当前无可删除的翻译规则。")
                        return
                    rule_list = rule_raw if isinstance(rule_raw, list) else [rule_raw]
                    new_rules = []
                    # 支持 * 作为通配符
                    src_is_any = len(src) == 1 and src[0] == "*"
                    tgt_is_any = len(tgt) == 1 and tgt[0] == "*"
                    if src and tgt:
                        to_del = set()
                        for s in src:
                            for t in tgt:
                                if s == t and s != "*":
                                    continue
                                to_del.add((s, t))
                        for rule in rule_list:
                            rule_src = rule.get("source_langs", [])
                            rule_tgt = rule.get("target_langs", [])
                            s = rule_src[0] if rule_src else ""
                            t = rule_tgt[0] if rule_tgt else ""
                            match_src = src_is_any or s in src
                            match_tgt = tgt_is_any or t in tgt
                            if match_src and match_tgt:
                                continue  # 删除
                            new_rules.append(rule)
                    else:
                        for rule in rule_list:
                            rule_src = set(rule.get("source_langs", []))
                            rule_tgt = set(rule.get("target_langs", []))
                            src_match = src_is_any or not src or any(s in rule_src for s in src)
                            tgt_match = tgt_is_any or not tgt or any(t in rule_tgt for t in tgt)
                            if not (src_match and tgt_match):
                                new_rules.append(rule)
                    if not new_rules:
                        remove_rule(chat_id, chat_id)
                        await send_ephemeral_reply(event, f"已移除指定源/目标语言的翻译规则。")
                    else:
                        rules[str(chat_id)][str(chat_id)] = new_rules
                        save_json_config(rules_path, rules)
                        await send_ephemeral_reply(event, f"已移除部分规则，其余规则仍保留。")
                return
            return

    except Exception as e:
        logging.error(f"命令处理异常: {e}")
        await event.reply(f"命令处理异常: {e}")

# ========== 异步OpenAI调用（aiohttp实现，兼容第三方聚合服务） ==========
async def async_openai_chat(text, target_language, api_key, base_url, model="gpt-4o"):
    # 修正base_url拼接，避免重复/v1
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    url = url + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a translation engine, only returning translated answers."},
            {"role": "user", "content": f"Translate the text to {target_language} please do not explain my original text, do not explain the translation results, Do not explain the context.:\n{text}"}
        ]
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                raise Exception(f"OpenAI接口 {url} 状态码: {resp.status}")

# ========== Deeplx/OpenAI 顺序轮询+禁用+健康检查机制 ==========
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
                async with session.post(base_url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('code') == 200:
                            deeplx_fail_count[idx] = 0
                            # 健壮性：data['data'] 可能为 None
                            if data.get('data') is None or data.get('data') == "":
                                raise Exception(f"Deeplx接口 {base_url} 返回空结果")
                            return data['data']
                        else:
                            deeplx_fail_count[idx] += 1
                            logging.warning(f"Deeplx接口 {base_url} 返回异常: {data}")
                            raise Exception(f"Deeplx接口 {base_url} 返回异常: {data}")
                    else:
                        deeplx_fail_count[idx] += 1
                        logging.warning(f"Deeplx接口 {base_url} 失败，状态码: {resp.status}")
                        raise Exception(f"Deeplx接口 {base_url} 失败，状态码: {resp.status}")
        except Exception as e:
            deeplx_fail_count[idx] += 1
            logging.error(f"Deeplx接口 {base_url} 网络请求异常: {e}", exc_info=True)
        if deeplx_fail_count[idx] >= DEEPLX_FAIL_THRESHOLD:
            deeplx_disabled.add(idx)
            logging.error(f"已禁用第{idx+1}个deeplx接口: {base_url}，连续失败{DEEPLX_FAIL_THRESHOLD}次，请及时检查或更新！")
        tried += 1
    raise Exception("所有Deeplx接口均已禁用或不可用")

async def translate_with_openai(text, target_language):
    global current_openai_idx
    openai_cfg = config['openai']
    urls = openai_cfg.get('base_urls', [])
    keys = openai_cfg.get('api_keys', [])
    models = openai_cfg.get('models', ['gpt-4o'])
    min_count = min(len(urls), len(keys))
    if min_count == 0:
        raise Exception("openai base_urls 或 api_keys 未配置")
    tried = 0
    while tried < min_count:
        idx = current_openai_idx % min_count
        current_openai_idx = (current_openai_idx + 1) % min_count
        if idx in openai_disabled:
            tried += 1
            continue
        try:
            result = await async_openai_chat(text, target_language, keys[idx], urls[idx], model=models[0])
            openai_fail_count[idx] = 0
            return result
        except Exception as e:
            openai_fail_count[idx] += 1
            logging.warning(f"OpenAI接口 {urls[idx]}+key[{idx}]调用失败: {e}", exc_info=True)
            if openai_fail_count[idx] >= OPENAI_FAIL_THRESHOLD:
                openai_disabled.add(idx)
                logging.error(f"已禁用第{idx+1}个OpenAI接口: {urls[idx]}，key序号: {idx}，连续失败{OPENAI_FAIL_THRESHOLD}次，请及时检查或更新！")
            tried += 1
            continue
    raise Exception("所有OpenAI接口均已禁用或不可用")

# ========== 健康检查任务（全局只启动一次） ==========
async def disabled_engine_health_check_loop(interval=600):
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
                models = openai_cfg.get('models', ['gpt-4o'])
                min_count = min(len(urls), len(keys))
                for idx in list(openai_disabled):
                    if idx >= min_count:
                        continue
                    try:
                        result = await async_openai_chat("hello", "zh", keys[idx], urls[idx], model=models[0])
                        if result:
                            openai_disabled.remove(idx)
                            openai_fail_count[idx] = 0
                            logging.info(f"[HEALTH] OpenAI接口已恢复: 第{idx+1}个 {urls[idx]}，key序号: {idx}")
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[HEALTH] OpenAI禁用检测异常: {e}")
        await asyncio.sleep(interval)

# ========== 翻译主流程 ==========
async def translate_text(text, source_lang, target_langs, prefer=None):
    if prefer is None:
        prefer = config.get('default_translate_source', 'deeplx')
    backup = 'deeplx' if prefer == 'openai' else 'openai'
    result = {}
    semaphore = asyncio.Semaphore(5)

    async def translate_one(lang, engine):
        await semaphore.acquire()
        try:
            if engine == 'deeplx':
                return lang, await translate_with_deeplx(text, source_lang, lang)
            else:
                return lang, await translate_with_openai(text, lang)
        except Exception as e:
            logging.warning(f"{engine}翻译{lang}失败: {e}")
            return lang, None
        finally:
            semaphore.release()

    # 主引擎并发
    tasks = [translate_one(lang, prefer) for lang in target_langs]
    results = await asyncio.gather(*tasks)
    for lang, val in results:
        result[lang] = val

    all_primary_failed = all(result.get(lang) is None for lang in target_langs)
    if all_primary_failed:
        # 备用引擎并发
        tasks = [translate_one(lang, backup) for lang in target_langs]
        results = await asyncio.gather(*tasks)
        for lang, val in results:
            if val is not None:
                result[lang] = val
            else:
                result[lang] = f"[翻译失败]{backup}接口异常"
    return {k: v for k, v in result.items() if v is not None and v != ""}

# ========== 语言检测与消息处理（增强版） ==========

def get_language_proportions(text: str) -> dict:
    """计算文本中主要语言字符的比例"""
    proportions = {}
    clean_text = ''.join(filter(str.isalnum, text))
    if not clean_text:
        return {}
    total_len = len(clean_text)
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', clean_text))
    english_chars = len(re.findall(r'[a-zA-Z]', clean_text))
    japanese_chars = len(re.findall(r'[\u3040-\u30ff]', clean_text))
    korean_chars = len(re.findall(r'[\uac00-\ud7af]', clean_text))
    if chinese_chars > 0:
        proportions['zh'] = chinese_chars / total_len
    if english_chars > 0:
        proportions['en'] = english_chars / total_len
    if japanese_chars > 0:
        proportions['ja'] = japanese_chars / total_len
    if korean_chars > 0:
        proportions['ko'] = korean_chars / total_len
    return proportions

def contains_chinese(text):
    for character in text:
        if '\u4e00' <= character <= '\u9fff':
            return True
    return False

import unicodedata
import string

def remove_emoji_and_punct(text):
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE,
    )
    text = emoji_pattern.sub(r'', text)
    # 标点替换为空格（包括中英文标点）
    new_text = []
    for ch in text:
        if ch in string.punctuation or unicodedata.category(ch).startswith('P'):
            new_text.append(' ')
        else:
            new_text.append(ch)
    return ''.join(new_text)

def contains_non_chinese(text):
    clean = remove_emoji_and_punct(text)
    if re.search(r'[A-Za-z]', clean):
        return True
    if re.search(r'[\u0600-\u06FF]', clean):
        return True
    if re.search(r'[\u0900-\u097F]', clean):
        return True
    if re.search(r'[\u0400-\u04FF]', clean):
        return True
    if re.search(r'[\uAC00-\uD7AF]', clean):
        return True
    if re.search(r'[\u3040-\u30FF]', clean):
        return True
    return False

def is_pure_url(text):
    url_pattern = r'^\s*http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+\s*$'
    return re.match(url_pattern, text) is not None

def count_lang_keywords(text):
    # 高频词表，可根据需要扩展
    lang_keywords = {
        "fr": ["pas", "est", "le", "la", "un", "une", "je", "tu", "vous", "nous", "avec", "pour", "mais", "sur", "dans", "des", "du", "au", "aux", "ce", "cette", "ces", "mon", "ton", "son", "leur", "qui", "que", "quoi", "où", "comment", "parce", "bien", "mal", "très", "plus", "moins", "aussi", "comme", "si", "non", "oui"],
        "en": ["the", "is", "are", "you", "he", "she", "it", "and", "but", "not", "with", "for", "on", "in", "at", "by", "to", "of", "from", "as", "that", "this", "these", "those", "my", "your", "his", "her", "their", "who", "what", "where", "how", "because", "very", "well", "bad", "good", "no", "yes"],
        "de": ["nicht", "und", "ist", "ich", "du", "sie", "wir", "ihr", "mein", "dein", "sein", "ihr", "unser", "euer", "kein", "ja", "nein", "bitte", "danke", "gut", "schlecht", "sehr", "auch", "aber", "oder", "wenn", "weil", "was", "wer", "wie", "wo", "warum", "dass", "dies", "das", "ein", "eine", "mit", "für", "auf", "im", "am", "aus", "bei", "nach", "vor", "über", "unter", "zwischen"],
        "es": ["no", "sí", "pero", "muy", "también", "como", "más", "menos", "por", "para", "con", "sin", "sobre", "entre", "cuando", "porque", "qué", "quién", "dónde", "cómo", "cuándo", "yo", "tú", "él", "ella", "nosotros", "vosotros", "ellos", "ellas", "mi", "tu", "su", "nuestro", "vuestro", "este", "ese", "aquel", "uno", "una", "unos", "unas"],
        "it": ["non", "sì", "ma", "molto", "anche", "come", "più", "meno", "per", "con", "senza", "su", "tra", "quando", "perché", "che", "chi", "dove", "come", "quando", "io", "tu", "lui", "lei", "noi", "voi", "loro", "mio", "tuo", "suo", "nostro", "vostro", "questo", "quello", "uno", "una", "alcuni", "alcune"],
        "pt": ["não", "sim", "mas", "muito", "também", "como", "mais", "menos", "por", "para", "com", "sem", "sobre", "entre", "quando", "porque", "que", "quem", "onde", "como", "quando", "eu", "tu", "ele", "ela", "nós", "vós", "eles", "elas", "meu", "teu", "seu", "nosso", "vosso", "este", "esse", "aquele", "um", "uma", "uns", "umas"],
        "nl": ["niet", "en", "is", "ik", "jij", "hij", "zij", "wij", "jullie", "mijn", "jouw", "zijn", "haar", "ons", "onze", "geen", "ja", "nee", "alstublieft", "dank", "goed", "slecht", "zeer", "也", "maar", "of", "als", "omdat", "wat", "wie", "hoe", "waar", "waarom", "dat", "deze", "dit", "een", "met", "voor", "op", "in", "uit", "bij", "naar", "voor", "over", "onder", "tussen"]
    }
    text_lower = text.lower()
    lang_hit_count = {}
    for lang, keywords in lang_keywords.items():
        count = 0
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text_lower):
                count += 1
        lang_hit_count[lang] = count
    return lang_hit_count

# ========== fasttext 语言识别 ==========
try:
    import fasttext
    _fasttext_model = None
    if os.path.exists(fasttext_model_path):
        _fasttext_model = fasttext.load_model(fasttext_model_path)
    else:
        print(f"[INFO] fastText 语言识别模型 {fasttext_model_path} 未找到，正在自动下载...")
        try:
            import requests
            url = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                with open(fasttext_model_path, 'wb') as f:
                    downloaded = 0
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            done = int(50 * downloaded / total) if total else 0
                            print(f"\r[下载进度] [{'#' * done}{'.' * (50 - done)}] {downloaded // 1024}KB/{total // 1024 if total else '?'}KB", end='')
                print("\n[INFO] lid.176.bin 下载完成。")
            _fasttext_model = fasttext.load_model(fasttext_model_path)
        except Exception as e:
            print(f"[WARN] fastText 语言识别模型自动下载失败: {e}")
            print(f"[WARN] 请手动下载 https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin 并放到脚本目录。")
except ImportError:
    _fasttext_model = None
    print("[WARN] 未安装 fasttext，语言识别将回退为字符区间法。")
except Exception as e:
    _fasttext_model = None
    print(f"[WARN] fastText 加载异常: {e}")

def detect_language(text, source_langs=None):
    # 0. 分句主导语言投票
    def phrase_main_lang(phrase):
        zh_count = len(re.findall(r'[\u4e00-\u9fff]', phrase))
        en_count = len(re.findall(r'[a-zA-Z]', phrase))
        en_words = re.findall(r'[a-zA-Z]+', phrase)
        # 判断是否为完整英文句子（以大写字母开头，单词数>=4，结尾有标点）
        is_full_en_sentence = (
            len(en_words) >= 4 and
            re.match(r'^[A-Z]', phrase.strip()) and
            re.search(r'[.!?]$', phrase.strip())
        )
        if is_full_en_sentence:
            return 'en_full'
        if zh_count > en_count:
            return 'zh'
        elif en_count > zh_count:
            return 'en'
        else:
            return None

    split_phrases = re.split(r'[，,。！？!?.；;、\s]+', text)
    phrase_langs = [phrase_main_lang(p) for p in split_phrases if p.strip()]
    if len(phrase_langs) >= 2:
        zh_votes = phrase_langs.count('zh')
        en_votes = phrase_langs.count('en')
        en_full_votes = phrase_langs.count('en_full')
        # 英文完整句权重最高
        if en_full_votes > 0 and en_full_votes >= zh_votes:
            logging.info(f"[DEBUG] 分句主导语言投票: en_full_votes={en_full_votes}, zh_votes={zh_votes}，整体判定为 'en'")
            return 'en'
        if zh_votes > en_votes and zh_votes >= 1:
            logging.info(f"[DEBUG] 分句主导语言投票: zh_votes={zh_votes}, en_votes={en_votes}，整体判定为 'zh'")
            return 'zh'
        if en_votes > zh_votes and en_votes >= 1:
            logging.info(f"[DEBUG] 分句主导语言投票: zh_votes={zh_votes}, en_votes={en_votes}，整体判定为 'en'")
            return 'en'
    # 0. 特殊短文本和外来词优先判中文
    external_words = {"nice", "ok", "good", "cool", "yes", "no", "hi", "hello", "bye", "sorry", "thanks", "thankyou"}
    text_stripped = text.strip().lower()
    chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_words = re.findall(r'[a-zA-Z]+', text)
    # 若短文本且含中文，优先判为中文
    if len(text_stripped) <= 6 and chinese_count > 0:
        logging.info(f"[DEBUG] 短文本含中文，优先判定为 'zh'")
        return 'zh'
    # 若含中文且英文部分为外来词，优先判为中文
    if chinese_count > 0 and all(w.lower() in external_words for w in english_words if w):
        logging.info(f"[DEBUG] 中英混杂且英文为外来词，优先判定为 'zh'")
        return 'zh'

    # 1. 综合比例与结构判定
    proportions = get_language_proportions(text)
    en_prop = proportions.get('en', 0)
    zh_prop = proportions.get('zh', 0)
    en_word_count = len([w for w in english_words if w])
    # 英文主导判定：英文比例>0.5，且中文字符数<=2，且英文单词数>=2
    if en_prop > 0.5 and chinese_count <= 2 and en_word_count >= 2:
        logging.info(f"[DEBUG] 英文主导混杂，判定为 'en' (en_prop={en_prop:.2%}, zh_count={chinese_count}, en_words={en_word_count})")
        return 'en'
    # 中文主导判定
    if zh_prop > 0.4:
        logging.info(f"[DEBUG] 字符比例判定: 中文占比 {zh_prop:.2%}，判定为 'zh'")
        return 'zh'
    if proportions.get('ja', 0) > 0.4:
        logging.info(f"[DEBUG] 字符比例判定: 日文占比 {proportions['ja']:.2%}，判定为 'ja'")
        return 'ja'
    if proportions.get('ko', 0) > 0.4:
        logging.info(f"[DEBUG] 字符比例判定: 韩文占比 {proportions['ko']:.2%}，判定为 'ko'") 
        return 'ko'
    if en_prop > 0.6:
        logging.info(f"[DEBUG] 字符比例判定: 英文占比 {en_prop:.2%}，判定为 'en'")
        return 'en'

    # 2. 高频词法
    lang_hits = count_lang_keywords(text)
    if lang_hits:
        max_count = max(lang_hits.values())
        if max_count > 0:
            candidates = [lang for lang, cnt in lang_hits.items() if cnt == max_count]
            if len(candidates) == 1:
                logging.info(f"[DEBUG] 高频词法判定: {candidates[0]} 命中数={max_count}")
                return candidates[0]

    # 3. fastText
    ft_cfg = config.get('fasttext', {})
    enabled = ft_cfg.get('enabled', True)
    threshold = float(ft_cfg.get('confidence_threshold', 0.8))
    fallback_enabled = ft_cfg.get('fallback_enabled', True)
    pure_text = remove_emoji_and_punct(text)
    if enabled and _fasttext_model and pure_text:
        try:
            pred = _fasttext_model.predict(pure_text.replace("\n", " ")[:512])
            lang = pred[0][0].replace("__label__", "")
            prob = float(pred[1][0]) if pred and len(pred) > 1 and len(pred[1]) > 0 else 0.0
            logging.info(f"[DEBUG] fastText.predict('{pure_text[:32]}...')={pred}")
            if prob >= threshold:
                return lang
            # 混合主导性再判定
            if lang == 'en' and prob < 0.9 and proportions.get('zh', 0) > 0.1:
                logging.info(f"[DEBUG] fastText 判定为 'en' 但置信度低 ({prob:.2f}) 且含中文，修正为 'zh'")
                return 'zh'
        except Exception as e:
            print(f"[WARN] fastText 识别异常: {e}")

    # 4. fallback
    if fallback_enabled:
        logging.info(f"[DEBUG] fallback: 正在用区间法检测 pure_text='{pure_text}'")
        # 中英混杂都明显，双向
        if proportions.get('zh', 0) > 0.2 and proportions.get('en', 0) > 0.2:
            logging.info(f"[DEBUG] fallback: 中英混杂明显，返回 ['zh', 'en'] 作为源语言")
            return ['zh', 'en']
        if proportions.get('zh', 0) > proportions.get('en', 0):
            return 'zh'
        if proportions.get('en', 0) > proportions.get('zh', 0):
            return 'en'
        if re.search(r'[\u4e00-\u9fff]', text):
            return 'zh'
        elif re.search(r'[\u0400-\u04FF]', text):
            return 'ru'
        elif re.search(r'[\u3040-\u30FF]', text):
            return 'ja'
        elif re.search(r'[\uAC00-\uD7AF]', text):
            return 'ko'
        elif re.search(r'[\u0600-\u06FF]', text):
            return 'ar'
        elif re.search(r'[A-Za-zÀ-ÿ]', text) and not re.search(r'[\u4e00-\u9fff\u0400-\u04FF\u3040-\u30FF\uAC00-\uD7AF\u0600-\u06FF]', text):
            return 'en'
    return 'unknown'

@client.on(events.NewMessage)
async def handle_message(event):
    if not event.message or not event.message.text:
        return
    try:
        if getattr(event.message, "deleted", False):
            return
    except Exception:
        return
    text = event.message.text.strip()
    if ".fy-" in text and contains_chinese(text):
        logging.info(f"[DEBUG] .fy-指令+中文命中，跳过翻译: '{text}'")
        return
    if should_ignore(text):
        return
    if text.startswith('.fy-'):
        return
    if is_pure_url(text):
        return

    group_id = str(event.chat_id)
    user_id = str(event.sender_id)
    rule_raw = get_rule(group_id, user_id)
    logging.info(f"[DEBUG] handle_message in chat_id={group_id}, user_id={user_id}, rule_found={bool(rule_raw)}")
    if not rule_raw:
        return

    rule_list = rule_raw if isinstance(rule_raw, list) else [rule_raw]
    prefer = config.get('default_translate_source', 'deeplx')
    lang_map = {
        "en": "英语", "zh": "中文", "fr": "法语", "de": "德语", "ru": "俄语", "ja": "日语", "ko": "韩语", "ar": "阿拉伯语",
        "hi": "印地语", "tr": "土耳其语", "fa": "波斯语", "uk": "乌克兰语", "es": "西班牙语", "it": "意大利语", "rm": "罗曼什语",
        "pt": "葡萄牙语", "pl": "波兰语", "nl": "荷兰语", "sv": "瑞典语", "ro": "罗马尼亚语", "cs": "捷克语", "el": "希腊语",
        "da": "丹麦语", "fi": "芬兰语", "hu": "匈牙利语", "he": "希伯来语", "bg": "保加利亚语", "sr": "塞尔维亚语",
        "hr": "克罗地亚语", "sk": "斯洛伐克语", "sl": "斯洛文尼亚语", "no": "挪威语"
    }

    # 1. 先判定主语种
    detected_lang = detect_language(text)
    logging.info(f"[DEBUG] 主语种判定: detected_lang={detected_lang}")

    # 2. 用主语种匹配所有规则，收集所有目标语言
    all_targets = set()
    detected_langs = detected_lang if isinstance(detected_lang, list) else [detected_lang]
    for dlang in detected_langs:
        for rule in rule_list:
            source_langs = rule.get('source_langs', ['en'])
            target_langs = rule.get('target_langs', ['zh'])
            # 只匹配主语种
            if dlang in source_langs or (dlang == "zh" and "zh" in source_langs and contains_chinese(text)):
                for lang in target_langs:
                    if lang != dlang:
                        # 跳过目标为zh且原文为中文
                        if lang == "zh" and contains_chinese(text):
                            continue
                        all_targets.add((dlang, lang))
    if not all_targets:
        logging.info(f"[DEBUG] 未找到匹配的规则或目标语言，跳过翻译")
        return

    # 3. 一次性并发翻译
    try:
        start_time = time.time()
        # 按源语言分组目标语言
        src2tgts = {}
        for src, tgt in all_targets:
            src2tgts.setdefault(src, set()).add(tgt)
        reply_text = ""
        for src, tgts in src2tgts.items():
            translated = await translate_text(text, src, list(tgts), prefer=prefer)
            for lang in tgts:
                reply = translated.get(lang, "")
                if not reply or reply.strip() == text.strip():
                    logging.warning(f"[DEBUG] {src}->{lang} 翻译失败或与原文一致，未输出")
                    continue
                lang_name = lang_map.get(lang, lang)
                reply_text += f"{src}->{lang}：`{reply}`\n"
        if reply_text:
            try:
                await event.reply(reply_text.strip())
            except Exception as e:
                logging.error(f"Reply error (chat_id={group_id}, user_id={user_id}): {type(e).__name__} - {e}")
    except Exception as e:
        logging.error(f"消息翻译异常: {e}")

import sys

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
    # 确保全局变量初始化
    config = load_yaml_config('config.yaml')
    self_default_rule = str(config.get("self_default_rule", "zh,en"))
    other_default_rule = str(config.get("other_default_rule", "en,zh"))
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
        # 启动全局健康检查任务
        loop.create_task(disabled_engine_health_check_loop(interval=600))
        client.start()
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
            time.sleep(0.5)
        except Exception as e:
            print(f"[WARN] 断开连接出错: {e}")
            logging.warning(f"断开连接出错: {e}", exc_info=True)
        sys.exit(0)
