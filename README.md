# tg-autotranslate
借助GPT融合了linux.do论坛里的两个大佬分享的脚本，用于自学自用，请轻喷~
使用gpt干活前，参考的教程：
`https://linux.do/t/topic/18308` 和 `https://linux.do/t/topic/9582`
### 1、准备工作：
#### 1.1创建文件夹，并进入文件夹，把py文件和yaml配置文件拉下来，
```
mkdir -p /root/tg-autotranslate
```
```
cd /root/tg-autotranslate
```

```
git clone https://github.com/fsyllkn/tg-autotranslate.git
```
#### 1.2编辑config.yaml配置文件，填写tg相关信息、deeplx的URL（支持linux.do的码子，格式见配置文件）、OpenAI的apikey和URL（支持中转key）
```
nano config.yaml
或者
vi config.yaml
```
# 附：配置文件说明（待详细说明。参考配置文件内注释）
* 
<details>
<summary><code><strong>「 点击展开 查看配置文件 」</strong></code></summary>

****
## 1.根据配置文件的描述填写即可

### 1.1 tg的id、hash、api_id
### 1.2 linux.do的码子、openai的的中转URL和apikey
## 2、默认翻译规则填写（默认也可）
### 2.1尽可能支持
</details>

****

#### 1.3安装依赖：`pip install aiohttp telethon` ,如果你的机器还缺其他依赖，自行安装即可。

### 2、测试运行程序（注意cd进入脚本所在的文件夹）
```
python3 tg-autotranslate.py
```
#### 2.1首次运行会提示登录tg，根据提示登录:
1·需要tg的手机号码（需要输入+130178787878这样格式）；2·验证码（已登录设备上的tg接收）；3·密码（二步验证，如有设置）
![image](https://github.com/user-attachments/assets/c6f01d92-0f9e-46eb-9012-937708838a9b)

#### 2.2测试指令及翻译是否正常:
在tg里输入指令测试是否正常、是否有报错，支持id和用户名
指令帮助：
- `.fy-on` 私聊、群聊-开启对自己消息的翻译（规则默认）；
- `.fy-off` 私聊、群聊-关闭自己翻译功能；
- `.fy-on,fr|en,zh` 私聊、群聊-指定自己英或法译中；
- `.fy-add` 私聊-翻译对方消息（规则默认）；
- `.fy-del` 私聊-关闭翻译对方消息功能；
- `.fy-del` 群聊-关闭翻译所有成员消息功能；
- `.fy-add,zh|ru,en|fr` 私聊-为对方开中文、俄语译英、法；
- `.fy-add,成员id或用户名` 群聊-对某个成员英译中；
- `.fy-add,成员id或用户名,源语言,目标语言` 群聊-为指定成员开启方向翻译；
- `.fy-del,成员id或用户名` 群聊-关闭翻译指定成员消息功能；
- `.fy-add,成员id或用户名,源语言,目标语言` 群聊-为指定成员开启方向翻译；
- `.fy-del,成员id或用户名,*,ar|fr` 群聊-删除翻译指定成员消息部分规则功能；
- `.fy-clear` 一键清空所有翻译规则；
- `.fy-list` 查看用户开启翻译功能的规则；
- `.fy-help` 查看指令与用法说明。
  部分无用户名的，如果需要用户id查询，可以借助bncr无界的脚本功能实现

### 3、设置启动服务，方便开机启动和管理

#### 3.1查看python3路径
```
which python3
```
返回以下内容（如果返回的路径和3.2中ExecStart=/usr/bin/python3一致，继续，不一致，修改3.2一致后再写入）
```
/usr/bin/python3
```
#### 3.2根据自己的路径编辑后，直接在ssh里梭哈（注意路径要和自己前面创建的一致——WorkingDirectory=/root/tg-autotranslate、/root/tg-autotranslate/tg-autotranslate.py）
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
#### 3.3重新加载：
```
systemctl daemon-reload
```
#### 3.4立即启动并设置开机自启：
```
systemctl enable --now tg-autotranslate
```
或者
```
systemctl enable tg-autotranslate
systemctl start tg-autotranslate
```
#### 3.5检查服务状态
```
systemctl status tg-autotranslate
```





