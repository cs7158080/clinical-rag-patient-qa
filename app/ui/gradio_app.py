import gradio as gr
import os
import json
import logging
from app.config import AppConfig
from app.storage import db, pinecone_client
from app.deidentification.reid_map import (
    load as load_reid_map,
    save as save_reid_map,
    add_entity,
    token_to_hash,
)
from app.generation.qa import run_query
from app.generation.summary_generator import run_generate_summary
from app.ingestion.pipeline import run_ingestion

logger = logging.getLogger(__name__)

RTL_CSS = """
.gradio-container {direction: rtl; text-align: right;}
.tabitem {direction: rtl;}
label {text-align: right; display: block;}
.gr-button {direction: rtl;}
select {direction: rtl;}
textarea {direction: rtl;}
.gr-dropdown {direction: rtl;}
.gr-textbox {direction: rtl;}
.gr-markdown {direction: rtl; text-align: right;}
.gr-markdown ol, .gr-markdown ul {direction: rtl; text-align: right; margin-right: 1.5em; margin-left: 0;}
.gr-markdown li {text-align: right;}
footer {display: none !important;}
"""

THEME_COLORS = {
    'primary': '#5B9BB5',
    'bg': '#FFFFFF',
    'bg_secondary': '#F8F9FA',
    'text': '#2D3748',
    'border': '#E2E8F0',
}


def build_app(config: AppConfig, db_path: str, pinecone_index, reid_map_path: str):

    def sync_patients_from_filesystem():
        patients_root = config.patients_root
        if not os.path.isdir(patients_root):
            logger.warning("patients_root does not exist: %s", patients_root)
            return
        # Lookup-or-mint an ID for every patient folder; persist the map only
        # when a new folder minted a new key (random IDs are not recomputable).
        reid_map = load_reid_map(reid_map_path)
        entries_before = len(reid_map)
        for entry in os.scandir(patients_root):
            if entry.is_dir():
                token = add_entity(reid_map, "PERSON", entry.name)
                patient_id = token_to_hash(token)
                db.upsert_patient_metadata(db_path, patient_id, "display_name", entry.name)
        if len(reid_map) != entries_before:
            save_reid_map(reid_map_path, reid_map)

    def get_patient_choices():
        sync_patients_from_filesystem()
        patients = db.get_patient_list(db_path)
        return [(p['display_name'], p['patient_id']) for p in patients]

    def get_session_dates(patient_id: str):
        if not patient_id:
            return []
        dates = db.get_treatment_session_dates(db_path, patient_id)
        return dates

    def get_file_choices(patient_id: str):
        if not patient_id:
            return []
        paths = db.get_patient_files(db_path, patient_id)
        return [(os.path.basename(p), p) for p in paths]

    def open_file_in_word(file_path: str):
        if not file_path:
            return "אנא בחרי קובץ"
        if not os.path.isfile(file_path):
            return f"הקובץ לא נמצא: {file_path}"
        try:
            os.startfile(file_path)
            return f"נפתח: {os.path.basename(file_path)}"
        except Exception as e:
            logger.error(f"Failed to open file: {e}")
            return f"שגיאה בפתיחת הקובץ: {e}"

    def on_patient_change(patient_id):
        dates = get_session_dates(patient_id)   # newest first
        file_choices = get_file_choices(patient_id)
        return (
            gr.update(choices=dates, value=dates[-1] if dates else None),  # from = oldest
            gr.update(choices=dates, value=dates[0] if dates else None),   # to = newest
            "",
            gr.update(choices=file_choices, value=file_choices[0][1] if file_choices else None),
        )

    async def ask_question(question: str, patient_id: str):
        if not question.strip():
            return "אנא הקלידי שאלה"
        if not patient_id:
            return "אנא בחרי מטופל/ת תחילה"
        logger.info(f"Query received for patient_id={patient_id[:8]}...")
        try:
            result = await run_query(question, patient_id, config, db_path, pinecone_index, reid_map_path)
            logger.info("Answer generated successfully")
            return result
        except Exception as e:
            logger.error(f"Query error: {e}")
            return "אירעה שגיאה. נסי שוב."

    async def ingest_files():
        if not os.path.isdir(config.patients_root):
            return "תיקיית המטופלים לא נמצאה: " + config.patients_root
        reid_map = load_reid_map(reid_map_path)
        try:
            results = await run_ingestion(config.patients_root, config, reid_map, db_path, pinecone_index)
        except Exception as e:
            logger.error(f"Ingestion error: {e}")
            return f"שגיאה בטעינה: {e}"

        if not results:
            return "לא נמצאו קבצי docx בתיקיית המטופלים."

        ok = skipped = blocked = errors = 0
        lines = []
        for file_path, result in results:
            filename = os.path.basename(file_path)
            if result == "ok":
                ok += 1
                lines.append(f"✓  {filename}")
            elif result == "skipped":
                skipped += 1
                lines.append(f"○  {filename} (ללא שינוי)")
            elif result.startswith("blocked:"):
                blocked += 1
                reason = result.split(":", 1)[1]
                lines.append(f"✗  {filename} (חסום: {reason})")
            else:
                errors += 1
                lines.append(f"✗  {filename} ({result})")

        summary = f"סיום טעינה — {ok} חדשים, {skipped} ללא שינוי, {blocked} חסומים, {errors} שגיאות\n\n"
        return summary + "\n".join(lines)

    async def generate_summary(patient_id: str, date_from: str, date_to: str):
        if not patient_id:
            return "אנא בחרי מטופל/ת תחילה"
        if not date_from or not date_to:
            return "אנא בחרי תאריכי התחלה וסיום"
        if date_from > date_to:
            return "שגיאה: תאריך ההתחלה חייב להיות לפני תאריך הסיום"
        logger.info(
            "Generate summary triggered for patient_id=%s... range=%s to %s",
            patient_id[:8], date_from, date_to,
        )
        try:
            result = await run_generate_summary(patient_id, date_from, date_to, config, db_path, reid_map_path)
            try:
                paths = json.loads(result)
                if paths.get("docx"):
                    return f"הסיכום נוצר: {paths['docx']}"
                return result
            except (json.JSONDecodeError, TypeError):
                return result
        except Exception as e:
            logger.error(f"Generate summary error: {e}")
            return "אירעה שגיאה ביצירת הסיכום"

    initial_patients = get_patient_choices()
    initial_files = get_file_choices(initial_patients[0][1] if initial_patients else None)

    with gr.Blocks(
        title="מערכת שאלות ותשובות — תיקי מטופלים",
        theme=gr.themes.Soft(
            primary_hue="blue",
            neutral_hue="slate",
        ),
        css=RTL_CSS,
    ) as app:
        gr.Markdown("# 👤 מערכת שאלות ותשובות — תיקי מטופלים")

        with gr.Row():
            patient_dropdown = gr.Dropdown(
                label="מטופל/ת",
                choices=initial_patients,
                value=initial_patients[0][1] if initial_patients else None,
                type="value",
                scale=4,
            )
            refresh_btn = gr.Button("רענן רשימה", scale=1)

        with gr.Tabs():
            with gr.TabItem("💬 שאלות ותשובות"):
                question_input = gr.Textbox(
                    label="שאלה",
                    placeholder="לדוגמה: על מה עבדנו בשלושת החודשים האחרונים?",
                    lines=2,
                )
                submit_btn = gr.Button("שלח", variant="primary")
                answer_output = gr.Markdown()

            with gr.TabItem("📝 יצירת סיכום ביקור"):
                initial_dates = get_session_dates(initial_patients[0][1] if initial_patients else None)
                with gr.Row():
                    date_from_dropdown = gr.Dropdown(
                        label="מ-תאריך (הטיפול הראשון בסיכום)",
                        choices=initial_dates,
                        value=initial_dates[-1] if initial_dates else None,
                        scale=1,
                    )
                    date_to_dropdown = gr.Dropdown(
                        label="עד תאריך (הטיפול האחרון)",
                        choices=initial_dates,
                        value=initial_dates[0] if initial_dates else None,
                        scale=1,
                    )
                generate_btn = gr.Button("צור סיכום", variant="primary")
                generate_status = gr.Textbox(
                    label="סטטוס",
                    interactive=False,
                )

            with gr.TabItem("📂 טעינת קבצים"):
                ingest_btn = gr.Button("הפעל טעינה", variant="primary")
                ingest_status = gr.Textbox(
                    label="תוצאות",
                    lines=15,
                    interactive=False,
                )

            with gr.TabItem("📖 קבצי מטופל"):
                file_dropdown = gr.Dropdown(
                    label="קבצים",
                    choices=initial_files,
                    value=initial_files[0][1] if initial_files else None,
                    type="value",
                )
                open_file_btn = gr.Button("פתח ב-Word", variant="primary")
                open_file_status = gr.Textbox(
                    label="סטטוס",
                    interactive=False,
                )

        def refresh_patients():
            choices = get_patient_choices()
            return gr.update(choices=choices, value=choices[0][1] if choices else None)

        refresh_btn.click(fn=refresh_patients, outputs=[patient_dropdown])

        ingest_btn.click(
            fn=ingest_files,
            outputs=[ingest_status],
        )

        patient_dropdown.change(
            fn=on_patient_change,
            inputs=[patient_dropdown],
            outputs=[date_from_dropdown, date_to_dropdown, answer_output, file_dropdown],
        )

        open_file_btn.click(
            fn=open_file_in_word,
            inputs=[file_dropdown],
            outputs=[open_file_status],
        )

        submit_btn.click(
            fn=ask_question,
            inputs=[question_input, patient_dropdown],
            outputs=[answer_output],
        )

        generate_btn.click(
            fn=generate_summary,
            inputs=[patient_dropdown, date_from_dropdown, date_to_dropdown],
            outputs=[generate_status],
        ).then(
            fn=lambda pid: gr.update(choices=get_file_choices(pid)) if pid else gr.update(),
            inputs=[patient_dropdown],
            outputs=[file_dropdown],
        )

    return app
