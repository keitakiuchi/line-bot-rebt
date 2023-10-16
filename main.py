from flask import Flask, request, abort
import os
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
)
import requests
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__) # stripeの情報の確認
import stripe
import psycopg2
import datetime

app = Flask(__name__)

# 環境変数取得
YOUR_CHANNEL_ACCESS_TOKEN = os.environ["YOUR_CHANNEL_ACCESS_TOKEN"]
YOUR_CHANNEL_SECRET = os.environ["YOUR_CHANNEL_SECRET"]
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
GPT4_API_URL = 'https://api.openai.com/v1/chat/completions'

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
STRIPE_PRICE_ID = os.environ["SUBSCRIPTION_PRICE_ID"]

# db接続
def get_connection():
    dsn = f"host={os.environ['DB_HOST']} " \
          f"port=5432 " \
          f"dbname={os.environ['DB_NAME']} " \
          f"user={os.environ['DB_USER']} " \
          f"password={os.environ['DB_PASS']}"
    return psycopg2.connect(dsn)

@app.route("/")
def hello_world():
    return "hello world!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

def generate_gpt4_response(prompt):
    sys_prompt = """
        You are a helpful assistant.
        """
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {OPENAI_API_KEY}'
    }
    data = {
        'model': "gpt-4",
        'messages': [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ],
        'temperature': 1
    }

    response_json = response.json()
    # return response_json['choices'][0]['message']['content'].strip()
    # Add this line to log the response from OpenAI API
    app.logger.info("Response from OpenAI API: " + str(response_json))

    try:
        response = requests.post(GPT4_API_URL, headers=headers, json=data)
        response.raise_for_status()  # Check if the request was successful
        response_json = response.json()
        return response_json['choices'][0]['message']['content'].strip()
    except requests.RequestException as e:
        app.logger.error(f"OpenAI API request failed: {e}")
        return "Sorry, I couldn't understand that."
        
def get_system_responses_in_last_24_hours(userId):
    # この関数の中でデータベースにアクセスして、指定されたユーザーに対する過去24時間以内のシステムの応答数を取得します。
    # 以下は仮の実装の例です。
    connection = get_connection()
    cursor = connection.cursor()
    try:
        query = """
        SELECT COUNT(*) FROM line_bot_logs 
        WHERE sender='system' AND lineId=%s AND timestamp > NOW() - INTERVAL '24 HOURS';
        """
        cursor.execute(query, (userId,))
        result = cursor.fetchone()
        return result[0]
    except Exception as e:
        print(f"Error: {e}")
        return 0
    finally:
        cursor.close()
        connection.close()

# LINEからのメッセージを処理し、必要に応じてStripeの情報も確認します。
@handler.add(MessageEvent, message=TextMessage)
def handle_line_message(event):
    # event.sourceオブジェクトの属性とその値をログに出力
    for attr in dir(event.source):
        logging.info(f"Attribute: {attr}, Value: {getattr(event.source, attr)}")

    # ユーザーからのイベントの場合、ユーザーIDを出力
    userId = getattr(event.source, 'user_id', None)

    # 現在のタイムスタンプを取得
    current_timestamp = datetime.datetime.now()

    # stripeIdを取得 (userIdが存在しない場合も考慮しています)
    stripe_id = None
    if userId:
        subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
        stripe_id = subscription_details['stripeId'] if subscription_details else None
        subscription_status = subscription_details['status'] if subscription_details else None

        # LINEからのメッセージをログに保存
        log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text)

        # ステータスがactiveなら、利用回数の制限を気にせずに応答
        if subscription_status == "active":
            reply_text = generate_gpt4_response(event.message.text)
        else:
            response_count = get_system_responses_in_last_24_hours(userId)
            if response_count < 2: 
                reply_text = generate_gpt4_response(event.message.text)
            else:
                reply_text = "利用回数の上限に達しました。24時間後に再度お試しください。"
    else:
        reply_text = "エラーが発生しました。"

    # メッセージをログに保存
    log_to_database(current_timestamp, 'system', userId, stripe_id, reply_text)

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# stripeの情報を参照
def get_subscription_details_for_user(userId, STRIPE_PRICE_ID):
    subscriptions = stripe.Subscription.list(limit=100)
    for subscription in subscriptions.data:
        if subscription["items"]["data"][0]["price"]["id"] == STRIPE_PRICE_ID and subscription["metadata"].get("line_user") == userId:
            return {
                'status': subscription["status"],
                'stripeId': subscription["customer"]
            }
    return None

# Stripeの情報を確認する関数
def check_subscription_status(userId):
    return get_subscription_details_for_user(userId, STRIPE_PRICE_ID)

# データをdbに入れる関数
def log_to_database(timestamp, sender, userId, stripeId, message):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        query = """
        INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message) 
        VALUES (%s, %s, %s, %s, %s);
        """
        cursor.execute(query, (timestamp, sender, userId, stripeId, message))
        connection.commit()
    except Exception as e:
        print(f"Error: {e}")
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


## データベース実装
#####################################
# from flask import Flask, request, abort
# import os
# import openai
# from linebot import (
#     LineBotApi, WebhookHandler
# )
# from linebot.exceptions import (
#     InvalidSignatureError
# )
# from linebot.models import (
#     MessageEvent, TextMessage, TextSendMessage,
# )
# import requests
# import logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__) # stripeの情報の確認
# import stripe
# import psycopg2
# import datetime

# app = Flask(__name__)

# # 環境変数取得
# YOUR_CHANNEL_ACCESS_TOKEN = os.environ["YOUR_CHANNEL_ACCESS_TOKEN"]
# YOUR_CHANNEL_SECRET = os.environ["YOUR_CHANNEL_SECRET"]
# line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
# handler = WebhookHandler(YOUR_CHANNEL_SECRET)

# OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
# GPT4_API_URL = 'https://api.openai.com/v1/chat/completions'

# stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
# STRIPE_PRICE_ID = os.environ["SUBSCRIPTION_PRICE_ID"]

# # db接続
# def get_connection():
#     dsn = f"host={os.environ['DB_HOST']} " \
#           f"port=5432 " \
#           f"dbname={os.environ['DB_NAME']} " \
#           f"user={os.environ['DB_USER']} " \
#           f"password={os.environ['DB_PASS']}"
#     return psycopg2.connect(dsn)

# @app.route("/")
# def hello_world():
#     return "hello world!"

# @app.route("/callback", methods=['POST'])
# def callback():
#     signature = request.headers['X-Line-Signature']
#     body = request.get_data(as_text=True)
#     app.logger.info("Request body: " + body)
#     try:
#         handler.handle(body, signature)
#     except InvalidSignatureError:
#         abort(400)
#     return 'OK'

# def generate_gpt4_response(prompt):
#     headers = {
#         'Content-Type': 'application/json',
#         'Authorization': f'Bearer {OPENAI_API_KEY}'
#     }
#     data = {
#         'model': "gpt-4",
#         'messages': [
#             {"role": "system", "content": "You are a helpful assistant."},
#             {"role": "user", "content": prompt}
#         ]
#     }

#     response = requests.post(GPT4_API_URL, headers=headers, json=data)
#     response_json = response.json()
#     # return response_json['choices'][0]['message']['content'].strip()
#     # Add this line to log the response from OpenAI API
#     app.logger.info("Response from OpenAI API: " + str(response_json))

#     try:
#         response = requests.post(GPT4_API_URL, headers=headers, json=data)
#         response.raise_for_status()  # Check if the request was successful
#         response_json = response.json()
#         return response_json['choices'][0]['message']['content'].strip()
#     except requests.RequestException as e:
#         app.logger.error(f"OpenAI API request failed: {e}")
#         return "Sorry, I couldn't understand that."
        
# def get_system_responses_in_last_24_hours(userId):
#     # この関数の中でデータベースにアクセスして、指定されたユーザーに対する過去24時間以内のシステムの応答数を取得します。
#     # 以下は仮の実装の例です。
#     connection = get_connection()
#     cursor = connection.cursor()
#     try:
#         query = """
#         SELECT COUNT(*) FROM line_bot_logs 
#         WHERE sender='system' AND lineId=%s AND timestamp > NOW() - INTERVAL '24 HOURS';
#         """
#         cursor.execute(query, (userId,))
#         result = cursor.fetchone()
#         return result[0]
#     except Exception as e:
#         print(f"Error: {e}")
#         return 0
#     finally:
#         cursor.close()
#         connection.close()

# # LINEからのメッセージを処理し、必要に応じてStripeの情報も確認します。
# @handler.add(MessageEvent, message=TextMessage)
# def handle_line_message(event):
#     # event.sourceオブジェクトの属性とその値をログに出力
#     for attr in dir(event.source):
#         logging.info(f"Attribute: {attr}, Value: {getattr(event.source, attr)}")

#     # ユーザーからのイベントの場合、ユーザーIDを出力
#     userId = getattr(event.source, 'user_id', None)

#     # 現在のタイムスタンプを取得
#     current_timestamp = datetime.datetime.now()

#     # stripeIdを取得 (userIdが存在しない場合も考慮しています)
#     stripe_id = None
#     if userId:
#         subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
#         stripe_id = subscription_details['stripeId'] if subscription_details else None

#     # LINEからのメッセージをログに保存
#     log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text)

#     response_count = get_system_responses_in_last_24_hours(userId)
#     if userId and check_subscription_status(userId) == "negathive": ## ここで調整 ## active
#         reply_text = generate_gpt4_response(event.message.text)
#     else:
#         if response_count < 2: ## ここで調整 ##
#             reply_text = generate_gpt4_response(event.message.text)
#         else:
#             reply_text = "利用回数の上限に達しました。24時間後に再度お試しください。"

#     # メッセージをログに保存
#     log_to_database(current_timestamp, 'system', userId, stripe_id, reply_text)

#     line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# # stripeの情報を参照
# def get_subscription_details_for_user(userId, STRIPE_PRICE_ID):
#     subscriptions = stripe.Subscription.list(limit=100)
#     for subscription in subscriptions.data:
#         if subscription["items"]["data"][0]["price"]["id"] == STRIPE_PRICE_ID and subscription["metadata"].get("line_user") == userId:
#             return {
#                 'status': subscription["status"],
#                 'stripeId': subscription["customer"]
#             }
#     return None

# # Stripeの情報を確認する関数
# def check_subscription_status(userId):
#     return get_subscription_details_for_user(userId, STRIPE_PRICE_ID)

# # データをdbに入れる関数
# def log_to_database(timestamp, sender, userId, stripeId, message):
#     connection = get_connection()
#     cursor = connection.cursor()
#     try:
#         query = """
#         INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message) 
#         VALUES (%s, %s, %s, %s, %s);
#         """
#         cursor.execute(query, (timestamp, sender, userId, stripeId, message))
#         connection.commit()
#     except Exception as e:
#         print(f"Error: {e}")
#         connection.rollback()
#     finally:
#         cursor.close()
#         connection.close()

# if __name__ == "__main__":
#     port = int(os.getenv("PORT", 5000))
#     app.run(host="0.0.0.0", port=port)
