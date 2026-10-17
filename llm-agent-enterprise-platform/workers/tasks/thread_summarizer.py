import os
from celery import shared_task
import openai
import psycopg2

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres_secure_password")
POSTGRES_DB = os.getenv("POSTGRES_DB", "llm_enterprise_db")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

@shared_task(name="tasks.summarize_thread_title")
def summarize_thread_title(conversation_id: str, first_message: str):
    try:
        title = first_message[:40] + ("..." if len(first_message) > 40 else "")
        
        if OPENAI_API_KEY and not OPENAI_API_KEY.startswith("sk-proj-example"):
            try:
                client = openai.OpenAI(api_key=OPENAI_API_KEY)
                res = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "Generate a concise 3-5 word title summarizing the user prompt. Do not use quotes."},
                        {"role": "user", "content": first_message}
                    ],
                    max_tokens=20
                )
                generated = res.choices[0].message.content.strip()
                if generated:
                    title = generated
            except Exception:
                pass

        # Update Postgres database
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            dbname=POSTGRES_DB
        )
        cur = conn.cursor()
        cur.execute(
            "UPDATE conversations SET title = %s WHERE id = %s",
            (title, conversation_id)
        )
        conn.commit()
        cur.close()
        conn.close()

        return {"status": "success", "conversation_id": conversation_id, "title": title}
    except Exception as e:
        return {"status": "error", "error": str(e)}
