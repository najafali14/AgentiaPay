import imaplib
import email
from email.header import decode_header
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel,EmailStr
import firebase_admin
from firebase_admin import credentials, firestore
from google import genai
import os
import base64
from bs4 import BeautifulSoup
import smtplib
from email.message import EmailMessage
from typing import List,Dict
# Firebase initialization
# Get base64-encoded credentials from env
 firebase_b64 = os.getenv("FIREBASE_CREDENTIALS")
 
 if firebase_b64:
     decoded = base64.b64decode(firebase_b64).decode("utf-8")
     with open("credentials.json", "w") as f:
         f.write(decoded)
 
     cred = credentials.Certificate("credentials.json")
     firebase_admin.initialize_app(cred)
 else:
     raise Exception("FIREBASE_CREDENTIALS not found in environment")

# cred = credentials.Certificate('credentials.json')  # Path to your Firebase credentials JSON file
# firebase_admin.initialize_app(cred)
db = firestore.client()
emails_ref = db.collection('captured_emails')  # Collection to store emails
sellers_ref = db.collection('sellers_data')  # Collection to store seller details
transactions_ref = db.collection('transactions')  # Collection to store transaction ids

# Email and sender details
EMAIL_ACCOUNT = "najafali32304@gmail.com"
EMAIL_PASSWORD = "gtcz noly qjmv xfmu"
SPECIFIC_SENDER = "service@nayapay.com"

# FastAPI app setup
app = FastAPI()

# CORS for frontend
origins = [
    "*",
    "https://urban-umbrella-9w65j9w795jf99rp-3000.app.github.dev/"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class FormData(BaseModel):
    name: str
    email: str
    phone: str
    website: str
    businessType: str
    description: str
    transaction_id: str
    amount: int
# Pydantic model to capture checkout details from frontend

global agent
class CartItem(BaseModel):
    title: str
    price: float
    quantity: int

class CheckoutData(BaseModel):
    nam: str
    email: str
    phone: str
    address: str
    city: str
    postal_code: str
    amount: float
    cart_items: str  # JSON stringified cart
    transaction_id: str


# Decode email header
def decode_header_value(value):
    if not value:
        return ""
    decoded_parts = decode_header(value)
    decoded_value, encoding = decoded_parts[0]
    if isinstance(decoded_value, bytes):
        return decoded_value.decode(encoding or "utf-8", errors="ignore")
    return decoded_value

# Extract email body
def extract_html_body(msg):
    """Extract the HTML body and convert to plain text."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_dispo = str(part.get("Content-Disposition"))

            if content_type == "text/html" and "attachment" not in content_dispo:
                charset = part.get_content_charset() or "utf-8"
                html = part.get_payload(decode=True).decode(charset, errors="ignore")
                # Convert HTML to plain text using BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                return soup.get_text(separator="\n", strip=True)
    else:
        if msg.get_content_type() == "text/html":
            html = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            # Convert HTML to plain text using BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            return soup.get_text(separator="\n", strip=True)
    return None



# LLM client setup
client = genai.Client(api_key="AIzaSyAxz3kNZLBz2PH124b-pfqVuulj960QvKo")

# Endpoint to poll emails
@app.get("/poll-emails")
def Poll_Emails():
    try:
        print("Connecting to Gmail...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("inbox")

        result, data = mail.search(None, "UNSEEN")
        email_ids = data[0].split()

        if not email_ids:
            print("No new emails found.")
            return

        for eid in email_ids:
            res, msg_data = mail.fetch(eid, "(RFC822)")
            if res == "OK":
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                sender = decode_header_value(msg.get("From"))

                if SPECIFIC_SENDER in sender:
                    print(f"\n--- New email from NayaPay ({sender}) ---")
                    text_content = extract_html_body(msg)
                    # print(text_content)
                    if text_content:
                        # print(text_content)  # Print the plain text body
                        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"""
            extract transaction id and recieved amount from {text_content}.output must be in python dictionary two keys and values. don't give extra explanation
            """
        )
                        Agent1_extract = response.text
                        print(Agent1_extract)
                        emails_ref.add({
                        "body": Agent1_extract
                        })
                    else:
                        print("No HTML body found.")
                else:
                    pass

        mail.logout()
        print("\nDone.")

    except Exception as e:
        print(f"Error: {str(e)}")

# Endpoint to verify transaction ID
@app.post("/api/checkout")
async def Id(data: CheckoutData):
    try:
        # Step 1: Combine all email texts
        emails = emails_ref.stream()
        email_texts = ""
        transactions = transactions_ref.stream()
        transaction_list = [doc.to_dict().get("transactions_ids") for doc in transactions]
        for email_doc in emails:
            email_data = email_doc.to_dict()
            email_texts += email_data.get('body', '')

        if not email_texts:
            raise ValueError("No email body found.")


        # Step 3: Ask LLM
        prompt = f"""
You are a payment confirmation agent.

Check if the transaction ID '{data.transaction_id}' and amount '{data.amount}' are both present in the following email content:

{email_texts}

If either is missing, respond with only: try again  
If both are found, then check if the transaction ID '{data.transaction_id}' is already in this list: {transaction_list}

If already exists, respond with only: try again  
If not, respond with only: success  

Only respond with one word: success or try again. Do not include any explanation.
"""
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )

        agent = response.text.strip().lower()
        print("LLM response:", agent)

        # Step 4: Add to transactions if success
        if agent == "success":
            transactions_ref.add({
                "transactions_ids": data.transaction_id
            })

        return {"status": agent, "message": "order done"}

    except Exception as e:
        print(f"Error: {e}")
        return JSONResponse(content={"message": "Error processing request"}, status_code=500)

    
@app.post("/apply")
def Apply(data: FormData):
    print(data.transaction_id, data.amount)
    emails = emails_ref.stream()
    transactions = transactions_ref.stream()
    transaction_list = [doc.to_dict().get("transactions_ids") for doc in transactions]
    email_texts = ""

    for email_doc in emails:
        email_data = email_doc.to_dict()
        email_texts += email_data.get('body', '')
            
    # Step 3: Ask LLM
    prompt = f"""
You are a payment confirmation agent.

Check if the transaction ID '{data.transaction_id}' is present in the following email content:

'{email_texts}'

If either is missing, respond with only: try again  
If found, then check if the transaction ID '{data.transaction_id}' is already in this list: '{transaction_list}'

If already exists, respond with only: try again  
If not, respond with only: success  

Only respond with one word: success or try again. Do not include any explanation.
"""
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    agent = response.text.strip().lower()
    print("Agent result:", agent)

    if agent == "success":
        transactions_ref.add({
                "transactions_ids": data.transaction_id
            })
        # Store to Firestore
        sellers_ref.add({
            "name": data.name,
            "email": data.email,
            "phone": data.phone,
            "website": data.website,
            "businessType": data.businessType,
            "description": data.description
        })

        # Send email to admin
        msg = EmailMessage()
        msg['Subject'] = 'New Merchant Application'
        msg['From'] = 'agentiapay@gmail.com'
        msg['To'] = 'najafali32304@gmail.com'

        body = f"""
A new merchant has submitted a payment gateway application via AgentiaPay.

Details:
---------
Name: {data.name}
Email: {data.email}
Phone: {data.phone}
Website: {data.website}
Business Type: {data.businessType}
Description:
{data.description}

---------
This message was sent automatically by AgentiaPay after a new merchant submission.
"""
        msg.set_content(body)

        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login('agentiapay@gmail.com', 'gygt snms qldq tizm')  # Use secure app password
                smtp.send_message(msg)
                print("Email sent to admin.")
        except Exception as e:
            print("Email error:", e)

    return {"status": agent}
