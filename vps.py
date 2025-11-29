#!/usr/bin/env python3
"""
CloudCone Black Friday 优惠监控脚本 - Cron 版本
每分钟运行一次，只发送 Flash Sale 套餐
"""

import requests
import json
import hashlib
import logging
from datetime import datetime, timedelta
import sys
import os
from dotenv import load_dotenv
import re
import time
from md2tgmd import escape

# 加载环境变量
load_dotenv()

# 配置信息
CONFIG = {
    # CloudCone API 地址
    'API_URL': 'https://app.cloudcone.com/events/blackfriday-offers',
    
    # Telegram Bot 配置（从环境变量读取）
    'TELEGRAM_API_KEY': os.getenv('TELEGRAM_API_KEY'),
    'TELEGRAM_CHAT_IDS': os.getenv('TELEGRAM_CHAT_ID', '').split(','),
    
    # 数据文件路径（用于存储上次检查的数据）
    'DATA_FILE': 'cloudcone_data.json'
}

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cloudcone_monitor.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

class CloudConeMonitor:
    def __init__(self, config):
        self.config = config
        self.last_data_hash = None
        self.last_offers = {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        })
        
        # 验证配置
        self._validate_config()
    
    def _validate_config(self):
        """验证配置是否完整"""
        if not self.config['TELEGRAM_API_KEY']:
            raise ValueError("TELEGRAM_API_KEY 未设置")
        
        if not self.config['TELEGRAM_CHAT_IDS'] or not any(self.config['TELEGRAM_CHAT_IDS']):
            raise ValueError("TELEGRAM_CHAT_ID 未设置")
        
        # 清理空的聊天ID
        self.config['TELEGRAM_CHAT_IDS'] = [chat_id.strip() for chat_id in self.config['TELEGRAM_CHAT_IDS'] if chat_id.strip()]
        
        logging.info(f"配置验证成功，将发送到 {len(self.config['TELEGRAM_CHAT_IDS'])} 个聊天")
    
    def send_telegram_message(self, message, chat_id=None):
        """发送 Telegram 消息到指定聊天或所有聊天"""
        url = f"https://api.telegram.org/bot{self.config['TELEGRAM_API_KEY']}/sendMessage"
        
        if chat_id:
            chat_ids = [chat_id]
        else:
            chat_ids = self.config['TELEGRAM_CHAT_IDS']
        
        success_count = 0
        for cid in chat_ids:
            payload = {
                'chat_id': cid,
                'text': message,
                'parse_mode': 'MarkdownV2',
                'disable_web_page_preview': False
            }
            
            try:
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                success_count += 1
                logging.info(f"Telegram 消息发送到 {cid} 成功")
            except requests.RequestException as e:
                logging.error(f"发送 Telegram 消息到 {cid} 失败: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logging.error(f"响应内容: {e.response.text}")
        
        return success_count > 0
    
    def get_data_hash(self, data):
        """生成数据的哈希值用于比较，专注于 Flash Sale 相关数据"""
        # 创建一个只包含 Flash Sale 相关数据的结构用于哈希比较
        flash_sale_data = {
            'vps_flash_sales': {},
            'sc2_flash_sales': {}
        }
        
        # 处理 VPS 数据，只关注 Flash Sale
        if 'vps_data' in data:
            for offer_id, offer in data['vps_data'].items():
                # 检查是否是 Flash Sale
                name = offer.get('name', '')
                is_flash_sale = (
                    'STL-BF' in name or 
                    'HFS' in name or 
                    'Flash' in str(offer) or
                    any(keyword in name for keyword in ['STL', 'HFS', 'FLASH', 'LA-BF'])
                )
                
                if is_flash_sale:
                    flash_sale_data['vps_flash_sales'][offer_id] = {
                        'name': name,
                        'cpu': offer.get('cpu', 0),
                        'ram': offer.get('ram', ''),
                        'disk': offer.get('disk', 0),
                        'bandwidth': offer.get('bandwidth', ''),
                        'usd_price': offer.get('usd_price', 0),
                        'order_url': offer.get('order_url', '')
                    }
        
        # 处理 SC2 数据，只关注 Flash Sale
        if 'sc2_data' in data:
            for offer_id, offer in data['sc2_data'].items():
                # 检查 SC2 是否有 Flash Sale
                name = offer.get('name', '')
                is_flash_sale = (
                    'Flash' in str(offer) or 
                    'STL-BF' in name or
                    any(keyword in name for keyword in ['STL', 'FLASH'])
                )
                
                if is_flash_sale:
                    flash_sale_data['sc2_flash_sales'][offer_id] = {
                        'name': name,
                        'cpu': offer.get('cpu', 0),
                        'ram': offer.get('ram', ''),
                        'disk': offer.get('disk', 0),
                        'bandwidth': offer.get('bandwidth', ''),
                        'usd_price': offer.get('usd_price', 0),
                        'order_url': offer.get('order_url', '')
                    }
        
        data_str = json.dumps(flash_sale_data, sort_keys=True)
        hash_value = hashlib.md5(data_str.encode()).hexdigest()
        logging.info(f"生成的 Flash Sale 哈希: {hash_value}")
        return hash_value
    
    def fetch_offers(self):
        """获取优惠数据"""
        try:
            response = self.session.get(self.config['API_URL'], timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') != 1:
                logging.error(f"API 返回错误: {data.get('message')}")
                return None
            
            return data.get('__data', {})
        except requests.RequestException as e:
            logging.error(f"请求 API 失败: {e}")
            return None
        except json.JSONDecodeError as e:
            logging.error(f"解析 JSON 失败: {e}")
            return None
    
    def parse_offers(self, data):
        """解析优惠数据，只关注 Flash Sale"""
        offers = {}
        
        # 解析 VPS 优惠，只保留 Flash Sale
        vps_data = data.get('vps_data', {})
        for offer_id, offer in vps_data.items():
            # 检查是否是 Flash Sale - 更宽松的条件
            name = offer.get('name', '')
            is_flash_sale = (
                'STL-BF' in name or 
                'HFS' in name or 
                'Flash' in str(offer) or
                any(keyword in name for keyword in ['STL', 'HFS', 'FLASH', 'LA-BF'])
            )
            
            # 调试日志
            if is_flash_sale:
                logging.info(f"检测到 VPS Flash Sale: {name}")
            
            # 只处理 Flash Sale 套餐
            if is_flash_sale:
                # 处理 CPU 数据，确保是整数
                cpu = offer.get('cpu', 0)
                if cpu is None:
                    cpu = 0
                elif isinstance(cpu, str):
                    try:
                        cpu = int(cpu)
                    except (ValueError, TypeError):
                        cpu = 0
                
                offers[offer_id] = {
                    'type': 'VPS',
                    'name': name,
                    'cpu': cpu,
                    'ram': offer.get('ram', ''),
                    'disk': offer.get('disk', 0),
                    'bandwidth': offer.get('bandwidth', ''),
                    'price': offer.get('usd_price', 0),
                    'order_url': f"https://app.cloudcone.com{offer.get('order_url', '')}",
                    'is_flash_sale': True
                }
        
        # 解析 SC2 优惠，只保留 Flash Sale
        sc2_data = data.get('sc2_data', {})
        for offer_id, offer in sc2_data.items():
            # 检查 SC2 是否有 Flash Sale
            name = offer.get('name', '')
            is_flash_sale = (
                'Flash' in str(offer) or 
                'STL-BF' in name or
                any(keyword in name for keyword in ['STL', 'FLASH'])
            )
            
            if is_flash_sale:
                logging.info(f"检测到 SC2 Flash Sale: {name}")
                
                # 处理 SC2 的 CPU 数据
                cpu = offer.get('cpu', 0)
                if cpu is None:
                    cpu = 0
                elif isinstance(cpu, str):
                    try:
                        cpu = int(cpu)
                    except (ValueError, TypeError):
                        cpu = 0
                
                # 如果 CPU 为 0，尝试从名称中提取
                if cpu == 0 and 'SC2' in name:
                    cpu_match = re.search(r'SC2-(\d+)', name)
                    if cpu_match:
                        cpu = int(cpu_match.group(1))
                
                offers[offer_id] = {
                    'type': 'SC2',
                    'name': name,
                    'cpu': cpu,
                    'ram': offer.get('ram', ''),
                    'disk': offer.get('disk', 0),
                    'bandwidth': offer.get('bandwidth', ''),
                    'price': offer.get('usd_price', 0),
                    'order_url': f"https://app.cloudcone.com{offer.get('order_url', '')}",
                    'is_flash_sale': True
                }
        
        logging.info(f"总共找到 {len(offers)} 个 Flash Sale 套餐")
        return offers
    
    def format_offer_message(self, offer):
        """格式化单个优惠信息消息"""
        flash_sale = "🔥 " if offer.get('is_flash_sale') else ""
        
        # 不在这里转义，在发送前统一用 md2tgmd 转义
        name = offer['name']
        cpu = str(offer['cpu'])
        ram = offer['ram']
        disk = str(offer['disk'])
        bandwidth = offer['bandwidth']
        price = str(offer['price'])
        order_url = offer['order_url']
        
        # 创建原始消息，不进行转义
        message = f"{flash_sale}**{name}** | CPU:{cpu} | 内存:{ram} | 存储:{disk}G | 流量:{bandwidth} | 价格:${price}/年 | [链接]({order_url})"
        
        return message  # 返回原始消息，不调用 md2tgmd
        
    def format_all_offers_message(self, offers):
        """格式化所有 Flash Sale 优惠套餐的汇总消息"""
        # 只保留 Flash Sale 套餐的详细信息，不要统计信息
        offers_messages = []
        
        # 添加 VPS Flash Sale 套餐
        vps_offers = [offer for offer in offers.values() if offer['type'] == 'VPS']
        if vps_offers:
            for offer in vps_offers:
                offers_messages.append(self.format_offer_message(offer))
        
        # 添加 SC2 Flash Sale 套餐
        sc2_offers = [offer for offer in offers.values() if offer['type'] == 'SC2']
        if sc2_offers:
            for offer in sc2_offers:
                offers_messages.append(self.format_offer_message(offer))
        
        # 如果没有 Flash Sale，显示提示
        if not offers:
            offers_messages.append("⚠️ *当前没有 Flash Sale 套餐*")
        
        full_message = "\n".join(offers_messages)
        logging.info(f"生成 Flash Sale 消息长度: {len(full_message)} 字符")
        return full_message
        
    def save_data(self, data_hash, offers):
        """保存数据到文件"""
        try:
            with open(self.config['DATA_FILE'], 'w', encoding='utf-8') as f:
                json.dump({
                    'last_hash': data_hash,
                    'last_offers': offers,
                    'last_update': datetime.now().isoformat(),
                    'last_check': datetime.now().isoformat()
                }, f, ensure_ascii=False, indent=2)
            logging.info("数据保存成功")
        except Exception as e:
            logging.error(f"保存数据失败: {e}")
    
    def load_data(self):
        """从文件加载数据"""
        try:
            with open(self.config['DATA_FILE'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.last_data_hash = data.get('last_hash')
                self.last_offers = data.get('last_offers', {})
                last_update = data.get('last_update', '未知')
                logging.info(f"加载历史数据，上次更新时间: {last_update}")
                logging.info(f"上次数据哈希: {self.last_data_hash}")
                return True
        except FileNotFoundError:
            logging.info("未找到历史数据文件，首次运行")
            return False
        except Exception as e:
            logging.error(f"加载数据失败: {e}")
            return False
    
    def test_bot_connection(self):
        """测试机器人连接"""
        url = f"https://api.telegram.org/bot{self.config['TELEGRAM_API_KEY']}/getMe"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    bot_info = data.get('result', {})
                    logging.info(f"机器人连接成功: @{bot_info.get('username')} ({bot_info.get('first_name')})")
                    return True
                else:
                    logging.error(f"机器人连接失败: {data.get('description')}")
                    return False
            else:
                logging.error(f"HTTP 错误: {response.status_code}")
                return False
        except requests.RequestException as e:
            logging.error(f"测试机器人连接失败: {e}")
            return False
    
    def run_single_check(self):
        """执行单次检查（供 cron 调用）"""
        logging.info("开始检查优惠更新...")
        
        # 加载历史数据
        has_previous_data = self.load_data()
        
        # 获取数据
        data = self.fetch_offers()
        if not data:
            logging.error("无法获取优惠数据")
            return False
        
        # 生成数据哈希
        current_hash = self.get_data_hash(data)
        logging.info(f"当前数据哈希: {current_hash}")
        logging.info(f"上次数据哈希: {self.last_data_hash}")
        
        # 检查是否有变化
        if current_hash == self.last_data_hash:
            logging.info("数据无变化，跳过发送")
            # 即使无变化也更新检查时间
            self.save_data(current_hash, self.last_offers)
            return False
        
        logging.info("检测到数据变化！准备发送 Flash Sale 套餐")
        
        # 解析优惠数据
        current_offers = self.parse_offers(data)
        logging.info(f"解析到 {len(current_offers)} 个 Flash Sale 套餐")
        
        # 调试信息：显示每个套餐的详细信息
        for offer_id, offer in current_offers.items():
            logging.info(f"Flash Sale 套餐: {offer['name']}, CPU={offer['cpu']}核心, 价格=${offer['price']}")
        
        # 如果没有 Flash Sale 套餐，也记录日志
        if len(current_offers) == 0:
            logging.info("当前没有 Flash Sale 套餐")
            # 即使没有 Flash Sale，也要更新哈希值，避免重复检测
            self.last_data_hash = current_hash
            self.last_offers = current_offers
            self.save_data(current_hash, current_offers)
            return True
        
        # 格式化所有优惠消息
        all_offers_message = self.format_all_offers_message(current_offers)
        
        # 发送消息（由于消息可能很长，分成多个部分发送）
        message_parts = self.split_message(all_offers_message)
        
        success = True
        for i, part in enumerate(message_parts):
            logging.info(f"发送消息第 {i+1}/{len(message_parts)} 部分")
            # 在发送前统一用 md2tgmd 转义
            escaped_part = escape(part)  # 直接使用 escape，不需要重复导入
            if not self.send_telegram_message(escaped_part):
                success = False
                logging.error("发送消息失败")
                break
            # 消息间短暂延迟，避免发送过快
            time.sleep(1)
        
        if success:
            logging.info(f"成功发送 {len(current_offers)} 个 Flash Sale 套餐")
            # 更新数据
            self.last_data_hash = current_hash
            self.last_offers = current_offers
            self.save_data(current_hash, current_offers)
        else:
            logging.error("发送消息失败，数据未更新")
        
        return success
    
    def split_message(self, message, max_length=4000):
        """将长消息分割成多个部分（Telegram 消息长度限制）"""
        if len(message) <= max_length:
            return [message]
        
        # 简单的消息分割逻辑
        parts = []
        lines = message.split('\n')
        current_part = []
        current_length = 0
        
        for line in lines:
            line_length = len(line)
            if current_length + line_length + 1 > max_length and current_part:
                parts.append('\n'.join(current_part))
                current_part = [line]
                current_length = line_length
            else:
                current_part.append(line)
                current_length += line_length + 1
        
        if current_part:
            parts.append('\n'.join(current_part))
        
        logging.info(f"消息被分割成 {len(parts)} 部分")
        return parts

def main():
    """主函数"""
    try:
        # 创建监控实例
        monitor = CloudConeMonitor(CONFIG)
        
        # 测试机器人连接
        if not monitor.test_bot_connection():
            logging.error("机器人连接测试失败，请检查 Token 是否正确")
            sys.exit(1)
        
        # 执行单次检查
        success = monitor.run_single_check()
        
        if success:
            logging.info("监控检查完成 - 检测到更新并发送通知")
        else:
            logging.info("监控检查完成 - 无更新或检查失败")
            
        sys.exit(0 if success else 1)
            
    except ValueError as e:
        logging.error(f"配置错误: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"运行失败: {e}")
        import traceback
        logging.error(f"详细错误信息: {traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    main()