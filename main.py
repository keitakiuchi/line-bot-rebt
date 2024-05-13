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
    # app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

sys_prompt = """
        You are playing the role of a well praising, supportive, Japanese speaking counselor. Here's the specific method you must use during the conversation:
        Listen-Back 1: After the user makes a statement, you should paraphrase it into a single sentence, while also adding a new nuance or interpretation to it.\n
        Wait for the user's reply to your Listen-Back 1 (for instance, they might say only "yes").\n
        Listen-Back 2: After receiving the user's response, you will then further paraphrase their reply, once again condensing it into one sentence and adding another layer of meaning or interpretation.\n
        Once you've done Listen-Back 1 and Listen-Back 2 and received a response from the user, you may then pose a question. You will be given specific questions to ask later.\n
        After the user answers your question, return to Listen-Back 1 - paraphrase their answer in one sentence and introduce a new nuance or interpretation.\n
        You can ask your next question only after:
        Receiving a response to your Listen-Back 1,
        Providing your Listen-Back 2, and
        Getting another response from the user.
        In essence, you should never ask consecutive questions. There should always be a pattern of Listen-Back 1, user response, Listen-Back 2, and another user response before you can move on to the next question.
        Please ask the questions in the order below.\n
        Order_of_questions = {
        1: Start by asking me a question that I find particularly troubling about it.\n
        2: Then, inquire about how I'd envision the ideal outcome.\n
        3: Proceed by asking about what little I've already done\n
        4: Follow up by exploring other actions I'm currently undertaking.\n
        5: Delve into potential resources that could aid in achieving my goals.\n
        6: Discuss the immediate actions I can take to move closer to my aspirations.\n
        7: Lastly, encourage me to complete the very first step in that direction with some positive feedbacks, and asking if you can close the conversation.\n
        }\n
        Examples = [
            {"prompt": """"""
                User: I'm so busy I don't even have time to sleep. \n
                You: You are having trouble getting enough sleep. \n
                User: Yes. \n
                You: You are so busy that you want to manage to get some sleep. \n
                User: Yes. \n
                """""",
             "completion": "In what way do you have problems when you get less sleep?"},
            {"prompt": """"""
                User: I get sick when I get less sleep. \n
                You: You are worried about getting sick. \n
                User: Yes. \n
                You: You feel that sleep time is important to stay healthy. \n
                User: That is right. \n
                """""", "completion": "What do you hope to become?"},
            {"prompt": """"""
                User: I want to be free from suffering. But I cannot relinquish responsibility. \n
                You: You want to be free from suffering, but at the same time you can't give up your responsibility. \n
                User: Exactly. \n
                You: You are searching for your own way forward. \n
                User: Maybe so. \n
                """""", "completion": "When do you think you are getting closer to the path you should be on, even if only a little?"}
        ]\n
        Please follow the above procedures strictly for consultation.
        """

def generate_gpt4_response(prompt, userId):
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
        'model': "gpt-4o",
        'messages': conversation_history,
        'temperature': 1
    }
    # ここでconversation_historyの内容をログに出力
    # app.logger.info("Conversation history sent to : " + str(conversation_history))
    # 旧："gpt-4-1106-preview"

    try:
        response = requests.post(GPT4_API_URL, headers=headers, json=data)
        response.raise_for_status()  # Check if the request was successful
        response_json = response.json() # This line has been moved here
        # Add this line to log the response from  API
        # app.logger.info("Response from  API: " + str(response_json))
        return response_json['choices'][0]['message']['content'].strip()
    except requests.RequestException as e:
        # app.logger.error(f" API request failed: {e}")
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

def deactivate_conversation_history(userId):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        query = """
        UPDATE line_bot_logs SET is_active=FALSE 
        WHERE lineId=%s;
        """
        cursor.execute(query, (userId,))
        connection.commit()
    except Exception as e:
        print(f"Error: {e}")
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

# LINEからのメッセージを処理し、必要に応じてStripeの情報も確認します。
@handler.add(MessageEvent, message=TextMessage)
def handle_line_message(event):
    userId = getattr(event.source, 'user_id', None)

    if event.message.text == "スタート" and userId:
        deactivate_conversation_history(userId)
        reply_text = "頼りにしてくださりありがとうございます。今日はどんなお話をうかがいましょうか？"
    else:
        # 現在のタイムスタンプを取得
        current_timestamp = datetime.datetime.now()

        if userId:
            subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
            stripe_id = subscription_details['stripeId'] if subscription_details else None
            subscription_status = subscription_details['status'] if subscription_details else None

            log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text, True, sys_prompt)  # is_activeをTrueで保存

            if subscription_status == "active": ####################本番はactive################
                reply_text = generate_gpt4_response(event.message.text, userId)
            else:
                response_count = get_system_responses_in_last_24_hours(userId)
                if response_count < 5: 
                    reply_text = generate_gpt4_response(event.message.text, userId)
                else:
                    reply_text = "利用回数の上限に達しました。24時間後に再度お試しください。こちらから回数無制限の有料プランに申し込むこともできます：https://line-login-3fbeac7c6978.herokuapp.com/"
        else:
            reply_text = "エラーが発生しました。"

        # メッセージをログに保存
        log_to_database(current_timestamp, 'system', userId, stripe_id, reply_text, True, sys_prompt)  # is_activeをTrueで保存

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
def log_to_database(timestamp, sender, userId, stripeId, message, is_active=True, sys_prompt=''):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        query = """
        INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message, is_active, sys_prompt) 
        VALUES (%s, %s, %s, %s, %s, %s, %s);
        """
        cursor.execute(query, (timestamp, sender, userId, stripeId, message, is_active, sys_prompt))
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
        WHERE lineId=%s AND is_active=TRUE 
        ORDER BY timestamp DESC LIMIT 10;
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


## 旧 ##
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
#     # app.logger.info("Request body: " + body)
#     try:
#         handler.handle(body, signature)
#     except InvalidSignatureError:
#         abort(400)
#     return 'OK'

# sys_prompt = """
#         You are playing the role of a well praising, supportive, Japanese speaking counselor. Here's the specific method you must use during the conversation:
#         Listen-Back 1: After the user makes a statement, you should paraphrase it into a single sentence, while also adding a new nuance or interpretation to it.\n
#         Wait for the user's reply to your Listen-Back 1 (for instance, they might say only "yes").\n
#         Listen-Back 2: After receiving the user's response, you will then further paraphrase their reply, once again condensing it into one sentence and adding another layer of meaning or interpretation.\n
#         Once you've done Listen-Back 1 and Listen-Back 2 and received a response from the user, you may then pose a question. You will be given specific questions to ask later.\n
#         After the user answers your question, return to Listen-Back 1 - paraphrase their answer in one sentence and introduce a new nuance or interpretation.\n
#         You can ask your next question only after:
#         Receiving a response to your Listen-Back 1,
#         Providing your Listen-Back 2, and
#         Getting another response from the user.
#         In essence, you should never ask consecutive questions. There should always be a pattern of Listen-Back 1, user response, Listen-Back 2, and another user response before you can move on to the next question.
#         Please ask the questions in the order below.\n
#         Order_of_questions = {
#         1: Start by asking me a question that I find particularly troubling about it.\n
#         2: Then, inquire about how I'd envision the ideal outcome.\n
#         3: Proceed by asking about what little I've already done\n
#         4: Follow up by exploring other actions I'm currently undertaking.\n
#         5: Delve into potential resources that could aid in achieving my goals.\n
#         6: Discuss the immediate actions I can take to move closer to my aspirations.\n
#         7: Lastly, encourage me to complete the very first step in that direction with some positive feedbacks, and asking if you can close the conversation.\n
#         }\n
#         Examples = [
#             {"prompt": """"""
#                 User: I'm so busy I don't even have time to sleep. \n
#                 You: You are having trouble getting enough sleep. \n
#                 User: Yes. \n
#                 You: You are so busy that you want to manage to get some sleep. \n
#                 User: Yes. \n
#                 """""",
#              "completion": "In what way do you have problems when you get less sleep?"},
#             {"prompt": """"""
#                 User: I get sick when I get less sleep. \n
#                 You: You are worried about getting sick. \n
#                 User: Yes. \n
#                 You: You feel that sleep time is important to stay healthy. \n
#                 User: That is right. \n
#                 """""", "completion": "What do you hope to become?"},
#             {"prompt": """"""
#                 User: I want to be free from suffering. But I cannot relinquish responsibility. \n
#                 You: You want to be free from suffering, but at the same time you can't give up your responsibility. \n
#                 User: Exactly. \n
#                 You: You are searching for your own way forward. \n
#                 User: Maybe so. \n
#                 """""", "completion": "When do you think you are getting closer to the path you should be on, even if only a little?"}
#         ]\n
#         Please follow the above procedures strictly for consultation.
#         """

# def generate_gpt4_response(prompt, userId):
#     headers = {
#         'Content-Type': 'application/json',
#         'Authorization': f'Bearer {OPENAI_API_KEY}'
#     }
#     # 過去の会話履歴を取得
#     conversation_history = get_conversation_history(userId)
#     # sys_promptを会話の最初に追加
#     conversation_history.insert(0, {"role": "system", "content": sys_prompt})
#     # ユーザーからの最新のメッセージを追加
#     conversation_history.append({"role": "user", "content": prompt})

#     data = {
#         'model': "gpt-4",
#         'messages': conversation_history,
#         'temperature': 1
#     }
#     # ここでconversation_historyの内容をログに出力
#     # app.logger.info("Conversation history sent to OpenAI: " + str(conversation_history))

#     try:
#         response = requests.post(GPT4_API_URL, headers=headers, json=data)
#         response.raise_for_status()  # Check if the request was successful
#         response_json = response.json() # This line has been moved here
#         # Add this line to log the response from OpenAI API
#         # app.logger.info("Response from OpenAI API: " + str(response_json))
#         return response_json['choices'][0]['message']['content'].strip()
#     except requests.RequestException as e:
#         # app.logger.error(f"OpenAI API request failed: {e}")
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

# def deactivate_conversation_history(userId):
#     connection = get_connection()
#     cursor = connection.cursor()
#     try:
#         query = """
#         UPDATE line_bot_logs SET is_active=FALSE 
#         WHERE lineId=%s;
#         """
#         cursor.execute(query, (userId,))
#         connection.commit()
#     except Exception as e:
#         print(f"Error: {e}")
#         connection.rollback()
#     finally:
#         cursor.close()
#         connection.close()

# # LINEからのメッセージを処理し、必要に応じてStripeの情報も確認します。
# @handler.add(MessageEvent, message=TextMessage)
# def handle_line_message(event):
#     userId = getattr(event.source, 'user_id', None)

#     if event.message.text == "スタート" and userId:
#         deactivate_conversation_history(userId)
#         reply_text = "頼りにしてくださりありがとうございます。今日はどんなお話をうかがいましょうか？"
#     else:
#         # 現在のタイムスタンプを取得
#         current_timestamp = datetime.datetime.now()

#         if userId:
#             subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
#             stripe_id = subscription_details['stripeId'] if subscription_details else None
#             subscription_status = subscription_details['status'] if subscription_details else None

#             log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text, True, sys_prompt)  # is_activeをTrueで保存

#             if subscription_status == "active": ####################本番はactive################
#                 reply_text = generate_gpt4_response(event.message.text, userId)
#             else:
#                 response_count = get_system_responses_in_last_24_hours(userId)
#                 if response_count < 5: 
#                     reply_text = generate_gpt4_response(event.message.text, userId)
#                 else:
#                     reply_text = "利用回数の上限に達しました。24時間後に再度お試しください。こちらから回数無制限の有料プランに申し込むこともできます：https://line-login-3fbeac7c6978.herokuapp.com/"
#         else:
#             reply_text = "エラーが発生しました。"

#         # メッセージをログに保存
#         log_to_database(current_timestamp, 'system', userId, stripe_id, reply_text, True, sys_prompt)  # is_activeをTrueで保存

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
# def log_to_database(timestamp, sender, userId, stripeId, message, is_active=True, sys_prompt=''):
#     connection = get_connection()
#     cursor = connection.cursor()
#     try:
#         query = """
#         INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message, is_active, sys_prompt) 
#         VALUES (%s, %s, %s, %s, %s, %s, %s);
#         """
#         cursor.execute(query, (timestamp, sender, userId, stripeId, message, is_active, sys_prompt))
#         connection.commit()
#     except Exception as e:
#         print(f"Error: {e}")
#         connection.rollback()
#     finally:
#         cursor.close()
#         connection.close()

# # 会話履歴を参照する関数
# def get_conversation_history(userId):
#     connection = get_connection()
#     cursor = connection.cursor()
#     conversations = []

#     try:
#         query = """
#         SELECT sender, message FROM line_bot_logs 
#         WHERE lineId=%s AND is_active=TRUE 
#         ORDER BY timestamp DESC LIMIT 10;
#         """
#         cursor.execute(query, (userId,))
        
#         results = cursor.fetchall()
#         for result in results:
#             role = 'user' if result[0] == 'user' else 'assistant'
#             conversations.append({"role": role, "content": result[1]})
#     except Exception as e:
#         print(f"Error: {e}")
#     finally:
#         cursor.close()
#         connection.close()

#     # 最新の会話が最後に来るように反転
#     return conversations[::-1]

# if __name__ == "__main__":
#     port = int(os.getenv("PORT", 5000))
#     app.run(host="0.0.0.0", port=port)
