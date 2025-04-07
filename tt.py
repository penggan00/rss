import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 加载配置
load_dotenv()

# 城市ID映射
CITIES = {
    "南昌": os.getenv("CITY_NANCHANG", "101240101"),
    "萍乡": os.getenv("CITY_PINGXIANG", "101240901")
}

def get_weather_forecast(city_id):
    """获取3天天气预报"""
    url = f"https://{os.getenv('QWEATHER_API_HOST')}/v7/weather/3d"
    params = {
        "location": city_id,
        "key": os.getenv('QWEATHER_API_KEY')
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data.get("code") == "200":
            return data["daily"]
        print(f"API错误: {data.get('message')}")
        return None
    except Exception as e:
        print(f"请求失败: {str(e)}")
        return None

def check_rain_tomorrow(forecast_data):
    """检查明天是否有雨"""
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    for day in forecast_data:
        if day["fxDate"] == tomorrow:
            return "雨" in day["textDay"] or "雨" in day["textNight"]
    return False

def send_telegram_alert(city, weather_data):
    """发送Telegram提醒"""
    message = (
        f"**{city}** 明天有雨！\n\n"
    )
    
    url = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_API_KEY')}/sendMessage"
    payload = {
        "chat_id": os.getenv('TELEGRAM_CHAT_ID'),
        "text": message,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

def main():
    for city, city_id in CITIES.items():
        forecast = get_weather_forecast(city_id)
        if forecast:
            if check_rain_tomorrow(forecast):
                tomorrow_data = forecast[1]  # 第2条是明天数据
                print(f"⚠️ {city} 明天下雨，发送提醒...")
                send_telegram_alert(city, tomorrow_data)
            else:
                print(f"✅ {city} 明天无雨")

if __name__ == "__main__":
    main()