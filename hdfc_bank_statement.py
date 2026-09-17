from browser_use import ChatGoogle, ChatOpenAI, Agent, BrowserSession
from dotenv import load_dotenv
from pydantic import BaseModel
import asyncio
import json
from datetime import datetime
import os
import csv

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
    available_periods: list[str]
    period_selection_succeeded: bool
    period_selection_error: str
    download_only: bool
    download_result: str

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

def get_valid_date(prompt):
    while True:
        date_str = input(prompt).strip()
        try:
            parsed = datetime.strptime(date_str, "%d-%m-%Y")
        except ValueError:
            print("That doesn't look like a valid date. Please use DD-MM-YYYY format (e.g. 01-06-2026).")
            continue
        if parsed.date() >= datetime.now().date():
            print("HDFC's statement data likely doesn't cover today or future dates. Please enter an earlier date.")
            continue
        return date_str

def check_result(history, run_name):
    result = history.structured_output
    if result is None:
        print(f"{run_name} never produced a result — check the log for what went wrong.")
        return None, False
    judgement = history.history[-1].result[-1].judgement
    if judgement is None:
        print(f"No judge verdict was produced for {run_name.lower()} — treat this result as unverified.")
    elif not judgement.verdict:
        print(f"⚠️ WARNING: The judge does NOT agree {run_name.lower()} was properly verified.")
        print(f"Judge's reason: {judgement.failure_reason}")
    return result, True

def save_transactions_to_csv(transactions, period_name):
    filename = f"transactions_{period_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Description", "Reference Number", "Amount", "Closing Balance"])
        for txn in transactions:
            writer.writerow([txn.date, txn.description, txn.reference_number, txn.amount, txn.closing_balance])
    return filename

async def select_period_and_extract(agent, available_periods):
    max_attempts = 3
    attempt = 0
    succeeded = False
    while attempt < max_attempts and not succeeded:
        attempt += 1
        for period in available_periods:
            print(f"{period}\n")
        selected_period = input("Choose one of the periods to fetch the transaction history: ")
        if selected_period == "Custom Date":
            while True:
                from_date = get_valid_date("Enter the From date (DD-MM-YYYY): ")
                to_date = get_valid_date("Enter the To date (DD-MM-YYYY): ")
                if datetime.strptime(from_date, "%d-%m-%Y") <= datetime.strptime(to_date, "%d-%m-%Y"):
                    break
                print("The From date must come before the To date. Please enter both again.")
            period_instruction = f'select "Custom Date" as the statement period, then enter "{from_date}" as the From Date and "{to_date}" as the To Date'
        else:
            period_instruction = f'select "{selected_period}" as the statement period'
        agent.add_new_task(f"On the statement page, {period_instruction}, then confirm whether the selection succeeded — set period_selection_succeeded to true only if you can point to concrete evidence, or false with the problem described in period_selection_error. Once selection succeeds, use the evaluate action to concretely determine the page's state by querying for two specific things separately: (1) actual transaction row/panel elements — for example, elements whose id or class indicates a transaction row, not just a generic <table> count — and (2) the presence of Download and/or Email buttons. Do not infer the page's state from a table element count alone, and do not report any specific number of transactions or pagination text (like '20 transactions' or 'Showing 1-10 of 20') unless that exact text was literally returned by an evaluate call. Only set download_only to true if your evaluate result shows Download/Email controls and zero real transaction row elements found. In that case, select the 'Delimited' format if available (otherwise Excel), click Download, and set download_result to the exact filename or confirmation shown — or the exact error if it failed. Do NOT click Email under any circumstances. If your evaluate result instead shows one or more real transaction row elements, set download_only to false; row expansion and extraction will happen in a separate follow-up step.")
        period_history = await agent.run()
        period_check, ok = check_result(period_history, "Period selection result")

        if ok and period_check.period_selection_succeeded:
            succeeded = True
            print(f"Period '{selected_period}' selected successfully.")

            if period_check.download_only:
                print(f"'{selected_period}' offered only a Download option. Result: {period_check.download_result}")
            else:
                agent.add_new_task("Now, for the currently selected statement period, expand every visible transaction row and use the evaluate action to extract each row's date, description, reference number, amount, and closing balance — the same way described earlier. If more pages exist, paginate through all of them, up to 5 pagination clicks. Populate the transactions list with only what the JavaScript extraction literally returns.")
                period_transactions_history = await agent.run()
                period_transactions_check, ok = check_result(period_transactions_history, "Transaction extraction result")
                if ok:
                    print(f"Transactions found for '{selected_period}': {len(period_transactions_check.transactions)}")
                    for txn in period_transactions_check.transactions:
                        print(f"  {txn.date} | {txn.description} | {txn.reference_number} | {txn.amount} | {txn.closing_balance}")
                    saved_path = save_transactions_to_csv(period_transactions_check.transactions, selected_period)
                    print(f"Transactions also saved to: {saved_path}")

    if not succeeded:
        print("User did not select a correct period")

agent = Agent(
    task = """Rules, in priority order:
    1. Go to https://now.hdfc.bank.in/retail-app/ and log in with username x_username and password x_password. Set login_attempted to true.
    2. If a login error appears, stop immediately, do not retry, and set login_error to the exact text shown. Leave all remaining fields false or empty.
    3. If a CAPTCHA appears at any point — during login or afterward — do not attempt to solve it. Stop immediately and set captcha_encountered to true.
    4. If prompted to choose an OTP delivery method at any point, select SMS, click to request the code, and set otp_delivery_method_selected accordingly. Do NOT attempt to enter, guess, or generate any OTP value.
    5. If an OTP entry screen appears at any point — during login or afterward — stop completely, set otp_screen_reached to true, and do nothing further.
    6. If login succeeds with no CAPTCHA or OTP, navigate to wherever account statements or transaction history are viewable.
    7. If reaching that page triggers anything covered by rules 3-5, apply them there too.
    8. Once you reach the statement page, use the evaluate action to read ONLY the statement-period dropdown element's own <option> values and the current URL. Do NOT read or return the page's full visible text or body content, since the default view may already show real transaction rows — capturing that would violate rule 8's own restriction below. Populate available_periods with the literal text of every dropdown option found. Do NOT select any period yet, and do NOT expand or extract any transaction rows. Stop here once the list is reported.
    9. Before setting any field, gather concrete evidence using the evaluate action — but never read more of the page than a specific rule calls for. On the statement page during discovery, that means only the dropdown's own options and the URL, per rule 8 — not the full page body.
    10. If anything happens that doesn't match these cases, describe it in unexpected_state; otherwise leave that field empty.""",
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
        account_check, ok = check_result(login_history, "Login/statement result")
        if not ok:
            return
        print(f"Login attempted: {account_check.login_attempted}")
        print(f"Login error: {account_check.login_error}")
        print(f"CAPTCHA encountered: {account_check.captcha_encountered}")
        print(f"OTP screen reached: {account_check.otp_screen_reached}")
        print(f"OTP delivery method selected: {account_check.otp_delivery_method_selected}")
        print(f"Unexpected state: {account_check.unexpected_state}")
        print(f"Statement page reached: {account_check.statement_page_reached}")
        print(f"Statement page description: {account_check.statement_page_description}")

        if(account_check.available_periods):
            await select_period_and_extract(agent, account_check.available_periods)
        elif not account_check.otp_screen_reached and not account_check.captcha_encountered:
            print("No statement periods were discovered — period selection was skipped.")

        if (account_check.captcha_encountered):
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
            otp_result, ok = check_result(otp_history, "OTP submission result")
            if ok and otp_result.otp_submission_succeeded:
                agent.add_new_task("Navigate to wherever account statements are viewable. Once there, use the evaluate action to read ONLY the statement-period dropdown element's own <option> values and the current URL. Do NOT read or return the page's full visible text or body content, since the default view may already show real transaction rows. Populate available_periods with the literal text of every option found. Do NOT select any period yet, and do NOT expand or extract any transaction rows. Stop here once the list is reported.")
                post_otp_discovery_history = await agent.run()
                post_otp_discovery_check, ok = check_result(post_otp_discovery_history, "Post-OTP period discovery result")
                if ok and post_otp_discovery_check.available_periods:
                    await select_period_and_extract(agent, post_otp_discovery_check.available_periods)
                elif ok:
                    print("No statement periods were discovered after OTP — period selection was skipped.")
            elif ok:
                print(f"OTP submission failed: {otp_result.otp_submission_result}")
    finally:
        await browser_session.kill()

asyncio.run(main())