# tg-autotranslate
借助GPT融合了linux.do论坛里的两个大佬分享的脚本，用于自学自用，请轻喷~
`https://linux.do/t/topic/18308` 和 `https://linux.do/t/topic/9582`
把py文件和yaml配置文件放同目录，根据说明，填写完整，运行py脚本即可
使用前可能需要安装依赖：`pip install aiohttp telethon` ,如果你本机还缺其他依赖，自行安装即可。

指令帮助：
- .fy-on 开启私聊和群聊中自己默认中译英；
- .fy-off 关闭对自己群聊中消息翻译功能，如果在私聊中，则同时关闭对双方消息的翻译；
- .fy-on,fr,zh 指定自己私聊和群聊中法译中；
- .fy-add 私聊为对方开默认英译中；
- .fy-add,zh|ru,en|fr 私聊为对方开中或者法语译英、法；
- .fy-add,成员id （群组）开启默认英译中，私聊中开启对方默认英译中；
- .fy-add,成员id,源语言,目标语言 （群组）为指定成员开启方向翻译；
- .fy-del,成员id 群组中移除成员规则；
- .fy-clear 管理员一键清除所有翻译规则；
- .fy-help 查看指令与用法说明。
- 关于群成员id查询，可以借助bncr无界的脚本功能实现
