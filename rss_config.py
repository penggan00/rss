# rss_config.py
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

RSS_GROUPS = [ # RSS 组配置列表
    # ================== 国际新闻组 ==================False: 关闭 / True: 开启
    {
        "name": "国际新闻",
        "urls": [
      #      'https://feeds.bbci.co.uk/news/world/rss.xml',  # BBC
            'https://www3.nhk.or.jp/rss/news/cat6.xml',     # NHK
       #     'https://www.cnbc.com/id/100003114/device/rss/rss.html',  # CNBC
         #   'https://feeds.a.dj.com/rss/RSSWorldNews.xml',  # 华尔街日报
        #    'https://feeds.content.dowjones.io/public/rss/RSSWorldNews',   # 华尔街日报
        #    'https://feeds.content.dowjones.io/public/rss/socialeconomyfeed',
         #  'https://www.aljazeera.com/xml/rss/all.xml',    # 半岛电视台
        #    'https://www.ft.com/?format=rss',                 # 金融时报
       #     'https://www3.nhk.or.jp/rss/news/cat5.xml',  # NHK 商业
       #     'http://rss.cnn.com/rss/cnn_topstories.rss',   # cnn
       #     'https://www.theguardian.com/world/rss',     # 卫报
      #      'https://www.theverge.com/rss/index.xml',   # The Verge:
        ],
        "group_key": "RSS_FEEDS",
        "interval": 3590,      # 60分钟 
      #  "batch_send_interval": 14390,   # 4小时批量推送
        "send_separately": False,  # 新增：设置为True时，每条消息单独发送
        "history_days": 180,     # 新增，保留30天
        "bot_token": os.getenv("RSS_TWO"),    # Telegram Bot Token
        "processor": {
            "translate": True,       #翻译开
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": True,         # False 禁止预览  True 允许预览
            "show_count": False         # ✅新增
        }
    },

    # ================== 国际新闻中文组 ==================False: 关闭 / True: 开启
    {
        "name": "国际新闻中文",
        "urls": [
             'https://www.chinanews.com.cn/rss/scroll-news.xml', #中新网
         #   'https://www.ftchinese.com/rss/news',   # ft中文网
       #     'https://sputniknews.cn/export/rss2/archive/index.xml',  # 俄新社
         #   'https://cn.nytimes.com/rss.html',  # 纽约时报中文网
        ],
        "group_key": "RSS_FEEDS_INTERNATIONAL",
        "interval": 1790,      # 30分钟
        "batch_send_interval": 3590,   # 批量推送←加上即
        "history_days": 300,     # 新增，保留30天
        "bot_token": os.getenv("RSS_TWO"),    # Telegram Bot Token
        "processor": {
            "translate": False,       #翻译 False: 关闭 / True: 开启
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,         # 禁止预览
            "show_count": False        # ✅新增
        }
    },

    # ================== 快讯组 ==================
    {
        "name": "快讯",
        "urls": [
         #   'https://rsshub.app/10jqka/realtimenews', #同花顺财经
            'https://36kr.com/feed-newsflash',  # 36氪快讯
        #    'https://36kr.com/feed',  # 36氪综合
            
        ],
        "group_key": "FOURTH_RSS_FEEDS",
        "interval": 700,       # 10分钟 
        "batch_send_interval": 21590,   # 批量推送
        "history_days": 7,     # 新增，保留3天
        "bot_token": os.getenv("RSS_LINDA"),   # Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,            # 禁止预览
            "show_count": False          #计数
        }
    },

    {
        "name": "同花顺",
        "urls": [
            'https://rsshub.app/10jqka/realtimenews', #同花顺财经
         #   'https://36kr.com/feed-newsflash',  # 36氪快讯
        #    'https://36kr.com/feed',  # 36氪综合
            
        ],
        "group_key": "FOURTH_RRSS_FEEDS",
        "interval": 700,       # 10分钟 
        "batch_send_interval": 21590,   # 批量推送
        "history_days": 7,     # 新增，保留3天
        "bot_token": os.getenv("RSS_LINDA"),   # Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "templates": {  # 改为 templates（复数），支持多种模板
          #      "normal": "{subject}\n[more]({url})",  # 普通模板
                "normal": "*{subject}*\n[more]({url})",  # 普通模板
                "highlight": "*{subject}*\n[more]({url})"  # 加粗模板
            },
            "filter": {
                "enable": True,     # 过滤开关     False: 关闭 / True: 开启
                "mode": "allow",    # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "scope": "title",   # 检查范围：title/all (只检查标题或标题+摘要)
                "keywords": ["比亚迪", "比特币", "美元", "历史",  "走强",  "失守",  "恒生指数", "高开", "涨停", "低开", "涨超", "黄金", "油", "汇率",  "跌停", "跌超", "突发", "重大", "人民币"] 
            },
            "highlight": {
                "enable": False,
                "scope": "title",  # 检查范围：title/all (只检查标题或标题+摘要)
                "keywords": ["比亚迪", "比特币", "美元", "历史",  "走强",  "失守",  "恒生指数",  "高开", "涨停", "低开", "涨超", "黄金", "油", "汇率",  "跌停", "跌超", "突发", "重大", "人民币"] ,
                "use_template": "highlight"  # 指定使用哪个模板
            },
            "preview": False,
            "show_count": False
        }
    },

    # ================== 综合资讯 ==================
    {
        "name": "综合资讯",
        "urls": [
          #  'https://rsshub.app/telegram/channel/zaobaosg', # 新加坡联合早报
          #  'https://rsshub.app/telegram/channel/rocCHL',  # 小鹏
      #      'https://rsshub.app/telegram/channel/tnews365', # 竹新社
            'https://www.v2ex.com/index.xml',  # V2EX
            'https://www.52pojie.cn/forum.php?mod=rss',  # 吾爱破解
        ],
        "group_key": "TOURTH_RSS_FEEDS",
        "interval": 1790,       # 30分钟
        "batch_send_interval": 35990,   # 批量推送
        "history_days": 180,     # 新增，保留3天
        "bot_token": os.getenv("TONGHUASHUN_RSS"),  #   Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,            # 禁止预览
            "show_count": False          #计数
        }
    },
    # ================== tegegram ==================
    {
        "name": "tg",
        "urls": [
            'https://rsshub.app/telegram/channel/shareAliyun', # 阿里云盘资源分享
         #   'https://rsshub.app/telegram/channel/cosfulimax', 
          #  'https://rsshub.app/telegram/channel/dianying4K', 
          #  'https://rsshub.app/telegram/channel/netcupoffical',  #netcpu
           'https://penggan0-rsstt.hf.space', #rsstt保活

        ],
        "group_key": "ZONGHE_RSS_FEEDS",
        "interval": 3590,       # 60分钟
        "batch_send_interval": 21590,   # 批量推送
        "history_days": 300,     # 新增，保留300天
        "bot_token": os.getenv("RSS_ZONGHE"),  #   Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "[{subject}]({url})",
            "filter": {
                "enable": True,  # 过滤开关     False: 关闭 / True: 开启
                "mode": "block",  # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "keywords": ["电子书", "epub", "mobi", "pdf", "azw3"]  # 本组关键词列表
            },
            "preview": False,            # 禁止预览
            "show_count": False          #计数
        }
    },
    # ================== 新浪博客 ==================
    {
        "name": "社交媒体",
        "urls": [
         #   'https://rsshub.app/weibo/user/3194547262',  # 江西高速
         #   'https://rsshub.app/weibo/user/1699432410',  # 新华社
        #    'https://rsshub.app/weibo/user/2656274875',  # 央视新闻
            'https://rsshub.app/weibo/user/2716786595',  # 聚萍乡
            'https://rsshub.app/weibo/user/1891035762',  # 交警
       #     'https://rsshub.app/weibo/user/3917937138',  # 发布
        #    'https://rsshub.app/weibo/user/3213094623',  # 邮政
            'https://rsshub.app/weibo/user/2818241427',  # 冒险岛

        ],
        "group_key": "FIFTH_RSSSA_FEEDS",
        "interval": 3590,    # 1小时
        "batch_send_interval": 17990,   # 批量推送    
        "history_days": 300,     # 新增，保留300天
        "bot_token": os.getenv("RRSS_LINDA"),  # Telegram Bot Token
        "processor": {
            "translate": False,     #翻译关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{summary}*\n[more]({url})",
            "preview": False,        # 禁止预览
            "show_count": False     #计数
        }
    },

    # ================== 技术论坛组 ==================
    {
        "name": "技术论坛",
        "urls": [
            'https://rss.nodeseek.com',
        ],
        "group_key": "FIFTH_RSS_RSS_SAN", 
        "interval": 240,
        "batch_send_interval": 1790,
        "history_days": 3,
        "bot_token": os.getenv("RSS_SAN"),
        "processor": {
            "translate": False,
            "header_template": "📢 _{source}_\n",
            "templates": {  # 改为 templates（复数），支持多种模板
                "normal": "*{subject}*\n[more]({url})",  # 普通过滤加粗
           #     "normal": "{subject}\n[more]({url})",  # 普通模板
                "highlight": "*{subject}*\n[more]({url})"  # 加粗模板
            },
            "filter": {
                "enable": True,    # 过滤开关     False: 关闭 / True: 开启
                "mode": "allow",    # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "scope": "title",   # 检查范围：title/all (只检查标题或标题+摘要)
                "keywords": ["免", "cf", "cl", "黑", "低", "小", "卡", "年", "bug", "白", "github", "节", "闪", "cc", "rn", "动", "cloudcone", "脚本", "代码", "docker", "剩", "gcp", "aws", "oracle", "google", "香港", "奖", "新加坡", "cmi", "hk", "sg", "折"]
            },
            "highlight": {
                "enable": False,     # 过滤开关     False: 关闭 / True: 开启
                "scope": "title",   # 检查范围：title/all (只检查标题或标题+摘要)
                "keywords": ["免", "cf", "cl", "黑", "低", "小", "卡", "年", "bug", "白", "github", "节", "闪", "cc", "rn", "动", "cloudcone", "脚本", "代码", "docker", "剩", "gcp", "aws", "oracle", "google", "香港", "奖", "新加坡", "cmi", "hk", "sg", "折"],
                "use_template": "highlight"  # 指定使用哪个模板
            },
            "preview": False,      # 禁止预览
            "show_count": False    # ✅新增
        }
    },
    # ================== vps 翻译 ==================
    {
        "name": "vps",
        "urls": [
            'https://lowendspirit.com/discussions/feed.rss', # lowendspirit
            'https://lowendtalk.com/discussions/feed.rss',   # lowendtalk
        ],
        "group_key": "FIFTH_RSS_RRSS_SAN",
        "interval": 3590,      # 60分钟 
        "batch_send_interval": 17990,   # 批量推送
        "history_days": 300,     # 保留60天
        "bot_token": os.getenv("RSS_SAN"),    # Telegram Bot Token
        "processor": {
            "translate": True,       #翻译开
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,         # 禁止预览
            "show_count": False        # ✅新增
        }
    },
    # ================== YouTube频道组 ==================
    {
        "name": "YouTube频道",
        "urls": [
         #   'https://blog.090227.xyz/atom.xml',
         #   'https://www.freedidi.com/feed',
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCvijahEyGtvMpmMHBu4FS2w', # 零度解说
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC96OvMh0Mb_3NmuE8Dpu7Gg', # 搞机零距离
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQoagx4VHBw3HkAyzvKEEBA', # 科技共享
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCbCCUH8S3yhlm7__rhxR2QQ', # 不良林
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCMtXiCoKFrc2ovAGc1eywDg', # 一休
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCii04BCvYIdQvshrdNDAcww', # 悟空的日常
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCJMEiNh1HvpopPU3n9vJsMQ', # 理科男士
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCYjB6uufPeHSwuHs8wovLjg', # 中指通
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCSs4A6HYKmHA2MG_0z-F0xw', # 李永乐老师
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCZDgXi7VpKhBJxsPuZcBpgA', # 可恩KeEn
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCxukdnZiXnTFvjF5B5dvJ5w', # 甬哥侃侃侃ygkkk
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCUfT9BAofYBKUTiEVrgYGZw', # 科技分享
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC51FT5EeNPiiQzatlA2RlRA', # 乌客wuke
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCDD8WJ7Il3zWBgEYBUtc9xQ', # jack stone
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCWurUlxgm7YJPPggDz9YJjw', # 一瓶奶油
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCvENMyIFurJi_SrnbnbyiZw', # 酷友社
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCmhbF9emhHa-oZPiBfcLFaQ', # WenWeekly
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC3BNSKOaphlEoK4L7QTlpbA', # 中外观察
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCXk0rwHPG9eGV8SaF2p8KUQ', # 烏鴉笑笑
                    # ... 其他YouTube频道（共18个）
        ],
        "group_key": "YOUTUBE_RSSS_FEEDS", # YouTube频道
        "interval": 3590,      # 60分钟
       # "batch_send_interval": 10800,   # 批量推送
        "history_days": 720,     # 新增，保留30天
        "bot_token": os.getenv("RSS_TOKEN"),   # Telegram Bot Token
        "processor": {
            "translate": False,                    #翻译关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": True,                # 预览
            "show_count": False               #计数
        }
    },

    # ================== 中文YouTube组 ==================
    {
        "name": "中文YouTube",
        "urls": [
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCUNciDq-y6I6lEQPeoP-R5A', # 苏恒观察
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCXkOTZJ743JgVhJWmNV8F3Q', # 寒國人
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC2r2LPbOUssIa02EbOIm7NA', # 星球熱點
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCF-Q1Zwyn9681F7du8DMAWg', # 謝宗桓-老謝來了
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCSYBgX9pWGiUAcBxjnj6JCQ', # 郭正亮頻道
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCNiJNzSkfumLB7bYtXcIEmg', # 真的很博通
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCN0eCImZY6_OiJbo8cy5bLw', # 屈機TV
         #   'https://www.youtube.com/feeds/videos.xml?channel_id=UCb3TZ4SD_Ys3j4z0-8o6auA', # BBC News 中文
       #     'https://www.youtube.com/feeds/videos.xml?channel_id=UCiwt1aanVMoPYUt_CQYCPQg', # 全球大視野
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC000Jn3HGeQSwBuX_cLDK8Q', # 我是柳傑克
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQFEBaHCJrHu2hzDA_69WQg', # 国漫说
            'https://www.youtube.com/feeds/videos.xml?channel_id=UChJ8YKw6E1rjFHVS9vovrZw', # BNE TV - 新西兰中文国际频道
          #  'https://www.youtube.com/feeds/videos.xml?channel_id=UCJncdiH3BQUBgCroBmhsUhQ', # 观察者网
        # 影视
            'https://www.youtube.com/feeds/videos.xml?channel_id=UC7Xeh7thVIgs_qfTlwC-dag', # Marc TV
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCCD14H7fJQl3UZNWhYMG3Mg', # 温城鲤
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCQO2T82PiHCYbqmCQ6QO6lw', # 月亮說
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCHW6W9g2TJL2_Lf7GfoI5kg', # 电影放映厅
            'https://www.youtube.com/feeds/videos.xml?channel_id=UCi2GvcaxZCN-61a0co8Smnw', # 館長
   # bilibili
       #     'https://rsshub.app/bilibili/user/video/271034954', #无限海子
        #    'https://rsshub.app/bilibili/user/video/10720688', #乌客wuke
         #   'https://rsshub.app/bilibili/user/video/33683045', #张召忠
        #    'https://rsshub.app/bilibili/user/video/9458053', #李永乐
         #   'https://rsshub.app/bilibili/user/video/456664753', #央视新闻
          #  'https://rsshub.app/bilibili/user/video/95832115', #汐朵曼
          #  'https://rsshub.app/bilibili/user/video/3546741104183937', #油管精選字幕组
          #  'https://rsshub.app/bilibili/user/video/52165725', #王骁Albert
        ],
        "group_key": "FIFTH_RSS_YOUTUBE", # YouTube频道
        "interval": 3590,     # 1小时
        "batch_send_interval": 71990,   # 批量推送
        "history_days": 720,     # 新增，保留300天
        "bot_token": os.getenv("YOUTUBE_RSS"),    # Telegram Bot Token
        "processor": {
        "translate": False,                    #翻译关
        "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
    #   "template": "*{subject}*\n🔗 {url}",
        "template": "*{subject}*\n[more]({url})",
            "filter": {
                "enable": True,  # 过滤开关     False: 关闭 / True: 开启
                "mode": "block",  # allow模式：包含关键词才发送 / block模式：包含关键词不发送
                "scope": "link",  # 只过滤链接
                "keywords": ["/shorts/", "/shorts/"]  # 本组关键词列表
            },
        "preview": True,                       # 预览
        "show_count": False                    #计数
    }
    },
    # ================== 社交媒体组+翻译预览 ==================
    {
        "name": "社交媒体",
        "urls": [
        #    'https://rsshub.app/twitter/media/clawcloud43609', # claw.cloud
            'https://www.apkmirror.com/feed/', # apk
         #   'https://rsshub.app/twitter/media/ElonMuskAOC',   # Elon Musk
        #    'https://rsshub.app/twitter/media/elonmusk',   # Elon Musk
        #    'https://rsshub.app/telegram/channel/netcupoffical',  #netcpu
        #    'https://www.youtube.com/feeds/videos.xml?channel_id=UCQeRaTukNYft1_6AZPACnog',  # Asmongold

        ],
        "group_key": "FIFTH_RSS_FEEDS",   # YouTube频道
        "interval": 7000,    # 2小时
        "batch_send_interval": 71990,   # 批量推送
        "history_days": 360,     # 新增，保留30天
        "bot_token": os.getenv("YOUTUBE_RSS"),  # Telegram Bot Token
        "processor": {
            "translate": True,          #翻译开
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
         #   "template": "*{subject}*\n🔗 {url}",
            "template": "*{subject}*\n[more]({url})",
            "preview": True,        # 预览
            "show_count": False     #计数
        }
    },
    # ================== 中文媒体组 ==================
    {
        "name": "中文媒体", 
        "urls": [
            'https://rsshub.app/guancha/headline', # 观察者网 头条
            'https://rsshub.app/guancha', # 观察者网全部
        #    'https://rsshub.app/zaobao/znews/china', # 联合早报 中国
        ],
        "group_key": "THIRD_RSS_FEEDS",
        "interval": 3590,      # 1小时
        "batch_send_interval": 14350,   # 批量推送
        "history_days": 90,     # 新增，保留30天
        "bot_token": os.getenv("RSS_LINDA_YOUTUBE"), # Telegram Bot Token
        "processor": {
            "translate": False,                        #翻译开关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "*{subject}*\n[more]({url})",
            "preview": False,                             # 禁止预览
            "show_count": False                       #计数
        }
    },
    # ================== 保活 ==================
    {
        "name": "保活",
        "urls": [
         #   'https://rsshub.app/telegram/channel/shareAliyun', # 阿里云盘资源分享
         #   'https://rsshub.app/telegram/channel/cosfulimax', 
          #  'https://rsshub.app/telegram/channel/dianying4K', 
            'https://penggan0-iopenlist.hf.space',  #openlist
            'https://penggan0-rsstt.hf.space', #rsstt保活

        ],
        "group_key": "ZONGHE_RSSTT_FEEDS",
        "interval": 36000,       # 600分钟
     #   "batch_send_interval": 21590,   # 批量推送
        "history_days": 300,     # 新增，保留300天
        "bot_token": os.getenv("RSS_ZONGHE"),  #   Telegram Bot Token
        "processor": {
            "translate": False,     #翻译开关
            "header_template": "📢 _{source}_\n",  # 新增标题模板 ★
            "template": "[{subject}]({url})",
            "preview": False,            # 禁止预览
            "show_count": False          #计数
        }
    }
]