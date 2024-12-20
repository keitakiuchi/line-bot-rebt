from flask import Flask, request, abort
import os
import re
from uuid import uuid4
from datetime import datetime, timedelta
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
from psycopg2.extras import RealDictCursor
from typing import Dict, Any
from fastapi import Request
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.runnables import RunnableLambda
from langchain.schema.runnable.utils import ConfigurableFieldSpec
from langchain_community.chat_message_histories import ChatMessageHistory

app = Flask(__name__)

import redis
from urllib.parse import urlparse
# Redis クライアントの初期化
# REDIS_TLS_URL を優先的に使用し、なければ REDIS_URL を使用
# Redis クライアントの初期化（グローバルスコープ）
redis_url = os.environ.get("REDIS_TLS_URL") or os.environ.get("REDIS_URL")
url = urlparse(redis_url)
redis_client = redis.Redis(
    host=url.hostname,
    port=url.port,
    password=url.password,
    ssl=(url.scheme == "rediss"),
    ssl_cert_reqs=None
)

# 接続テスト
try:
    redis_client.ping()
    logger.info("Successfully connected to Redis")
except redis.ConnectionError as e:
    logger.error(f"Failed to connect to Redis: {e}")
    raise
except Exception as e:
    logger.error(f"Failed to initialize Redis client: {str(e)}")
    raise

# 環境変数取得
YOUR_CHANNEL_ACCESS_TOKEN = os.environ["YOUR_CHANNEL_ACCESS_TOKEN"]
YOUR_CHANNEL_SECRET = os.environ["YOUR_CHANNEL_SECRET"]
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]

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

# グローバル変数として sys_prompt を定義（または handle_line_message 内で初期化）
current_prompt = ""

###### LangChain ######
def _per_request_config_modifier(config: Dict[str, Any], userId: str) -> Dict[str, Any]:
    config = config.copy()

    if "configurable" not in config:
        config["configurable"] = {}

    config["configurable"]["conversation_id"] = userId  # conversation_idとしてlineidを使用
    config["configurable"]["user_id"] = userId

    return config

# def _per_request_config_modifier(config: Dict[str, Any], userId: str) -> Dict[str, Any]:
#     """Update the config with userId"""
#     config = config.copy()

#     # "configurable"キーが存在しない場合は新しく作成する
#     if "configurable" not in config:
#         config["configurable"] = {}

#     config["configurable"]["conversation_id"] = "test0902"
#     config["configurable"]["user_id"] = userId

#     return config

# PostgreSQLの設定
db_config = {
    'host': os.environ['DB_HOST'],
    'port': 5432,
    'user': os.environ['DB_USER'],
    'password': os.environ['DB_PASS'],
    'database': os.environ['DB_NAME'],
}

# データベースからメッセージ履歴を取得する関数
def get_session_history(user_id: str,
                        conversation_id: str = None) -> BaseChatMessageHistory:
    if conversation_id is None:
        conversation_id = user_id

    with psycopg2.connect(**db_config, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            # cur.execute('SELECT sender, message FROM line_bot_logs WHERE Lineid = %s ORDER BY timestamp DESC, id DESC', 
            #             (conversation_id,))
            # 直近41件のメッセージを取得するクエリに変更
            cur.execute('SELECT sender, message FROM line_bot_logs WHERE Lineid = %s AND is_active = TRUE ORDER BY id DESC LIMIT 41', 
                         (conversation_id,))
            rows = cur.fetchall()

            # メッセージを逆順から元の順序に戻す
            rows.reverse()
            
            chat_history = ChatMessageHistory()
            for row in rows:
                role = 'assistant' if row['sender'] == 'system' else 'user'
                chat_history.add_message({"role": role, "content": row['message']})
            
            # # デバッグ用: 追加された履歴を出力
            # print("Chat history being returned:", chat_history.messages)
            return chat_history

# def get_session_history(user_id: str,
#                         conversation_id: str = None) -> BaseChatMessageHistory:
#     # conversation_id が指定されていない場合は user_id を使用する
#     if conversation_id is None:
#         conversation_id = user_id

#     with psycopg2.connect(**db_config, cursor_factory=RealDictCursor) as conn:
#         with conn.cursor() as cur:
#             cur.execute('SELECT * FROM line_bot_logs WHERE Lineid = %s ORDER BY Timestamp ASC', 
#                         (conversation_id,))
#             rows = cur.fetchall()
#             chat_history = ChatMessageHistory()
#             for row in rows:
#                 chat_history.add_message(row['message'])  # roleは不要ならば削除
#             return chat_history

# ルートモデル選択
model_root = ChatGoogleGenerativeAI(
    temperature=0,
    model="gemini-1.5-flash",
    top_p=0.95,
    top_k=64,
)
# model_root = ChatOpenAI(temperature=0, model="gpt-4o-mini")

# 応答モデル選択
# model_name="gemini-1.5-flash"
model_name = "gpt-4o-2024-08-06"
# model_name="claude-3-5-sonnet-20240620"

if model_name.startswith("gemini"):
    model_response = ChatGoogleGenerativeAI(temperature=1, model=model_name)
elif model_name.startswith("gpt"):
    model_response = ChatOpenAI(temperature=1, model=model_name)
elif model_name.startswith("claude"):
    model_response = ChatAnthropic(temperature=1, model=model_name)
else:
    raise ValueError("Unknown model name")

root_prompt = f"""
ユーザの入力: {{input}}
あなたはユーザの入力が「質問」なのか、相談のフローを進めるための「返答」なのかを判断するためのAIエージェントです。上記のユーザの入力について、回答を求める質問だったら"question"、
それ以外だったら"other"と出力しください。ユーザの入力の文体にとらわれずに、下記の対話履歴を加味して、相談のフローを継続すべきと思われる場合には、"other"として、
相談のフローをいったん止めて質問に答えるべきと思われる場合には"question"と判断してください。

出力は"question"か"other"のどちらかのみ出力し、それ以外の言葉は出力しないでください。

下記の対話履歴は、文脈理解の参考のためのものです。aiの返答は参考にしないでください。あなたは"question"か"other"のどちらかのみを、必ず出力してください。
"""

# chain = (PromptTemplate.from_template(root_prompt)
#          | model_root
#          | StrOutputParser())

chain = (
    ChatPromptTemplate.from_messages([
        (
            "system",
            root_prompt,
        ),
        MessagesPlaceholder(variable_name="history"),
        # ("human", "{input}"),
    ])
    | model_root
    | StrOutputParser())

chain_memory = RunnableWithMessageHistory(
    chain,
    get_session_history,
    input_messages_key="input",
    history_messages_key="history",
    history_factory_config=[
        ConfigurableFieldSpec(
            id="user_id",
            annotation=str,
            name="User ID",
            description="Unique identifier for the user.",
            default="",
            is_shared=True,
        ),
        ConfigurableFieldSpec(
            id="conversation_id",
            annotation=str,
            name="Conversation ID",
            description="Unique identifier for the conversation.",
            default="",
            is_shared=True,
        ),
    ],
)

# 分岐先1：聞き返し
reflection_prompt = f"""
あなたはRational Emotive Behavior Therapy（REBT）を専門とするAIアシスタントです。あなたの仕事は、REBTの問題、イラショナルな信念とラショナルな信念、REBTの感情理論、
REBTのテクニックに基づいて、REBTのフローに沿って「ユーザーの入力」に応答することです。必要なとき以外質問はせずに、共感的なリフレクションを提供することに集中してください。

REBT理論の概要
1. REBTが扱う問題
   - 状況そのものではなく、状況に対する個人の受け止め方や感じ方に焦点を当てる。
   - 不合理な信念に起因する感情的問題に対処する。
   - ストレスを軽減し、効果的な反応を改善することを目指す。

2. イラショナルな信念とラショナルな信念：
   - イラショナルな信念は、絶対的な要求と、破局視、フラストレーション耐性の低さ、自己卑下、他者卑下が組み合わさっている。
   - イラショナルな信念をラショナルな信念に変える：
     - 絶対的な要求を現実的な願望に
     - 破局化を非破局化へ
     - 低い欲求不満耐性を高い欲求不満耐性へ
     - 自己卑下を自己受容へ
     - 他者卑下を他者受容へ

3. REBTの感情理論：
   - 主な問題感情：不安、抑うつ、恥、罪悪感、不健康な怒り、傷心、嫉妬、妬み。
   - それぞれの感情は特定のイラショナルな信念と結びつきがち。

4. REBTの技法：
   - 必要なとき以外質問をしない。
   - 質問よりも共感的なリフレクション、つまりユーザの入力を言い換え、気持ち、思い、状況についての解釈を交えて伝え返すことを使う。
   - 各フローのステップで、利用者と少なくとも3回のやりとりをする。
   - 過去よりも未来に焦点を当てる。
   - 弱みよりも強みと資源を強調する。
   - 利用者の長所や資源を褒める。
   - 全般的に進むべき方向を提案するのではなく、先にユーザの考えを聞く。その上で、必要があれば提案し、さらに、その提案に対するユーザの認識を確認する
   - 応答は簡潔に2文以内で

REBTのフロー・ステップ
1. 導入: 声をかけてくれたことを労い、あいさつをし、ユーザの入力を踏まえて今日取り組みたいことについてたずねる。
2. 困りごとの明確化: ユーザが何で困っているかを明らかにする。
3. どうなりたいかの明確化: REBTの理論の扱う問題を踏まえて、ユーザがどうなることを望んでいるのかを明らかにする。
4. 問題の原因となる感情の特定: REBTの理論の感情の説明に基づき、問題の原因となっている感情、目標達成の妨げとなっている感情を明らかにする。
5. 場面の特定: 過度な感情が生じた場面を特定する。
6. 結果の明確化: 5の場面で、過度な感情によって生じた問題行動と体の不調を明らかにする。
7. 考えの確認: 5の場面で、過度な感情の原因となっている考えについて確認する。
8. REBT理論の説明: REBTでは、「～でなければならない」、「もし～だったら最悪だ」、「もし～だったら私はダメ人間だ」といったイラショナルな信念が過度な感情を引き起こし、目標達成を妨げていると考えるというREBTの理論を説明し、理解を確認する。
9. 絶対的な要求の特定: REBTの理論の信念の説明に基づいて、問題となっている過度な感情の原因である絶対的な要求（～であってはならない、～でなければならないなど）を特定する。
10. ネガティブな評価の特定: REBTの理論の信念の説明に基づいて、問題となっている過度な感情の原因であるネガティブな評価（～であったら最悪、～は耐えられない、私はダメ人間、あの人はダメ人間など）を特定する。
11. イラショナルな信念の特定：絶対的な要求とネガティブな評価をあわせたイラショナルな信念が実感に合っているか確認し、必要に応じて修正する
12. ABCの確認: 4の場面でイラショナルな信念を持つことで、過度な感情が生じ、問題行動が発生していることを確認する。
13. 認知再構成に向けた合意: イラショナルな信念が問題を引き起こしていて、それを変えることが目標達成の役に立つことを確認する。扱うべき場面、信念、感情が合っているか確認し、間違っていたら、再度、目標達成のために扱うべき場面、信念、感情を特定する。
14. 認知再構成: イラショナルな信念をどのように変えればいいと思うかたずねる。返答を踏まえて、REBTの理論の信念の説明に基づいて、イラショナルな信念に変わるラショナルな信念について話し合う。ただし、この段階ではラショナルな信念を持てた方がいいだろうと頭で理解できれば十分で、納得感が低くてもかまわない。
15. ラショナルな信念の吟味：提案されたラショナルな信念が問題になっている4の過度な感情を緩め、6の問題行動や体の反応を和らげるのに役立ちそうか確認し、必要に応じて修正する
16. 確信を強める: ラショナルな信念への確信を強めるための問答。
17. イメージで検証: 5の場面でイラショナルな信念を持った時の結末と、ラショナルな信念を持った時の結末をイメージして比較してみるように促す。そして、ラショナルな信念の有効性を確認する。
18. 練習の必要性: ラショナルな信念よりもイラショナルな信念との付き合いが長いので、ラショナルな信念が身に馴染むには時間がかかることについて話し合う。
19. ホームワークの設定: ラショナルな信念に従って行動する練習をするためのホームワークを設定する。例えば、毎日ラショナルな信念を唱えたり、何か目標達成の役に立つ行動を起こす前にラショナルな信念を思い出すなど。
20. 労いと承認（このステップは2回でいい）：2～19までのやり取りを踏まえて、ユーザの大変さを労い、問題の解決や目標の達成に向けて努力している点を賞賛する。その他、セッションを通して明らかにされた、ユーザの強みや、尊敬できる点についてフィードバックする。
21．振り返り：セッションを通して気づいた点、学びになった点、良かった点、今後取り組んでみたいこと等について、振り返りを求める。1つで終わらず、他には？他には？と、できるだけ多く挙げてもらう。
22．エンディング：今後、問題解決や目標達成に向けていい方向に進むことを願っていることを伝え、応援と前向きな見通しを伝えて、セッションを終えてよいか確認する

応答作成の手順
1. 対話履歴を注意深く読む。
2. 1つ前、2つ前、3つ前のフロー・ステップが同じ場合、対話履歴や直近のユーザの入力から判断し、次のフロー・ステップに進むかどうか決める。
3. 1つ前、2つ前、3つ前のフロー・ステップが同じでなければ、1つ前のフロー・ステップを継続する。
4. 回答は2文以内で簡潔に。
5. 回答がREBTの理論と技法に沿ったものであること。
6. 質問はしなくてもよければしない。必要な場合は、可能な限り、質問形じゃない表現に変える。
7. 考えや信念、思考について「気持ち」や「感じる」と表現しない。考え、信念、思考は、「思い」であり、「思う」もの。「気持ち」は、感情を指す場合のみ使用する。
8. アドバイスは必要なとき以外しない。アドバイスが絶対に必要な時は、「REBTの概要」や「REBTのフロー・ステップ」に沿って行う

禁止行為
- 1つ前、2つ前、3つ前のフロー・ステップがすべて同じでない場合に、次のフロー・ステップに進まない。
- できるだけ、質問ではなく、共感的なリフレクションを用いる。
- 「よくわかります」とは言わず、「わかるような気がします」と言う。
- 1回の応答に複数の質問を含めない。
- 回答に英単語はなるべく含めない（例えば、"awful" は、「ひどい」とする）
- 対話履歴を踏まえて、同じような言い回しばかり使わない。

回答の出力：
現在のフローステップ番号と名前を<flow>タブ内に、対話履歴における同じフロー・ステップの直近の継続回数を<numbers>タブ内に、これらに基づいて検討されたユーザへの返答を
<response>タブ内に出力してください。


対話を通して応答作成の手順を順守し、1つのフローステップを3回以上続けてください。

ユーザ入力: {{input}}
Response:
"""

reflection_chain = (
    ChatPromptTemplate.from_messages([
        (
            "system",
            reflection_prompt,
        ),
        MessagesPlaceholder(variable_name="history"),
        # ("human", "{input}"),
    ])
    | model_response)

reflection_chain_memory = RunnableWithMessageHistory(
    reflection_chain,
    get_session_history,
    input_messages_key="input",
    history_messages_key="history",
    history_factory_config=[
        ConfigurableFieldSpec(
            id="user_id",
            annotation=str,
            name="User ID",
            description="Unique identifier for the user.",
            default="",
            is_shared=True,
        ),
        ConfigurableFieldSpec(
            id="conversation_id",
            annotation=str,
            name="Conversation ID",
            description="Unique identifier for the conversation.",
            default="",
            is_shared=True,
        ),
    ],
)

# 分岐先2: 質問への回答
question_prompt = f"""
「ユーザの質問」に認知行動療法の専門家として、丁寧に答えてください。ハルシネーションはせずに、わからないことはわからないと答えて、アプリの管理者にたずねるように伝えてください。加えて、何かを回答する際は、
念のため、これはAIシステムからの情報提供なので、信ぴょう性が不確かな可能性があります。重要なことについてはご自身でご確認くださいと加えてください。認知行動療法やREBTの説明は、下記の内容を参照してください。
セッション履歴に関する質問は、文脈情報として渡されているこれまでのやり取りを踏まえて返答してください。

# 認知行動療法やREBTについて
0. 概要
   - アルバート・エリスによって開発された認知行動療法の手法がREBT（論理情動行動療法）です。
   - REBTは、認知行動療法の3つの波（行動アプローチ、認知アプローチ、マインドフルネスアプローチ）の中では、認知アプローチに分類されます。
   - 認知アプローチでは、問題となる感情、行動、身体反応が、出来事により活性化されたイラショナルな信念によって生じると考え、イラショナルな信念をラショナルなものに変えようとします
1. REBTが扱う問題
   - 状況そのものではなく、状況に対する個人の受け止め方や感じ方に焦点を当てる。
   - イラショナルな信念に起因する感情的問題に対処する。
   - ストレスを軽減し、効果的な反応を改善することを目指す。
2. イラショナルな信念とラショナルな信念：
   - イラショナルな信念は、絶対的な要求と、破局視、フラストレーション耐性の低さ、自己卑下、他者卑下が組み合わさっている。
   - イラショナルな信念をラショナルな信念に変える：
     - 絶対的な要求を現実的な願望に
     - 破局化を非破局化へ
     - 低い欲求不満耐性を高い欲求不満耐性へ
     - 自己卑下を自己受容へ
     - 他者卑下を他者受容へ
3. REBTの感情理論：
   - 主な問題感情：不安、抑うつ、恥、罪悪感、不健康な怒り、傷心、嫉妬、妬み。
   - それぞれの感情は特定のイラショナルな信念と結びつきがち。
4. REBTの技法：
   - 必要なとき以外質問をしない。
   - 質問よりも共感的なリフレクション、つまりユーザの入力を言い換え、気持ち、思い、状況についての解釈を交えて伝え返すことを使う。
   - 各フローのステップで、利用者と少なくとも3回のやりとりをする。
   - 過去よりも未来に焦点を当てる。
   - 弱みよりも強みと資源を強調する。
   - 利用者の長所や資源を褒める。
   - 全般的に進むべき方向を提案するのではなく、先にユーザの考えを聞く。その上で、必要があれば提案し、さらに、その提案に対するユーザの認識を確認する
   - 応答は簡潔に2文以内で

# 回答の出力
回答は<response>タブ内に出力してください。

ユーザの質問: {{input}}
Response:
"""

question_chain = (ChatPromptTemplate.from_messages([
    (
        "system",
        question_prompt,
    ),
    MessagesPlaceholder(variable_name="history"),
    # ("human", "{input}"),
])
                  | model_response)

# question_chain_memory = question_chain
question_chain_memory = RunnableWithMessageHistory(
    question_chain,
    get_session_history,
    input_messages_key="input",
    history_messages_key="history",
    history_factory_config=[
        ConfigurableFieldSpec(
            id="user_id",
            annotation=str,
            name="User ID",
            description="Unique identifier for the user.",
            default="",
            is_shared=True,
        ),
        ConfigurableFieldSpec(
            id="conversation_id",
            annotation=str,
            name="Conversation ID",
            description="Unique identifier for the conversation.",
            default="",
            is_shared=True,
        ),
    ],
)

# 統合
# ルート関数
def route(info):
    global current_prompt
    # print("root_decision: ", info["topic"].lower())
    if "question" in info["topic"].lower():
        current_prompt = question_prompt.format(input="{input}")
        return question_chain_memory
    # elif "other" in info["topic"].lower():
    #     return reflection_chain
    else:
        current_prompt = reflection_prompt.format(input="{input}")
        return reflection_chain_memory


# RunnableLambdaを使った結合
full_chain = {
    # "topic": chain,
    "topic": chain_memory,
    "input": lambda x: x["input"]
} | RunnableLambda(route) | StrOutputParser()

# store = {}

######### LangChainここまで #########
def generate_claude_response(prompt, userId):
    config = _per_request_config_modifier({}, userId)  # 初期の config に userId を追加
    input = {
        "input": prompt,
        "user_id": userId  # user_idのみを使用
    }

    try:
        # # 履歴のデバッグログ
        # print("Debug: History before model invocation:", get_session_history(userId, userId).messages)
        response = full_chain.invoke(input, config)
        # logger.info(f"Response from GPT: {response}")
        return response
    except Exception as e:
        print(f"Error: {e}")
        return "Sorry, I couldn't understand that."


# def generate_claude_response(prompt, userId):
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
#         'model': "gpt-4o",
#         'messages': conversation_history,
#         'temperature': 1
#     }
#     # ここでconversation_historyの内容をログに出力
#     # app.logger.info("Conversation history sent to : " + str(conversation_history))
#     # 旧："gpt-4-1106-preview"

#     try:
#         response = requests.post(GPT4_API_URL, headers=headers, json=data)
#         response.raise_for_status()  # Check if the request was successful
#         response_json = response.json() # This line has been moved here
#         # Add this line to log the response from  API
#         # app.logger.info("Response from  API: " + str(response_json))
#         return response_json['choices'][0]['message']['content'].strip()
#     except requests.RequestException as e:
#         # app.logger.error(f" API request failed: {e}")
#         return "Sorry, I couldn't understand that."

        
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
    # logger.info(f"Attempting to deactivate conversation history for user: {userId}")
    connection = get_connection()
    cursor = connection.cursor()
    try:
        query = """
        UPDATE line_bot_logs SET is_active=FALSE 
        WHERE lineId=%s;
        """
        cursor.execute(query, (userId,))
        connection.commit()
        set_user_state(userId, 'normal')  # ユーザーの状態をリセット
    except Exception as e:
        print(f"Error: {e}")
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

# LINEからのメッセージを処理し、必要に応じてStripeの情報も確認します。
def get_user_state(user_id):
    state = redis_client.get(f"user_state:{user_id}")
    return state.decode('utf-8') if state else 'normal'

def set_user_state(user_id, state):
    redis_client.set(f"user_state:{user_id}", state)
    redis_client.expire(f"user_state:{user_id}", 1800)  # 30分後に期限切れ

@handler.add(MessageEvent, message=TextMessage)
def handle_line_message(event):
    global current_prompt
    userId = getattr(event.source, 'user_id', None)
    
    if not userId:
        reply_text = "エラーが発生しました。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    current_state = get_user_state(userId)

    # ユーザーが「リセット」を送信した場合
    if event.message.text == "リセット":
        set_user_state(userId, 'awaiting_reset_confirmation')
        reply_text = "過去の対話履歴を削除して良いですか？一度削除すると元には戻せません。よろしければ「はい」と入力してください。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # ユーザーが「はい」を送信した場合、リセット確認状態なら履歴を削除
    elif current_state == 'awaiting_reset_confirmation':
        if event.message.text.lower() == "はい":
            deactivate_conversation_history(userId)
            reply_text = "対話履歴を削除しました。"
        else:
            reply_text = "対話履歴の削除を中止しました。"
        set_user_state(userId, 'normal')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # 通常のメッセージ処理
    current_timestamp = datetime.now()

    if userId:
        # LangSmithによる追跡
        os.environ["LANGCHAIN_API_KEY"]
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        LANGCHAIN_ENDPOINT="https://api.smith.langchain.com"
        os.environ["LANGCHAIN_PROJECT"] = f"lineREBT_{userId}"
        
        subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
        stripe_id = subscription_details['stripeId'] if subscription_details else None
        subscription_status = subscription_details['status'] if subscription_details else None

        log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text, current_prompt, model_name, True)

        if subscription_status == None: ####################本番は"active", テストはNone################
            full_response = generate_claude_response(event.message.text, userId)
            # <response>タグの中身を抽出
            match = re.search(r'<response>(.*?)</response>', full_response, re.DOTALL)
            if match:
                reply_text = match.group(1)
            else:
                reply_text = full_response
        else:
            response_count = get_system_responses_in_last_24_hours(userId)
            if response_count < 5: 
                full_response = generate_claude_response(event.message.text, userId)
                # <response>タグの中身を抽出
                match = re.search(r'<response>(.*?)</response>', full_response, re.DOTALL)
                if match:
                    reply_text = match.group(1)
                else:
                    reply_text = full_response
            else:
                line_login_url = os.environ["LINE_LOGIN_URL"]
                reply_text = f"利用回数の上限に達しました。24時間後に再度お試しください。こちらから回数無制限の有料プランに申し込むこともできます：{line_login_url}"
    else:
        reply_text = "エラーが発生しました。"

    # メッセージをログに保存
    log_to_database(current_timestamp, 'system', userId, stripe_id, full_response, current_prompt, model_name, True)

    # 最終的な返信メッセージを送信
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


# # LINEからのメッセージを処理し、必要に応じてStripeの情報も確認します。
# # ユーザーごとの確認フラグを保持する辞書を追加
# reset_confirmation = {}

# @handler.add(MessageEvent, message=TextMessage)
# def handle_line_message(event):
#     global current_prompt, reset_confirmation  # current_prompt を使用するためにグローバル変数として宣言
#     userId = getattr(event.source, 'user_id', None)
    
#     # logger.info(f"Current reset_confirmation state: {reset_confirmation}")
    
#     # ユーザーが「リセット」を送信した場合
#     if event.message.text == "リセット" and userId:
#         # logger.info(f"Reset requested for user: {userId}")
#         # 確認メッセージを送信し、確認フラグを立てる
#         reply_text = "過去の対話履歴を削除して良いですか？一度削除すると元には戻せません。よろしければ「はい」と入力してください。"
#         reset_confirmation[userId] = True
#         line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
#         # logger.info(f"Current reset_confirmation state: {reset_confirmation}")
#         return  # ここで処理を終了し、他の処理が実行されないようにする

#     # ユーザーが「はい」を送信した場合、リセット確認フラグが有効なら履歴を削除
#     # loogerをコメントアウトすると、リセットが上手く機能しない。Herokuの環境では、各リクエストが異なるワーカープロセスで処理される可能性があり、メモリ内の状態が共有されないからっぽい。
#     elif event.message.text == "はい" and reset_confirmation.get(userId, False):
#         logger.info(f"Confirmation 'はい' received for user: {userId}")
#         logger.info(f"Current reset_confirmation state before processing 'はい': {reset_confirmation}")
#         deactivate_conversation_history(userId)
#         logger.info(f"Conversation history reset for user: {userId}")
#         reply_text = "対話履歴を削除しました。"
#         reset_confirmation[userId] = False  # フラグをリセット
#         line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
#         logger.info(f"Current reset_confirmation state: {reset_confirmation}")
#         return  # ここで処理を終了し、他の処理が実行されないようにする

#     # 確認メッセージ後に「はい」以外の応答があった場合、削除を中止
#     elif reset_confirmation.get(userId, False):
#         reply_text = "対話履歴の削除を中止しました。"
#         reset_confirmation[userId] = False  # フラグをリセット
#         line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
#         return  # ここで処理を終了し、他の処理が実行されないようにする

#     else:
#         # logger.info(f"Current reset_confirmation state: {reset_confirmation}")
#         # その他の通常メッセージ処理
#         current_timestamp = datetime.now()

#         if userId:
#             # LangSmithによる追跡
#             os.environ["LANGCHAIN_API_KEY"]
#             os.environ["LANGCHAIN_TRACING_V2"] = "true"
#             LANGCHAIN_ENDPOINT="https://api.smith.langchain.com"
#             os.environ["LANGCHAIN_PROJECT"] = f"lineREBT_{userId}"
            
#             subscription_details = get_subscription_details_for_user(userId, STRIPE_PRICE_ID)
#             stripe_id = subscription_details['stripeId'] if subscription_details else None
#             subscription_status = subscription_details['status'] if subscription_details else None

#             log_to_database(current_timestamp, 'user', userId, stripe_id, event.message.text, current_prompt, model_name, True)

#             if subscription_status == None: ####################本番は"active", テストはNone################
#                 full_response = generate_claude_response(event.message.text, userId)
#                 # <response>タグの中身を抽出
#                 match = re.search(r'<response>(.*?)</response>', full_response, re.DOTALL)
#                 if match:
#                     reply_text = match.group(1)
#                 else:
#                     reply_text = full_response
#             else:
#                 response_count = get_system_responses_in_last_24_hours(userId)
#                 if response_count < 5: 
#                     full_response = generate_claude_response(event.message.text, userId)
#                     # <response>タグの中身を抽出
#                     match = re.search(r'<response>(.*?)</response>', full_response, re.DOTALL)
#                     if match:
#                         reply_text = match.group(1)
#                     else:
#                         reply_text = full_response
#                 else:
#                     line_login_url = os.environ["LINE_LOGIN_URL"]
#                     reply_text = f"利用回数の上限に達しました。24時間後に再度お試しください。こちらから回数無制限の有料プランに申し込むこともできます：{line_login_url}"
#         else:
#             reply_text = "エラーが発生しました。"

#         # メッセージをログに保存
#         log_to_database(current_timestamp, 'system', userId, stripe_id, full_response, current_prompt, model_name, True)

#         # 最終的な返信メッセージを送信
#         line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

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
def log_to_database(timestamp, sender, userId, stripeId, message, sys_prompt, model_name=None, is_active=True):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        query = """
        INSERT INTO line_bot_logs (timestamp, sender, lineId, stripeId, message, is_active, sys_prompt, model) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """
        cursor.execute(query, (timestamp, sender, userId, stripeId, message, is_active, sys_prompt, model_name))
        connection.commit()
    except Exception as e:
        print(f"Error: {e}")
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

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

# sys_prompt = "You will be playing the role of a supportive, Japanese-speaking counselor. Here is the conversation history so far:\n\n<conversation_history>\n{{CONVERSATION_HISTORY}}\n</conversation_history>\n\nThe user has just said:\n<user_statement>\n{{QUESTION}}\n</user_statement>\n\nPlease carefully review the conversation history and the user's latest statement. Your goal is to provide supportive counseling while following this specific method:\n\n1. Listen-Back 1: After the user makes a statement, paraphrase it into a single sentence while adding a new nuance or interpretation. \n2. Wait for the user's reply to your Listen-Back 1.\n3. Listen-Back 2: After receiving the user's response, further paraphrase their reply, condensing it into one sentence and adding another layer of meaning or interpretation.\n4. Once you've done Listen-Back 1 and Listen-Back 2 and received a response from the user, you may then pose a question from the list below, in the specified order. Do not ask a question out of order.\n5. After the user answers your question, return to Listen-Back 1 - paraphrase their answer in one sentence and introduce a new nuance or interpretation. \n6. You can ask your next question only after receiving a response to your Listen-Back 1, providing your Listen-Back 2, and getting another response from the user.\n\nIn essence, never ask consecutive questions. Always follow the pattern of Listen-Back 1, user response, Listen-Back 2, another user response before moving on to the next question.\n\nHere is the order in which you should ask questions:\n1. Start by asking the user about something they find particularly troubling.\n2. Then, inquire about how they'd envision the ideal outcome. \n3. Proceed by asking about what little they've already done.\n4. Follow up by exploring other actions they're currently undertaking.\n5. Delve into potential resources that could aid in achieving their goals.\n6. Discuss the immediate actions they can take to move closer to their aspirations.\n7. Lastly, encourage them to complete the very first step in that direction with some positive feedback, and ask if you can close the conversation.\n\n<example>\nUser: I'm so busy I don't even have time to sleep.\nYou: You are having trouble getting enough sleep.\nUser: Yes.\nYou: You are so busy that you want to manage to get some sleep.\nUser: Yes.\nYou: In what way do you have problems when you get less sleep?\n</example>\n\n<example>  \nUser: I get sick when I get less sleep.\nYou: You are worried about getting sick.\nUser: Yes.\nYou: You feel that sleep time is important to stay healthy.\nUser: That is right.\nYou: What do you hope to become?\n</example>\n\n<example>\nUser: I want to be free from suffering. But I cannot relinquish responsibility.\nYou: You want to be free from suffering, but at the same time you can't give up your responsibility.\nUser: Exactly.\nYou: You are searching for your own way forward.\nUser: Maybe so.\nYou: When do you think you are getting closer to the path you should be on, even if only a little?  \n</example>\n\nPlease follow the above procedures strictly for the consultation."

# def generate_claude_response(prompt, userId):
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
#         'model': "gpt-4o",
#         'messages': conversation_history,
#         'temperature': 1
#     }
#     # ここでconversation_historyの内容をログに出力
#     # app.logger.info("Conversation history sent to : " + str(conversation_history))
#     # 旧："gpt-4-1106-preview"

#     try:
#         response = requests.post(GPT4_API_URL, headers=headers, json=data)
#         response.raise_for_status()  # Check if the request was successful
#         response_json = response.json() # This line has been moved here
#         # Add this line to log the response from  API
#         # app.logger.info("Response from  API: " + str(response_json))
#         return response_json['choices'][0]['message']['content'].strip()
#     except requests.RequestException as e:
#         # app.logger.error(f" API request failed: {e}")
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
#                 reply_text = generate_claude_response(event.message.text, userId)
#             else:
#                 response_count = get_system_responses_in_last_24_hours(userId)
#                 if response_count < 5: 
#                     reply_text = generate_claude_response(event.message.text, userId)
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
