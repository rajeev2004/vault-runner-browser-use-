from browser_use import ChatGoogle, ChatOpenAI, Agent, BrowserSession
from dotenv import load_dotenv
from pydantic import BaseModel
import asyncio
import json
from datetime import datetime
import os

load_dotenv()
llm = ChatOpenAI(model = 'gpt-5.6-terra')
fallback_llm = ChatGoogle(model = 'gemini-flash-latest')

sensitive_data = {
    'x_username': os.getenv('HDFC_USERNAME'),
    'x_password': os.getenv('HDFC_PASSWORD')
}

if not sensitive_data['x_username'] or not sensitive_data['x_password']:
    raise ValueError("HDFC_USERNAME or HDFC_PASSWORD is missing from your .env file.")

# keep_alive=True is required so the browser survives between separate
# agent.run() calls (needed for the OTP hand-off below)
# Enforced at the browser level, not just a task instruction — navigation
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
    otp_submission_succeeded: bool
    transactions: list[HDFCTransaction]

def log_step(browser_state, agent_output, step_number):
        log = {
            "timestamp" : datetime.now().isoformat(),
            "step" : step_number,
            "agent_output" : agent_output.model_dump()
        }
        write_log(log)

def write_log(entry):
    with open("statement_log.json1", "a") as f:
        f.write(f"{json.dumps(entry)}\n")

agent = Agent(
    task = """Rules, in priority order:
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
    # Vision disabled deliberately
    use_vision = False,
    llm = llm,
    fallback_llm = fallback_llm,
    sensitive_data = sensitive_data,
    browser_session = browser_session,
    register_new_step_callback = log_step,
    output_model_schema = AccountCheck
)

async def main():
    try:
        await browser_session.start()
        login_history = await agent.run()
        account_check = login_history.structured_output

        if account_check is None:
            print("The agent never produced a result — check the log for what went wrong.")
            return
        
        # Judge checks below are informational only — they never block or reverse an action that already happened.
        login_judgement = login_history.history[-1].result[-1].judgement
        if login_judgement is None:
            print("No judge verdict was produced for the login/statement result — treat this as unverified.")
        elif not login_judgement.verdict:
            print("⚠️ WARNING: The judge does NOT agree with the agent's report — treat this result with caution.")
            print(f"Judge's reason: {login_judgement.failure_reason}")
        print(f"Login attempted: {account_check.login_attempted}")
        print(f"Login error: {account_check.login_error}")
        print(f"CAPTCHA encountered: {account_check.captcha_encountered}")
        print(f"OTP screen reached: {account_check.otp_screen_reached}")
        print(f"OTP delivery method selected: {account_check.otp_delivery_method_selected}")
        print(f"Unexpected state: {account_check.unexpected_state}")
        print(f"Statement page reached: {account_check.statement_page_reached}")
        print(f"Statement page description: {account_check.statement_page_description}")

        if account_check.captcha_encountered:
            print("A CAPTCHA appeared. This script has stopped and will not attempt it.")
            print("Open your browser normally, log in yourself, and complete this task by hand.")

        if (account_check.otp_screen_reached):
            otp_code = input("Check your phone for the OTP and enter it here: ").strip()

            # 4 or 6 digits covers the common cases, but this is a general assumption
            while not otp_code.isdigit() or len(otp_code) not in (4, 6):
                otp_code = input("That doesn't look like a valid OTP. Please enter the numeric code exactly as received: ").strip()
            sensitive_data['x_otp'] = otp_code
            agent.add_new_task("Enter x_otp into the OTP field and submit it. Set otp_submission_succeeded to true only if you can point to concrete evidence of success (like reaching an authenticated page), or false otherwise. Set otp_submission_result to describe exactly what happened — success and what page you reached, or the exact error shown.")
            otp_history = await agent.run()
            otp_result = otp_history.structured_output
            otp_judgement = otp_history.history[-1].result[-1].judgement

            if otp_result is None:
                print("The OTP submission run never produced a result — check the log for what went wrong.")
            else:
                if otp_judgement is None:
                    print("No judge verdict was produced for the OTP submission — treat this result as unverified.")
                elif not otp_judgement.verdict:
                    print("⚠️ WARNING: The judge does NOT agree the OTP submission was properly verified.")
                    print(f"Judge's reason: {otp_judgement.failure_reason}")

                if (otp_result.otp_submission_succeeded):
                    agent.add_new_task("Now navigate to wherever account statements are viewable and extract the Recent Transactions using the same evaluate-based approach as mentioned in the rules.")
                    post_otp_history = await agent.run()
                    post_otp_check = post_otp_history.structured_output

                    if (post_otp_check is not None):
                        post_otp_judgement = post_otp_history.history[-1].result[-1].judgement

                        if post_otp_judgement is None:
                            print("No judge verdict was produced for the post-OTP transactions — treat this result as unverified.")
                        elif not post_otp_judgement.verdict:
                            print("⚠️ WARNING: The judge does NOT agree the post-OTP transactions were properly verified.")
                            print(f"Judge's reason: {post_otp_judgement.failure_reason}")
                        print(f"Transactions found after OTP: {len(post_otp_check.transactions)}")
                        for txn in post_otp_check.transactions:
                            print(f"  {txn.date} | {txn.description} | {txn.reference_number} | {txn.amount} | {txn.closing_balance}")
                    else:
                        print("The navigation did not work properly!")
                else:
                    print(f"OTP submission failed: {otp_result.otp_submission_result}")

        print(f"Transactions found: {len(account_check.transactions)}")
        for txn in account_check.transactions:
            print(f"  {txn.date} | {txn.description} | {txn.reference_number} | {txn.amount} | {txn.closing_balance}")
    finally:
        await browser_session.kill()

asyncio.run(main())