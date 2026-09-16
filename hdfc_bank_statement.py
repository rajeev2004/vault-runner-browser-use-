from browser_use import ChatGoogle, ChatOpenRouter, ChatGroq, ChatOpenAI, Agent, BrowserSession
from dotenv import load_dotenv
from pydantic import BaseModel
import asyncio
import json
from datetime import datetime
import re
import os
load_dotenv()
llm = ChatOpenAI(model = 'gpt-5.6-terra')
fallback_llm = ChatGoogle(model = 'gemini-flash-latest')
sensitive_data = {
    'x_username': os.getenv('HDFC_USERNAME'),
    'x_password': os.getenv('HDFC_PASSWORD')
}
browser_session = BrowserSession(keep_alive = True, allowed_domains = ['now.hdfc.bank.in'])

class LoginCheck(BaseModel):
    login_attempted: bool
    login_error: str
    captcha_encountered: bool
    otp_screen_reached: bool
    otp_delivery_method_selected: str
    unexpected_state: str

