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

class HDFCTransaction(BaseModel):
    date: str
    description: str
    reference_number: str
    amount: str
    closing_balance: str

class AccountCheck(BaseModel):
    login_attempted: bool
    login_error: str
    captcha_encountered: bool
    otp_screen_reached: bool
    otp_delivery_method_selected: str
    unexpected_state: str
    statement_page_reached: bool
    statement_page_description: str
    otp_submission_result: str
    transactions: list[HDFCTransaction]

def log_step(browser_state, agent_output, step_number):
        log = {
            "timestamp" : datetime.now().isoformat(),
            "step" : step_number,
            "agent_output" : agent_output.model_dump()
        }
        write_log(log);

def write_log(entry):
    with open("statement_log.json1", "a") as f:
        f.write(f"{json.dumps(entry)}\n")

agent = Agent(
    task = f"""Rules, in priority order:
    1. Go to https://now.hdfc.bank.in/retail-app/ and log in with username x_username and password x_password. Set login_attempted to true.
    2. If a login error appears, stop immediately, do not retry, and set login_error to the exact text shown. Leave all remaining fields false or empty.
    3. If a CAPTCHA appears at any point — during login or afterward — do not attempt to solve it. Stop immediately and set captcha_encountered to true.
    4. If prompted to choose an OTP delivery method at any point, select SMS, click to request the code, and set otp_delivery_method_selected accordingly. Do NOT attempt to enter, guess, or generate any OTP value.
    5. If an OTP entry screen appears at any point — during login or afterward — stop completely, set otp_screen_reached to true, and do nothing further.
    6. If login succeeds with no CAPTCHA or OTP, navigate to wherever account statements or transaction history are viewable.
    7. If reaching that page triggers anything covered by rules 3-5, apply them there too.
    8. Once you reach the statement page, select "Recent Transactions" as the statement period. For each transaction row visible, click its panel toggle to expand it and reveal its full details, then use the evaluate action to extract that row's date, description, reference number, amount, and closing balance from the expanded panel. Repeat for every row visible on the page. Populate the transactions list only with data that literally appears in the JavaScript extraction results — do not summarize, paraphrase, or add any detail not directly present. If a field is genuinely not available even after expanding a row, use an empty string rather than guessing.
    9. After extracting all rows currently visible, check whether more transactions exist beyond what's shown — for example, a "Next" control, page numbers, or text like "1-10 of 20". If more exist, navigate to the next page and repeat the same expand-and-extract process for every row there, adding to the same transactions list rather than replacing it. Continue until every page has been covered or no further page control exists. Do not click a pagination control more than 5 times in a row, to avoid looping indefinitely if the page doesn't behave as expected.
    10. Before setting any field, gather concrete evidence using the evaluate action — read the actual page URL and visible text. Do not rely on a visual impression alone.
    11. If anything happens that doesn't match these cases, describe it in unexpected_state; otherwise leave that field empty.""",
    use_vision = False,
    llm = llm,
    fallback_llm = fallback_llm,
    sensitive_data = sensitive_data,
    browser_session = browser_session,
    register_new_step_callback = log_step,
    output_model_schema = AccountCheck
)

async def main():
    await browser_session.start()
    history = await agent.run()
    response = history.structured_output
    if response is None:
        print("The agent never produced a result — check the log for what went wrong.")
        await browser_session.kill()
        return
    judgement = history.history[-1].result[-1].judgement
    if(judgement is not None and not judgement.verdict):
        print("⚠️ WARNING: The judge does NOT agree with the agent's report — treat this result with caution.")
        print(f"Judge's reason: {judgement.failure_reason}")
    print(f"Login attempted: {response.login_attempted}")
    print(f"Login error: {response.login_error}")
    print(f"CAPTCHA encountered: {response.captcha_encountered}")
    print(f"OTP screen reached: {response.otp_screen_reached}")
    print(f"OTP delivery method selected: {response.otp_delivery_method_selected}")
    print(f"Unexpected state: {response.unexpected_state}")
    print(f"Statement page reached: {response.statement_page_reached}")
    print(f"Statement page description: {response.statement_page_description}")
    if (response.otp_screen_reached):
        otp_code = input("Check your phone for the OTP and enter it here: ")
        sensitive_data['x_otp'] = otp_code
        agent.add_new_task("Enter x_otp into the OTP field and submit it. Set otp_submission_result to describe exactly what happened — success and what page you reached, or the exact error shown.")
        history2 = await agent.run()
        final_response = history2.structured_output
        if final_response is None:
            print("The OTP submission run never produced a result — check the log for what went wrong.")
        else:
            print(f"OTP submission result: {final_response.otp_submission_result}")
    print(f"Transactions found: {len(response.transactions)}")
    for t in response.transactions:
        print(f"  {t.date} | {t.description} | {t.reference_number} | {t.amount} | {t.closing_balance}")
    await browser_session.kill()

asyncio.run(main())