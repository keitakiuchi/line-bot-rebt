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
    # # Webhookデータをログに出力
    # logging.info(f"Received webhook data: {request.data.decode('utf-8')}")

    # event.sourceオブジェクトの属性とその値をログに出力
    for attr in dir(event.source):
        logging.info(f"Attribute: {attr}, Value: {getattr(event.source, attr)}")

    # ユーザーからのイベントの場合、ユーザーIDを出力
    userId = getattr(event.source, 'user_id', None)
    if userId:
        logging.info(f"Received message from user ID: {userId}")
        # Stripeの情報を確認
        check_subscription_status(userId)
    else:
        logging.info("No userId attribute found in source.")

    # LINEから受信したテキストメッセージを処理
    text = event.message.text
    reply_text = generate_gpt4_response(text)
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# stripeの情報を参照
def get_subscription_status_for_user(userId, STRIPE_PRICE_ID):
    # キャッシュまたはデータベースからユーザのサブスクリプション情報を取得しようとするロジックをここに追加

    # キャッシュに情報がない場合、Stripe APIを呼び出す
    customers = stripe.Customer.list(email=userId)  # 仮にuserIdがemailとして保存されている場合
    for customer in customers:
        if customer.metadata.get('line_id') == userId:
            subscriptions = stripe.Subscription.list(customer=customer.id)
            
            if not subscriptions.data:  # 顧客がサブスクリプションを持っていない場合
                return "idなし"

            for subscription in subscriptions.data:
                if subscription["items"]["data"][0]["price"]["id"] == STRIPE_PRICE_ID:
                    return subscription.status  # activeまたはそれ以外のステータスを返す

    return "idなし"

# Stripeの情報を確認する関数
def check_subscription_status(userId):
    status = get_subscription_status_for_user(userId, STRIPE_PRICE_ID)
    if status == "active":
        logging.info("サブスクリプションはアクティブです。")
    elif status == "idなし":
        logging.info("サブスクリプションのIDがありません。")
    else:
        logging.info(f"サブスクリプションのステータスは{status}です。")

# 以下の関数はメッセージが来たときに呼び出されるとします。
def on_message_received(message):
    userId = message.get('userId') 
    handle_message(userId)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


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
#     # # Webhookデータをログに出力
#     # logging.info(f"Received webhook data: {request.data.decode('utf-8')}")

#     # event.sourceオブジェクトの属性とその値をログに出力
#     for attr in dir(event.source):
#         logging.info(f"Attribute: {attr}, Value: {getattr(event.source, attr)}")

#     # ユーザーからのイベントの場合、ユーザーIDを出力
#     userId = getattr(event.source, 'user_id', None)
#     if userId:
#         logging.info(f"Received message from user ID: {userId}")
#         # Stripeの情報を確認
#         check_subscription_status(userId)
#     else:
#         logging.info("No userId attribute found in source.")

#     # LINEから受信したテキストメッセージを処理
#     text = event.message.text
#     reply_text = generate_gpt4_response(text)
#     line_bot_api.reply_message(
#         event.reply_token,
#         TextSendMessage(text=reply_text)
#     )

# # stripeの情報を参照
# def get_subscription_status_for_user(userId, STRIPE_PRICE_ID):
#     customers = stripe.Customer.list(limit=100)
#     subscriptions = stripe.Subscription.list(limit=10)

#     # 指定された価格IDと一致するサブスクリプションを特定し、関連情報をログに出力
#     for subscription in subscriptions.data:
#         if subscription["items"]["data"][0]["price"]["id"] == STRIPE_PRICE_ID:
#             line_user = subscription["metadata"].get("line_user", "N/A")  # "N/A"はline_userが存在しない場合のデフォルト値
#             status = get_subscription_status_for_user(userId, STRIPE_PRICE_ID)
#             logging.info(f"line_user: {line_user}, status: {status}")

#     # for customer in customers.data:
#     #     logger.info(customer)
#     # for subscription in subscriptions.data:
#     #     logger.info(subscription)
    
#     for customer in customers:
#         if customer.metadata.get('line_id') == userId:
#             subscriptions = stripe.Subscription.list(customer=customer.id)
            
#             if not subscriptions.data:  # 顧客がサブスクリプションを持っていない場合
#                 return "idなし"

#             for subscription in subscriptions.data:
#                 return subscription.status  # activeまたはそれ以外のステータスを返す

#     return "idなし"

# # # Stripeの情報を確認する関数
# # def check_subscription_status(userId):
# #     status = get_subscription_status_for_user(userId, STRIPE_PRICE_ID)
# #     if status == "active":
# #         logging.info("サブスクリプションはアクティブです。")
# #     elif status == "idなし":
# #         logging.info("サブスクリプションのIDがありません。")
# #     else:
# #         logging.info(f"サブスクリプションのステータスは{status}です。")

# # # 以下の関数はメッセージが来たときに呼び出されるとします。
# # def on_message_received(message):
# #     userId = message.get('userId') 
# #     handle_message(userId)

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
