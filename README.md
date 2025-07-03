# tg-autotranslate
借助GPT融合了linux.do论坛里的两个大佬分享的脚本，用于自学自用，请轻喷~
`https://linux.do/t/topic/18308` 和 `https://linux.do/t/topic/9582`
一、安装：
1、准备工作：
1.1创建文件夹，并进入文件夹，把py文件和yaml配置文件拉下来，
```
mkdir -p /root/tg-autotranslate
```
```
cd /root/tg-autotranslate
```

```
git clone 
```
1.2填写tg相关信息、deeplx的URL（支持linux.do的码子，格式见配置文件）、OpenAI的apikey和URL（支持中转key）

1.3安装依赖：`pip install aiohttp telethon` ,如果你的机器还缺其他依赖，自行安装即可。

2、运行程序
```
python3 tg-autotranslate.py
```
2.1登录tg，根据提示登录:
1·需要tg的手机号码（需要输入+130178787878这样格式）；2·验证码（已登录设备上的tg接收）；3·密码（二步验证，如有设置）
![image](https://github.com/user-attachments/assets/c6f01d92-0f9e-46eb-9012-937708838a9b)

2.2测试:
在tg里输入指令测试是否正常、是否有报错
指令帮助：
- `.fy-on` 开启私聊和群聊中自己默认中译英；
- `.fy-off` 关闭对自己群聊中消息翻译功能，如果在私聊中，则同时关闭对双方消息的翻译；
- `.fy-on,fr,zh` 指定自己私聊和群聊中法译中；
- `.fy-add` 私聊为对方开默认英译中；
- `.fy-add,zh|ru,en|fr` 私聊为对方开中或者法语译英、法；
- `.fy-add,成员id` 群组中开启对某个成员的英译中（默认）；
- `.fy-add,成员id,源语言,目标语言` 群组中为指定成员开启方向翻译；
- `.fy-del,成员id` 群组中移除成员规则；
- `.fy-clear` 管理员一键清除所有翻译规则；
- `.fy-help` 查看指令与用法说明。
- 关于群成员id查询，可以借助bncr无界的脚本功能实现

3、设置启动服务，方便开机启动和管理
使用systemd
3.1查看python3路径
```
which python3
```
返回以下内容（如果返回的路径和3.2中ExecStart=/usr/bin/python3一致，继续，不一致，修改3.2一致后再写入）
```
/usr/bin/python3
```
3.2写入内容（注意路径要和自己前面创建的一致——WorkingDirectory=/root/tg-autotranslate、/root/tg-autotranslate/tg-autotranslate.py）
```
sudo bash -c 'cat <<EOF > /etc/systemd/system/tg-autotranslate.service
[Unit]
Description=Translate Bot Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/tg-autotranslate
ExecStart=/usr/bin/python3 /root/tg-autotranslate/tg-autotranslate.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF'
```
3.3重新加载配置：
```
systemctl daemon-reload
```
3.4立即启动并设置开机自启：
```
systemctl enable --now tg-autotranslate.service
```
3.5检查服务状态
```
systemctl status tg-autotranslate.service
```





