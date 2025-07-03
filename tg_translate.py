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

# ======== 加载翻译方向规则配置 ==========
self_default_rule = str(config.get("self_default_rule", "zh,en"))
other_default_rule = str(config.get("other_default_rule", "en,zh"))
translate_rules = {str(k): v for k, v in config.get("translate_rules", {}).items()} if "translate_rules" in config else {}

# ========== 热加载定时器 ==========
def start_config_hot_reload(interval=60):
    def reload_loop():
        last_mtime = None
        config_path = os.path.abspath('config.yaml')
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
            time.sleep(interval)
    t = threading.Thread(target=reload_loop, daemon=True)
    t.start()

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

def should_ignore(text):
    # 完全匹配或以.开头的命令
    t = text.strip().lower()
    if t in ignore_words:
        return True
    for word in ignore_words:
        if word.startswith('.') and t.startswith(word):
            return True
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
    """发送指令回复，10秒后自动删除（指令消息和回复消息），并写日志"""
    try:
        rep_msg = await event.reply(reply_text)
        chat_id = event.chat_id
        operator_id = event.sender_id
        logging.info(f"[CMD-EPHEMERAL] user {operator_id} sent command: {event.text!r}, reply: {reply_text!r}")
        await asyncio.sleep(20)
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
                "- `.fy-on` 开启私聊和群聊中自己默认中译英；\n"
                "- `.fy-off` 关闭对自己群聊中消息翻译功能，如果在私聊中，则同时关闭对双方消息的翻译；\n"
                "- `.fy-on,fr,zh` 指定自己私聊和群聊中法译中；\n"
                "- `.fy-add` 私聊为对方开默认英译中；\n"
                "- `.fy-add,zh|ru,en|fr` 私聊为对方开中或者法语译英、法；\n"
                "- `.fy-add,成员id` （群组）开启默认英译中，私聊中开启对方默认英译中；\n"
                "- `.fy-add,成员id,源语言,目标语言` （群组）为指定成员开启方向翻译；\n"
                "- `.fy-del,成员id` 群组中移除成员规则；\n"
                "- `.fy-clear` 管理员一键清除所有翻译规则；\n"
                "- `.fy-help` 查看指令与用法说明。"
            )
            await send_ephemeral_reply(event, help_msg)
            return
        ## ========= .fy-on 开启自己翻译 ========= ##
        if cmd == ".fy-on":
            # 支持 .fy-on或 .fy-on,src,tgt/方向 任意场景，仅操作self
            argstr = ",".join(args) if args else ""
            rule_key = argstr if argstr else self_default_rule
            src, tgt = parse_rule_pair(rule_key, self_default_rule)
            set_rule(chat_id, self_id, {"source_langs": src, "target_langs": tgt})
            await send_ephemeral_reply(
                event,
                f"已为 [自己] 在本{'群组' if is_group else '私聊'}开启翻译：{src} → {tgt}"
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
                    set_rule(chat_id, mem_id, {"source_langs": src, "target_langs": tgt})
                    await send_ephemeral_reply(
                        event, f"已为群组成员{mem_id}启用翻译：{src} → {tgt}"
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
                set_rule(chat_id, chat_id, {"source_langs": src, "target_langs": tgt})
                await send_ephemeral_reply(
                    event, f"已为对方（此私聊）启用翻译：{src} → {tgt}"
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

async def translate_with_openai(text, target_language):
    """顺序对应轮询 OpenAI base_urls 与 api_keys"""
    import asyncio
    openai_cfg = config['openai']
    urls = openai_cfg.get('base_urls', [])
    keys = openai_cfg.get('api_keys', [])
    try:
        min_count = min(len(urls), len(keys))
        if min_count == 0:
            raise Exception("openai base_urls 或 api_keys 未配置")
        loop = asyncio.get_event_loop()
        for i in range(min_count):
            try:
                client = OpenAI(api_key=keys[i], base_url=urls[i])
                def do_translate():
                    try:
                        response = client.chat.completions.create(
                            model="gpt-4o",
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
                return result
            except Exception as e:
                logging.warning(f"OpenAI接口 SDK {urls[i]}+key[{i}]调用失败: {e}", exc_info=True)
                continue
        raise Exception("所有OpenAI接口均不可用")
    except Exception as e:
        logging.warning(f"OpenAI调用失败: {e}", exc_info=True)
        raise Exception(f"translate_with_openai 失败: {e}")

async def translate_with_deeplx(text, source_lang, target_lang):
    deeplx_cfg = config['deeplx']
    base_urls = deeplx_cfg.get('base_urls', [])
    for base_url in base_urls:
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
                                return data['data']
                            else:
                                logging.warning(f"Deeplx接口 {base_url} 返回异常: {data}")
                        else:
                            logging.warning(f"Deeplx接口 {base_url} 失败，状态码: {resp.status}")
                except Exception as e:
                    logging.error(f"Deeplx接口 {base_url} 网络请求异常: {e}", exc_info=True)
        except Exception as e:
            logging.warning(f"Deeplx接口 {base_url} 失败，尝试下一个: {e}", exc_info=True)
    raise Exception("所有Deeplx接口均不可用")

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
    return any('\u4e00' <= character <= '\u9fff' for character in text)

def contains_non_chinese(text):
    return any(character < '\u4e00' or character > '\u9fff' for character in text)

def is_pure_url(text):
    url_pattern = r'^\s*http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+\s*$'
    return re.match(url_pattern, text) is not None

@client.on(events.NewMessage)
async def handle_message(event):
    # 只处理非命令消息
    if not event.message or not event.message.text:
        return
    text = event.message.text.strip()
    if text.startswith('.fy-'):
        return  # 命令消息交由handle_command处理

    if should_ignore(text):
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
    reply_text = ""
    prefer = config.get('default_translate_source', 'deeplx')
    hit = False
    for rule in rule_list:
        source_langs = rule.get('source_langs', ['en'])
        target_langs = rule.get('target_langs', ['zh'])
        # 智能识别源语言，找到适合本条消息的语言组
        active_source = None
        for sl in source_langs:
            if sl == "zh" and contains_chinese(text):
                active_source = sl
                break
            elif sl != "zh" and contains_non_chinese(text):
                active_source = sl
                break
        if not active_source:
            continue
        try:
            start_time = time.time()
            translated = await translate_text(text, active_source, target_langs, prefer=prefer)
            logging.info(f"翻译耗时: {time.time() - start_time:.2f}s")
            for lang in target_langs:
                reply = translated.get(lang, "")
                if reply:
                    reply_text += f"[{lang}] {reply}\n"
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
    print("配置和规则加载完毕，主程序入口。")
    # 启动热加载定时器（每60秒自动reload一次config.yaml）
    start_config_hot_reload(interval=60)
    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(handle_async_exception)
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
