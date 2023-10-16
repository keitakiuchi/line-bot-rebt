from flask import Flask, request, abort
import os
import openai
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
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {OPENAI_API_KEY}'
    }
    data = {
        'model': "gpt-4",
        'messages': [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ]
    }

    response = requests.post(GPT4_API_URL, headers=headers, json=data)
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

    # LINEからのメッセージをログに保存
    log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text)

    response_count = get_system_responses_in_last_24_hours(userId)
    if userId and check_subscription_status(userId) == "active":
        reply_text = generate_gpt4_response(event.message.text)
    else:
        if response_count < 2:
            reply_text = generate_gpt4_response(event.message.text)
        else:
            reply_text = "利用回数の上限に達しました。24時間後に再度お試しください。"

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
        
# # LINEからのメッセージを処理し、必要に応じてStripeの情報も確認します。
# @handler.add(MessageEvent, message=TextMessage)
# def handle_line_message(event):
#     # event.sourceオブジェクトの属性とその値をログに出力
#     for attr in dir(event.source):
#         logging.info(f"Attribute: {attr}, Value: {getattr(event.source, attr)}")

#     # ユーザーからのイベントの場合、ユーザーIDを出力
#     userId = getattr(event.source, 'user_id', None)
#     if userId:
#         logging.info(f"Received message from user ID: {userId}")
#         status = check_subscription_status(userId)
#         if status == "active": # この部分を実際のステータスに合わせて調整してください
#             text = event.message.text
#             reply_text = generate_gpt4_response(text)
#             line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
#         else:
#             # サブスクリプションがactiveでない場合、以下のメッセージを返す
#             reply_text = "利用回数の上限に達しました。明日以降またお待ちしています。"
#             line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
#     else:
#         logging.info("No userId attribute found in source.")

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
# def insert_into_line_bot_logs(timestamp, sender, lineId, stripeId, message):
#     connection = get_connection()
#     cursor = connection.cursor()
#     try:
#         query = """
#         INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message) 
#         VALUES (%s, %s, %s, %s, %s);
#         """
#         cursor.execute(query, (timestamp, sender, lineId, stripeId, message))
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


######################################
## GPT-4 #############################
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

# app = Flask(__name__)

# # 環境変数取得
# YOUR_CHANNEL_ACCESS_TOKEN = os.environ["YOUR_CHANNEL_ACCESS_TOKEN"]
# YOUR_CHANNEL_SECRET = os.environ["YOUR_CHANNEL_SECRET"]
# OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
# handler = WebhookHandler(YOUR_CHANNEL_SECRET)
# OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
# GPT4_API_URL = 'https://api.openai.com/v1/chat/completions'

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
        
# @handler.add(MessageEvent, message=TextMessage)
# def handle_message(event):
#     # Webhookデータをログに出力
#     logging.info(f"Received webhook data: {request.data.decode('utf-8')}")

#     # event.sourceオブジェクトの属性とその値をログに出力
#     for attr in dir(event.source):
#         logging.info(f"Attribute: {attr}, Value: {getattr(event.source, attr)}")

#     # ユーザーからのイベントの場合、ユーザーIDを出力
#     userId = getattr(event.source, 'user_id', None)
#     if userId:
#         logging.info(f"Received message from user ID: {userId}")
#     else:
#         logging.info("No userId attribute found in source.")
    
#     # LINEから受信したテキストメッセージを処理
#     text = event.message.text
#     reply_text = generate_gpt4_response(text)
#     LINE_BOT_API.reply_message(
#         event.reply_token,
#         TextSendMessage(text=reply_text)
#     )


# if __name__ == "__main__":
#     port = int(os.getenv("PORT", 5000))
#     app.run(host="0.0.0.0", port=port)


######################################
## davinci ###########################
# from flask import Flask, request, abort
# import os
# import openai

# from linebot import (
#     LineBotApi, WebhookLINE_BOT_API
# )
# from linebot.exceptions import (
#     InvalidSignatureError
# )
# from linebot.models import (
#     MessageEvent, TextMessage, TextSendMessage,
# )

# app = Flask(__name__)

# # 環境変数取得
# YOUR_CHANNEL_ACCESS_TOKEN = os.environ["YOUR_CHANNEL_ACCESS_TOKEN"]
# YOUR_CHANNEL_SECRET = os.environ["YOUR_CHANNEL_SECRET"]
# OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

#  = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
# LINE_BOT_API = WebhookLINE_BOT_API(YOUR_CHANNEL_SECRET)
# OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# @app.route("/")
# def hello_world():
#     return "hello world!"

# @app.route("/callback", methods=['POST'])
# def callback():
#     # get X-Line-Signature header value
#     signature = request.headers['X-Line-Signature']

#     # get request body as text
#     body = request.get_data(as_text=True)
#     app.logger.info("Request body: " + body)

#     # handle webhook body
#     try:
#         handler.handle(body, signature)
#     except InvalidSignatureError:
#         abort(400)

#     return 'OK'

# @handler.add(MessageEvent, message=TextMessage)
# def handle_message(event):
#     # LINEからのメッセージをログに出力
#     app.logger.info("Received message from LINE: " + event.message.text)
#     response = openai.Completion.create(
#     engine="davinci",
#     prompt=event.message.text,
#     max_tokens=150
#   )
#   generated_response = response.choices[0].text.strip()

#     # LINEに応答を送信
#     .reply_message(
#         event.reply_token,
#         TextSendMessage(text=generated_response)
#     )

# if __name__ == "__main__":
#     port = int(os.getenv("PORT", 5000))
#     app.run(host="0.0.0.0", port=port)

## GPTなし ##########################
# from flask import Flask, request, abort
# import os
# import openai

# from linebot import (
#     LineBotApi, WebhookLINE_BOT_API
# )
# from linebot.exceptions import (
#     InvalidSignatureError
# )
# from linebot.models import (
#     MessageEvent, TextMessage, TextSendMessage,
# )

# app = Flask(__name__)

# #環境変数取得
# YOUR_CHANNEL_ACCESS_TOKEN = os.environ["YOUR_CHANNEL_ACCESS_TOKEN"]
# YOUR_CHANNEL_SECRET = os.environ["YOUR_CHANNEL_SECRET"]

#  = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
# LINE_BOT_API = WebhookLINE_BOT_API(YOUR_CHANNEL_SECRET)

# @app.route("/")
# def hello_world():
#     return "hello world!"

# @app.route("/callback", methods=['POST'])
# def callback():
#     # get X-Line-Signature header value
#     signature = request.headers['X-Line-Signature']

#     # get request body as text
#     body = request.get_data(as_text=True)
#     app.logger.info("Request body: " + body)

#     # handle webhook body
#     try:
#         handler.handle(body, signature)
#     except InvalidSignatureError:
#         abort(400)

#     return 'OK'

# @handler.add(MessageEvent, message=TextMessage)
# def handle_message(event):
#     LINE_BOT_API.reply_message(
#         event.reply_token,
#         TextSendMessage(text=event.message.text))

# if __name__ == "__main__":
# #    app.run()
#     port = int(os.getenv("PORT"))
#     app.run(host="0.0.0.0", port=port)
