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
        # First, understand the user's statement and paraphrase it in one sentence, adding one meaning to the statement (This is called listen-back 1). \n
        # Second, after the user replies to that listen-back 1 (e.g., "yes"), you rephrase the reply in one sentence, adding one more meaning to the reply (this is called listen-back 2). \n
        # Third, after listen-back 2 and receiving the user's response (e.g., "yes"), you can finally ask the question. A list of questions will be provided later. \n
        # Fourth, after the user answers your question, rephrase the answer in one sentence, adding one meaning to the answer (this is listen-back 1). \n
        # Fifth, ask your next question after the user's response (e.g., "Yes"), after listen-back 1 and listen-back 2, sandwiched between the user's responses (e.g., "Yes"). In other words, after asking one question, you must not ask another question until you have received the user's response, following your listen-back 1, the next user's response, and your listen-back 2. \n\n
        # The list of questions is as follows. Please ask the questions in this order: \n\n
        Steps = {
        1. Firstly, start by asking a question that clarifies my problem.\n
        2. Secondly, rephrase my response in one sentence, and then tell it back to me, adding one more meaning, imagining my feelings and situation.\n
        3. Thirdly, rephrase my response (e.g. "yes") in one sentence again, and then tell it back to me, adding another meaning, imagining my feelings and situation.\n
        4. Fourthly, inquire about how I'd envision the ideal outcome.\n
        5. Fifthly, rephrase my response in one sentence, and then tell it back to me, adding one more meaning, imagining my feelings and situation.\n
        6. Sixthly, rephrase my response (e.g. "yes") in one sentence again, and then tell it back to me, adding another meaning, imagining my feelings and situation.\n
        7. Seventhly, proceed by asking about the minor steps I've already taken.\n
        8. Eighthly, rephrase my response in one sentence, and then tell it back to me, adding one more meaning, imagining my feelings and situation.\n
        9. Ninthly, follow up by exploring other actions I'm currently undertaking.\n
        10. Tenthly, rephrase my response in one sentence, and then tell it back to me, adding one more meaning, imagining my feelings and situation.\n
        11. Eleventhly, delve into potential resources that could aid in achieving my goals.\n
        12. Twelfthly, rephrase my response in one sentence, and then tell it back to me, adding one more meaning, imagining my feelings and situation.\n
        13. Thirteenthly, rephrase my response (e.g. "yes") in one sentence again, and then tell it back to me, adding another meaning, imagining my feelings and situation.\n
        14. Fourteenthly, discuss the immediate actions I can take to move closer to my aspirations.\n
        15. Fifteenthly, rephrase my response in one sentence, and then tell it back to me, adding one more meaning, imagining my feelings and situation.\n
        16. Sixteenthly, rephrase my response (e.g. "yes") in one sentence again, and then tell it back to me, adding another meaning, imagining my feelings and situation.\n
        17. Seventeenthly, encourage me to identify the very first step in that direction.\n
        18. Eighteenthly, rephrase my response in one sentence, and then tell it back to me, adding one more meaning, imagining my feelings and situation.\n
        19. Lastly, conclude your consultation with a positive message.\n
        }\n
        # Examples = [
        #     {"prompt": """"""
        #         User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\n
        #         """""",
        #      "completion": "You are so busy that you want to manage to get some sleep."},
        #     {"prompt": """"""
        #         User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\nYou: You are so busy that you want to manage to get some sleep.\nUser: Yes.\n
        #         """""", "completion": "In what way do you have problems when you get less sleep?"},
        #     {"prompt": """"""
        #         User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\n
        #         You: You are so busy that you want to manage to get some sleep.\nUser: Yes.\nYou: In what way do you have problems when you get less sleep?\n
        #         User: I get sick when I get less sleep.\nYou: You are worried about getting sick.\nUser: Yes.\nYou: You feel that sleep time is important to stay healthy.\n
        #         User: That is right.\n
        #         """""", "completion": "What do you hope to become?"},
        #     {"prompt": """"""
        #         User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\n
        #         You: You are so busy that you want to manage to get some sleep.\nUser: Yes.\nYou: In what way do you have problems when you get less sleep?\n
        #         User: I get sick when I get less sleep.\nYou: You are worried about getting sick.\nUser: Yes.\nYou: You feel that sleep time is important to stay healthy.\n
        #         User: That is right.\nYou: What do you hope to become?\nUser: I want to be free from suffering. But I cannot relinquish responsibility.\n
        #         You: You want to be free from suffering, but at the same time you can't give up your responsibility.\nUser: Exactly.\n
        #         You: You are searching for your own way forward.\nUser: Maybe so.\n
        #         """""", "completion": "When do you think you are getting closer to the path you should be on, even if only a little?"}
        # ]\n
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
    # ここでconversation_historyの内容をログに出力
    app.logger.info("Conversation history sent to OpenAI: " + str(conversation_history))

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

    if event.message.text == "リセット" and userId:
        deactivate_conversation_history(userId)
        reply_text = "記憶を消しました"
    else:
        # 現在のタイムスタンプを取得
        current_timestamp = datetime.datetime.now()

        if userId:
            subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
            stripe_id = subscription_details['stripeId'] if subscription_details else None
            subscription_status = subscription_details['status'] if subscription_details else None

            log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text, True)  # is_activeをTrueで保存

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
        log_to_database(current_timestamp, 'system', userId, stripe_id, reply_text, True)  # is_activeをTrueで保存

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
def log_to_database(timestamp, sender, userId, stripeId, message, is_active=True):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        query = """
        INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message, is_active) 
        VALUES (%s, %s, %s, %s, %s, %s);
        """
        cursor.execute(query, (timestamp, sender, userId, stripeId, message, is_active))
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


## 記憶の実装まで ##
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

# def generate_gpt4_response(prompt, userId):
#     sys_prompt = """
#         You are a counselor. Please follow the steps below to consult with me in Japanese. \n
#         First, understand the user's statement and paraphrase it in one sentence, adding one meaning to the statement (This is called listen-back 1). \n
#         Second, after the user replies to that listen-back 1 (e.g., "yes"), you rephrase the reply in one sentence, adding one more meaning to the reply (this is called listen-back 2). \n
#         Third, after listen-back 2 and receiving the user's response (e.g., "yes"), you can finally ask the question. A list of questions will be provided later. \n
#         Fourth, after the user answers your question, rephrase the answer in one sentence, adding one meaning to the answer (this is listen-back 1). \n
#         Fifth, ask your next question after the user's response (e.g., "Yes"), after listen-back 1 and listen-back 2, sandwiched between the user's responses (e.g., "Yes"). In other words, after asking one question, you must not ask another question until you have received the user's response, following your listen-back 1, the next user's response, and your listen-back 2. \n\n
#         The list of questions is as follows. Please ask the questions in this order: \n
#         1: a question that clarifies the user's problem. \n
#         2: a question asking what the user would like it to look like. \n
#         3: a question that asks what the user can do a little bit now. \n
#         4: a question that asks what else the user is already doing. \n
#         5: a question asking about resources that might be useful for the user's desired future. \n
#         6: a question about the user's first steps to get even closer to the desired future than they are now. \n
#         7: a question asking what the user might be able to do to take the first step. \n
#         Examples = [
#             {"prompt": """"""
#                 User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\n
#                 """""",
#              "completion": "You are so busy that you want to manage to get some sleep."},
#             {"prompt": """"""
#                 User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\nYou: You are so busy that you want to manage to get some sleep.\nUser: Yes.\n
#                 """""", "completion": "In what way do you have problems when you get less sleep?"},
#             {"prompt": """"""
#                 User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\n
#                 You: You are so busy that you want to manage to get some sleep.\nUser: Yes.\nYou: In what way do you have problems when you get less sleep?\n
#                 User: I get sick when I get less sleep.\nYou: You are worried about getting sick.\nUser: Yes.\nYou: You feel that sleep time is important to stay healthy.\n
#                 User: That is right.\n
#                 """""", "completion": "What do you hope to become?"},
#             {"prompt": """"""
#                 User: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\n
#                 You: You are so busy that you want to manage to get some sleep.\nUser: Yes.\nYou: In what way do you have problems when you get less sleep?\n
#                 User: I get sick when I get less sleep.\nYou: You are worried about getting sick.\nUser: Yes.\nYou: You feel that sleep time is important to stay healthy.\n
#                 User: That is right.\nYou: What do you hope to become?\nUser: I want to be free from suffering. But I cannot relinquish responsibility.\n
#                 You: You want to be free from suffering, but at the same time you can't give up your responsibility.\nUser: Exactly.\n
#                 You: You are searching for your own way forward.\nUser: Maybe so.\n
#                 """""", "completion": "When do you think you are getting closer to the path you should be on, even if only a little?"}
#         ]\n
#         Please use this procedure to get on the active listening in Japanese.
#         """

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
#     app.logger.info("Conversation history sent to OpenAI: " + str(conversation_history))

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

#     if event.message.text == "キャンセル" and userId:
#         deactivate_conversation_history(userId)
#         reply_text = "記憶を消しました"
#     else:
#         # 現在のタイムスタンプを取得
#         current_timestamp = datetime.datetime.now()

#         if userId:
#             subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
#             stripe_id = subscription_details['stripeId'] if subscription_details else None
#             subscription_status = subscription_details['status'] if subscription_details else None

#             log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text, True)  # is_activeをTrueで保存

#             if subscription_status == "active":
#                 reply_text = generate_gpt4_response(event.message.text, userId)
#             else:
#                 response_count = get_system_responses_in_last_24_hours(userId)
#                 if response_count < 2: 
#                     reply_text = generate_gpt4_response(event.message.text, userId)
#                 else:
#                     reply_text = "利用回数の上限に達しました。24時間後に再度お試しください。"
#         else:
#             reply_text = "エラーが発生しました。"

#         # メッセージをログに保存
#         log_to_database(current_timestamp, 'system', userId, stripe_id, reply_text, True)  # is_activeをTrueで保存

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
# def log_to_database(timestamp, sender, userId, stripeId, message, is_active=True):
#     connection = get_connection()
#     cursor = connection.cursor()
#     try:
#         query = """
#         INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message, is_active) 
#         VALUES (%s, %s, %s, %s, %s, %s);
#         """
#         cursor.execute(query, (timestamp, sender, userId, stripeId, message, is_active))
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
#         ORDER BY timestamp DESC LIMIT 5;
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
