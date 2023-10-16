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

def generate_gpt4_response(prompt, userId):
    sys_prompt = """
        You are a counselor. Please follow the steps below to consult with me in Japanese. \n
        First, understand the user's statement and paraphrase it in one sentence, adding one meaning to the statement (This is called listen-back 1). \n
        Second, after the user replies to that listen-back 1 (e.g., "yes"), you rephrase the reply in one sentence, adding one more meaning to the reply (this is called listen-back 2). \n
        Third, after listen-back 2 and receiving the user's response (e.g., "yes"), you can finally ask the question. In other words, you ask a question after every two turns of exchange between the user and you. A list of questions will be provided later. \n
        Fourth, after the user answers your question, rephrase the answer in one sentence, adding one meaning to the answer (this is listen-back 1). \n
        Fifth, after the user replies (e.g., "yes") to listen-back 1, you rephrase the response in one sentence with one additional meaning (this is listen-back 2). This means that after you asked one question, you would do two turns of the exchange, responding to the user's answer with a listen-back. \n
        Sixth, ask your next question after the user's response (e.g., "Yes"), after listen-back 1 and listen-back 2, sandwiched between the user's responses (e.g., "Yes"). In other words, after asking one question, you must not ask another question until you have received the user's response, following your listen-back 1, the next user's response, and your listen-back 2. \n
        Seventh, repeat these steps (your listen-back 1, then the user's reply, then your listen-back 2, then the user's reply, then your question). \n\n
        The list of questions is as follows. Please ask the questions in this order: \n
        1: a question that clarifies the user's problem. \n
        2: a question asking what the user would like it to look like. \n
        3: a question that asks what the user can do a little bit now. \n
        4: a question that asks what else the user is already doing. \n
        5: a question asking about resources that might be useful for the user's desired future. \n
        6: a question about the user's first steps to get even closer to the desired future than they are now. \n
        7: a question asking what the user might be able to do to take the first step. \n
        Examples of correct exchanges are shown below. \n
        Examples: \n
        Example 1: \n
        User: I'm so busy I don't even have time to sleep. \n
        You: You are having trouble getting enough sleep. \n
        User: Yes. \n
        You: You are so busy that you want to manage to get some sleep. \n
        User: Yes. \n
        You: In what way do you have problems when you get less sleep? \n\n
        Example 2:\n
        User: I get sick when I get less sleep. \n
        You: You are worried about getting sick. \n
        User: Yes. \n
        You: You feel that sleep time is important to stay healthy. \n
        User: That is right. \n
        You: What do you hope to become? \n
        Example 3: \n
        User: I want to be free from suffering. But I cannot relinquish responsibility. \n
        You: You want to be free from suffering, but at the same time you can't give up your responsibility. \n
        User: Exactly. \n
        You: You are searching for your own way forward. \n
        User: Maybe so. \n
        You: When do you think you are getting closer to the path you should be on, even if only a little? \n
        Please use this procedure to get on the active listening in Japanese.
        """

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {OPENAI_API_KEY}'
    }
    # 過去の会話履歴を取得
    conversation_history = get_conversation_history(userId)
    # sys_promptを会話の最初に追加
    conversation_history.insert(0, {"role": "system", "content": sys_prompt})
    # ユーザーからの最新のメッセージを追加
    conversation_history.append({"role": "user", "content": prompt})

    data = {
        'model': "gpt-4",
        'messages': conversation_history,
        'temperature': 1
    }

    try:
        response = requests.post(GPT4_API_URL, headers=headers, json=data)
        response.raise_for_status()  # Check if the request was successful
        response_json = response.json() # This line has been moved here
        # Add this line to log the response from OpenAI API
        app.logger.info("Response from OpenAI API: " + str(response_json))
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
            reply_text = generate_gpt4_response(event.message.text, userId)
        else:
            response_count = get_system_responses_in_last_24_hours(userId)
            if response_count < 2: 
                reply_text = generate_gpt4_response(event.message.text, userId)
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

# 会話履歴を参照する関数
def get_conversation_history(userId):
    connection = get_connection()
    cursor = connection.cursor()
    conversations = []

    try:
        query = """
        SELECT sender, message FROM line_bot_logs 
        WHERE lineId=%s AND timestamp > NOW() - INTERVAL '12 HOURS' 
        ORDER BY timestamp DESC LIMIT 5;
        """
        cursor.execute(query, (userId,))
        results = cursor.fetchall()
        for result in results:
            role = 'user' if result[0] == 'user' else 'assistant'
            conversations.append({"role": role, "content": result[1]})
    except Exception as e:
        print(f"Error: {e}")
    finally:
        cursor.close()
        connection.close()
    
    # 最新の会話が最後に来るように反転
    return conversations[::-1]


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


## データベース実装
#####################################
# from flask import Flask, request, abort
# import os
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
#     sys_prompt = """
#         You are a helpful assistant.
#         """
#     headers = {
#         'Content-Type': 'application/json',
#         'Authorization': f'Bearer {OPENAI_API_KEY}'
#     }
#     data = {
#         'model': "gpt-4",
#         'messages': [
#             {"role": "system", "content": sys_prompt},
#             {"role": "user", "content": prompt}
#         ],
#         'temperature': 1
#     }

#     try:
#         response = requests.post(GPT4_API_URL, headers=headers, json=data)
#         response.raise_for_status()  # Check if the request was successful
#         response_json = response.json() # This line has been moved here
#         # Add this line to log the response from OpenAI API
#         app.logger.info("Response from OpenAI API: " + str(response_json))
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
#         subscription_status = subscription_details['status'] if subscription_details else None

#         # LINEからのメッセージをログに保存
#         log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text)

#         # ステータスがactiveなら、利用回数の制限を気にせずに応答
#         if subscription_status == "active":
#             reply_text = generate_gpt4_response(event.message.text)
#         else:
#             response_count = get_system_responses_in_last_24_hours(userId)
#             if response_count < 2: 
#                 reply_text = generate_gpt4_response(event.message.text)
#             else:
#                 reply_text = "利用回数の上限に達しました。24時間後に再度お試しください。"
#     else:
#         reply_text = "エラーが発生しました。"

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
