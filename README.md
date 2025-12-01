```
git clone https://github.com/penggan00/rss.git
# 安装
/bin/bash ~/rss/setup.sh
chmod +x ~/rss/{rss.sh,call.sh,usd.sh,mail.sh,rss2.sh,tt.sh}
chmod +x ~/rss/ss.sh
```
```
docker pull penggan0/rss-full:latest
```
```
docker pull penggan0/rss-full-alpine:latest
```
sudo docker-compose pull
sudo docker-compose down
sudo docker-compose up -d

```
#一键更新
cd
~/rss
bash -c "$(curl -fsSL https://penggan00.github.io/my-blog/sh/github.sh)"
```
crontab -e
24 2 * * * bash -c "$(curl -fsSL https://penggan00.github.io/my-blog/sh/github.sh)"
*/10 * * * * /bin/bash ~/rss/rss.sh
5,15,25,35,45,55 * * * * /bin/bash ~/rss/rss.sh
27 23 * * * /bin/bash ~/rss/rss.sh
22 2 * * * /bin/bash ~/rss/ss.sh
* * * * * /bin/bash ~/rss/vps.sh
```
pip install beautifulsoup4 html5lib html2text md2tgmd python-dotenv requests
pip install aiosqlite
pip install langdetect
apt install python3-venv
pip install html2text
pip install frontend
```
```
# 创建虚拟环境
python3 -m venv rss_venv
# 激活虚拟环境
source rss_venv/bin/activate
python3 rss.py
```
python3 usd.py
source rss_venv/bin/activate
#python3 mail.py
python3 gpt.py


```
#安装依赖
python3 -m pip install -r requirements.txt
```
```
# 生成依赖
pip freeze > requirements.txt
```
```
# 退出虚拟环境
deactivate
```
\d rss_status  -- 查看rss_status表的结构
SELECT * FROM rss_status LIMIT 5;

```
#  sql
CREATE DATABASE rss_status;
\c rss_status
``` 

**youtube**
```
/sub https://www.youtube.com/feeds/videos.xml?channel_id=UCvijahEyGtvMpmMHBu4FS2w https://www.youtube.com/feeds/videos.xml?channel_id=UC96OvMh0Mb_3NmuE8Dpu7Gg https://www.youtube.com/feeds/videos.xml?channel_id=UCQoagx4VHBw3HkAyzvKEEBA https://www.youtube.com/feeds/videos.xml?channel_id=UCbCCUH8S3yhlm7__rhxR2QQ https://www.youtube.com/feeds/videos.xml?channel_id=UCMtXiCoKFrc2ovAGc1eywDg https://www.youtube.com/feeds/videos.xml?channel_id=UCii04BCvYIdQvshrdNDAcww https://www.youtube.com/feeds/videos.xml?channel_id=UCJMEiNh1HvpopPU3n9vJsMQ https://www.youtube.com/feeds/videos.xml?channel_id=UCYjB6uufPeHSwuHs8wovLjg https://www.youtube.com/feeds/videos.xml?channel_id=UCSs4A6HYKmHA2MG_0z-F0xw https://www.youtube.com/feeds/videos.xml?channel_id=UCZDgXi7VpKhBJxsPuZcBpgA https://www.youtube.com/feeds/videos.xml?channel_id=UCxukdnZiXnTFvjF5B5dvJ5w https://www.youtube.com/feeds/videos.xml?channel_id=UCUfT9BAofYBKUTiEVrgYGZw https://www.youtube.com/feeds/videos.xml?channel_id=UC51FT5EeNPiiQzatlA2RlRA https://www.youtube.com/feeds/videos.xml?channel_id=UCDD8WJ7Il3zWBgEYBUtc9xQ https://www.youtube.com/feeds/videos.xml?channel_id=UCWurUlxgm7YJPPggDz9YJjw https://www.youtube.com/feeds/videos.xml?channel_id=UCvENMyIFurJi_SrnbnbyiZw https://www.youtube.com/feeds/videos.xml?channel_id=UCmhbF9emhHa-oZPiBfcLFaQ https://www.youtube.com/feeds/videos.xml?channel_id=UC3BNSKOaphlEoK4L7QTlpbA
```