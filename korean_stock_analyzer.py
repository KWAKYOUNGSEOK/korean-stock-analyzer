
# Korean Stock Analyzer with Strategy Customization, Alerts, Portfolio Tracking, and GPT Strategy Tuning

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
from datetime import datetime
import requests
import json
import time
import csv
import schedule
import threading
import smtplib
from email.mime.text import MIMEText
import openai
from io import BytesIO
from PIL import ImageGrab

st.set_page_config(page_title="한국 주식 AI 자동 매매 시스템", layout="wide")
st.title("🤖 AI 자동매매 + 전략 개선 + 성과 보고서 + 백테스트")

mode = st.radio("💼 모드 선택", ("모의투자", "실전투자"))
alert_email = st.text_input("📧 알림 이메일 주소 (선택)", "")
telegram_token = st.text_input("📱 텔레그램 봇 토큰 (선택)", "")
telegram_chat_id = st.text_input("💬 텔레그램 챗 ID (선택)", "")
openai.api_key = st.text_input("🔐 OpenAI API Key (선택)", type="password")

account = {
    "CANO": "43019240",
    "ACNT_PRDT_CD": "01",
    "total_profit": 0,
    "daily_profit_target": 50.0,
    "initial_capital": 1000000,
}

@st.cache_data
def load_korean_tickers():
    url = 'https://kind.krx.co.kr/corpgeneral/corpList.do?method=download'
    krx_df = pd.read_html(url, header=0, encoding='euc-kr')[0]
    krx_df = krx_df[['회사명', '종목코드']]
    krx_df['종목코드'] = krx_df['종목코드'].apply(lambda x: f"{int(x):06d}.KS")
    return dict(zip(krx_df['회사명'], krx_df['종목코드']))

def calculate_signals(data):
    data['MA20'] = data['Close'].rolling(window=20).mean()
    data['UpperBB'] = data['MA20'] + 2 * data['Close'].rolling(window=20).std()
    data['LowerBB'] = data['MA20'] - 2 * data['Close'].rolling(window=20).std()
    delta = data['Close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    data['RSI'] = 100 - (100 / (1 + rs))
    return data

def get_trade_recommendation(data):
    rsi = data['RSI'].iloc[-1]
    close = data['Close'].iloc[-1]
    lower_bb = data['LowerBB'].iloc[-1]
    upper_bb = data['UpperBB'].iloc[-1]
    if rsi < 30 and close < lower_bb:
        return "✅ 매수 기회", close, round(close * 1.05, 2), round(close * 0.97, 2)
    elif rsi > 70 and close > upper_bb:
        return "❌ 매도 신호", close, None, None
    return "보류", None, None, None

def send_kis_order(token, code, price, qty, action, appkey, appsecret):
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": "TTTC0802U" if action == "buy" else "TTTC0801U",
    }
    body = {
        "CANO": account['CANO'],
        "ACNT_PRDT_CD": account['ACNT_PRDT_CD'],
        "PDNO": code,
        "ORD_DVSN": "00",
        "ORD_QTY": str(qty),
        "ORD_UNPR": str(price),
    }
    if mode == "실전투자":
        res = requests.post("https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash", headers=headers, data=json.dumps(body))
        return res.json()
    return {"rt_msg": "[모의투자] 주문 성공 (시뮬레이션)"}

def send_telegram_alert(message):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        data = {"chat_id": telegram_chat_id, "text": message}
        requests.post(url, data=data)
    except:
        pass

def send_telegram_capture():
    try:
        img = ImageGrab.grab()
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        requests.post(
            f"https://api.telegram.org/bot{telegram_token}/sendPhoto",
            data={"chat_id": telegram_chat_id},
            files={"photo": buf},
        )
    except:
        pass

def improve_strategy(prompt="다음 RSI+볼린저 전략을 개선해줘"):
    if openai.api_key:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "system", "content": "당신은 퀀트 전략 전문가입니다."},
                      {"role": "user", "content": prompt}]
        )
        return response['choices'][0]['message']['content']
    return "(GPT 전략 개선을 위해 OpenAI API Key가 필요합니다.)"

def get_kis_token(appkey, appsecret):
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": appkey, "appsecret": appsecret}
    res = requests.post(url, headers=headers, data=json.dumps(body))
    return res.json().get("access_token", "")

def analyze_and_trade():
    tickers = load_korean_tickers()
    appkey = st.secrets.get("APP_KEY", "YOUR_APP_KEY")
    appsecret = st.secrets.get("APP_SECRET", "YOUR_APP_SECRET")
    token = get_kis_token(appkey, appsecret)
    result = []
    profit_sum = 0
    for name, code in list(tickers.items())[:20]:
        try:
            df = yf.download(code, period="1d", interval="1m")
            if df.empty: continue
            df = calculate_signals(df)
            signal, price, tp, sl = get_trade_recommendation(df)
            row = {"종목명": name, "코드": code, "현재가": round(price, 2) if price else None, "RSI": round(df['RSI'].iloc[-1], 2), "추천 신호": signal, "추천 가격": price, "익절가": tp, "손절가": sl}
            if signal == "✅ 매수 기회" and price:
                res = send_kis_order(token, code[:6], price, 1, "buy", appkey, appsecret)
                row["매수 결과"] = res.get("rt_msg", "응답 없음")
                profit_sum += tp - price
            elif signal == "❌ 매도 신호" and price:
                res = send_kis_order(token, code[:6], price, 1, "sell", appkey, appsecret)
                row["매도 결과"] = res.get("rt_msg", "응답 없음")
            result.append(row)
            if telegram_token and telegram_chat_id:
                send_telegram_alert(f"📢 {name} - {signal}\n현재가: {price}")
        except Exception as e:
            result.append({"종목명": name, "코드": code, "에러": str(e)})
            continue
    account['total_profit'] += profit_sum
    daily_return = (account['total_profit'] / account['initial_capital']) * 100
    df_result = pd.DataFrame(result)
    df_result.to_csv("추천_종목_리포트.csv", index=False)
    st.subheader("📊 예상 수익률 분포")
   df_result['수익률'] = df_result.apply(
    lambda x: (x.get('익절가', 0) - x.get('추천 가격', 0)) / x.get('추천 가격', 1) * 100 
    if x.get('추천 가격') and x.get('익절가') else 0,
    axis=1
)
    st.bar_chart(df_result.set_index('종목명')['수익률'])
    if alert_email:
        send_email_alert("[AI 주식 성과 보고서]", df_result.to_string(), alert_email)
    if telegram_token and telegram_chat_id:
        send_telegram_alert("📈 AI 추천 요약 완료")
        send_telegram_capture()
    st.info(f"오늘 누적 수익률: {daily_return:.2f}%")
    if daily_return >= account['daily_profit_target']:
        st.success("🎯 목표 수익률 달성! 매매 자동 중단.")
        schedule.clear()
    return df_result

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

if st.button("🔁 지금 분석 및 주문 실행"):
    df = analyze_and_trade()
    st.dataframe(df)

if st.checkbox("🕒 실시간 매분 분석 실행"):
    schedule.every(1).minutes.do(analyze_and_trade)
    thread = threading.Thread(target=run_schedule)
    thread.start()
    st.success("백그라운드 분석 실행 중")

if st.button("🧠 GPT 전략 개선 요청"):
    suggestion = improve_strategy()
    st.code(suggestion)
